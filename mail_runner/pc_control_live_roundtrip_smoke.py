"""Live replay/artifact smoke for the VPS-first pc-control plane."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

from .artifact_resolver import write_artifact_index
from .config import load_config
from .external_delivery_index import write_external_delivery_index
from .file_surface import derive_file_surface_url, upload_artifact_to_file_surface
from .models import ExternalDelivery, RunArtifact, RunResult
from .outbound.relay_bootstrap import derive_healthz_url
from .pc_control_live_support import (
    DEFAULT_REMOTE_KEY_PATH,
    DEFAULT_REMOTE_STATE_DIR,
    DEFAULT_REMOTE_USER,
    PROJECT_ROOT,
    build_ssl_context,
    direct_websocket_connect_kwargs,
    fetch_remote_json,
    slug_text,
    timestamp,
    timestamp_slug,
    write_json,
)
from .pc_control_operator_dispatch import (
    build_operator_dispatch_request_payload,
    enqueue_pc_control_operator_dispatch,
)
from .pc_control_plane_client import derive_pc_control_url
from .pc_control_plane_projection import derive_stream_id, project_artifact_manifest, project_output_chunks
from .pc_workspace_inventory import build_execution_capabilities, collect_workspace_inventory
from .relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcErrorMessage,
    PcHelloAckMessage,
    PcOutputResumeRequestMessage,
    build_artifact_manifest,
    build_command_ack,
    build_command_event,
    build_command_result,
    build_heartbeat,
    build_output_chunk,
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_server_message,
)
from .stream_events import StreamEvent, stream_events_path, write_stream_events
from .status import BACKEND_CODEX, RUN_STATUS_SUCCESS

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_pc_control_live_smoke"


def select_probe_workspace(
    workspaces: list[dict[str, Any]],
    *,
    preferred_repo_path: str | Path | None = None,
) -> dict[str, Any]:
    if not workspaces:
        raise ValueError("at least one workspace is required")
    normalized_preferred = str(preferred_repo_path or "").strip().lower()
    if normalized_preferred:
        for workspace in workspaces:
            repo_path = str(workspace.get("repo_path") or "").strip().lower()
            if repo_path == normalized_preferred:
                return dict(workspace)
    return dict(workspaces[0])


def extract_remote_command_record(
    commands_payload: dict[str, Any] | None,
    *,
    pc_id: str,
    command_id: str,
) -> dict[str, Any] | None:
    raw_items = commands_payload.get("commands") if isinstance(commands_payload, dict) else None
    if not isinstance(raw_items, list):
        return None
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("pc_id") or "").strip() != pc_id:
            continue
        if str(item.get("command_id") or "").strip() != command_id:
            continue
        return dict(item)
    return None


def evaluate_live_roundtrip_observation(
    remote_record: dict[str, Any] | None,
    *,
    expected_stream_id: str,
    expected_artifact_ids: list[str],
    expected_after_seq: int,
) -> dict[str, Any]:
    if not isinstance(remote_record, dict):
        return {
            "record_present": False,
            "ack_status": None,
            "event_types": [],
            "resume_after_seq_matches": False,
            "output_chunk_seqs": [],
            "output_stream_ids": [],
            "result_final_status": None,
            "artifact_manifest_present": False,
            "artifact_ids": [],
            "artifact_download_ref_sources": [],
            "success": False,
        }
    events = remote_record.get("events") if isinstance(remote_record.get("events"), list) else []
    output_chunks = remote_record.get("output_chunks") if isinstance(remote_record.get("output_chunks"), list) else []
    artifact_manifest = remote_record.get("artifact_manifest") if isinstance(remote_record.get("artifact_manifest"), dict) else None
    artifact_items = artifact_manifest.get("artifacts") if isinstance(artifact_manifest, dict) and isinstance(artifact_manifest.get("artifacts"), list) else []
    output_chunk_seqs = [int(item.get("seq")) for item in output_chunks if isinstance(item, dict) and isinstance(item.get("seq"), int)]
    output_stream_ids = sorted(
        {
            str(item.get("stream_id") or "").strip()
            for item in output_chunks
            if isinstance(item, dict) and str(item.get("stream_id") or "").strip()
        }
    )
    event_types = [
        str(item.get("event_type") or "").strip()
        for item in events
        if isinstance(item, dict) and str(item.get("event_type") or "").strip()
    ]
    artifact_ids = [
        str(item.get("artifact_id") or "").strip()
        for item in artifact_items
        if isinstance(item, dict) and str(item.get("artifact_id") or "").strip()
    ]
    artifact_download_ref_sources = [
        str(item.get("download_ref_source") or "").strip() or None
        for item in artifact_items
        if isinstance(item, dict)
    ]
    output_chunk_seqs_sorted = sorted(output_chunk_seqs)
    return {
        "record_present": True,
        "ack_status": str(remote_record.get("ack_status") or "").strip() or None,
        "event_types": event_types,
        "resume_after_seq_matches": output_chunk_seqs_sorted[:1] == [expected_after_seq],
        "output_chunk_seqs": output_chunk_seqs_sorted,
        "output_stream_ids": output_stream_ids,
        "result_final_status": (
            str((remote_record.get("result") or {}).get("final_status") or "").strip()
            if isinstance(remote_record.get("result"), dict)
            else None
        ),
        "artifact_manifest_present": artifact_manifest is not None,
        "artifact_ids": artifact_ids,
        "artifact_download_ref_sources": artifact_download_ref_sources,
        "success": (
            str(remote_record.get("ack_status") or "").strip() == "accepted"
            and event_types == ["accepted", "running", "done"]
            and output_chunk_seqs_sorted == [1, 2, 3]
            and output_stream_ids == [expected_stream_id]
            and isinstance(remote_record.get("result"), dict)
            and str((remote_record.get("result") or {}).get("final_status") or "").strip() == "done"
            and artifact_ids == expected_artifact_ids
            and all(artifact_download_ref_sources)
        ),
    }


def _absolute_artifact_url(file_surface_url: str, relative_url: str) -> str:
    parsed = urlsplit(file_surface_url)
    return urlunsplit((parsed.scheme, parsed.netloc, str(relative_url or "").strip(), "", ""))


def _effective_execution() -> dict[str, Any]:
    return {
        "backend": "codex",
        "profile": "default",
        "permission": "default",
        "backend_transport": "sdk",
        "resolved_model": None,
    }


def _build_probe_result(*, thread_id: str, task_id: str, started_at: str, finished_at: str) -> RunResult:
    return RunResult(
        task_id=task_id,
        thread_id=thread_id,
        backend=BACKEND_CODEX,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at=started_at,
        finished_at=finished_at,
        stdout_file=f"runs/{task_id}/stdout.log",
        stderr_file=f"runs/{task_id}/stderr.log",
        summary_file=f"runs/{task_id}/summary.md",
        artifacts_dir=f"runs/{task_id}/artifacts",
        changed_files=[],
        tests_passed=True,
        backend_transport="sdk",
    )


def prepare_probe_roundtrip_evidence(
    *,
    output_dir: Path,
    run_name: str,
    relay_url: str,
    transport_token: str,
    verify_tls: bool,
    ca_file: str | None,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    slug = slug_text(run_name)[-24:]
    task_root = output_dir / run_name / "probe_task_root"
    thread_id = f"thread_live_pc_control_roundtrip_{slug}"
    task_id = f"task_live_pc_control_roundtrip_{slug}"
    run_dir = task_root / thread_id / "runs" / task_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "stdout.log").write_text("LIVE_PC_CONTROL_ROUNDTRIP_STDOUT\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")
    (run_dir / "summary.md").write_text("LIVE_PC_CONTROL_REPLAY_ARTIFACT_OK\n", encoding="utf-8")

    result = _build_probe_result(
        thread_id=thread_id,
        task_id=task_id,
        started_at=started_at,
        finished_at=finished_at,
    )
    stream_path = stream_events_path(task_root, thread_id, task_id)
    write_stream_events(
        stream_path,
        [
            StreamEvent(
                ts=started_at,
                seq=1,
                thread_id=thread_id,
                task_id=task_id,
                backend="codex",
                backend_transport="sdk",
                kind="turn.started",
                text="Replay probe started",
                status="running",
            ),
            StreamEvent(
                ts=finished_at,
                seq=2,
                thread_id=thread_id,
                task_id=task_id,
                backend="codex",
                backend_transport="sdk",
                kind="assistant.delta",
                text="LIVE_PC_CONTROL_REPLAY_ARTIFACT_OK",
                delta="LIVE_PC_CONTROL_REPLAY_ARTIFACT_OK",
                item_type="agent_message",
                status="completed",
            ),
            StreamEvent(
                ts=finished_at,
                seq=3,
                thread_id=thread_id,
                task_id=task_id,
                backend="codex",
                backend_transport="sdk",
                kind="turn.completed",
                text="Turn completed",
                status="completed",
            ),
        ],
    )

    artifact_path = artifacts_dir / "live_probe_report.txt"
    artifact_path.write_text("LIVE_PC_CONTROL_ARTIFACT_OK\n", encoding="utf-8")
    artifact = RunArtifact(
        artifact_id="artifact-live-probe-report",
        path=str(artifact_path),
        name="live_probe_report.txt",
        kind="file",
        content_type="text/plain",
        source="manifest",
    )
    index_path = write_artifact_index(task_root, result, [artifact], [])
    if index_path is None:
        raise RuntimeError("write_artifact_index did not produce an index")

    file_surface_url = derive_file_surface_url(relay_url)
    upload = upload_artifact_to_file_surface(
        task_root,
        result,
        artifact,
        file_surface_url=file_surface_url,
        transport_token=transport_token,
        role="artifact_delivery",
        timeout_seconds=15,
        verify_tls=verify_tls,
        ca_file=ca_file,
        trace_id=f"trace:pc-control-live-roundtrip:{run_name}",
        probe_id=run_name,
    )
    if not upload.success or not isinstance(upload.descriptor, dict):
        raise RuntimeError(
            f"file_surface upload failed: {upload.error_code or 'upload_failed'} {upload.error_message or ''}".strip()
        )
    artifact_descriptor = upload.descriptor.get("artifact")
    if not isinstance(artifact_descriptor, dict):
        raise RuntimeError("file_surface upload descriptor missing artifact payload")

    download_url = _absolute_artifact_url(file_surface_url, str(artifact_descriptor.get("download_url") or "").strip())
    write_external_delivery_index(
        task_root,
        result,
        artifacts=[artifact],
        deliveries=[
            ExternalDelivery(
                artifact_id=artifact.artifact_id,
                name=artifact.name,
                provider="file_surface",
                url=download_url,
                expires_at=finished_at,
                object_key=str(artifact_descriptor.get("file_id") or "").strip(),
                size_bytes=artifact_path.stat().st_size,
                content_type=artifact.content_type,
                bucket="relay-file-surface",
                path=str(artifact_path),
            )
        ],
        recorded_at=finished_at,
    )

    output_chunks = project_output_chunks(task_root, thread_id=thread_id, task_id=task_id)
    artifact_manifest = project_artifact_manifest(task_root, result=result)
    if artifact_manifest is None:
        raise RuntimeError("project_artifact_manifest did not produce a manifest")
    return {
        "task_root": str(task_root),
        "thread_id": thread_id,
        "task_id": task_id,
        "stream_id": derive_stream_id(thread_id, task_id),
        "result": result,
        "output_chunks": output_chunks,
        "artifact_manifest": artifact_manifest,
        "artifact_ids": [artifact.artifact_id],
        "file_surface_upload": upload.descriptor,
        "index_path": str(index_path),
        "stream_path": str(stream_path),
    }


async def _receive_server_message(websocket, *, timeout_seconds: int):
    raw = json.loads(await asyncio.wait_for(websocket.recv(), timeout=max(1, int(timeout_seconds))))
    parsed = parse_pc_control_server_message(raw)
    if isinstance(parsed, PcErrorMessage):
        raise RuntimeError(f"{parsed.payload['code']}: {parsed.payload['message']}")
    return parsed


async def _receive_until(
    websocket,
    *,
    timeout_seconds: int,
    predicate,
) -> list[Any]:
    collected: list[Any] = []
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        raw = json.loads(await asyncio.wait_for(websocket.recv(), timeout=remaining))
        parsed = parse_pc_control_server_message(raw)
        if isinstance(parsed, PcErrorMessage):
            raise RuntimeError(f"{parsed.payload['code']}: {parsed.payload['message']}")
        collected.append(parsed)
        if predicate(collected):
            return collected
    raise RuntimeError("Timed out while waiting for expected pc-control server messages")


def _serialize_message(message: Any) -> dict[str, Any]:
    return asdict(message)


def _build_structured_payload(*, task_id: str, thread_id: str, result: RunResult) -> dict[str, Any]:
    return {
        "kind": "run_result",
        "task_id": task_id,
        "thread_id": thread_id,
        "run_status": result.status,
        "stdout_file": result.stdout_file,
        "stderr_file": result.stderr_file,
        "summary_file": result.summary_file,
        "artifacts_dir": result.artifacts_dir,
        "changed_files": [],
        "tests_passed": True,
        "backend_session_id": None,
        "backend_session_resumable": False,
        "thread_status": "done",
    }


def _find_resume_request(messages: list[Any]) -> PcOutputResumeRequestMessage | None:
    for message in messages:
        if isinstance(message, PcOutputResumeRequestMessage):
            return message
    return None


def _find_hello_ack(messages: list[Any]) -> PcHelloAckMessage | None:
    for message in messages:
        if isinstance(message, PcHelloAckMessage):
            return message
    return None


def _poll_remote_command_record(
    *,
    remote_host: str,
    remote_user: str,
    remote_key_path: Path,
    remote_state_dir: str,
    pc_id: str,
    command_id: str,
    timeout_seconds: int = 20,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    last_payload: dict[str, Any] | None = None
    last_record: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        payload = fetch_remote_json(
            host=remote_host,
            user=remote_user,
            key_path=remote_key_path,
            remote_path=f"{remote_state_dir.rstrip('/')}/commands.json",
        )
        record = extract_remote_command_record(payload, pc_id=pc_id, command_id=command_id)
        last_payload = payload
        last_record = record
        if (
            isinstance(record, dict)
            and isinstance(record.get("result"), dict)
            and isinstance(record.get("artifact_manifest"), dict)
            and isinstance(record.get("output_chunks"), list)
            and len(record.get("output_chunks") or []) >= 3
        ):
            return payload, record
        time.sleep(1.0)
    return last_payload, last_record


async def async_run_pc_control_live_roundtrip_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path,
    remote_user: str = DEFAULT_REMOTE_USER,
    remote_key_path: str | Path | None = DEFAULT_REMOTE_KEY_PATH,
    remote_state_dir: str = DEFAULT_REMOTE_STATE_DIR,
    probe_pc_id: str | None = None,
) -> dict[str, Any]:
    resolved_config_path = Path(config_path).resolve()
    config = load_config(str(resolved_config_path))
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    configured_pc_id = str(config.relay_client_id or "").strip()
    client_version = str(config.relay_client_version or "").strip()
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("pc_control_live_roundtrip_smoke requires outbound_transport=relay")
    if not relay_url or not transport_token or not configured_pc_id or not client_version:
        raise ValueError("relay_url, relay_transport_token, relay_client_id, and relay_client_version are required")

    pc_id = str(probe_pc_id or "").strip() or f"{configured_pc_id}-live-roundtrip-{slug_text(run_name)[-12:]}"
    run_root = output_dir / run_name
    run_root.mkdir(parents=True, exist_ok=True)
    pc_control_url = derive_pc_control_url(relay_url)
    health_url = derive_healthz_url(relay_url)
    remote_host = urlsplit(health_url).hostname or ""
    resolved_remote_key_path = Path(remote_key_path).resolve() if remote_key_path is not None else None
    ssl_context = build_ssl_context(config)
    workspaces = collect_workspace_inventory(config)
    capabilities = build_execution_capabilities(config).to_payload()
    selected_workspace = select_probe_workspace(workspaces, preferred_repo_path=PROJECT_ROOT)
    started_at = timestamp()
    finished_at = timestamp()
    probe_evidence = prepare_probe_roundtrip_evidence(
        output_dir=output_dir,
        run_name=run_name,
        relay_url=relay_url,
        transport_token=transport_token,
        verify_tls=bool(config.relay_verify_tls),
        ca_file=(str(config.relay_ca_file or "").strip() or None),
        started_at=started_at,
        finished_at=finished_at,
    )
    command_id = f"cmd_live_pc_control_roundtrip_{slug_text(run_name)[-16:]}"
    session_id = f"live_pc_control_roundtrip_{slug_text(run_name)[-16:]}"
    trace_id = f"trace:pc-control:operator-dispatch:{pc_id}:{command_id}"

    async with websockets.connect(
        pc_control_url,
        ssl=ssl_context,
        open_timeout=max(1, int(config.relay_timeout_seconds)),
        close_timeout=max(1, int(config.relay_timeout_seconds)),
        extra_headers={"Authorization": f"Bearer {transport_token}"},
        max_size=4 * 1024 * 1024,
        **direct_websocket_connect_kwargs(),
    ) as websocket_one:
        await websocket_one.send(
            json.dumps(
                build_pc_hello(
                    message_id=f"pc_hello:{run_name}:1",
                    trace_id=f"trace:pc-control-live-roundtrip:{run_name}:hello1",
                    pc_id=pc_id,
                    sent_at=timestamp(),
                    display_name=pc_id,
                    client_version=client_version,
                    host_fingerprint=f"live-roundtrip-host:{run_name}",
                    runtime_fingerprint=f"live-roundtrip-runtime:{run_name}:1",
                    capabilities=capabilities,
                ),
                ensure_ascii=False,
            )
        )
        hello_ack_one = await _receive_server_message(websocket_one, timeout_seconds=config.relay_timeout_seconds)
        if not isinstance(hello_ack_one, PcHelloAckMessage):
            raise RuntimeError("expected hello_ack on first connection")
        first_epoch = hello_ack_one.connection_epoch

        await websocket_one.send(
            json.dumps(
                build_workspace_snapshot(
                    message_id=f"workspace_snapshot:{run_name}:1",
                    trace_id=f"trace:pc-control-live-roundtrip:{run_name}:snapshot1",
                    pc_id=pc_id,
                    connection_epoch=first_epoch,
                    sent_at=timestamp(),
                    snapshot_id=f"snapshot:{run_name}:1",
                    workspaces=workspaces,
                ),
                ensure_ascii=False,
            )
        )
        await asyncio.sleep(1.0)
        operator_response = await asyncio.to_thread(
            enqueue_pc_control_operator_dispatch,
            config=config,
            request_payload=build_operator_dispatch_request_payload(
                pc_id=pc_id,
                workspace_id=str(selected_workspace.get("workspace_id") or "").strip(),
                command_type="new_task",
                session_id=session_id,
                command_id=command_id,
                execution_policy={
                    "backend": "codex",
                    "profile": "default",
                    "permission": "default",
                    "backend_transport": "sdk",
                },
                command_payload={
                    "task_text": "Live pc-control replay/artifact probe. The operator will drive ack/event/output/result manually.",
                    "timeout_minutes": 10,
                    "mode": "analysis_only",
                },
            ),
            timeout_seconds=config.relay_timeout_seconds,
        )

        await websocket_one.send(
            json.dumps(
                build_heartbeat(
                    message_id=f"heartbeat:{run_name}:dispatch-trigger",
                    trace_id=f"trace:pc-control-live-roundtrip:{run_name}:dispatch-trigger",
                    pc_id=pc_id,
                    connection_epoch=first_epoch,
                    sent_at=timestamp(),
                    active_run_count=0,
                    workspace_count=len(workspaces),
                    load_hint="normal",
                ),
                ensure_ascii=False,
            )
        )
        dispatch_message = await _receive_server_message(websocket_one, timeout_seconds=config.relay_timeout_seconds)
        if not isinstance(dispatch_message, PcCommandDispatchMessage):
            raise RuntimeError(f"expected command_dispatch, got {type(dispatch_message).__name__}")
        if dispatch_message.payload["command_id"] != command_id:
            raise RuntimeError("received command_dispatch for an unexpected command_id")

        await websocket_one.send(
            json.dumps(
                build_command_ack(
                    message_id=f"command_ack:{run_name}:1",
                    trace_id=dispatch_message.trace_id,
                    pc_id=pc_id,
                    connection_epoch=first_epoch,
                    sent_at=timestamp(),
                    command_id=command_id,
                    ack_status="accepted",
                ),
                ensure_ascii=False,
            )
        )
        await websocket_one.send(
            json.dumps(
                build_command_event(
                    message_id=f"event:{run_name}:accepted",
                    trace_id=dispatch_message.trace_id,
                    pc_id=pc_id,
                    connection_epoch=first_epoch,
                    sent_at=timestamp(),
                    event_id=f"event:{command_id}:accepted",
                    command_id=command_id,
                    event_type="accepted",
                    summary="probe accepted the manual live roundtrip dispatch",
                    effective_execution=_effective_execution(),
                    event_payload={},
                ),
                ensure_ascii=False,
            )
        )
        await websocket_one.send(
            json.dumps(
                build_command_event(
                    message_id=f"event:{run_name}:running",
                    trace_id=dispatch_message.trace_id,
                    pc_id=pc_id,
                    connection_epoch=first_epoch,
                    sent_at=timestamp(),
                    event_id=f"event:{command_id}:running",
                    command_id=command_id,
                    event_type="running",
                    summary="probe is running the manual live roundtrip sequence",
                    effective_execution=_effective_execution(),
                    event_payload={
                        "thread_id": probe_evidence["thread_id"],
                        "task_id": probe_evidence["task_id"],
                        "workspace_id": str(selected_workspace.get("workspace_id") or "").strip(),
                    },
                ),
                ensure_ascii=False,
            )
        )
        first_chunk = probe_evidence["output_chunks"][0]
        await websocket_one.send(
            json.dumps(
                build_output_chunk(
                    message_id=f"output_chunk:{run_name}:1",
                    trace_id=dispatch_message.trace_id,
                    pc_id=pc_id,
                    connection_epoch=first_epoch,
                    sent_at=timestamp(),
                    output_chunk_id=f"output:{command_id}:{first_chunk['stream_id']}:{first_chunk['seq']}",
                    command_id=command_id,
                    stream_id=str(first_chunk["stream_id"]),
                    stream_id_source=(str(first_chunk["stream_id_source"]) if first_chunk.get("stream_id_source") else None),
                    seq=int(first_chunk["seq"]),
                    kind=str(first_chunk["kind"]),
                    text=(str(first_chunk["text"]) if first_chunk.get("text") else None),
                    delta=(str(first_chunk["delta"]) if first_chunk.get("delta") else None),
                    item_type=(str(first_chunk["item_type"]) if first_chunk.get("item_type") else None),
                    status=(str(first_chunk["status"]) if first_chunk.get("status") else None),
                ),
                ensure_ascii=False,
            )
        )
        await websocket_one.close()

    async with websockets.connect(
        pc_control_url,
        ssl=ssl_context,
        open_timeout=max(1, int(config.relay_timeout_seconds)),
        close_timeout=max(1, int(config.relay_timeout_seconds)),
        extra_headers={"Authorization": f"Bearer {transport_token}"},
        max_size=4 * 1024 * 1024,
        **direct_websocket_connect_kwargs(),
    ) as websocket_two:
        await websocket_two.send(
            json.dumps(
                build_pc_hello(
                    message_id=f"pc_hello:{run_name}:2",
                    trace_id=f"trace:pc-control-live-roundtrip:{run_name}:hello2",
                    pc_id=pc_id,
                    sent_at=timestamp(),
                    display_name=pc_id,
                    client_version=client_version,
                    host_fingerprint=f"live-roundtrip-host:{run_name}",
                    runtime_fingerprint=f"live-roundtrip-runtime:{run_name}:2",
                    capabilities=capabilities,
                ),
                ensure_ascii=False,
            )
        )
        reconnect_messages = await _receive_until(
            websocket_two,
            timeout_seconds=config.relay_timeout_seconds,
            predicate=lambda items: (
                _find_hello_ack(items) is not None and _find_resume_request(items) is not None
            ),
        )
        hello_ack_two = _find_hello_ack(reconnect_messages)
        resume_request = _find_resume_request(reconnect_messages)
        if hello_ack_two is None or resume_request is None:
            raise RuntimeError("expected hello_ack and output_resume_request after reconnect")

        second_epoch = hello_ack_two.connection_epoch
        for chunk in probe_evidence["output_chunks"][1:]:
            await websocket_two.send(
                json.dumps(
                    build_output_chunk(
                        message_id=f"output_chunk:{run_name}:{chunk['seq']}",
                        trace_id=trace_id,
                        pc_id=pc_id,
                        connection_epoch=second_epoch,
                        sent_at=timestamp(),
                        output_chunk_id=f"output:{command_id}:{chunk['stream_id']}:{chunk['seq']}",
                        command_id=command_id,
                        stream_id=str(chunk["stream_id"]),
                        stream_id_source=(str(chunk["stream_id_source"]) if chunk.get("stream_id_source") else None),
                        seq=int(chunk["seq"]),
                        kind=str(chunk["kind"]),
                        text=(str(chunk["text"]) if chunk.get("text") else None),
                        delta=(str(chunk["delta"]) if chunk.get("delta") else None),
                        item_type=(str(chunk["item_type"]) if chunk.get("item_type") else None),
                        status=(str(chunk["status"]) if chunk.get("status") else None),
                    ),
                    ensure_ascii=False,
                )
            )

        await websocket_two.send(
            json.dumps(
                build_command_event(
                    message_id=f"event:{run_name}:done",
                    trace_id=trace_id,
                    pc_id=pc_id,
                    connection_epoch=second_epoch,
                    sent_at=timestamp(),
                    event_id=f"event:{command_id}:done",
                    command_id=command_id,
                    event_type="done",
                    summary="LIVE_PC_CONTROL_REPLAY_ARTIFACT_OK",
                    effective_execution=_effective_execution(),
                    event_payload={
                        "thread_id": probe_evidence["thread_id"],
                        "task_id": probe_evidence["task_id"],
                    },
                ),
                ensure_ascii=False,
            )
        )
        result: RunResult = probe_evidence["result"]
        await websocket_two.send(
            json.dumps(
                build_command_result(
                    message_id=f"result:{run_name}:done",
                    trace_id=trace_id,
                    pc_id=pc_id,
                    connection_epoch=second_epoch,
                    sent_at=timestamp(),
                    result_id=f"result:{command_id}",
                    command_id=command_id,
                    final_status="done",
                    summary="LIVE_PC_CONTROL_REPLAY_ARTIFACT_OK",
                    structured_payload=_build_structured_payload(
                        task_id=probe_evidence["task_id"],
                        thread_id=probe_evidence["thread_id"],
                        result=result,
                    ),
                    effective_execution=_effective_execution(),
                ),
                ensure_ascii=False,
            )
        )
        manifest = probe_evidence["artifact_manifest"]
        await websocket_two.send(
            json.dumps(
                build_artifact_manifest(
                    message_id=f"artifact_manifest:{run_name}:done",
                    trace_id=trace_id,
                    pc_id=pc_id,
                    connection_epoch=second_epoch,
                    sent_at=timestamp(),
                    manifest_id=f"artifact_manifest:{command_id}",
                    command_id=command_id,
                    artifacts=list(manifest["artifacts"]),
                    artifacts_root=(str(manifest["artifacts_root"]) if manifest.get("artifacts_root") else None),
                    source=(str(manifest["source"]) if manifest.get("source") else None),
                ),
                ensure_ascii=False,
            )
        )
        await asyncio.sleep(1.0)

    remote_commands_payload = None
    remote_command_record = None
    remote_state_fetch_error = None
    if remote_host and resolved_remote_key_path is not None and resolved_remote_key_path.exists():
        try:
            remote_commands_payload, remote_command_record = await asyncio.to_thread(
                _poll_remote_command_record,
                remote_host=remote_host,
                remote_user=remote_user,
                remote_key_path=resolved_remote_key_path,
                remote_state_dir=remote_state_dir,
                pc_id=pc_id,
                command_id=command_id,
            )
        except Exception as exc:
            remote_state_fetch_error = f"{type(exc).__name__}: {exc}"

    observation = evaluate_live_roundtrip_observation(
        remote_command_record,
        expected_stream_id=str(probe_evidence["stream_id"]),
        expected_artifact_ids=list(probe_evidence["artifact_ids"]),
        expected_after_seq=int(resume_request.payload["after_seq"]),
    )
    result_payload = {
        "run_name": run_name,
        "config_path": str(resolved_config_path),
        "relay_url": relay_url,
        "pc_control_url": pc_control_url,
        "health_url": health_url,
        "pc_id": pc_id,
        "workspace_id": str(selected_workspace.get("workspace_id") or "").strip(),
        "command_id": command_id,
        "session_id": session_id,
        "operator_response": operator_response,
        "hello_ack_one": _serialize_message(hello_ack_one),
        "command_dispatch": _serialize_message(dispatch_message),
        "hello_ack_two": _serialize_message(hello_ack_two),
        "output_resume_request": _serialize_message(resume_request),
        "reconnect_messages": [_serialize_message(item) for item in reconnect_messages],
        "probe_evidence": {
            "task_root": probe_evidence["task_root"],
            "thread_id": probe_evidence["thread_id"],
            "task_id": probe_evidence["task_id"],
            "stream_id": probe_evidence["stream_id"],
            "output_chunks": probe_evidence["output_chunks"],
            "artifact_manifest": probe_evidence["artifact_manifest"],
            "artifact_ids": probe_evidence["artifact_ids"],
            "file_surface_upload": probe_evidence["file_surface_upload"],
            "index_path": probe_evidence["index_path"],
            "stream_path": probe_evidence["stream_path"],
        },
        "remote_commands_payload": remote_commands_payload,
        "remote_command_record": remote_command_record,
        "remote_state_fetch_error": remote_state_fetch_error,
        "observation": observation,
        "success": (
            observation["success"]
            and int(resume_request.payload["after_seq"]) == 1
            and str(resume_request.payload.get("stream_id") or "").strip() == str(probe_evidence["stream_id"])
            and hello_ack_two.connection_epoch > first_epoch
        ),
    }
    write_json(run_root / "smoke_result.json", result_payload)
    return result_payload


def run_pc_control_live_roundtrip_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path,
    remote_user: str = DEFAULT_REMOTE_USER,
    remote_key_path: str | Path | None = DEFAULT_REMOTE_KEY_PATH,
    remote_state_dir: str = DEFAULT_REMOTE_STATE_DIR,
    probe_pc_id: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        async_run_pc_control_live_roundtrip_smoke(
            output_dir=output_dir,
            run_name=run_name,
            config_path=config_path,
            remote_user=remote_user,
            remote_key_path=remote_key_path,
            remote_state_dir=remote_state_dir,
            probe_pc_id=probe_pc_id,
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live replay/artifact pc-control smoke against a real relay host."
    )
    parser.add_argument("--config", required=True, help="Local mail-runner config path with relay_url and transport token.")
    parser.add_argument("--run-name", default="", help="Optional run name. Defaults to a timestamped slug.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT), help="Directory to store smoke outputs.")
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER, help="SSH user for remote relay state inspection.")
    parser.add_argument(
        "--remote-key-path",
        default=str(DEFAULT_REMOTE_KEY_PATH),
        help="SSH private key path for remote relay state inspection.",
    )
    parser.add_argument(
        "--remote-state-dir",
        default=DEFAULT_REMOTE_STATE_DIR,
        help="Remote relay pc_control state directory.",
    )
    parser.add_argument(
        "--pc-id",
        default="",
        help="Optional probe pc_id. Defaults to '<relay_client_id>-live-roundtrip-*' to avoid disturbing the resident sidecar.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    run_name = str(args.run_name or "").strip() or f"pc-control-live-roundtrip-smoke-{timestamp_slug()}"
    result = run_pc_control_live_roundtrip_smoke(
        output_dir=Path(args.output_dir),
        run_name=run_name,
        config_path=args.config,
        remote_user=args.remote_user,
        remote_key_path=args.remote_key_path,
        remote_state_dir=args.remote_state_dir,
        probe_pc_id=args.pc_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
