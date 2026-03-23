from __future__ import annotations

from dataclasses import dataclass

from mail_runner.outbound.relay_bootstrap import build_hello_payload
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.control_protocol import ControlEventMessage, ControlResultMessage, parse_control_server_message
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.protocol import RelayHelloAckMessage, RelayPacketAckMessage, parse_server_message
from mail_runner.relay_server.transport_probe import RelayTaskMailTransportProbeHandler
from mail_runner.transport_probe_mail import (
    TRANSPORT_PROBE_MAIL_HEADER,
    TRANSPORT_PROBE_MAIL_HEADER_VALUE,
    TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION,
    TRANSPORT_PROBE_OBSERVATION_SURFACE,
    TRANSPORT_PROBE_PACKET_ID_HEADER,
    TRANSPORT_PROBE_REQUEST_ID_HEADER,
    TRANSPORT_PROBE_ID_HEADER,
    TRANSPORT_PROBE_TRACE_ID_HEADER,
)


class FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return f"<probe-{len(self.sent_messages)}@example.com>"


class FailingMailClient:
    def __init__(self, message: str) -> None:
        self.message = message

    def send_mail(self, **kwargs):
        raise RuntimeError(self.message)


@dataclass
class _FakeMonotonic:
    value: float

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_transport_probe_handler_bridges_mail_and_replays_stably(tmp_path) -> None:
    mail_client = FakeMailClient()
    monotonic = _FakeMonotonic(100.0)
    observation_state = {"calls": 0}
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
            task_root=str(tmp_path / "shared_task_root"),
            smtp_host="smtp.example.com",
            smtp_user="relay@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
        ),
        direct_packet_handler=RelayTaskMailTransportProbeHandler(
            RelayServerConfig(
                host="127.0.0.1",
                port=8787,
                transport_token="relay-secret",
                state_dir=str(tmp_path / "relay_state_handler"),
                smtp_host="smtp.example.com",
                smtp_user="relay@example.com",
                smtp_password="secret",
                from_addr="relay@example.com",
                taskmail_bot_mailbox_addr="bot@example.com",
                taskmail_direct_from_addr="taskmail-user@example.com",
            ),
            mail_client=mail_client,
            clock=lambda: "2026-03-24T10:00:00",
            monotonic_fn=monotonic,
            sleep_fn=monotonic.sleep,
            observation_loader=lambda _probe_id: _observation_after_first_poll(
                observation_state,
                monotonic,
                transport_message_id="<probe-1@example.com>",
            ),
        ),
        clock=lambda: "2026-03-24T10:00:00",
    )
    connection_id = _connect(server)

    responses = server.handle_client_message_batch(_canonical_transport_probe_packet(), connection_id=connection_id)
    parsed_ack = parse_server_message(responses[0])
    parsed_events = [parse_control_server_message(item) for item in responses[1:-1]]
    parsed_result = parse_control_server_message(responses[-1])
    packet = server.packet_store.get_packet("android-control:transport-probe:probe_req_001")

    assert isinstance(parsed_ack, RelayPacketAckMessage)
    assert parsed_ack.accepted is True
    assert parsed_ack.transport_message_id == "<probe-1@example.com>"
    assert [item.event_type for item in parsed_events] == [
        "vps_probe_packet_received",
        "vps_probe_packet_accepted",
        "vps_probe_bridge_started",
        "vps_probe_bridge_finished",
        "vps_probe_observation_started",
        "vps_probe_observation_observed",
        "vps_probe_result_started",
        "vps_probe_result_finished",
    ]
    assert all(isinstance(item, ControlEventMessage) for item in parsed_events)
    assert isinstance(parsed_result, ControlResultMessage)
    assert parsed_result.result_type == "transport_probe_result"
    assert parsed_result.status == "completed"
    assert parsed_result.payload["outcome"] == "observed"
    assert parsed_result.payload["observation_scope"] == "pc_mailbox_ingress"
    assert parsed_result.payload["delivery"]["transport_message_id"] == "<probe-1@example.com>"
    assert parsed_result.payload["observation"]["status"] == "observed"
    assert parsed_result.payload["observation"]["delivery"]["transport_message_id"] == "<probe-1@example.com>"
    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert packet.attempt_count == 1
    assert len(packet.server_messages) == 9
    assert mail_client.sent_messages[0]["to_addr"] == "bot@example.com"
    assert mail_client.sent_messages[0]["subject"] == "[TPROBE][A2P][MAIL] probe-transport-001"
    assert "Probe-Version: taskmail-transport-probe-payload-v1" in str(mail_client.sent_messages[0]["body"])
    assert mail_client.sent_messages[0]["headers"]["X-TaskMail-Transport-Probe"] == "1"

    replayed = server.handle_client_message_batch(_canonical_transport_probe_packet(), connection_id=connection_id)
    replay_ack = parse_server_message(replayed[0])
    replay_events = [parse_control_server_message(item) for item in replayed[1:-1]]
    replay_result = parse_control_server_message(replayed[-1])

    assert isinstance(replay_ack, RelayPacketAckMessage)
    assert isinstance(replay_result, ControlResultMessage)
    assert replay_ack.receipt_id == parsed_ack.receipt_id
    assert [item.event_id for item in replay_events] == [item.event_id for item in parsed_events]
    assert replay_result.result_id == parsed_result.result_id
    assert replay_result.payload == parsed_result.payload
    assert len(mail_client.sent_messages) == 1


