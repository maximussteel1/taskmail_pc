"""Relay-native current-session action execution without mailbox ingress."""

from __future__ import annotations

import secrets
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .context_layer import build_context
from .intent_parser import parse_action
from .mail_attachments import materialize_incoming_attachments
from .models import MailAttachment, MailEnvelope, ParsedMailAction, QuestionAnswer, RunResult, TaskSnapshot, ThreadState
from .question_utils import effective_pending_questions, merge_question_answers, missing_required_question_ids
from .session_semantics import effective_thread_status, thread_can_attempt_resume
from .status import (
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_KILLED,
    THREAD_STATUS_PAUSED,
    THREAD_STATUS_RUNNING,
)
from .task_compiler import compile_task
from .thread_store import load_thread_state, save_thread_state
from .workspace import WorkspaceManager

_NON_ENDABLE_ACTIVE_THREAD_STATUSES = {THREAD_STATUS_ACCEPTED, THREAD_STATUS_RUNNING}
_ACTION_TYPES = {"reply", "status", "pause", "resume", "kill", "end", "answers", "attachment_continuation"}
LOGGER = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _generate_task_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(2)


def _clear_pending_question_state(state: ThreadState) -> None:
    state.pending_question_id = None
    state.pending_question_text = None
    state.pending_choices = []
    state.pending_question_set_id = None
    state.pending_questions = []
    state.collected_answers = []
    state.awaiting_since = None


def _set_thread_lifecycle(state: ThreadState, *, lifecycle: str) -> None:
    state.lifecycle = lifecycle
    state.updated_at = _timestamp()
    state.last_progress_at = state.updated_at


def _synthetic_envelope(
    *,
    body_text: str = "",
    attachments: list[MailAttachment] | None = None,
) -> MailEnvelope:
    return MailEnvelope(
        message_id="<relay-native-session-action@local>",
        subject="relay-native-session-action",
        from_addr="relay-runtime@local",
        to_addr="runner@local",
        date=_timestamp(),
        body_text=body_text,
        attachments=list(attachments or []),
    )


def _attachment_reply_text(payload: dict[str, Any]) -> str:
    return str(payload.get("reply_text") or "").strip()


def _single_answer_text(question_answers: list[QuestionAnswer]) -> str:
    if len(question_answers) != 1:
        return ""
    return question_answers[0].value


def _context_attachments(attachment_paths: list[str]) -> list[MailAttachment]:
    attachments: list[MailAttachment] = []
    for index, raw_path in enumerate(attachment_paths, start=1):
        saved_path = str(raw_path or "").strip()
        if not saved_path:
            continue
        filename = Path(saved_path).name or f"attachment_{index}"
        attachments.append(
            MailAttachment(
                filename=filename,
                content_type="application/octet-stream",
                size_bytes=0,
                saved_path=saved_path,
            )
        )
    return attachments


def _parsed_action_for_command(
    *,
    action_type: str,
    action_payload: dict[str, Any],
    state: ThreadState,
    context: dict[str, Any],
) -> ParsedMailAction:
    if action_type == "reply":
        parsed = parse_action(context)
        return ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            raw_user_text=str(action_payload.get("reply_text") or "").strip(),
            profile=parsed.profile,
            permission=parsed.permission,
            task_text_delta=parsed.task_text_delta,
            acceptance_delta=parsed.acceptance_delta,
            timeout_minutes=parsed.timeout_minutes,
            mode=parsed.mode,
        )
    if action_type == "status":
        return ParsedMailAction(action="STATUS_QUERY", confidence=1.0, raw_user_text="")
    if action_type == "pause":
        return ParsedMailAction(action="PAUSE_SESSION", confidence=1.0, raw_user_text="")
    if action_type == "resume":
        return ParsedMailAction(action="RESUME_SESSION", confidence=1.0, raw_user_text="")
    if action_type == "kill":
        return ParsedMailAction(action="KILL", confidence=1.0, raw_user_text="")
    if action_type == "end":
        return ParsedMailAction(action="END_SESSION", confidence=1.0, raw_user_text="")
    if action_type == "answers":
        question_answers = [
            QuestionAnswer(
                question_id=str(item.get("question_id") or "").strip(),
                value=str(item.get("value") or "").strip(),
                raw_value=str(item.get("value") or "").strip(),
            )
            for item in list(action_payload.get("question_answers") or [])
        ]
        return ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=1.0,
            raw_user_text=_single_answer_text(question_answers),
            question_answers=question_answers,
        )
    if action_type == "attachment_continuation":
        parsed = parse_action(context)
        raw_text = _attachment_reply_text(action_payload)
        parsed_action = "ANSWER_QUESTION" if state.status == THREAD_STATUS_AWAITING_USER_INPUT else "CONTINUE_SESSION"
        return ParsedMailAction(
            action=parsed_action,
            confidence=1.0,
            raw_user_text=raw_text,
            profile=parsed.profile,
            permission=parsed.permission,
            task_text_delta=parsed.task_text_delta,
            acceptance_delta=parsed.acceptance_delta,
            timeout_minutes=parsed.timeout_minutes,
            mode=parsed.mode,
        )
    raise ValueError(f"unsupported relay-native session action: {action_type}")


