"""Thread, workspace, and session persistence helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .models import MailAttachment, MailEnvelope, QuestionAnswer, QuestionItem, SessionState, ThreadState, WorkspaceState
from .parser import extract_session_tag, normalize_subject
from .status import (
    BACKEND_TRANSPORT_CLI,
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_DONE,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_KILLED,
    THREAD_STATUS_PAUSED,
    THREAD_STATUS_RUNNING,
)
from .workspace import WorkspaceManager

THREAD_PREFIX = "thread_"
RAW_MAIL_RE = re.compile(r"^raw_(\d+)\.json$")

THREAD_TO_SESSION_STATUS = {
    THREAD_STATUS_ACCEPTED: "queued",
    THREAD_STATUS_RUNNING: "running",
    THREAD_STATUS_AWAITING_USER_INPUT: "waiting_user",
    THREAD_STATUS_PAUSED: "paused",
    THREAD_STATUS_DONE: "done",
    THREAD_STATUS_FAILED: "failed",
    THREAD_STATUS_KILLED: "killed",
}


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
    root = task_root if task_root is not None else load_config().resolve_task_root()
    return WorkspaceManager(root)


def normalize_workspace_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return re.sub(r"[\\/]+", "/", text).rstrip("/").lower()


def build_workspace_norm(repo_path: str, workdir: str | None) -> str:
    repo_norm = normalize_workspace_value(repo_path)
    if repo_norm is None:
        raise ValueError("repo_path is required to build a workspace identity")
    workdir_norm = normalize_workspace_value(workdir)
    if workdir_norm:
        return f"{repo_norm}|{workdir_norm}"
    return repo_norm


def build_workspace_id(repo_path: str, workdir: str | None) -> str:
    workspace_norm = build_workspace_norm(repo_path, workdir)
    digest = hashlib.sha1(workspace_norm.encode("utf-8")).hexdigest()[:12]
    return f"workspace_{digest}"


def _iter_thread_ids(task_root: str | Path | None = None) -> list[str]:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return []
    return sorted(
        path.name
        for path in workspace.task_root.iterdir()
        if path.is_dir() and path.name.startswith(THREAD_PREFIX)
    )


def list_all_thread_states(task_root: str | Path | None = None) -> list[ThreadState]:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return []
    states = [load_thread_state(thread_id, workspace.task_root) for thread_id in _iter_thread_ids(workspace.task_root)]
    return sorted(states, key=lambda item: item.updated_at, reverse=True)


def _next_thread_id(task_root: str | Path | None = None) -> str:
    numbers: list[int] = []
    for thread_id in _iter_thread_ids(task_root):
        suffix = thread_id.removeprefix(THREAD_PREFIX)
        if suffix.isdigit():
            numbers.append(int(suffix))
    return f"{THREAD_PREFIX}{max(numbers, default=0) + 1:03d}"


def _payload_to_dict(payload: MailEnvelope | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, MailEnvelope):
        envelope = payload
        return _json_safe(
            {
                "message_id": envelope.message_id,
                "subject": envelope.subject,
                "from_addr": envelope.from_addr,
                "to_addr": envelope.to_addr,
                "date": envelope.date,
                "in_reply_to": envelope.in_reply_to,
                "references": list(envelope.references),
                "body_text": envelope.body_text,
                "attachments": [
                    {
                        "filename": item.filename,
                        "content_type": item.content_type,
                        "size_bytes": item.size_bytes,
                        "saved_path": item.saved_path,
                        "raw_saved_path": item.raw_saved_path,
                        "content_id": item.content_id,
                        "is_inline": item.is_inline,
                        "sha256": item.sha256,
                    }
                    for item in envelope.attachments
                ],
                "raw_headers": dict(envelope.raw_headers),
            }
        )
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


def _session_status_from_thread_status(status: str) -> str:
    return THREAD_TO_SESSION_STATUS.get(status, "queued")


def _build_workspace_state_from_thread(state: ThreadState, existing: WorkspaceState | None = None) -> WorkspaceState:
    now = state.updated_at or _timestamp()
    session_ids = list(existing.session_ids) if existing is not None else []
    session_id = state.session_id or state.thread_id
    if session_id not in session_ids:
        session_ids.append(session_id)
    active_session_ids = list(existing.active_session_ids) if existing is not None else []
    if existing is not None and not active_session_ids and existing.active_session_id:
        active_session_ids = [existing.active_session_id]
    queued_session_ids = list(existing.queued_session_ids) if existing is not None else []
    queued_session_ids = [item for item in queued_session_ids if item != session_id]
    active_session_ids = [item for item in active_session_ids if item != session_id]
    if state.lifecycle == "active" and state.status == THREAD_STATUS_RUNNING:
        active_session_ids.append(session_id)
    if state.lifecycle == "active" and (
        state.status == THREAD_STATUS_ACCEPTED or (state.queued_task_id and state.status != THREAD_STATUS_RUNNING)
    ):
        queued_session_ids.append(session_id)
    active_session_id = active_session_ids[0] if active_session_ids else None
    return WorkspaceState(
        workspace_id=state.workspace_id or build_workspace_id(state.repo_path, state.workdir),
        repo_path=state.repo_path,
        workdir=state.workdir,
        workspace_norm=state.workspace_norm or build_workspace_norm(state.repo_path, state.workdir),
        session_ids=session_ids,
        active_session_ids=active_session_ids,
        active_session_id=active_session_id,
        queued_session_ids=queued_session_ids,
        created_at=existing.created_at if existing is not None else (state.created_at or now),
        updated_at=now,
    )


def _build_session_state_from_thread(state: ThreadState) -> SessionState:
    pending_task_count = 1 if state.queued_task_id else 0
    derived_status = _session_status_from_thread_status(state.status)
    if state.queued_task_id and state.status not in {THREAD_STATUS_RUNNING, THREAD_STATUS_AWAITING_USER_INPUT}:
        derived_status = "queued"
    return SessionState(
        session_id=state.session_id or state.thread_id,
        workspace_id=state.workspace_id or build_workspace_id(state.repo_path, state.workdir),
        thread_id=state.thread_id,
        session_name=state.session_name or state.subject_norm,
        session_norm=state.session_norm or state.subject_norm,
        backend=state.backend,
        profile=state.profile,
        permission=state.permission,
        repo_path=state.repo_path,
        workdir=state.workdir,
        status=derived_status,
        current_task_id=state.current_task_id,
        last_task_snapshot_file=state.last_task_snapshot_file,
        queued_task_id=state.queued_task_id,
        queued_snapshot_file=state.queued_snapshot_file,
        pending_task_count=pending_task_count,
        history_files=list(state.history_files),
        last_summary=state.last_summary,
        lifecycle=state.lifecycle,
        last_active_at=state.last_active_at,
        last_progress_at=state.last_progress_at,
        backend_session_id=state.backend_session_id,
        backend_session_resumable=state.backend_session_resumable,
        backend_transport=state.backend_transport,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def save_workspace_state(state: WorkspaceState, task_root: str | Path | None = None) -> None:
    workspace = _workspace(task_root)
    workspace.ensure_workspace_layout(state.workspace_id)
    workspace.save_json(workspace.workspace_state_path(state.workspace_id), asdict(state))


def load_workspace_state(workspace_id: str, task_root: str | Path | None = None) -> WorkspaceState:
    workspace = _workspace(task_root)
    payload = workspace.load_json(workspace.workspace_state_path(workspace_id))
    return WorkspaceState(**payload)


def save_session_state(state: SessionState, task_root: str | Path | None = None) -> None:
    workspace = _workspace(task_root)
    workspace.ensure_workspace_layout(state.workspace_id)
    workspace.save_json(workspace.session_state_path(state.workspace_id, state.session_id), asdict(state))


def load_session_state(workspace_id: str, session_id: str, task_root: str | Path | None = None) -> SessionState:
    workspace = _workspace(task_root)
    payload = workspace.load_json(workspace.session_state_path(workspace_id, session_id))
    return SessionState(**payload)


def find_session(
    repo_path: str,
    workdir: str | None,
    session_name: str,
    task_root: str | Path | None = None,
) -> SessionState | None:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return None
    workspace_id = build_workspace_id(repo_path, workdir)
    sessions_dir = workspace.workspace_sessions_dir(workspace_id)
    if not sessions_dir.exists():
        return None
    session_norm = normalize_subject(session_name)
    matches: list[SessionState] = []
    for state_path in sorted(sessions_dir.glob("*.json")):
        payload = workspace.load_json(state_path)
        state = SessionState(**payload)
        if state.session_norm == session_norm:
            matches.append(state)
    if len(matches) == 1:
        return matches[0]
    return None


def find_thread_for_workspace_session(
    repo_path: str,
    workdir: str | None,
    session_name: str,
    task_root: str | Path | None = None,
) -> str | None:
    session_state = find_session(repo_path, workdir, session_name, task_root)
    if session_state is None:
        return None
    return session_state.thread_id


def list_workspace_sessions(
    repo_path: str,
    workdir: str | None,
    task_root: str | Path | None = None,
) -> list[SessionState]:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return []
    workspace_id = build_workspace_id(repo_path, workdir)
    sessions_dir = workspace.workspace_sessions_dir(workspace_id)
    if not sessions_dir.exists():
        return []
    sessions: list[SessionState] = []
    for state_path in sorted(sessions_dir.glob("*.json")):
        payload = workspace.load_json(state_path)
        sessions.append(SessionState(**payload))
    return sorted(sessions, key=lambda item: item.updated_at, reverse=True)


def find_workspace_session_by_id(
    repo_path: str,
    workdir: str | None,
    session_id: str,
    task_root: str | Path | None = None,
) -> SessionState | None:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return None
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    workspace_id = build_workspace_id(repo_path, workdir)
    session_path = workspace.session_state_path(workspace_id, normalized_session_id)
    if not session_path.exists():
        return None
    payload = workspace.load_json(session_path)
    return SessionState(**payload)


def sync_session_indexes(state: ThreadState, task_root: str | Path | None = None) -> None:
    if not state.workspace_id:
        return
    try:
        existing_workspace = load_workspace_state(state.workspace_id, task_root)
    except FileNotFoundError:
        existing_workspace = None
    save_workspace_state(_build_workspace_state_from_thread(state, existing_workspace), task_root)
    save_session_state(_build_session_state_from_thread(state), task_root)


def resolve_thread(
    envelope: MailEnvelope,
    task_root: str | Path | None = None,
    capsule_state: dict[str, Any] | None = None,
) -> str | None:
    workspace = _workspace(task_root)
    if not workspace.task_root.exists():
        return None

    if capsule_state:
        candidate_thread_id = str(capsule_state.get("thread_id", "")).strip()
        if candidate_thread_id and workspace.thread_state_path(candidate_thread_id).exists():
            return candidate_thread_id

        candidate_workspace_id = str(capsule_state.get("workspace_id", "")).strip()
        candidate_session_id = str(capsule_state.get("session_id", "")).strip()
        if candidate_workspace_id and candidate_session_id:
            session_path = workspace.session_state_path(candidate_workspace_id, candidate_session_id)
            if session_path.exists():
                return load_session_state(candidate_workspace_id, candidate_session_id, workspace.task_root).thread_id

    subject_session_id = extract_session_tag(envelope.subject)
    if subject_session_id and workspace.thread_state_path(subject_session_id).exists():
        return subject_session_id

    thread_ids = _iter_thread_ids(workspace.task_root)
    header_candidates = [
        candidate
        for candidate in [envelope.message_id, envelope.in_reply_to, *envelope.references]
        if candidate
    ]
    for candidate in header_candidates:
        for thread_id in thread_ids:
            if candidate in _thread_message_ids(thread_id, workspace.task_root):
                return thread_id
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
    lifecycle: str = "active",
    last_active_at: str | None = None,
    last_progress_at: str | None = None,
    thread_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    profile: str | None = None,
    permission: str | None = None,
    pending_question_id: str | None = None,
    pending_question_text: str | None = None,
    pending_choices: list[str] | None = None,
    pending_question_set_id: str | None = None,
    pending_questions: list[QuestionItem] | None = None,
    collected_answers: list[QuestionAnswer] | None = None,
    awaiting_since: str | None = None,
    paused_from_status: str | None = None,
    workspace_id: str | None = None,
    workspace_norm: str | None = None,
    session_id: str | None = None,
    session_name: str | None = None,
    session_norm: str | None = None,
    canonical_reply_recipient: str | None = None,
    queued_task_id: str | None = None,
    queued_snapshot_file: str | None = None,
    backend_session_id: str | None = None,
    backend_session_resumable: bool = False,
    backend_transport: str = BACKEND_TRANSPORT_CLI,
) -> ThreadState:
    workspace = _workspace(task_root)
    workspace.ensure_layout()
    actual_thread_id = thread_id or _next_thread_id(workspace.task_root)
    state_path = workspace.thread_state_path(actual_thread_id)
    if state_path.exists():
        raise FileExistsError(f"Thread already exists: {actual_thread_id}")

    now = _timestamp()
    resolved_workspace_id = workspace_id or build_workspace_id(repo_path, workdir)
    resolved_workspace_norm = workspace_norm or build_workspace_norm(repo_path, workdir)
    resolved_session_id = session_id or actual_thread_id
    resolved_session_name = session_name or subject_norm
    resolved_session_norm = session_norm or normalize_subject(resolved_session_name)
    state = ThreadState(
        thread_id=actual_thread_id,
        root_message_id=root_message_id,
        latest_message_id=latest_message_id,
        subject_norm=subject_norm,
        backend=backend,
        profile=profile,
        permission=permission,
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=current_task_id,
        last_task_snapshot_file=last_task_snapshot_file,
        status=status,
        history_files=list(history_files or []),
        last_summary=last_summary,
        lifecycle=lifecycle,
        last_active_at=last_active_at or updated_at or created_at or now,
        last_progress_at=last_progress_at or updated_at or created_at or now,
        pending_question_id=pending_question_id,
        pending_question_text=pending_question_text,
        pending_choices=list(pending_choices or []),
        pending_question_set_id=pending_question_set_id,
        pending_questions=list(pending_questions or []),
        collected_answers=list(collected_answers or []),
        awaiting_since=awaiting_since,
        paused_from_status=paused_from_status,
        workspace_id=resolved_workspace_id,
        workspace_norm=resolved_workspace_norm,
        session_id=resolved_session_id,
        session_name=resolved_session_name,
        session_norm=resolved_session_norm,
        canonical_reply_recipient=canonical_reply_recipient,
        backend_session_id=backend_session_id,
        backend_session_resumable=backend_session_resumable,
        backend_transport=backend_transport,
        queued_task_id=queued_task_id,
        queued_snapshot_file=queued_snapshot_file,
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
    sync_session_indexes(state, workspace.task_root)


def save_raw_mail(
    thread_id: str,
    payload: MailEnvelope | dict[str, Any],
    task_root: str | Path | None = None,
) -> Path:
    workspace = _workspace(task_root)
    workspace.ensure_thread_layout(thread_id)
    next_index = 1
    for raw_path in workspace.mail_dir(thread_id).glob("raw_*.json"):
        match = RAW_MAIL_RE.match(raw_path.name)
        if match:
            next_index = max(next_index, int(match.group(1)) + 1)
    target = workspace.mail_dir(thread_id) / f"raw_{next_index:03d}.json"
    if isinstance(payload, MailEnvelope) and payload.attachments:
        attachment_dir = workspace.mail_dir(thread_id) / f"raw_{next_index:03d}_attachments"
        attachment_dir.mkdir(parents=True, exist_ok=True)
        archived: list[MailAttachment] = []
        for index, attachment in enumerate(payload.attachments, start=1):
            safe_name = re.sub(r"[\\/]+", "_", attachment.filename)
            file_path = attachment_dir / f"{index:03d}_{safe_name}"
            file_path.write_bytes(attachment.content_bytes)
            archived.append(
                MailAttachment(
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    size_bytes=attachment.size_bytes,
                    saved_path=attachment.saved_path,
                    raw_saved_path=str(file_path),
                    content_id=attachment.content_id,
                    is_inline=attachment.is_inline,
                    sha256=attachment.sha256,
                    content_bytes=attachment.content_bytes,
                )
            )
        payload.attachments = archived
    return workspace.save_json(target, _payload_to_dict(payload))
