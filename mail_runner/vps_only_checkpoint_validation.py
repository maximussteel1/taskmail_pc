"""Single-entry validation for the current vps_only checkpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.parse
from pathlib import Path
from typing import Any

import requests
import websockets

from .artifact_contract_smoke import run_artifact_contract_smoke
from .config import PROJECT_ROOT, load_config
from .external_delivery_window import build_external_delivery_window_report
from .outbound.relay_bootstrap import build_hello_payload, derive_healthz_url, probe_healthz
from .pc_control_live_support import build_ssl_context, direct_websocket_connect_kwargs, timestamp_slug, write_json
from .relay_server.protocol import RelayErrorMessage, RelayHelloAckMessage, RelayPacketAckMessage, parse_server_message

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_vps_only_checkpoint_validation"


def derive_relay_http_url(relay_url: str, path: str) -> str:
    normalized = str(relay_url or "").strip()
    if not normalized:
        raise ValueError("relay_url must be a non-empty string")
    parsed = urllib.parse.urlsplit(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError("relay_url must use ws:// or wss://")
    http_scheme = "https" if scheme == "wss" else "http"
    normalized_path = str(path or "").strip() or "/"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    return urllib.parse.urlunsplit((http_scheme, parsed.netloc, normalized_path, "", ""))


def derive_pc_control_operator_nodes_url(relay_url: str) -> str:
    return derive_relay_http_url(relay_url, "/debug/pc-control/nodes")


def derive_pc_control_operator_workspaces_url(relay_url: str) -> str:
    return derive_relay_http_url(relay_url, "/debug/pc-control/workspaces")


def _build_direct_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def _relay_verify_arg(config) -> bool | str:
    ca_file = str(config.relay_ca_file or "").strip()
    if ca_file:
        return ca_file
    return bool(config.relay_verify_tls)


def _canonical_direct_new_task_probe_packet() -> dict[str, object]:
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:new-task:req_vps_only_probe",
        "client_trace_id": "req_vps_only_probe",
        "task_run_packet": {
            "schema_version": "phase2-direct-outbound-contract-v1",
            "action": "new_task",
            "request_id": "req_vps_only_probe",
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-vps-only-probe",
            },
            "new_task": {
                "backend": "codex",
                "repo_path": "E:\\projects\\mail_based_task_manager",
                "workdir": ".",
                "task_text": "Validate that direct new_task is closed in vps_only.",
                "subject_title": "Validate vps_only direct new_task rejection",
                "timeout_minutes": 15,
                "mode": "analysis_only",
                "profile": "default",
                "permission": "default",
                "acceptance": [
                    "Return unsupported_action instead of accepting the packet.",
                ],
            },
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "phase2-direct-outbound-contract-v1",
            "action": "new_task",
            "fallback_policy": "mail",
        },
        "sent_at": "2026-03-27T00:00:00",
    }


def _http_get_json(
    *,
    url: str,
    bearer_token: str,
    verify: bool | str,
    timeout_seconds: int,
) -> dict[str, Any]:
    session = _build_direct_requests_session()
    try:
        response = session.get(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=max(1, int(timeout_seconds)),
            verify=verify,
        )
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "http_status": None,
            "payload": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    finally:
        session.close()

    payload: dict[str, Any] | None
    try:
        decoded = response.json()
        payload = decoded if isinstance(decoded, dict) else None
    except Exception:
        payload = None
    return {
        "ok": response.status_code == 200 and isinstance(payload, dict),
        "url": url,
        "http_status": response.status_code,
        "payload": payload,
        "error_type": None if response.status_code == 200 else "HTTPError",
        "error_message": None if response.status_code == 200 else f"HTTP {response.status_code}",
    }


def _build_pc_control_observation(
    *,
    nodes_result: dict[str, Any],
    workspaces_result: dict[str, Any],
    expected_pc_id: str | None,
) -> dict[str, Any]:
    nodes_payload = nodes_result.get("payload") if isinstance(nodes_result.get("payload"), dict) else {}
    workspaces_payload = workspaces_result.get("payload") if isinstance(workspaces_result.get("payload"), dict) else {}
    nodes = nodes_payload.get("nodes") if isinstance(nodes_payload.get("nodes"), list) else []
    workspaces = workspaces_payload.get("workspaces") if isinstance(workspaces_payload.get("workspaces"), list) else []

    normalized_expected_pc_id = str(expected_pc_id or "").strip() or None
    target_node = None
    if normalized_expected_pc_id is not None:
        for item in nodes:
            if isinstance(item, dict) and str(item.get("pc_id") or "").strip() == normalized_expected_pc_id:
                target_node = item
                break
    elif nodes:
        first = nodes[0]
        target_node = first if isinstance(first, dict) else None

    target_pc_id = (
        str(target_node.get("pc_id") or "").strip()
        if isinstance(target_node, dict)
        else normalized_expected_pc_id
    )
    target_workspaces = [
        item
        for item in workspaces
        if isinstance(item, dict) and (not target_pc_id or str(item.get("pc_id") or "").strip() == target_pc_id)
    ]
    observed_workspace_count = len(target_workspaces)
    if observed_workspace_count == 0 and isinstance(target_node, dict):
        workspace_count = target_node.get("workspace_count")
        if isinstance(workspace_count, int):
            observed_workspace_count = workspace_count

    target_pc_online = isinstance(target_node, dict) and str(target_node.get("status") or "").strip() == "online"
    current_connection_epoch = target_node.get("current_connection_epoch") if isinstance(target_node, dict) else None
    checks = {
        "nodes_http_ok": bool(nodes_result.get("ok")),
        "workspaces_http_ok": bool(workspaces_result.get("ok")),
        "target_pc_found": isinstance(target_node, dict),
        "target_pc_online": target_pc_online,
        "current_connection_epoch_positive": isinstance(current_connection_epoch, int) and current_connection_epoch > 0,
        "workspace_count_nonzero": observed_workspace_count > 0,
    }
    return {
        "target_pc_id": target_pc_id,
        "node_count": len([item for item in nodes if isinstance(item, dict)]),
        "workspace_record_count": len(target_workspaces),
        "observed_workspace_count": observed_workspace_count,
        "target_node": target_node,
        "checks": checks,
        "success": all(checks.values()),
        "nodes_result": nodes_result,
        "workspaces_result": workspaces_result,
    }


async def _async_probe_direct_new_task_disabled(*, config) -> dict[str, Any]:
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    if not relay_url or not transport_token:
        return {
            "success": False,
            "relay_url": relay_url,
            "error_type": "ValueError",
            "error_message": "relay_url and relay_transport_token are required",
        }

    ssl_context = build_ssl_context(config)
    try:
        async with websockets.connect(
            relay_url,
            ssl=ssl_context,
            open_timeout=max(1, int(config.relay_timeout_seconds)),
            close_timeout=max(1, int(config.relay_timeout_seconds)),
            extra_headers={"Authorization": f"Bearer {transport_token}"},
            max_size=4 * 1024 * 1024,
            **direct_websocket_connect_kwargs(),
        ) as websocket:
            await websocket.send(
                json.dumps(
                    build_hello_payload(
                        client_id="vps-only-validator",
                        client_version="0.1.0",
                        transport_token=transport_token,
                    ),
                    ensure_ascii=False,
                )
            )
            hello_response = parse_server_message(json.loads(await websocket.recv()))
            if not isinstance(hello_response, RelayHelloAckMessage):
                return {
                    "success": False,
                    "relay_url": relay_url,
                    "handshake_ok": False,
                    "response_type": type(hello_response).__name__,
                    "error_type": "ProtocolError",
                    "error_message": "expected hello_ack before direct probe",
                }

            await websocket.send(json.dumps(_canonical_direct_new_task_probe_packet(), ensure_ascii=False))
            response = parse_server_message(json.loads(await websocket.recv()))
            if isinstance(response, RelayErrorMessage):
                return {
                    "success": response.code == "unsupported_action",
                    "relay_url": relay_url,
                    "handshake_ok": True,
                    "connection_id": hello_response.connection_id,
                    "response_type": "error",
                    "error_code": response.code,
                    "error_message": response.message,
                }
            if isinstance(response, RelayPacketAckMessage):
                return {
                    "success": False,
                    "relay_url": relay_url,
                    "handshake_ok": True,
                    "connection_id": hello_response.connection_id,
                    "response_type": "packet_ack",
                    "packet_id": response.packet_id,
                    "receipt_id": response.receipt_id,
                    "error_message": "direct new_task was unexpectedly accepted",
                }
            return {
                "success": False,
                "relay_url": relay_url,
                "handshake_ok": True,
                "connection_id": hello_response.connection_id,
                "response_type": type(response).__name__,
                "error_type": "ProtocolError",
                "error_message": "unexpected relay response type for direct probe",
            }
    except Exception as exc:
        return {
            "success": False,
            "relay_url": relay_url,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def probe_direct_new_task_disabled(*, config) -> dict[str, Any]:
    return asyncio.run(_async_probe_direct_new_task_disabled(config=config))


def run_vps_only_checkpoint_validation(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path,
    expected_pc_id: str | None = None,
    window_limit_runs: int = 20,
) -> dict[str, Any]:
    resolved_config_path = Path(config_path).resolve()
    config = load_config(str(resolved_config_path))
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("vps_only checkpoint validation requires outbound_transport=relay")
    if not str(config.relay_url or "").strip():
        raise ValueError("vps_only checkpoint validation requires relay_url")
    if not str(config.relay_transport_token or "").strip():
        raise ValueError("vps_only checkpoint validation requires relay_transport_token")

    run_root = output_dir / run_name
    run_root.mkdir(parents=True, exist_ok=True)
    config_base_dir = resolved_config_path.parent
    task_root = config.resolve_task_root(config_base_dir)
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    verify_arg = _relay_verify_arg(config)

    health_result = probe_healthz(
        derive_healthz_url(relay_url),
        timeout_seconds=config.relay_timeout_seconds,
        verify_tls=config.relay_verify_tls,
        ca_file=config.relay_ca_file or None,
    ).to_dict()
    health_payload = health_result.get("payload") if isinstance(health_result.get("payload"), dict) else {}
    health_checks = {
        "status_ok": health_payload.get("status") == "ok",
        "direct_ingress_disabled": health_payload.get("taskmail_direct_ingress_enabled") is False,
        "scheduler_present": (
            isinstance(health_payload.get("task_root"), dict)
            and health_payload["task_root"].get("scheduler_present") is True
        ),
    }
    health_observation = {
        "result": health_result,
        "checks": health_checks,
        "success": bool(health_result.get("ok")) and all(health_checks.values()),
    }

    nodes_result = _http_get_json(
        url=derive_pc_control_operator_nodes_url(relay_url),
        bearer_token=transport_token,
        verify=verify_arg,
        timeout_seconds=config.relay_timeout_seconds,
    )
    workspaces_result = _http_get_json(
        url=derive_pc_control_operator_workspaces_url(relay_url),
        bearer_token=transport_token,
        verify=verify_arg,
        timeout_seconds=config.relay_timeout_seconds,
    )
    pc_control_observation = _build_pc_control_observation(
        nodes_result=nodes_result,
        workspaces_result=workspaces_result,
        expected_pc_id=expected_pc_id or str(config.relay_client_id or "").strip() or None,
    )

    artifact_smoke = run_artifact_contract_smoke(
        output_dir=run_root / "artifact_contract_smoke",
        run_name=f"{run_name}-artifact-contract",
        config_path=resolved_config_path,
    )
    file_surface_observation = {
        "success": bool(artifact_smoke.get("success")),
        "smoke_result_path": artifact_smoke.get("smoke_result_path"),
        "delivery_notices": list(artifact_smoke.get("delivery_notices") or []),
        "live_relay_file_surface": dict(artifact_smoke.get("live_relay_file_surface") or {}),
        "failures": list(artifact_smoke.get("failures") or []),
    }

    window_report = build_external_delivery_window_report(
        task_root,
        limit_runs=window_limit_runs,
        owner_preference=config.external_delivery_backend_preference,
    )

    direct_probe = probe_direct_new_task_disabled(config=config)

    failures: list[str] = []
    if not health_observation["success"]:
        failures.append("healthz checks failed")
    if not pc_control_observation["success"]:
        failures.append("pc-control read-side checks failed")
    if not file_surface_observation["success"]:
        failures.append("relay /v1/files smoke failed")
    if window_report.get("window_ready") is not True:
        failures.append("external-delivery observation window is not clean yet")
    if not bool(direct_probe.get("success")):
        failures.append("old direct new_task did not reject with unsupported_action")

    result = {
        "run_name": run_name,
        "config_path": str(resolved_config_path),
        "task_root": str(task_root),
        "relay_url": relay_url,
        "healthz": health_observation,
        "pc_control": pc_control_observation,
        "file_surface": file_surface_observation,
        "window_report": window_report,
        "direct_new_task_probe": direct_probe,
        "success": len(failures) == 0,
        "failures": failures,
    }
    write_json(run_root / "validation_result.json", result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the current vps_only checkpoint validation bundle.")
    parser.add_argument("--config", required=True, help="Local mail-runner config path with relay settings.")
    parser.add_argument("--run-name", default="", help="Optional run name. Defaults to a timestamped slug.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT), help="Directory to store validation outputs.")
    parser.add_argument("--expected-pc-id", default="", help="Optional expected pc_id. Defaults to relay_client_id.")
    parser.add_argument(
        "--window-limit-runs",
        type=int,
        default=20,
        help="How many recent runs to include in the observation window.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    run_name = str(args.run_name or "").strip() or f"vps-only-checkpoint-validation-{timestamp_slug()}"
    result = run_vps_only_checkpoint_validation(
        output_dir=Path(args.output_dir),
        run_name=run_name,
        config_path=args.config,
        expected_pc_id=args.expected_pc_id or None,
        window_limit_runs=args.window_limit_runs,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
