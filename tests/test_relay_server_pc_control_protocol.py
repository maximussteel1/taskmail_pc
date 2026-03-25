from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_control_protocol import (
    PcCommandAckMessage,
    PcCommandDispatchMessage,
    PcControlProtocolError,
    PcErrorMessage,
    PcHelloAckMessage,
    PcHelloMessage,
    PcWorkspaceSnapshotMessage,
    build_command_ack,
    build_command_dispatch,
    build_pc_error,
    build_pc_hello,
    build_pc_hello_ack,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)


def _capabilities() -> dict[str, object]:
    return {
        "streaming": True,
        "artifact_manifest": True,
        "workspace_snapshot": True,
        "supported_backends": ["codex", "opencode"],
        "profile_catalogs": {
            "codex": ["fast", "strong"],
            "opencode": ["fast", "strong"],
        },
        "permission_modes": ["default", "highest"],
        "backend_transport_modes": {
            "codex": ["cli", "sdk"],
            "opencode": ["cli", "sdk"],
        },
    }


def test_pc_hello_roundtrip_parses_capabilities() -> None:
    payload = build_pc_hello(
        message_id="msg_hello_001",
        trace_id="trace_hello_001",
        pc_id="pc_home",
        sent_at="2026-03-25T10:00:00",
        display_name="Home PC",
        client_version="0.1.0",
        host_fingerprint="host_123",
        runtime_fingerprint="runtime_456",
        capabilities=_capabilities(),
    )

    parsed = parse_pc_control_client_message(payload)

    assert isinstance(parsed, PcHelloMessage)
    assert parsed.connection_epoch == 0
    assert parsed.payload["display_name"] == "Home PC"
    assert parsed.payload["capabilities"]["supported_backends"] == ["codex", "opencode"]


def test_workspace_snapshot_requires_workspace_capabilities() -> None:
    payload = {
        "schema_version": "v1",
        "type": "workspace_snapshot",
        "message_id": "msg_ws_001",
        "trace_id": "trace_ws_001",
        "pc_id": "pc_home",
        "connection_epoch": 1,
        "sent_at": "2026-03-25T10:00:10",
        "payload": {
            "snapshot_id": "snapshot_001",
            "workspaces": [
                {
                    "workspace_id": "workspace_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                }
            ],
        },
    }

    with pytest.raises(PcControlProtocolError, match="payload.workspaces\\[0\\]\\.capabilities must be a dict"):
        parse_pc_control_client_message(payload)


def test_workspace_snapshot_roundtrip_parses_workspace_entries() -> None:
    payload = build_workspace_snapshot(
        message_id="msg_ws_001",
        trace_id="trace_ws_001",
        pc_id="pc_home",
        connection_epoch=3,
        sent_at="2026-03-25T10:00:10",
        snapshot_id="snapshot_001",
        workspaces=[
            {
                "workspace_id": "workspace_001",
                "workspace_norm": "e:/projects/repo_a",
                "repo_path": "E:\\projects\\repo_a",
                "workdir": None,
                "display_name": "repo_a",
                "source": "project_sync_roots",
                "capabilities": _capabilities(),
            }
        ],
    )

    parsed = parse_pc_control_client_message(payload)

    assert isinstance(parsed, PcWorkspaceSnapshotMessage)
    assert parsed.payload["snapshot_id"] == "snapshot_001"
    assert parsed.payload["workspaces"][0]["workspace_id"] == "workspace_001"
    assert parsed.payload["workspaces"][0]["capabilities"]["permission_modes"] == ["default", "highest"]


def test_pc_control_server_messages_roundtrip() -> None:
    hello_ack = parse_pc_control_server_message(
        build_pc_hello_ack(
            message_id="msg_hello_ack_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:01",
            keepalive_seconds=15,
        )
    )
    error = parse_pc_control_server_message(
        build_pc_error(
            message_id="msg_error_001",
            trace_id="trace_error_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:02",
            code="stale_connection_epoch",
            message="stale connection",
        )
    )

    assert isinstance(hello_ack, PcHelloAckMessage)
    assert hello_ack.connection_epoch == 7
    assert isinstance(error, PcErrorMessage)
    assert error.payload["code"] == "stale_connection_epoch"


def test_command_dispatch_and_ack_roundtrip() -> None:
    dispatch = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:20",
            command_id="cmd_001",
            command_type="new_task",
            workspace_id="workspace_001",
            execution_policy={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
            },
            command_payload={"task_text": "Refactor floor_shear.py"},
        )
    )
    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:21",
            command_id="cmd_001",
            ack_status="accepted_but_queued",
            queue_position=1,
            reason="command accepted into the local runner queue",
            error_code=None,
        )
    )

    assert isinstance(dispatch, PcCommandDispatchMessage)
    assert dispatch.payload["execution_policy"]["backend"] == "codex"
    assert isinstance(ack, PcCommandAckMessage)
    assert ack.payload["ack_status"] == "accepted_but_queued"
    assert ack.payload["queue_position"] == 1
