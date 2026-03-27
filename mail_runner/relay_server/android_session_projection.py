"""Shared read-side helpers for Android-facing session facades."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import SessionState
from ..workspace import WorkspaceManager
from .pc_control_runtime import PcControlRuntime


def normalize_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def normalize_filter(values: list[str] | None) -> set[str] | None:
    normalized = {
        item
        for raw in values or []
        if (item := normalize_text(raw)) is not None
    }
    return normalized or None


def coerce_task_root(task_root: str | Path) -> Path:
    normalized = str(task_root or "").strip()
    if not normalized:
        raise ValueError("task_root must be a non-empty path")
    return Path(normalized)


def load_all_session_states(task_root: str | Path) -> list[SessionState]:
    workspace = WorkspaceManager(coerce_task_root(task_root))
    sessions_dir = workspace.workspaces_dir()
    if not sessions_dir.exists():
        return []
    sessions: list[SessionState] = []
    for state_path in sorted(sessions_dir.glob("*/sessions/*.json")):
        payload = workspace.load_json(state_path)
        sessions.append(SessionState(**payload))
    sessions.sort(
        key=lambda item: (
            item.last_progress_at or item.last_active_at or item.updated_at,
            item.updated_at,
            item.session_id,
        ),
        reverse=True,
    )
    sessions.sort(key=lambda item: item.lifecycle != "active")
    return sessions


@dataclass(slots=True)
class PcLocatorIndex:
    pc_by_thread_id: dict[str, str]
    unique_pc_by_session_id: dict[str, str]
    unique_pc_by_command_locator: dict[tuple[str, str], str]
    unique_pc_by_workspace_id: dict[str, str]


def _collapse_unique_pc_ids(pc_ids_by_key: dict[Any, set[str]]) -> dict[Any, str]:
    return {
        key: next(iter(pc_ids))
        for key, pc_ids in pc_ids_by_key.items()
        if len(pc_ids) == 1
    }


def build_pc_locator_index(pc_control_runtime: PcControlRuntime | None) -> PcLocatorIndex:
    if pc_control_runtime is None:
        return PcLocatorIndex(
            pc_by_thread_id={},
            unique_pc_by_session_id={},
            unique_pc_by_command_locator={},
            unique_pc_by_workspace_id={},
        )

    pc_by_thread_id: dict[str, str] = {}
    pc_ids_by_session_id: dict[str, set[str]] = {}
    for binding in pc_control_runtime.list_thread_bindings():
        pc_id = normalize_text(binding.get("pc_id"))
        thread_id = normalize_text(binding.get("thread_id"))
        session_id = normalize_text(binding.get("session_id"))
        if pc_id is None:
            continue
        if thread_id is not None:
            pc_by_thread_id.setdefault(thread_id, pc_id)
        if session_id is not None:
            pc_ids_by_session_id.setdefault(session_id, set()).add(pc_id)

    pc_ids_by_command_locator: dict[tuple[str, str], set[str]] = {}
    for command in pc_control_runtime.list_commands():
        pc_id = normalize_text(command.get("pc_id"))
        workspace_id = normalize_text(command.get("workspace_id"))
        session_id = normalize_text(command.get("session_id"))
        if pc_id is None or workspace_id is None or session_id is None:
            continue
        pc_ids_by_command_locator.setdefault((session_id, workspace_id), set()).add(pc_id)

    pc_ids_by_workspace_id: dict[str, set[str]] = {}
    for workspace in pc_control_runtime.list_workspaces():
        pc_id = normalize_text(workspace.get("pc_id"))
        workspace_id = normalize_text(workspace.get("workspace_id"))
        if pc_id is None or workspace_id is None:
            continue
        pc_ids_by_workspace_id.setdefault(workspace_id, set()).add(pc_id)

    return PcLocatorIndex(
        pc_by_thread_id=pc_by_thread_id,
        unique_pc_by_session_id=_collapse_unique_pc_ids(pc_ids_by_session_id),
        unique_pc_by_command_locator=_collapse_unique_pc_ids(pc_ids_by_command_locator),
        unique_pc_by_workspace_id=_collapse_unique_pc_ids(pc_ids_by_workspace_id),
    )


def resolve_pc_id(session_state: SessionState, locator_index: PcLocatorIndex) -> str | None:
    thread_match = locator_index.pc_by_thread_id.get(session_state.thread_id)
    if thread_match is not None:
        return thread_match
    session_match = locator_index.unique_pc_by_session_id.get(session_state.session_id)
    if session_match is not None:
        return session_match
    command_match = locator_index.unique_pc_by_command_locator.get(
        (session_state.session_id, session_state.workspace_id)
    )
    if command_match is not None:
        return command_match
    return locator_index.unique_pc_by_workspace_id.get(session_state.workspace_id)


def build_android_session_record(session_state: SessionState, *, pc_id: str | None) -> dict[str, Any]:
    return {
        "session_id": session_state.session_id,
        "thread_id": session_state.thread_id,
        "pc_id": pc_id,
        "workspace_id": session_state.workspace_id,
        "session_name": session_state.session_name,
        "status": session_state.status,
        "lifecycle": session_state.lifecycle,
        "backend": session_state.backend,
        "backend_transport": session_state.backend_transport,
        "profile": session_state.profile,
        "permission": session_state.permission,
        "repo_path": session_state.repo_path,
        "workdir": session_state.workdir,
        "current_task_id": session_state.current_task_id,
        "queued_task_id": session_state.queued_task_id,
        "pending_task_count": session_state.pending_task_count,
        "last_summary": session_state.last_summary,
        "last_active_at": session_state.last_active_at,
        "last_progress_at": session_state.last_progress_at,
        "backend_session_id": session_state.backend_session_id,
        "backend_session_resumable": session_state.backend_session_resumable,
        "created_at": session_state.created_at,
        "updated_at": session_state.updated_at,
    }


__all__ = [
    "PcLocatorIndex",
    "build_android_session_record",
    "build_pc_locator_index",
    "coerce_task_root",
    "load_all_session_states",
    "normalize_filter",
    "normalize_text",
    "resolve_pc_id",
]
