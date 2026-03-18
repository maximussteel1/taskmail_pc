"""Task snapshot compilation helpers."""

from __future__ import annotations

from dataclasses import replace

from .models import ParsedMailAction, TaskSnapshot, ThreadState
from .question_utils import (
    canonical_answer_context,
    canonical_answer_summary,
    effective_pending_questions,
    effective_question_set_id,
    merge_question_answers,
)
from .status import BACKEND_CODEX, BACKEND_TRANSPORT_CLI

_APPEND_PREFIX = "Additional context from reply:"


def _append_context_text(base_text: str, delta: str) -> str:
    clean_delta = delta.strip()
    if not clean_delta:
        return base_text
    if not base_text.strip():
        return f"{_APPEND_PREFIX}\n{clean_delta}"
    return f"{base_text.rstrip()}\n\n{_APPEND_PREFIX}\n{clean_delta}"


def _merge_attachment_paths(base_paths: list[str], incoming_paths: list[str]) -> list[str]:
    merged = list(base_paths)
    seen = set(base_paths)
    for item in incoming_paths:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _attachment_summary_text(paths: list[str]) -> str:
    if not paths:
        return ""
    summary_lines = ["New incoming attachments materialized in workdir:"]
    summary_lines.extend(f"- {path}" for path in paths if str(path).strip())
    return "\n".join(summary_lines).strip()


def _combine_turn_text(raw_text: str, incoming_paths: list[str]) -> str:
    parts: list[str] = []
    normalized_text = raw_text.strip()
    if normalized_text:
        parts.append(normalized_text)
    attachment_block = _attachment_summary_text(incoming_paths)
    if attachment_block:
        parts.append(attachment_block)
    return "\n\n".join(parts).strip()


def _continued_backend_transport(*, backend: str, current_backend: str, current_transport: str) -> str:
    if backend != BACKEND_CODEX:
        return BACKEND_TRANSPORT_CLI
    if current_backend == BACKEND_CODEX:
        return current_transport
    return BACKEND_TRANSPORT_CLI


def _legacy_answer_block(thread_state: ThreadState, latest_snapshot: TaskSnapshot, raw_user_text: str) -> str:
    question_id = thread_state.pending_question_id or f"question_{latest_snapshot.task_id}"
    question_text = thread_state.pending_question_text or "Additional clarification requested by the backend."
    return (
        f"Answer to pending question ({question_id}):\n"
        f"Question: {question_text}\n"
        f"Answer: {raw_user_text.strip()}"
    )


def _build_fresh_run_snapshot(
    latest_snapshot: TaskSnapshot,
    *,
    task_id: str,
    thread_id: str,
    timestamp: str,
    backend: str,
    profile: str | None,
    permission: str | None,
    task_text: str,
    acceptance: list[str],
    timeout_minutes: int,
    mode: str,
    attachments: list[str],
    backend_transport: str,
    recovery_text: str = "",
    incoming_attachment_paths: list[str] | None = None,
) -> TaskSnapshot:
    recovery_context = _combine_turn_text(recovery_text, list(incoming_attachment_paths or []))
    if recovery_context:
        task_text = _append_context_text(task_text, recovery_context)
    return replace(
        latest_snapshot,
        task_id=task_id,
        thread_id=thread_id,
        updated_at=timestamp,
        created_at=timestamp,
        backend=backend,
        profile=profile,
        permission=permission,
        task_text=task_text,
        acceptance=acceptance,
        timeout_minutes=timeout_minutes,
        mode=mode,
        attachments=attachments,
        run_mode="new",
        backend_session_id=None,
        turn_text=None,
        backend_transport=backend_transport,
    )