def test_transport_probe_handler_materializes_failed_result_after_accept(tmp_path) -> None:
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
            smtp_host="smtp.example.com",
            smtp_user="relay@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
        ),
        direct_packet_handler=RelayTaskMailTransportProbeHandler(
            RelayServerConfig(
                host="127.0.0.1",
                port=8787,
                transport_token="relay-secret",
                state_dir=str(tmp_path / "relay_state_handler"),
                smtp_host="smtp.example.com",
                smtp_user="relay@example.com",
                smtp_password="secret",
                from_addr="relay@example.com",
                taskmail_bot_mailbox_addr="bot@example.com",
                taskmail_direct_from_addr="taskmail-user@example.com",
            ),
            mail_client=FailingMailClient("smtp temporarily unavailable"),
            clock=lambda: "2026-03-24T10:01:00",
            monotonic_fn=lambda: 100.0,
        ),
        clock=lambda: "2026-03-24T10:01:00",
    )
    connection_id = _connect(server)

    responses = server.handle_client_message_batch(_canonical_transport_probe_packet(), connection_id=connection_id)
    parsed_ack = parse_server_message(responses[0])
    parsed_events = [parse_control_server_message(item) for item in responses[1:-1]]
    parsed_result = parse_control_server_message(responses[-1])
    packet = server.packet_store.get_packet("android-control:transport-probe:probe_req_001")

    assert isinstance(parsed_ack, RelayPacketAckMessage)
    assert parsed_ack.accepted is True
    assert parsed_ack.transport_message_id is None
    assert [item.event_type for item in parsed_events] == [
        "vps_probe_packet_received",
        "vps_probe_packet_accepted",
        "vps_probe_bridge_started",
        "vps_probe_result_started",
        "vps_probe_result_finished",
    ]
    assert isinstance(parsed_result, ControlResultMessage)
    assert parsed_result.status == "failed"
    assert parsed_result.payload["outcome"] == "failed"
    assert parsed_result.payload["observation"]["status"] == "not_attempted"
    assert parsed_result.payload["delivery"]["error_code"] == "mail_submit_failed"
    assert parsed_result.payload["delivery"]["error_message"] == "smtp temporarily unavailable"
    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert packet.attempt_count == 1


