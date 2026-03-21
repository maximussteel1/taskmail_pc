from __future__ import annotations

from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.protocol import (
    RelayErrorMessage,
    RelayHelloAckMessage,
    RelayPacketAckMessage,
    parse_server_message,
)
from mail_runner.relay_server.auth import token_fingerprint


def test_loopback_server_accepts_authenticated_hello_and_packet_idempotently() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(
        config,
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        clock=lambda: "2026-03-20T14:20:00",
    )

    hello_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "pc-001",
                "client_version": "0.1.0",
                "transport_token_id": token_fingerprint("relay-secret"),
                "sent_at": "2026-03-20T14:20:00",
            },
            provided_token="relay-secret",
        )
    )
    packet_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "packet",
                "packet_id": "packet:001",
                "client_trace_id": "task_001",
                "task_run_packet": {"packet_id": "packet:001", "task_id": "task_001"},
                "dispatch_metadata": {"subject": "Demo task"},
                "sent_at": "2026-03-20T14:20:01",
            },
            connection_id="conn:pc-001",
        )
    )
    repeated_packet_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "packet",
                "packet_id": "packet:001",
                "client_trace_id": "task_001",
                "task_run_packet": {"packet_id": "packet:001", "task_id": "task_001"},
                "dispatch_metadata": {"subject": "Demo task"},
                "sent_at": "2026-03-20T14:20:02",
            },
            connection_id="conn:pc-001",
        )
    )

    assert isinstance(hello_response, RelayHelloAckMessage)
    assert hello_response.connection_id == "conn:pc-001"
    assert isinstance(packet_response, RelayPacketAckMessage)
    assert packet_response.accepted is True
    assert packet_response.receipt_id == "receipt:packet:001"
    assert isinstance(repeated_packet_response, RelayPacketAckMessage)
    assert repeated_packet_response.receipt_id == "receipt:packet:001"
    assert server.session_store.count() == 1
    assert server.packet_store.count() == 1


def test_loopback_server_rejects_invalid_transport_token() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(config, clock=lambda: "2026-03-20T14:21:00")

    response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "pc-001",
                "client_version": "0.1.0",
                "transport_token_id": "badbadbadbad",
                "sent_at": "2026-03-20T14:21:00",
            },
            provided_token="wrong-secret",
        )
    )

    assert isinstance(response, RelayErrorMessage)
    assert response.code == "unauthorized"
