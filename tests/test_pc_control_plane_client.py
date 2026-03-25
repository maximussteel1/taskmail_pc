from __future__ import annotations

import asyncio

from mail_runner.config import AppConfig
from mail_runner.pc_control_plane_client import PcControlPlaneClient
from mail_runner.relay_server.app import build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_control_protocol import build_command_dispatch, parse_pc_control_server_message
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import PersistentSessionStore


def test_pc_control_plane_client_registers_and_reports_workspace_snapshot(tmp_path) -> None:
    asyncio.run(_run_pc_control_plane_client_test(tmp_path))


async def _run_pc_control_plane_client_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    (sync_root / "alpha").mkdir(parents=True)

    relay_config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_name="TaskMail Relay",
        from_addr="bot@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    pc_runtime = build_pc_control_runtime(relay_config)
    relay = build_runtime_relay(
        relay_config,
        session_store=session_store,
        packet_store=packet_store,
    )
    server = await start_relay_server(
        relay_config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
        pc_control_runtime=pc_runtime,
    )
    client = None
    try:
        host, port = server.sockets[0].getsockname()[:2]
        app_config = AppConfig(
            relay_url=f"ws://{host}:{port}/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
            project_sync_roots=[str(sync_root)],
        )
        client = PcControlPlaneClient(
            relay_url=app_config.relay_url,
            transport_token=app_config.relay_transport_token,
            pc_id=app_config.relay_client_id,
            client_version=app_config.relay_client_version,
            display_name="pc_home",
            config=app_config,
            heartbeat_interval_seconds=1,
            snapshot_interval_seconds=1,
        )
        client.start()
        await asyncio.sleep(2.5)

        node = pc_runtime.node_store.get_node("pc_home")
        workspace_items = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")
        assert node is not None
        assert len(workspace_items) == 1
        command = parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_cmd_001",
                trace_id="trace_cmd_001",
                pc_id="pc_home",
                connection_epoch=node.current_connection_epoch,
                sent_at="2026-03-25T10:00:20",
                command_id="cmd_001",
                command_type="new_task",
                workspace_id=workspace_items[0].workspace_id,
                execution_policy={
                    "backend": "codex",
                    "profile": "default",
                    "permission": "default",
                    "backend_transport": "sdk",
                },
                command_payload={"task_text": "Refactor floor_shear.py"},
            )
        )
        pc_runtime.enqueue_command(command)
        await _wait_until(
            lambda: (
                pc_runtime.command_store.get_command("pc_home", "cmd_001") is not None
                and pc_runtime.command_store.get_command("pc_home", "cmd_001").ack_status is not None
            ),
            timeout_seconds=5,
        )

        node = pc_runtime.node_store.get_node("pc_home")
        workspace_items = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")
        command_record = pc_runtime.command_store.get_command("pc_home", "cmd_001")

        assert node is not None
        assert node.status == "online"
        assert node.current_connection_epoch >= 1
        assert node.workspace_count == 1
        assert len(workspace_items) == 1
        assert workspace_items[0].repo_path == str(sync_root / "alpha")
        assert command_record is not None
        assert command_record.ack_status == "accepted"
    finally:
        if client is not None:
            client.stop()
        server.close()
        await server.wait_closed()


async def _wait_until(predicate, *, timeout_seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition was not satisfied before timeout")


def test_pc_control_plane_client_rejects_unresolved_profile_model() -> None:
    app_config = AppConfig(
        relay_url="ws://127.0.0.1:8787/relay",
        relay_transport_token="relay-secret",
        relay_client_id="pc_home",
        relay_client_version="0.1.0",
        codex_profile_models={"strong": ""},
    )
    client = PcControlPlaneClient(
        relay_url=app_config.relay_url,
        transport_token=app_config.relay_transport_token,
        pc_id=app_config.relay_client_id,
        client_version=app_config.relay_client_version,
        display_name="pc_home",
        config=app_config,
        workspace_provider=lambda: [
            {
                "workspace_id": "workspace_001",
                "workspace_norm": "workspace_norm_001",
                "repo_path": "E:\\projects\\repo_a",
                "workdir": None,
                "display_name": "repo_a",
                "source": "project_sync_roots",
                "capabilities": {
                    "streaming": True,
                    "artifact_manifest": True,
                    "workspace_snapshot": True,
                    "supported_backends": ["codex"],
                    "profile_catalogs": {"codex": ["strong"]},
                    "permission_modes": ["default", "highest"],
                    "backend_transport_modes": {"codex": ["cli", "sdk"]},
                },
            }
        ],
    )

    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_003",
            trace_id="trace_cmd_003",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:20",
            command_id="cmd_003",
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

    admission = client._admit_command(command)

    assert admission["ack_status"] == "rejected"
    assert admission["error_code"] == "profile_model_unresolved"
