"""Android-facing thin facade for authoritative session history reads."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .android_session_snapshot_facade import (
    AndroidSessionSnapshotFacadeError,
    build_android_session_snapshot,
)
from .pc_control_runtime import PcControlRuntime

ANDROID_SESSION_HISTORY_PATH = "/v1/android/session-history"
ANDROID_SESSION_HISTORY_SCHEMA_VERSION = "taskmail-android-session-history-facade-v1"


def build_android_session_history(
    *,
    query: dict[str, list[str]],
    task_root: str | Path,
    pc_control_runtime: PcControlRuntime | None = None,
) -> dict[str, Any]:
    snapshot_payload = build_android_session_snapshot(
        query=query,
        task_root=task_root,
        pc_control_runtime=pc_control_runtime,
    )
    return {
        "schema_version": ANDROID_SESSION_HISTORY_SCHEMA_VERSION,
        "history_id": f"sess_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": snapshot_payload["generated_at"],
        "locator": dict(snapshot_payload["locator"]),
        "session": dict(snapshot_payload["session"]),
        "history_rounds": list(snapshot_payload["session_snapshot"]["history_rounds"]),
    }


__all__ = [
    "ANDROID_SESSION_HISTORY_PATH",
    "ANDROID_SESSION_HISTORY_SCHEMA_VERSION",
    "AndroidSessionSnapshotFacadeError",
    "build_android_session_history",
]
