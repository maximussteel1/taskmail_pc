"""Relay-side `transport_probe` support for shared `/control`."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ..mail_io import MailClient, SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from ..outbound.contract import TransportReceipt
from ..transport_probe_mail import (
    TRANSPORT_PROBE_MAIL_HEADER,
    TRANSPORT_PROBE_MAIL_HEADER_VALUE,
    TRANSPORT_PROBE_PACKET_ID_HEADER,
    TRANSPORT_PROBE_REQUEST_ID_HEADER,
    TRANSPORT_PROBE_ID_HEADER,
    TRANSPORT_PROBE_TRACE_ID_HEADER,
    TRANSPORT_PROBE_OBSERVATION_SURFACE,
    load_transport_probe_observation,
)
from .config import RelayServerConfig
from .control_protocol import (
    CONTROL_CHANNEL,
    CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
    CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
    build_control_event,
    build_control_result,
)
from .direct_actions import RelayDirectActionError, RelayDirectActionResult
from .packet_store import AcceptedRelayPacket
from .protocol import RelayPacketMessage

_TRANSPORT_PROBE_SCENARIO = "android_direct_ping_to_vps_to_pc"
_TRANSPORT_PROBE_DIRECTION = "android_to_pc"
_TRANSPORT_PROBE_TRANSPORT_KIND = "mail"
_TRANSPORT_PROBE_TRANSPORT_NAME = "relay_transport_probe_mail_bridge"
_TRANSPORT_PROBE_RESULT_ID_PREFIX = "transport-probe-result"
_TRANSPORT_PROBE_EVENT_ID_PREFIX = "transport-probe-event"
_TRANSPORT_PROBE_RESULT_TYPE = "transport_probe_result"
_TRANSPORT_PROBE_CLOCK_SOURCE = "vps_monotonic"
_TRANSPORT_PROBE_SUBJECT_PREFIX = "[TPROBE][A2P][MAIL]"
_TRANSPORT_PROBE_RESULT_SCOPE_RELAY = "relay_mail_bridge"
_TRANSPORT_PROBE_OBSERVATION_SOURCE = "relay_visible_task_root"
_DEFAULT_OBSERVATION_POLL_SECONDS = 1.0

LOGGER = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelayDirectActionError("invalid_payload", f"{field_name} must be a non-empty string")
    return value.strip()


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RelayDirectActionError("invalid_payload", f"{field_name} must be a dict")
    return dict(value)


def _require_positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise RelayDirectActionError("invalid_payload", f"{field_name} must be a positive integer")
    return value


def is_taskmail_transport_probe_packet(message: RelayPacketMessage) -> bool:
    task_schema = str(message.task_run_packet.get("schema_version") or "").strip()
    dispatch_schema = str(message.dispatch_metadata.get("schema_version") or "").strip()
    task_action = str(message.task_run_packet.get("action") or "").strip().lower()
    dispatch_action = str(message.dispatch_metadata.get("action") or "").strip().lower()
    channel = str(message.dispatch_metadata.get("channel") or "").strip()
    if task_action and dispatch_action and task_action != dispatch_action:
        return False
    normalized_action = task_action or dispatch_action
    if normalized_action != CONTROL_TRANSPORT_PROBE_COMMAND_TYPE:
        return False
    if channel != CONTROL_CHANNEL:
        return False
    return (
        task_schema == CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA
        or dispatch_schema == CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA
    )


@dataclass(slots=True)
class TransportProbePayload:
    request_id: str
    trace_id: str
    probe_id: str
    scenario: str
    direction: str
    transport_kind: str
    payload_text: str
    timeout_seconds: int
    related: dict[str, Any] | None


@dataclass(slots=True)
class TransportProbeObservationWaitResult:
    outcome: str
    result_status: str
    summary_text: str
    observation_scope: str
    observation: dict[str, Any]
    terminal_event_type: str
    terminal_event_payload: dict[str, Any]


class RelayTaskMailTransportProbeHandler:
    """Bridges a deterministic transport probe mail into the bot mailbox and emits control events/results."""

    transport_name = _TRANSPORT_PROBE_TRANSPORT_NAME
    control_payload_schemas = (CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,)

    def __init__(
        self,
        config: RelayServerConfig,
        *,
        mail_client: MailClient | None = None,
        clock: Callable[[], str] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        observation_loader: Callable[[str], dict[str, Any] | None] | None = None,
        poll_interval_seconds: float = _DEFAULT_OBSERVATION_POLL_SECONDS,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._config = config
        self._mail_client = mail_client or MailClient(config.to_taskmail_direct_mail_config())
        self._clock = clock or _timestamp
        self._monotonic_fn = monotonic_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep
        self._task_root = str(config.task_root or "").strip() or None
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._bot_mailbox_addr = _require_text(config.taskmail_bot_mailbox_addr, "taskmail_bot_mailbox_addr")
        if observation_loader is not None:
            self._observation_loader = observation_loader
            self._observation_lookup_enabled = True
        elif self._task_root is not None:
            self._observation_loader = lambda probe_id: load_transport_probe_observation(self._task_root or "", probe_id)
            self._observation_lookup_enabled = True
        else:
            self._observation_loader = lambda _probe_id: None
            self._observation_lookup_enabled = False

    def matches(self, message: RelayPacketMessage) -> bool:
        return is_taskmail_transport_probe_packet(message)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        _parse_transport_probe_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> RelayDirectActionResult:
        payload = _parse_transport_probe_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        related_base = _build_related(
            payload,
            packet=packet,
        )
        monotonic_origin = self._monotonic_fn()

        probe_subject = f"{_TRANSPORT_PROBE_SUBJECT_PREFIX} {payload.probe_id}"
        probe_body = _build_probe_body(payload)
        probe_headers = {
            SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
            TRANSPORT_PROBE_MAIL_HEADER: TRANSPORT_PROBE_MAIL_HEADER_VALUE,
            "X-TaskMail-Probe-Id": payload.probe_id,
            "X-TaskMail-Relay-Packet-Id": packet.packet_id,
            "X-TaskMail-Relay-Request-Id": payload.request_id,
            "X-TaskMail-Trace-Id": payload.trace_id,
        }

        events = [
            self._build_event(
                payload,
                packet=packet,
                related=related_base,
                event_type="vps_probe_packet_received",
                monotonic_origin=monotonic_origin,
                extra_payload={
                    "summary_text": "relay received transport_probe command",
                },
            ),
            self._build_event(
                payload,
                packet=packet,
                related=related_base,
                event_type="vps_probe_packet_accepted",
                monotonic_origin=monotonic_origin,
                extra_payload={
                    "summary_text": "relay accepted transport_probe command into replayable store",
                },
            ),
            self._build_event(
                payload,
                packet=packet,
                related=related_base,
                event_type="vps_probe_bridge_started",
                monotonic_origin=monotonic_origin,
                extra_payload={
                    "summary_text": "relay started probe mail bridge",
                    "delivery": {
                        "to_addr": self._bot_mailbox_addr,
                        "subject": probe_subject,
                    },
                },
            ),
        ]

        transport_message_id: str | None = None
        result_status = "completed"
        outcome = "submitted"
        summary_text = "relay submitted transport_probe mail toward the bot mailbox"
        observation_scope = _TRANSPORT_PROBE_RESULT_SCOPE_RELAY
        result_delivery: dict[str, Any] = {
            "to_addr": self._bot_mailbox_addr,
            "subject": probe_subject,
        }
        observation_payload: dict[str, Any] = {
            "status": "not_attempted",
            "source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
            "reason_code": "mail_not_submitted",
            "reason_text": "relay has not submitted a transport_probe mail yet",
        }

        try:
            transport_message_id = self._mail_client.send_mail(
                to_addr=self._bot_mailbox_addr,
                subject=probe_subject,
                body=probe_body,
                headers=probe_headers,
            )
        except Exception as exc:
            error_message = str(exc).strip() or "failed to submit transport_probe mail"
            outcome = "failed"
            result_status = "failed"
            summary_text = "relay failed to submit transport_probe mail"
            result_delivery["error_code"] = "mail_submit_failed"
            result_delivery["error_message"] = error_message
            observation_payload = {
                "status": "not_attempted",
                "source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                "reason_code": "mail_submit_failed",
                "reason_text": "relay did not wait for PC observation because mail submission failed",
            }
        else:
            result_delivery["transport_message_id"] = transport_message_id
            events.append(
                self._build_event(
                    payload,
                    packet=packet,
                    related=related_base,
                    event_type="vps_probe_bridge_finished",
                    monotonic_origin=monotonic_origin,
                    extra_payload={
                        "summary_text": "relay finished probe mail bridge submission",
                        "delivery": {
                            "to_addr": self._bot_mailbox_addr,
                            "subject": probe_subject,
                            "transport_message_id": transport_message_id,
                        },
                    },
                )
            )
            events.append(
                self._build_event(
                    payload,
                    packet=packet,
                    related=related_base,
                    event_type="vps_probe_observation_started",
                    monotonic_origin=monotonic_origin,
                    extra_payload={
                        "summary_text": "relay started waiting for PC mailbox observation",
                        "timeout_seconds": payload.timeout_seconds,
                        "observation_source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                        "delivery": {
                            "transport_message_id": transport_message_id,
                        },
                    },
                )
            )
            observation_wait_result = self._wait_for_observation(
                payload,
                packet=packet,
                transport_message_id=transport_message_id,
            )
            outcome = observation_wait_result.outcome
            result_status = observation_wait_result.result_status
            summary_text = observation_wait_result.summary_text
            observation_scope = observation_wait_result.observation_scope
            observation_payload = observation_wait_result.observation
            events.append(
                self._build_event(
                    payload,
                    packet=packet,
                    related=related_base,
                    event_type=observation_wait_result.terminal_event_type,
                    monotonic_origin=monotonic_origin,
                    extra_payload=observation_wait_result.terminal_event_payload,
                )
            )

        events.append(
            self._build_event(
                payload,
                packet=packet,
                related=related_base,
                event_type="vps_probe_result_started",
                monotonic_origin=monotonic_origin,
                extra_payload={
                    "summary_text": "relay started transport_probe result materialization",
                    "delivery": dict(result_delivery),
                    "observation": dict(observation_payload),
                },
            )
        )
        events.append(
            self._build_event(
                payload,
                packet=packet,
                related=related_base,
                event_type="vps_probe_result_finished",
                monotonic_origin=monotonic_origin,
                extra_payload={
                    "summary_text": "relay finished transport_probe result materialization",
                    "outcome": outcome,
                    "delivery": dict(result_delivery),
                    "observation": dict(observation_payload),
                },
            )
        )

        result_id = f"{_TRANSPORT_PROBE_RESULT_ID_PREFIX}:{payload.request_id}"
        result_related = dict(related_base)
        result_related["result_id"] = result_id
        result_message = build_control_result(
            request_id=payload.request_id,
            packet_id=packet.packet_id,
            command_type=CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
            payload_schema=CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
            result_type=_TRANSPORT_PROBE_RESULT_TYPE,
            status=result_status,
            receipt_id=packet.receipt_id,
            result_id=result_id,
            sent_at=self._clock(),
            payload={
                "probe_id": payload.probe_id,
                "scenario": payload.scenario,
                "direction": payload.direction,
                "transport_kind": payload.transport_kind,
                "payload_text": payload.payload_text,
                "timeout_seconds": payload.timeout_seconds,
                "outcome": outcome,
                "summary_text": summary_text,
                "observation_scope": observation_scope,
                "delivery": result_delivery,
                "observation": observation_payload,
                "timeline": {
                    "clock_source": _TRANSPORT_PROBE_CLOCK_SOURCE,
                    "monotonic_ms": _monotonic_ms(self._monotonic_fn(), monotonic_origin),
                },
            },
            related=result_related,
        )
        return RelayDirectActionResult(
            receipt=TransportReceipt(
                success=True,
                transport_name=self.transport_name,
                sent_at=self._clock(),
                transport_message_id=transport_message_id,
            ),
            server_messages=[*events, result_message],
        )

    def _build_event(
        self,
        payload: TransportProbePayload,
        *,
        packet: AcceptedRelayPacket,
        related: dict[str, Any],
        event_type: str,
        monotonic_origin: float,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = f"{_TRANSPORT_PROBE_EVENT_ID_PREFIX}:{payload.request_id}:{event_type}"
        event_related = dict(related)
        event_related["event_id"] = event_id
        event_payload = {
            "probe_id": payload.probe_id,
            "probe_event_type": event_type,
            "scenario": payload.scenario,
            "direction": payload.direction,
            "transport_kind": payload.transport_kind,
            "timeline": {
                "clock_source": _TRANSPORT_PROBE_CLOCK_SOURCE,
                "monotonic_ms": _monotonic_ms(self._monotonic_fn(), monotonic_origin),
            },
        }
        if extra_payload:
            event_payload.update(extra_payload)
        return build_control_event(
            request_id=payload.request_id,
            packet_id=packet.packet_id,
            command_type=CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
            payload_schema=CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
            event_type=event_type,
            receipt_id=packet.receipt_id,
            event_id=event_id,
            sent_at=self._clock(),
            payload=event_payload,
            related=event_related,
        )

    def _wait_for_observation(
        self,
        payload: TransportProbePayload,
        *,
        packet: AcceptedRelayPacket,
        transport_message_id: str,
    ) -> TransportProbeObservationWaitResult:
        if not self._observation_lookup_enabled:
            return TransportProbeObservationWaitResult(
                outcome="submitted",
                result_status="partial",
                summary_text="relay submitted transport_probe mail but cannot read PC observation because relay task_root is not configured",
                observation_scope=_TRANSPORT_PROBE_RESULT_SCOPE_RELAY,
                observation={
                    "status": "unavailable",
                    "source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                    "reason_code": "task_root_unconfigured",
                    "reason_text": "relay task_root is not configured for transport_probe observation lookup",
                    "timeout_seconds": payload.timeout_seconds,
                    "expected_transport_message_id": transport_message_id,
                },
                terminal_event_type="vps_probe_observation_skipped",
                terminal_event_payload={
                    "summary_text": "relay skipped PC mailbox observation wait because relay task_root is not configured",
                    "observation_source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                    "reason_code": "task_root_unconfigured",
                    "delivery": {
                        "transport_message_id": transport_message_id,
                    },
                },
            )

        wait_started_at = self._clock()
        deadline = self._monotonic_fn() + float(payload.timeout_seconds)
        last_load_error: str | None = None
        while True:
            observation = self._load_matching_observation(
                payload,
                packet=packet,
                transport_message_id=transport_message_id,
            )
            if observation is not None:
                observation_payload = {
                    "status": "observed",
                    "source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                    "wait_started_at": wait_started_at,
                    "wait_finished_at": self._clock(),
                    "timeout_seconds": payload.timeout_seconds,
                    "first_observed_at": observation["first_observed_at"],
                    "last_observed_at": observation["last_observed_at"],
                    "seen_count": observation["seen_count"],
                    "observed_message_ids": list(observation["observed_message_ids"]),
                    "delivery": dict(observation["delivery"]),
                }
                return TransportProbeObservationWaitResult(
                    outcome="observed",
                    result_status="completed",
                    summary_text="relay observed PC mailbox evidence for the submitted transport_probe mail",
                    observation_scope=str(observation["observation_scope"]),
                    observation=observation_payload,
                    terminal_event_type="vps_probe_observation_observed",
                    terminal_event_payload={
                        "summary_text": "relay observed PC mailbox evidence for transport_probe mail",
                        "observation_source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                        "observation_scope": str(observation["observation_scope"]),
                        "observation": dict(observation_payload),
                    },
                )
            now_value = self._monotonic_fn()
            if now_value >= deadline:
                observation_payload = {
                    "status": "timed_out",
                    "source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                    "wait_started_at": wait_started_at,
                    "wait_finished_at": self._clock(),
                    "timeout_seconds": payload.timeout_seconds,
                    "expected_transport_message_id": transport_message_id,
                }
                if last_load_error:
                    observation_payload["last_load_error"] = last_load_error
                event_payload = {
                    "summary_text": "relay timed out while waiting for PC mailbox observation",
                    "observation_source": _TRANSPORT_PROBE_OBSERVATION_SOURCE,
                    "timeout_seconds": payload.timeout_seconds,
                    "delivery": {
                        "transport_message_id": transport_message_id,
                    },
                }
                if last_load_error:
                    event_payload["last_load_error"] = last_load_error
                return TransportProbeObservationWaitResult(
                    outcome="timed_out",
                    result_status="partial",
                    summary_text="relay submitted transport_probe mail but timed out waiting for PC mailbox observation",
                    observation_scope=_TRANSPORT_PROBE_RESULT_SCOPE_RELAY,
                    observation=observation_payload,
                    terminal_event_type="vps_probe_observation_timed_out",
                    terminal_event_payload=event_payload,
                )
            sleep_seconds = min(self._poll_interval_seconds, max(0.0, deadline - now_value))
            if sleep_seconds <= 0:
                continue
            try:
                self._sleep_fn(sleep_seconds)
            except Exception as exc:
                last_load_error = str(exc).strip() or "observation_wait_sleep_failed"
                LOGGER.debug(
                    "transport_probe observation wait sleep failed probe_id=%s request_id=%s packet_id=%s error=%s",
                    payload.probe_id,
                    payload.request_id,
                    packet.packet_id,
                    last_load_error,
                )

    def _load_matching_observation(
        self,
        payload: TransportProbePayload,
        *,
        packet: AcceptedRelayPacket,
        transport_message_id: str,
    ) -> dict[str, Any] | None:
        try:
            observation = self._observation_loader(payload.probe_id)
        except Exception as exc:
            LOGGER.debug(
                "transport_probe observation load failed probe_id=%s request_id=%s packet_id=%s error=%s",
                payload.probe_id,
                payload.request_id,
                packet.packet_id,
                str(exc).strip() or exc.__class__.__name__,
            )
            return None
        if observation is None:
            return None
        if not self._observation_matches(
            payload,
            packet=packet,
            transport_message_id=transport_message_id,
            observation=observation,
        ):
            return None
        return observation

    def _observation_matches(
        self,
        payload: TransportProbePayload,
        *,
        packet: AcceptedRelayPacket,
        transport_message_id: str,
        observation: dict[str, Any],
    ) -> bool:
        return all(
            (
                observation.get("schema_version") is not None,
                observation.get("status") == "observed",
                observation.get("probe_id") == payload.probe_id,
                observation.get("request_id") == payload.request_id,
                observation.get("packet_id") == packet.packet_id,
                observation.get("trace_id") == payload.trace_id,
                observation.get("observation_scope") == TRANSPORT_PROBE_OBSERVATION_SURFACE,
                isinstance(observation.get("delivery"), dict),
                isinstance(observation.get("probe_mail"), dict),
                isinstance(observation.get("headers"), dict),
                observation["delivery"].get("transport_message_id") == transport_message_id,
                observation["probe_mail"].get("schema_version") == CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
                observation["probe_mail"].get("scenario") == payload.scenario,
                observation["probe_mail"].get("direction") == payload.direction,
                observation["probe_mail"].get("transport_kind") == payload.transport_kind,
                observation["probe_mail"].get("payload_text") == payload.payload_text,
                observation["probe_mail"].get("timeout_seconds") == payload.timeout_seconds,
                observation["headers"].get(TRANSPORT_PROBE_ID_HEADER) == payload.probe_id,
                observation["headers"].get(TRANSPORT_PROBE_REQUEST_ID_HEADER) == payload.request_id,
                observation["headers"].get(TRANSPORT_PROBE_PACKET_ID_HEADER) == packet.packet_id,
                observation["headers"].get(TRANSPORT_PROBE_TRACE_ID_HEADER) == payload.trace_id,
            )
        )


def _parse_transport_probe_payload(
    *,
    client_trace_id: str,
    task_run_packet: dict[str, Any],
    dispatch_metadata: dict[str, Any],
) -> TransportProbePayload:
    task_payload = _require_mapping(task_run_packet, "task_run_packet")
    dispatch_payload = _require_mapping(dispatch_metadata, "dispatch_metadata")

    task_schema = _require_text(task_payload.get("schema_version"), "task_run_packet.schema_version")
    dispatch_schema = _require_text(dispatch_payload.get("schema_version"), "dispatch_metadata.schema_version")
    if task_schema != CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA or dispatch_schema != CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA:
        raise RelayDirectActionError(
            "validation_failed",
            f"only {CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA} is supported for transport_probe",
        )

    task_action = _require_text(task_payload.get("action"), "task_run_packet.action").lower()
    dispatch_action = _require_text(dispatch_payload.get("action"), "dispatch_metadata.action").lower()
    if task_action != CONTROL_TRANSPORT_PROBE_COMMAND_TYPE or dispatch_action != CONTROL_TRANSPORT_PROBE_COMMAND_TYPE:
        raise RelayDirectActionError(
            "unsupported_action",
            f"only direct action {CONTROL_TRANSPORT_PROBE_COMMAND_TYPE} is supported on /control",
        )

    channel = _require_text(dispatch_payload.get("channel"), "dispatch_metadata.channel")
    if channel != CONTROL_CHANNEL:
        raise RelayDirectActionError("invalid_payload", f"dispatch_metadata.channel must be {CONTROL_CHANNEL}")

    request_id = _require_text(task_payload.get("request_id"), "task_run_packet.request_id")
    if _require_text(client_trace_id, "client_trace_id") != request_id:
        raise RelayDirectActionError("invalid_payload", "client_trace_id must equal task_run_packet.request_id")

    control_trace = _require_mapping(dispatch_payload.get("control_trace"), "dispatch_metadata.control_trace")
    trace_id = _require_text(control_trace.get("trace_id"), "dispatch_metadata.control_trace.trace_id")
    trace_probe_id = _require_text(control_trace.get("probe_id"), "dispatch_metadata.control_trace.probe_id")

    probe_id = _require_text(task_payload.get("probe_id"), "task_run_packet.probe_id")
    if trace_probe_id != probe_id:
        raise RelayDirectActionError("invalid_payload", "dispatch_metadata.control_trace.probe_id must equal probe_id")

    scenario = _require_text(task_payload.get("scenario"), "task_run_packet.scenario")
    if scenario != _TRANSPORT_PROBE_SCENARIO:
        raise RelayDirectActionError(
            "unsupported_action",
            f"only scenario {_TRANSPORT_PROBE_SCENARIO} is currently supported",
        )

    direction = _require_text(task_payload.get("direction"), "task_run_packet.direction")
    if direction != _TRANSPORT_PROBE_DIRECTION:
        raise RelayDirectActionError(
            "unsupported_action",
            f"only direction {_TRANSPORT_PROBE_DIRECTION} is currently supported",
        )

    transport_kind = _require_text(task_payload.get("transport_kind"), "task_run_packet.transport_kind").lower()
    if transport_kind != _TRANSPORT_PROBE_TRANSPORT_KIND:
        raise RelayDirectActionError(
            "unsupported_action",
            f"only transport_kind {_TRANSPORT_PROBE_TRANSPORT_KIND} is currently supported",
        )

    payload_text = _require_text(task_payload.get("payload_text"), "task_run_packet.payload_text")
    if "\n" in payload_text or "\r" in payload_text:
        raise RelayDirectActionError("invalid_payload", "payload_text must be single-line text")

    if "artifacts" in task_payload and task_payload.get("artifacts"):
        raise RelayDirectActionError(
            "unsupported_action",
            "artifact-bearing transport_probe commands are not available in the current slice",
        )

    related = dispatch_payload.get("control_related")
    if related is not None:
        related = _require_mapping(related, "dispatch_metadata.control_related")

    return TransportProbePayload(
        request_id=request_id,
        trace_id=trace_id,
        probe_id=probe_id,
        scenario=scenario,
        direction=direction,
        transport_kind=transport_kind,
        payload_text=payload_text,
        timeout_seconds=_require_positive_int(task_payload.get("timeout_seconds"), "task_run_packet.timeout_seconds"),
        related=related,
    )


def _build_related(
    payload: TransportProbePayload,
    *,
    packet: AcceptedRelayPacket,
) -> dict[str, Any]:
    related = dict(payload.related or {})
    related["trace_id"] = payload.trace_id
    related["probe_id"] = payload.probe_id
    related["request_id"] = payload.request_id
    related["packet_id"] = packet.packet_id
    related["receipt_id"] = packet.receipt_id
    return related


def _build_probe_body(payload: TransportProbePayload) -> str:
    lines = [
        f"Probe-Version: {CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA}",
        f"Probe-Id: {payload.probe_id}",
        f"Scenario: {payload.scenario}",
        f"Direction: {payload.direction}",
        f"Transport-Kind: {payload.transport_kind}",
        f"Timeout-Seconds: {payload.timeout_seconds}",
        f"Payload-Text: {payload.payload_text}",
    ]
    return "\n".join(lines).strip() + "\n"


def _monotonic_ms(now_value: float, origin_value: float) -> int:
    return max(0, int(round((now_value - origin_value) * 1000)))
