"""Submit one live Android fake reply request to a relay host."""

from __future__ import annotations

import argparse
import json

from .android_relay_test_client import (
    DEFAULT_ANDROID_RELAY_BASE_URL,
    build_fake_reply_payload,
    post_android_relay_json,
    write_probe_output,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit one live Android fake reply request to a relay host.")
    parser.add_argument("--base-url", default=DEFAULT_ANDROID_RELAY_BASE_URL, help="Relay HTTP base URL.")
    parser.add_argument("--android-app-token", required=True, help="Android app bearer token.")
    parser.add_argument("--session-id", required=True, help="Target session id.")
    parser.add_argument("--reply-text", required=True, help="Reply text to submit.")
    parser.add_argument("--workspace-id", default="", help="Optional supporting workspace id.")
    parser.add_argument("--thread-id", default="", help="Optional supporting thread id.")
    parser.add_argument("--request-id", default="", help="Optional explicit request id.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = build_fake_reply_payload(
        session_id=args.session_id,
        reply_text=args.reply_text,
        workspace_id=args.workspace_id or None,
        thread_id=args.thread_id or None,
        request_id=args.request_id or None,
    )
    result = post_android_relay_json(
        base_url=args.base_url,
        path="/v1/android/session-action",
        android_app_token=args.android_app_token,
        payload=payload,
    )
    write_probe_output(args.output or None, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
