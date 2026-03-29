"""Android-facing thin facade for current-session session-action commands."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import ThreadState
from ..session_action_closeout import build_target_session_identity
from .android_session_projection import build_pc_locator_index, resolve_pc_id
from .android_session_action_request_store import (
    AndroidSessionActionRequestRecord,
    InMemoryAndroidSessionActionRequestStore,
)
from .pc_command_store import PcCommandConflictError, PcCommandRecord
from .pc_control_protocol import build_command_dispatch, parse_pc_control_server_message
from .pc_control_runtime import PcCommandDispatchValidationError, PcControlRuntime
from .phase3_subscription import (
    Phase3SubscribeSessionDetailRequest,
    Phase3SubscriptionError,
    ThreadStorePhase3SessionDetailProvider,
)

ANDROID_SESSION_ACTION_PATH = "/v1/android/session-action"
ANDROID_SESSION_ACTION_SCHEMA_VERSION = "taskmail-android-session-action-facade-v1"
_ACK_WAIT_SECONDS = 5.0
_ACK_WAIT_KEEPALIVE_PADDING_SECONDS = 5.0
_ACK_POLL_SECONDS = 0.05
_SUPPORTED_ACTIONS = {"reply", "status", "pause", "resume", "kill", "end", "answers", "attachment_continuation"}
_EMPTY_OBJECT_ACTIONS = {"pause", "resume", "kill", "end"}
_ALLOWED_REJECT_ERROR_CODES = {
    "direct_temporarily_unavailable",
    "invalid_command_payload",
    "pc_offline",
    "session_binding_unresolved",
    "session_identity_mismatch",
    "session_recipient_unresolved",
    "unsupported_backend",
    "unsupported_permission",
    "unsupported_profile",
    "validation_failed",
    "workspace_unavailable",
}


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AndroidSessionActionFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message=f"{field_name} must be a non-empty string",
            retryable=False,
        )
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AndroidSessionActionFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message=f"{field_name} must be a string when provided",
            retryable=False,
        )
    normalized = value.strip()
    return normalized or None


def _required_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AndroidSessionActionFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message=f"{field_name} must be a JSON object",
            retryable=False,
        )
    return dict(value)


def _normalized_question_answers(value: Any, field_name: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise AndroidSessionActionFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message=f"{field_name} must be a non-empty list",
            retryable=False,
        )
    normalized: list[dict[str, str]] = []
    seen_question_ids: set[str] = set()
    for index, item in enumerate(value):
        item_field_name = f"{field_name}[{index}]"
        if not isinstance(item, dict):
            raise AndroidSessionActionFacadeError(
                status_code=400,
                error_code="invalid_payload",
                error_message=f"{item_field_name} must be an object",
                retryable=False,
            )
        question_id = _require_text(item.get("question_id"), f"{item_field_name}.question_id")
        if question_id in seen_question_ids:
            raise AndroidSessionActionFacadeError(
                status_code=400,
                error_code="invalid_payload",
                error_message=f"{item_field_name}.question_id duplicates an earlier answer entry",
                retryable=False,
            )
        value_text = _require_text(item.get("value"), f"{item_field_name}.value")
        seen_question_ids.add(question_id)
        normalized.append(
            {
                "question_id": question_id,
                "value": value_text,
            }
        )
    return sorted(normalized, key=lambda item: item["question_id"])


def _normalized_attachment_items(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise AndroidSessionActionFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message=f"{field_name} must be a non-empty list",
            retryable=False,
        )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_field_name = f"{field_name}[{index}]"
        if not isinstance(item, dict):
            raise AndroidSessionActionFacadeError(
                status_code=400,
                error_code="invalid_payload",
                error_message=f"{item_field_name} must be an object",
                retryable=False,
            )
        name = _require_text(item.get("name"), f"{item_field_name}.name")
        content_type = _require_text(item.get("content_type"), f"{item_field_name}.content_type")
        content_bytes_b64 = _require_text(item.get("content_bytes_b64"), f"{item_field_name}.content_bytes_b64")
        size_bytes = item.get("size_bytes")
        if size_bytes is not None and (not isinstance(size_bytes, int) or size_bytes < 0):
            raise AndroidSessionActionFacadeError(
                status_code=400,
                error_code="invalid_payload",
                error_message=f"{item_field_name}.size_bytes must be a non-negative integer",
                retryable=False,
            )
        normalized.append(
            {
                "name": name,
                "content_type": content_type,
                "content_bytes_b64": content_bytes_b64,
                "size_bytes": size_bytes,
            }
        )
    return normalized


def _normalize_reject_error_code(code: str | None) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        raise AndroidSessionActionContractError("rejected submit_ack must include an error_code")
    if normalized in {"unknown_workspace", "workspace_unavailable"}:
        normalized = "workspace_unavailable"
    if normalized in {"unknown_pc", "pc_not_online", "pc_offline"}:
        normalized = "pc_offline"
    if normalized == "unsupported_backend_transport":
        normalized = "unsupported_backend"
    if normalized == "session_identity_unresolved":
        normalized = "session_binding_unresolved"
    if normalized not in _ALLOWED_REJECT_ERROR_CODES:
        raise AndroidSessionActionContractError(f"unmapped rejected submit_ack error_code: {normalized}")
    return normalized


def _build_submit_ack(
    *,
    ack_status: str,
    queue_position: int | None = None,
    reason: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    normalized_error_code = (
        _normalize_reject_error_code(error_code)
        if str(ack_status or "").strip() == "rejected"
        else None
    )
    return {
        "ack_status": ack_status,
        "queue_position": queue_position,
        "reason": reason,
        "error_code": normalized_error_code,
    }


def _build_submit_response(
    *,
    command_id: str,
    submit_ack: dict[str, Any],
    target_session_identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": ANDROID_SESSION_ACTION_SCHEMA_VERSION,
        "status": submit_ack["ack_status"],
        "command_id": command_id,
        "submit_ack": submit_ack,
        "target_session_identity": dict(target_session_identity),
    }


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


def _sanitize_identifier(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:32] or "request"


def _command_id_for_request(request_id: str) -> str:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:12]
    return f"cmd:android-session-action:{_sanitize_identifier(request_id)}:{digest}"


def _request_fingerprint(request: "AndroidSessionActionCommand") -> str:
    normalized_payload = {
        "action": request.action,
        "target": {
            "workspace_id": request.target.workspace_id,
            "session_id": request.target.session_id,
            "thread_id": request.target.thread_id,
        },
        request.action: request.action_payload,
    }
    rendered = json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _should_record_facade_result(status_code: int) -> bool:
    return status_code in {200, 404, 409}


@dataclass(slots=True)
class AndroidSessionActionSubmitResponse:
    status_code: int
    payload: dict[str, Any]


@dataclass(slots=True)
class AndroidSessionActionFacadeError(RuntimeError):
    status_code: int
    error_code: str
    error_message: str
    retryable: bool = False
    command_id: str | None = None
    target_session_identity: dict[str, Any] | None = None

    def to_response_payload(self) -> dict[str, Any]:
        payload = {
            "status": "error",
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retryable": self.retryable,
        }
        if self.command_id is not None:
            payload["command_id"] = self.command_id
        if self.target_session_identity is not None:
            payload["target_session_identity"] = dict(self.target_session_identity)
        return payload


class AndroidSessionActionContractError(RuntimeError):
    pass


class AndroidSessionActionSubmitTimeout(RuntimeError):
    def __init__(
        self,
        *,
        command_id: str,
        target_session_identity: dict[str, Any] | None = None,
    ) -> None:
        self.command_id = command_id
        self.target_session_identity = dict(target_session_identity or {}) or None
        super().__init__("timed out waiting for submit ack from the target pc")


@dataclass(slots=True)
class AndroidSessionActionTarget:
    workspace_id: str | None
    session_id: str
    thread_id: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AndroidSessionActionTarget":
        data = _required_mapping(payload, "target")
        scope = _optional_text(data.get("scope"), "target.scope")
        if scope is not None and scope != "current_session":
            raise AndroidSessionActionFacadeError(
                status_code=400,
                error_code="invalid_payload",
                error_message="target.scope must equal current_session when provided",
                retryable=False,
            )
        return cls(
            workspace_id=_optional_text(data.get("workspace_id"), "target.workspace_id"),
            session_id=_require_text(data.get("session_id"), "target.session_id"),
            thread_id=_optional_text(data.get("thread_id"), "target.thread_id"),
        )


@dataclass(slots=True)
class AndroidSessionActionCommand:
    request_id: str
    action: str
    target: AndroidSessionActionTarget
    action_payload: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AndroidSessionActionCommand":
        if not isinstance(payload, dict):
            raise AndroidSessionActionFacadeError(
                status_code=400,
                error_code="invalid_payload",
                error_message="request body must be a JSON object",
                retryable=False,
            )
        request_id = _require_text(payload.get("request_id"), "request_id")
        action = _require_text(payload.get("action"), "action").lower()
        if action not in _SUPPORTED_ACTIONS:
            raise AndroidSessionActionFacadeError(
                status_code=422,
                error_code="unsupported_action",
                error_message=f"unsupported session-action action: {action}",
                retryable=False,
            )
        target = AndroidSessionActionTarget.from_payload(payload.get("target"))
        if action == "reply":
            reply_payload = _required_mapping(payload.get("reply"), "reply")
            return cls(
                request_id=request_id,
                action=action,
                target=target,
                action_payload={
                    "reply_text": _require_text(reply_payload.get("reply_text"), "reply.reply_text"),
                },
            )
        if action in _EMPTY_OBJECT_ACTIONS:
            action_payload = payload.get(action)
            if action_payload is None:
                normalized_action_payload: dict[str, Any] = {}
            else:
                normalized_action_payload = _required_mapping(action_payload, action)
            if normalized_action_payload:
                raise AndroidSessionActionFacadeError(
                    status_code=400,
                    error_code="invalid_payload",
                    error_message=f"{action} must be an empty object in the current first slice",
                    retryable=False,
                )
            return cls(
                request_id=request_id,
                action=action,
                target=target,
                action_payload=normalized_action_payload,
            )
        if action == "answers":
            answers_payload = _required_mapping(payload.get("answers"), "answers")
            return cls(
                request_id=request_id,
                action=action,
                target=target,
                action_payload={
                    "question_answers": _normalized_question_answers(
                        answers_payload.get("question_answers"),
                        "answers.question_answers",
                    )
                },
            )
        if action == "attachment_continuation":
            attachment_payload = _required_mapping(payload.get("attachment_continuation"), "attachment_continuation")
            normalized_attachment_payload: dict[str, Any] = {
                "attachments": _normalized_attachment_items(
                    attachment_payload.get("attachments"),
                    "attachment_continuation.attachments",
                )
            }
            reply_text = _optional_text(
                attachment_payload.get("reply_text"),
                "attachment_continuation.reply_text",
            )
            if reply_text is not None:
                normalized_attachment_payload["reply_text"] = reply_text
            return cls(
                request_id=request_id,
                action=action,
                target=target,
                action_payload=normalized_attachment_payload,
            )
        status_payload = payload.get("status")
        if status_payload is None:
            normalized_status_payload: dict[str, Any] = {}
        else:
            normalized_status_payload = _required_mapping(status_payload, "status")
        return cls(
            request_id=request_id,
            action=action,
            target=target,
            action_payload=normalized_status_payload,
        )


def _map_resolver_error(exc: Phase3SubscriptionError) -> AndroidSessionActionFacadeError:
    if exc.code == "session_not_found":
        return AndroidSessionActionFacadeError(
            status_code=404,
            error_code="session_not_found",
            error_message=exc.message,
            retryable=False,
        )
    if exc.code in {"workspace_identity_mismatch", "session_identity_mismatch"}:
        return AndroidSessionActionFacadeError(
            status_code=409,
            error_code="session_identity_mismatch",
            error_message=exc.message,
            retryable=False,
        )
    if exc.code in {"workspace_identity_unresolved", "session_binding_unresolved", "session_identity_unresolved"}:
        return AndroidSessionActionFacadeError(
            status_code=409,
            error_code="session_binding_unresolved",
            error_message=exc.message,
            retryable=False,
        )
    return AndroidSessionActionFacadeError(
        status_code=409,
        error_code="session_binding_unresolved",
        error_message=exc.message,
        retryable=False,
    )


def _resolve_target_state(
    *,
    request: AndroidSessionActionCommand,
    task_root: str | Path,
) -> ThreadState:
    resolved_task_root = Path(str(task_root).strip())
    provider = ThreadStorePhase3SessionDetailProvider(task_root=resolved_task_root)
    try:
        _session_state, thread_state = provider.resolve_session_detail(
            Phase3SubscribeSessionDetailRequest(
                request_id=f"android-session-action-resolve:{request.request_id}",
                workspace_id=request.target.workspace_id,
                repo_path=None,
                workdir=None,
                session_id=request.target.session_id,
                thread_id=request.target.thread_id,
                last_known_sequence=0,
                reason="detail_open",
            )
        )
        return thread_state
    except Phase3SubscriptionError as exc:
        raise _map_resolver_error(exc) from exc


def _resolve_target_session_identity(
    *,
    target_state: ThreadState,
    pc_control_runtime: PcControlRuntime,
) -> dict[str, Any]:
    locator_index = build_pc_locator_index(pc_control_runtime)
    pc_id = resolve_pc_id(target_state, locator_index)
    if pc_id is None:
        raise AndroidSessionActionFacadeError(
            status_code=409,
            error_code="session_binding_unresolved",
            error_message="could not resolve a canonical pc binding for the requested session action",
            retryable=False,
        )
    target_identity = build_target_session_identity(
        workspace_id=target_state.workspace_id,
        session_id=target_state.session_id or target_state.thread_id,
        thread_id=target_state.thread_id,
    ) or {}
    return {
        "pc_id": pc_id,
        **target_identity,
    }


def _build_command_payload(
    *,
    request: AndroidSessionActionCommand,
    target_session_identity: dict[str, Any],
) -> dict[str, Any]:
    target_payload = {
        "scope": "current_session",
        "workspace_id": target_session_identity["workspace_id"],
        "session_id": target_session_identity["session_id"],
    }
    thread_id = str(target_session_identity.get("thread_id") or "").strip()
    if thread_id:
        target_payload["thread_id"] = thread_id
    payload = {
        "target": target_payload,
    }
    if request.action == "reply":
        payload["reply"] = {
            "reply_text": request.action_payload["reply_text"],
        }
    elif request.action == "status":
        payload["status"] = dict(request.action_payload)
    else:
        payload[request.action] = dict(request.action_payload)
    return payload


def _replayed_response(
    record: AndroidSessionActionRequestRecord,
    *,
    request_fingerprint: str,
) -> AndroidSessionActionSubmitResponse:
    if record.request_fingerprint != request_fingerprint:
        raise AndroidSessionActionFacadeError(
            status_code=409,
            error_code="request_id_conflict",
            error_message="request_id already exists with a different canonical payload",
            retryable=False,
            command_id=str(record.response_payload.get("command_id") or "").strip() or None,
            target_session_identity=(
                dict(record.response_payload.get("target_session_identity") or {})
                if isinstance(record.response_payload.get("target_session_identity"), dict)
                else None
            ),
        )
    return AndroidSessionActionSubmitResponse(
        status_code=record.response_status_code,
        payload=dict(record.response_payload),
    )


def _store_response(
    request_store: InMemoryAndroidSessionActionRequestStore,
    *,
    request: AndroidSessionActionCommand,
    request_fingerprint: str,
    response: AndroidSessionActionSubmitResponse,
) -> AndroidSessionActionSubmitResponse:
    if not _should_record_facade_result(response.status_code):
        return response
    stored_record, _created = request_store.upsert_response(
        AndroidSessionActionRequestRecord(
            request_id=request.request_id,
            request_fingerprint=request_fingerprint,
            response_status_code=response.status_code,
            response_payload=response.payload,
            created_at=_timestamp(),
            updated_at=_timestamp(),
        )
    )
    return _replayed_response(stored_record, request_fingerprint=request_fingerprint)


def submit_android_session_action_command(
    payload: dict[str, Any],
    *,
    pc_control_runtime: PcControlRuntime,
    request_store: InMemoryAndroidSessionActionRequestStore,
    task_root: str | Path | None,
    ack_wait_seconds: float = _ACK_WAIT_SECONDS,
    ack_poll_seconds: float = _ACK_POLL_SECONDS,
) -> AndroidSessionActionSubmitResponse:
    request = AndroidSessionActionCommand.from_payload(payload)
    request_fingerprint = _request_fingerprint(request)
    existing = request_store.get_request(request.request_id)
    if existing is not None:
        return _replayed_response(existing, request_fingerprint=request_fingerprint)
    if not str(task_root or "").strip():
        raise AndroidSessionActionFacadeError(
            status_code=503,
            error_code="task_root_unavailable",
            error_message="task_root is not configured",
            retryable=True,
        )

    try:
        target_state = _resolve_target_state(request=request, task_root=task_root)
        target_session_identity = _resolve_target_session_identity(
            target_state=target_state,
            pc_control_runtime=pc_control_runtime,
        )
    except AndroidSessionActionFacadeError as exc:
        response = AndroidSessionActionSubmitResponse(
            status_code=exc.status_code,
            payload=exc.to_response_payload(),
        )
        return _store_response(
            request_store,
            request=request,
            request_fingerprint=request_fingerprint,
            response=response,
        )
    command_id = _command_id_for_request(request.request_id)
    command_payload = _build_command_payload(
        request=request,
        target_session_identity=target_session_identity,
    )
    dispatch_payload = build_command_dispatch(
        message_id=f"android-session-action:{command_id}",
        trace_id=f"trace:android-session-action:{command_id}",
        pc_id=target_session_identity["pc_id"],
        connection_epoch=1,
        sent_at=_timestamp(),
        command_id=command_id,
        command_type=request.action,
        workspace_id=target_session_identity["workspace_id"],
        session_id=target_session_identity["session_id"],
        execution_policy={},
        command_payload=command_payload,
    )
    dispatch_message = parse_pc_control_server_message(dispatch_payload)

    try:
        pc_control_runtime.enqueue_command(dispatch_message)
    except PcCommandDispatchValidationError as exc:
        submit_ack = _build_submit_ack(
            ack_status="rejected",
            reason=exc.message,
            error_code=exc.code,
        )
        response = AndroidSessionActionSubmitResponse(
            status_code=200,
            payload=_build_submit_response(
                command_id=command_id,
                submit_ack=submit_ack,
                target_session_identity=target_session_identity,
            ),
        )
        return _store_response(
            request_store,
            request=request,
            request_fingerprint=request_fingerprint,
            response=response,
        )
    except PcCommandConflictError as exc:
        raise AndroidSessionActionFacadeError(
            status_code=409,
            error_code="request_id_conflict",
            error_message=exc.message,
            retryable=False,
            command_id=command_id,
            target_session_identity=target_session_identity,
        ) from exc

    effective_ack_wait_seconds = max(
        float(ack_wait_seconds),
        float(pc_control_runtime.keepalive_seconds) + _ACK_WAIT_KEEPALIVE_PADDING_SECONDS,
    )
    record = _wait_for_command_ack(
        runtime=pc_control_runtime,
        pc_id=target_session_identity["pc_id"],
        command_id=command_id,
        timeout_seconds=effective_ack_wait_seconds,
        poll_seconds=ack_poll_seconds,
    )
    if record is None or record.ack_status is None:
        raise AndroidSessionActionSubmitTimeout(
            command_id=command_id,
            target_session_identity=target_session_identity,
        )

    submit_ack = _build_submit_ack(
        ack_status=record.ack_status,
        queue_position=record.queue_position,
        reason=record.reason,
        error_code=record.error_code,
    )
    response = AndroidSessionActionSubmitResponse(
        status_code=200,
        payload=_build_submit_response(
            command_id=record.command_id,
            submit_ack=submit_ack,
            target_session_identity=target_session_identity,
        ),
    )
    return _store_response(
        request_store,
        request=request,
        request_fingerprint=request_fingerprint,
        response=response,
    )


__all__ = [
    "ANDROID_SESSION_ACTION_PATH",
    "ANDROID_SESSION_ACTION_SCHEMA_VERSION",
    "AndroidSessionActionContractError",
    "AndroidSessionActionFacadeError",
    "AndroidSessionActionSubmitResponse",
    "AndroidSessionActionSubmitTimeout",
    "submit_android_session_action_command",
]
