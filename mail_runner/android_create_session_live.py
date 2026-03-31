"""Submit one live Android create-session request to a relay host."""

from __future__ import annotations

import argparse
import json

from .android_relay_test_client import (
    DEFAULT_ANDROID_RELAY_BASE_URL,
    build_create_session_payload,
    post_android_relay_json,
    write_probe_output,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit one live Android create-session request to a relay host.")
    parser.add_argument("--base-url", default=DEFAULT_ANDROID_RELAY_BASE_URL, help="Relay HTTP base URL.")
    parser.add_argument("--android-app-token", required=True, help="Android app bearer token.")
    parser.add_argument("--pc-id", required=True, help="Target PC id.")
    parser.add_argument("--workspace-id", required=True, help="Target workspace id.")
    parser.add_argument("--prompt", required=True, help="Prompt text for the new session.")
    parser.add_argument("--backend", default="codex", help="Execution backend.")
    parser.add_argument("--profile", default="default", help="Execution profile.")
    parser.add_argument("--permission", default="default", help="Execution permission.")
    parser.add_argument("--backend-transport", default="sdk", help="Execution backend transport.")
    parser.add_argument("--mode", default="", help="Optional create-session mode.")
    parser.add_argument("--timeout-seconds", type=int, default=600, help="Task timeout seconds.")
    parser.add_argument("--acceptance", nargs="*", default=None, help="Optional acceptance strings.")
    parser.add_argument("--repo-path", default="", help="Optional repo_path override.")
    parser.add_argument("--workdir", default="", help="Optional workdir override.")
    parser.add_argument("--canonical-reply-recipient", default="", help="Optional canonical reply recipient.")
    parser.add_argument("--source", default="vps_probe", help="Optional create-session source.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = build_create_session_payload(
        pc_id=args.pc_id,
        workspace_id=args.workspace_id,
        prompt=args.prompt,
        backend=args.backend,
        profile=args.profile,
        permission=args.permission,
        backend_transport=args.backend_transport,
        mode=args.mode or None,
        timeout_seconds=args.timeout_seconds,
        acceptance=args.acceptance,
        repo_path=args.repo_path or None,
        workdir=args.workdir or None,
        canonical_reply_recipient=args.canonical_reply_recipient or None,
        source=args.source or None,
    )
    result = post_android_relay_json(
        base_url=args.base_url,
        path="/v1/android/create-session",
        android_app_token=args.android_app_token,
        payload=payload,
    )
    write_probe_output(args.output or None, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
