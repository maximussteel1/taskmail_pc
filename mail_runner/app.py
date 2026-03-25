"""Application entrypoint for bootstrap, mail polling, and reply handling."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .adapters.codex_adapter import CodexAdapter
from .adapters.codex_routing_adapter import CodexRoutingAdapter
from .adapters.codex_sdk_adapter import CodexSdkAdapter
from .adapters.opencode_adapter import OpenCodeAdapter
from .adapters.opencode_routing_adapter import OpenCodeRoutingAdapter
from .adapters.opencode_sdk_adapter import OpenCodeSdkAdapter
from .config import AppConfig, load_config
from .context_layer import build_context
from .dispatcher import Dispatcher
from .intent_parser import parse_action
from .mail_attachments import materialize_incoming_attachments
from .mail_io import MailClient, SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .mail_retention import SYNC_PROJECT_FOLDER_LIST_SUBJECT
from .models import MailEnvelope, ParsedMailAction, RunResult, TaskSnapshot, ThreadState
from .monitor_windows import MonitorWindowManager
from .outbound.service import build_references as _build_references, send_status_update as _send_status_update
from .question_utils import effective_pending_questions, merge_question_answers, missing_required_question_ids
from .parser import parse_initial_task, parse_subject
from .pc_control_plane_client import build_pc_control_plane_client
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
    build_status_subject,
)
from .runtime_control import (
    list_runner_restart_request_paths,
    list_thread_close_request_paths,
    list_thread_kill_request_paths,
    read_runner_restart_request,
    read_thread_close_request,
    read_thread_kill_request,
    write_runner_restart_request,
)
from .runner import SerialTaskRunner
from .session_action_closeout import (
    ACTION_TYPE_HEADER,
    RECEIPT_ID_HEADER,
    build_target_session_identity,
    target_session_identity_from_headers,
    upsert_session_action_closeout,
)
from .session_semantics import effective_thread_status, thread_can_attempt_resume
from .state_capsule import parse_state_capsule
from .stream_events import load_stream_events, stream_events_path
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
    find_workspace_session_by_id,
    list_all_thread_states,
    list_workspace_sessions,
    load_thread_state,
    resolve_thread,
    save_raw_mail,
    save_thread_state,
)
from .transport_probe_mail import is_transport_probe_mail, record_transport_probe_observation

LOGGER = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SYNC_REPLY_STATE_FILENAME = "sync_reply_state.json"
_MAX_TRACKED_SYNC_REPLY_IDS = 100
_NON_ENDABLE_ACTIVE_THREAD_STATUSES = {THREAD_STATUS_ACCEPTED, THREAD_STATUS_RUNNING}
_CONFIG_PATH_ENV = "MAIL_RUNNER_CONFIG"
_LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc

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
    "mail_runner.runtime_control",
    "mail_runner.runner",
    "mail_runner.adapters.base",
    "mail_runner.adapters.mock_adapter",
    "mail_runner.adapters.opencode_adapter",
    "mail_runner.adapters.opencode_routing_adapter",
    "mail_runner.adapters.opencode_sdk_adapter",
    "mail_runner.adapters.codex_adapter",
    "mail_runner.adapters.codex_routing_adapter",
    "mail_runner.adapters.codex_sdk_adapter",
)

TEMPLATE_FILES = (
    PACKAGE_ROOT / "templates" / "opencode_prompt.txt",
    PACKAGE_ROOT / "templates" / "codex_prompt.txt",
)
_RUNTIME_DIR_ENV = "MAIL_RUNNER_RUNTIME_DIR"
_DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "_tmp_live_mail_runner"
_DIRECT_POST_CREATION_HEADER = "X-TaskMail-Direct"
_DIRECT_POST_CREATION_REQUEST_ID_HEADER = "X-TaskMail-Relay-Request-Id"
_DIRECT_POST_CREATION_PACKET_ID_HEADER = "X-TaskMail-Relay-Packet-Id"
_DIRECT_POST_CREATION_ACTION_TYPES = frozenset({"status", "reply"})


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


def _current_time_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_mail_datetime(value: datetime | str) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        parsed = None
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            parsed = None
        if parsed is None:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _new_task_mail_skip_reason(envelope: MailEnvelope, config: AppConfig) -> str | None:
    max_age_minutes = max(0, int(config.new_task_max_age_minutes))
    if max_age_minutes <= 0:
        return None

    received_at = _normalize_mail_datetime(envelope.date)
    if received_at is None:
        return (
            "freshness guard requires a parseable Date header "
            f"(raw_date={envelope.date!r})"
        )

    age = _current_time_utc() - received_at
    if age < timedelta(0) or age <= timedelta(minutes=max_age_minutes):
        return None

    age_minutes = age.total_seconds() / 60.0
    return (
        f"received_at={received_at.isoformat()} "
        f"age_minutes={age_minutes:.1f} "
        f"limit_minutes={max_age_minutes}"
    )


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
        opencode_adapter=OpenCodeRoutingAdapter(
            cli_adapter=OpenCodeAdapter(effective_config),
            sdk_adapter=OpenCodeSdkAdapter(effective_config),
        ),
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
        buffer_lines=config.monitor_window_buffer_lines,
        history_limit=config.monitor_window_history_limit,
    )


def _resolve_runtime_dir() -> Path:
    runtime_dir = os.getenv(_RUNTIME_DIR_ENV)
    if runtime_dir:
        return Path(runtime_dir).resolve()
    return _DEFAULT_RUNTIME_DIR.resolve()


def _process_runtime_thread_kill_requests(
    runner: SerialTaskRunner,
    *,
    runtime_dir: Path | None,
) -> dict[str, int]:
    stats = {"seen": 0, "accepted": 0, "ignored": 0, "invalid": 0}
    if runtime_dir is None:
        return stats

    for request_path in list_thread_kill_request_paths(runtime_dir):
        stats["seen"] += 1
        try:
            request = read_thread_kill_request(request_path)
        except Exception:
            LOGGER.exception("Unable to parse thread kill request: %s", request_path)
            stats["invalid"] += 1
            request_path.unlink(missing_ok=True)
            continue

        thread_id = request["thread_id"]
        task_id = request["task_id"]
        source = request["source"]
        if runner.kill_thread(thread_id, expected_task_id=task_id):
            LOGGER.info(
                "Accepted local thread kill request. thread=%s task=%s source=%s",
                thread_id,
                task_id,
                source,
            )
            stats["accepted"] += 1
        else:
            LOGGER.info(
                "Ignored local thread kill request without matching active run. thread=%s task=%s source=%s",
                thread_id,
                task_id,
                source,
            )
            stats["ignored"] += 1
        request_path.unlink(missing_ok=True)
    return stats


def _process_runtime_thread_close_requests(
    runner: SerialTaskRunner,
    *,
    runtime_dir: Path | None,
) -> dict[str, int]:
    stats = {"seen": 0, "completed": 0, "pending": 0, "ignored": 0, "invalid": 0}
    if runtime_dir is None:
        return stats

    for request_path in list_thread_close_request_paths(runtime_dir):
        stats["seen"] += 1
        try:
            request = read_thread_close_request(request_path)
        except Exception:
            LOGGER.exception("Unable to parse thread close request: %s", request_path)
            stats["invalid"] += 1
            request_path.unlink(missing_ok=True)
            continue

        thread_id = request["thread_id"]
        requested_task_id = request["task_id"]
        source = request["source"]
        try:
            state = load_thread_state(thread_id, runner.workspace.task_root)
        except FileNotFoundError:
            LOGGER.info(
                "Ignored local thread close request for missing thread. thread=%s task=%s source=%s",
                thread_id,
                requested_task_id,
                source,
            )
            stats["ignored"] += 1
            request_path.unlink(missing_ok=True)
            continue

        current_task_id = str(state.current_task_id or "").strip()
        if current_task_id != requested_task_id:
            LOGGER.info(
                "Ignored stale local thread close request. thread=%s requested_task=%s current_task=%s source=%s",
                thread_id,
                requested_task_id,
                current_task_id or "-",
                source,
            )
            stats["ignored"] += 1
            request_path.unlink(missing_ok=True)
            continue

        if state.lifecycle != "active":
            LOGGER.info(
                "Completed local thread close request because session is already inactive. thread=%s task=%s source=%s",
                thread_id,
                requested_task_id,
                source,
            )
            stats["completed"] += 1
            request_path.unlink(missing_ok=True)
            continue

        if state.status in _NON_ENDABLE_ACTIVE_THREAD_STATUSES:
            killed = runner.kill_thread(thread_id, expected_task_id=current_task_id)
            try:
                state = load_thread_state(thread_id, runner.workspace.task_root)
            except FileNotFoundError:
                state = None
            if state is None:
                LOGGER.info(
                    "Ignored local thread close request after thread disappeared. thread=%s task=%s source=%s",
                    thread_id,
                    requested_task_id,
                    source,
                )
                stats["ignored"] += 1
                request_path.unlink(missing_ok=True)
                continue
            if state.current_task_id != requested_task_id:
                LOGGER.info(
                    "Ignored local thread close request after task changed. thread=%s requested_task=%s current_task=%s source=%s",
                    thread_id,
                    requested_task_id,
                    state.current_task_id or "-",
                    source,
                )
                stats["ignored"] += 1
                request_path.unlink(missing_ok=True)
                continue
            if state.lifecycle == "active" and state.status in _NON_ENDABLE_ACTIVE_THREAD_STATUSES:
                if state.status == THREAD_STATUS_ACCEPTED and not killed:
                    LOGGER.info(
                        "Completing local thread close request for accepted thread without an active backend run. thread=%s task=%s source=%s",
                        thread_id,
                        requested_task_id,
                        source,
                    )
                else:
                    LOGGER.info(
                        "Local thread close request is waiting for thread shutdown. thread=%s task=%s source=%s kill_requested=%s",
                        thread_id,
                        requested_task_id,
                        source,
                        killed,
                    )
                    stats["pending"] += 1
                    continue

        _set_thread_lifecycle(state, lifecycle="ended")
        save_thread_state(state, runner.workspace.task_root)
        LOGGER.info(
            "Completed local thread close request. thread=%s task=%s source=%s final_status=%s",
            thread_id,
            requested_task_id,
            source,
            state.status,
        )
        stats["completed"] += 1
        request_path.unlink(missing_ok=True)
    return stats


def _manage_mail_runner_script_path() -> Path:
    return (PROJECT_ROOT / "scripts" / "manage_mail_runner.ps1").resolve()


def _schedule_detached_runner_restart(*, config_path: str, runtime_dir: Path) -> tuple[bool, str]:
    script_path = _manage_mail_runner_script_path()
    if not script_path.exists():
        return False, f"Restart helper script not found: {script_path}"

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "detach-restart",
        "-ConfigPath",
        str(Path(config_path).resolve()),
        "-RuntimeDir",
        str(runtime_dir.resolve()),
        "-NoPopup",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    detail = stdout or stderr or f"exit={completed.returncode}"
    if completed.returncode != 0:
        return False, detail
    return True, detail


def _maybe_schedule_requested_runner_restart(runtime_dir: Path | None) -> bool:
    if runtime_dir is None:
        return False

    request_paths = list_runner_restart_request_paths(runtime_dir)
    if not request_paths:
        return False

    valid_requests: list[tuple[Path, dict[str, str]]] = []
    for request_path in request_paths:
        try:
            valid_requests.append((request_path, read_runner_restart_request(request_path)))
        except Exception:
            LOGGER.exception("Unable to parse runner restart request: %s", request_path)
            request_path.unlink(missing_ok=True)

    if not valid_requests:
        return False

    config_path = os.getenv(_CONFIG_PATH_ENV) or ""
    if not config_path.strip():
        LOGGER.error("Unable to schedule detached runner restart because %s is missing.", _CONFIG_PATH_ENV)
        return False

    primary_path, primary_request = valid_requests[0]
    ok, detail = _schedule_detached_runner_restart(config_path=config_path, runtime_dir=runtime_dir)
    if not ok:
        LOGGER.error(
            "Unable to schedule detached runner restart. request=%s source=%s detail=%s",
            primary_request.get("request_id") or primary_path.name,
            primary_request.get("source") or "unknown",
            detail,
        )
        return False

    for request_path, _ in valid_requests:
        request_path.unlink(missing_ok=True)

    LOGGER.warning(
        "Scheduled detached runner restart. request=%s source=%s thread=%s message=%s detail=%s",
        primary_request.get("request_id") or primary_path.name,
        primary_request.get("source") or "unknown",
        primary_request.get("thread_id") or "-",
        primary_request.get("message_id") or "-",
        detail,
    )
    return True


def _sleep_with_runtime_control(
    seconds: float,
    *,
    runner: SerialTaskRunner,
    runtime_dir: Path | None,
    mail_client: MailClient | None = None,
) -> bool:
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        sleep_seconds = min(1.0, remaining)
        if mail_client is not None:
            event_detected = mail_client.wait_for_new_messages(sleep_seconds)
        else:
            time.sleep(sleep_seconds)
            event_detected = False
        runner.collect_finished()
        runner.dispatch_ready()
        _process_runtime_thread_kill_requests(runner, runtime_dir=runtime_dir)
        _process_runtime_thread_close_requests(runner, runtime_dir=runtime_dir)
        if event_detected:
            return True
        remaining -= sleep_seconds
    return False


def _materialize_envelope_attachments(envelope: MailEnvelope, repo_path: str, workdir: str | None) -> MailEnvelope:
    if not envelope.attachments:
        return envelope
    return materialize_incoming_attachments(
        envelope,
        repo_path=repo_path,
        workdir=workdir,
        auto_create_workdir=False,
    )


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
def _collect_live_assistant_output(events: list[Any]) -> str:
    chunks: list[str] = []
    latest_completed_text = ""
    for event in events:
        if event.kind == "assistant.delta" and event.delta:
            chunks.append(event.delta)
            continue
        if event.kind != "assistant.completed":
            continue
        text = str(event.text or "").strip()
        if not chunks and text:
            latest_completed_text = text
    return "".join(chunks).strip() or latest_completed_text


def _build_running_status_summary(task_root: Path, state: ThreadState) -> str:
    _ = task_root
    _ = state
    return "Running."


def _build_running_status_reply(task_root: Path, state: ThreadState) -> str:
    current_task_id = str(state.current_task_id or "").strip()
    if not current_task_id:
        return "No assistant output yet."
    try:
        events = load_stream_events(stream_events_path(task_root, state.thread_id, current_task_id))
    except Exception:
        LOGGER.exception("Unable to load live stream events for status query on %s", state.thread_id)
        return "No assistant output yet."
    assistant_output = _collect_live_assistant_output(events)
    if assistant_output:
        return assistant_output
    return "No assistant output yet."


def _build_non_running_status_summary(state: ThreadState) -> str:
    if state.status == THREAD_STATUS_ACCEPTED:
        return "This session is not currently running. It is accepted and waiting in the local queue."
    if state.status == THREAD_STATUS_AWAITING_USER_INPUT:
        return "This session is not currently running. It is waiting for your answer before the backend can continue."
    if state.status == THREAD_STATUS_PAUSED:
        return "This session is not currently running. It is paused and requires /resume before it can continue."
    lifecycle_suffix = f" Lifecycle: {state.lifecycle}." if state.lifecycle != "active" else ""
    return f"This session is not currently running. Current thread status: {state.status}.{lifecycle_suffix}"


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


def _handle_transport_probe_mail(
    envelope: MailEnvelope,
    task_root: Path,
) -> bool:
    try:
        observed_probe, observation_path = record_transport_probe_observation(
            task_root,
            envelope,
            observed_at=_timestamp(),
        )
    except Exception:
        LOGGER.exception(
            "Unable to record transport-probe mailbox observation. message_id=%s subject=%s",
            getattr(envelope, "message_id", "unknown"),
            getattr(envelope, "subject", ""),
        )
        return True

    LOGGER.info(
        "Recorded transport-probe mailbox observation. probe_id=%s request_id=%s packet_id=%s path=%s",
        observed_probe.probe_id,
        observed_probe.request_id,
        observed_probe.packet_id,
        observation_path,
    )
    return True


def _record_incoming_mail(state: ThreadState, envelope: MailEnvelope, task_root: Path) -> None:
    save_raw_mail(state.thread_id, envelope, task_root)
    state.latest_message_id = envelope.message_id
    state.updated_at = _timestamp()
    save_thread_state(state, task_root)


def _archive_incoming_mail(thread_id: str, envelope: MailEnvelope, task_root: Path) -> None:
    save_raw_mail(thread_id, envelope, task_root)


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
                "Targeted commands stay within this workspace.",
                "Use /status <session_id>, /last <session_id>, /continue <session_id>, /resume <session_id>, /pause <session_id>, /end <session_id>, or /kill <session_id>.",
                "Use /restart-runner from any session when you need the hosted mail loop to restart itself locally.",
                "Targeted replies continue on the target session's own mail chain.",
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


def _resolve_target_thread_state(
    state: ThreadState,
    *,
    target_session_id: str | None,
    task_root: Path,
) -> ThreadState | None:
    normalized_target = str(target_session_id or "").strip()
    if not normalized_target:
        return state
    current_session_id = state.session_id or state.thread_id
    if normalized_target == current_session_id:
        return state
    target_session = find_workspace_session_by_id(state.repo_path, state.workdir, normalized_target, task_root)
    if target_session is None:
        return None
    return load_thread_state(target_session.thread_id, task_root)


def _set_thread_lifecycle(state: ThreadState, *, lifecycle: str) -> None:
    state.lifecycle = lifecycle
    state.updated_at = _timestamp()
    state.last_progress_at = state.updated_at


def _enforce_active_session_cap(
    task_root: Path,
    *,
    target_thread_id: str,
    max_active_sessions: int,
) -> list[str]:
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
    if desired_active_count <= max_active_sessions:
        return []

    ended_thread_ids: list[str] = []
    while desired_active_count > max_active_sessions:
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
    accepted_reply_message_id: str | None = None,
    accepted_references: list[str] | None = None,
    accepted_reply_to_existing: bool = True,
    accepted_intro: str | None = None,
) -> bool:
    auto_ended_thread_ids = _enforce_active_session_cap(
        task_root,
        target_thread_id=snapshot.thread_id,
        max_active_sessions=config.max_active_sessions,
    )
    effective_accepted_intro = accepted_intro
    if auto_ended_thread_ids:
        cap_note = (
            "Auto-ended least recently active session(s) to keep the active working set within "
            f"{config.max_active_sessions}: "
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
            reply_message_id=accepted_reply_message_id,
            references=accepted_references,
            reply_to_existing=accepted_reply_to_existing,
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
        default_transport_for_backend=config.default_transport_for_backend,
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
        accepted_reply_to_existing=False,
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
        accepted_reply_message_id=envelope.message_id,
        accepted_references=_build_references(envelope.message_id, envelope.references),
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

    invoking_state = load_thread_state(thread_id, task_root)
    invoking_context = build_context(envelope, invoking_state, task_root)
    action = parse_action(invoking_context, subject_info)
    target_state = invoking_state
    target_reply_chain = False

    if action.target_session_id and action.action not in {"LIST_SESSIONS", "NEW_SESSION", "UNKNOWN"}:
        resolved_target_state = _resolve_target_thread_state(
            invoking_state,
            target_session_id=action.target_session_id,
            task_root=task_root,
        )
        if resolved_target_state is None:
            _record_incoming_mail(invoking_state, envelope, task_root)
            _send_status_update(
                mail_client,
                config,
                task_root,
                to_addr=envelope.from_addr,
                subject_text=invoking_state.session_name or invoking_state.subject_norm,
                status_label=MAIL_STATUS_STATUS,
                state=invoking_state,
                task_snapshot=invoking_context["latest_snapshot"],
                result=invoking_context["latest_result"],
                intro=(
                    f"Session '{action.target_session_id}' was not found in this workspace. "
                    "Use /sessions to list the available sessions."
                ),
                reply_message_id=envelope.message_id,
                references=_build_references(envelope.message_id, envelope.references),
            )
            return True
        target_state = resolved_target_state
        target_reply_chain = target_state.thread_id != invoking_state.thread_id

    if envelope.attachments:
        envelope = _materialize_envelope_attachments(envelope, target_state.repo_path, target_state.workdir)

    context = build_context(envelope, target_state, task_root)
    snapshot = context["latest_snapshot"]
    subject_text = _subject_text_for_thread(subject_info, envelope, target_state)
    action = parse_action(context, subject_info)

    if action.action != "NEW_SESSION":
        if target_reply_chain:
            _archive_incoming_mail(invoking_state.thread_id, envelope, task_root)
        else:
            _record_incoming_mail(invoking_state, envelope, task_root)

    return _handle_existing_action(
        envelope,
        config,
        task_root,
        mail_client,
        runner,
        state=target_state,
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
        target_reply_chain=target_reply_chain,
    )


def _direct_post_creation_closeout_context(envelope: MailEnvelope) -> dict[str, Any] | None:
    raw_headers = envelope.raw_headers if isinstance(envelope.raw_headers, dict) else {}
    if str(raw_headers.get(_DIRECT_POST_CREATION_HEADER) or "").strip() != "1":
        return None

    request_id = str(raw_headers.get(_DIRECT_POST_CREATION_REQUEST_ID_HEADER) or "").strip()
    action_type = str(raw_headers.get(ACTION_TYPE_HEADER) or "").strip().lower()
    if not request_id or action_type not in _DIRECT_POST_CREATION_ACTION_TYPES:
        return None

    return {
        "action_type": action_type,
        "request_id": request_id,
        "packet_id": str(raw_headers.get(_DIRECT_POST_CREATION_PACKET_ID_HEADER) or "").strip() or None,
        "receipt_id": str(raw_headers.get(RECEIPT_ID_HEADER) or "").strip() or None,
        "target_session_identity": target_session_identity_from_headers(raw_headers),
    }


def _fallback_target_session_identity(state: ThreadState) -> dict[str, str] | None:
    return build_target_session_identity(
        workspace_id=state.workspace_id,
        session_id=state.session_id or state.thread_id,
        thread_id=state.thread_id,
    )


def _maybe_upsert_direct_post_creation_mail_closeout(
    envelope: MailEnvelope,
    task_root: Path,
    *,
    state: ThreadState,
    status_label: str,
    subject_text: str,
    terminal_mail_message_id: str | None,
) -> None:
    context = _direct_post_creation_closeout_context(envelope)
    if context is None:
        return

    terminal_mail_subject = None
    if terminal_mail_message_id is not None:
        terminal_mail_subject = build_status_subject(
            status_label,
            subject_text,
            state.session_id or state.thread_id,
        )

    try:
        upsert_session_action_closeout(
            task_root,
            thread_id=state.thread_id,
            action_type=context["action_type"],
            request_id=context["request_id"],
            ingress_message_id=envelope.message_id,
            packet_id=context["packet_id"],
            receipt_id=context["receipt_id"],
            terminal_mail_subject=terminal_mail_subject,
            terminal_mail_message_id=terminal_mail_message_id,
            last_summary=state.last_summary,
            target_session_identity=context["target_session_identity"] or _fallback_target_session_identity(state),
        )
    except Exception:
        LOGGER.exception(
            "Unable to upsert direct post-creation closeout from mail response. thread=%s request_id=%s",
            state.thread_id,
            context["request_id"],
        )


def _send_existing_action_status_update(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    *,
    to_addr: str,
    subject_text: str,
    status_label: str,
    state: ThreadState,
    task_snapshot: TaskSnapshot,
    result: RunResult | None = None,
    intro: str | None = None,
    target_reply_chain: bool = False,
    summary_override: str | None = None,
    reply_override: str | None = None,
) -> str | None:
    sent_message_id = _send_status_update(
        mail_client,
        config,
        task_root,
        to_addr=to_addr,
        subject_text=subject_text,
        status_label=status_label,
        state=state,
        task_snapshot=task_snapshot,
        result=result,
        intro=intro,
        summary_override=summary_override,
        reply_override=reply_override,
        reply_message_id=None if target_reply_chain else envelope.message_id,
        references=None if target_reply_chain else _build_references(envelope.message_id, envelope.references),
    )
    _maybe_upsert_direct_post_creation_mail_closeout(
        envelope,
        task_root,
        state=state,
        status_label=status_label,
        subject_text=subject_text,
        terminal_mail_message_id=sent_message_id,
    )
    return sent_message_id


def _send_current_status_query(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    *,
    state: ThreadState,
    snapshot: TaskSnapshot,
    subject_text: str,
    target_reply_chain: bool,
) -> bool:
    status_label = MAIL_STATUS_PAUSED if state.status == THREAD_STATUS_PAUSED else MAIL_STATUS_STATUS
    summary_override = (
        _build_running_status_summary(task_root, state)
        if state.status == THREAD_STATUS_RUNNING
        else _build_non_running_status_summary(state)
    )
    reply_override = _build_running_status_reply(task_root, state) if state.status == THREAD_STATUS_RUNNING else None
    _send_existing_action_status_update(
        envelope,
        config,
        task_root,
        mail_client,
        to_addr=envelope.from_addr,
        subject_text=subject_text,
        status_label=status_label,
        state=state,
        task_snapshot=snapshot,
        result=None,
        target_reply_chain=target_reply_chain,
        summary_override=summary_override,
        reply_override=reply_override,
    )
    return True


def _send_last_result_query(
    envelope: MailEnvelope,
    config: AppConfig,
    task_root: Path,
    mail_client: Any,
    *,
    state: ThreadState,
    snapshot: TaskSnapshot,
    latest_result: RunResult | None,
    subject_text: str,
    target_reply_chain: bool,
) -> bool:
    if latest_result is None:
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=None,
            intro="No persisted local result is available for this session yet.",
            target_reply_chain=target_reply_chain,
            summary_override="There is no previous result to show.",
        )
        return True

    _send_existing_action_status_update(
        envelope,
        config,
        task_root,
        mail_client,
        to_addr=envelope.from_addr,
        subject_text=subject_text,
        status_label=MAIL_STATUS_STATUS,
        state=state,
        task_snapshot=snapshot,
        result=latest_result,
        intro="Latest local result for this session. This is a local lookup only; no backend call was made.",
        target_reply_chain=target_reply_chain,
    )
    return True


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
    target_reply_chain: bool = False,
) -> bool:

    if action.action == "STATUS_QUERY":
        return _send_current_status_query(
            envelope,
            config,
            task_root,
            mail_client,
            state=state,
            snapshot=snapshot,
            subject_text=subject_text,
            target_reply_chain=target_reply_chain,
        )

    if action.action == "LAST_RESULT_QUERY":
        return _send_last_result_query(
            envelope,
            config,
            task_root,
            mail_client,
            state=state,
            snapshot=snapshot,
            latest_result=latest_result,
            subject_text=subject_text,
            target_reply_chain=target_reply_chain,
        )

    if action.action == "RESTART_RUNNER":
        if not background:
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="Runner restart is only available while the hosted background mail loop is running. This local one-shot process will not queue a restart request.",
                target_reply_chain=target_reply_chain,
                summary_override="Runner restart is unavailable in one-shot mode.",
            )
            return True

        runtime_dir = _resolve_runtime_dir()
        write_runner_restart_request(
            runtime_dir,
            source="mail",
            thread_id=state.thread_id,
            message_id=envelope.message_id,
        )
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="A local mail runner restart has been scheduled. The host will restart itself shortly via an external launcher. Any currently running sessions may be interrupted and later recover as resumable sessions.",
            target_reply_chain=target_reply_chain,
            summary_override="Runner restart scheduled.",
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
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This session is already accepted or running. Wait for it to stop naturally, or use /kill before /end.",
                target_reply_chain=target_reply_chain,
            )
            return True
        if state.lifecycle == "ended":
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This session is already ended. Reply with /resume if you want to bring it back into the active working set.",
                target_reply_chain=target_reply_chain,
            )
            return True
        _set_thread_lifecycle(state, lifecycle="ended")
        save_thread_state(state, task_root)
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="This session is now ended and removed from the active working set. Reply with /resume if you want to continue this same thread later.",
            target_reply_chain=target_reply_chain,
        )
        return True

    if action.action == "PAUSE_SESSION":
        if state.status in {THREAD_STATUS_ACCEPTED, THREAD_STATUS_RUNNING}:
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This session is already accepted or running. Wait for it to stop naturally, or use /kill instead of /pause.",
                target_reply_chain=target_reply_chain,
            )
            return True
        if state.status == THREAD_STATUS_PAUSED:
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_PAUSED,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro=_paused_intro(state),
                target_reply_chain=target_reply_chain,
            )
            return True
        state.paused_from_status = state.status
        state.status = THREAD_STATUS_PAUSED
        state.updated_at = _timestamp()
        state.last_progress_at = state.updated_at
        save_thread_state(state, task_root)
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_PAUSED,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro=_paused_intro(state),
            target_reply_chain=target_reply_chain,
        )
        return True

    if state.status == THREAD_STATUS_PAUSED:
        pending_questions = effective_pending_questions(state, fallback_task_id=snapshot.task_id)
        if action.action in {"CONTINUE_SESSION", "UNKNOWN"}:
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_PAUSED,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro=_paused_intro(state, ignored_reply=True),
                target_reply_chain=target_reply_chain,
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
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_QUESTION,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="The session is no longer paused, but it still needs answers before the backend can continue.",
                target_reply_chain=target_reply_chain,
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
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_KILLED,
                state=state,
                task_snapshot=snapshot,
                intro="The pending task was cancelled while waiting for user input.",
                target_reply_chain=target_reply_chain,
            )
            return True
        if action.action == "RERUN":
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_STATUS,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="This thread is awaiting an answer to the pending question set. Reply with the answer before rerunning.",
                target_reply_chain=target_reply_chain,
            )
            return True
        if action.action == "UNKNOWN":
            _send_existing_action_status_update(
                envelope,
                config,
                task_root,
                mail_client,
                to_addr=envelope.from_addr,
                subject_text=subject_text,
                status_label=MAIL_STATUS_QUESTION,
                state=state,
                task_snapshot=snapshot,
                result=latest_result,
                intro="Reply did not include a valid answer. Please answer the pending question set below.",
                target_reply_chain=target_reply_chain,
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
                    _send_existing_action_status_update(
                        envelope,
                        config,
                        task_root,
                        mail_client,
                        to_addr=envelope.from_addr,
                        subject_text=subject_text,
                        status_label=MAIL_STATUS_QUESTION,
                        state=state,
                        task_snapshot=snapshot,
                        result=latest_result,
                        intro="Some answers were saved, but there are invalid or unknown entries. Please review the pending question set below.",
                        target_reply_chain=target_reply_chain,
                    )
                    return True
                missing_question_ids = missing_required_question_ids(pending_questions, merged_answers)
                if missing_question_ids:
                    state.collected_answers = merged_answers
                    state.updated_at = _timestamp()
                    state.last_progress_at = state.updated_at
                    save_thread_state(state, task_root)
                    _send_existing_action_status_update(
                        envelope,
                        config,
                        task_root,
                        mail_client,
                        to_addr=envelope.from_addr,
                        subject_text=subject_text,
                        status_label=MAIL_STATUS_QUESTION,
                        state=state,
                        task_snapshot=snapshot,
                        result=latest_result,
                        intro="Some answers were saved, but required questions are still missing. Please complete the remaining question set below.",
                        target_reply_chain=target_reply_chain,
                    )
                    return True

    if action.action == "KILL":
        target_task_id = direct_kill_target or state.current_task_id
        if runner.kill(target_task_id):
            return True
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
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
            target_reply_chain=target_reply_chain,
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
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
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
            target_reply_chain=target_reply_chain,
        )
        return True

    if state.status == THREAD_STATUS_AWAITING_USER_INPUT and action.action != "ANSWER_QUESTION":
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_QUESTION,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="This thread is waiting for an answer to the pending question set below.",
            target_reply_chain=target_reply_chain,
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
        default_transport_for_backend=config.default_transport_for_backend,
    )
    if compiled is None:
        _send_existing_action_status_update(
            envelope,
            config,
            task_root,
            mail_client,
            to_addr=envelope.from_addr,
            subject_text=subject_text,
            status_label=MAIL_STATUS_STATUS,
            state=state,
            task_snapshot=snapshot,
            result=latest_result,
            intro="Reply was not understood. No changes were applied.",
            target_reply_chain=target_reply_chain,
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
        latest_message_id=state.latest_message_id,
        subject_norm=state.subject_norm,
        session_name=state.session_name or subject_text,
        save_incoming_on_accept=False,
        background=background,
        accepted_reply_message_id=None if target_reply_chain else envelope.message_id,
        accepted_references=None if target_reply_chain else _build_references(envelope.message_id, envelope.references),
        accepted_reply_to_existing=True,
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
    if is_transport_probe_mail(envelope):
        return _handle_transport_probe_mail(envelope, task_root)

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
        skip_reason = _new_task_mail_skip_reason(envelope, config)
        if skip_reason:
            LOGGER.warning(
                "Ignoring new task mail outside freshness window. message_id=%s subject=%s detail=%s",
                envelope.message_id,
                envelope.subject,
                skip_reason,
            )
            return False
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
        max_active_sessions=config.max_active_sessions,
        max_active_sessions_per_workspace=config.max_active_sessions_per_workspace,
        opencode_transport_default=config.opencode_transport_default,
        codex_transport_default=config.codex_transport_default,
        recovery_callback_factory=_build_recovery_callback_factory(config, task_root, client),
    )
    return _process_batch(config, task_root, client, runner, background=False)


def run_forever(config: AppConfig, *, base_dir: str | Path | None = None) -> None:
    details = bootstrap(config, base_dir)
    task_root = Path(details["task_root"])
    runtime_dir = _resolve_runtime_dir()
    client = MailClient(config)
    runner = SerialTaskRunner(
        task_root,
        _build_dispatcher(config),
        max_active_sessions=config.max_active_sessions,
        max_active_sessions_per_workspace=config.max_active_sessions_per_workspace,
        opencode_transport_default=config.opencode_transport_default,
        codex_transport_default=config.codex_transport_default,
        recovery_callback_factory=_build_recovery_callback_factory(config, task_root, client),
        monitor_window_manager=_build_monitor_window_manager(config, task_root=task_root, base_dir=base_dir),
    )
    pc_control_client = build_pc_control_plane_client(config, runner=runner)
    if pc_control_client is not None:
        LOGGER.info("Starting pc-control sidecar. pc_id=%s relay_url=%s", config.relay_client_id, config.relay_url)
        pc_control_client.start()
    try:
        while True:
            runner.collect_finished()
            runner.dispatch_ready()
            _process_runtime_thread_kill_requests(runner, runtime_dir=runtime_dir)
            _process_runtime_thread_close_requests(runner, runtime_dir=runtime_dir)
            restart_scheduled = _maybe_schedule_requested_runner_restart(runtime_dir)
            stats = _process_batch(config, task_root, client, runner, background=True)
            runner.collect_finished()
            runner.dispatch_ready()
            control_stats = _process_runtime_thread_kill_requests(runner, runtime_dir=runtime_dir)
            close_stats = _process_runtime_thread_close_requests(runner, runtime_dir=runtime_dir)
            restart_scheduled = _maybe_schedule_requested_runner_restart(runtime_dir) or restart_scheduled
            LOGGER.info(
                "Polling cycle complete. fetched=%s processed=%s skipped=%s failed=%s busy=%s restart_scheduled=%s control_seen=%s control_accepted=%s control_ignored=%s control_invalid=%s close_seen=%s close_completed=%s close_pending=%s close_ignored=%s close_invalid=%s",
                stats["fetched"],
                stats["processed"],
                stats["skipped"],
                stats["failed"],
                runner.is_busy(),
                restart_scheduled,
                control_stats["seen"],
                control_stats["accepted"],
                control_stats["ignored"],
                control_stats["invalid"],
                close_stats["seen"],
                close_stats["completed"],
                close_stats["pending"],
                close_stats["ignored"],
                close_stats["invalid"],
            )
            woke_for_mail = _sleep_with_runtime_control(
                config.poll_seconds,
                runner=runner,
                runtime_dir=runtime_dir,
                mail_client=client,
            )
            if woke_for_mail:
                LOGGER.info(
                    "Mailbox wait ended early because mailbox sync was requested. receive_mode=%s",
                    client.receive_mode(),
                )
    finally:
        if pc_control_client is not None:
            pc_control_client.stop()
        client.close()


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
