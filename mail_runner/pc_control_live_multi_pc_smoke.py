"""Live multi-PC routing smoke for the VPS-first pc-control plane."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import websockets

from .config import load_config
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
from .pc_workspace_inventory import build_execution_capabilities, collect_workspace_inventory
from .relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcErrorMessage,
    PcHelloAckMessage,
    build_command_ack,
    build_command_event,
    build_command_result,
    build_heartbeat,
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_server_message,
)

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


def build_probe_workspace(
    base_workspace: dict[str, Any],
    *,
    workspace_id: str,
    display_name: str,
) -> dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_display_name = str(display_name or "").strip()
    if not normalized_workspace_id:
        raise ValueError("workspace_id is required")
    if not normalized_display_name:
        raise ValueError("display_name is required")
    workspace = dict(base_workspace)
    workspace["workspace_id"] = normalized_workspace_id
    workspace["workspace_norm"] = normalized_workspace_id.lower()
    workspace["display_name"] = normalized_display_name
    return workspace


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


def extract_remote_workspace_record(
    workspaces_payload: dict[str, Any] | None,
    *,
    pc_id: str,
    workspace_id: str,
) -> dict[str, Any] | None:
    raw_items = workspaces_payload.get("workspaces") if isinstance(workspaces_payload, dict) else None
    if not isinstance(raw_items, list):
        return None
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("pc_id") or "").strip() != pc_id:
            continue
        if str(item.get("workspace_id") or "").strip() != workspace_id:
            continue
        return dict(item)
    return None


def evaluate_dispatch_route_observation(
    *,
    dispatch_message: dict[str, Any] | None,
    cross_message: dict[str, Any] | None,
    remote_record: dict[str, Any] | None,
    expected_pc_id: str,
    expected_workspace_id: str,
    expected_command_id: str,
) -> dict[str, Any]:
    received_command_id = None
    received_workspace_id = None
    received_connection_epoch = None
    received_pc_id = None
    if isinstance(dispatch_message, dict):
        payload = dispatch_message.get("payload")
        if isinstance(payload, dict):
            received_command_id = str(payload.get("command_id") or "").strip() or None
            received_workspace_id = str(payload.get("workspace_id") or "").strip() or None
        received_connection_epoch = (
            int(dispatch_message.get("connection_epoch"))
            if isinstance(dispatch_message.get("connection_epoch"), int)
            else None
        )
        received_pc_id = str(dispatch_message.get("pc_id") or "").strip() or None
    event_types = []
    remote_result_status = None
    remote_pc_id = None
    remote_workspace_id = None
    remote_ack_status = None
    if isinstance(remote_record, dict):
        remote_pc_id = str(remote_record.get("pc_id") or "").strip() or None
        remote_workspace_id = str(remote_record.get("workspace_id") or "").strip() or None
        remote_ack_status = str(remote_record.get("ack_status") or "").strip() or None
        if isinstance(remote_record.get("result"), dict):
            remote_result_status = str((remote_record.get("result") or {}).get("final_status") or "").strip() or None
        if isinstance(remote_record.get("events"), list):
            event_types = [
                str(item.get("event_type") or "").strip()
                for item in remote_record["events"]
                if isinstance(item, dict) and str(item.get("event_type") or "").strip()
            ]
    return {
        "dispatch_received": dispatch_message is not None,
        "received_pc_id": received_pc_id,
        "received_connection_epoch": received_connection_epoch,
        "received_command_id": received_command_id,
        "received_workspace_id": received_workspace_id,
        "cross_message_present": cross_message is not None,
        "cross_message_type": str(cross_message.get("type") or "").strip() or None
        if isinstance(cross_message, dict)
        else None,
        "remote_record_present": remote_record is not None,
        "remote_pc_id": remote_pc_id,
        "remote_workspace_id": remote_workspace_id,
        "remote_ack_status": remote_ack_status,
        "remote_event_types": event_types,
        "remote_result_status": remote_result_status,
        "success": (
            dispatch_message is not None
            and cross_message is None
            and received_pc_id == expected_pc_id
            and received_command_id == expected_command_id
            and received_workspace_id == expected_workspace_id
            and remote_record is not None
            and remote_pc_id == expected_pc_id
            and remote_workspace_id == expected_workspace_id
            and remote_ack_status == "accepted"
            and event_types == ["accepted", "running", "done"]
            and remote_result_status == "done"
        ),
    }


def evaluate_workspace_registration_observation(
    workspaces_payload: dict[str, Any] | None,
    *,
    expected_pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    observed_pairs: list[dict[str, Any]] = []
    for pc_id, workspace_id in expected_pairs:
        record = extract_remote_workspace_record(workspaces_payload, pc_id=pc_id, workspace_id=workspace_id)
        observed_pairs.append(
            {
                "pc_id": pc_id,
                "workspace_id": workspace_id,
                "present": record is not None,
                "repo_path": str(record.get("repo_path") or "").strip() if isinstance(record, dict) else None,
                "workdir": str(record.get("workdir") or "").strip() if isinstance(record, dict) else None,
            }
        )
    return {
        "pairs": observed_pairs,
        "success": all(item["present"] for item in observed_pairs),
    }


def _effective_execution() -> dict[str, Any]:
    return {
        "backend": "codex",
        "profile": "default",
        "permission": "default",
        "backend_transport": "sdk",
        "resolved_model": None,
    }


def _structured_payload(*, command_id: str, label: str) -> dict[str, Any]:
    return {
        "kind": "probe_result",
        "task_id": command_id,
        "thread_id": f"thread:{label}",
        "run_status": "success",
        "summary_file": None,
        "artifacts_dir": None,
        "changed_files": [],
        "tests_passed": True,
        "backend_session_id": None,
        "backend_session_resumable": False,
        "thread_status": "done",
    }


def _serialize_message(message: Any) -> dict[str, Any]:
    return asdict(message)


async def _receive_message(websocket, *, timeout_seconds: int) -> Any:
    raw = json.loads(await asyncio.wait_for(websocket.recv(), timeout=max(1, int(timeout_seconds))))
    return parse_pc_control_server_message(raw)


async def _receive_dispatch(websocket, *, timeout_seconds: int) -> PcCommandDispatchMessage:
    parsed = await _receive_message(websocket, timeout_seconds=timeout_seconds)
    if isinstance(parsed, PcCommandDispatchMessage):
        return parsed
    raise RuntimeError(f"expected command_dispatch, got {type(parsed).__name__}")


async def _receive_optional_message(websocket, *, timeout_seconds: int) -> dict[str, Any] | None:
    try:
        parsed = await _receive_message(websocket, timeout_seconds=timeout_seconds)
    except asyncio.TimeoutError:
        return None
    return _serialize_message(parsed)


async def _handshake_probe(
    websocket,
    *,
    run_name: str,
    label: str,
    pc_id: str,
    client_version: str,
    capabilities: dict[str, Any],
    workspace: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    hello = build_pc_hello(
        message_id=f"pc_hello:{run_name}:{label}",
        trace_id=f"trace:pc-control-live-multi:{run_name}:{label}:hello",
        pc_id=pc_id,
        sent_at=timestamp(),
        display_name=pc_id,
        client_version=client_version,
        host_fingerprint=f"live-multi-host:{run_name}:{label}",
        runtime_fingerprint=f"live-multi-runtime:{run_name}:{label}",
        capabilities=capabilities,
    )
    await websocket.send(json.dumps(hello, ensure_ascii=False))
    hello_ack = await _receive_message(websocket, timeout_seconds=timeout_seconds)
    if not isinstance(hello_ack, PcHelloAckMessage):
        raise RuntimeError(f"expected hello_ack for {label}, got {type(hello_ack).__name__}")
    snapshot = build_workspace_snapshot(
        message_id=f"workspace_snapshot:{run_name}:{label}",
        trace_id=f"trace:pc-control-live-multi:{run_name}:{label}:snapshot",
        pc_id=pc_id,
        connection_epoch=hello_ack.connection_epoch,
        sent_at=timestamp(),
        snapshot_id=f"snapshot:{run_name}:{label}",
        workspaces=[workspace],
    )
    await websocket.send(json.dumps(snapshot, ensure_ascii=False))
    return {
        "hello_ack": _serialize_message(hello_ack),
        "workspace": dict(workspace),
        "connection_epoch": hello_ack.connection_epoch,
    }


async def _send_heartbeat(
    websocket,
    *,
    run_name: str,
    label: str,
    pc_id: str,
    connection_epoch: int,
) -> None:
    heartbeat = build_heartbeat(
        message_id=f"heartbeat:{run_name}:{label}:{timestamp_slug()}",
        trace_id=f"trace:pc-control-live-multi:{run_name}:{label}:heartbeat",
        pc_id=pc_id,
        connection_epoch=connection_epoch,
        sent_at=timestamp(),
        active_run_count=0,
        workspace_count=1,
        load_hint="idle",
    )
    await websocket.send(json.dumps(heartbeat, ensure_ascii=False))


async def _settle_dispatched_command(
    websocket,
    *,
    run_name: str,
    label: str,
    pc_id: str,
    connection_epoch: int,
    dispatch_message: PcCommandDispatchMessage,
) -> None:
    command_id = dispatch_message.payload["command_id"]
    trace_id = dispatch_message.trace_id
    await websocket.send(
        json.dumps(
            build_command_ack(
                message_id=f"command_ack:{run_name}:{label}",
                trace_id=trace_id,
                pc_id=pc_id,
                connection_epoch=connection_epoch,
                sent_at=timestamp(),
                command_id=command_id,
                ack_status="accepted",
            ),
            ensure_ascii=False,
        )
    )
    for event_type, summary in (
        ("accepted", f"{label} accepted the multi-pc routing probe"),
        ("running", f"{label} is running the multi-pc routing probe"),
        ("done", f"MULTI_PC_ROUTING_{label.upper()}_OK"),
    ):
        await websocket.send(
            json.dumps(
                build_command_event(
                    message_id=f"event:{run_name}:{label}:{event_type}",
                    trace_id=trace_id,
                    pc_id=pc_id,
                    connection_epoch=connection_epoch,
                    sent_at=timestamp(),
                    event_id=f"event:{command_id}:{event_type}",
                    command_id=command_id,
                    event_type=event_type,
                    summary=summary,
                    effective_execution=_effective_execution(),
                    event_payload={},
                ),
                ensure_ascii=False,
            )
        )
    await websocket.send(
        json.dumps(
            build_command_result(
                message_id=f"result:{run_name}:{label}",
                trace_id=trace_id,
                pc_id=pc_id,
                connection_epoch=connection_epoch,
                sent_at=timestamp(),
                result_id=f"result:{command_id}",
                command_id=command_id,
                final_status="done",
                summary=f"MULTI_PC_ROUTING_{label.upper()}_OK",
                structured_payload=_structured_payload(command_id=command_id, label=label),
                effective_execution=_effective_execution(),
            ),
            ensure_ascii=False,
        )
    )


async def _dispatch_and_observe_route(
    *,
    config,
    run_name: str,
    label: str,
    target_pc_id: str,
    target_workspace_id: str,
    target_websocket,
    target_connection_epoch: int,
    other_websocket,
    other_pc_id: str,
    other_connection_epoch: int,
) -> dict[str, Any]:
    command_id = f"cmd_live_pc_control_multi_{slug_text(run_name)[-16:]}_{label}"
    session_id = f"session_live_pc_control_multi_{slug_text(run_name)[-16:]}_{label}"
    request_payload = build_operator_dispatch_request_payload(
        pc_id=target_pc_id,
        workspace_id=target_workspace_id,
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
            "task_text": f"Manual multi-PC routing probe for {label}. Probe-only dispatch; do not execute external work.",
            "timeout_minutes": 5,
            "mode": "analysis_only",
        },
    )
    operator_response = enqueue_pc_control_operator_dispatch(config=config, request_payload=request_payload)
    await _send_heartbeat(
        target_websocket,
        run_name=run_name,
        label=f"{label}:target",
        pc_id=target_pc_id,
        connection_epoch=target_connection_epoch,
    )
    dispatch_message = await _receive_dispatch(
        target_websocket,
        timeout_seconds=max(2, int(config.relay_timeout_seconds)),
    )
    await _send_heartbeat(
        other_websocket,
        run_name=run_name,
        label=f"{label}:other",
        pc_id=other_pc_id,
        connection_epoch=other_connection_epoch,
    )
    cross_message = await _receive_optional_message(
        other_websocket,
        timeout_seconds=2,
    )
    await _settle_dispatched_command(
        target_websocket,
        run_name=run_name,
        label=label,
        pc_id=target_pc_id,
        connection_epoch=target_connection_epoch,
        dispatch_message=dispatch_message,
    )
    return {
        "label": label,
        "command_id": command_id,
        "session_id": session_id,
        "operator_response": operator_response,
        "dispatch_message": _serialize_message(dispatch_message),
        "cross_message": cross_message,
    }


async def async_run_pc_control_live_multi_pc_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path,
    remote_user: str = DEFAULT_REMOTE_USER,
    remote_key_path: str | Path | None = DEFAULT_REMOTE_KEY_PATH,
    remote_state_dir: str = DEFAULT_REMOTE_STATE_DIR,
    fetch_remote_state: bool = True,
) -> dict[str, Any]:
    resolved_config_path = Path(config_path).resolve()
    config = load_config(str(resolved_config_path))
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    configured_pc_id = str(config.relay_client_id or "").strip()
    client_version = str(config.relay_client_version or "").strip()
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("pc_control_live_multi_pc_smoke requires outbound_transport=relay")
    if not relay_url or not transport_token or not configured_pc_id or not client_version:
        raise ValueError(
            "pc_control_live_multi_pc_smoke requires relay_url, relay_transport_token, relay_client_id, relay_client_version"
        )

    run_root = output_dir / run_name
    run_root.mkdir(parents=True, exist_ok=True)
    pc_control_url = derive_pc_control_url(relay_url)
    remote_host = urlsplit(relay_url).hostname or ""
    ssl_context = build_ssl_context(config)
    capabilities = build_execution_capabilities(config).to_payload()
    base_workspace = select_probe_workspace(collect_workspace_inventory(config), preferred_repo_path=PROJECT_ROOT)
    run_slug = slug_text(run_name)[-16:]
    probe_a_pc_id = f"{configured_pc_id}-live-multi-a-{run_slug}"
    probe_b_pc_id = f"{configured_pc_id}-live-multi-b-{run_slug}"
    workspace_a = build_probe_workspace(
        base_workspace,
        workspace_id=f"workspace_live_multi_a_{run_slug}",
        display_name=f"live-multi-a-{run_slug}",
    )
    workspace_b = build_probe_workspace(
        base_workspace,
        workspace_id=f"workspace_live_multi_b_{run_slug}",
        display_name=f"live-multi-b-{run_slug}",
    )

    connect_kwargs = {
        "ssl": ssl_context,
        "open_timeout": max(1, int(config.relay_timeout_seconds)),
        "close_timeout": max(1, int(config.relay_timeout_seconds)),
        "extra_headers": {"Authorization": f"Bearer {transport_token}"},
        "max_size": 4 * 1024 * 1024,
        **direct_websocket_connect_kwargs(),
    }

    remote_workspaces_payload = None
    remote_commands_payload = None
    remote_state_fetch_error = None
    resolved_remote_key_path = Path(remote_key_path).resolve() if remote_key_path is not None else None

    async with websockets.connect(pc_control_url, **connect_kwargs) as websocket_a:
        async with websockets.connect(pc_control_url, **connect_kwargs) as websocket_b:
            probe_a = await _handshake_probe(
                websocket_a,
                run_name=run_name,
                label="probe-a",
                pc_id=probe_a_pc_id,
                client_version=client_version,
                capabilities=capabilities,
                workspace=workspace_a,
                timeout_seconds=max(2, int(config.relay_timeout_seconds)),
            )
            probe_b = await _handshake_probe(
                websocket_b,
                run_name=run_name,
                label="probe-b",
                pc_id=probe_b_pc_id,
                client_version=client_version,
                capabilities=capabilities,
                workspace=workspace_b,
                timeout_seconds=max(2, int(config.relay_timeout_seconds)),
            )

            route_a = await _dispatch_and_observe_route(
                config=config,
                run_name=run_name,
                label="probe-a",
                target_pc_id=probe_a_pc_id,
                target_workspace_id=workspace_a["workspace_id"],
                target_websocket=websocket_a,
                target_connection_epoch=probe_a["connection_epoch"],
                other_websocket=websocket_b,
                other_pc_id=probe_b_pc_id,
                other_connection_epoch=probe_b["connection_epoch"],
            )
            route_b = await _dispatch_and_observe_route(
                config=config,
                run_name=run_name,
                label="probe-b",
                target_pc_id=probe_b_pc_id,
                target_workspace_id=workspace_b["workspace_id"],
                target_websocket=websocket_b,
                target_connection_epoch=probe_b["connection_epoch"],
                other_websocket=websocket_a,
                other_pc_id=probe_a_pc_id,
                other_connection_epoch=probe_a["connection_epoch"],
            )

            await asyncio.sleep(1.0)

            if fetch_remote_state and remote_host and resolved_remote_key_path is not None and resolved_remote_key_path.exists():
                try:
                    remote_workspaces_payload = fetch_remote_json(
                        host=remote_host,
                        user=remote_user,
                        key_path=resolved_remote_key_path,
                        remote_path=f"{remote_state_dir.rstrip('/')}/workspaces.json",
                    )
                    remote_commands_payload = fetch_remote_json(
                        host=remote_host,
                        user=remote_user,
                        key_path=resolved_remote_key_path,
                        remote_path=f"{remote_state_dir.rstrip('/')}/commands.json",
                    )
                except Exception as exc:  # pragma: no cover - live environment dependent
                    remote_state_fetch_error = str(exc)

            cleanup_snapshot_a = build_workspace_snapshot(
                message_id=f"workspace_snapshot:{run_name}:cleanup:a",
                trace_id=f"trace:pc-control-live-multi:{run_name}:cleanup:a",
                pc_id=probe_a_pc_id,
                connection_epoch=probe_a["connection_epoch"],
                sent_at=timestamp(),
                snapshot_id=f"snapshot:{run_name}:cleanup:a",
                workspaces=[],
            )
            cleanup_snapshot_b = build_workspace_snapshot(
                message_id=f"workspace_snapshot:{run_name}:cleanup:b",
                trace_id=f"trace:pc-control-live-multi:{run_name}:cleanup:b",
                pc_id=probe_b_pc_id,
                connection_epoch=probe_b["connection_epoch"],
                sent_at=timestamp(),
                snapshot_id=f"snapshot:{run_name}:cleanup:b",
                workspaces=[],
            )
            await websocket_a.send(json.dumps(cleanup_snapshot_a, ensure_ascii=False))
            await websocket_b.send(json.dumps(cleanup_snapshot_b, ensure_ascii=False))

    workspace_observation = evaluate_workspace_registration_observation(
        remote_workspaces_payload,
        expected_pairs=[
            (probe_a_pc_id, workspace_a["workspace_id"]),
            (probe_b_pc_id, workspace_b["workspace_id"]),
        ],
    )
    route_a_observation = evaluate_dispatch_route_observation(
        dispatch_message=route_a["dispatch_message"],
        cross_message=route_a["cross_message"],
        remote_record=extract_remote_command_record(
            remote_commands_payload,
            pc_id=probe_a_pc_id,
            command_id=route_a["command_id"],
        ),
        expected_pc_id=probe_a_pc_id,
        expected_workspace_id=workspace_a["workspace_id"],
        expected_command_id=route_a["command_id"],
    )
    route_b_observation = evaluate_dispatch_route_observation(
        dispatch_message=route_b["dispatch_message"],
        cross_message=route_b["cross_message"],
        remote_record=extract_remote_command_record(
            remote_commands_payload,
            pc_id=probe_b_pc_id,
            command_id=route_b["command_id"],
        ),
        expected_pc_id=probe_b_pc_id,
        expected_workspace_id=workspace_b["workspace_id"],
        expected_command_id=route_b["command_id"],
    )

    result = {
        "run_name": run_name,
        "config_path": str(resolved_config_path),
        "relay_url": relay_url,
        "pc_control_url": pc_control_url,
        "probe_a": {
            "pc_id": probe_a_pc_id,
            "workspace": workspace_a,
            "hello_ack": probe_a["hello_ack"],
        },
        "probe_b": {
            "pc_id": probe_b_pc_id,
            "workspace": workspace_b,
            "hello_ack": probe_b["hello_ack"],
        },
        "routes": {
            "probe_a": route_a,
            "probe_b": route_b,
        },
        "workspace_observation": workspace_observation,
        "route_observation": {
            "probe_a": route_a_observation,
            "probe_b": route_b_observation,
        },
        "remote_workspaces_payload": remote_workspaces_payload,
        "remote_commands_payload": remote_commands_payload,
        "remote_state_fetch_error": remote_state_fetch_error,
        "success": workspace_observation["success"] and route_a_observation["success"] and route_b_observation["success"],
    }
    write_json(run_root / "smoke_result.json", result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a live multi-PC routing smoke against the public relay /pc-control.")
    parser.add_argument("--config", required=True, help="mail-runner config path with relay settings")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT), help="directory to store smoke output")
    parser.add_argument("--run-name", default="", help="optional fixed run name")
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER, help="SSH user for relay-host state fetch")
    parser.add_argument(
        "--remote-key-path",
        default=str(DEFAULT_REMOTE_KEY_PATH),
        help="SSH key path for relay-host state fetch",
    )
    parser.add_argument(
        "--remote-state-dir",
        default=DEFAULT_REMOTE_STATE_DIR,
        help="relay-host pc-control state directory",
    )
    parser.add_argument(
        "--no-fetch-remote-state",
        action="store_true",
        help="skip SSH fetch of relay-host pc-control state files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    run_name = str(args.run_name or "").strip() or f"pc-control-live-multi-pc-smoke-{timestamp_slug()}"
    result = asyncio.run(
        async_run_pc_control_live_multi_pc_smoke(
            output_dir=output_dir,
            run_name=run_name,
            config_path=args.config,
            remote_user=args.remote_user,
            remote_key_path=args.remote_key_path or None,
            remote_state_dir=args.remote_state_dir,
            fetch_remote_state=not bool(args.no_fetch_remote_state),
        )
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
