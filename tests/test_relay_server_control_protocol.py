from __future__ import annotations

import pytest

from mail_runner.relay_server.control_protocol import (
    CONTROL_BOOTSTRAP_COMMAND_TYPE,
    CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
    CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
    CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
    ControlBridgeError,
    ControlCommandAckMessage,
    ControlCommandMessage,
    ControlEventMessage,
    ControlHelloAckMessage,
    ControlHelloMessage,
    ControlPongMessage,
    ControlResultMessage,
    build_control_event,
    build_control_hello_ack,
    build_control_pong,
    build_relay_packet_from_control_command,
    negotiate_control_payload_schemas,
    parse_control_client_message,
    parse_control_server_message,
    translate_relay_response_to_control,
)
from mail_runner.relay_server.protocol import ProtocolValidationError, RelayErrorMessage, build_bootstrap_result, build_error_message


def test_parse_control_client_message_supports_hello_command_and_ping() -> None:
    hello = parse_control_client_message(
        {
            "message_type": "hello",
            "client_id": "android-taskmail",
            "client_version": "0.1.0",
            "transport_token_id": "abc123abc123",
            "supported_payload_schemas": [
                CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
                "future-schema",
            ],
            "sent_at": "2026-03-23T12:30:00",
        }
    )
    command = parse_control_client_message(_control_command_payload())
    ping = parse_control_client_message(
        {
            "message_type": "ping",
            "sent_at": "2026-03-23T12:30:02",
        }
    )

    assert isinstance(hello, ControlHelloMessage)
    assert isinstance(command, ControlCommandMessage)
    assert isinstance(ping.message_type, str)
    assert ping.message_type == "ping"


def test_parse_control_client_message_rejects_unknown_message_type() -> None:
    with pytest.raises(ProtocolValidationError):
        parse_control_client_message({"message_type": "unknown"})


def test_parse_control_server_message_supports_control_ack_result_pong_and_error() -> None:
    hello_ack = parse_control_server_message(
        build_control_hello_ack(
            connection_id="conn-001",
            server_time="2026-03-23T12:30:05",
            heartbeat_seconds=30,
            transport_token_id="abc123abc123",
            accepted_payload_schemas=[CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA],
        )
    )
    command_ack = parse_control_server_message(
        translate_relay_response_to_control(
            {
                "message_type": "packet_ack",
                "packet_id": "control:sync:req_001",
                "accepted": True,
                "receipt_id": "receipt:001",
                "received_at": "2026-03-23T12:30:06",
            },
            message=ControlCommandMessage(**_control_command_payload()),
        )
    )
    result = parse_control_server_message(
        translate_relay_response_to_control(
            build_bootstrap_result(
                request_id="req_001",
                packet_id="control:sync:req_001",
                receipt_id="receipt:001",
                result_id="bootstrap-result:req_001",
                sent_at="2026-03-23T12:30:07",
                sync_project_folders_result=_sync_project_folders_result_payload(),
            ),
            message=ControlCommandMessage(**_control_command_payload()),
        )
    )
    event = parse_control_server_message(
        build_control_event(
            request_id="req_001",
            packet_id="control:sync:req_001",
            command_type=CONTROL_BOOTSTRAP_COMMAND_TYPE,
            payload_schema=CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
            event_type="vps_probe_packet_received",
            receipt_id="receipt:001",
            event_id="event:req_001:vps_probe_packet_received",
            sent_at="2026-03-23T12:30:07",
            payload={
                "probe_id": "probe-001",
                "probe_event_type": "vps_probe_packet_received",
                "timeline": {
                    "clock_source": "vps_monotonic",
                    "monotonic_ms": 0,
                },
            },
        )
    )
    pong = parse_control_server_message(build_control_pong(sent_at="2026-03-23T12:30:08"))
    error = parse_control_server_message(
        build_error_message(
            code="unsupported_action",
            message="payload is not supported",
            sent_at="2026-03-23T12:30:09",
        )
    )

    assert isinstance(hello_ack, ControlHelloAckMessage)
    assert isinstance(command_ack, ControlCommandAckMessage)
    assert isinstance(event, ControlEventMessage)
    assert isinstance(result, ControlResultMessage)
    assert isinstance(pong, ControlPongMessage)
    assert isinstance(error, RelayErrorMessage)


def test_control_command_mapping_preserves_replay_identity_and_related_metadata() -> None:
    message = ControlCommandMessage(**_control_command_payload())

    relay_packet = build_relay_packet_from_control_command(message)
    ack = parse_control_server_message(
        translate_relay_response_to_control(
            {
                "message_type": "packet_ack",
                "packet_id": message.packet_id,
                "accepted": True,
                "receipt_id": "receipt:001",
                "received_at": "2026-03-23T12:30:06",
            },
            message=message,
        )
    )
    result = parse_control_server_message(
        translate_relay_response_to_control(
            build_bootstrap_result(
                request_id=message.request_id,
                packet_id=message.packet_id,
                receipt_id="receipt:001",
                result_id="bootstrap-result:req_001",
                sent_at="2026-03-23T12:30:07",
                sync_project_folders_result=_sync_project_folders_result_payload(),
            ),
            message=message,
        )
    )

    assert relay_packet["message_type"] == "packet"
    assert relay_packet["client_trace_id"] == message.request_id
    assert relay_packet["task_run_packet"] == {
        "schema_version": CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
        "action": CONTROL_BOOTSTRAP_COMMAND_TYPE,
        "request_id": "req_001",
        "origin": {
            "client": "android_taskmail",
            "sender_account_uuid": "acc-001",
        },
        "sync_project_folders": {},
    }
    assert relay_packet["dispatch_metadata"]["control_trace"] == {
        "trace_id": "trace-001",
        "probe_id": "probe-001",
    }
    assert relay_packet["dispatch_metadata"]["control_related"] == {"ui_surface": "bootstrap_sheet"}

    assert isinstance(ack, ControlCommandAckMessage)
    assert ack.accepted is True
    assert ack.receipt_id == "receipt:001"
    assert ack.related == {
        "ui_surface": "bootstrap_sheet",
        "trace_id": "trace-001",
        "probe_id": "probe-001",
        "request_id": "req_001",
        "packet_id": "control:sync:req_001",
        "receipt_id": "receipt:001",
    }

    assert isinstance(result, ControlResultMessage)
    assert result.result_type == "sync_project_folders_result"
    assert result.status == "completed"
    assert result.result_id == "bootstrap-result:req_001"
    assert result.related == {
        "ui_surface": "bootstrap_sheet",
        "trace_id": "trace-001",
        "probe_id": "probe-001",
        "request_id": "req_001",
        "packet_id": "control:sync:req_001",
        "receipt_id": "receipt:001",
        "result_id": "bootstrap-result:req_001",
    }
    assert result.payload["sync_project_folders_result"]["roots"][0]["entries"] == [
        {
            "name": "alpha",
            "path": "E:\\projects\\alpha",
        }
    ]