def test_transport_probe_handler_returns_partial_when_observation_times_out(tmp_path) -> None:
    mail_client = FakeMailClient()
    monotonic = _FakeMonotonic(100.0)
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
            task_root=str(tmp_path / "shared_task_root"),
            smtp_host="smtp.example.com",
            smtp_user="relay@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
        ),
        direct_packet_handler=RelayTaskMailTransportProbeHandler(
            RelayServerConfig(
                host="127.0.0.1",
                port=8787,
                transport_token="relay-secret",
                state_dir=str(tmp_path / "relay_state_handler"),
                task_root=str(tmp_path / "shared_task_root"),
                smtp_host="smtp.example.com",
                smtp_user="relay@example.com",
                smtp_password="secret",
                from_addr="relay@example.com",
                taskmail_bot_mailbox_addr="bot@example.com",
                taskmail_direct_from_addr="taskmail-user@example.com",
            ),
            mail_client=mail_client,
            clock=lambda: "2026-03-24T10:00:00",
            monotonic_fn=monotonic,
            sleep_fn=monotonic.sleep,
            observation_loader=lambda _probe_id: None,
            poll_interval_seconds=1.0,
        ),
        clock=lambda: "2026-03-24T10:00:00",
    )
    connection_id = _connect(server)
    packet = _canonical_transport_probe_packet()
    packet["task_run_packet"]["timeout_seconds"] = 2

    responses = server.handle_client_message_batch(packet, connection_id=connection_id)
    parsed_ack = parse_server_message(responses[0])
    parsed_events = [parse_control_server_message(item) for item in responses[1:-1]]
    parsed_result = parse_control_server_message(responses[-1])

    assert isinstance(parsed_ack, RelayPacketAckMessage)
    assert parsed_ack.accepted is True
    assert [item.event_type for item in parsed_events] == [
        "vps_probe_packet_received",
        "vps_probe_packet_accepted",
        "vps_probe_bridge_started",
        "vps_probe_bridge_finished",
        "vps_probe_observation_started",
        "vps_probe_observation_timed_out",
        "vps_probe_result_started",
        "vps_probe_result_finished",
    ]
    assert isinstance(parsed_result, ControlResultMessage)
    assert parsed_result.status == "partial"
    assert parsed_result.payload["outcome"] == "timed_out"
    assert parsed_result.payload["observation_scope"] == "relay_mail_bridge"
    assert parsed_result.payload["observation"]["status"] == "timed_out"


def test_transport_probe_handler_returns_partial_when_task_root_is_not_configured(tmp_path) -> None:
    mail_client = FakeMailClient()
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
            smtp_host="smtp.example.com",
            smtp_user="relay@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
        ),
        direct_packet_handler=RelayTaskMailTransportProbeHandler(
            RelayServerConfig(
                host="127.0.0.1",
                port=8787,
                transport_token="relay-secret",
                state_dir=str(tmp_path / "relay_state_handler"),
                smtp_host="smtp.example.com",
                smtp_user="relay@example.com",
                smtp_password="secret",
                from_addr="relay@example.com",
                taskmail_bot_mailbox_addr="bot@example.com",
                taskmail_direct_from_addr="taskmail-user@example.com",
            ),
            mail_client=mail_client,
            clock=lambda: "2026-03-24T10:00:00",
            monotonic_fn=lambda: 100.0,
        ),
        clock=lambda: "2026-03-24T10:00:00",
    )
    connection_id = _connect(server)

    responses = server.handle_client_message_batch(_canonical_transport_probe_packet(), connection_id=connection_id)
    parsed_events = [parse_control_server_message(item) for item in responses[1:-1]]
    parsed_result = parse_control_server_message(responses[-1])

    assert [item.event_type for item in parsed_events] == [
        "vps_probe_packet_received",
        "vps_probe_packet_accepted",
        "vps_probe_bridge_started",
        "vps_probe_bridge_finished",
        "vps_probe_observation_started",
        "vps_probe_observation_skipped",
        "vps_probe_result_started",
        "vps_probe_result_finished",
    ]
    assert isinstance(parsed_result, ControlResultMessage)
    assert parsed_result.status == "partial"
    assert parsed_result.payload["outcome"] == "submitted"
    assert parsed_result.payload["observation"]["status"] == "unavailable"


def test_transport_probe_handler_rejects_unsupported_scenario_before_accept(tmp_path) -> None:
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
            smtp_host="smtp.example.com",
            smtp_user="relay@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
        ),
        direct_packet_handler=RelayTaskMailTransportProbeHandler(
            RelayServerConfig(
                host="127.0.0.1",
                port=8787,
                transport_token="relay-secret",
                state_dir=str(tmp_path / "relay_state_handler"),
                smtp_host="smtp.example.com",
                smtp_user="relay@example.com",
                smtp_password="secret",
                from_addr="relay@example.com",
                taskmail_bot_mailbox_addr="bot@example.com",
                taskmail_direct_from_addr="taskmail-user@example.com",
            ),
            mail_client=FakeMailClient(),
            clock=lambda: "2026-03-24T10:02:00",
            monotonic_fn=lambda: 100.0,
        ),
        clock=lambda: "2026-03-24T10:02:00",
    )
    connection_id = _connect(server)
    packet = _canonical_transport_probe_packet()
    packet["task_run_packet"]["scenario"] = "android_mail_ping_to_pc"

    parsed = parse_server_message(server.handle_client_message(packet, connection_id=connection_id))

    assert parsed.code == "unsupported_action"
    assert server.packet_store.get_packet("android-control:transport-probe:probe_req_001") is None


