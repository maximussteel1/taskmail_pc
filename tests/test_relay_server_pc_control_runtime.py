from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcErrorMessage,
    PcHelloAckMessage,
    build_command_ack,
    build_command_dispatch,
    build_heartbeat,
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_command_store import InMemoryPcCommandStore
from mail_runner.relay_server.pc_control_runtime import PcControlRuntime
from mail_runner.relay_server.pc_control_runtime import PcCommandDispatchValidationError
from mail_runner.relay_server.pc_credential_registry import InMemoryPcCredentialRegistry
from mail_runner.relay_server.pc_node_store import InMemoryPcNodeStore
from mail_runner.relay_server.workspace_inventory_store import InMemoryWorkspaceInventoryStore


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


def _runtime() -> PcControlRuntime:
    return PcControlRuntime(
        credential_registry=InMemoryPcCredentialRegistry(default_transport_token="relay-secret"),
        node_store=InMemoryPcNodeStore(),
        workspace_store=InMemoryWorkspaceInventoryStore(),
        command_store=InMemoryPcCommandStore(),
        keepalive_seconds=15,
        clock=lambda: "2026-03-25T10:00:00",
    )


def test_pc_control_runtime_accepts_hello_heartbeat_and_workspace_snapshot() -> None:
    runtime = _runtime()
    hello_message = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )

    response, connection_id, connection_epoch = runtime.handle_hello(hello_message, provided_token="relay-secret")
    parsed_response = parse_pc_control_server_message(response)

    assert isinstance(parsed_response, PcHelloAckMessage)
    assert connection_epoch == 1

    heartbeat = parse_pc_control_client_message(
        build_heartbeat(
            message_id="msg_hb_001",
            trace_id="trace_hb_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:05",
            active_run_count=2,
            workspace_count=1,
            load_hint="busy",
        )
    )
    assert runtime.handle_heartbeat(heartbeat, connection_id=connection_id) is None

    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_ws_001",
            trace_id="trace_ws_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:06",
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "workspace_norm_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                    "source": "project_sync_roots",
                    "capabilities": _capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None

    node = runtime.node_store.get_node("pc_home")
    workspace = runtime.workspace_store.get_workspace("pc_home", "workspace_001")

    assert node is not None
    assert node.active_run_count == 2
    assert node.workspace_count == 1
    assert workspace is not None
    assert workspace.repo_path == "E:\\projects\\repo_a"


def test_pc_control_runtime_rejects_stale_epoch_after_reconnect() -> None:
    runtime = _runtime()
    first_hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, first_connection_id, first_epoch = runtime.handle_hello(first_hello, provided_token="relay-secret")
    second_hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_002",
            trace_id="trace_hello_002",
            pc_id="pc_home",
            sent_at="2026-03-25T10:01:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, _second_connection_id, second_epoch = runtime.handle_hello(second_hello, provided_token="relay-secret")

    stale_heartbeat = parse_pc_control_client_message(
        build_heartbeat(
            message_id="msg_hb_001",
            trace_id="trace_hb_001",
            pc_id="pc_home",
            connection_epoch=first_epoch,
            sent_at="2026-03-25T10:01:05",
            active_run_count=0,
            workspace_count=0,
            load_hint="normal",
        )
    )
    response = runtime.handle_heartbeat(stale_heartbeat, connection_id=first_connection_id)
    parsed = parse_pc_control_server_message(response)

    assert second_epoch == 2
    assert isinstance(parsed, PcErrorMessage)
    assert parsed.payload["code"] == "stale_connection_epoch"


def test_pc_control_runtime_dispatches_pending_command_and_records_ack() -> None:
    runtime = _runtime()
    hello_message = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, connection_id, connection_epoch = runtime.handle_hello(hello_message, provided_token="relay-secret")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_ws_001",
            trace_id="trace_ws_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:06",
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "workspace_norm_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                    "source": "project_sync_roots",
                    "capabilities": _capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None

    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:10",
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
    assert isinstance(command, PcCommandDispatchMessage)

    runtime.enqueue_command(command)
    pending = runtime.collect_pending_dispatches(
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
    )

    assert len(pending) == 1
    assert pending[0]["payload"]["command_id"] == "cmd_001"

    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:11",
            command_id="cmd_001",
            ack_status="accepted",
        )
    )

    assert runtime.handle_command_ack(ack, connection_id=connection_id) is None
    record = runtime.command_store.get_command("pc_home", "cmd_001")

    assert record is not None
    assert record.ack_status == "accepted"


def test_pc_control_runtime_rejects_unsupported_backend_before_dispatch() -> None:
    runtime = _runtime()
    hello_message = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, connection_id, connection_epoch = runtime.handle_hello(hello_message, provided_token="relay-secret")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_ws_001",
            trace_id="trace_ws_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:06",
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "workspace_norm_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                    "source": "project_sync_roots",
                    "capabilities": _capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None

    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_002",
            trace_id="trace_cmd_002",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:12",
            command_id="cmd_002",
            command_type="new_task",
            workspace_id="workspace_001",
            execution_policy={
                "backend": "claude",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
            },
            command_payload={"task_text": "Refactor floor_shear.py"},
        )
    )

    with pytest.raises(PcCommandDispatchValidationError, match="backend is not supported"):
        runtime.enqueue_command(command)
