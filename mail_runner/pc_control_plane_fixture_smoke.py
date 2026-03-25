"""Standalone fixture smoke for the current PC control-plane skeleton."""

from __future__ import annotations

import asyncio
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifact_resolver import write_artifact_index
from .config import AppConfig
from .file_surface import write_artifact_upload_success_binding
from .models import RunArtifact, RunResult
from .pc_control_plane_client import PcControlPlaneClient
from .pc_control_plane_projection import project_artifact_manifest
from .pc_workspace_inventory import build_execution_capabilities
from .relay_server.pc_command_store import InMemoryPcCommandStore
from .relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcErrorMessage,
    PcHelloAckMessage,
    PcOutputResumeRequestMessage,
    build_artifact_manifest,
    build_command_ack,
    build_command_dispatch,
    build_command_event,
    build_command_result,
    build_output_chunk,
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
from .stream_events import STREAM_EVENTS_FILENAME
from .workspace import WorkspaceManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_pc_control_plane_fixture_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class _RunnerProbe:
    def __init__(self, task_root: Path | None = None) -> None:
        self.active = 0
        self.queued = 0
        self.workspace = WorkspaceManager(task_root) if task_root is not None else None

    def active_count(self) -> int:
        return self.active

    def queued_count(self) -> int:
        return self.queued


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent_frames: list[dict[str, Any]] = []

    async def send(self, payload: str) -> None:
        self.sent_frames.append(json.loads(payload))


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


def _projected_artifact_manifest_fixture(run_root: Path) -> dict[str, Any]:
    task_root = run_root / "tasks"
    workspace = WorkspaceManager(task_root)
    thread_id = "thread_cmd_001"
    task_id = "task_cmd_001"
    run_dir = workspace.create_run_dir(thread_id, task_id, exist_ok=True)
    stream_path = workspace.run_file_path(thread_id, task_id, STREAM_EVENTS_FILENAME)
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-03-25T10:00:03",
                        "seq": 1,
                        "thread_id": thread_id,
                        "task_id": task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.delta",
                        "delta": "Hello",
                        "status": "streaming",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-03-25T10:00:04",
                        "seq": 2,
                        "thread_id": thread_id,
                        "task_id": task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.completed",
                        "text": "Hello world",
                        "status": "completed",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts_root = run_dir / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    preview_path = artifacts_root / "preview.png"
    report_path = artifacts_root / "report.md"
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture-preview")
    report_path.write_text("# fixture artifact\n", encoding="utf-8")

    result = RunResult(
        task_id=task_id,
        thread_id=thread_id,
        backend="codex",
        status="success",
        exit_code=0,
        started_at="2026-03-25T10:00:02",
        finished_at="2026-03-25T10:00:05",
        stdout_file="runs/task_cmd_001/stdout.log",
        stderr_file="runs/task_cmd_001/stderr.log",
        summary_file="runs/task_cmd_001/summary.md",
        artifacts_dir="runs/task_cmd_001/artifacts",
        changed_files=[],
        tests_passed=True,
        backend_transport="sdk",
    )
    artifacts = [
        RunArtifact(
            artifact_id="artifact-preview",
            path=str(preview_path),
            name="preview.png",
            kind="image",
            content_type="image/png",
            source="manifest",
            inline_preview=True,
            caption="Fixture preview",
        ),
        RunArtifact(
            artifact_id="artifact-report",
            path=str(report_path),
            name="report.md",
            kind="file",
            content_type="text/markdown",
            source="manifest",
        ),
    ]
    index_path = write_artifact_index(task_root, result, artifacts, [])
    if index_path is None:
        raise RuntimeError("failed to write artifact_index.json for fixture smoke")
    write_artifact_upload_success_binding(
        task_root,
        result,
        artifacts[0],
        role="artifact_delivery",
        file_id="file_preview_001",
        metadata_url="/v1/files/file_preview_001",
        download_url="/v1/files/file_preview_001/content",
        uploaded_at="2026-03-25T10:00:05",
        trace_id="trace_cmd_001",
    )
    binding_path = write_artifact_upload_success_binding(
        task_root,
        result,
        artifacts[0],
        role="artifact_delivery",
        file_id="file_preview_002",
        metadata_url="/v1/files/file_preview_002",
        download_url="/v1/files/file_preview_002/content",
        uploaded_at="2026-03-25T10:00:06",
        trace_id="trace_cmd_001",
    )
    manifest = project_artifact_manifest(task_root, result=result)
    return {
        "task_root": str(task_root),
        "thread_id": thread_id,
        "task_id": task_id,
        "stream_path": str(stream_path),
        "stream_id": f"{thread_id}:{task_id}",
        "artifact_index_path": str(index_path),
        "binding_index_path": str(binding_path),
        "manifest": manifest,
    }


