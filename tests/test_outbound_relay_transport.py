from __future__ import annotations

from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket
from mail_runner.outbound import relay_transport
from mail_runner.outbound.relay_transport import RelayTransport
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.loopback import LoopbackRelayServer


def test_relay_transport_returns_failure_receipt_when_not_configured() -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:20:00",
        message_kind="status_update",
        content_format="text/plain+text/html",
        html="<html><body>Done.</body></html>",
        text_fallback="Status: DONE\n",
        state_patch={"thread_id": "thread_001"},
        client_trace_id="task_001",
    )
    request = OutboundDispatchRequest(
        packet=packet,
        to_addr="user@example.com",
        subject="[DONE][S:thread_001] Demo task",
    )

    receipt = RelayTransport().send(request)

    assert receipt.success is False
    assert receipt.transport_name == "relay"
    assert receipt.transport_message_id is None
    assert receipt.error_message == "Relay transport is selected, but no relay loopback or transport token is configured."


def test_relay_transport_sends_request_through_loopback_server() -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T14:25:00",
        message_kind="status_update",
        content_format="text/plain+text/html",
        html="<html><body>Done.</body></html>",
        text_fallback="Status: DONE\n",
        state_patch={"thread_id": "thread_001"},
        client_trace_id="task_001",
    )
    request = OutboundDispatchRequest(
        packet=packet,
        to_addr="user@example.com",
        subject="[DONE][S:thread_001] Demo task",
        references=["<root@example.com>"],
        headers={"X-Test": "1"},
    )
    server = LoopbackRelayServer(
        RelayServerConfig(host="127.0.0.1", port=8787, transport_token="relay-secret"),
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        clock=lambda: "2026-03-20T14:25:01",
    )

    receipt = RelayTransport(server, transport_token="relay-secret", client_id="pc-001", client_version="0.1.0").send(
        request
    )

    assert receipt.success is True
    assert receipt.transport_name == "relay"
    assert receipt.transport_message_id == "receipt:packet:task_001:test"
    stored_packet = server.packet_store.get_packet("packet:task_001:test")
    assert stored_packet is not None
    assert stored_packet.dispatch_metadata["subject"] == "[DONE][S:thread_001] Demo task"
    assert stored_packet.dispatch_metadata["references"] == ["<root@example.com>"]


def test_relay_transport_direct_websocket_connect_kwargs_disable_proxy_when_supported(monkeypatch) -> None:
    monkeypatch.setattr(relay_transport, "_WEBSOCKETS_CONNECT_SUPPORTS_PROXY", True)

    assert relay_transport._direct_websocket_connect_kwargs() == {"proxy": None}


def test_relay_transport_direct_websocket_connect_kwargs_stays_empty_when_proxy_kwarg_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(relay_transport, "_WEBSOCKETS_CONNECT_SUPPORTS_PROXY", False)

    assert relay_transport._direct_websocket_connect_kwargs() == {}
