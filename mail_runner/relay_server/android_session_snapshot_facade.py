"""Android-facing thin facade for session snapshot/detail reads."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .android_session_projection import (
    build_android_session_record,
    build_pc_locator_index,
    coerce_task_root,
    resolve_pc_id,
)
from .android_history_round_projection import build_android_history_rounds
from .pc_control_runtime import PcControlRuntime
from .phase3_emitter import project_phase3_session_snapshot
from .phase3_subscription import (
    Phase3SubscriptionError,
    Phase3SubscribeSessionDetailRequest,
    ThreadStorePhase3SessionDetailProvider,
)

ANDROID_SESSION_SNAPSHOT_PATH = "/v1/android/session-snapshot"
ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION = "taskmail-android-session-snapshot-facade-v1"
_LATEST_SESSION_ACTION_TYPES = {"reply", "status", "pause", "resume", "kill", "end", "answers", "attachment_continuation"}


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalize_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_query_text(query: dict[str, list[str]], field_name: str) -> str | None:
    values = query.get(field_name) or []
    if not values:
        return None
    return _normalize_text(values[0])


@dataclass(slots=True)
class AndroidSessionSnapshotFacadeError(RuntimeError):
    status_code: int
    error_code: str
    error_message: str
    retryable: bool = False

    def to_response_payload(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retryable": self.retryable,
        }


def _build_request_from_query(query: dict[str, list[str]]) -> Phase3SubscribeSessionDetailRequest:
    workspace_id = _optional_query_text(query, "workspace_id")
    repo_path = _optional_query_text(query, "repo_path")
    workdir = _optional_query_text(query, "workdir")
    session_id = _optional_query_text(query, "session_id")
    thread_id = _optional_query_text(query, "thread_id")

    if session_id is None and thread_id is None:
        raise AndroidSessionSnapshotFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message="session_id or thread_id is required",
            retryable=False,
        )

    return Phase3SubscribeSessionDetailRequest(
        request_id=(
            f"android-session-snapshot:{session_id or thread_id or 'unknown'}:{datetime.now().strftime('%Y%m%d%H%M%S')}"
        ),
        workspace_id=workspace_id,
        repo_path=repo_path,
        workdir=workdir,
        session_id=session_id,
        thread_id=thread_id,
        last_known_sequence=0,
        reason="detail_open",
    )


def _map_subscription_error(exc: Phase3SubscriptionError) -> AndroidSessionSnapshotFacadeError:
    if exc.code == "session_not_found":
        return AndroidSessionSnapshotFacadeError(
            status_code=404,
            error_code=exc.code,
            error_message=exc.message,
            retryable=False,
        )
    if exc.reject:
        return AndroidSessionSnapshotFacadeError(
            status_code=409,
            error_code=exc.code,
            error_message=exc.message,
            retryable=False,
        )
    return AndroidSessionSnapshotFacadeError(
        status_code=400,
        error_code=exc.code,
        error_message=exc.message,
        retryable=False,
    )


def _latest_session_action_continuity(
    *,
    session_id: str,
    workspace_id: str,
    pc_control_runtime: PcControlRuntime | None,
) -> dict[str, Any] | None:
    if pc_control_runtime is None:
        return None
    matching_commands: list[dict[str, Any]] = []
    for command in pc_control_runtime.list_commands():
        if str(command.get("command_type") or "").strip() not in _LATEST_SESSION_ACTION_TYPES:
            continue
        if str(command.get("workspace_id") or "").strip() != workspace_id:
            continue
        if str(command.get("session_id") or "").strip() != session_id:
            continue
        if str(command.get("ack_status") or "").strip() == "" and not isinstance(command.get("result"), dict):
            continue
        matching_commands.append(command)
    if not matching_commands:
        return None
    matching_commands.sort(
        key=lambda item: (
            str(item.get("acked_at") or ""),
            str(item.get("created_at") or ""),
            str(item.get("command_id") or ""),
        ),
        reverse=True,
    )
    latest = matching_commands[0]
    continuity = {
        "command_id": str(latest.get("command_id") or "").strip(),
        "action_type": str(latest.get("command_type") or "").strip(),
        "submit_ack": {
            "ack_status": str(latest.get("ack_status") or "").strip() or None,
            "queue_position": latest.get("queue_position"),
            "reason": latest.get("reason"),
            "error_code": latest.get("error_code"),
        },
        "created_at": str(latest.get("created_at") or "").strip() or None,
        "acked_at": str(latest.get("acked_at") or "").strip() or None,
        "pc_id": str(latest.get("pc_id") or "").strip() or None,
    }
    result = latest.get("result")
    if isinstance(result, dict):
        continuity["result_status"] = str(result.get("final_status") or "").strip() or None
        structured_payload = result.get("structured_payload")
        if isinstance(structured_payload, dict) and structured_payload.get("kind") == "session_action_result":
            session_action_result = structured_payload.get("session_action_result")
            if isinstance(session_action_result, dict):
                continuity["session_action_result"] = dict(session_action_result)
    return continuity


def build_android_session_snapshot(
    *,
    query: dict[str, list[str]],
    task_root: str | Path,
    pc_control_runtime: PcControlRuntime | None = None,
) -> dict[str, Any]:
    generated_at = _timestamp()
    resolved_task_root = coerce_task_root(task_root)
    request = _build_request_from_query(query)
    provider = ThreadStorePhase3SessionDetailProvider(task_root=resolved_task_root)

    try:
        session_state, thread_state = provider.resolve_session_detail(request)
    except Phase3SubscriptionError as exc:
        raise _map_subscription_error(exc) from exc

    pc_id = resolve_pc_id(session_state, build_pc_locator_index(pc_control_runtime))
    return {
        "schema_version": ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": f"sess_detail_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": generated_at,
        "locator": {
            "pc_id": pc_id,
            "workspace_id": session_state.workspace_id,
            "session_id": session_state.session_id,
            "thread_id": session_state.thread_id,
        },
        "session": build_android_session_record(session_state, pc_id=pc_id),
        "session_snapshot": project_phase3_session_snapshot(
            session_state,
            thread_state,
            emitted_at=generated_at,
        )
        | {
            "latest_session_action": _latest_session_action_continuity(
                session_id=session_state.session_id,
                workspace_id=session_state.workspace_id,
                pc_control_runtime=pc_control_runtime,
            ),
            "history_rounds": build_android_history_rounds(
                session_state=session_state,
                thread_state=thread_state,
                task_root=resolved_task_root,
            ),
        },
    }


__all__ = [
    "ANDROID_SESSION_SNAPSHOT_PATH",
    "ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION",
    "AndroidSessionSnapshotFacadeError",
    "build_android_session_snapshot",
]