def _load_context(
    *,
    task_root: Path,
    state: ThreadState,
    body_text: str = "",
    attachment_paths: list[str] | None = None,
) -> dict[str, Any]:
    return build_context(
        _synthetic_envelope(
            body_text=body_text,
            attachments=_context_attachments(list(attachment_paths or [])),
        ),
        state,
        task_root,
    )


def _load_latest_result_from_workspace(
    *,
    workspace: WorkspaceManager,
    thread_id: str,
    task_id: str,
) -> RunResult | None:
    try:
        return workspace.load_run_result(thread_id, f"runs/{task_id}/result.json")
    except FileNotFoundError:
        return None


def _resolved_state_after_runner_start(
    *,
    workspace: WorkspaceManager,
    thread_id: str,
    captured_accepted: ThreadState | None,
    captured_running: ThreadState | None,
    captured_finished_state: ThreadState | None,
) -> ThreadState:
    if captured_finished_state is not None:
        return captured_finished_state
    if captured_running is not None:
        return captured_running
    if captured_accepted is not None:
        return captured_accepted
    return load_thread_state(thread_id, workspace.task_root)


@dataclass(slots=True)
class RelayNativeSessionActionResult:
    action_type: str
    execution_status: str
    summary: str
    state_changed: bool
    target_state: ThreadState
    task_snapshot: TaskSnapshot
    latest_result: RunResult | None
    started_snapshot: TaskSnapshot | None = None