def test_control_command_mapping_rejects_reserved_keys_and_unsupported_schema() -> None:
    unsupported_message = ControlCommandMessage(
        **{
            **_control_command_payload(),
            "payload_schema": "future-schema",
        }
    )
    reserved_key_message = ControlCommandMessage(
        **{
            **_control_command_payload(),
            "payload": {
                "request_id": "should-not-be-here",
                "origin": {
                    "client": "android_taskmail",
                },
                "sync_project_folders": {},
            },
        }
    )

    with pytest.raises(ControlBridgeError, match="payload_schema/command_type is not supported on /control"):
        build_relay_packet_from_control_command(unsupported_message)

    with pytest.raises(ControlBridgeError, match="payload must not redefine reserved keys: request_id"):
        build_relay_packet_from_control_command(reserved_key_message)

    assert negotiate_control_payload_schemas(
        ["future-schema", CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA, CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA]
    ) == [CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA]


def test_transport_probe_command_mapping_enforces_probe_identity_and_no_fallback() -> None:
    message = ControlCommandMessage(**_transport_probe_command_payload())

    relay_packet = build_relay_packet_from_control_command(message)

    assert relay_packet["task_run_packet"] == {
        "schema_version": CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
        "action": CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
        "request_id": "probe_req_001",
        "probe_id": "probe-transport-001",
        "scenario": "android_direct_ping_to_vps_to_pc",
        "direction": "android_to_pc",
        "transport_kind": "mail",
        "payload_text": "PING transport probe",
        "timeout_seconds": 180,
    }
    assert relay_packet["dispatch_metadata"]["fallback_policy"] == "none"
    assert relay_packet["dispatch_metadata"]["control_trace"] == {
        "trace_id": "trace-transport-001",
        "probe_id": "probe-transport-001",
    }

    with pytest.raises(ControlBridgeError, match="trace.probe_id must equal payload.probe_id"):
        build_relay_packet_from_control_command(
            ControlCommandMessage(
                **{
                    **_transport_probe_command_payload(),
                    "trace": {
                        "trace_id": "trace-transport-001",
                        "probe_id": "probe-transport-mismatch",
                    },
                }
            )
        )

    with pytest.raises(ControlBridgeError, match="payload.payload_text must be single-line text"):
        build_relay_packet_from_control_command(
            ControlCommandMessage(
                **{
                    **_transport_probe_command_payload(),
                    "payload": {
                        **_transport_probe_command_payload()["payload"],
                        "payload_text": "PING\ntransport probe",
                    },
                }
            )
        )


def _control_command_payload() -> dict[str, object]:
    return {
        "message_type": "command",
        "request_id": "req_001",
        "packet_id": "control:sync:req_001",
        "command_type": CONTROL_BOOTSTRAP_COMMAND_TYPE,
        "payload_schema": CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
        "trace": {
            "trace_id": "trace-001",
            "probe_id": "probe-001",
        },
        "payload": {
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "sync_project_folders": {},
        },
        "related": {
            "ui_surface": "bootstrap_sheet",
        },
        "sent_at": "2026-03-23T12:30:01",
    }


def _transport_probe_command_payload() -> dict[str, object]:
    return {
        "message_type": "command",
        "request_id": "probe_req_001",
        "packet_id": "android-control:transport-probe:probe_req_001",
        "command_type": CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
        "payload_schema": CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
        "trace": {
            "trace_id": "trace-transport-001",
            "probe_id": "probe-transport-001",
        },
        "payload": {
            "probe_id": "probe-transport-001",
            "scenario": "android_direct_ping_to_vps_to_pc",
            "direction": "android_to_pc",
            "transport_kind": "mail",
            "payload_text": "PING transport probe",
            "timeout_seconds": 180,
        },
        "related": {
            "ui_surface": "transport_probe_sheet",
        },
        "sent_at": "2026-03-24T10:00:00",
    }


def _sync_project_folders_result_payload() -> dict[str, object]:
    return {
        "summary_text": "Project folder sync completed. No task was created.",
        "scanned_at": "2026-03-23T12:30:07",
        "task_created": False,
        "thread_created": False,
        "session_created": False,
        "roots": [
            {
                "root_path": "E:\\projects",
                "available": True,
                "error": None,
                "entries": [
                    {
                        "name": "alpha",
                        "path": "E:\\projects\\alpha",
                    }
                ],
            }
        ],
        "canonical_body_text": "Project folder sync completed. No task was created.\n\n- alpha",
    }
