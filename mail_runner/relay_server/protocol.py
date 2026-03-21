"""Minimal relay protocol parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_PHASE3_SCHEMA_VERSION = "phase3-direct-inbound-wire-v1"
_PHASE3_STATUSES = {"queued", "running", "awaiting_user_input", "paused", "done", "failed", "killed"}
_PHASE3_LIFECYCLE_STATES = {"active", "ended"}
_PHASE3_PAUSED_FROM_STATUSES = {"queued", "awaiting_user_input", "done", "failed", "killed"}
_PHASE3_QUESTION_TYPES = {"single_choice", "boolean", "short_text"}
_PHASE3_TIMELINE_ITEM_TYPES = {
    "status_transition",
    "assistant_reply_preview",
    "question_prompt",
    "paused_hint",
    "terminal_summary",
}
_PHASE3_DELTA_TYPES = {"state_transition", "timeline_append"}


class ProtocolValidationError(ValueError):
    """Raised when a relay message payload does not match the MVP draft."""


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolValidationError(f"{field_name} must be a dict")
    return dict(value)


def _require_optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _require_mapping(value, field_name)


def _require_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ProtocolValidationError(f"{field_name} must be a list[str]")
    normalized: list[str] = []
    for index, item in enumerate(value):
        normalized.append(_require_text(item, f"{field_name}[{index}]"))
    return normalized


def _require_optional_string_list(value: Any, field_name: str) -> list[str] | None:
    if value is None:
        return None
    return _require_string_list(value, field_name)


def _require_string_dict(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProtocolValidationError(f"{field_name} must be a dict[str, str]")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized[_require_text(key, f"{field_name}.key")] = _require_text(item, f"{field_name}[{key}]")
    return normalized


def _require_literal(value: Any, field_name: str, allowed: set[str]) -> str:
    text = _require_text(value, field_name)
    if text not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ProtocolValidationError(f"{field_name} must be one of: {allowed_text}")
    return text


def _require_optional_literal(value: Any, field_name: str, allowed: set[str]) -> str | None:
    if value is None:
        return None
    return _require_literal(value, field_name, allowed)


@dataclass(slots=True)
class RelayHelloMessage:
    message_type: str
    client_id: str
    client_version: str
    transport_token_id: str
    sent_at: str

    def __post_init__(self) -> None:
        if self.message_type != "hello":
            raise ProtocolValidationError("hello messages must use message_type='hello'")
        self.client_id = _require_text(self.client_id, "client_id")
        self.client_version = _require_text(self.client_version, "client_version")
        self.transport_token_id = _require_text(self.transport_token_id, "transport_token_id")
        self.sent_at = _require_text(self.sent_at, "sent_at")


@dataclass(slots=True)
class RelayPacketMessage:
    message_type: str
    packet_id: str
    client_trace_id: str
    task_run_packet: dict[str, Any]
    dispatch_metadata: dict[str, Any]
    sent_at: str

    def __post_init__(self) -> None:
        if self.message_type != "packet":
            raise ProtocolValidationError("packet messages must use message_type='packet'")
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.client_trace_id = _require_text(self.client_trace_id, "client_trace_id")
        self.task_run_packet = _require_mapping(self.task_run_packet, "task_run_packet")
        self.dispatch_metadata = _require_mapping(self.dispatch_metadata, "dispatch_metadata")
        self.sent_at = _require_text(self.sent_at, "sent_at")


@dataclass(slots=True)
class RelayPingMessage:
    message_type: str
    sent_at: str

    def __post_init__(self) -> None:
        if self.message_type != "ping":
            raise ProtocolValidationError("ping messages must use message_type='ping'")
        self.sent_at = _require_text(self.sent_at, "sent_at")


RelayClientMessage = RelayHelloMessage | RelayPacketMessage | RelayPingMessage


@dataclass(slots=True)
class RelayHelloAckMessage:
    message_type: str
    connection_id: str
    server_time: str
    heartbeat_seconds: int

    def __post_init__(self) -> None:
        if self.message_type != "hello_ack":
            raise ProtocolValidationError("hello_ack messages must use message_type='hello_ack'")
        self.connection_id = _require_text(self.connection_id, "connection_id")
        self.server_time = _require_text(self.server_time, "server_time")
        if not isinstance(self.heartbeat_seconds, int) or self.heartbeat_seconds <= 0:
            raise ProtocolValidationError("heartbeat_seconds must be a positive integer")


@dataclass(slots=True)
class RelayPacketAckMessage:
    message_type: str
    packet_id: str
    accepted: bool
    receipt_id: str
    received_at: str
    transport_message_id: str | None = None
    error_message: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.message_type != "packet_ack":
            raise ProtocolValidationError("packet_ack messages must use message_type='packet_ack'")
        self.packet_id = _require_text(self.packet_id, "packet_id")
        if not isinstance(self.accepted, bool):
            raise ProtocolValidationError("accepted must be a bool")
        self.receipt_id = _require_text(self.receipt_id, "receipt_id")
        self.received_at = _require_text(self.received_at, "received_at")
        if self.transport_message_id is not None:
            self.transport_message_id = _require_text(self.transport_message_id, "transport_message_id")
        if self.error_message is not None:
            self.error_message = _require_text(self.error_message, "error_message")
        if self.error_code is not None:
            self.error_code = _require_text(self.error_code, "error_code")


@dataclass(slots=True)
class RelayErrorMessage:
    message_type: str
    code: str
    message: str
    sent_at: str

    def __post_init__(self) -> None:
        if self.message_type != "error":
            raise ProtocolValidationError("error messages must use message_type='error'")
        self.code = _require_text(self.code, "code")
        self.message = _require_text(self.message, "message")
        self.sent_at = _require_text(self.sent_at, "sent_at")


def _validate_question_state(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    data = _require_mapping(value, field_name)
    data["question_set_id"] = _require_text(data.get("question_set_id"), f"{field_name}.question_set_id")
    question_count = data.get("question_count")
    if not isinstance(question_count, int) or question_count <= 0:
        raise ProtocolValidationError(f"{field_name}.question_count must be a positive integer")
    questions_raw = data.get("questions")
    if not isinstance(questions_raw, list) or not questions_raw:
        raise ProtocolValidationError(f"{field_name}.questions must be a non-empty list")
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(questions_raw):
        question = _require_mapping(item, f"{field_name}.questions[{index}]")
        question["question_id"] = _require_text(question.get("question_id"), f"{field_name}.questions[{index}].question_id")
        question["question_text"] = _require_text(question.get("question_text"), f"{field_name}.questions[{index}].question_text")
        question["question_type"] = _require_literal(
            question.get("question_type"),
            f"{field_name}.questions[{index}].question_type",
            _PHASE3_QUESTION_TYPES,
        )
        if not isinstance(question.get("required"), bool):
            raise ProtocolValidationError(f"{field_name}.questions[{index}].required must be a bool")
        question["choices"] = _require_string_list(question.get("choices"), f"{field_name}.questions[{index}].choices")
        question["choice_labels"] = _require_string_dict(
            question.get("choice_labels"),
            f"{field_name}.questions[{index}].choice_labels",
        )
        if not set(question["choice_labels"]).issubset(set(question["choices"])):
            raise ProtocolValidationError(
                f"{field_name}.questions[{index}].choice_labels keys must be a subset of choices"
            )
        questions.append(question)
    if question_count != len(questions):
        raise ProtocolValidationError(f"{field_name}.question_count must match questions length")
    data["questions"] = questions
    return data


def _validate_timeline_items(value: Any, field_name: str, *, require_non_empty: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ProtocolValidationError(f"{field_name} must be a list[dict]")
    if require_non_empty and not value:
        raise ProtocolValidationError(f"{field_name} must be a non-empty list")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        timeline_item = _require_mapping(item, f"{field_name}[{index}]")
        timeline_item["item_id"] = _require_text(timeline_item.get("item_id"), f"{field_name}[{index}].item_id")
        timeline_item["business_event_key"] = _require_text(
            timeline_item.get("business_event_key"),
            f"{field_name}[{index}].business_event_key",
        )
        timeline_item["item_type"] = _require_literal(
            timeline_item.get("item_type"),
            f"{field_name}[{index}].item_type",
            _PHASE3_TIMELINE_ITEM_TYPES,
        )
        timeline_item["created_at"] = _require_text(timeline_item.get("created_at"), f"{field_name}[{index}].created_at")
        timeline_item["status"] = _require_optional_literal(
            timeline_item.get("status"),
            f"{field_name}[{index}].status",
            _PHASE3_STATUSES,
        )
        timeline_item["text"] = _require_optional_text(timeline_item.get("text"), f"{field_name}[{index}].text")
        timeline_item["question_set_id"] = _require_optional_text(
            timeline_item.get("question_set_id"),
            f"{field_name}[{index}].question_set_id",
        )
        timeline_item["question_ids"] = _require_string_list(
            timeline_item.get("question_ids"),
            f"{field_name}[{index}].question_ids",
        )
        timeline_item["paused_from_status"] = _require_optional_literal(
            timeline_item.get("paused_from_status"),
            f"{field_name}[{index}].paused_from_status",
            _PHASE3_PAUSED_FROM_STATUSES,
        )
        item_type = timeline_item["item_type"]
        if item_type == "status_transition" and timeline_item["status"] is None:
            raise ProtocolValidationError(f"{field_name}[{index}].status_transition requires status")
        if item_type == "assistant_reply_preview" and timeline_item["text"] is None:
            raise ProtocolValidationError(f"{field_name}[{index}].assistant_reply_preview requires text")
        if item_type == "question_prompt":
            if timeline_item["question_set_id"] is None or not timeline_item["question_ids"] or timeline_item["text"] is None:
                raise ProtocolValidationError(
                    f"{field_name}[{index}].question_prompt requires question_set_id, question_ids, and text"
                )
        if item_type == "paused_hint":
            if timeline_item["paused_from_status"] is None or timeline_item["text"] is None:
                raise ProtocolValidationError(
                    f"{field_name}[{index}].paused_hint requires paused_from_status and text"
                )
        if item_type == "terminal_summary" and (timeline_item["status"] is None or timeline_item["text"] is None):
            raise ProtocolValidationError(f"{field_name}[{index}].terminal_summary requires status and text")
        items.append(timeline_item)
    return items


def _validate_session_snapshot(value: Any, field_name: str) -> dict[str, Any]:
    data = _require_mapping(value, field_name)
    data["session_name"] = _require_text(data.get("session_name"), f"{field_name}.session_name")
    data["backend"] = _require_text(data.get("backend"), f"{field_name}.backend")
    data["repo_path"] = _require_text(data.get("repo_path"), f"{field_name}.repo_path")
    data["workdir"] = _require_optional_text(data.get("workdir"), f"{field_name}.workdir")
    data["status"] = _require_literal(data.get("status"), f"{field_name}.status", _PHASE3_STATUSES)
    data["lifecycle"] = _require_literal(data.get("lifecycle"), f"{field_name}.lifecycle", _PHASE3_LIFECYCLE_STATES)
    data["last_summary"] = _require_text(data.get("last_summary"), f"{field_name}.last_summary")
    data["last_active_at"] = _require_text(data.get("last_active_at"), f"{field_name}.last_active_at")
    data["last_progress_at"] = _require_text(data.get("last_progress_at"), f"{field_name}.last_progress_at")
    data["paused_from_status"] = _require_optional_literal(
        data.get("paused_from_status"),
        f"{field_name}.paused_from_status",
        _PHASE3_PAUSED_FROM_STATUSES,
    )
    data["question_state"] = _validate_question_state(data.get("question_state"), f"{field_name}.question_state")
    data["timeline_items"] = _validate_timeline_items(
        data.get("timeline_items"),
        f"{field_name}.timeline_items",
        require_non_empty=False,
    )
    return data


def _validate_state_transition(value: Any, field_name: str) -> dict[str, Any]:
    data = _require_mapping(value, field_name)
    data["status"] = _require_literal(data.get("status"), f"{field_name}.status", _PHASE3_STATUSES)
    data["lifecycle"] = _require_optional_literal(
        data.get("lifecycle"),
        f"{field_name}.lifecycle",
        _PHASE3_LIFECYCLE_STATES,
    )
    data["last_summary"] = _require_optional_text(data.get("last_summary"), f"{field_name}.last_summary")
    data["last_active_at"] = _require_optional_text(data.get("last_active_at"), f"{field_name}.last_active_at")
    data["last_progress_at"] = _require_optional_text(data.get("last_progress_at"), f"{field_name}.last_progress_at")
    data["paused_from_status"] = _require_optional_literal(
        data.get("paused_from_status"),
        f"{field_name}.paused_from_status",
        _PHASE3_PAUSED_FROM_STATUSES,
    )
    data["question_state"] = _validate_question_state(data.get("question_state"), f"{field_name}.question_state")
    return data


def _validate_session_delta(value: Any, field_name: str) -> dict[str, Any]:
    data = _require_mapping(value, field_name)
    data["delta_type"] = _require_literal(data.get("delta_type"), f"{field_name}.delta_type", _PHASE3_DELTA_TYPES)
    if data["delta_type"] == "state_transition":
        data["state_transition"] = _validate_state_transition(
            data.get("state_transition"),
            f"{field_name}.state_transition",
        )
        if data.get("timeline_items") is not None:
            raise ProtocolValidationError(f"{field_name}.timeline_items is not allowed for state_transition")
    else:
        data["timeline_items"] = _validate_timeline_items(
            data.get("timeline_items"),
            f"{field_name}.timeline_items",
            require_non_empty=True,
        )
        if data.get("state_transition") is not None:
            raise ProtocolValidationError(f"{field_name}.state_transition is not allowed for timeline_append")
    return data


@dataclass(slots=True)
class RelaySessionUpdateMessage:
    message_type: str
    schema_version: str
    subscription_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    task_id: str
    update_id: str
    sequence: int
    sent_at: str
    update_type: str
    session_snapshot: dict[str, Any] | None = None
    session_delta: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.message_type != "session_update":
            raise ProtocolValidationError("session_update messages must use message_type='session_update'")
        self.schema_version = _require_text(self.schema_version, "schema_version")
        if self.schema_version != _PHASE3_SCHEMA_VERSION:
            raise ProtocolValidationError(f"schema_version must be {_PHASE3_SCHEMA_VERSION}")
        self.subscription_id = _require_text(self.subscription_id, "subscription_id")
        self.workspace_id = _require_text(self.workspace_id, "workspace_id")
        self.session_id = _require_text(self.session_id, "session_id")
        self.thread_id = _require_text(self.thread_id, "thread_id")
        self.task_id = _require_text(self.task_id, "task_id")
        self.update_id = _require_text(self.update_id, "update_id")
        if not isinstance(self.sequence, int) or self.sequence <= 0:
            raise ProtocolValidationError("sequence must be a positive integer")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.update_type = _require_literal(self.update_type, "update_type", {"session_snapshot", "session_delta"})
        if self.update_type == "session_snapshot":
            self.session_snapshot = _validate_session_snapshot(self.session_snapshot, "session_snapshot")
            if self.session_delta is not None:
                raise ProtocolValidationError("session_delta must be omitted for session_snapshot messages")
        else:
            self.session_delta = _validate_session_delta(self.session_delta, "session_delta")
            if self.session_snapshot is not None:
                raise ProtocolValidationError("session_snapshot must be omitted for session_delta messages")


RelayServerMessage = RelayHelloAckMessage | RelayPacketAckMessage | RelayErrorMessage | RelaySessionUpdateMessage


def parse_client_message(payload: dict[str, Any]) -> RelayClientMessage:
    data = _require_mapping(payload, "payload")
    message_type = _require_text(str(data.get("message_type") or ""), "message_type")
    if message_type == "hello":
        return RelayHelloMessage(**data)
    if message_type == "packet":
        return RelayPacketMessage(**data)
    if message_type == "ping":
        return RelayPingMessage(**data)
    raise ProtocolValidationError(f"unsupported message_type: {message_type}")


def parse_server_message(payload: dict[str, Any]) -> RelayServerMessage:
    data = _require_mapping(payload, "payload")
    message_type = _require_text(str(data.get("message_type") or ""), "message_type")
    if message_type == "hello_ack":
        return RelayHelloAckMessage(**data)
    if message_type == "packet_ack":
        return RelayPacketAckMessage(**data)
    if message_type == "session_update":
        return RelaySessionUpdateMessage(**data)
    if message_type == "error":
        return RelayErrorMessage(**data)
    raise ProtocolValidationError(f"unsupported message_type: {message_type}")


def build_hello_ack(*, connection_id: str, server_time: str, heartbeat_seconds: int) -> dict[str, Any]:
    if not isinstance(heartbeat_seconds, int) or heartbeat_seconds <= 0:
        raise ProtocolValidationError("heartbeat_seconds must be a positive integer")
    return {
        "message_type": "hello_ack",
        "connection_id": _require_text(connection_id, "connection_id"),
        "server_time": _require_text(server_time, "server_time"),
        "heartbeat_seconds": heartbeat_seconds,
    }


def build_packet_ack(
    *,
    packet_id: str,
    accepted: bool,
    receipt_id: str,
    received_at: str,
    transport_message_id: str | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    if not isinstance(accepted, bool):
        raise ProtocolValidationError("accepted must be a bool")
    payload = {
        "message_type": "packet_ack",
        "packet_id": _require_text(packet_id, "packet_id"),
        "accepted": accepted,
        "receipt_id": _require_text(receipt_id, "receipt_id"),
        "received_at": _require_text(received_at, "received_at"),
    }
    if transport_message_id is not None:
        payload["transport_message_id"] = _require_text(transport_message_id, "transport_message_id")
    if error_message is not None:
        payload["error_message"] = _require_text(error_message, "error_message")
    if error_code is not None:
        payload["error_code"] = _require_text(error_code, "error_code")
    return payload


def build_error_message(*, code: str, message: str, sent_at: str) -> dict[str, Any]:
    return {
        "message_type": "error",
        "code": _require_text(code, "code"),
        "message": _require_text(message, "message"),
        "sent_at": _require_text(sent_at, "sent_at"),
    }


def build_session_update(
    *,
    schema_version: str,
    subscription_id: str,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    task_id: str,
    update_id: str,
    sequence: int,
    sent_at: str,
    update_type: str,
    session_snapshot: dict[str, Any] | None = None,
    session_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "message_type": "session_update",
        "schema_version": _require_text(schema_version, "schema_version"),
        "subscription_id": _require_text(subscription_id, "subscription_id"),
        "workspace_id": _require_text(workspace_id, "workspace_id"),
        "session_id": _require_text(session_id, "session_id"),
        "thread_id": _require_text(thread_id, "thread_id"),
        "task_id": _require_text(task_id, "task_id"),
        "update_id": _require_text(update_id, "update_id"),
        "sequence": sequence,
        "sent_at": _require_text(sent_at, "sent_at"),
        "update_type": _require_text(update_type, "update_type"),
    }
    if session_snapshot is not None:
        payload["session_snapshot"] = _require_mapping(session_snapshot, "session_snapshot")
    if session_delta is not None:
        payload["session_delta"] = _require_mapping(session_delta, "session_delta")
    RelaySessionUpdateMessage(**payload)
    return payload
