"""Thread persistence and lookup helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import MailEnvelope, ThreadState
from .status import THREAD_STATUS_ACCEPTED
from .workspace import WorkspaceManager

THREAD_PREFIX = "thread_"
_RAW_MAIL_RE = re.compile(r"^raw_(\d+)\.json$")


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _workspace(task_root: str | Path | None = None) -> WorkspaceManager:
    root = task_root if task_root is not None else AppConfig().resolve_task_root()
    return WorkspaceManager(root)


def _iter_thread_ids(task_root: str | Path | None = None) -> list[str]:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return []
    return sorted(
        path.name
        for path in workspace.task_root.iterdir()
        if path.is_dir() and path.name.startswith(THREAD_PREFIX)
    )


def _next_thread_id(task_root: str | Path | None = None) -> str:
    numbers: list[int] = []
    for thread_id in _iter_thread_ids(task_root):
        suffix = thread_id.removeprefix(THREAD_PREFIX)
        if suffix.isdigit():
            numbers.append(int(suffix))
    return f"{THREAD_PREFIX}{max(numbers, default=0) + 1:03d}"


def _payload_to_dict(payload: MailEnvelope | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, MailEnvelope):
        return _json_safe(asdict(payload))
    return _json_safe(dict(payload))

def _thread_message_ids(thread_id: str, task_root: str | Path | None = None) -> set[str]:
    workspace = _workspace(task_root)
    message_ids: set[str] = set()
    state_path = workspace.thread_state_path(thread_id)
    if state_path.exists():
        state = load_thread_state(thread_id, workspace.task_root)
        message_ids.update({state.root_message_id, state.latest_message_id})
    for raw_path in workspace.mail_dir(thread_id).glob("raw_*.json"):
        data = workspace.load_json(raw_path)
        message_id = data.get("message_id")
        if isinstance(message_id, str) and message_id.strip():
            message_ids.add(message_id.strip())
    return message_ids


def resolve_thread(
    envelope: MailEnvelope,
    task_root: str | Path | None = None,
    capsule_state: dict[str, Any] | None = None,
) -> str | None:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return None

    thread_ids = _iter_thread_ids(workspace.task_root)
    header_candidates = [candidate for candidate in [envelope.message_id, envelope.in_reply_to, *envelope.references] if candidate]
    for candidate in header_candidates:
        for thread_id in thread_ids:
            if candidate in _thread_message_ids(thread_id, workspace.task_root):
                return thread_id

    if capsule_state:
        candidate_thread_id = str(capsule_state.get("thread_id", "")).strip()
        if candidate_thread_id and workspace.thread_state_path(candidate_thread_id).exists():
            return candidate_thread_id

    subject_norm = str(capsule_state.get("subject_norm", "")).strip() if capsule_state else ""
    if not subject_norm:
        subject_norm = str(envelope.subject or "").strip()
    if subject_norm:
        from .parser import normalize_subject

        normalized = normalize_subject(subject_norm)
        matches: list[str] = []
        for thread_id in thread_ids:
            state_path = workspace.thread_state_path(thread_id)
            if not state_path.exists():
                continue
            state = load_thread_state(thread_id, workspace.task_root)
            if state.subject_norm == normalized:
                matches.append(thread_id)
        if len(matches) == 1:
            return matches[0]
    return None


def create_thread(
    *,
    root_message_id: str,
    latest_message_id: str,
    subject_norm: str,
    backend: str,
    repo_path: str,
    workdir: str | None,
    current_task_id: str,
    last_task_snapshot_file: str,
    task_root: str | Path | None = None,
    status: str = THREAD_STATUS_ACCEPTED,
    history_files: list[str] | None = None,
    last_summary: str | None = None,
    thread_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    profile: str | None = None,
    pending_question_id: str | None = None,
    pending_question_text: str | None = None,
    pending_choices: list[str] | None = None,
    awaiting_since: str | None = None,
) -> ThreadState:
    workspace = _workspace(task_root)
    workspace.ensure_layout()
    actual_thread_id = thread_id or _next_thread_id(workspace.task_root)
    state_path = workspace.thread_state_path(actual_thread_id)
    if state_path.exists():
        raise FileExistsError(f"Thread already exists: {actual_thread_id}")

    now = _timestamp()
    state = ThreadState(
        thread_id=actual_thread_id,
        root_message_id=root_message_id,
        latest_message_id=latest_message_id,
        subject_norm=subject_norm,
        backend=backend,
        profile=profile,
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=current_task_id,
        last_task_snapshot_file=last_task_snapshot_file,
        status=status,
        history_files=list(history_files or []),
        last_summary=last_summary,
        pending_question_id=pending_question_id,
        pending_question_text=pending_question_text,
        pending_choices=list(pending_choices or []),
        awaiting_since=awaiting_since,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    save_thread_state(state, workspace.task_root)
    return state


def load_thread_state(thread_id: str, task_root: str | Path | None = None) -> ThreadState:
    workspace = _workspace(task_root)
    data = json.loads(workspace.thread_state_path(thread_id).read_text(encoding="utf-8"))
    return ThreadState(**data)


def save_thread_state(state: ThreadState, task_root: str | Path | None = None) -> None:
    workspace = _workspace(task_root)
    workspace.ensure_thread_layout(state.thread_id)
    workspace.save_json(workspace.thread_state_path(state.thread_id), asdict(state))


def save_raw_mail(
    thread_id: str,
    payload: MailEnvelope | dict[str, Any],
    task_root: str | Path | None = None,
) -> Path:
    workspace = _workspace(task_root)
    workspace.ensure_thread_layout(thread_id)
    next_index = 1
    for raw_path in workspace.mail_dir(thread_id).glob("raw_*.json"):
        match = _RAW_MAIL_RE.match(raw_path.name)
        if match:
            next_index = max(next_index, int(match.group(1)) + 1)
    target = workspace.mail_dir(thread_id) / f"raw_{next_index:03d}.json"
    return workspace.save_json(target, _payload_to_dict(payload))