def compile_task(
    action: ParsedMailAction,
    thread_state: ThreadState,
    latest_snapshot: TaskSnapshot,
    *,
    task_id: str | None = None,
    now: str | None = None,
    thread_id: str | None = None,
    incoming_attachment_paths: list[str] | None = None,
    fallback_to_new_run: bool = False,
) -> TaskSnapshot | None:
    if action.action in {"STATUS_QUERY", "KILL", "UNKNOWN", "LIST_SESSIONS", "PAUSE_SESSION"}:
        return None

    new_task_id = task_id or latest_snapshot.task_id
    timestamp = now or latest_snapshot.updated_at
    backend = action.backend or latest_snapshot.backend
    profile = action.profile if action.profile is not None else thread_state.profile
    permission = action.permission if action.permission is not None else thread_state.permission
    next_thread_id = thread_id or latest_snapshot.thread_id
    attachment_paths = _merge_attachment_paths(
        latest_snapshot.attachments,
        list(incoming_attachment_paths or []),
    )
    backend_transport = _continued_backend_transport(
        backend=backend,
        current_backend=thread_state.backend,
        current_transport=thread_state.backend_transport,
    )

    if action.action == "RERUN":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            permission=permission,
            attachments=attachment_paths,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport=backend_transport,
        )

    if action.action == "UPDATE_TASK":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            permission=permission,
            task_text=action.task_text_delta or latest_snapshot.task_text,
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            attachments=attachment_paths,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport=backend_transport,
        )

    if action.action == "APPEND_CONTEXT":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            permission=permission,
            task_text=_append_context_text(latest_snapshot.task_text, action.raw_user_text),
            attachments=attachment_paths,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport=backend_transport,
        )

    if action.action == "NEW_SESSION":
        task_text = action.task_text_delta or latest_snapshot.task_text
        if not action.task_text_delta and action.raw_user_text.strip():
            task_text = _append_context_text(task_text, action.raw_user_text)
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            permission=permission,
            task_text=task_text,
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            attachments=attachment_paths,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport=backend_transport,
        )

    if action.action in {"CONTINUE_SESSION", "RESUME_SESSION"}:
        if fallback_to_new_run:
            return _build_fresh_run_snapshot(
                latest_snapshot,
                task_id=new_task_id,
                thread_id=next_thread_id,
                timestamp=timestamp,
                backend=backend,
                profile=profile,
                permission=permission,
                task_text=action.task_text_delta or latest_snapshot.task_text,
                acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
                timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
                mode=action.mode or latest_snapshot.mode,
                attachments=attachment_paths,
                backend_transport=backend_transport,
                recovery_text="" if action.task_text_delta else action.raw_user_text,
                incoming_attachment_paths=incoming_attachment_paths,
            )
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            permission=permission,
            task_text=action.task_text_delta or latest_snapshot.task_text,
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            attachments=attachment_paths,
            run_mode="resume",
            backend_session_id=thread_state.backend_session_id,
            turn_text=_combine_turn_text(action.raw_user_text, attachment_paths) or "Continue the previous task.",
            backend_transport=backend_transport,
        )

    if action.action == "ANSWER_QUESTION":
        base_text = action.task_text_delta or latest_snapshot.task_text
        pending_questions = effective_pending_questions(thread_state, fallback_task_id=latest_snapshot.task_id)
        if len(pending_questions) > 1 and action.question_answers:
            merged_answers = merge_question_answers(thread_state.collected_answers, action.question_answers)
            question_set_id = effective_question_set_id(thread_state, fallback_task_id=latest_snapshot.task_id) or (
                pending_questions[0].question_set_id
            )
            answer_block = canonical_answer_context(question_set_id, pending_questions, merged_answers)
            turn_text = canonical_answer_summary(question_set_id, merged_answers)
        else:
            answer_block = _legacy_answer_block(thread_state, latest_snapshot, action.raw_user_text)
            turn_text = action.raw_user_text.strip() or (
                thread_state.pending_question_text or "Additional clarification requested by the backend."
            )
        if fallback_to_new_run:
            return _build_fresh_run_snapshot(
                latest_snapshot,
                task_id=new_task_id,
                thread_id=next_thread_id,
                timestamp=timestamp,
                backend=backend,
                profile=profile,
                permission=permission,
                task_text=base_text,
                acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
                timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
                mode=action.mode or latest_snapshot.mode,
                attachments=attachment_paths,
                backend_transport=backend_transport,
                recovery_text=answer_block,
                incoming_attachment_paths=incoming_attachment_paths,
            )
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            permission=permission,
            task_text=_append_context_text(base_text, answer_block),
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            attachments=attachment_paths,
            run_mode="resume",
            backend_session_id=thread_state.backend_session_id,
            turn_text=_combine_turn_text(turn_text, attachment_paths) or turn_text,
            backend_transport=backend_transport,
        )

    return None
