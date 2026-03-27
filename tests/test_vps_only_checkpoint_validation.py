from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from mail_runner.artifact_resolver import write_artifact_index
from mail_runner.config import AppConfig
from mail_runner.external_delivery_index import write_external_delivery_index
from mail_runner.models import ExternalDelivery, RunArtifact, RunResult
from mail_runner.relay_server.app import build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_control_protocol import (
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_client_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import PersistentSessionStore
from mail_runner.vps_only_checkpoint_validation import (
    derive_pc_control_operator_nodes_url,
    derive_pc_control_operator_workspaces_url,
    derive_relay_http_url,
    run_vps_only_checkpoint_validation,
)


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _pc_control_capabilities() -> dict[str, object]:
    return {
        "streaming": True,
        "artifact_manifest": True,
        "workspace_snapshot": True,
        "supported_backends": ["codex", "opencode"],
        "profile_catalogs": {"codex": ["default"], "opencode": ["default"]},
        "permission_modes": ["default", "highest"],
        "backend_transport_modes": {
            "codex": ["cli", "sdk"],
            "opencode": ["cli", "sdk"],
        },
    }


def _write_window_run(task_root: Path) -> None:
    thread_id = "thread_301"
    task_id = "task_301"
    run_dir = task_root / thread_id / "runs" / task_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / "small.bin"
    artifact_path.write_bytes(b"window-ready")
    result = RunResult(
        task_id=task_id,
        thread_id=thread_id,
        backend="codex",
        status="success",
        exit_code=0,
        started_at="2026-03-27T00:10:00",
        finished_at="2026-03-27T00:10:01",
        stdout_file=f"runs/{task_id}/stdout.log",
        stderr_file=f"runs/{task_id}/stderr.log",
        summary_file=f"runs/{task_id}/summary.md",
        artifacts_dir=f"runs/{task_id}/artifacts",
        changed_files=[],
        tests_passed=True,
    )
    (run_dir / "result.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact = RunArtifact(
        artifact_id="artifact-small",
        path=str(artifact_path),
        name="small.bin",
        kind="file",
        content_type="application/octet-stream",
        source="directory_fallback",
    )
    index_path = write_artifact_index(task_root, result, [artifact], [])
    assert index_path is not None
    delivery_path = write_external_delivery_index(
        task_root,
        result,
        artifacts=[artifact],
        deliveries=[
            ExternalDelivery(
                artifact_id="artifact-small",
                name="small.bin",
                provider="file_surface",
                url="https://relay.example/v1/files/file_small/content",
                expires_at="2026-03-30T00:00:00",
                object_key="file_small",
                size_bytes=artifact_path.stat().st_size,
                content_type="application/octet-stream",
                bucket="relay-file-surface",
                path=str(artifact_path),
            )
        ],
        recorded_at="2026-03-27T00:10:01",
    )
    assert delivery_path is not None


def test_derive_relay_http_urls_from_relay_url() -> None:
    assert derive_relay_http_url("ws://127.0.0.1:8787/relay", "/healthz") == "http://127.0.0.1:8787/healthz"
    assert (
        derive_pc_control_operator_nodes_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/nodes"
    )
    assert (
        derive_pc_control_operator_workspaces_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/workspaces"
    )


def test_run_vps_only_checkpoint_validation_succeeds_on_local_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_root = tmp_path / "tasks"
    (task_root / "_scheduler").mkdir(parents=True)
    _write_window_run(task_root)

    state_dir = tmp_path / "relay_state"
    relay_config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        task_root=str(task_root),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    pc_runtime = build_pc_control_runtime(relay_config)
    now = _now()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="hello_001",
            trace_id="trace_hello_001",
            pc_id="pc-home",
            sent_at=now,
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_pc_control_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = pc_runtime.handle_hello(hello, provided_token="relay-secret")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="snapshot_001",
            trace_id="trace_snapshot_001",
            pc_id="pc-home",
            connection_epoch=connection_epoch,
            sent_at=now,
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "e:/projects/mail_based_task_manager",
                    "repo_path": "E:\\projects\\mail_based_task_manager",
                    "workdir": None,
                    "display_name": "mail_based_task_manager",
                    "source": "project_sync_roots",
                    "capabilities": _pc_control_capabilities(),
                }
            ],
        )
    )
    assert pc_runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None

    monkeypatch.setattr("mail_runner.relay_server.app.load_config", lambda: AppConfig(control_plane_mode="vps_only"))
    relay = build_runtime_relay(
        relay_config,
        session_store=session_store,
        packet_store=packet_store,
    )

    async def _run() -> dict[str, object]:
        server = await start_relay_server(
            relay_config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=pc_runtime,
        )
        try:
            host, port = server.sockets[0].getsockname()[:2]
            config_path = tmp_path / "mail_config.relay.local.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "outbound_transport: relay",
                        f"relay_url: ws://{host}:{port}/relay",
                        "relay_transport_token: relay-secret",
                        "relay_timeout_seconds: 5",
                        "relay_client_id: pc-home",
                        "relay_client_version: 0.1.0",
                        "control_plane_mode: vps_only",
                        "external_delivery_backend_preference: file_surface",
                        f"task_root: {task_root}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return await asyncio.to_thread(
                run_vps_only_checkpoint_validation,
                output_dir=tmp_path / "validation_output",
                run_name="local-vps-only-checkpoint",
                config_path=config_path,
            )
        finally:
            server.close()
            await server.wait_closed()

    result = asyncio.run(_run())

    assert result["success"] is True
    assert result["healthz"]["success"] is True
    assert result["pc_control"]["success"] is True
    assert result["pc_control"]["target_pc_id"] == "pc-home"
    assert result["file_surface"]["success"] is True
    assert result["window_report"]["window_ready"] is True
    assert result["direct_new_task_probe"]["success"] is True
    assert result["direct_new_task_probe"]["error_code"] == "unsupported_action"
