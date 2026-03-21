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
    RelaySessionUpdateMessage,
    build_error_message,
    build_hello_ack,
    build_packet_ack,
    build_session_update,
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


def test_parse_server_message_supports_hello_ack_packet_ack_error_and_session_update() -> None:
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
    session_update = parse_server_message(
        {
            "message_type": "session_update",
            "schema_version": "phase3-direct-inbound-wire-v1",
            "subscription_id": "sub-001",
            "workspace_id": "workspace_001",
            "session_id": "session_001",
            "thread_id": "thread_001",
            "task_id": "task_001",
            "update_id": "sessupd:session_001:1",
            "sequence": 1,
            "sent_at": "2026-03-21T20:00:00",
            "update_type": "session_snapshot",
            "session_snapshot": {
                "session_name": "Demo",
                "backend": "codex",
                "repo_path": "E:\\repo",
                "workdir": "src",
                "status": "running",
                "lifecycle": "active",
                "last_summary": "Running.",
                "last_active_at": "2026-03-21T20:00:00",
                "last_progress_at": "2026-03-21T20:00:00",
                "paused_from_status": None,
                "question_state": None,
                "timeline_items": [],
            },
        }
    )

    assert isinstance(hello_ack, RelayHelloAckMessage)
    assert isinstance(packet_ack, RelayPacketAckMessage)
    assert isinstance(error_message, RelayErrorMessage)
    assert isinstance(session_update, RelaySessionUpdateMessage)


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


def test_build_session_update_validates_snapshot_and_delta_shapes() -> None:
    snapshot_payload = build_session_update(
        schema_version="phase3-direct-inbound-wire-v1",
        subscription_id="sub-001",
        workspace_id="workspace_001",
        session_id="session_001",
        thread_id="thread_001",
        task_id="task_001",
        update_id="sessupd:session_001:1",
        sequence=1,
        sent_at="2026-03-21T20:00:00",
        update_type="session_snapshot",
        session_snapshot={
            "session_name": "Demo",
            "backend": "codex",
            "repo_path": "E:\\repo",
            "workdir": "src",
            "status": "running",
            "lifecycle": "active",
            "last_summary": "Running.",
            "last_active_at": "2026-03-21T20:00:00",
            "last_progress_at": "2026-03-21T20:00:00",
            "paused_from_status": None,
            "question_state": None,
            "timeline_items": [],
        },
    )
    delta_payload = build_session_update(
        schema_version="phase3-direct-inbound-wire-v1",
        subscription_id="sub-001",
        workspace_id="workspace_001",
        session_id="session_001",
        thread_id="thread_001",
        task_id="task_001",
        update_id="sessupd:session_001:2",
        sequence=2,
        sent_at="2026-03-21T20:01:00",
        update_type="session_delta",
        session_delta={
            "delta_type": "timeline_append",
            "timeline_items": [
                {
                    "item_id": "tl_001",
                    "business_event_key": "reply/2026-03-21T20:01:00",
                    "item_type": "assistant_reply_preview",
                    "created_at": "2026-03-21T20:01:00",
                    "status": None,
                    "text": "Preview",
                    "question_set_id": None,
                    "question_ids": [],
                    "paused_from_status": None,
                }
            ],
        },
    )

    assert snapshot_payload["message_type"] == "session_update"
    assert snapshot_payload["update_type"] == "session_snapshot"
    assert delta_payload["message_type"] == "session_update"
    assert delta_payload["update_type"] == "session_delta"


def test_packet_ack_supports_optional_error_code_for_ack_level_rejection() -> None:
    payload = build_packet_ack(
        packet_id="packet:negative:001",
        accepted=False,
        receipt_id="receipt:negative:001",
        received_at="2026-03-21T14:00:00",
        error_message="forced hard rejection for smoke",
        error_code="invalid_payload",
    )

    parsed = parse_server_message(payload)

    assert isinstance(parsed, RelayPacketAckMessage)
    assert parsed.accepted is False
    assert parsed.error_message == "forced hard rejection for smoke"
    assert parsed.error_code == "invalid_payload"
