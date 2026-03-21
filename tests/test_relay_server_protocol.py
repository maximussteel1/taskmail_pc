from __future__ import annotations

import pytest

from mail_runner.relay_server.protocol import (
    ProtocolValidationError,
    RelayErrorMessage,
    RelayHelloMessage,
    RelayHelloAckMessage,
    RelayPacketMessage,
    RelayPacketAckMessage,
    RelayPingMessage,
    build_error_message,
    build_hello_ack,
    build_packet_ack,
    parse_client_message,
    parse_server_message,
)


def test_parse_client_message_supports_hello_packet_and_ping() -> None:
    hello = parse_client_message(
        {
            "message_type": "hello",
            "client_id": "pc-001",
            "client_version": "0.1.0",
            "transport_token_id": "abc123",
            "sent_at": "2026-03-20T13:30:00",
        }
    )
    packet = parse_client_message(
        {
            "message_type": "packet",
            "packet_id": "packet:001",
            "client_trace_id": "task_001",
            "task_run_packet": {"packet_id": "packet:001"},
            "dispatch_metadata": {"subject": "Demo"},
            "sent_at": "2026-03-20T13:30:01",
        }
    )
    ping = parse_client_message(
        {
            "message_type": "ping",
            "sent_at": "2026-03-20T13:30:02",
        }
    )

    assert isinstance(hello, RelayHelloMessage)
    assert isinstance(packet, RelayPacketMessage)
    assert isinstance(ping, RelayPingMessage)


def test_parse_client_message_rejects_unknown_message_type() -> None:
    with pytest.raises(ProtocolValidationError):
        parse_client_message({"message_type": "unknown"})


def test_parse_server_message_supports_hello_ack_packet_ack_and_error() -> None:
    hello_ack = parse_server_message(
        {
            "message_type": "hello_ack",
            "connection_id": "conn-001",
            "server_time": "2026-03-20T13:30:05",
            "heartbeat_seconds": 30,
        }
    )
    packet_ack = parse_server_message(
        {
            "message_type": "packet_ack",
            "packet_id": "packet:001",
            "accepted": True,
            "receipt_id": "receipt:001",
            "received_at": "2026-03-20T13:30:06",
        }
    )
    error_message = parse_server_message(
        {
            "message_type": "error",
            "code": "unauthorized",
            "message": "token mismatch",
            "sent_at": "2026-03-20T13:30:06",
        }
    )

    assert isinstance(hello_ack, RelayHelloAckMessage)
    assert isinstance(packet_ack, RelayPacketAckMessage)
    assert isinstance(error_message, RelayErrorMessage)


def test_build_hello_ack_packet_ack_and_error_message_validate_inputs() -> None:
    hello_ack = build_hello_ack(
        connection_id="conn-001",
        server_time="2026-03-20T13:30:05",
        heartbeat_seconds=30,
    )
    packet_ack = build_packet_ack(
        packet_id="packet:001",
        accepted=True,
        receipt_id="receipt:001",
        received_at="2026-03-20T13:30:06",
    )
    error_message = build_error_message(
        code="unauthorized",
        message="token mismatch",
        sent_at="2026-03-20T13:30:06",
    )

    assert hello_ack["message_type"] == "hello_ack"
    assert hello_ack["heartbeat_seconds"] == 30
    assert packet_ack["message_type"] == "packet_ack"
    assert packet_ack["accepted"] is True
    assert error_message["message_type"] == "error"
    assert error_message["code"] == "unauthorized"
