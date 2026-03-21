"""Minimal relay protocol parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


RelayServerMessage = RelayHelloAckMessage | RelayPacketAckMessage | RelayErrorMessage


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
    return payload


def build_error_message(*, code: str, message: str, sent_at: str) -> dict[str, Any]:
    return {
        "message_type": "error",
        "code": _require_text(code, "code"),
        "message": _require_text(message, "message"),
        "sent_at": _require_text(sent_at, "sent_at"),
    }
