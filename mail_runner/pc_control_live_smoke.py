"""Live smoke for the VPS-first PC control plane over a real relay host."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import websockets

from .config import load_config
from .outbound.relay_bootstrap import derive_healthz_url, probe_healthz
from .pc_control_plane_client import derive_pc_control_url
from .pc_control_live_support import (
    DEFAULT_REMOTE_KEY_PATH,
    DEFAULT_REMOTE_STATE_DIR,
    DEFAULT_REMOTE_USER,
    build_ssl_context,
    direct_websocket_connect_kwargs,
    fetch_remote_json,
    slug_text,
    timestamp,
    timestamp_slug,
    write_json,
)
from .pc_workspace_inventory import build_execution_capabilities, collect_workspace_inventory
from .relay_server.pc_control_protocol import (
    PcErrorMessage,
    PcHelloAckMessage,
    build_heartbeat,
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_server_message,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_pc_control_live_smoke"


def _health_counts(payload: dict[str, Any] | None) -> dict[str, int | None]:
    pc_control = payload.get("pc_control") if isinstance(payload, dict) else None
    if not isinstance(pc_control, dict):
        return {"node_count": None, "workspace_count": None, "command_count": None}
    return {
        "node_count": pc_control.get("node_count") if isinstance(pc_control.get("node_count"), int) else None,
        "workspace_count": (
            pc_control.get("workspace_count") if isinstance(pc_control.get("workspace_count"), int) else None
        ),
        "command_count": pc_control.get("command_count") if isinstance(pc_control.get("command_count"), int) else None,
    }


def evaluate_workspace_snapshot_observation(
    *,
    health_before_payload: dict[str, Any] | None,
    health_payload: dict[str, Any] | None,
    remote_nodes_payload: dict[str, Any] | None,
    remote_workspaces_payload: dict[str, Any] | None,
    pc_id: str,
    expected_workspace_count: int,
    snapshot_sent_at: str,
    expected_connection_epoch: int,
) -> dict[str, Any]:
    health_before = _health_counts(health_before_payload)
    health = _health_counts(health_payload)
    remote_node = None
    node_items = remote_nodes_payload.get("nodes") if isinstance(remote_nodes_payload, dict) else None
    if isinstance(node_items, list):
        for item in node_items:
            if isinstance(item, dict) and item.get("pc_id") == pc_id:
                remote_node = item
                break
    remote_workspaces = []
    workspace_items = remote_workspaces_payload.get("workspaces") if isinstance(remote_workspaces_payload, dict) else None
    if isinstance(workspace_items, list):
        remote_workspaces = [item for item in workspace_items if isinstance(item, dict) and item.get("pc_id") == pc_id]

    health_node_delta = None
    health_workspace_delta = None
    if isinstance(health["node_count"], int) and isinstance(health_before["node_count"], int):
        health_node_delta = health["node_count"] - health_before["node_count"]
    if isinstance(health["workspace_count"], int) and isinstance(health_before["workspace_count"], int):
        health_workspace_delta = health["workspace_count"] - health_before["workspace_count"]
    observed_workspace_count = len(remote_workspaces) if remote_workspaces else health_workspace_delta
    all_remote_workspace_updates_match = bool(remote_workspaces) and all(
        item.get("updated_at") == snapshot_sent_at for item in remote_workspaces
    )
    return {
        "health_counts": health,
        "health_before_counts": health_before,
        "health_node_delta": health_node_delta,
        "health_workspace_delta": health_workspace_delta,
        "remote_node_present": remote_node is not None,
        "remote_workspace_records": len(remote_workspaces),
        "remote_node_workspace_count_matches": bool(remote_node)
        and remote_node.get("workspace_count") == expected_workspace_count,
        "remote_node_updated_at_matches": bool(remote_node) and remote_node.get("updated_at") == snapshot_sent_at,
        "remote_node_epoch_matches": bool(remote_node)
        and remote_node.get("current_connection_epoch") == expected_connection_epoch,
        "remote_workspace_updated_at_matches": all_remote_workspace_updates_match,
        "observed_workspace_count": observed_workspace_count,
        "workspace_count_matches": observed_workspace_count == expected_workspace_count,
        "success": (
            (
                observed_workspace_count == expected_workspace_count
                or (
                    not remote_workspaces
                    and health_node_delta == 1
                    and health_workspace_delta == expected_workspace_count
                )
            )
            and (
                remote_node is None
                or (
                    remote_node.get("workspace_count") == expected_workspace_count
                    and remote_node.get("updated_at") == snapshot_sent_at
                    and remote_node.get("current_connection_epoch") == expected_connection_epoch
                )
            )
            and (not remote_workspaces or all_remote_workspace_updates_match)
        ),
    }


def evaluate_stale_epoch_error(message: PcErrorMessage | None) -> dict[str, Any]:
    if message is None:
        return {"received": False, "code": None, "message": None, "success": False}
    code = message.payload.get("code")
    return {
        "received": True,
        "code": code,
        "message": message.payload.get("message"),
        "success": code == "stale_connection_epoch",
    }


async def _receive_server_message(
    websocket,
    *,
    timeout_seconds: int,
) -> PcHelloAckMessage | PcErrorMessage:
    raw = json.loads(await asyncio.wait_for(websocket.recv(), timeout=max(1, int(timeout_seconds))))
    parsed = parse_pc_control_server_message(raw)
    if isinstance(parsed, (PcHelloAckMessage, PcErrorMessage)):
        return parsed
    raise RuntimeError(f"unexpected server message type: {type(parsed).__name__}")


async def async_run_pc_control_live_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path,
    remote_user: str = DEFAULT_REMOTE_USER,
    remote_key_path: str | Path | None = DEFAULT_REMOTE_KEY_PATH,
    remote_state_dir: str = DEFAULT_REMOTE_STATE_DIR,
    fetch_remote_state: bool = True,
    probe_pc_id: str | None = None,
) -> dict[str, Any]:
    resolved_config_path = Path(config_path).resolve()
    config = load_config(str(resolved_config_path))
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    configured_pc_id = str(config.relay_client_id or "").strip()
    pc_id = str(probe_pc_id or "").strip() or f"{configured_pc_id}-live-smoke-{slug_text(run_name)[-12:]}"
    client_version = str(config.relay_client_version or "").strip()
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("pc_control_live_smoke requires outbound_transport=relay")
    if not relay_url or not transport_token or not pc_id or not client_version:
        raise ValueError(
            "pc_control_live_smoke requires relay_url, relay_transport_token, relay_client_id, relay_client_version"
        )

    run_root = output_dir / run_name
    run_root.mkdir(parents=True, exist_ok=True)
    pc_control_url = derive_pc_control_url(relay_url)
    health_url = derive_healthz_url(relay_url)
    remote_host = urlsplit(health_url).hostname or ""
    ssl_context = build_ssl_context(config)
    workspaces = collect_workspace_inventory(config)
    capabilities = build_execution_capabilities(config).to_payload()
    health_before = probe_healthz(
        health_url,
        timeout_seconds=config.relay_timeout_seconds,
        verify_tls=config.relay_verify_tls,
        ca_file=config.relay_ca_file or None,
    ).to_dict()

    hello_one_sent_at = timestamp()
    snapshot_sent_at = timestamp()
    hello_two_sent_at = timestamp()
    stale_heartbeat_sent_at = timestamp()

    async with websockets.connect(
        pc_control_url,
        ssl=ssl_context,
        open_timeout=max(1, int(config.relay_timeout_seconds)),
        close_timeout=max(1, int(config.relay_timeout_seconds)),
        extra_headers={"Authorization": f"Bearer {transport_token}"},
        max_size=4 * 1024 * 1024,
        **direct_websocket_connect_kwargs(),
    ) as websocket_one:
        hello_one = build_pc_hello(
            message_id=f"pc_hello:{run_name}:1",
            trace_id=f"trace:pc_control_live_smoke:{run_name}:hello1",
            pc_id=pc_id,
            sent_at=hello_one_sent_at,
            display_name=pc_id,
            client_version=client_version,
            host_fingerprint=f"live-smoke-host:{run_name}",
            runtime_fingerprint=f"live-smoke-runtime:{run_name}",
            capabilities=capabilities,
        )
        await websocket_one.send(json.dumps(hello_one, ensure_ascii=False))
        hello_ack_one = await _receive_server_message(websocket_one, timeout_seconds=config.relay_timeout_seconds)
        if not isinstance(hello_ack_one, PcHelloAckMessage):
            raise RuntimeError(f"expected hello_ack, got {type(hello_ack_one).__name__}")
        first_epoch = hello_ack_one.connection_epoch

        snapshot = build_workspace_snapshot(
            message_id=f"workspace_snapshot:{run_name}:1",
            trace_id=f"trace:pc_control_live_smoke:{run_name}:snapshot",
            pc_id=pc_id,
            connection_epoch=first_epoch,
            sent_at=snapshot_sent_at,
            snapshot_id=f"snapshot:{run_name}",
            workspaces=workspaces,
        )
        await websocket_one.send(json.dumps(snapshot, ensure_ascii=False))
        await asyncio.sleep(1.0)

        health_after_snapshot = probe_healthz(
            health_url,
            timeout_seconds=config.relay_timeout_seconds,
            verify_tls=config.relay_verify_tls,
            ca_file=config.relay_ca_file or None,
        ).to_dict()

        remote_nodes_payload = None
        remote_workspaces_payload = None
        resolved_remote_key_path = Path(remote_key_path).resolve() if remote_key_path is not None else None
        remote_state_fetch_error = None
        if fetch_remote_state and remote_host and resolved_remote_key_path is not None and resolved_remote_key_path.exists():
            try:
                remote_nodes_payload = fetch_remote_json(
                    host=remote_host,
                    user=remote_user,
                    key_path=resolved_remote_key_path,
                    remote_path=f"{remote_state_dir.rstrip('/')}/pc_nodes.json",
                )
                remote_workspaces_payload = fetch_remote_json(
                    host=remote_host,
                    user=remote_user,
                    key_path=resolved_remote_key_path,
                    remote_path=f"{remote_state_dir.rstrip('/')}/workspaces.json",
                )
            except Exception as exc:
                remote_state_fetch_error = f"{type(exc).__name__}: {exc}"

        snapshot_observation = evaluate_workspace_snapshot_observation(
            health_before_payload=health_before.get("payload"),
            health_payload=health_after_snapshot.get("payload"),
            remote_nodes_payload=remote_nodes_payload,
            remote_workspaces_payload=remote_workspaces_payload,
            pc_id=pc_id,
            expected_workspace_count=len(workspaces),
            snapshot_sent_at=snapshot_sent_at,
            expected_connection_epoch=first_epoch,
        )

        async with websockets.connect(
            pc_control_url,
            ssl=ssl_context,
            open_timeout=max(1, int(config.relay_timeout_seconds)),
            close_timeout=max(1, int(config.relay_timeout_seconds)),
            extra_headers={"Authorization": f"Bearer {transport_token}"},
            max_size=4 * 1024 * 1024,
            **direct_websocket_connect_kwargs(),
        ) as websocket_two:
            hello_two = build_pc_hello(
                message_id=f"pc_hello:{run_name}:2",
                trace_id=f"trace:pc_control_live_smoke:{run_name}:hello2",
                pc_id=pc_id,
                sent_at=hello_two_sent_at,
                display_name=pc_id,
                client_version=client_version,
                host_fingerprint=f"live-smoke-host:{run_name}",
                runtime_fingerprint=f"live-smoke-runtime:{run_name}:reconnect",
                capabilities=capabilities,
            )
            await websocket_two.send(json.dumps(hello_two, ensure_ascii=False))
            hello_ack_two = await _receive_server_message(websocket_two, timeout_seconds=config.relay_timeout_seconds)
            if not isinstance(hello_ack_two, PcHelloAckMessage):
                raise RuntimeError(f"expected second hello_ack, got {type(hello_ack_two).__name__}")

            stale_heartbeat = build_heartbeat(
                message_id=f"heartbeat:{run_name}:stale",
                trace_id=f"trace:pc_control_live_smoke:{run_name}:stale_heartbeat",
                pc_id=pc_id,
                connection_epoch=first_epoch,
                sent_at=stale_heartbeat_sent_at,
                active_run_count=0,
                workspace_count=len(workspaces),
                load_hint="normal",
            )
            await websocket_one.send(json.dumps(stale_heartbeat, ensure_ascii=False))
            stale_response = await _receive_server_message(websocket_one, timeout_seconds=config.relay_timeout_seconds)
            stale_error = stale_response if isinstance(stale_response, PcErrorMessage) else None

    stale_epoch_observation = evaluate_stale_epoch_error(stale_error)
    result = {
        "run_name": run_name,
        "config_path": str(resolved_config_path),
        "relay_url": relay_url,
        "pc_control_url": pc_control_url,
        "health_url": health_url,
        "configured_pc_id": configured_pc_id,
        "pc_id": pc_id,
        "client_version": client_version,
        "workspace_count": len(workspaces),
        "workspace_ids": [item["workspace_id"] for item in workspaces],
        "health_before": health_before,
        "hello_ack_one": asdict(hello_ack_one),
        "snapshot_sent_at": snapshot_sent_at,
        "health_after_snapshot": health_after_snapshot,
        "remote_nodes_payload": remote_nodes_payload,
        "remote_workspaces_payload": remote_workspaces_payload,
        "remote_state_fetch_error": remote_state_fetch_error,
        "snapshot_observation": snapshot_observation,
        "hello_ack_two": asdict(hello_ack_two),
        "stale_epoch_observation": stale_epoch_observation,
        "success": (
            bool(health_before.get("ok"))
            and snapshot_observation["success"]
            and stale_epoch_observation["success"]
            and hello_ack_two.connection_epoch > first_epoch
        ),
    }
    write_json(run_root / "smoke_result.json", result)
    return result


def run_pc_control_live_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path,
    remote_user: str = DEFAULT_REMOTE_USER,
    remote_key_path: str | Path | None = DEFAULT_REMOTE_KEY_PATH,
    remote_state_dir: str = DEFAULT_REMOTE_STATE_DIR,
    fetch_remote_state: bool = True,
    probe_pc_id: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        async_run_pc_control_live_smoke(
            output_dir=output_dir,
            run_name=run_name,
            config_path=config_path,
            remote_user=remote_user,
            remote_key_path=remote_key_path,
            remote_state_dir=remote_state_dir,
            fetch_remote_state=fetch_remote_state,
            probe_pc_id=probe_pc_id,
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a live single-PC pc-control smoke against a real relay host.")
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
        "--skip-remote-state",
        action="store_true",
        help="Skip SSH-based remote state inspection and only rely on /healthz plus websocket evidence.",
    )
    parser.add_argument(
        "--pc-id",
        default="",
        help="Optional probe pc_id. Defaults to '<relay_client_id>-live-smoke' to avoid disturbing the resident sidecar.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    run_name = str(args.run_name or "").strip() or f"pc-control-live-smoke-{timestamp_slug()}"
    result = run_pc_control_live_smoke(
        output_dir=Path(args.output_dir),
        run_name=run_name,
        config_path=args.config,
        remote_user=args.remote_user,
        remote_key_path=None if args.skip_remote_state else args.remote_key_path,
        remote_state_dir=args.remote_state_dir,
        fetch_remote_state=not args.skip_remote_state,
        probe_pc_id=args.pc_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
