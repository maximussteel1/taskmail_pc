"""Standalone fixture smoke for the current PC control-plane skeleton."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .pc_control_plane_client import PcControlPlaneClient
from .pc_workspace_inventory import build_execution_capabilities
from .relay_server.pc_command_store import InMemoryPcCommandStore
from .relay_server.pc_control_protocol import (
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
from .relay_server.pc_control_runtime import PcCommandDispatchValidationError, PcControlRuntime
from .relay_server.pc_credential_registry import InMemoryPcCredentialRegistry
from .relay_server.pc_node_store import InMemoryPcNodeStore
from .relay_server.workspace_inventory_store import InMemoryWorkspaceInventoryStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_pc_control_plane_fixture_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class _RunnerProbe:
    def __init__(self) -> None:
        self.active = 0
        self.queued = 0

    def active_count(self) -> int:
        return self.active

    def queued_count(self) -> int:
        return self.queued


def _config() -> AppConfig:
    return AppConfig(
        relay_url="ws://relay.example/relay",
        relay_transport_token="relay-secret",
        relay_client_id="pc_home",
        relay_client_version="0.1.0-dev",
        codex_profile_models={"fast": "gpt-5-codex-mini", "strong": "gpt-5-codex"},
        opencode_profile_models={"fast": "qwen-fast", "strong": "qwen-strong"},
    )


def _workspace_inventory(config: AppConfig) -> list[dict[str, Any]]:
    capabilities = build_execution_capabilities(config).to_payload()
    return [
        {
            "workspace_id": "workspace_001",
            "workspace_norm": "e:/projects/repo_a",
            "repo_path": "E:\\projects\\repo_a",
            "workdir": None,
            "display_name": "repo_a",
            "source": "project_sync_roots",
            "capabilities": capabilities,
        }
    ]


def _runtime() -> PcControlRuntime:
    return PcControlRuntime(
        credential_registry=InMemoryPcCredentialRegistry(default_transport_token="relay-secret"),
        node_store=InMemoryPcNodeStore(),
        workspace_store=InMemoryWorkspaceInventoryStore(),
        command_store=InMemoryPcCommandStore(),
        keepalive_seconds=15,
        clock=lambda: "2026-03-25T10:00:00",
    )


def _client(config: AppConfig, runner_probe: _RunnerProbe, workspace_provider) -> PcControlPlaneClient:
    return PcControlPlaneClient(
        relay_url=config.relay_url,
        transport_token=config.relay_transport_token,
        pc_id=config.relay_client_id,
        client_version=config.relay_client_version,
        display_name="Home PC",
        config=config,
        runner=runner_probe,
        workspace_provider=workspace_provider,
        clock=lambda: "2026-03-25T10:00:00",
        monotonic_fn=lambda: 0.0,
    )


def run_pc_control_plane_fixture_smoke(*, output_dir: Path, run_name: str) -> dict[str, Any]:
    run_root = output_dir / run_name
    config = _config()
    workspace_provider = lambda: _workspace_inventory(config)
    runner_probe = _RunnerProbe()
    runtime = _runtime()
    client = _client(config, runner_probe, workspace_provider)

    failures: list[str] = []
    smoke_result: dict[str, Any] = {
        "success": False,
        "run_name": run_name,
        "steps": {},
        "gaps": [],
        "cleanup": {
            "required": False,
            "cleanup_ok": True,
            "reason": "fixture smoke; no external process or listener is started",
        },
        "failures": failures,
    }

    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0-dev",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=build_execution_capabilities(config).to_payload(),
        )
    )
    hello_response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="relay-secret")
    parsed_hello_response = parse_pc_control_server_message(hello_response)
    if not isinstance(parsed_hello_response, PcHelloAckMessage):
        failures.append("hello did not return PcHelloAckMessage.")
    smoke_result["steps"]["hello"] = {
        "connection_id": connection_id,
        "connection_epoch": connection_epoch,
        "hello_ack": hello_response,
    }

    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_ws_001",
            trace_id="trace_ws_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:01",
            snapshot_id="snapshot_001",
            workspaces=workspace_provider(),
        )
    )
    snapshot_error = runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id)
    if snapshot_error is not None:
        failures.append("workspace_snapshot unexpectedly returned an error.")
    smoke_result["steps"]["workspace_snapshot"] = {
        "error": snapshot_error,
        "workspace_count": len(workspace_provider()),
    }

    accepted_dispatch = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:02",
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
    if not isinstance(accepted_dispatch, PcCommandDispatchMessage):
        failures.append("accepted command_dispatch did not parse.")
    runtime.enqueue_command(accepted_dispatch)
    pending_dispatches = runtime.collect_pending_dispatches(
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
    )
    if len(pending_dispatches) != 1:
        failures.append(f"Expected 1 pending dispatch, got {len(pending_dispatches)}.")
    client_dispatch = parse_pc_control_server_message(pending_dispatches[0])
    accepted_admission = client._admit_command(client_dispatch)
    if accepted_admission["ack_status"] != "accepted":
        failures.append(f"Expected accepted ack_status, got {accepted_admission['ack_status']!r}.")
    accepted_ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:03",
            command_id="cmd_001",
            ack_status=accepted_admission["ack_status"],
            queue_position=accepted_admission["queue_position"],
            reason=accepted_admission["reason"],
            error_code=accepted_admission["error_code"],
        )
    )
    accepted_ack_error = runtime.handle_command_ack(accepted_ack, connection_id=connection_id)
    if accepted_ack_error is not None:
        failures.append("accepted command_ack unexpectedly returned an error.")
    smoke_result["steps"]["accepted_dispatch"] = {
        "dispatch": pending_dispatches[0],
        "ack": build_command_ack(
            message_id="msg_ack_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:03",
            command_id="cmd_001",
            ack_status=accepted_admission["ack_status"],
            queue_position=accepted_admission["queue_position"],
            reason=accepted_admission["reason"],
            error_code=accepted_admission["error_code"],
        ),
    }

    runner_probe.active = 1
    runner_probe.queued = 1
    queued_dispatch = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_002",
            trace_id="trace_cmd_002",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:04",
            command_id="cmd_002",
            command_type="new_task",
            workspace_id="workspace_001",
            execution_policy={
                "backend": "opencode",
                "profile": "fast",
                "permission": "default",
                "backend_transport": "sdk",
            },
            command_payload={"task_text": "Create a smoke note"},
        )
    )
    runtime.enqueue_command(queued_dispatch)
    queued_pending = runtime.collect_pending_dispatches(
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
    )
    if len(queued_pending) != 1:
        failures.append(f"Expected 1 queued dispatch, got {len(queued_pending)}.")
    queued_client_dispatch = parse_pc_control_server_message(queued_pending[0])
    queued_admission = client._admit_command(queued_client_dispatch)
    if queued_admission["ack_status"] != "accepted_but_queued":
        failures.append(f"Expected accepted_but_queued, got {queued_admission['ack_status']!r}.")
    queued_ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_002",
            trace_id="trace_cmd_002",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:05",
            command_id="cmd_002",
            ack_status=queued_admission["ack_status"],
            queue_position=queued_admission["queue_position"],
            reason=queued_admission["reason"],
            error_code=queued_admission["error_code"],
        )
    )
    queued_ack_error = runtime.handle_command_ack(queued_ack, connection_id=connection_id)
    if queued_ack_error is not None:
        failures.append("queued command_ack unexpectedly returned an error.")
    smoke_result["steps"]["queued_dispatch"] = {
        "dispatch": queued_pending[0],
        "ack": build_command_ack(
            message_id="msg_ack_002",
            trace_id="trace_cmd_002",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:05",
            command_id="cmd_002",
            ack_status=queued_admission["ack_status"],
            queue_position=queued_admission["queue_position"],
            reason=queued_admission["reason"],
            error_code=queued_admission["error_code"],
        ),
    }

    unsupported_dispatch = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_003",
            trace_id="trace_cmd_003",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:06",
            command_id="cmd_003",
            command_type="new_task",
            workspace_id="workspace_001",
            execution_policy={
                "backend": "claude",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
            },
            command_payload={"task_text": "Unsupported backend check"},
        )
    )
    unsupported_error = None
    try:
        runtime.enqueue_command(unsupported_dispatch)
    except PcCommandDispatchValidationError as exc:
        unsupported_error = {"code": exc.code, "message": exc.message}
    if unsupported_error is None or unsupported_error["code"] != "unsupported_backend":
        failures.append("unsupported backend dispatch did not fail with unsupported_backend.")
    smoke_result["steps"]["unsupported_backend"] = {"error": unsupported_error}

    second_hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_002",
            trace_id="trace_hello_002",
            pc_id="pc_home",
            sent_at="2026-03-25T10:01:00",
            display_name="Home PC",
            client_version="0.1.0-dev",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=build_execution_capabilities(config).to_payload(),
        )
    )
    second_hello_response, second_connection_id, second_connection_epoch = runtime.handle_hello(
        second_hello,
        provided_token="relay-secret",
    )
    _ = parse_pc_control_server_message(second_hello_response)
    stale_heartbeat = parse_pc_control_client_message(
        build_heartbeat(
            message_id="msg_hb_stale_001",
            trace_id="trace_hb_stale_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:01:05",
            active_run_count=0,
            workspace_count=1,
            load_hint="normal",
        )
    )
    stale_error_payload = runtime.handle_heartbeat(stale_heartbeat, connection_id=connection_id)
    stale_error = parse_pc_control_server_message(stale_error_payload) if stale_error_payload is not None else None
    if not isinstance(stale_error, PcErrorMessage) or stale_error.payload["code"] != "stale_connection_epoch":
        failures.append("stale heartbeat did not return stale_connection_epoch.")
    smoke_result["steps"]["stale_epoch"] = {
        "first_connection_id": connection_id,
        "first_epoch": connection_epoch,
        "second_connection_id": second_connection_id,
        "second_epoch": second_connection_epoch,
        "error": stale_error_payload,
    }

    unsupported_server_types: list[str] = []
    for message_type in ("event", "result", "output_chunk"):
        try:
            parse_pc_control_server_message({"type": message_type})
        except Exception:
            unsupported_server_types.append(message_type)
    smoke_result["gaps"] = [
        {
            "kind": "missing_server_message_types",
            "summary": "Current pc-control protocol/runtime still lacks canonical server message types for event/result/output_chunk.",
            "missing_types": unsupported_server_types,
            "recorded": True,
        },
        {
            "kind": "artifact_manifest_capability_only",
            "summary": "Current workspace capability advertises artifact_manifest, but this control-plane skeleton does not yet emit canonical artifact manifest packets.",
            "recorded": True,
        },
    ]
    if set(unsupported_server_types) != {"event", "result", "output_chunk"}:
        failures.append(f"Unexpected unsupported server type set: {unsupported_server_types}")

    smoke_result["success"] = not failures
    smoke_result_path = run_root / "smoke_result.json"
    smoke_result["smoke_result_path"] = str(smoke_result_path)
    _write_json(smoke_result_path, smoke_result)
    return smoke_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixture smoke for the current PC control-plane skeleton.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"pc-control-plane-fixture-smoke-{_timestamp_slug()}"
    result = run_pc_control_plane_fixture_smoke(output_dir=Path(args.output_dir), run_name=run_name)
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
