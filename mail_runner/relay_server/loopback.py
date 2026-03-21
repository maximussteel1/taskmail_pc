"""Local in-process relay loopback for Phase B protocol bring-up."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Callable

from ..outbound.contract import TransportReceipt

from .auth import token_fingerprint, validate_transport_token
from .config import RelayServerConfig
from .packet_store import InMemoryAcceptedPacketStore
from .protocol import (
    ProtocolValidationError,
    RelayHelloMessage,
    RelayPacketMessage,
    RelayPingMessage,
    build_error_message,
    build_hello_ack,
    build_packet_ack,
    parse_client_message,
)
from .session_store import InMemorySessionStore

_DEFAULT_HEARTBEAT_SECONDS = 30


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class LoopbackRelayServer:
    def __init__(
        self,
        config: RelayServerConfig,
        *,
        session_store: InMemorySessionStore | None = None,
        packet_store: InMemoryAcceptedPacketStore | None = None,
        delivery_callback: Callable[[Any], TransportReceipt] | None = None,
        heartbeat_seconds: int = _DEFAULT_HEARTBEAT_SECONDS,
        connection_id_factory: Callable[[str], str] | None = None,
        receipt_id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(heartbeat_seconds, int) or heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be a positive integer")
        self._config = config
        self._session_store = session_store or InMemorySessionStore()
        self._packet_store = packet_store or InMemoryAcceptedPacketStore()
        self._delivery_callback = delivery_callback
        self._heartbeat_seconds = heartbeat_seconds
        self._connection_id_factory = connection_id_factory or self._default_connection_id
        self._receipt_id_factory = receipt_id_factory or self._default_receipt_id
        self._clock = clock or _timestamp

    @property
    def session_store(self) -> InMemorySessionStore:
        return self._session_store

    @property
    def packet_store(self) -> InMemoryAcceptedPacketStore:
        return self._packet_store

    def handle_client_message(
        self,
        payload: dict[str, Any],
        *,
        provided_token: str | None = None,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        now = self._clock()
        try:
            message = parse_client_message(payload)
        except ProtocolValidationError as exc:
            return build_error_message(code="invalid_payload", message=str(exc), sent_at=now)

        if isinstance(message, RelayHelloMessage):
            return self._handle_hello(message, provided_token=provided_token, now=now)
        if isinstance(message, RelayPacketMessage):
            return self._handle_packet(message, connection_id=connection_id, now=now)
        if isinstance(message, RelayPingMessage):
            return self._handle_ping(message, connection_id=connection_id, now=now)
        return build_error_message(code="unsupported_message_type", message="unsupported client message", sent_at=now)

    def _handle_hello(
        self,
        message: RelayHelloMessage,
        *,
        provided_token: str | None,
        now: str,
    ) -> dict[str, Any]:
        if not validate_transport_token(str(provided_token or ""), self._config.transport_token):
            return build_error_message(code="unauthorized", message="transport token mismatch", sent_at=now)
        expected_token_id = token_fingerprint(self._config.transport_token)
        if message.transport_token_id != expected_token_id:
            return build_error_message(code="token_id_mismatch", message="transport token id mismatch", sent_at=now)

        connection_id = self._connection_id_factory(message.client_id)
        self._session_store.upsert_session(
            connection_id=connection_id,
            client_id=message.client_id,
            connected_at=now,
            last_seen_at=message.sent_at,
        )
        return build_hello_ack(
            connection_id=connection_id,
            server_time=now,
            heartbeat_seconds=self._heartbeat_seconds,
        )

    def _handle_packet(
        self,
        message: RelayPacketMessage,
        *,
        connection_id: str | None,
        now: str,
    ) -> dict[str, Any]:
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id:
            return build_error_message(code="missing_connection", message="connection_id is required for packet", sent_at=now)
        session = self._session_store.get_session(normalized_connection_id)
        if session is None:
            return build_error_message(code="unknown_connection", message="connection not found", sent_at=now)
        self._session_store.touch_session(normalized_connection_id, last_seen_at=message.sent_at)
        accepted_packet = self._packet_store.accept_packet(
            packet_id=message.packet_id,
            receipt_id=self._receipt_id_factory(message.packet_id),
            connection_id=normalized_connection_id,
            client_id=session.client_id,
            client_trace_id=message.client_trace_id,
            received_at=now,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )
        if accepted_packet.delivery_status == "delivered":
            return build_packet_ack(
                packet_id=accepted_packet.packet_id,
                accepted=True,
                receipt_id=accepted_packet.receipt_id,
                received_at=accepted_packet.received_at,
                transport_message_id=accepted_packet.transport_message_id,
            )

        if self._delivery_callback is not None:
            receipt = self._delivery_callback(accepted_packet)
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=receipt.transport_name,
                success=receipt.success,
                transport_message_id=receipt.transport_message_id,
                error_message=receipt.error_message,
            )
            if not receipt.success:
                return build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=False,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                    error_message=receipt.error_message,
                )
            return build_packet_ack(
                packet_id=updated_packet.packet_id,
                accepted=True,
                receipt_id=updated_packet.receipt_id,
                received_at=updated_packet.received_at,
                transport_message_id=updated_packet.transport_message_id,
            )
        return build_packet_ack(
            packet_id=accepted_packet.packet_id,
            accepted=True,
            receipt_id=accepted_packet.receipt_id,
            received_at=accepted_packet.received_at,
        )

    def _handle_ping(
        self,
        message: RelayPingMessage,
        *,
        connection_id: str | None,
        now: str,
    ) -> dict[str, Any]:
        normalized_connection_id = str(connection_id or "").strip()
        if normalized_connection_id:
            self._session_store.touch_session(normalized_connection_id, last_seen_at=message.sent_at)
        return {
            "message_type": "ping",
            "sent_at": now,
        }

    @staticmethod
    def _default_connection_id(client_id: str) -> str:
        return f"relay-conn:{client_id}:{secrets.token_hex(4)}"

    @staticmethod
    def _default_receipt_id(packet_id: str) -> str:
        return f"relay-receipt:{packet_id}:{secrets.token_hex(4)}"