def execute_relay_native_session_action(
    *,
    action_type: str,
    action_payload: dict[str, Any],
    target_state: ThreadState,
    config: AppConfig,
    task_root: str | Path,
    runner: Any,
    incoming_attachment_paths: list[str] | None = None,
    finished_state_callback: Callable[[ThreadState, TaskSnapshot, RunResult], None] | None = None,
) -> RelayNativeSessionActionResult:
    normalized_action_type = str(action_type or "").strip().lower()
    if normalized_action_type not in _ACTION_TYPES:
        raise ValueError(f"unsupported relay-native session action: {normalized_action_type}")

    resolved_task_root = Path(task_root)
    workspace = WorkspaceManager(resolved_task_root)
    state = load_thread_state(target_state.thread_id, resolved_task_root)
    normalized_attachment_paths = list(incoming_attachment_paths or [])
    command_body_text = ""
    if normalized_action_type == "reply":
        command_body_text = str(action_payload.get("reply_text") or "").strip()
    elif normalized_action_type == "attachment_continuation":
        command_body_text = _attachment_reply_text(action_payload)
    context = _load_context(
        task_root=resolved_task_root,
        state=state,
        body_text=command_body_text,
        attachment_paths=normalized_attachment_paths,
    )
    snapshot: TaskSnapshot = context["latest_snapshot"]
    latest_result: RunResult | None = context["latest_result"]
    action = _parsed_action_for_command(
        action_type=normalized_action_type,
        action_payload=action_payload,
        state=state,
        context=context,
    )

    if action.action == "STATUS_QUERY":
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="completed",
            summary="Current session snapshot loaded from local runtime.",
            state_changed=False,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    if action.action == "END_SESSION":
        if state.status in _NON_ENDABLE_ACTIVE_THREAD_STATUSES:
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="no_op",
                summary="This session is already accepted or running. Use kill before end.",
                state_changed=False,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        if state.lifecycle == "ended":
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="no_op",
                summary="This session is already ended.",
                state_changed=False,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        _set_thread_lifecycle(state, lifecycle="ended")
        save_thread_state(state, resolved_task_root)
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="completed",
            summary="This session is now ended and removed from the active working set.",
            state_changed=True,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    if action.action == "PAUSE_SESSION":
        if state.status in _NON_ENDABLE_ACTIVE_THREAD_STATUSES:
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="no_op",
                summary="This session is already accepted or running. Use kill instead of pause.",
                state_changed=False,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        if state.status == THREAD_STATUS_PAUSED:
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="no_op",
                summary="This session is already paused.",
                state_changed=False,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        state.paused_from_status = state.status
        state.status = THREAD_STATUS_PAUSED
        state.updated_at = _timestamp()
        state.last_progress_at = state.updated_at
        save_thread_state(state, resolved_task_root)
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="completed",
            summary="This session is now paused.",
            state_changed=True,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    if state.status == THREAD_STATUS_PAUSED:
        pending_questions = effective_pending_questions(state, fallback_task_id=snapshot.task_id)
        if action.action == "CONTINUE_SESSION":
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="no_op",
                summary="This session is paused. Use resume before continuing.",
                state_changed=False,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        if action.action == "RESUME_SESSION" and pending_questions:
            if state.lifecycle != "active":
                state.lifecycle = "active"
            state.status = THREAD_STATUS_AWAITING_USER_INPUT
            state.paused_from_status = None
            state.updated_at = _timestamp()
            state.last_progress_at = state.updated_at
            save_thread_state(state, resolved_task_root)
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="completed",
                summary="The session is no longer paused, but it still needs answers before continuing.",
                state_changed=True,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        if action.action == "ANSWER_QUESTION":
            if state.lifecycle != "active":
                state.lifecycle = "active"
            state.status = THREAD_STATUS_AWAITING_USER_INPUT
            state.paused_from_status = None
            state.updated_at = _timestamp()
            state.last_progress_at = state.updated_at
            save_thread_state(state, resolved_task_root)

    if state.status == THREAD_STATUS_AWAITING_USER_INPUT:
        if action.action == "KILL":
            state.status = THREAD_STATUS_KILLED
            state.last_summary = "Task was cancelled while awaiting user input."
            _clear_pending_question_state(state)
            state.updated_at = _timestamp()
            state.last_progress_at = state.updated_at
            save_thread_state(state, resolved_task_root)
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="completed",
                summary="The pending task was cancelled while waiting for user input.",
                state_changed=True,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        if action.action == "ANSWER_QUESTION":
            if state.lifecycle != "active":
                state.lifecycle = "active"
            pending_questions = effective_pending_questions(state, fallback_task_id=snapshot.task_id)
            if len(pending_questions) > 1:
                merged_answers = merge_question_answers(state.collected_answers, action.question_answers)
                if action.invalid_answer_messages:
                    state.collected_answers = merged_answers
                    state.updated_at = _timestamp()
                    state.last_progress_at = state.updated_at
                    save_thread_state(state, resolved_task_root)
                    return RelayNativeSessionActionResult(
                        action_type=normalized_action_type,
                        execution_status="no_op",
                        summary="Some answers were saved, but there are invalid or unknown entries.",
                        state_changed=True,
                        target_state=state,
                        task_snapshot=snapshot,
                        latest_result=latest_result,
                    )
                missing_question_ids = missing_required_question_ids(pending_questions, merged_answers)
                if missing_question_ids:
                    state.collected_answers = merged_answers
                    state.updated_at = _timestamp()
                    state.last_progress_at = state.updated_at
                    save_thread_state(state, resolved_task_root)
                    return RelayNativeSessionActionResult(
                        action_type=normalized_action_type,
                        execution_status="no_op",
                        summary="Some answers were saved, but required questions are still missing.",
                        state_changed=True,
                        target_state=state,
                        task_snapshot=snapshot,
                        latest_result=latest_result,
                    )

    if action.action == "KILL":
        target_task_id = state.current_task_id
        if runner.kill(target_task_id):
            return RelayNativeSessionActionResult(
                action_type=normalized_action_type,
                execution_status="kill_requested",
                summary="Kill was requested for the active task.",
                state_changed=False,
                target_state=state,
                task_snapshot=snapshot,
                latest_result=latest_result,
            )
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="no_op",
            summary="No running task is available to kill for this thread.",
            state_changed=False,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    effective_status = effective_thread_status(state)
    can_attempt_resume = thread_can_attempt_resume(state)
    recovery_from_failed_thread = (
        action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION", "RESUME_SESSION"}
        and effective_status == THREAD_STATUS_FAILED
        and not can_attempt_resume
    )
    if action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION", "RESUME_SESSION"} and not can_attempt_resume and not recovery_from_failed_thread:
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="no_op",
            summary="This session does not have a resumable native backend context. Use a fresh session instead.",
            state_changed=False,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    if state.status == THREAD_STATUS_AWAITING_USER_INPUT and action.action != "ANSWER_QUESTION":
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="no_op",
            summary="This thread is waiting for answers to the pending question set.",
            state_changed=False,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    compiled = compile_task(
        action,
        state,
        snapshot,
        task_id=_generate_task_id(),
        now=_timestamp(),
        incoming_attachment_paths=normalized_attachment_paths,
        fallback_to_new_run=recovery_from_failed_thread,
        default_transport_for_backend=config.default_transport_for_backend,
    )
    if compiled is None:
        return RelayNativeSessionActionResult(
            action_type=normalized_action_type,
            execution_status="no_op",
            summary="Reply was not understood. No changes were applied.",
            state_changed=False,
            target_state=state,
            task_snapshot=snapshot,
            latest_result=latest_result,
        )

    captured_accepted: ThreadState | None = None
    captured_running: ThreadState | None = None
    captured_finished_state: ThreadState | None = None
    captured_result: RunResult | None = None

    def _on_accepted(captured_state: ThreadState) -> None:
        nonlocal captured_accepted
        captured_accepted = captured_state

    def _on_running(captured_state: ThreadState) -> None:
        nonlocal captured_running
        captured_running = captured_state

    def _on_finished(captured_state: ThreadState, result: RunResult) -> None:
        nonlocal captured_finished_state, captured_result
        captured_finished_state = captured_state
        captured_result = result
        if finished_state_callback is not None:
            try:
                finished_state_callback(captured_state, compiled, result)
            except Exception:
                LOGGER.exception(
                    "relay-native session action finished-state callback failed. thread=%s task_id=%s",
                    captured_state.thread_id,
                    result.task_id,
                )

    returned_state = runner.start_background_task(
        compiled,
        root_message_id=state.root_message_id,
        latest_message_id=state.latest_message_id,
        subject_norm=state.subject_norm,
        session_name=state.session_name or state.subject_norm,
        on_accepted=_on_accepted,
        on_running=_on_running,
        on_finished=_on_finished,
    )
    if captured_accepted is None and isinstance(returned_state, ThreadState):
        captured_accepted = returned_state

    resolved_state = _resolved_state_after_runner_start(
        workspace=workspace,
        thread_id=state.thread_id,
        captured_accepted=captured_accepted,
        captured_running=captured_running,
        captured_finished_state=captured_finished_state,
    )
    resolved_result = captured_result
    if resolved_result is None:
        resolved_result = _load_latest_result_from_workspace(
            workspace=workspace,
            thread_id=compiled.thread_id,
            task_id=compiled.task_id,
        )

    execution_status = "accepted"
    summary = f"Local runner accepted {normalized_action_type} for execution."
    if resolved_result is not None:
        execution_status = "completed"
        summary = str(resolved_state.last_summary or "").strip() or (
            str(resolved_result.error_message or "").strip() or f"{normalized_action_type} completed locally."
        )

    return RelayNativeSessionActionResult(
        action_type=normalized_action_type,
        execution_status=execution_status,
        summary=summary,
        state_changed=True,
        target_state=resolved_state,
        task_snapshot=compiled,
        latest_result=resolved_result,
        started_snapshot=compiled,
    )


def materialize_session_action_attachments(
    *,
    task_root: str | Path,
    repo_path: str,
    workdir: str | None,
    attachments: list[MailAttachment],
    filename_prefix: str,
) -> list[str]:
    if not attachments:
        return []
    materialized = materialize_incoming_attachments(
        MailEnvelope(
            message_id="<relay-native-session-action-attachments@local>",
            subject="relay-native-session-action-attachments",
            from_addr="relay-runtime@local",
            to_addr="runner@local",
            date=_timestamp(),
            body_text="relay-native session action attachments",
            attachments=attachments,
        ),
        repo_path=repo_path,
        workdir=workdir,
        auto_create_workdir=False,
        filename_prefix=filename_prefix,
    )
    return [
        str(item.saved_path).strip()
        for item in materialized.attachments
        if str(item.saved_path or "").strip()
    ]
