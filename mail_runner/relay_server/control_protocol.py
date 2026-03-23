"""Shared `/control` protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolValidationError, RelayErrorMessage

CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA = "taskmail-bootstrap-control-contract-v2"
CONTROL_BOOTSTRAP_COMMAND_TYPE = "sync_project_folders"
CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA = "taskmail-transport-probe-payload-v1"
CONTROL_TRANSPORT_PROBE_COMMAND_TYPE = "transport_probe"
CONTROL_CHANNEL = "taskmail_android_direct"
CONTROL_FALLBACK_POLICY = "mail"
CONTROL_NO_FALLBACK_POLICY = "none"
SUPPORTED_CONTROL_PAYLOAD_SCHEMAS = (
    CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
    CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
)
_CONTROL_RESULT_TYPE_BOOTSTRAP = "sync_project_folders_result"
_CONTROL_RESULT_TYPE_TRANSPORT_PROBE = "transport_probe_result"
_CONTROL_RESULT_STATUSES = {"partial", "completed", "failed"}
_CONTROL_ROUTE_METADATA = {
    (CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA, CONTROL_BOOTSTRAP_COMMAND_TYPE): {
        "fallback_policy": CONTROL_FALLBACK_POLICY,
    },
    (CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA, CONTROL_TRANSPORT_PROBE_COMMAND_TYPE): {
        "fallback_policy": CONTROL_NO_FALLBACK_POLICY,
    },
}


def _require_text(value: Any, field_name: str) -> str:
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


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolValidationError(f"{field_name} must be a bool")
    return value


def _require_positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ProtocolValidationError(f"{field_name} must be a positive integer")
    return value


@dataclass(slots=True)
class ControlHelloMessage:
    message_type: str
    client_id: str
    client_version: str
    transport_token_id: str
    sent_at: str
    supported_payload_schemas: list[str] | None = None

    def __post_init__(self) -> None:
        if self.message_type != "hello":
            raise ProtocolValidationError("hello messages must use message_type='hello'")
        self.client_id = _require_text(self.client_id, "client_id")
        self.client_version = _require_text(self.client_version, "client_version")
        self.transport_token_id = _require_text(self.transport_token_id, "transport_token_id")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.supported_payload_schemas = _require_optional_string_list(
            self.supported_payload_schemas,
            "supported_payload_schemas",
        )


@dataclass(slots=True)
class ControlCommandMessage:
    message_type: str
    request_id: str
    packet_id: str
    command_type: str
    payload_schema: str
    trace: dict[str, Any]
    payload: dict[str, Any]
    sent_at: str
    related: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.message_type != "command":
            raise ProtocolValidationError("command messages must use message_type='command'")
        self.request_id = _require_text(self.request_id, "request_id")
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.command_type = _require_text(self.command_type, "command_type")
        self.payload_schema = _require_text(self.payload_schema, "payload_schema")
        self.trace = _require_mapping(self.trace, "trace")
        self.trace["trace_id"] = _require_text(self.trace.get("trace_id"), "trace.trace_id")
        probe_id = self.trace.get("probe_id")
        if probe_id is not None:
            self.trace["probe_id"] = _require_text(probe_id, "trace.probe_id")
        self.payload = _require_mapping(self.payload, "payload")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.related = _require_optional_mapping(self.related, "related")


@dataclass(slots=True)
class ControlPingMessage:
    message_type: str
    sent_at: str

    def __post_init__(self) -> None:
        if self.message_type != "ping":
            raise ProtocolValidationError("ping messages must use message_type='ping'")
        self.sent_at = _require_text(self.sent_at, "sent_at")


ControlClientMessage = ControlHelloMessage | ControlCommandMessage | ControlPingMessage


@dataclass(slots=True)
class ControlHelloAckMessage:
    message_type: str
    connection_id: str
    server_time: str
    heartbeat_seconds: int
    transport_token_id: str
    accepted_payload_schemas: list[str]

    def __post_init__(self) -> None:
        if self.message_type != "hello_ack":
            raise ProtocolValidationError("hello_ack messages must use message_type='hello_ack'")
        self.connection_id = _require_text(self.connection_id, "connection_id")
        self.server_time = _require_text(self.server_time, "server_time")
        if not isinstance(self.heartbeat_seconds, int) or self.heartbeat_seconds <= 0:
            raise ProtocolValidationError("heartbeat_seconds must be a positive integer")
        self.transport_token_id = _require_text(self.transport_token_id, "transport_token_id")
        self.accepted_payload_schemas = _require_string_list(
            self.accepted_payload_schemas,
            "accepted_payload_schemas",
        )


@dataclass(slots=True)
class ControlCommandAckMessage:
    message_type: str
    request_id: str
    packet_id: str
    command_type: str
    payload_schema: str
    accepted: bool
    receipt_id: str
    received_at: str
    transport_message_id: str | None = None
    related: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.message_type != "command_ack":
            raise ProtocolValidationError("command_ack messages must use message_type='command_ack'")
        self.request_id = _require_text(self.request_id, "request_id")
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.command_type = _require_text(self.command_type, "command_type")
        self.payload_schema = _require_text(self.payload_schema, "payload_schema")
        self.accepted = _require_bool(self.accepted, "accepted")
        self.receipt_id = _require_text(self.receipt_id, "receipt_id")
        self.received_at = _require_text(self.received_at, "received_at")
        if self.transport_message_id is not None:
            self.transport_message_id = _require_text(self.transport_message_id, "transport_message_id")
        self.related = _require_optional_mapping(self.related, "related")
        if self.error_code is not None:
            self.error_code = _require_text(self.error_code, "error_code")
        if self.error_message is not None:
            self.error_message = _require_text(self.error_message, "error_message")


@dataclass(slots=True)
class ControlResultMessage:
    message_type: str
    request_id: str
    packet_id: str
    command_type: str
    payload_schema: str
    result_type: str
    status: str
    receipt_id: str
    result_id: str
    sent_at: str
    payload: dict[str, Any]
    related: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.message_type != "result":
            raise ProtocolValidationError("result messages must use message_type='result'")
        self.request_id = _require_text(self.request_id, "request_id")
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.command_type = _require_text(self.command_type, "command_type")
        self.payload_schema = _require_text(self.payload_schema, "payload_schema")
        self.result_type = _require_text(self.result_type, "result_type")
        self.status = _require_text(self.status, "status")
        if self.status not in _CONTROL_RESULT_STATUSES:
            allowed = ", ".join(sorted(_CONTROL_RESULT_STATUSES))
            raise ProtocolValidationError(f"status must be one of: {allowed}")
        self.receipt_id = _require_text(self.receipt_id, "receipt_id")
        self.result_id = _require_text(self.result_id, "result_id")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.payload = _require_mapping(self.payload, "payload")
        self.related = _require_optional_mapping(self.related, "related")


@dataclass(slots=True)
class ControlEventMessage:
    message_type: str
    request_id: str
    packet_id: str
    command_type: str
    payload_schema: str
    event_type: str
    receipt_id: str
    event_id: str
    sent_at: str
    payload: dict[str, Any]
    related: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.message_type != "event":
            raise ProtocolValidationError("event messages must use message_type='event'")
        self.request_id = _require_text(self.request_id, "request_id")
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.command_type = _require_text(self.command_type, "command_type")
        self.payload_schema = _require_text(self.payload_schema, "payload_schema")
        self.event_type = _require_text(self.event_type, "event_type")
        self.receipt_id = _require_text(self.receipt_id, "receipt_id")
        self.event_id = _require_text(self.event_id, "event_id")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.payload = _require_mapping(self.payload, "payload")
        self.related = _require_optional_mapping(self.related, "related")


@dataclass(slots=True)
class ControlPongMessage:
    message_type: str
    sent_at: str

    def __post_init__(self) -> None:
        if self.message_type != "pong":
            raise ProtocolValidationError("pong messages must use message_type='pong'")
        self.sent_at = _require_text(self.sent_at, "sent_at")


ControlServerMessage = (
    ControlHelloAckMessage
    | ControlCommandAckMessage
    | ControlEventMessage
    | ControlResultMessage
    | ControlPongMessage
    | RelayErrorMessage
)


class ControlBridgeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


def parse_control_client_message(payload: dict[str, Any]) -> ControlClientMessage:
    data = _require_mapping(payload, "payload")
    message_type = _require_text(data.get("message_type"), "message_type")
    if message_type == "hello":
        return ControlHelloMessage(**data)
    if message_type == "command":
        return ControlCommandMessage(**data)
    if message_type == "ping":
        return ControlPingMessage(**data)
    raise ProtocolValidationError(f"unsupported message_type: {message_type}")


def parse_control_server_message(payload: dict[str, Any]) -> ControlServerMessage:
    data = _require_mapping(payload, "payload")
    message_type = _require_text(data.get("message_type"), "message_type")
    if message_type == "hello_ack":
        return ControlHelloAckMessage(**data)
    if message_type == "command_ack":
        return ControlCommandAckMessage(**data)
    if message_type == "event":
        return ControlEventMessage(**data)
    if message_type == "result":
        return ControlResultMessage(**data)
    if message_type == "pong":
        return ControlPongMessage(**data)
    if message_type == "error":
        return RelayErrorMessage(**data)
    raise ProtocolValidationError(f"unsupported message_type: {message_type}")


def negotiate_control_payload_schemas(
    requested: list[str] | None,
    *,
    supported_payload_schemas: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    available = SUPPORTED_CONTROL_PAYLOAD_SCHEMAS if supported_payload_schemas is None else supported_payload_schemas
    if not requested:
        return list(available)
    accepted: list[str] = []
    supported = set(available)
    for schema in requested:
        if schema in supported and schema not in accepted:
            accepted.append(schema)
    return accepted


def build_control_hello_ack(
    *,
    connection_id: str,
    server_time: str,
    heartbeat_seconds: int,
    transport_token_id: str,
    accepted_payload_schemas: list[str],
) -> dict[str, Any]:
    payload = {
        "message_type": "hello_ack",
        "connection_id": _require_text(connection_id, "connection_id"),
        "server_time": _require_text(server_time, "server_time"),
        "heartbeat_seconds": heartbeat_seconds,
        "transport_token_id": _require_text(transport_token_id, "transport_token_id"),
        "accepted_payload_schemas": _require_string_list(
            accepted_payload_schemas,
            "accepted_payload_schemas",
        ),
    }
    ControlHelloAckMessage(**payload)
    return payload


def build_control_command_ack(
    *,
    request_id: str,
    packet_id: str,
    command_type: str,
    payload_schema: str,
    accepted: bool,
    receipt_id: str,
    received_at: str,
    transport_message_id: str | None = None,
    related: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "message_type": "command_ack",
        "request_id": _require_text(request_id, "request_id"),
        "packet_id": _require_text(packet_id, "packet_id"),
        "command_type": _require_text(command_type, "command_type"),
        "payload_schema": _require_text(payload_schema, "payload_schema"),
        "accepted": _require_bool(accepted, "accepted"),
        "receipt_id": _require_text(receipt_id, "receipt_id"),
        "received_at": _require_text(received_at, "received_at"),
    }
    if transport_message_id is not None:
        payload["transport_message_id"] = _require_text(transport_message_id, "transport_message_id")
    if related is not None:
        payload["related"] = _require_mapping(related, "related")
    if error_code is not None:
        payload["error_code"] = _require_text(error_code, "error_code")
    if error_message is not None:
        payload["error_message"] = _require_text(error_message, "error_message")
    ControlCommandAckMessage(**payload)
    return payload


def build_control_result(
    *,
    request_id: str,
    packet_id: str,
    command_type: str,
    payload_schema: str,
    result_type: str,
    status: str,
    receipt_id: str,
    result_id: str,
    sent_at: str,
    payload: dict[str, Any],
    related: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rendered = {
        "message_type": "result",
        "request_id": _require_text(request_id, "request_id"),
        "packet_id": _require_text(packet_id, "packet_id"),
        "command_type": _require_text(command_type, "command_type"),
        "payload_schema": _require_text(payload_schema, "payload_schema"),
        "result_type": _require_text(result_type, "result_type"),
        "status": _require_text(status, "status"),
        "receipt_id": _require_text(receipt_id, "receipt_id"),
        "result_id": _require_text(result_id, "result_id"),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": _require_mapping(payload, "payload"),
    }
    if related is not None:
        rendered["related"] = _require_mapping(related, "related")
    ControlResultMessage(**rendered)
    return rendered


def build_control_event(
    *,
    request_id: str,
    packet_id: str,
    command_type: str,
    payload_schema: str,
    event_type: str,
    receipt_id: str,
    event_id: str,
    sent_at: str,
    payload: dict[str, Any],
    related: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rendered = {
        "message_type": "event",
        "request_id": _require_text(request_id, "request_id"),
        "packet_id": _require_text(packet_id, "packet_id"),
        "command_type": _require_text(command_type, "command_type"),
        "payload_schema": _require_text(payload_schema, "payload_schema"),
        "event_type": _require_text(event_type, "event_type"),
        "receipt_id": _require_text(receipt_id, "receipt_id"),
        "event_id": _require_text(event_id, "event_id"),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": _require_mapping(payload, "payload"),
    }
    if related is not None:
        rendered["related"] = _require_mapping(related, "related")
    ControlEventMessage(**rendered)
    return rendered


def build_control_pong(*, sent_at: str) -> dict[str, Any]:
    payload = {
        "message_type": "pong",
        "sent_at": _require_text(sent_at, "sent_at"),
    }
    ControlPongMessage(**payload)
    return payload


def build_relay_packet_from_control_command(message: ControlCommandMessage) -> dict[str, Any]:
    route_metadata = _CONTROL_ROUTE_METADATA.get((message.payload_schema, message.command_type))
    if route_metadata is None:
        raise ControlBridgeError(
            "unsupported_action",
            (
                "payload_schema/command_type is not supported on /control: "
                f"{message.payload_schema} / {message.command_type}"
            ),
        )
    if message.payload_schema == CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA:
        _validate_control_transport_probe_payload(message)
    overlapping_keys = sorted(
        key for key in ("schema_version", "action", "request_id") if key in message.payload
    )
    if overlapping_keys:
        raise ControlBridgeError(
            "invalid_payload",
            f"payload must not redefine reserved keys: {', '.join(overlapping_keys)}",
        )
    task_run_packet = {
        "schema_version": message.payload_schema,
        "action": message.command_type,
        "request_id": message.request_id,
        **message.payload,
    }
    dispatch_metadata = {
        "channel": CONTROL_CHANNEL,
        "schema_version": message.payload_schema,
        "action": message.command_type,
        "fallback_policy": str(route_metadata["fallback_policy"]),
        "control_trace": dict(message.trace),
    }
    if message.related is not None:
        dispatch_metadata["control_related"] = dict(message.related)
    return {
        "message_type": "packet",
        "packet_id": message.packet_id,
        "client_trace_id": message.request_id,
        "task_run_packet": task_run_packet,
        "dispatch_metadata": dispatch_metadata,
        "sent_at": message.sent_at,
    }


def build_control_related(
    message: ControlCommandMessage,
    *,
    receipt_id: str | None = None,
    result_id: str | None = None,
) -> dict[str, Any]:
    related = dict(message.related or {})
    related["trace_id"] = message.trace["trace_id"]
    if "probe_id" in message.trace:
        related["probe_id"] = message.trace["probe_id"]
    related["request_id"] = message.request_id
    related["packet_id"] = message.packet_id
    if receipt_id:
        related["receipt_id"] = _require_text(receipt_id, "receipt_id")
    if result_id:
        related["result_id"] = _require_text(result_id, "result_id")
    return related


def translate_relay_response_to_control(
    response: dict[str, Any],
    *,
    message: ControlCommandMessage,
) -> dict[str, Any]:
    response_type = str(response.get("message_type") or "").strip()
    if response_type == "packet_ack":
        receipt_id = _require_text(response.get("receipt_id"), "receipt_id")
        return build_control_command_ack(
            request_id=message.request_id,
            packet_id=message.packet_id,
            command_type=message.command_type,
            payload_schema=message.payload_schema,
            accepted=_require_bool(response.get("accepted"), "accepted"),
            receipt_id=receipt_id,
            received_at=_require_text(response.get("received_at"), "received_at"),
            transport_message_id=response.get("transport_message_id"),
            related=build_control_related(message, receipt_id=receipt_id),
            error_code=response.get("error_code"),
            error_message=response.get("error_message"),
        )
    if response_type == "bootstrap_result":
        receipt_id = _require_text(response.get("receipt_id"), "receipt_id")
        result_id = _require_text(response.get("result_id"), "result_id")
        return build_control_result(
            request_id=message.request_id,
            packet_id=message.packet_id,
            command_type=message.command_type,
            payload_schema=message.payload_schema,
            result_type=_CONTROL_RESULT_TYPE_BOOTSTRAP,
            status="completed",
            receipt_id=receipt_id,
            result_id=result_id,
            sent_at=_require_text(response.get("sent_at"), "sent_at"),
            payload={
                "sync_project_folders_result": _require_mapping(
                    response.get("sync_project_folders_result"),
                    "sync_project_folders_result",
                )
            },
            related=build_control_related(message, receipt_id=receipt_id, result_id=result_id),
        )
    if response_type in {"event", "result"}:
        parse_control_server_message(dict(response))
        return dict(response)
    if response_type == "error":
        return dict(response)
    raise ControlBridgeError(
        "server_error",
        f"relay response cannot be translated for /control: {response_type or '<missing>'}",
    )


def _validate_control_transport_probe_payload(message: ControlCommandMessage) -> None:
    trace_probe_id = _require_text(message.trace.get("probe_id"), "trace.probe_id")
    payload_probe_id = _require_text(message.payload.get("probe_id"), "payload.probe_id")
    if trace_probe_id != payload_probe_id:
        raise ControlBridgeError("invalid_payload", "trace.probe_id must equal payload.probe_id")
    _require_text(message.payload.get("scenario"), "payload.scenario")
    _require_text(message.payload.get("direction"), "payload.direction")
    _require_text(message.payload.get("transport_kind"), "payload.transport_kind")
    payload_text = _require_text(message.payload.get("payload_text"), "payload.payload_text")
    if "\n" in payload_text or "\r" in payload_text:
        raise ControlBridgeError("invalid_payload", "payload.payload_text must be single-line text")
    _require_positive_int(message.payload.get("timeout_seconds"), "payload.timeout_seconds")
