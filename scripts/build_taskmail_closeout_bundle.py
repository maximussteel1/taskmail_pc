"""Build a TaskMail daily closeout bundle for one archived run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mail_runner.taskmail_closeout import build_taskmail_daily_closeout_bundle, write_taskmail_daily_closeout_bundle


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble a TaskMail daily closeout bundle from canonical_summary.json and optional Android evidence."
    )
    parser.add_argument("thread_id", help="Thread ID, for example thread_095")
    parser.add_argument(
        "--task-root",
        default="tasks",
        help="Task root directory. Defaults to ./tasks.",
    )
    parser.add_argument(
        "--task-id",
        help="Optional task/run ID. Defaults to thread_state.current_task_id.",
    )
    parser.add_argument(
        "--android-send-records",
        help="Optional path to Android taskmail_new_task_send_records.json.",
    )
    parser.add_argument(
        "--sender-account-id",
        help="Optional sender account ID used to narrow Android latest-evidence selection.",
    )
    parser.add_argument(
        "--android-last-summary",
        help="Optional Android terminal-summary token used for weak last_summary bind checks.",
    )
    parser.add_argument(
        "--relay-state-dir",
        help="Optional relay state dir that contains packets.json and delivery_attempts.jsonl.",
    )
    parser.add_argument(
        "--output",
        help="Optional output file path. When omitted, prints JSON unless --write-run-artifact is set.",
    )
    parser.add_argument(
        "--write-run-artifact",
        action="store_true",
        help="Write the bundle to runs/<task_id>/taskmail_daily_closeout_bundle.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.output or args.write_run_artifact:
            output_path = write_taskmail_daily_closeout_bundle(
                args.thread_id,
                args.task_root,
                task_id=args.task_id,
                android_send_records_path=args.android_send_records,
                sender_account_id=args.sender_account_id,
                android_last_summary=args.android_last_summary,
                relay_state_dir=args.relay_state_dir,
                output_path=args.output,
            )
            print(output_path)
            return 0

        bundle = build_taskmail_daily_closeout_bundle(
            args.thread_id,
            args.task_root,
            task_id=args.task_id,
            android_send_records_path=args.android_send_records,
            sender_account_id=args.sender_account_id,
            android_last_summary=args.android_last_summary,
            relay_state_dir=args.relay_state_dir,
        )
        rendered = json.dumps(bundle, indent=2, ensure_ascii=False)
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
        print(rendered)
        return 0
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
