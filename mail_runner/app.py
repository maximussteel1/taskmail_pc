"""Application entrypoint for bootstrap, mail polling, and reply handling."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .adapters.codex_adapter import CodexAdapter
from .adapters.opencode_adapter import OpenCodeAdapter
from .config import AppConfig, load_config
from .context_layer import build_context
from .dispatcher import Dispatcher
from .intent_parser import parse_action
from .mail_io import MailClient, SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .models import MailEnvelope, ParsedMailAction, RunResult, TaskSnapshot, ThreadState
from .parser import parse_initial_task, parse_subject
from .reporter import (
    MAIL_STATUS_ACCEPTED,
    MAIL_STATUS_DONE,
    MAIL_STATUS_FAILED,
    MAIL_STATUS_KILLED,
    MAIL_STATUS_QUESTION,
    MAIL_STATUS_RUNNING,
    MAIL_STATUS_STATUS,
    build_status_mail,
    build_status_subject,
)
from .runner import SerialTaskRunner
from .state_capsule import parse_state_capsule
from .status import (
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_KILLED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_KILLED,
)
from .task_compiler import compile_task
from .thread_store import (
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

BOOTSTRAP_MODULES = (
    "mail_runner.config",
    "mail_runner.models",
    "mail_runner.mail_io",
    "mail_runner.parser",
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
)

TEMPLATE_FILES = (
    PACKAGE_ROOT / "templates" / "opencode_prompt.txt",
    PACKAGE_ROOT / "templates" / "codex_prompt.txt",
)


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


def bootstrap(config: AppConfig, base_dir: str | Path | None = None) -> dict[str, str | int]:
    task_root = config.resolve_task_root(base_dir or PROJECT_ROOT)
    task_root.mkdir(parents=True, exist_ok=True)

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
        codex_adapter=CodexAdapter(effective_config),
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
        seen_ids.add(message_id)
        message_ids.append(message_id)
    return message_ids


def _prune_previous_status_mails(mail_client: Any, config: AppConfig, task_root: Path, state: ThreadState, *, keep_message_id: str) -> None:
    if not config.prune_old_status_mails:
        return
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
        captured_reply = _load_captured_reply(task_root, result)
        body = build_status_mail(
            status_label,
            state,
            task_snapshot=task_snapshot,
            result=result,
            captured_reply=captured_reply,
            intro=intro,
            question_id=question_id,
            question_text=question_text,
            pending_choices=pending_choices,
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
            attachments=None,
            in_reply_to=reply_message_id,
            references=references,
            headers={SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
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
            )
            _prune_previous_status_mails(mail_client, config, task_root, state, keep_message_id=sent_message_id)
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


def _record_incoming_mail(state: ThreadState, envelope: MailEnvelope, task_root: Path) -> None:
    save_raw_mail(state.thread_id, envelope, task_root)
    state.latest_message_id = envelope.message_id
    state.updated_at = _timestamp()
    save_thread_state(state, task_root)


def _status_label_for_result(result: RunResult) -> str:
    if result.status == RUN_STATUS_SUCCESS:
        return MAIL_STATUS_DONE
    if result.status == RUN_STATUS_AWAITING_USER_INPUT:
        return MAIL_STATUS_QUESTION
    if result.status == RUN_STATUS_KILLED:
        return MAIL_STATUS_KILLED
    if result.status == RUN_STATUS_PAUSED:
        return MAIL_STATUS_STATUS
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
                f"- {session.session_id}{label} | {session.status} | {session.session_name} | {summary}"
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
            intro=accepted_intro,
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
    background: bool,
) -> bool:
    compiled = compile_task(
        action,
        state,
        snapshot,
        task_id=_generate_task_id(),
        now=_timestamp(),
        thread_id=runner.next_thread_id(),
    )
    if compiled is None:
        return False
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

    snapshot = TaskSnapshot(
        task_id=_generate_task_id(),
        thread_id=runner.next_thread_id(),
        backend=subject_info["backend"],
        profile=task_data["profile"],
        repo_path=task_data["repo_path"],
        workdir=task_data["workdir"],
        task_text=task_data["task_text"],
        acceptance=task_data["acceptance"],
        timeout_minutes=task_data["timeout_minutes"],
        mode=task_data["mode"],
        attachments=[],
        created_at=_timestamp(),
        updated_at=_timestamp(),
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
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
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
            background=background,
        )

    if state.status == THREAD_STATUS_AWAITING_USER_INPUT:
        if action.action == "KILL":
            state.status = THREAD_STATUS_KILLED
            state.last_summary = "Task was cancelled while awaiting user input."
            state.pending_question_id = None
            state.pending_question_text = None
            state.pending_choices = []
            state.awaiting_since = None
            state.updated_at = _timestamp()
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
                intro="This thread is awaiting an answer to the pending question. Reply with the answer before rerunning.",
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
                intro="Reply did not include an answer. Please answer the pending question below.",
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
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="No running task is available to kill for this thread.",
            reply_message_id=envelope.message_id,
            references=_build_references(envelope.message_id, envelope.references),
        )
        return True

    can_attempt_resume = bool(state.backend_session_id) and (
        state.backend_session_resumable or state.status == THREAD_STATUS_KILLED
    )
    if action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION"} and not can_attempt_resume:
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
            intro="This session does not have a resumable native backend context. Use /new or /rerun instead.",
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
            intro="This thread is waiting for an answer to the pending question below.",
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
    if action.action in {"CONTINUE_SESSION", "ANSWER_QUESTION"} and state.status == THREAD_STATUS_KILLED:
        accepted_intro = (
            "Resuming a session after kill. This is a risk recovery: the previous native context "
            "may have stopped mid-step, so you should verify the next result carefully."
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
    runner = SerialTaskRunner(task_root, active_dispatcher, max_concurrent_runs=config.max_concurrent_runs)
    return _process_batch(config, task_root, client, runner, background=False)


def run_forever(config: AppConfig, *, base_dir: str | Path | None = None) -> None:
    details = bootstrap(config, base_dir)
    task_root = Path(details["task_root"])
    client = MailClient(config)
    runner = SerialTaskRunner(task_root, _build_dispatcher(config), max_concurrent_runs=config.max_concurrent_runs)
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
