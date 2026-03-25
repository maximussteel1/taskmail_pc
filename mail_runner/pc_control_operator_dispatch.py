"""Operator-facing helper for enqueueing a live pc-control dispatch."""

from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from .config import AppConfig, load_config


def derive_pc_control_operator_dispatch_url(relay_url: str) -> str:
    normalized = str(relay_url or "").strip()
    if not normalized:
        raise ValueError("relay_url must be a non-empty string")
    parsed = urllib.parse.urlsplit(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError("relay_url must use ws:// or wss://")
    http_scheme = "https" if scheme == "wss" else "http"
    return urllib.parse.urlunsplit((http_scheme, parsed.netloc, "/debug/pc-control/dispatch", "", ""))


def _build_direct_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def _parse_json_mapping(text: str, *, field_name: str) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        return {}
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must decode to a JSON object")
    return dict(payload)


def build_operator_dispatch_request_payload(
    *,
    pc_id: str,
    workspace_id: str,
    command_type: str,
    session_id: str | None = None,
    command_id: str | None = None,
    execution_policy: dict[str, Any] | None = None,
    command_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_pc_id = str(pc_id or "").strip()
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_command_type = str(command_type or "").strip()
    if not normalized_pc_id:
        raise ValueError("pc_id is required")
    if not normalized_workspace_id:
        raise ValueError("workspace_id is required")
    if not normalized_command_type:
        raise ValueError("command_type is required")
    payload = {
        "pc_id": normalized_pc_id,
        "workspace_id": normalized_workspace_id,
        "command_type": normalized_command_type,
        "execution_policy": dict(execution_policy or {}),
        "payload": dict(command_payload or {}),
    }
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id:
        payload["session_id"] = normalized_session_id
    normalized_command_id = str(command_id or "").strip()
    if normalized_command_id:
        payload["command_id"] = normalized_command_id
    return payload


def enqueue_pc_control_operator_dispatch(
    *,
    config: AppConfig,
    request_payload: dict[str, Any],
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("operator dispatch requires outbound_transport=relay")
    if not relay_url or not transport_token:
        raise ValueError("operator dispatch requires relay_url and relay_transport_token")
    url = derive_pc_control_operator_dispatch_url(relay_url)
    session = _build_direct_requests_session()
    try:
        response = session.post(
            url,
            headers={"Authorization": f"Bearer {transport_token}"},
            json=request_payload,
            timeout=max(1, int(timeout_seconds if timeout_seconds is not None else config.relay_timeout_seconds)),
            verify=(str(config.relay_ca_file or "").strip() or bool(config.relay_verify_tls)),
        )
    finally:
        session.close()
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"operator dispatch failed: HTTP {response.status_code} {json.dumps(payload, ensure_ascii=False)}")
    if not isinstance(payload, dict):
        raise RuntimeError("operator dispatch response must be a JSON object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit one operator-only pc-control dispatch to the relay.")
    parser.add_argument("--config", required=True, help="mail-runner config path with relay settings.")
    parser.add_argument("--pc-id", required=True, help="Target pc_id.")
    parser.add_argument("--workspace-id", required=True, help="Target workspace_id.")
    parser.add_argument("--command-type", required=True, help="Command type, for example status/reply/new_task.")
    parser.add_argument("--session-id", default="", help="Optional session/thread identity.")
    parser.add_argument("--command-id", default="", help="Optional fixed command_id.")
    parser.add_argument("--execution-policy-json", default="", help="Optional execution_policy JSON object.")
    parser.add_argument("--payload-json", default="", help="Optional command payload JSON object.")
    parser.add_argument("--timeout-seconds", type=int, default=0, help="Override request timeout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = load_config(str(config_path))
    request_payload = build_operator_dispatch_request_payload(
        pc_id=args.pc_id,
        workspace_id=args.workspace_id,
        command_type=args.command_type,
        session_id=args.session_id or None,
        command_id=args.command_id or None,
        execution_policy=_parse_json_mapping(args.execution_policy_json, field_name="execution_policy_json"),
        command_payload=_parse_json_mapping(args.payload_json, field_name="payload_json"),
    )
    payload = enqueue_pc_control_operator_dispatch(
        config=config,
        request_payload=request_payload,
        timeout_seconds=args.timeout_seconds or None,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
