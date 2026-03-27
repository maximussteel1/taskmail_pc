"""Android-facing thin facade for session list reads."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .android_session_projection import (
    build_android_session_record,
    build_pc_locator_index,
    load_all_session_states,
    normalize_filter,
    resolve_pc_id,
)
from .pc_control_runtime import PcControlRuntime

ANDROID_SESSIONS_PATH = "/v1/android/sessions"
ANDROID_SESSIONS_SCHEMA_VERSION = "taskmail-android-sessions-facade-v1"
DEFAULT_REFRESH_AFTER_SECONDS = 15


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _matches_filters(
    *,
    session_state: Any,
    pc_id: str | None,
    include_ended: bool,
    pc_filter: set[str] | None,
    workspace_filter: set[str] | None,
    session_filter: set[str] | None,
    thread_filter: set[str] | None,
) -> bool:
    if not include_ended and session_state.lifecycle == "ended":
        return False
    if pc_filter is not None and pc_id not in pc_filter:
        return False
    if workspace_filter is not None and session_state.workspace_id not in workspace_filter:
        return False
    if session_filter is not None and session_state.session_id not in session_filter:
        return False
    if thread_filter is not None and session_state.thread_id not in thread_filter:
        return False
    return True


def build_android_sessions_snapshot(
    *,
    task_root: str | Path,
    pc_control_runtime: PcControlRuntime | None = None,
    include_ended: bool = False,
    pc_ids: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    session_ids: list[str] | None = None,
    thread_ids: list[str] | None = None,
    refresh_after_seconds: int = DEFAULT_REFRESH_AFTER_SECONDS,
) -> dict[str, Any]:
    generated_at = _timestamp()
    locator_index = build_pc_locator_index(pc_control_runtime)
    sessions = load_all_session_states(task_root)

    normalized_pc_filter = normalize_filter(pc_ids)
    normalized_workspace_filter = normalize_filter(workspace_ids)
    normalized_session_filter = normalize_filter(session_ids)
    normalized_thread_filter = normalize_filter(thread_ids)

    records: list[dict[str, Any]] = []
    for session_state in sessions:
        resolved_pc_id = resolve_pc_id(session_state, locator_index)
        if not _matches_filters(
            session_state=session_state,
            pc_id=resolved_pc_id,
            include_ended=include_ended,
            pc_filter=normalized_pc_filter,
            workspace_filter=normalized_workspace_filter,
            session_filter=normalized_session_filter,
            thread_filter=normalized_thread_filter,
        ):
            continue
        records.append(build_android_session_record(session_state, pc_id=resolved_pc_id))

    return {
        "schema_version": ANDROID_SESSIONS_SCHEMA_VERSION,
        "snapshot_id": f"sess_snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": generated_at,
        "refresh_after_seconds": max(1, int(refresh_after_seconds)),
        "session_count": len(records),
        "sessions": records,
    }


__all__ = [
    "ANDROID_SESSIONS_PATH",
    "ANDROID_SESSIONS_SCHEMA_VERSION",
    "DEFAULT_REFRESH_AFTER_SECONDS",
    "build_android_sessions_snapshot",
]
