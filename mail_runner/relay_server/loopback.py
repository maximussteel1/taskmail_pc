"""Local in-process relay loopback for Phase B protocol bring-up."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any, Callable

from ..outbound.contract import TransportReceipt

from .auth import token_fingerprint, validate_transport_token
from .config import RelayServerConfig
from .direct_actions import RelayDirectActionError, RelayDirectPacketHandler, is_taskmail_direct_packet
from .packet_store import InMemoryAcceptedPacketStore
from .phase3_emitter import (
    build_phase3_session_snapshot_update,
    build_phase3_state_transition_update,
    build_phase3_timeline_append_update,
    project_phase3_session_snapshot,
)
from .phase3_subscription import (
    Phase3SessionDetailProvider,
    Phase3SubscribeSessionDetailRequest,
    Phase3SubscriptionError,
    ThreadStorePhase3SessionDetailProvider,
    parse_phase3_subscribe_request,
)
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
_DEFAULT_PHASE3_BROADCAST_INTERVAL_SECONDS = 1.0
_PHASE3_SNAPSHOT_STATIC_FIELDS = ("session_name", "backend", "repo_path", "workdir")
_PHASE3_MEANINGFUL_STATE_FIELDS = ("status", "lifecycle", "last_summary", "paused_from_status", "question_state")

LOGGER = logging.getLogger(__name__)


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
        direct_packet_handler: RelayDirectPacketHandler | None = None,
        phase3_session_detail_provider: Phase3SessionDetailProvider | None = None,
        heartbeat_seconds: int = _DEFAULT_HEARTBEAT_SECONDS,
        phase3_broadcast_interval_seconds: float = _DEFAULT_PHASE3_BROADCAST_INTERVAL_SECONDS,
        connection_id_factory: Callable[[str], str] | None = None,
        receipt_id_factory: Callable[[str], str] | None = None,
        subscription_id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(heartbeat_seconds, int) or heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be a positive integer")
        if not isinstance(phase3_broadcast_interval_seconds, (int, float)) or phase3_broadcast_interval_seconds <= 0:
            raise ValueError("phase3_broadcast_interval_seconds must be a positive number")
        self._config = config
        self._session_store = session_store or InMemorySessionStore()
        self._packet_store = packet_store or InMemoryAcceptedPacketStore()
        self._delivery_callback = delivery_callback
        self._direct_packet_handler = direct_packet_handler
        self._phase3_session_detail_provider = phase3_session_detail_provider or ThreadStorePhase3SessionDetailProvider()
        self._heartbeat_seconds = heartbeat_seconds
        self._phase3_broadcast_interval_seconds = float(phase3_broadcast_interval_seconds)
        self._phase3_subscription_cache: dict[str, dict[str, Any]] = {}
        self._connection_id_factory = connection_id_factory or self._default_connection_id
        self._receipt_id_factory = receipt_id_factory or self._default_receipt_id
        self._subscription_id_factory = subscription_id_factory or self._default_subscription_id
        self._clock = clock or _timestamp

    @property
    def session_store(self) -> InMemorySessionStore:
        return self._session_store

    @property
    def packet_store(self) -> InMemoryAcceptedPacketStore:
        return self._packet_store

    @property
    def phase3_broadcast_interval_seconds(self) -> float:
        return self._phase3_broadcast_interval_seconds

    def handle_client_message(
        self,
        payload: dict[str, Any],
        *,
        provided_token: str | None = None,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        responses = self.handle_client_message_batch(
            payload,
            provided_token=provided_token,
            connection_id=connection_id,
        )
        if not responses:
            return build_error_message(code="server_error", message="relay produced no response", sent_at=self._clock())
        return responses[0]

    def collect_subscription_updates(
        self,
        connection_id: str,
        *,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        current_time = now or self._clock()
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id:
            return []
        session = self._session_store.get_session(normalized_connection_id)
        if session is None or session.closed_at is not None:
            self._phase3_subscription_cache.pop(normalized_connection_id, None)
            return []
        subscription_id = str(session.active_subscription_id or "").strip()
        workspace_id = str(session.subscribed_workspace_id or "").strip()
        session_id = str(session.subscribed_session_id or "").strip()
        thread_id = str(session.subscribed_thread_id or "").strip()
        if not subscription_id or not workspace_id or not session_id or not thread_id:
            self._phase3_subscription_cache.pop(normalized_connection_id, None)
            return []
        if self._phase3_session_detail_provider is None:
            return []

        cache = self._phase3_subscription_cache.get(normalized_connection_id)
        request = Phase3SubscribeSessionDetailRequest(
            request_id=subscription_id,
            workspace_id=workspace_id,
            repo_path=None,
            workdir=None,
            session_id=session_id,
            thread_id=thread_id,
            last_known_sequence=session.last_subscription_sequence,
            reason="detail_refresh",
        )
        try:
            session_state, thread_state = self._phase3_session_detail_provider.resolve_session_detail(request)
        except Phase3SubscriptionError as exc:
            LOGGER.debug(
                "phase3 broadcaster skipped subscription update connection_id=%s code=%s message=%s",
                normalized_connection_id,
                exc.code,
                exc.message,
            )
            return []
        except Exception:
            LOGGER.exception(
                "phase3 broadcaster failed to resolve session detail connection_id=%s",
                normalized_connection_id,
            )
            return []

        current_snapshot = project_phase3_session_snapshot(
            session_state,
            thread_state,
            emitted_at=current_time,
        )
        if cache is None:
            sequence = self._session_store.reserve_session_sequence(
                session_state.workspace_id,
                session_state.session_id,
                minimum_next_sequence=session.last_subscription_sequence + 1,
            )
            snapshot_update = build_phase3_session_snapshot_update(
                subscription_id=subscription_id,
                session_state=session_state,
                thread_state=thread_state,
                update_id=f"sessupd:{session_state.session_id}:{sequence}",
                sequence=sequence,
                sent_at=current_time,
            )
            self._store_phase3_subscription_cache(
                normalized_connection_id,
                subscription_id=subscription_id,
                session_state=session_state,
                snapshot=current_snapshot,
            )
            self._session_store.upsert_subscription(
                normalized_connection_id,
                subscription_id=subscription_id,
                workspace_id=session_state.workspace_id,
                session_id=session_state.session_id,
                thread_id=session_state.thread_id,
                last_sequence=sequence,
            )
            return [snapshot_update]

        if self._phase3_requires_snapshot_refresh(
            cache,
            subscription_id=subscription_id,
            session_state=session_state,
            snapshot=current_snapshot,
        ):
            sequence = self._session_store.reserve_session_sequence(
                session_state.workspace_id,
                session_state.session_id,
                minimum_next_sequence=session.last_subscription_sequence + 1,
            )
            snapshot_update = build_phase3_session_snapshot_update(
                subscription_id=subscription_id,
                session_state=session_state,
                thread_state=thread_state,
                update_id=f"sessupd:{session_state.session_id}:{sequence}",
                sequence=sequence,
                sent_at=current_time,
            )
            self._store_phase3_subscription_cache(
                normalized_connection_id,
                subscription_id=subscription_id,
                session_state=session_state,
                snapshot=current_snapshot,
            )
            self._session_store.upsert_subscription(
                normalized_connection_id,
                subscription_id=subscription_id,
                workspace_id=session_state.workspace_id,
                session_id=session_state.session_id,
                thread_id=session_state.thread_id,
                last_sequence=sequence,
            )
            return [snapshot_update]

        updates: list[dict[str, Any]] = []
        last_sequence = session.last_subscription_sequence

        if self._phase3_meaningful_state_changed(cache, session_state=session_state, snapshot=current_snapshot):
            last_sequence = self._session_store.reserve_session_sequence(
                session_state.workspace_id,
                session_state.session_id,
                minimum_next_sequence=last_sequence + 1,
            )
            updates.append(
                build_phase3_state_transition_update(
                    subscription_id=subscription_id,
                    session_state=session_state,
                    thread_state=thread_state,
                    update_id=f"sessupd:{session_state.session_id}:{last_sequence}",
                    sequence=last_sequence,
                    sent_at=current_time,
                )
            )

        append_items = self._phase3_new_timeline_items(cache, current_snapshot)
        if append_items:
            last_sequence = self._session_store.reserve_session_sequence(
                session_state.workspace_id,
                session_state.session_id,
                minimum_next_sequence=last_sequence + 1,
            )
            updates.append(
                build_phase3_timeline_append_update(
                    subscription_id=subscription_id,
                    session_state=session_state,
                    update_id=f"sessupd:{session_state.session_id}:{last_sequence}",
                    sequence=last_sequence,
                    sent_at=current_time,
                    timeline_items=append_items,
                )
            )

        if not updates:
            return []

        self._store_phase3_subscription_cache(
            normalized_connection_id,
            subscription_id=subscription_id,
            session_state=session_state,
            snapshot=current_snapshot,
            keep_existing_timeline=True,
        )
        self._session_store.upsert_subscription(
            normalized_connection_id,
            subscription_id=subscription_id,
            workspace_id=session_state.workspace_id,
            session_id=session_state.session_id,
            thread_id=session_state.thread_id,
            last_sequence=last_sequence,
        )
        return updates

    def handle_client_message_batch(
        self,
        payload: dict[str, Any],
        *,
        provided_token: str | None = None,
        connection_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now = self._clock()
        try:
            message = parse_client_message(payload)
        except ProtocolValidationError as exc:
            return [build_error_message(code="invalid_payload", message=str(exc), sent_at=now)]

        if isinstance(message, RelayHelloMessage):
            return [self._handle_hello(message, provided_token=provided_token, now=now)]
        if isinstance(message, RelayPacketMessage):
            return self._handle_packet_batch(message, connection_id=connection_id, now=now)
        if isinstance(message, RelayPingMessage):
            return [self._handle_ping(message, connection_id=connection_id, now=now)]
        return [build_error_message(code="unsupported_message_type", message="unsupported client message", sent_at=now)]

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

    def _handle_packet_batch(
        self,
        message: RelayPacketMessage,
        *,
        connection_id: str | None,
        now: str,
    ) -> list[dict[str, Any]]:
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id:
            return [build_error_message(code="missing_connection", message="connection_id is required for packet", sent_at=now)]
        session = self._session_store.get_session(normalized_connection_id)
        if session is None:
            return [build_error_message(code="unknown_connection", message="connection not found", sent_at=now)]
        self._session_store.touch_session(normalized_connection_id, last_seen_at=message.sent_at)
        try:
            phase3_subscribe_request = parse_phase3_subscribe_request(message)
        except Phase3SubscriptionError as exc:
            if exc.reject:
                phase3_subscribe_request = None
            else:
                return [build_error_message(code=exc.code, message=exc.message, sent_at=now)]
        direct_packet_handler = None
        if phase3_subscribe_request is None:
            try:
                direct_packet_handler = self._resolve_direct_packet_handler(message)
            except RelayDirectActionError as exc:
                return [build_error_message(code=exc.code, message=exc.message, sent_at=now)]
            if direct_packet_handler is not None:
                try:
                    direct_packet_handler.validate_packet(message)
                except RelayDirectActionError as exc:
                    return [build_error_message(code=exc.code, message=exc.message, sent_at=now)]
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
            return [
                build_packet_ack(
                    packet_id=accepted_packet.packet_id,
                    accepted=True,
                    receipt_id=accepted_packet.receipt_id,
                    received_at=accepted_packet.received_at,
                    transport_message_id=accepted_packet.transport_message_id,
                )
            ]

        if phase3_subscribe_request is not None:
            return self._handle_phase3_subscribe(
                connection_id=normalized_connection_id,
                accepted_packet=accepted_packet,
                request=phase3_subscribe_request,
                now=now,
            )

        if direct_packet_handler is not None:
            try:
                receipt = direct_packet_handler.handle_accepted_packet(accepted_packet)
            except RelayDirectActionError as exc:
                self._packet_store.mark_delivery_result(
                    accepted_packet.packet_id,
                    attempted_at=now,
                    transport_name="relay_direct_new_task",
                    success=False,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                return [build_error_message(code=exc.code, message=exc.message, sent_at=now)]
            except Exception as exc:
                message_text = str(exc).strip() or "direct TaskMail action is temporarily unavailable"
                self._packet_store.mark_delivery_result(
                    accepted_packet.packet_id,
                    attempted_at=now,
                    transport_name="relay_direct_new_task",
                    success=False,
                    error_code="direct_temporarily_unavailable",
                    error_message=message_text,
                )
                return [
                    build_error_message(
                        code="direct_temporarily_unavailable",
                        message=message_text,
                        sent_at=now,
                    )
                ]
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=receipt.transport_name,
                success=receipt.success,
                transport_message_id=receipt.transport_message_id,
                error_code=receipt.error_code,
                error_message=receipt.error_message,
            )
            if not receipt.success:
                return [
                    build_packet_ack(
                        packet_id=updated_packet.packet_id,
                        accepted=False,
                        receipt_id=updated_packet.receipt_id,
                        received_at=updated_packet.received_at,
                        error_message=receipt.error_message,
                        error_code=receipt.error_code,
                    )
                ]
            return [
                build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=True,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                    transport_message_id=updated_packet.transport_message_id,
                )
            ]

        if self._delivery_callback is not None:
            receipt = self._delivery_callback(accepted_packet)
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=receipt.transport_name,
                success=receipt.success,
                transport_message_id=receipt.transport_message_id,
                error_code=receipt.error_code,
                error_message=receipt.error_message,
            )
            if not receipt.success:
                return [
                    build_packet_ack(
                        packet_id=updated_packet.packet_id,
                        accepted=False,
                        receipt_id=updated_packet.receipt_id,
                        received_at=updated_packet.received_at,
                        error_message=receipt.error_message,
                        error_code=receipt.error_code,
                    )
                ]
            return [
                build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=True,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                    transport_message_id=updated_packet.transport_message_id,
                )
            ]
        return [
            build_packet_ack(
                packet_id=accepted_packet.packet_id,
                accepted=True,
                receipt_id=accepted_packet.receipt_id,
                received_at=accepted_packet.received_at,
            )
        ]

    def _handle_phase3_subscribe(
        self,
        *,
        connection_id: str,
        accepted_packet,
        request,
        now: str,
    ) -> list[dict[str, Any]]:
        transport_name = "relay_session_detail_subscription"
        if self._phase3_session_detail_provider is None:
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=transport_name,
                success=False,
                error_code="direct_temporarily_unavailable",
                error_message="session detail subscribe is temporarily unavailable",
            )
            return [
                build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=False,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                    error_code="direct_temporarily_unavailable",
                    error_message="session detail subscribe is temporarily unavailable",
                )
            ]
        try:
            session_state, thread_state = self._phase3_session_detail_provider.resolve_session_detail(request)
            sequence = self._session_store.reserve_session_sequence(
                session_state.workspace_id,
                session_state.session_id,
                minimum_next_sequence=request.last_known_sequence + 1,
            )
            subscription_id = self._subscription_id_factory(session_state.session_id)
            session_update = build_phase3_session_snapshot_update(
                subscription_id=subscription_id,
                session_state=session_state,
                thread_state=thread_state,
                update_id=f"sessupd:{session_state.session_id}:{sequence}",
                sequence=sequence,
                sent_at=now,
            )
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=transport_name,
                success=True,
            )
            self._session_store.upsert_subscription(
                connection_id,
                subscription_id=subscription_id,
                workspace_id=session_state.workspace_id,
                session_id=session_state.session_id,
                thread_id=session_state.thread_id,
                last_sequence=sequence,
            )
            self._store_phase3_subscription_cache(
                connection_id,
                subscription_id=subscription_id,
                session_state=session_state,
                snapshot=session_update["session_snapshot"],
            )
            return [
                build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=True,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                ),
                session_update,
            ]
        except Phase3SubscriptionError as exc:
            if not exc.reject:
                return [build_error_message(code=exc.code, message=exc.message, sent_at=now)]
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=transport_name,
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )
            return [
                build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=False,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                    error_code=exc.code,
                    error_message=exc.message,
                )
            ]
        except Exception as exc:
            message_text = str(exc).strip() or "session detail subscribe is temporarily unavailable"
            updated_packet = self._packet_store.mark_delivery_result(
                accepted_packet.packet_id,
                attempted_at=now,
                transport_name=transport_name,
                success=False,
                error_code="direct_temporarily_unavailable",
                error_message=message_text,
            )
            return [
                build_packet_ack(
                    packet_id=updated_packet.packet_id,
                    accepted=False,
                    receipt_id=updated_packet.receipt_id,
                    received_at=updated_packet.received_at,
                    error_code="direct_temporarily_unavailable",
                    error_message=message_text,
                )
            ]

    def clear_subscription_runtime_state(self, connection_id: str) -> None:
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id:
            return
        self._phase3_subscription_cache.pop(normalized_connection_id, None)

    def _store_phase3_subscription_cache(
        self,
        connection_id: str,
        *,
        subscription_id: str,
        session_state,
        snapshot: dict[str, Any],
        keep_existing_timeline: bool = False,
    ) -> None:
        normalized_connection_id = str(connection_id or "").strip()
        if not normalized_connection_id:
            return
        existing = self._phase3_subscription_cache.get(normalized_connection_id) if keep_existing_timeline else None
        timeline_items_by_key = {
            str(item["business_event_key"]): dict(item)
            for item in snapshot.get("timeline_items", [])
        }
        if existing is not None:
            timeline_items_by_key = dict(existing.get("timeline_items_by_key", {})) | timeline_items_by_key
        self._phase3_subscription_cache[normalized_connection_id] = {
            "subscription_id": subscription_id,
            "workspace_id": session_state.workspace_id,
            "session_id": session_state.session_id,
            "thread_id": session_state.thread_id,
            "task_id": session_state.current_task_id,
            "snapshot": {
                key: snapshot.get(key)
                for key in (
                    "session_name",
                    "backend",
                    "repo_path",
                    "workdir",
                    "status",
                    "lifecycle",
                    "last_summary",
                    "last_active_at",
                    "last_progress_at",
                    "paused_from_status",
                    "question_state",
                )
            },
            "timeline_items_by_key": timeline_items_by_key,
        }

    def _phase3_requires_snapshot_refresh(
        self,
        cache: dict[str, Any],
        *,
        subscription_id: str,
        session_state,
        snapshot: dict[str, Any],
    ) -> bool:
        if cache.get("subscription_id") != subscription_id:
            return True
        if cache.get("workspace_id") != session_state.workspace_id:
            return True
        if cache.get("session_id") != session_state.session_id:
            return True
        if cache.get("thread_id") != session_state.thread_id:
            return True
        for field_name in _PHASE3_SNAPSHOT_STATIC_FIELDS:
            if cache.get("snapshot", {}).get(field_name) != snapshot.get(field_name):
                return True
        timeline_items_by_key = cache.get("timeline_items_by_key", {})
        for item in snapshot.get("timeline_items", []):
            business_event_key = str(item["business_event_key"])
            existing = timeline_items_by_key.get(business_event_key)
            if existing is not None and existing != item:
                return True
        return False

    def _phase3_meaningful_state_changed(
        self,
        cache: dict[str, Any],
        *,
        session_state,
        snapshot: dict[str, Any],
    ) -> bool:
        if cache.get("task_id") != session_state.current_task_id:
            return True
        previous_snapshot = cache.get("snapshot", {})
        for field_name in _PHASE3_MEANINGFUL_STATE_FIELDS:
            if previous_snapshot.get(field_name) != snapshot.get(field_name):
                return True
        return False

    def _phase3_new_timeline_items(
        self,
        cache: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        timeline_items_by_key = cache.get("timeline_items_by_key", {})
        new_items: list[dict[str, Any]] = []
        for item in snapshot.get("timeline_items", []):
            business_event_key = str(item["business_event_key"])
            if business_event_key not in timeline_items_by_key:
                new_items.append(dict(item))
        return new_items

    def _resolve_direct_packet_handler(self, message: RelayPacketMessage) -> RelayDirectPacketHandler | None:
        if self._direct_packet_handler is not None and self._direct_packet_handler.matches(message):
            return self._direct_packet_handler
        if is_taskmail_direct_packet(message):
            raise RelayDirectActionError("unsupported_action", "direct TaskMail action is not available on this relay")
        return None

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

    @staticmethod
    def _default_subscription_id(session_id: str) -> str:
        return f"relay-sub:{session_id}:{secrets.token_hex(4)}"
