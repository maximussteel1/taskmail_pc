"""Application entrypoint for bootstrap, mail polling, and reply handling."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .adapters.codex_adapter import CodexAdapter
from .adapters.codex_routing_adapter import CodexRoutingAdapter
from .adapters.codex_sdk_adapter import CodexSdkAdapter
from .adapters.opencode_adapter import OpenCodeAdapter
from .artifact_resolver import (
    project_run_artifacts_to_outgoing_attachments,
    resolve_run_artifacts,
    write_artifact_index,
)
from .config import AppConfig, load_config
from .context_layer import build_context
from .dispatcher import Dispatcher
from .external_delivery import prepare_external_deliveries
from .intent_parser import parse_action
from .mail_attachments import materialize_incoming_attachments
from .mail_io import MailClient, SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .mail_retention import SYNC_PROJECT_FOLDER_LIST_SUBJECT, is_prunable_thread_status_subject
from .models import MailEnvelope, OutgoingAttachment, ParsedMailAction, RunResult, TaskSnapshot, ThreadState
from .monitor_windows import MonitorWindowManager
from .question_utils import effective_pending_questions, merge_question_answers, missing_required_question_ids
from .parser import parse_initial_task, parse_subject
from .project_folder_sync import build_project_folder_sync_body, list_project_folders
from .reporter import (
    MAIL_STATUS_ACCEPTED,
    MAIL_STATUS_DONE,
    MAIL_STATUS_FAILED,
    MAIL_STATUS_KILLED,
    MAIL_STATUS_PAUSED,
    MAIL_STATUS_QUESTION,
    MAIL_STATUS_RUNNING,
    MAIL_STATUS_STATUS,
    build_status_html,
    build_status_markdown,
    build_status_subject,
    render_status_markdown_to_plain_text,
)
from .runner import SerialTaskRunner
from .session_semantics import effective_thread_status, thread_can_attempt_resume
from .state_capsule import parse_state_capsule
from .status import (
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_KILLED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_KILLED,
    THREAD_STATUS_PAUSED,
    THREAD_STATUS_RUNNING,
)
from .task_compiler import compile_task
from .thread_store import (
    list_all_thread_states,
    list_workspace_sessions,
    load_thread_state,
    resolve_thread,
    save_raw_mail,
    save_thread_state,
)

LOGGER = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SYNC_REPLY_STATE_FILENAME = "sync_reply_state.json"
_MAX_TRACKED_SYNC_REPLY_IDS = 100
_MAX_ACTIVE_SESSIONS = 4
_NON_ENDABLE_ACTIVE_THREAD_STATUSES = {THREAD_STATUS_ACCEPTED, THREAD_STATUS_RUNNING}

BOOTSTRAP_MODULES = (
    "mail_runner.config",
    "mail_runner.models",
    "mail_runner.mail_io",
    "mail_runner.mail_retention",
    "mail_runner.mail_attachments",
    "mail_runner.artifact_resolver",
    "mail_runner.external_delivery",
    "mail_runner.monitor_windows",
    "mail_runner.parser",
    "mail_runner.project_folder_sync",
    "mail_runner.thread_store",
    "mail_runner.quote_extractor",
    "mail_runner.state_capsule",
    "mail_runner.context_layer",
    "mail_runner.intent_parser",
    "mail_runner.task_compiler",
    "mail_runner.dispatcher",
    "mail_runner.workspace",
    "mail_runner.reporter",
    "mail_runner.runner",
    "mail_runner.adapters.base",
    "mail_runner.adapters.mock_adapter",
    "mail_runner.adapters.opencode_adapter",
    "mail_runner.adapters.codex_adapter",
    "mail_runner.adapters.codex_routing_adapter",
    "mail_runner.adapters.codex_sdk_adapter",
)

TEMPLATE_FILES = (
    PACKAGE_ROOT / "templates" / "opencode_prompt.txt",
    PACKAGE_ROOT / "templates" / "codex_prompt.txt",
)
_RUNTIME_DIR_ENV = "MAIL_RUNNER_RUNTIME_DIR"


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _import_modules(module_names: Iterable[str]) -> list[str]:
    imported: list[str] = []
    for module_name in module_names:
        importlib.import_module(module_name)
        imported.append(module_name)
    return imported


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

def _paused_intro(state: ThreadState, *, ignored_reply: bool = False) -> str:
    pending_questions = effective_pending_questions(state, fallback_task_id=state.current_task_id)
    if pending_questions:
        if ignored_reply:
            return (
                "This session is paused while it is waiting for answers. "
                "The latest reply was not applied. Reply with /resume and your answer, or send /resume to reopen the pending question mail."
            )
        return (
            "This session is paused while it is waiting for answers. "
            "Reply with /resume and your answer, or send /resume to reopen the pending question mail."
        )
    if ignored_reply:
        return "This session is paused. The latest reply was not applied. Reply with /resume when you're ready to continue."
    return "This session is paused. Reply with /resume when you're ready to continue."


def bootstrap(config: AppConfig, base_dir: str | Path | None = None) -> dict[str, str | int]:
    task_root = config.resolve_task_root(base_dir or PROJECT_ROOT)
    task_root.mkdir(parents=True, exist_ok=True)
    config.task_root = str(task_root)

    missing_templates = [str(path) for path in TEMPLATE_FILES if not path.exists()]
    if missing_templates:
        missing_text = ", ".join(missing_templates)
        raise FileNotFoundError(f"Missing prompt template files: {missing_text}")

    imported_modules = _import_modules(BOOTSTRAP_MODULES)
    return {
        "task_root": str(task_root),
        "template_count": len(TEMPLATE_FILES),
        "module_count": len(imported_modules),
    }


def _build_dispatcher(config: AppConfig | None = None) -> Dispatcher:
    effective_config = config or AppConfig()
    return Dispatcher(
        opencode_adapter=OpenCodeAdapter(effective_config),
        codex_adapter=CodexRoutingAdapter(
            cli_adapter=CodexAdapter(effective_config),
            sdk_adapter=CodexSdkAdapter(effective_config),
        ),
    )


def _build_monitor_window_manager(
    config: AppConfig,
    *,
    task_root: Path,
    base_dir: str | Path | None = None,
) -> MonitorWindowManager | None:
    if not config.spawn_monitor_windows:
        return None
    del base_dir
    config_path = os.getenv("MAIL_RUNNER_CONFIG") or None
    runtime_dir = os.getenv(_RUNTIME_DIR_ENV) or None
    return MonitorWindowManager(
        enabled=True,
        project_root=PROJECT_ROOT,
        task_root=task_root,
        config_path=config_path,
        runtime_dir=runtime_dir,
        refresh_seconds=config.monitor_window_refresh_seconds,
    )


def _build_references(message_id: str | None, existing: list[str]) -> list[str]:
    references = list(existing)
    if message_id and message_id not in references:
        references.append(message_id)
    return references


def _default_reply_headers(state: ThreadState) -> tuple[str | None, list[str]]:
    reply_to = state.latest_message_id or state.root_message_id
    references = _build_references(state.root_message_id, [])
    references = _build_references(state.latest_message_id, references)
    return reply_to, references


def _store_outgoing_mail(
    task_root: Path,
    config: AppConfig,
    state: ThreadState,
    *,
    to_addr: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: str | None,
    references: list[str],
    html_body: str | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> None:
    save_raw_mail(
        state.thread_id,
        {
            "message_id": message_id,
            "subject": subject,
            "from_addr": config.from_addr or config.smtp_user or config.imap_user,
            "to_addr": to_addr,
            "date": _timestamp(),
            "in_reply_to": in_reply_to,
            "references": list(references),
            "body_text": body,
            "html_body": html_body,
            "attachments": [
                {
                    "path": item.path,
                    "name": item.name,
                    "content_type": item.content_type,
                    "attach": item.attach,
                    "inline": item.inline,
                    "content_id": item.content_id,
                    "caption": item.caption,
                }
                for item in (attachments or [])
            ],
            "raw_headers": {
                "Subject": subject,
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
                "Message-ID": message_id,
            },
        },
        task_root,
    )
    if in_reply_to is None and state.root_message_id.startswith("local-root:"):
        state.root_message_id = message_id
    state.latest_message_id = message_id
    state.updated_at = _timestamp()
    save_thread_state(state, task_root)


def _materialize_envelope_attachments(envelope: MailEnvelope, repo_path: str, workdir: str | None) -> MailEnvelope:
    if not envelope.attachments:
        return envelope
    return materialize_incoming_attachments(
        envelope,
        repo_path=repo_path,
        workdir=workdir,
        auto_create_workdir=False,
    )


def _list_previous_status_message_ids(task_root: Path, state: ThreadState, *, keep_message_id: str) -> list[str]:
    mail_dir = task_root / state.thread_id / "mail"
    if not mail_dir.exists():
        return []

    message_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw_path in sorted(mail_dir.glob("raw_*.json")):
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.warning("Unable to parse stored mail payload: %s", raw_path)
            continue
        if not isinstance(payload, dict):
            continue
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id or message_id == keep_message_id or message_id in seen_ids:
            continue
        raw_headers = payload.get("raw_headers") or {}
        if not isinstance(raw_headers, dict):
            continue
        if str(raw_headers.get(SYSTEM_MESSAGE_HEADER) or "").strip() != SYSTEM_MESSAGE_HEADER_VALUE:
            continue
        subject = str(payload.get("subject") or raw_headers.get("Subject") or "").strip()
        if not is_prunable_thread_status_subject(subject):
            continue
        seen_ids.add(message_id)
        message_ids.append(message_id)
    return message_ids


def _sync_reply_state_path(task_root: Path) -> Path:
    return task_root / "_mailbox" / _SYNC_REPLY_STATE_FILENAME


def _load_sync_reply_message_ids(task_root: Path) -> list[str]:
    state_path = _sync_reply_state_path(task_root)
    if not state_path.exists():
        return []
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        LOGGER.warning("Unable to parse sync reply state: %s", state_path)
        return []
    if isinstance(payload, dict):
        raw_ids = payload.get("message_ids", [])
    elif isinstance(payload, list):
        raw_ids = payload
    else:
        return []

    message_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw_message_id in raw_ids:
        message_id = str(raw_message_id or "").strip()
        if not message_id or message_id in seen_ids:
            continue
        seen_ids.add(message_id)
        message_ids.append(message_id)
    return message_ids


def _save_sync_reply_message_ids(task_root: Path, message_ids: list[str]) -> None:
    state_path = _sync_reply_state_path(task_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"message_ids": message_ids[-_MAX_TRACKED_SYNC_REPLY_IDS:]}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _remember_sync_reply_message_id(task_root: Path, message_id: str) -> None:
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return
    message_ids = _load_sync_reply_message_ids(task_root)
    if normalized_message_id not in message_ids:
        message_ids.append(normalized_message_id)
    _save_sync_reply_message_ids(task_root, message_ids)


def _prune_previous_status_mails(mail_client: Any, task_root: Path, state: ThreadState, *, keep_message_id: str) -> None:
    delete_fn = getattr(mail_client, "delete_messages_by_message_ids", None)
    if not callable(delete_fn):
        return
    previous_message_ids = _list_previous_status_message_ids(task_root, state, keep_message_id=keep_message_id)
    if not previous_message_ids:
        return
    try:
        deleted_ids = list(delete_fn(previous_message_ids, mailbox="INBOX") or [])
    except Exception:
        LOGGER.exception("Unable to prune old status mails for thread %s", state.thread_id)
        return
    if deleted_ids:
        LOGGER.info(
            "Pruned %s old status mails from INBOX for thread %s",
            len(deleted_ids),
            state.thread_id,
        )


def _prune_previous_sync_mails(mail_client: Any, task_root: Path, *, keep_message_id: str) -> None:
    delete_fn = getattr(mail_client, "delete_messages_by_message_ids", None)
    if not callable(delete_fn):
        return
    previous_message_ids = [
        message_id for message_id in _load_sync_reply_message_ids(task_root) if message_id != keep_message_id
    ]
    if not previous_message_ids:
        return
    try:
        deleted_ids = list(delete_fn(previous_message_ids, mailbox="INBOX") or [])
    except Exception:
        LOGGER.exception("Unable to prune old sync mails from INBOX")
        return
    if deleted_ids:
        LOGGER.info("Pruned %s old sync mails from INBOX", len(deleted_ids))


def _send_status_update(
    mail_client: Any,
    config: AppConfig,
    task_root: Path,
    *,
    to_addr: str,
    subject_text: str,
    status_label: str,
    state: ThreadState,
    task_snapshot: TaskSnapshot,
    result: RunResult | None = None,
    intro: str | None = None,
    reply_message_id: str | None = None,
    references: list[str] | None = None,
    reply_to_existing: bool = True,
) -> str | None:
    try:
        question_id = result.question_id if result and result.question_id else state.pending_question_id
        question_text = result.question_text if result and result.question_text else state.pending_question_text
        pending_choices = list(result.pending_choices) if result and result.pending_choices else list(state.pending_choices)
        pending_questions = list(result.pending_questions) if result and result.pending_questions else list(state.pending_questions)
        collected_answers = list(state.collected_answers)
        question_set_id = result.question_set_id if result and result.question_set_id else state.pending_question_set_id
        captured_reply = _load_captured_reply(task_root, result)
        resolved_artifacts, skipped_attachments = resolve_run_artifacts(task_root, state, result)
        write_artifact_index(task_root, result, resolved_artifacts, skipped_attachments)
        resolved_attachments = project_run_artifacts_to_outgoing_attachments(resolved_artifacts)
        mail_artifacts, resolved_attachments, external_deliveries, delivery_notices = prepare_external_deliveries(
            config,
            artifacts=resolved_artifacts,
            attachments=resolved_attachments,
            result=result,
        )
        effective_notices = [*skipped_attachments, *delivery_notices]
        body_markdown = build_status_markdown(
            status_label,
            state,
            task_snapshot=task_snapshot,
            result=result,
            captured_reply=captured_reply,
            intro=intro,
            question_id=question_id,
            question_text=question_text,
            pending_choices=pending_choices,
            question_set_id=question_set_id,
            pending_questions=pending_questions,
            collected_answers=collected_answers,
            artifacts=mail_artifacts,
            external_deliveries=external_deliveries,
            skipped_messages=effective_notices,
        )
        body = render_status_markdown_to_plain_text(body_markdown)
        html_body = build_status_html(
            body,
            resolved_attachments,
            effective_notices,
            markdown_body=body_markdown,
            artifacts=mail_artifacts,
        )
        subject = build_status_subject(status_label, subject_text, state.session_id or state.thread_id)
        if not reply_to_existing:
            reply_message_id = None
            references = []
        elif reply_message_id is None:
            reply_message_id, references = _default_reply_headers(state)
        elif references is None:
            references = _build_references(reply_message_id, [])
        sent_message_id = mail_client.send_mail(
            to_addr=to_addr,
            subject=subject,
            body=body,
            attachments=resolved_attachments,
            in_reply_to=reply_message_id,
            references=references,
            headers={SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
            html_body=html_body,
        )
        if sent_message_id:
            _store_outgoing_mail(
                task_root,
                config,
                state,
                to_addr=to_addr,
                subject=subject,
                body=body,
                message_id=sent_message_id,
                in_reply_to=reply_message_id,
                references=list(references or []),
                html_body=html_body,
                attachments=resolved_attachments,
            )
            _prune_previous_status_mails(mail_client, task_root, state, keep_message_id=sent_message_id)
        return sent_message_id
    except Exception:
        LOGGER.exception("Unable to send status mail for task %s", state.current_task_id)
        return None


def _normalize_captured_output(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "").strip()


def _load_captured_reply(task_root: Path, result: RunResult | None) -> str | None:
    if result is None:
        return None

    candidate_rel_paths = [result.stdout_file]
    if result.status not in {RUN_STATUS_SUCCESS, RUN_STATUS_AWAITING_USER_INPUT}:
        candidate_rel_paths.append(result.stderr_file)

    for rel_path in candidate_rel_paths:
        candidate = task_root / result.thread_id / rel_path
        if not candidate.exists():
            continue
        content = _normalize_captured_output(candidate.read_text(encoding="utf-8", errors="replace"))
        if content:
            return content
    return None


def _subject_text_for_thread(subject_info: dict[str, Any], envelope: MailEnvelope, state: ThreadState | None = None) -> str:
    if state is not None and not subject_info.get("is_new_task"):
        return state.session_name or state.subject_norm or envelope.subject.strip()
    if subject_info.get("subject_text"):
        return str(subject_info["subject_text"]).strip()
    if state is not None and (state.session_name or state.subject_norm):
        return state.session_name or state.subject_norm
    return envelope.subject.strip()


def _handle_project_folder_sync(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
) -> bool:
    body = build_project_folder_sync_body(
        list_project_folders(list(config.project_sync_roots or [])),
        scanned_at=_timestamp(),
    )
    sent_message_id = mail_client.send_mail(
        to_addr=envelope.from_addr,
        subject=SYNC_PROJECT_FOLDER_LIST_SUBJECT,
        body=body,
        in_reply_to=envelope.message_id,
        references=_build_references(envelope.message_id, envelope.references),
        headers={SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
    )
    if sent_message_id:
        _remember_sync_reply_message_id(task_root, sent_message_id)
        _prune_previous_sync_mails(mail_client, task_root, keep_message_id=sent_message_id)
    return True


def _record_incoming_mail(state: ThreadState, envelope: MailEnvelope, task_root: Path) -> None:
    save_raw_mail(state.thread_id, envelope, task_root)
    state.latest_message_id = envelope.message_id
    state.updated_at = _timestamp()
    save_thread_state(state, task_root)


def _iter_stored_mail_payloads(task_root: Path, thread_id: str) -> list[dict[str, Any]]:
    mail_dir = task_root / thread_id / "mail"
    payloads: list[dict[str, Any]] = []
    for raw_path in sorted(mail_dir.glob("raw_*.json")):
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.warning("Unable to parse stored mail payload during recovery: %s", raw_path)
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _resolve_recovery_recipient(task_root: Path, state: ThreadState) -> str | None:
    for payload in reversed(_iter_stored_mail_payloads(task_root, state.thread_id)):
        raw_headers = payload.get("raw_headers") or {}
        if not isinstance(raw_headers, dict):
            raw_headers = {}
        if str(raw_headers.get(SYSTEM_MESSAGE_HEADER) or "").strip() == SYSTEM_MESSAGE_HEADER_VALUE:
            continue
        from_addr = str(payload.get("from_addr") or "").strip()
        if from_addr:
            return from_addr
    return None


def _build_recovery_callback_factory(
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
) -> Callable[[ThreadState, TaskSnapshot], tuple[Callable[[ThreadState], None] | None, Callable[[ThreadState, RunResult], None] | None]]:
    def build_callbacks(
        recovered_state: ThreadState,
        recovered_snapshot: TaskSnapshot,
    ) -> tuple[Callable[[ThreadState], None] | None, Callable[[ThreadState, RunResult], None] | None]:
        to_addr = _resolve_recovery_recipient(task_root, recovered_state)
        if not to_addr:
            LOGGER.warning(
                "Unable to recover recipient for thread %s; restart callbacks will be skipped.",
                recovered_state.thread_id,
            )
            return None, None
        subject_text = recovered_state.session_name or recovered_state.subject_norm or recovered_snapshot.thread_id

        def on_running(state: ThreadState) -> None:
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=to_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_RUNNING,
                state=state,
                task_snapshot=recovered_snapshot,
            )

        def on_finished(state: ThreadState, result: RunResult) -> None:
            intro = None
            if result.status == RUN_STATUS_AWAITING_USER_INPUT:
                intro = "The backend needs more information before it can continue. Reply to this email with your answer."
            elif result.status == RUN_STATUS_PAUSED:
                intro = _paused_intro(state)
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=to_addr,
                subject_text=subject_text,
                status_label=_status_label_for_result(result),
                state=state,
                task_snapshot=recovered_snapshot,
                result=result,
                intro=intro,
            )

        return on_running, on_finished

    return build_callbacks


def _status_label_for_result(result: RunResult) -> str:
    if result.status == RUN_STATUS_SUCCESS:
        return MAIL_STATUS_DONE
    if result.status == RUN_STATUS_AWAITING_USER_INPUT:
        return MAIL_STATUS_QUESTION
    if result.status == RUN_STATUS_KILLED:
        return MAIL_STATUS_KILLED
    if result.status == RUN_STATUS_PAUSED:
        return MAIL_STATUS_PAUSED
    return MAIL_STATUS_FAILED


def _send_session_listing(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    state: ThreadState,
    snapshot: TaskSnapshot,
    latest_result: RunResult | None,
) -> bool:
    sessions = list_workspace_sessions(state.repo_path, state.workdir, task_root)
    if sessions:
        lines = ["Sessions in this workspace:"]
        for session in sessions[:10]:
            summary = session.last_summary or "No summary yet."
            label = " (current)" if session.session_id == (state.session_id or state.thread_id) else ""
            lines.append(
                f"- {session.session_id}{label} | {session.lifecycle} | {session.status} | {session.session_name} | {summary}"
            )
        lines.extend(
            [
                "",
                "Reply to a session's latest mail to continue its native context.",
                "Reply here with /new to start a fresh session from this thread.",
            ]
        )
    else:
        lines = ["No sessions were found for this workspace yet."]
    _send_status_update(
        mail_client,
        config,
        task_root,
        to_addr=envelope.from_addr,
        subject_text=state.session_name or state.subject_norm,
        status_label=MAIL_STATUS_STATUS,
        state=state,
        task_snapshot=snapshot,
        result=latest_result,
        intro="\n".join(lines),
        reply_message_id=envelope.message_id,
        references=_build_references(envelope.message_id, envelope.references),
    )
    return True


def _thread_last_active_sort_key(state: ThreadState) -> tuple[str, str]:
    return (state.last_active_at or state.updated_at, state.thread_id)


def _set_thread_lifecycle(state: ThreadState, *, lifecycle: str) -> None:
    state.lifecycle = lifecycle
    state.updated_at = _timestamp()
    state.last_progress_at = state.updated_at


def _enforce_active_session_cap(task_root: Path, *, target_thread_id: str) -> list[str]:
    try:
        target_state = load_thread_state(target_thread_id, task_root)
    except FileNotFoundError:
        target_state = None

    target_needs_activation = target_state is None or target_state.lifecycle != "active"
    active_threads = [
        thread
        for thread in list_all_thread_states(task_root)
        if thread.lifecycle == "active" and thread.thread_id != target_thread_id
    ]
    desired_active_count = len(active_threads) + (1 if target_needs_activation else 0)
    if desired_active_count <= _MAX_ACTIVE_SESSIONS:
        return []

    ended_thread_ids: list[str] = []
    while desired_active_count > _MAX_ACTIVE_SESSIONS:
        candidates = [
            thread for thread in active_threads if thread.status not in _NON_ENDABLE_ACTIVE_THREAD_STATUSES
        ]
        if not candidates:
            LOGGER.warning(
                "Unable to enforce active session cap before starting %s: no safe active thread can be ended.",
                target_thread_id,
            )
            break
        oldest = min(candidates, key=_thread_last_active_sort_key)
        _set_thread_lifecycle(oldest, lifecycle="ended")
        save_thread_state(oldest, task_root)
        ended_thread_ids.append(oldest.thread_id)
        active_threads = [thread for thread in active_threads if thread.thread_id != oldest.thread_id]
        desired_active_count -= 1
    return ended_thread_ids


def _start_snapshot_run(
    runner: SerialTaskRunner,
    snapshot: TaskSnapshot,
    *,
    task_root: Path,
    config: AppConfig,
    mail_client: Any,
    incoming_envelope: MailEnvelope,
    subject_text: str,
    root_message_id: str,
    latest_message_id: str,
    subject_norm: str,
    session_name: str | None,
    save_incoming_on_accept: bool,
    background: bool,
    reply_to_incoming: bool = True,
    accepted_intro: str | None = None,
) -> bool:
    auto_ended_thread_ids = _enforce_active_session_cap(task_root, target_thread_id=snapshot.thread_id)
    effective_accepted_intro = accepted_intro
    if auto_ended_thread_ids:
        cap_note = (
            "Auto-ended least recently active session(s) to keep the active working set within 4: "
            + ", ".join(auto_ended_thread_ids)
        )
        effective_accepted_intro = f"{accepted_intro}\n\n{cap_note}" if accepted_intro else cap_note

    def on_accepted(state: ThreadState) -> None:
        if save_incoming_on_accept:
            save_raw_mail(state.thread_id, incoming_envelope, task_root)
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=incoming_envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_ACCEPTED,
            state=state,
            task_snapshot=snapshot,
            intro=effective_accepted_intro,
            reply_message_id=incoming_envelope.message_id if reply_to_incoming else None,
            references=(
                _build_references(incoming_envelope.message_id, incoming_envelope.references)
                if reply_to_incoming
                else None
            ),
            reply_to_existing=reply_to_incoming,
        )

    def on_running(state: ThreadState) -> None:
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=incoming_envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_RUNNING,
            state=state,
            task_snapshot=snapshot,
        )

    def on_finished(state: ThreadState, result: RunResult) -> None:
        intro = None
        if result.status == RUN_STATUS_AWAITING_USER_INPUT:
            intro = "The backend needs more information before it can continue. Reply to this email with your answer."
        elif result.status == RUN_STATUS_PAUSED:
            intro = _paused_intro(state)
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=incoming_envelope.from_addr,
            subject_text=subject_text,
            status_label=_status_label_for_result(result),
            state=state,
            task_snapshot=snapshot,
            result=result,
            intro=intro,
        )

    if background:
        runner.start_background_task(
            snapshot,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
            session_name=session_name,
            on_accepted=on_accepted,
            on_running=on_running,
            on_finished=on_finished,
        )
    else:
        runner.run_task_snapshot(
            snapshot,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
            session_name=session_name,
            on_accepted=on_accepted,
            on_running=on_running,
            on_finished=on_finished,
        )
    return True


def _start_new_session_from_reply(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
    *,
    state: ThreadState,
    snapshot: TaskSnapshot,
    subject_text: str,
    action: ParsedMailAction,
    incoming_attachment_paths: list[str] | None,
    background: bool,
) -> bool:
    compiled = compile_task(
        action,
        state,
        snapshot,
        task_id=_generate_task_id(),
        now=_timestamp(),
        thread_id=runner.next_thread_id(),
        incoming_attachment_paths=incoming_attachment_paths,
    )
    if compiled is None:
        return False
    compiled.backend_transport = config.default_transport_for_backend(compiled.backend)
    return _start_snapshot_run(
        runner,
        compiled,
        task_root=task_root,
        config=config,
        mail_client=mail_client,
        incoming_envelope=envelope,
        subject_text=subject_text,
        root_message_id=f"local-root:{compiled.thread_id}",
        latest_message_id=f"local-latest:{compiled.thread_id}",
        subject_norm=state.subject_norm,
        session_name=state.session_name or subject_text,
        save_incoming_on_accept=True,
        background=background,
        reply_to_incoming=False,
    )


def _send_busy_status(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
) -> bool:
    active_thread_id = runner.active_thread_id()
    if not active_thread_id:
        return False
    state = load_thread_state(active_thread_id, task_root)
    context = build_context(envelope, state, task_root)
    snapshot = context["latest_snapshot"]
    _send_status_update(
        mail_client,
        config,
        task_root,
        to_addr=envelope.from_addr,
        subject_text=state.session_name or state.subject_norm,
        status_label=MAIL_STATUS_STATUS,
        state=state,
        task_snapshot=snapshot,
        result=context["latest_result"],
        intro="Runner is busy with another task. Wait for completion or send a kill request for the active task.",
        reply_message_id=envelope.message_id,
        references=_build_references(envelope.message_id, envelope.references),
    )
    return True


def _process_new_task_mail(
    envelope: MailEnvelope,
    subject_info: dict[str, Any],
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
    *,
    background: bool,
) -> bool:
    try:
        task_data = parse_initial_task(envelope.body_text, default_timeout_minutes=config.default_timeout_minutes)
    except ValueError as exc:
        LOGGER.warning("Skipping invalid initial task mail %s: %s", envelope.message_id, exc)
        return False
    if envelope.attachments:
        envelope = _materialize_envelope_attachments(envelope, task_data["repo_path"], task_data["workdir"])

    snapshot = TaskSnapshot(
        task_id=_generate_task_id(),
        thread_id=runner.next_thread_id(),
        backend=subject_info["backend"],
        profile=task_data["profile"],
        permission=task_data["permission"],
        repo_path=task_data["repo_path"],
        workdir=task_data["workdir"],
        task_text=task_data["task_text"],
        acceptance=task_data["acceptance"],
        timeout_minutes=task_data["timeout_minutes"],
        mode=task_data["mode"],
        attachments=[item.saved_path for item in envelope.attachments if item.saved_path],
        created_at=_timestamp(),
        updated_at=_timestamp(),
        backend_transport=config.default_transport_for_backend(subject_info["backend"]),
    )
    subject_text = _subject_text_for_thread(subject_info, envelope)
    return _start_snapshot_run(
        runner,
        snapshot,
        task_root=task_root,
        config=config,
        mail_client=mail_client,
        incoming_envelope=envelope,
        subject_text=subject_text,
        root_message_id=envelope.message_id,
        latest_message_id=envelope.message_id,
        subject_norm=subject_info["subject_norm"],
        session_name=subject_text,
        save_incoming_on_accept=True,
        background=background,
    )


def _handle_direct_kill(
    envelope: MailEnvelope,
    subject_info: dict[str, Any],
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
) -> bool:
    target_task_id = str(subject_info.get("subject_text") or "").strip()
    if not target_task_id:
        return False
    if not runner.kill(target_task_id):
        active_thread_id = runner.active_thread_id()
        if not active_thread_id:
            return False
        state = load_thread_state(active_thread_id, task_root)
        context = build_context(envelope, state, task_root)
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=state.session_name or state.subject_norm,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=context["latest_snapshot"],
            result=context["latest_result"],
            intro=f"No running task matched kill request: {target_task_id}",
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    active_thread_id = runner.active_thread_id()
    if active_thread_id:
        state = load_thread_state(active_thread_id, task_root)
        _record_incoming_mail(state, envelope, task_root)
    return True


def _process_existing_thread_mail(
    envelope: MailEnvelope,
    subject_info: dict[str, Any],
    capsule_state: dict[str, str] | None,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
    *,
    background: bool,
) -> bool:
    thread_id = resolve_thread(envelope, task_root, capsule_state=capsule_state)
    if not thread_id:
        return False

    state = load_thread_state(thread_id, task_root)
    if envelope.attachments:
        envelope = _materialize_envelope_attachments(envelope, state.repo_path, state.workdir)
    context = build_context(envelope, state, task_root)
    snapshot = context["latest_snapshot"]
    subject_text = _subject_text_for_thread(subject_info, envelope, state)
    action = parse_action(context, subject_info)
    if action.action != "NEW_SESSION":
        _record_incoming_mail(state, envelope, task_root)

    return _handle_existing_action(
        envelope,
        config,
        task_root,
        mail_client,
        runner,
        state=state,
        snapshot=snapshot,
        latest_result=context["latest_result"],
        incoming_attachment_paths=context["incoming_attachment_paths"],
        subject_text=subject_text,
        action=action,
        direct_kill_target=(
            str(subject_info["subject_text"]).strip()
            if subject_info.get("action") == "KILL" and subject_info.get("subject_text")
            else None
        ),
        background=background,
    )


def _handle_existing_action(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
    *,
    state: ThreadState,
    snapshot: TaskSnapshot,
    latest_result: RunResult | None,
    incoming_attachment_paths: list[str] | None,
    subject_text: str,
    action: ParsedMailAction,
    direct_kill_target: str | None = None,
    background: bool,
) -> bool:

    if action.action == "STATUS_QUERY":
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_PAUSED if state.status == THREAD_STATUS_PAUSED else MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro=_paused_intro(state) if state.status == THREAD_STATUS_PAUSED else None,
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    if action.action == "LIST_SESSIONS":
        return _send_session_listing(
            envelope,
            config,
            task_root,
            mail_client,
            state,
            snapshot,
            latest_result,
        )

    if action.action == "NEW_SESSION":
        return _start_new_session_from_reply(
            envelope,
            config,
            task_root,
            mail_client,
            runner,
            state=state,
            snapshot=snapshot,
            subject_text=subject_text,
            action=action,
            incoming_attachment_paths=incoming_attachment_paths,
            background=background,
        )

    if action.action == "END_SESSION":
        if state.status in _NON_ENDABLE_ACTIVE_THREAD_STATUSES:
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This session is already accepted or running. Wait for it to stop naturally, or use /kill before /end.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        if state.lifecycle == "ended":
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This session is already ended. Reply with /resume if you want to bring it back into the active working set.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        _set_thread_lifecycle(state, lifecycle="ended")
        save_thread_state(state, task_root)
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="This session is now ended and removed from the active working set. Reply with /resume if you want to continue this same thread later.",
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    if action.action == "PAUSE_SESSION":
        if state.status in {THREAD_STATUS_ACCEPTED, THREAD_STATUS_RUNNING}:
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This session is already accepted or running. Wait for it to stop naturally, or use /kill instead of /pause.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        if state.status == THREAD_STATUS_PAUSED:
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_PAUSED,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro=_paused_intro(state),
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        state.paused_from_status = state.status
        state.status = THREAD_STATUS_PAUSED
        state.updated_at = _timestamp()
        state.last_progress_at = state.updated_at
        save_thread_state(state, task_root)
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_PAUSED,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro=_paused_intro(state),
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    if state.status == THREAD_STATUS_PAUSED:
        pending_questions = effective_pending_questions(state, fallback_task_id=snapshot.task_id)
        if action.action in {"CONTINUE_SESSION", "UNKNOWN"}:
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_PAUSED,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro=_paused_intro(state, ignored_reply=True),
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        if action.action == "RESUME_SESSION" and pending_questions:
            if state.lifecycle != "active":
                state.lifecycle = "active"
            state.status = THREAD_STATUS_AWAITING_USER_INPUT
            state.paused_from_status = None
            state.updated_at = _timestamp()
            state.last_progress_at = state.updated_at
            save_thread_state(state, task_root)
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_QUESTION,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="The session is no longer paused, but it still needs answers before the backend can continue.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        if action.action == "ANSWER_QUESTION":
            if state.lifecycle != "active":
                state.lifecycle = "active"
            state.status = THREAD_STATUS_AWAITING_USER_INPUT
            state.paused_from_status = None
            state.updated_at = _timestamp()
            state.last_progress_at = state.updated_at
            save_thread_state(state, task_root)

    if state.status == THREAD_STATUS_AWAITING_USER_INPUT:
        if action.action == "KILL":
            state.status = THREAD_STATUS_KILLED
            state.last_summary = "Task was cancelled while awaiting user input."
            _clear_pending_question_state(state)
            state.updated_at = _timestamp()
            state.last_progress_at = state.updated_at
            save_thread_state(state, task_root)
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_KILLED,
                state=state,
                task_snapshot=snapshot,
                intro="The pending task was cancelled while waiting for user input.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        if action.action == "RERUN":
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This thread is awaiting an answer to the pending question set. Reply with the answer before rerunning.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        if action.action == "UNKNOWN":
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_QUESTION,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="Reply did not include a valid answer. Please answer the pending question set below.",
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
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
                    save_thread_state(state, task_root)
                    _send_status_update(
                        mail_client,
                        config,
                        task_root,
                        to_addr=envelope.from_addr,
                        subject_text=subject_text,
                        status_label=MAIL_STATUS_QUESTION,
                        state=state,
                        task_snapshot=snapshot,
                        result=latest_result,
                        intro="Some answers were saved, but there are invalid or unknown entries. Please review the pending question set below.",
                        reply_message_id=envelope.message_id,
                        references=_build_references(envelope.message_id, envelope.references),
                    )
                    return True
                missing_question_ids = missing_required_question_ids(pending_questions, merged_answers)
                if missing_question_ids:
                    state.collected_answers = merged_answers
                    state.updated_at = _timestamp()
                    state.last_progress_at = state.updated_at
                    save_thread_state(state, task_root)
                    _send_status_update(
                        mail_client,
                        config,
                        task_root,
                        to_addr=envelope.from_addr,
                        subject_text=subject_text,
                        status_label=MAIL_STATUS_QUESTION,
                        state=state,
                        task_snapshot=snapshot,
                        result=latest_result,
                        intro="Some answers were saved, but required questions are still missing. Please complete the remaining question set below.",
                        reply_message_id=envelope.message_id,
                        references=_build_references(envelope.message_id, envelope.references),
                    )
                    return True

    if action.action == "KILL":
        target_task_id = direct_kill_target or state.current_task_id
        if runner.kill(target_task_id):
            return True
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_PAUSED if state.status == THREAD_STATUS_PAUSED else MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro=(
                "No running task is available to kill for this thread. The session remains paused."
                if state.status == THREAD_STATUS_PAUSED
                else "No running task is available to kill for this thread."
            ),
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    effective_status = effective_thread_status(state)
    can_attempt_resume = thread_can_attempt_resume(state)
    recovery_from_failed_thread = (
        action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION", "RESUME_SESSION"}
        and effective_status == THREAD_STATUS_FAILED
        and not can_attempt_resume
    )
    if action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION", "RESUME_SESSION"} and not can_attempt_resume and not recovery_from_failed_thread:
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_PAUSED if state.status == THREAD_STATUS_PAUSED else MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro=(
                "This paused session does not have a resumable native backend context. Use /new or /rerun instead."
                if state.status == THREAD_STATUS_PAUSED
                else "This session does not have a resumable native backend context. Use /new or /rerun instead."
            ),
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    if state.status == THREAD_STATUS_AWAITING_USER_INPUT and action.action != "ANSWER_QUESTION":
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_QUESTION,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="This thread is waiting for an answer to the pending question set below.",
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    compiled = compile_task(
        action,
        state,
        snapshot,
        task_id=_generate_task_id(),
        now=_timestamp(),
        incoming_attachment_paths=incoming_attachment_paths,
        fallback_to_new_run=recovery_from_failed_thread,
    )
    if compiled is None:
        _send_status_update(
            mail_client,
            config,
            task_root,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="Reply was not understood. No changes were applied.",
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    accepted_intro = None
    if action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION", "RESUME_SESSION"} and effective_status == THREAD_STATUS_KILLED:
        accepted_intro = (
            "Resuming a session after kill. This is a risk recovery: the previous native context "
            "may have stopped mid-step, so you should verify the next result carefully."
        )
    elif recovery_from_failed_thread:
        accepted_intro = (
            "Native session resume is unavailable for this failed thread. "
            "Starting a fresh recovery run from the latest saved task snapshot instead."
        )

    return _start_snapshot_run(
        runner,
        compiled,
        task_root=task_root,
        config=config,
        mail_client=mail_client,
        incoming_envelope=envelope,
        subject_text=subject_text,
        root_message_id=state.root_message_id,
        latest_message_id=envelope.message_id,
        subject_norm=state.subject_norm,
        session_name=state.session_name or subject_text,
        save_incoming_on_accept=False,
        background=background,
        accepted_intro=accepted_intro,
    )


def _process_mail(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
    *,
    background: bool,
) -> bool:
    subject_info = parse_subject(envelope.subject)
    capsule_state = parse_state_capsule(envelope.body_text)

    if (
        subject_info.get("action") == "KILL"
        and subject_info.get("subject_text")
        and not envelope.in_reply_to
        and not envelope.references
        and capsule_state is None
    ):
        if _handle_direct_kill(envelope, subject_info, config, task_root, mail_client, runner):
            return True

    if (
        subject_info.get("action") == "SYNC_PROJECT_FOLDERS"
        and not envelope.in_reply_to
        and not envelope.references
        and capsule_state is None
    ):
        return _handle_project_folder_sync(envelope, config, task_root, mail_client)

    if subject_info["is_new_task"] and not envelope.in_reply_to and not envelope.references:
        return _process_new_task_mail(
            envelope,
            subject_info,
            config,
            task_root,
            mail_client,
            runner,
            background=background,
        )

    if _process_existing_thread_mail(
        envelope,
        subject_info,
        capsule_state,
        config,
        task_root,
        mail_client,
        runner,
        background=background,
    ):
        return True

    LOGGER.info("Skipping unsupported mail: %s", envelope.subject)
    return False


def _process_batch(
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    runner: SerialTaskRunner,
    *,
    background: bool,
) -> dict[str, int]:
    fetched = mail_client.fetch_unseen_messages()
    stats = {"fetched": len(fetched), "processed": 0, "skipped": 0, "failed": 0}

    for envelope in fetched:
        if background:
            runner.collect_finished()
            runner.dispatch_ready()
        try:
            handled = _process_mail(
                envelope,
                config,
                task_root,
                mail_client,
                runner,
                background=background,
            )
        except Exception:
            LOGGER.exception("Unhandled failure while processing mail %s", getattr(envelope, "message_id", "unknown"))
            stats["failed"] += 1
            continue
        if handled:
            stats["processed"] += 1
        else:
            stats["skipped"] += 1
        if background:
            runner.collect_finished()
            runner.dispatch_ready()
    return stats


def process_once(
    config: AppConfig,
    *,
    base_dir: str | Path | None = None,
    mail_client: Any | None = None,
    dispatcher: Dispatcher | None = None,
) -> dict[str, int]:
    details = bootstrap(config, base_dir)
    task_root = Path(details["task_root"])
    client = mail_client or MailClient(config)
    active_dispatcher = dispatcher or _build_dispatcher(config)
    runner = SerialTaskRunner(
        task_root,
        active_dispatcher,
        max_concurrent_runs=config.max_concurrent_runs,
        codex_transport_default=config.codex_transport_default,
        recovery_callback_factory=_build_recovery_callback_factory(config, task_root, client),
    )
    return _process_batch(config, task_root, client, runner, background=False)


def run_forever(config: AppConfig, *, base_dir: str | Path | None = None) -> None:
    details = bootstrap(config, base_dir)
    task_root = Path(details["task_root"])
    client = MailClient(config)
    runner = SerialTaskRunner(
        task_root,
        _build_dispatcher(config),
        max_concurrent_runs=config.max_concurrent_runs,
        codex_transport_default=config.codex_transport_default,
        recovery_callback_factory=_build_recovery_callback_factory(config, task_root, client),
        monitor_window_manager=_build_monitor_window_manager(config, task_root=task_root, base_dir=base_dir),
    )
    while True:
        runner.collect_finished()
        runner.dispatch_ready()
        stats = _process_batch(config, task_root, client, runner, background=True)
        runner.collect_finished()
        runner.dispatch_ready()
        LOGGER.info(
            "Polling cycle complete. fetched=%s processed=%s skipped=%s failed=%s busy=%s",
            stats["fetched"],
            stats["processed"],
            stats["skipped"],
            stats["failed"],
            runner.is_busy(),
        )
        time.sleep(config.poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap or run the mail runner.")
    parser.add_argument("--config", help="Optional path to config.yaml")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="Process one batch of unseen mail and exit.")
    mode_group.add_argument("--loop", action="store_true", help="Poll the mailbox continuously.")
    args = parser.parse_args(argv)

    configure_logging()
    config = load_config(args.config)
    base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT

    if args.once:
        try:
            stats = process_once(config, base_dir=base_dir)
        except Exception:
            LOGGER.exception("Single-run processing failed.")
            return 1
        LOGGER.info(
            "Single-run processing completed. fetched=%s processed=%s skipped=%s failed=%s",
            stats["fetched"],
            stats["processed"],
            stats["skipped"],
            stats["failed"],
        )
        return 0 if stats["failed"] == 0 else 1

    if args.loop:
        try:
            run_forever(config, base_dir=base_dir)
        except Exception:
            LOGGER.exception("Poll loop terminated unexpectedly.")
            return 1
        return 0

    details = bootstrap(config, base_dir)
    LOGGER.info(
        "Bootstrap completed. task_root=%s templates=%s modules=%s",
        details["task_root"],
        details["template_count"],
        details["module_count"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
