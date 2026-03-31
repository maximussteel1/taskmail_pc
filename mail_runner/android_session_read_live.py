"""Fetch live Android read-side payloads from a relay host."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .android_relay_test_client import (
    DEFAULT_ANDROID_RELAY_BASE_URL,
    derive_android_relay_base_url_from_relay_url,
    get_android_relay_json,
    write_probe_output,
)
from .config import AppConfig, load_config

_ANDROID_APP_TOKEN_ENV = "MAIL_RELAY_ANDROID_APP_TOKEN"


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _relay_verify_arg(config: AppConfig) -> bool | str:
    ca_file = _optional_text(config.relay_ca_file)
    if ca_file:
        return ca_file
    return bool(config.relay_verify_tls)


def resolve_android_read_base_url(*, base_url: str | None = None, config_path: str | Path | None = None) -> str:
    normalized_base_url = _optional_text(base_url)
    if normalized_base_url is not None:
        return normalized_base_url
    if config_path is None:
        return DEFAULT_ANDROID_RELAY_BASE_URL
    config = load_config(str(Path(config_path).resolve()))
    relay_url = _optional_text(config.relay_url)
    if relay_url is None:
        raise ValueError("config relay_url is required when --base-url is omitted")
    return derive_android_relay_base_url_from_relay_url(relay_url)


def resolve_android_read_verify(
    *,
    insecure: bool = False,
    ca_file: str | None = None,
    config_path: str | Path | None = None,
) -> bool | str:
    normalized_ca_file = _optional_text(ca_file)
    if insecure and normalized_ca_file is not None:
        raise ValueError("--insecure and --ca-file cannot be used together")
    if insecure:
        return False
    if normalized_ca_file is not None:
        return normalized_ca_file
    if config_path is None:
        return True
    config = load_config(str(Path(config_path).resolve()))
    return _relay_verify_arg(config)


def resolve_android_read_timeout_seconds(
    *,
    timeout_seconds: int | None = None,
    config_path: str | Path | None = None,
) -> int:
    if timeout_seconds is not None and int(timeout_seconds) > 0:
        return int(timeout_seconds)
    if config_path is None:
        return 30
    config = load_config(str(Path(config_path).resolve()))
    return max(1, int(config.relay_timeout_seconds))


def resolve_android_app_token(android_app_token: str | None) -> str:
    explicit = _optional_text(android_app_token)
    if explicit is not None:
        return explicit
    from_env = _optional_text(os.getenv(_ANDROID_APP_TOKEN_ENV))
    if from_env is not None:
        return from_env
    raise ValueError(f"android app token is required via --android-app-token or {_ANDROID_APP_TOKEN_ENV}")


def build_android_sessions_query_params(
    *,
    pc_id: str | None = None,
    workspace_id: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    include_ended: bool = False,
) -> dict[str, str]:
    params: dict[str, str] = {}
    normalized_pc_id = _optional_text(pc_id)
    normalized_workspace_id = _optional_text(workspace_id)
    normalized_session_id = _optional_text(session_id)
    normalized_thread_id = _optional_text(thread_id)
    if normalized_pc_id is not None:
        params["pc_id"] = normalized_pc_id
    if normalized_workspace_id is not None:
        params["workspace_id"] = normalized_workspace_id
    if normalized_session_id is not None:
        params["session_id"] = normalized_session_id
    if normalized_thread_id is not None:
        params["thread_id"] = normalized_thread_id
    if include_ended:
        params["include_ended"] = "true"
    return params


def build_android_session_locator_query_params(
    *,
    workspace_id: str | None = None,
    repo_path: str | None = None,
    workdir: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, str]:
    normalized_session_id = _optional_text(session_id)
    normalized_thread_id = _optional_text(thread_id)
    if normalized_session_id is None and normalized_thread_id is None:
        raise ValueError("session-snapshot/session-history requires --session-id or --thread-id")

    params: dict[str, str] = {}
    normalized_workspace_id = _optional_text(workspace_id)
    normalized_repo_path = _optional_text(repo_path)
    normalized_workdir = _optional_text(workdir)
    if normalized_workspace_id is not None:
        params["workspace_id"] = normalized_workspace_id
    if normalized_repo_path is not None:
        params["repo_path"] = normalized_repo_path
    if normalized_workdir is not None:
        params["workdir"] = normalized_workdir
    if normalized_session_id is not None:
        params["session_id"] = normalized_session_id
    if normalized_thread_id is not None:
        params["thread_id"] = normalized_thread_id
    return params


def fetch_android_sessions(
    *,
    base_url: str,
    android_app_token: str,
    pc_id: str | None = None,
    workspace_id: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    include_ended: bool = False,
    timeout_seconds: int = 30,
    verify: bool | str = True,
) -> dict[str, Any]:
    return get_android_relay_json(
        base_url=base_url,
        path="/v1/android/sessions",
        android_app_token=android_app_token,
        query=build_android_sessions_query_params(
            pc_id=pc_id,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            include_ended=include_ended,
        ),
        timeout_seconds=timeout_seconds,
        verify=verify,
    )


def fetch_android_session_snapshot(
    *,
    base_url: str,
    android_app_token: str,
    workspace_id: str | None = None,
    repo_path: str | None = None,
    workdir: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    timeout_seconds: int = 30,
    verify: bool | str = True,
) -> dict[str, Any]:
    return get_android_relay_json(
        base_url=base_url,
        path="/v1/android/session-snapshot",
        android_app_token=android_app_token,
        query=build_android_session_locator_query_params(
            workspace_id=workspace_id,
            repo_path=repo_path,
            workdir=workdir,
            session_id=session_id,
            thread_id=thread_id,
        ),
        timeout_seconds=timeout_seconds,
        verify=verify,
    )


def fetch_android_session_history(
    *,
    base_url: str,
    android_app_token: str,
    workspace_id: str | None = None,
    repo_path: str | None = None,
    workdir: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    timeout_seconds: int = 30,
    verify: bool | str = True,
) -> dict[str, Any]:
    return get_android_relay_json(
        base_url=base_url,
        path="/v1/android/session-history",
        android_app_token=android_app_token,
        query=build_android_session_locator_query_params(
            workspace_id=workspace_id,
            repo_path=repo_path,
            workdir=workdir,
            session_id=session_id,
            thread_id=thread_id,
        ),
        timeout_seconds=timeout_seconds,
        verify=verify,
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        default="",
        help="Relay HTTP base URL. Defaults to --config relay_url or http://127.0.0.1:8787.",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional mail-runner config path used to derive relay HTTP base URL and TLS settings.",
    )
    parser.add_argument(
        "--android-app-token",
        default="",
        help=f"Android app bearer token. Falls back to {_ANDROID_APP_TOKEN_ENV}.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=0, help="Override request timeout seconds.")
    parser.add_argument("--ca-file", default="", help="Optional CA bundle file for HTTPS.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch live Android read-side payloads from a relay host.")
    subparsers = parser.add_subparsers(dest="view", required=True)

    sessions = subparsers.add_parser("sessions", help="Fetch GET /v1/android/sessions.")
    _add_common_arguments(sessions)
    sessions.add_argument("--pc-id", default="", help="Optional pc_id filter.")
    sessions.add_argument("--workspace-id", default="", help="Optional workspace_id filter.")
    sessions.add_argument("--session-id", default="", help="Optional session_id filter.")
    sessions.add_argument("--thread-id", default="", help="Optional thread_id filter.")
    sessions.add_argument("--include-ended", action="store_true", help="Include ended sessions.")

    snapshot = subparsers.add_parser("session-snapshot", help="Fetch GET /v1/android/session-snapshot.")
    _add_common_arguments(snapshot)
    snapshot.add_argument("--workspace-id", default="", help="Optional supporting workspace_id.")
    snapshot.add_argument("--repo-path", default="", help="Optional supporting repo_path.")
    snapshot.add_argument("--workdir", default="", help="Optional supporting workdir.")
    snapshot.add_argument("--session-id", default="", help="Canonical session id.")
    snapshot.add_argument("--thread-id", default="", help="Canonical thread id.")

    history = subparsers.add_parser("session-history", help="Fetch GET /v1/android/session-history.")
    _add_common_arguments(history)
    history.add_argument("--workspace-id", default="", help="Optional supporting workspace_id.")
    history.add_argument("--repo-path", default="", help="Optional supporting repo_path.")
    history.add_argument("--workdir", default="", help="Optional supporting workdir.")
    history.add_argument("--session-id", default="", help="Canonical session id.")
    history.add_argument("--thread-id", default="", help="Canonical thread id.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    config_path = _optional_text(args.config)
    base_url = resolve_android_read_base_url(base_url=args.base_url, config_path=config_path)
    verify = resolve_android_read_verify(
        insecure=bool(args.insecure),
        ca_file=args.ca_file,
        config_path=config_path,
    )
    timeout_seconds = resolve_android_read_timeout_seconds(
        timeout_seconds=(args.timeout_seconds if args.timeout_seconds > 0 else None),
        config_path=config_path,
    )
    android_app_token = resolve_android_app_token(args.android_app_token)

    if args.view == "sessions":
        result = fetch_android_sessions(
            base_url=base_url,
            android_app_token=android_app_token,
            pc_id=args.pc_id or None,
            workspace_id=args.workspace_id or None,
            session_id=args.session_id or None,
            thread_id=args.thread_id or None,
            include_ended=bool(args.include_ended),
            timeout_seconds=timeout_seconds,
            verify=verify,
        )
    elif args.view == "session-snapshot":
        result = fetch_android_session_snapshot(
            base_url=base_url,
            android_app_token=android_app_token,
            workspace_id=args.workspace_id or None,
            repo_path=args.repo_path or None,
            workdir=args.workdir or None,
            session_id=args.session_id or None,
            thread_id=args.thread_id or None,
            timeout_seconds=timeout_seconds,
            verify=verify,
        )
    elif args.view == "session-history":
        result = fetch_android_session_history(
            base_url=base_url,
            android_app_token=android_app_token,
            workspace_id=args.workspace_id or None,
            repo_path=args.repo_path or None,
            workdir=args.workdir or None,
            session_id=args.session_id or None,
            thread_id=args.thread_id or None,
            timeout_seconds=timeout_seconds,
            verify=verify,
        )
    else:
        raise ValueError(f"unsupported view: {args.view}")

    write_probe_output(args.output or None, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
