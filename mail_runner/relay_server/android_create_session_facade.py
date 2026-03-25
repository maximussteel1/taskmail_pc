"""Android-facing thin facade for CreateSessionCommand."""

from __future__ import annotations

import math
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .pc_command_store import PcCommandRecord
from .pc_control_protocol import build_command_dispatch, parse_pc_control_server_message
from .pc_control_runtime import PcCommandDispatchValidationError, PcControlRuntime

ANDROID_CREATE_SESSION_PATH = "/v1/android/create-session"
ANDROID_CREATE_SESSION_SCHEMA_VERSION = "taskmail-android-create-session-facade-v1"
_ACK_WAIT_SECONDS = 5.0
_ACK_POLL_SECONDS = 0.05


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AndroidCreateSessionRequestError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AndroidCreateSessionRequestError(f"{field_name} must be a string when provided")
    normalized = value.strip()
    return normalized or None


def _mapping_field(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = payload.get(field_name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AndroidCreateSessionRequestError(f"{field_name} must be a JSON object")
    return dict(value)


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise AndroidCreateSessionRequestError(f"{field_name} must be a positive integer when provided")
    return value


def _optional_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AndroidCreateSessionRequestError(f"{field_name} must be a list of strings when provided")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise AndroidCreateSessionRequestError(f"{field_name}[{index}] must be a string")
        normalized = item.strip()
        if normalized:
            items.append(normalized)
    return items


def _sanitize_identifier(value: str, *, prefix: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(value or "").strip())
    cleaned = cleaned.strip("._-")
    if not cleaned:
        cleaned = secrets.token_hex(4)
    return cleaned if cleaned.startswith(prefix) else f"{prefix}_{cleaned}"


def _generate_command_id() -> str:
    return f"cmd:android-create-session:{datetime.now().strftime('%Y%m%d_%H%M%S')}:{secrets.token_hex(4)}"


def _generate_session_id() -> str:
    return f"thread_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def _map_facade_error_code(code: str | None) -> str | None:
    normalized = str(code or "").strip()
    if not normalized:
        return None
    if normalized in {"unknown_workspace", "workspace_unavailable"}:
        return "workspace_unavailable"
    if normalized in {"unknown_pc", "pc_not_online", "pc_offline"}:
        return "pc_offline"
    if normalized == "unsupported_backend_transport":
        return "unsupported_backend"
    return normalized


def _build_submit_ack(
    *,
    ack_status: str,
    queue_position: int | None = None,
    reason: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "ack_status": ack_status,
        "queue_position": queue_position,
        "reason": reason,
        "error_code": _map_facade_error_code(error_code),
    }


def _build_submit_response(
    *,
    command_id: str,
    submit_ack: dict[str, Any],
    session_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": ANDROID_CREATE_SESSION_SCHEMA_VERSION,
        "status": submit_ack["ack_status"],
        "command_id": command_id,
        "submit_ack": submit_ack,
    }
    if session_binding is not None:
        payload["session_binding"] = session_binding
    return payload


def _wait_for_command_ack(
    *,
    runtime: PcControlRuntime,
    pc_id: str,
    command_id: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> PcCommandRecord | None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        record = runtime.command_store.get_command(pc_id, command_id)
        if record is not None and record.ack_status is not None:
            return record
        if time.monotonic() >= deadline:
            return record
        time.sleep(max(0.0, float(poll_seconds)))


class AndroidCreateSessionRequestError(ValueError):
    pass


class AndroidCreateSessionSubmitTimeout(RuntimeError):
    def __init__(self, *, command_id: str) -> None:
        self.command_id = command_id
        super().__init__("timed out waiting for submit ack from the target pc")


@dataclass(slots=True)
class AndroidCreateSessionCommand:
    pc_id: str
    workspace_id: str
    prompt: str
    execution_policy: dict[str, Any]
    mode: str | None = None
    timeout_seconds: int | None = None
    acceptance: list[str] = field(default_factory=list)
    repo_path: str | None = None
    workdir: str | None = None
    source: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AndroidCreateSessionCommand":
        if not isinstance(payload, dict):
            raise AndroidCreateSessionRequestError("request body must be a JSON object")
        return cls(
            pc_id=_require_text(payload.get("pc_id"), "pc_id"),
            workspace_id=_require_text(payload.get("workspace_id"), "workspace_id"),
            prompt=_require_text(payload.get("prompt"), "prompt"),
            execution_policy=_mapping_field(payload, "execution_policy"),
            mode=_optional_text(payload.get("mode"), "mode"),
            timeout_seconds=_optional_positive_int(payload.get("timeout_seconds"), "timeout_seconds"),
            acceptance=_optional_text_list(payload.get("acceptance"), "acceptance"),
            repo_path=_optional_text(payload.get("repo_path"), "repo_path"),
            workdir=_optional_text(payload.get("workdir"), "workdir"),
            source=_optional_text(payload.get("source"), "source"),
        )

    def build_command_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_text": self.prompt,
            "source": self.source or "android",
        }
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.timeout_seconds is not None:
            payload["timeout_seconds"] = self.timeout_seconds
            payload["timeout_minutes"] = max(1, int(math.ceil(self.timeout_seconds / 60.0)))
        if self.acceptance:
            payload["acceptance"] = list(self.acceptance)
        if self.repo_path is not None:
            payload["repo_path"] = self.repo_path
        if self.workdir is not None:
            payload["workdir"] = self.workdir
        return payload


def submit_android_create_session_command(
    payload: dict[str, Any],
    *,
    pc_control_runtime: PcControlRuntime,
    ack_wait_seconds: float = _ACK_WAIT_SECONDS,
    ack_poll_seconds: float = _ACK_POLL_SECONDS,
) -> dict[str, Any]:
    request = AndroidCreateSessionCommand.from_payload(payload)
    command_id = _generate_command_id()
    session_id = _generate_session_id()
    dispatch_payload = build_command_dispatch(
        message_id=f"android-create-session:{command_id}",
        trace_id=f"trace:android-create-session:{command_id}",
        pc_id=request.pc_id,
        connection_epoch=1,
        sent_at=_timestamp(),
        command_id=command_id,
        command_type="new_task",
        workspace_id=request.workspace_id,
        session_id=session_id,
        execution_policy=request.execution_policy,
        command_payload=request.build_command_payload(),
    )
    dispatch_message = parse_pc_control_server_message(dispatch_payload)
    try:
        record = pc_control_runtime.enqueue_command(dispatch_message)
    except PcCommandDispatchValidationError as exc:
        submit_ack = _build_submit_ack(
            ack_status="rejected",
            reason=exc.message,
            error_code=exc.code,
        )
        return _build_submit_response(command_id=command_id, submit_ack=submit_ack)

    record = _wait_for_command_ack(
        runtime=pc_control_runtime,
        pc_id=request.pc_id,
        command_id=record.command_id,
        timeout_seconds=ack_wait_seconds,
        poll_seconds=ack_poll_seconds,
    )
    if record is None or record.ack_status is None:
        raise AndroidCreateSessionSubmitTimeout(command_id=command_id)

    submit_ack = _build_submit_ack(
        ack_status=record.ack_status,
        queue_position=record.queue_position,
        reason=record.reason,
        error_code=record.error_code,
    )
    session_binding = None
    if record.ack_status in {"accepted", "accepted_but_queued"}:
        session_binding = {
            "session_id": record.session_id or session_id,
            "pc_id": record.pc_id,
            "workspace_id": record.workspace_id,
        }
    return _build_submit_response(
        command_id=record.command_id,
        submit_ack=submit_ack,
        session_binding=session_binding,
    )


__all__ = [
    "ANDROID_CREATE_SESSION_PATH",
    "ANDROID_CREATE_SESSION_SCHEMA_VERSION",
    "AndroidCreateSessionCommand",
    "AndroidCreateSessionRequestError",
    "AndroidCreateSessionSubmitTimeout",
    "submit_android_create_session_command",
]
