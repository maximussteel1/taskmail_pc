"""Operator-facing helper for querying live pc-control read-side state."""

from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from .config import AppConfig, load_config


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


def derive_pc_control_operator_commands_url(relay_url: str) -> str:
    return derive_relay_http_url(relay_url, "/debug/pc-control/commands")


def derive_pc_control_operator_lease_url(relay_url: str) -> str:
    return derive_relay_http_url(relay_url, "/debug/pc-control/lease")


def derive_pc_control_operator_ingress_url(relay_url: str) -> str:
    return derive_relay_http_url(relay_url, "/debug/pc-control/ingress")


def derive_pc_control_operator_terminal_outcome_url(relay_url: str) -> str:
    return derive_relay_http_url(relay_url, "/debug/pc-control/terminal-outcome")


def _build_direct_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def _relay_verify_arg(config: AppConfig) -> bool | str:
    ca_file = str(config.relay_ca_file or "").strip()
    if ca_file:
        return ca_file
    return bool(config.relay_verify_tls)


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_positive_int(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer when provided")
    return value


def _require_relay_operator_config(config: AppConfig) -> tuple[str, str]:
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("pc-control operator read-side requires outbound_transport=relay")
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    if not relay_url or not transport_token:
        raise ValueError("pc-control operator read-side requires relay_url and relay_transport_token")
    return relay_url, transport_token


def _http_get_json(
    *,
    url: str,
    bearer_token: str,
    query: dict[str, str] | None,
    verify: bool | str,
    timeout_seconds: int,
) -> dict[str, Any]:
    session = _build_direct_requests_session()
    try:
        response = session.get(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            params=(query or None),
            timeout=max(1, int(timeout_seconds)),
            verify=verify,
        )
    finally:
        session.close()
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(
            f"pc-control operator read-side failed: HTTP {response.status_code} {json.dumps(payload, ensure_ascii=False)}"
        )
    if not isinstance(payload, dict):
        raise RuntimeError("pc-control operator read-side response must be a JSON object")
    return payload


def build_pc_control_nodes_query_params(*, pc_id: str | None = None) -> dict[str, str]:
    normalized_pc_id = _optional_text(pc_id)
    return {} if normalized_pc_id is None else {"pc_id": normalized_pc_id}


def build_pc_control_workspaces_query_params(*, pc_id: str | None = None) -> dict[str, str]:
    normalized_pc_id = _optional_text(pc_id)
    return {} if normalized_pc_id is None else {"pc_id": normalized_pc_id}


def build_pc_control_commands_query_params(
    *,
    pc_id: str | None = None,
    command_id: str | None = None,
) -> dict[str, str]:
    normalized_pc_id = _optional_text(pc_id)
    normalized_command_id = _optional_text(command_id)
    if normalized_command_id is not None and normalized_pc_id is None:
        raise ValueError("pc_id is required when command_id is provided")
    params: dict[str, str] = {}
    if normalized_pc_id is not None:
        params["pc_id"] = normalized_pc_id
    if normalized_command_id is not None:
        params["command_id"] = normalized_command_id
    return params


def build_pc_control_lease_query_params(*, mailbox_key: str) -> dict[str, str]:
    return {"mailbox_key": _require_text(mailbox_key, "mailbox_key")}


def build_pc_control_ingress_query_params(
    *,
    ingress_id: str | None = None,
    mailbox_key: str | None = None,
    message_id: str | None = None,
    uid: int | None = None,
    uid_validity: int | None = None,
    folder: str = "INBOX",
) -> dict[str, str]:
    normalized_ingress_id = _optional_text(ingress_id)
    normalized_mailbox_key = _optional_text(mailbox_key)
    normalized_message_id = _optional_text(message_id)
    normalized_uid = _optional_positive_int(uid, "uid")
    normalized_uid_validity = _optional_positive_int(uid_validity, "uid_validity")
    normalized_folder = _require_text(folder, "folder")

    if normalized_ingress_id is not None:
        return {"ingress_id": normalized_ingress_id}
    if normalized_message_id is not None:
        if normalized_mailbox_key is None:
            raise ValueError("mailbox_key is required when message_id is provided")
        return {
            "mailbox_key": normalized_mailbox_key,
            "message_id": normalized_message_id,
        }
    if normalized_uid is not None:
        if normalized_mailbox_key is None:
            raise ValueError("mailbox_key is required when uid is provided")
        params = {
            "mailbox_key": normalized_mailbox_key,
            "uid": str(normalized_uid),
            "folder": normalized_folder,
        }
        if normalized_uid_validity is not None:
            params["uid_validity"] = str(normalized_uid_validity)
        return params
    raise ValueError("ingress lookup requires ingress_id or (mailbox_key + message_id) or (mailbox_key + uid)")


def build_pc_control_terminal_outcome_query_params(*, thread_id: str) -> dict[str, str]:
    return {"thread_id": _require_text(thread_id, "thread_id")}


def _fetch_pc_control_operator_view(
    *,
    config: AppConfig,
    url: str,
    query: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    _relay_url, transport_token = _require_relay_operator_config(config)
    return _http_get_json(
        url=url,
        bearer_token=transport_token,
        query=query,
        verify=_relay_verify_arg(config),
        timeout_seconds=(timeout_seconds if timeout_seconds is not None else config.relay_timeout_seconds),
    )


def fetch_pc_control_nodes(
    *,
    config: AppConfig,
    pc_id: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url, _transport_token = _require_relay_operator_config(config)
    return _fetch_pc_control_operator_view(
        config=config,
        url=derive_pc_control_operator_nodes_url(relay_url),
        query=build_pc_control_nodes_query_params(pc_id=pc_id),
        timeout_seconds=timeout_seconds,
    )


def fetch_pc_control_workspaces(
    *,
    config: AppConfig,
    pc_id: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url, _transport_token = _require_relay_operator_config(config)
    return _fetch_pc_control_operator_view(
        config=config,
        url=derive_pc_control_operator_workspaces_url(relay_url),
        query=build_pc_control_workspaces_query_params(pc_id=pc_id),
        timeout_seconds=timeout_seconds,
    )


def fetch_pc_control_commands(
    *,
    config: AppConfig,
    pc_id: str | None = None,
    command_id: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url, _transport_token = _require_relay_operator_config(config)
    return _fetch_pc_control_operator_view(
        config=config,
        url=derive_pc_control_operator_commands_url(relay_url),
        query=build_pc_control_commands_query_params(pc_id=pc_id, command_id=command_id),
        timeout_seconds=timeout_seconds,
    )


def fetch_pc_control_mailbox_lease(
    *,
    config: AppConfig,
    mailbox_key: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url, _transport_token = _require_relay_operator_config(config)
    return _fetch_pc_control_operator_view(
        config=config,
        url=derive_pc_control_operator_lease_url(relay_url),
        query=build_pc_control_lease_query_params(mailbox_key=mailbox_key),
        timeout_seconds=timeout_seconds,
    )


def fetch_pc_control_ingress(
    *,
    config: AppConfig,
    ingress_id: str | None = None,
    mailbox_key: str | None = None,
    message_id: str | None = None,
    uid: int | None = None,
    uid_validity: int | None = None,
    folder: str = "INBOX",
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url, _transport_token = _require_relay_operator_config(config)
    return _fetch_pc_control_operator_view(
        config=config,
        url=derive_pc_control_operator_ingress_url(relay_url),
        query=build_pc_control_ingress_query_params(
            ingress_id=ingress_id,
            mailbox_key=mailbox_key,
            message_id=message_id,
            uid=uid,
            uid_validity=uid_validity,
            folder=folder,
        ),
        timeout_seconds=timeout_seconds,
    )


def fetch_pc_control_terminal_outcome(
    *,
    config: AppConfig,
    thread_id: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url, _transport_token = _require_relay_operator_config(config)
    return _fetch_pc_control_operator_view(
        config=config,
        url=derive_pc_control_operator_terminal_outcome_url(relay_url),
        query=build_pc_control_terminal_outcome_query_params(thread_id=thread_id),
        timeout_seconds=timeout_seconds,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query operator-only pc-control read-side state from the relay.")
    parser.add_argument("--config", required=True, help="mail-runner config path with relay settings.")
    parser.add_argument("--timeout-seconds", type=int, default=0, help="Override request timeout.")
    subparsers = parser.add_subparsers(dest="view", required=True)

    nodes = subparsers.add_parser("nodes", help="List pc-control nodes or fetch one pc_id.")
    nodes.add_argument("--pc-id", default="", help="Optional pc_id filter.")

    workspaces = subparsers.add_parser("workspaces", help="List pc-control workspaces.")
    workspaces.add_argument("--pc-id", default="", help="Optional pc_id filter.")

    commands = subparsers.add_parser("commands", help="List commands or fetch one command detail.")
    commands.add_argument("--pc-id", default="", help="Optional pc_id filter.")
    commands.add_argument("--command-id", default="", help="Optional command_id detail lookup.")

    lease = subparsers.add_parser("lease", help="Fetch one mailbox lease view.")
    lease.add_argument("--mailbox-key", required=True, help="Mailbox lease key.")

    ingress = subparsers.add_parser("ingress", help="Fetch one ingress record by lookup selector.")
    ingress.add_argument("--ingress-id", default="", help="Direct ingress_id lookup.")
    ingress.add_argument("--mailbox-key", default="", help="Mailbox key for message-id or uid lookup.")
    ingress.add_argument("--message-id", default="", help="Lookup by canonical Message-Id.")
    ingress.add_argument("--uid", type=int, default=None, help="Lookup by UID.")
    ingress.add_argument("--uid-validity", type=int, default=None, help="Optional UIDVALIDITY for UID lookup.")
    ingress.add_argument("--folder", default="INBOX", help="Mailbox folder for UID lookup.")

    terminal_outcome = subparsers.add_parser("terminal-outcome", help="Fetch the latest terminal outcome for a thread.")
    terminal_outcome.add_argument("--thread-id", required=True, help="Thread id.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = load_config(str(config_path))
    timeout_seconds = args.timeout_seconds or None

    if args.view == "nodes":
        payload = fetch_pc_control_nodes(config=config, pc_id=args.pc_id or None, timeout_seconds=timeout_seconds)
    elif args.view == "workspaces":
        payload = fetch_pc_control_workspaces(config=config, pc_id=args.pc_id or None, timeout_seconds=timeout_seconds)
    elif args.view == "commands":
        payload = fetch_pc_control_commands(
            config=config,
            pc_id=args.pc_id or None,
            command_id=args.command_id or None,
            timeout_seconds=timeout_seconds,
        )
    elif args.view == "lease":
        payload = fetch_pc_control_mailbox_lease(
            config=config,
            mailbox_key=args.mailbox_key,
            timeout_seconds=timeout_seconds,
        )
    elif args.view == "ingress":
        payload = fetch_pc_control_ingress(
            config=config,
            ingress_id=args.ingress_id or None,
            mailbox_key=args.mailbox_key or None,
            message_id=args.message_id or None,
            uid=args.uid,
            uid_validity=args.uid_validity,
            folder=args.folder,
            timeout_seconds=timeout_seconds,
        )
    elif args.view == "terminal-outcome":
        payload = fetch_pc_control_terminal_outcome(
            config=config,
            thread_id=args.thread_id,
            timeout_seconds=timeout_seconds,
        )
    else:
        raise ValueError(f"unsupported view: {args.view}")

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
