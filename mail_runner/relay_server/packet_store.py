"""Accepted packet stores for relay delivery."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict")
    return dict(value)


def _require_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_mapping_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list[dict]")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        normalized.append(_require_mapping(item, f"{field_name}[{index}]"))
    return normalized


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(slots=True)
class AcceptedRelayPacket:
    packet_id: str
    receipt_id: str
    connection_id: str
    client_id: str
    client_trace_id: str
    received_at: str
    task_run_packet: dict[str, Any]
    dispatch_metadata: dict[str, Any]
    delivery_status: str = "pending"
    transport_message_id: str | None = None
    delivered_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    attempt_count: int = 0
    server_messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.receipt_id = _require_text(self.receipt_id, "receipt_id")
        self.connection_id = _require_text(self.connection_id, "connection_id")
        self.client_id = _require_text(self.client_id, "client_id")
        self.client_trace_id = _require_text(self.client_trace_id, "client_trace_id")
        self.received_at = _require_text(self.received_at, "received_at")
        self.task_run_packet = _require_mapping(self.task_run_packet, "task_run_packet")
        self.dispatch_metadata = _require_mapping(self.dispatch_metadata, "dispatch_metadata")
        self.delivery_status = _require_text(self.delivery_status, "delivery_status")
        if self.delivery_status not in {"pending", "delivered", "failed"}:
            raise ValueError("delivery_status must be one of: pending, delivered, failed")
        self.transport_message_id = _require_optional_text(self.transport_message_id, "transport_message_id")
        self.delivered_at = _require_optional_text(self.delivered_at, "delivered_at")
        self.last_error_code = _require_optional_text(self.last_error_code, "last_error_code")
        self.last_error_message = _require_optional_text(self.last_error_message, "last_error_message")
        if not isinstance(self.attempt_count, int) or self.attempt_count < 0:
            raise ValueError("attempt_count must be a non-negative integer")
        self.server_messages = _require_mapping_list(self.server_messages, "server_messages")


@dataclass(slots=True)
class RelayDeliveryAttempt:
    packet_id: str
    receipt_id: str
    transport_name: str
    attempted_at: str
    success: bool
    transport_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        self.packet_id = _require_text(self.packet_id, "packet_id")
        self.receipt_id = _require_text(self.receipt_id, "receipt_id")
        self.transport_name = _require_text(self.transport_name, "transport_name")
        self.attempted_at = _require_text(self.attempted_at, "attempted_at")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a bool")
        self.transport_message_id = _require_optional_text(self.transport_message_id, "transport_message_id")
        self.error_code = _require_optional_text(self.error_code, "error_code")
        self.error_message = _require_optional_text(self.error_message, "error_message")


class InMemoryAcceptedPacketStore:
    def __init__(self) -> None:
        self._packets: dict[str, AcceptedRelayPacket] = {}
        self._delivery_attempts: list[RelayDeliveryAttempt] = []
        self._lock = Lock()

    def accept_packet(
        self,
        *,
        packet_id: str,
        receipt_id: str,
        connection_id: str,
        client_id: str,
        client_trace_id: str,
        received_at: str,
        task_run_packet: dict[str, Any],
        dispatch_metadata: dict[str, Any],
    ) -> AcceptedRelayPacket:
        normalized_packet_id = _require_text(packet_id, "packet_id")
        with self._lock:
            existing = self._packets.get(normalized_packet_id)
            if existing is not None:
                return existing
            accepted_packet = AcceptedRelayPacket(
                packet_id=normalized_packet_id,
                receipt_id=receipt_id,
                connection_id=connection_id,
                client_id=client_id,
                client_trace_id=client_trace_id,
                received_at=received_at,
                task_run_packet=task_run_packet,
                dispatch_metadata=dispatch_metadata,
            )
            self._packets[normalized_packet_id] = accepted_packet
            return accepted_packet

    def mark_delivery_result(
        self,
        packet_id: str,
        *,
        attempted_at: str,
        transport_name: str,
        success: bool,
        transport_message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        server_messages: list[dict[str, Any]] | None = None,
    ) -> AcceptedRelayPacket:
        normalized_packet_id = _require_text(packet_id, "packet_id")
        with self._lock:
            packet = self._packets[normalized_packet_id]
            attempt = RelayDeliveryAttempt(
                packet_id=normalized_packet_id,
                receipt_id=packet.receipt_id,
                transport_name=transport_name,
                attempted_at=attempted_at,
                success=success,
                transport_message_id=transport_message_id,
                error_code=error_code,
                error_message=error_message,
            )
            packet.attempt_count += 1
            if success:
                packet.delivery_status = "delivered"
                packet.transport_message_id = _require_optional_text(transport_message_id, "transport_message_id")
                packet.delivered_at = _require_text(attempted_at, "attempted_at")
                packet.last_error_code = None
                packet.last_error_message = None
                if server_messages is not None:
                    packet.server_messages = _require_mapping_list(server_messages, "server_messages")
            else:
                packet.delivery_status = "failed"
                packet.last_error_code = _require_optional_text(error_code, "error_code")
                packet.last_error_message = _require_optional_text(error_message, "error_message")
            self._delivery_attempts.append(attempt)
            return packet

    def get_packet(self, packet_id: str) -> AcceptedRelayPacket | None:
        normalized_packet_id = _require_text(packet_id, "packet_id")
        with self._lock:
            return self._packets.get(normalized_packet_id)

    def list_packets(self) -> list[AcceptedRelayPacket]:
        with self._lock:
            return sorted(self._packets.values(), key=lambda item: item.packet_id)

    def list_delivery_attempts(self) -> list[RelayDeliveryAttempt]:
        with self._lock:
            return list(self._delivery_attempts)

    def count(self) -> int:
        with self._lock:
            return len(self._packets)


class PersistentAcceptedPacketStore:
    def __init__(self, state_dir: str | Path) -> None:
        self._state_dir = Path(state_dir)
        self._packets_path = self._state_dir / "packets.json"
        self._delivery_attempts_path = self._state_dir / "delivery_attempts.jsonl"
        self._lock = Lock()
        self._packets: dict[str, AcceptedRelayPacket] = {}
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._packets_path, default={"packets": []})
        packets = payload.get("packets", []) if isinstance(payload, dict) else []
        for item in packets:
            packet = AcceptedRelayPacket(**item)
            self._packets[packet.packet_id] = packet

    def _save(self) -> None:
        payload = {
            "version": 1,
            "packets": [asdict(item) for item in sorted(self._packets.values(), key=lambda item: item.packet_id)],
        }
        _write_json(self._packets_path, payload)

    def _append_delivery_attempt(self, attempt: RelayDeliveryAttempt) -> None:
        self._delivery_attempts_path.parent.mkdir(parents=True, exist_ok=True)
        with self._delivery_attempts_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(attempt), ensure_ascii=False) + "\n")

    def accept_packet(
        self,
        *,
        packet_id: str,
        receipt_id: str,
        connection_id: str,
        client_id: str,
        client_trace_id: str,
        received_at: str,
        task_run_packet: dict[str, Any],
        dispatch_metadata: dict[str, Any],
    ) -> AcceptedRelayPacket:
        normalized_packet_id = _require_text(packet_id, "packet_id")
        with self._lock:
            existing = self._packets.get(normalized_packet_id)
            if existing is not None:
                return existing
            accepted_packet = AcceptedRelayPacket(
                packet_id=normalized_packet_id,
                receipt_id=receipt_id,
                connection_id=connection_id,
                client_id=client_id,
                client_trace_id=client_trace_id,
                received_at=received_at,
                task_run_packet=task_run_packet,
                dispatch_metadata=dispatch_metadata,
            )
            self._packets[normalized_packet_id] = accepted_packet
            self._save()
            return accepted_packet

    def mark_delivery_result(
        self,
        packet_id: str,
        *,
        attempted_at: str,
        transport_name: str,
        success: bool,
        transport_message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        server_messages: list[dict[str, Any]] | None = None,
    ) -> AcceptedRelayPacket:
        normalized_packet_id = _require_text(packet_id, "packet_id")
        with self._lock:
            packet = self._packets[normalized_packet_id]
            attempt = RelayDeliveryAttempt(
                packet_id=normalized_packet_id,
                receipt_id=packet.receipt_id,
                transport_name=transport_name,
                attempted_at=attempted_at,
                success=success,
                transport_message_id=transport_message_id,
                error_code=error_code,
                error_message=error_message,
            )
            packet.attempt_count += 1
            if success:
                packet.delivery_status = "delivered"
                packet.transport_message_id = _require_optional_text(transport_message_id, "transport_message_id")
                packet.delivered_at = _require_text(attempted_at, "attempted_at")
                packet.last_error_code = None
                packet.last_error_message = None
                if server_messages is not None:
                    packet.server_messages = _require_mapping_list(server_messages, "server_messages")
            else:
                packet.delivery_status = "failed"
                packet.last_error_code = _require_optional_text(error_code, "error_code")
                packet.last_error_message = _require_optional_text(error_message, "error_message")
            self._save()
            self._append_delivery_attempt(attempt)
            return packet

    def get_packet(self, packet_id: str) -> AcceptedRelayPacket | None:
        normalized_packet_id = _require_text(packet_id, "packet_id")
        with self._lock:
            return self._packets.get(normalized_packet_id)

    def list_packets(self) -> list[AcceptedRelayPacket]:
        with self._lock:
            return sorted(self._packets.values(), key=lambda item: item.packet_id)

    def list_delivery_attempts(self) -> list[RelayDeliveryAttempt]:
        if not self._delivery_attempts_path.exists():
            return []
        attempts: list[RelayDeliveryAttempt] = []
        for line in self._delivery_attempts_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            attempts.append(RelayDeliveryAttempt(**json.loads(line)))
        return attempts

    def count(self) -> int:
        with self._lock:
            return len(self._packets)
