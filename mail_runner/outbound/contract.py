"""Outbound packet and dispatch request contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import OutgoingAttachment


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_optional_text(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _require_text(value, field_name)


def _normalize_string_list(values: list[str], field_name: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list[str]")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        _require_text(item, field_name)
        text = item.strip()
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    if not isinstance(headers, dict):
        raise ValueError("headers must be a dict[str, str]")
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        _require_text(str(key), "headers key")
        if not isinstance(value, str):
            raise ValueError("headers values must be strings")
        normalized[str(key).strip()] = value
    return normalized


def _normalize_attachments(values: list[OutgoingAttachment] | list[dict]) -> list[OutgoingAttachment]:
    if not isinstance(values, list):
        raise ValueError("attachments must be a list[OutgoingAttachment]")
    normalized: list[OutgoingAttachment] = []
    for item in values:
        if isinstance(item, OutgoingAttachment):
            normalized.append(item)
            continue
        if isinstance(item, dict):
            normalized.append(OutgoingAttachment(**item))
            continue
        raise ValueError("attachments must contain OutgoingAttachment-compatible items")
    return normalized


@dataclass(slots=True)
class TaskRunPacket:
    packet_id: str
    task_id: str
    created_at: str
    message_kind: str
    content_format: str
    html: str
    text_fallback: str
    attachments: list[OutgoingAttachment] = field(default_factory=list)
    parent_packet_id: str | None = None
    state_patch: dict[str, str] = field(default_factory=dict)
    client_trace_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.packet_id, "packet_id")
        _require_text(self.task_id, "task_id")
        _require_text(self.created_at, "created_at")
        _require_text(self.message_kind, "message_kind")
        _require_text(self.content_format, "content_format")
        if not isinstance(self.html, str):
            raise ValueError("html must be a string")
        if not isinstance(self.text_fallback, str):
            raise ValueError("text_fallback must be a string")
        self.attachments = _normalize_attachments(self.attachments)
        _require_optional_text(self.parent_packet_id, "parent_packet_id")
        if not isinstance(self.state_patch, dict):
            raise ValueError("state_patch must be a dict[str, str]")
        normalized_state_patch: dict[str, str] = {}
        for key, value in self.state_patch.items():
            _require_text(str(key), "state_patch key")
            if not isinstance(value, str):
                raise ValueError("state_patch values must be strings")
            normalized_state_patch[str(key).strip()] = value
        self.state_patch = normalized_state_patch
        _require_optional_text(self.client_trace_id, "client_trace_id")


@dataclass(slots=True)
class OutboundDispatchRequest:
    packet: TaskRunPacket
    to_addr: str
    subject: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.packet, TaskRunPacket):
            raise ValueError("packet must be a TaskRunPacket")
        _require_text(self.to_addr, "to_addr")
        _require_text(self.subject, "subject")
        _require_optional_text(self.in_reply_to, "in_reply_to")
        self.references = _normalize_string_list(self.references, "references")
        self.headers = _normalize_headers(self.headers)


@dataclass(slots=True)
class TransportReceipt:
    success: bool
    transport_name: str
    sent_at: str
    transport_message_id: str | None = None
    error_message: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ValueError("success must be a bool")
        _require_text(self.transport_name, "transport_name")
        _require_text(self.sent_at, "sent_at")
        _require_optional_text(self.transport_message_id, "transport_message_id")
        _require_optional_text(self.error_message, "error_message")
        _require_optional_text(self.error_code, "error_code")


@dataclass(slots=True)
class DeliveryAttempt:
    packet_id: str
    thread_id: str
    task_id: str
    transport_name: str
    sent_at: str
    success: bool
    to_addr: str
    subject: str
    transport_message_id: str | None = None
    error_message: str | None = None
    client_trace_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.packet_id, "packet_id")
        _require_text(self.thread_id, "thread_id")
        _require_text(self.task_id, "task_id")
        _require_text(self.transport_name, "transport_name")
        _require_text(self.sent_at, "sent_at")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a bool")
        _require_text(self.to_addr, "to_addr")
        _require_text(self.subject, "subject")
        _require_optional_text(self.transport_message_id, "transport_message_id")
        _require_optional_text(self.error_message, "error_message")
        _require_optional_text(self.client_trace_id, "client_trace_id")