def _connect(server: LoopbackRelayServer) -> str:
    response = server.handle_client_message(
        build_hello_payload(
            client_id="android-control",
            client_version="0.1.0",
            transport_token="relay-secret",
        ),
        provided_token="relay-secret",
    )
    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayHelloAckMessage)
    return parsed.connection_id


def _canonical_transport_probe_packet() -> dict[str, object]:
    return {
        "message_type": "packet",
        "packet_id": "android-control:transport-probe:probe_req_001",
        "client_trace_id": "probe_req_001",
        "task_run_packet": {
            "schema_version": "taskmail-transport-probe-payload-v1",
            "action": "transport_probe",
            "request_id": "probe_req_001",
            "probe_id": "probe-transport-001",
            "scenario": "android_direct_ping_to_vps_to_pc",
            "direction": "android_to_pc",
            "transport_kind": "mail",
            "payload_text": "PING transport probe",
            "timeout_seconds": 180,
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "taskmail-transport-probe-payload-v1",
            "action": "transport_probe",
            "fallback_policy": "none",
            "control_trace": {
                "trace_id": "trace-transport-001",
                "probe_id": "probe-transport-001",
            },
            "control_related": {
                "ui_surface": "transport_probe_sheet",
            },
        },
        "sent_at": "2026-03-24T10:00:00",
    }


def _observation_after_first_poll(
    state: dict[str, int],
    monotonic: _FakeMonotonic,
    *,
    transport_message_id: str,
) -> dict[str, object] | None:
    state["calls"] += 1
    if monotonic.value < 101.0:
        return None
    return _transport_probe_observation(transport_message_id=transport_message_id)


def _transport_probe_observation(*, transport_message_id: str) -> dict[str, object]:
    return {
        "schema_version": TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION,
        "probe_id": "probe-transport-001",
        "request_id": "probe_req_001",
        "packet_id": "android-control:transport-probe:probe_req_001",
        "trace_id": "trace-transport-001",
        "status": "observed",
        "observation_scope": TRANSPORT_PROBE_OBSERVATION_SURFACE,
        "first_observed_at": "2026-03-24T10:00:01",
        "last_observed_at": "2026-03-24T10:00:01",
        "seen_count": 1,
        "observed_message_ids": [transport_message_id],
        "delivery": {
            "transport_message_id": transport_message_id,
            "subject": "[TPROBE][A2P][MAIL] probe-transport-001",
            "from_addr": "taskmail-user@example.com",
            "to_addr": "bot@example.com",
            "mail_date": "2026-03-24T10:00:01",
        },
        "probe_mail": {
            "schema_version": "taskmail-transport-probe-payload-v1",
            "scenario": "android_direct_ping_to_vps_to_pc",
            "direction": "android_to_pc",
            "transport_kind": "mail",
            "payload_text": "PING transport probe",
            "timeout_seconds": 180,
            "body_text": (
                "Probe-Version: taskmail-transport-probe-payload-v1\n"
                "Probe-Id: probe-transport-001\n"
                "Scenario: android_direct_ping_to_vps_to_pc\n"
                "Direction: android_to_pc\n"
                "Transport-Kind: mail\n"
                "Timeout-Seconds: 180\n"
                "Payload-Text: PING transport probe\n"
            ),
        },
        "headers": {
            TRANSPORT_PROBE_MAIL_HEADER: TRANSPORT_PROBE_MAIL_HEADER_VALUE,
            TRANSPORT_PROBE_ID_HEADER: "probe-transport-001",
            TRANSPORT_PROBE_REQUEST_ID_HEADER: "probe_req_001",
            TRANSPORT_PROBE_PACKET_ID_HEADER: "android-control:transport-probe:probe_req_001",
            TRANSPORT_PROBE_TRACE_ID_HEADER: "trace-transport-001",
        },
    }
