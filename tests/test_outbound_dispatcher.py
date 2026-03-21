from __future__ import annotations

import pytest

from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket, TransportReceipt
from mail_runner.outbound.dispatcher import (
    OutboundDispatcher,
    UnsupportedOutboundTransportError,
    build_dispatcher,
    build_transport,
)
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.loopback import LoopbackRelayServer


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[OutboundDispatchRequest] = []

    def send(self, request: OutboundDispatchRequest) -> TransportReceipt:
        self.requests.append(request)
        return TransportReceipt(
            success=True,
            transport_name="email",
            sent_at="2026-03-20T11:05:05",
            transport_message_id="<dispatch@example.com>",
        )


def test_outbound_dispatcher_delegates_to_transport() -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:05:00",
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
        subject="[DONE][S:session_001] Demo task",
        references=["<root@example.com>"],
    )
    transport = FakeTransport()

    receipt = OutboundDispatcher(transport).send(request)

    assert receipt.transport_message_id == "<dispatch@example.com>"
    assert receipt.success is True
    assert transport.requests == [request]


class FakeMailClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_mail(self, **kwargs):
        self.calls.append(kwargs)
        return "<transport-factory@example.com>"


def test_build_dispatcher_uses_email_transport_by_default() -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:05:00",
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
        subject="[DONE][S:session_001] Demo task",
    )
    client = FakeMailClient()

    receipt = build_dispatcher(mail_client=client).send(request)

    assert receipt.success is True
    assert receipt.transport_name == "email"
    assert receipt.transport_message_id == "<transport-factory@example.com>"
    assert client.calls[0]["subject"] == "[DONE][S:session_001] Demo task"


def test_build_transport_rejects_unknown_transport_name() -> None:
    with pytest.raises(UnsupportedOutboundTransportError):
        build_transport(transport_name="bogus")


def test_build_dispatcher_can_construct_relay_transport_with_loopback_server() -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:05:00",
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
        subject="[DONE][S:session_001] Demo task",
    )
    server = LoopbackRelayServer(
        RelayServerConfig(host="127.0.0.1", port=8787, transport_token="relay-secret"),
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        clock=lambda: "2026-03-20T11:05:05",
    )

    receipt = build_dispatcher(
        transport_name="relay",
        relay_server=server,
        relay_transport_token="relay-secret",
        relay_client_id="pc-001",
        relay_client_version="0.1.0",
    ).send(request)

    assert receipt.success is True
    assert receipt.transport_name == "relay"
    assert receipt.transport_message_id == "receipt:packet:task_001:test"