def run_pc_control_plane_fixture_smoke(*, output_dir: Path, run_name: str) -> dict[str, Any]:
    run_root = output_dir / run_name
    config = _config()
    artifact_projection = _projected_artifact_manifest_fixture(run_root)
    workspace_provider = lambda: _workspace_inventory(config)
    runner_probe = _RunnerProbe(Path(artifact_projection["task_root"]))
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
    running_event = parse_pc_control_client_message(
        build_command_event(
            message_id="msg_evt_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:03",
            event_id="event:cmd_001:running",
            command_id="cmd_001",
            event_type="running",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            event_payload={
                "thread_id": artifact_projection["thread_id"],
                "task_id": artifact_projection["task_id"],
            },
        )
    )
    running_event_error = runtime.handle_event(running_event, connection_id=connection_id)
    if running_event_error is not None:
        failures.append("canonical event unexpectedly returned an error.")
    output_chunk_message = parse_pc_control_client_message(
        build_output_chunk(
            message_id="msg_out_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:04",
            output_chunk_id="output:cmd_001:thread_cmd_001:1",
            command_id="cmd_001",
            stream_id=artifact_projection["stream_id"],
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        )
    )
    output_chunk_error = runtime.handle_output_chunk(output_chunk_message, connection_id=connection_id)
    if output_chunk_error is not None:
        failures.append("canonical output_chunk unexpectedly returned an error.")
    client._remember_output_chunk_replay_context(
        "cmd_001",
        trace_id="trace_cmd_001",
        thread_id=str(artifact_projection["thread_id"]),
        task_id=str(artifact_projection["task_id"]),
    )
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
    client._current_connection_epoch = second_connection_epoch
    resume_requests = runtime.collect_output_resume_requests(
        pc_id="pc_home",
        connection_id=second_connection_id,
        connection_epoch=second_connection_epoch,
    )
    resume_request_payload = resume_requests[0] if resume_requests else None
    parsed_resume_request = (
        parse_pc_control_server_message(resume_request_payload) if resume_request_payload is not None else None
    )
    if len(resume_requests) != 1:
        failures.append(f"Expected 1 output_resume_request after reconnect, got {len(resume_requests)}.")
    if not isinstance(parsed_resume_request, PcOutputResumeRequestMessage):
        failures.append("output_resume_request did not parse after reconnect.")
    resume_websocket = _RecordingWebSocket()
    replay_output_chunk_error = None
    if isinstance(parsed_resume_request, PcOutputResumeRequestMessage):
        asyncio.run(
            client._handle_output_resume_request(
                resume_websocket,
                message=parsed_resume_request,
                connection_epoch=second_connection_epoch,
                send_lock=asyncio.Lock(),
            )
        )
        if len(resume_websocket.sent_frames) != 1:
            failures.append(f"Expected 1 replayed output_chunk, got {len(resume_websocket.sent_frames)}.")
        if resume_websocket.sent_frames:
            replay_message = parse_pc_control_client_message(resume_websocket.sent_frames[0])
            replay_output_chunk_error = runtime.handle_output_chunk(
                replay_message,
                connection_id=second_connection_id,
            )
            if replay_output_chunk_error is not None:
                failures.append("replayed output_chunk unexpectedly returned an error.")
            record_after_replay = runtime.command_store.get_command("pc_home", "cmd_001")
            expected_chunks = [(1, "Hello", None), (2, None, "Hello world")]
            actual_chunks = (
                [(chunk.seq, chunk.delta, chunk.text) for chunk in record_after_replay.output_chunks]
                if record_after_replay is not None
                else []
            )
            if actual_chunks != expected_chunks:
                failures.append(f"Unexpected output_chunk timeline after replay: {actual_chunks!r}.")
    smoke_result["steps"]["output_resume_request"] = {
        "resume_request": resume_request_payload,
        "replayed_output_chunks": list(resume_websocket.sent_frames),
        "replay_output_chunk_error": replay_output_chunk_error,
    }
    projected_artifact_manifest = artifact_projection["manifest"]
    if projected_artifact_manifest is None:
        failures.append("real artifact_index.json truth-projection did not produce artifact_manifest.")
        projected_artifact_manifest = {
            "artifacts_root": None,
            "source": None,
            "artifacts": [],
        }
    else:
        projected_items = projected_artifact_manifest["artifacts"]
        if len(projected_items) != 2:
            failures.append(f"Expected 2 projected artifacts, got {len(projected_items)}.")
        preview_item = next((item for item in projected_items if item["artifact_id"] == "artifact-preview"), None)
        report_item = next((item for item in projected_items if item["artifact_id"] == "artifact-report"), None)
        if preview_item is None:
            failures.append("artifact-preview missing from projected artifact_manifest.")
        elif preview_item.get("download_ref") != "/v1/files/file_preview_002/content":
            failures.append("artifact-preview did not project the latest uploaded download_ref.")
        if report_item is None:
            failures.append("artifact-report missing from projected artifact_manifest.")
        elif report_item.get("download_ref") is not None:
            failures.append("artifact-report unexpectedly projected a download_ref without binding.")
    result_message = parse_pc_control_client_message(
        build_command_result(
            message_id="msg_res_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=second_connection_epoch,
            sent_at="2026-03-25T10:00:04",
            result_id="result:cmd_001",
            command_id="cmd_001",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={
                "kind": "run_result",
                "thread_id": artifact_projection["thread_id"],
                "task_id": artifact_projection["task_id"],
            },
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        )
    )
    result_error = runtime.handle_result(result_message, connection_id=second_connection_id)
    if result_error is not None:
        failures.append("canonical result unexpectedly returned an error.")
    smoke_result["steps"]["event_result"] = {
        "event": build_command_event(
            message_id="msg_evt_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:03",
            event_id="event:cmd_001:running",
            command_id="cmd_001",
            event_type="running",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            event_payload={
                "thread_id": artifact_projection["thread_id"],
                "task_id": artifact_projection["task_id"],
            },
        ),
        "result": build_command_result(
            message_id="msg_res_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=second_connection_epoch,
            sent_at="2026-03-25T10:00:04",
            result_id="result:cmd_001",
            command_id="cmd_001",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={
                "kind": "run_result",
                "thread_id": artifact_projection["thread_id"],
                "task_id": artifact_projection["task_id"],
            },
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        ),
        "event_error": running_event_error,
        "result_error": result_error,
    }
    artifact_manifest_message = parse_pc_control_client_message(
        build_artifact_manifest(
            message_id="msg_art_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=second_connection_epoch,
            sent_at="2026-03-25T10:00:05",
            manifest_id="artifact_manifest:cmd_001",
            command_id="cmd_001",
            artifacts_root=projected_artifact_manifest["artifacts_root"],
            source=projected_artifact_manifest["source"],
            artifacts=projected_artifact_manifest["artifacts"],
        )
    )
    artifact_manifest_error = runtime.handle_artifact_manifest(
        artifact_manifest_message,
        connection_id=second_connection_id,
    )
    if artifact_manifest_error is not None:
        failures.append("canonical artifact_manifest unexpectedly returned an error.")
    smoke_result["steps"]["output_chunk_artifact_manifest"] = {
        "output_chunk": build_output_chunk(
            message_id="msg_out_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:04",
            output_chunk_id="output:cmd_001:thread_cmd_001:1",
            command_id="cmd_001",
            stream_id=artifact_projection["stream_id"],
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        ),
        "artifact_manifest": build_artifact_manifest(
            message_id="msg_art_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=second_connection_epoch,
            sent_at="2026-03-25T10:00:05",
            manifest_id="artifact_manifest:cmd_001",
            command_id="cmd_001",
            artifacts_root=projected_artifact_manifest["artifacts_root"],
            source=projected_artifact_manifest["source"],
            artifacts=projected_artifact_manifest["artifacts"],
        ),
        "artifact_truth_projection": artifact_projection,
        "output_chunk_error": output_chunk_error,
        "artifact_manifest_error": artifact_manifest_error,
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
        connection_id=second_connection_id,
        connection_epoch=second_connection_epoch,
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
            connection_epoch=second_connection_epoch,
            sent_at="2026-03-25T10:00:05",
            command_id="cmd_002",
            ack_status=queued_admission["ack_status"],
            queue_position=queued_admission["queue_position"],
            reason=queued_admission["reason"],
            error_code=queued_admission["error_code"],
        )
    )
    queued_ack_error = runtime.handle_command_ack(queued_ack, connection_id=second_connection_id)
    if queued_ack_error is not None:
        failures.append("queued command_ack unexpectedly returned an error.")
    smoke_result["steps"]["queued_dispatch"] = {
        "dispatch": queued_pending[0],
        "ack": build_command_ack(
            message_id="msg_ack_002",
            trace_id="trace_cmd_002",
            pc_id="pc_home",
            connection_epoch=second_connection_epoch,
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

    smoke_result["gaps"] = [
        {
            "kind": "output_resume_request_websocket_roundtrip_not_covered",
            "summary": "This fixture now verifies reconnect -> output_resume_request -> selective replay in an in-memory loopback, but it still does not cover a real websocket roundtrip or multi-PC subscription surface.",
            "recorded": True,
        },
        {
            "kind": "artifact_manifest_live_delivery_not_covered",
            "summary": "This fixture now uses a real artifact_index.json + artifact_file_binding_index.json truth-projection, but it still does not cover a live /v1/files or COS upload roundtrip.",
            "recorded": True,
        },
    ]

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
