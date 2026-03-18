"""Delete stale live-mailbox system mails that current retention rules would already remove."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.config import DEFAULT_CONFIG_PATH, ENV_PREFIX, load_config
from mail_runner.mail_io import MailClient
from mail_runner.mail_retention import (
    SystemMessageRef,
    collect_stale_sync_message_ids,
    collect_stale_thread_status_message_ids,
)


def _resolve_config_path(config_path: str | None) -> Path:
    if config_path:
        return Path(config_path).resolve()
    env_config = os.getenv(f"{ENV_PREFIX}CONFIG")
    if env_config:
        return Path(env_config).resolve()
    return DEFAULT_CONFIG_PATH.resolve()


def _dedupe_message_ids(message_ids: list[str]) -> list[str]:
    deduped: list[str] = []
    seen_ids: set[str] = set()
    for raw_message_id in message_ids:
        message_id = str(raw_message_id or "").strip()
        if not message_id or message_id in seen_ids:
            continue
        seen_ids.add(message_id)
        deduped.append(message_id)
    return deduped


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete stale live-mailbox system mails based on current task-status and [SYNC] retention rules."
    )
    parser.add_argument("--config", "-c", help="Path to the mail runner config file")
    parser.add_argument("--mailbox", default="INBOX", help="Mailbox to clean. Default: INBOX")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting it")
    parser.add_argument("--output", "-o", help="Optional JSON output path for the scan/delete record")
    parser.add_argument(
        "--threads-only",
        action="store_true",
        help="Only clean stale per-thread task status mails; skip global [SYNC] cleanup.",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Only clean stale global [SYNC] replies; skip per-thread task status cleanup.",
    )
    return parser


def _build_output_payload(
    *,
    config_path: Path,
    task_root: Path,
    mailbox: str,
    dry_run: bool,
    stale_thread_ids: list[str],
    stale_sync_ids: list[str],
    stale_message_ids: list[str],
    deleted_ids: list[str],
    missing_ids: list[str],
) -> dict[str, object]:
    return {
        "config_path": str(config_path),
        "task_root": str(task_root),
        "mailbox": mailbox,
        "dry_run": dry_run,
        "stale_thread_status_message_ids": list(stale_thread_ids),
        "stale_sync_message_ids": list(stale_sync_ids),
        "stale_message_ids": list(stale_message_ids),
        "deleted_message_ids": list(deleted_ids),
        "missing_message_ids": list(missing_ids),
        "counts": {
            "stale_thread_status_mails": len(stale_thread_ids),
            "stale_sync_replies": len(stale_sync_ids),
            "stale_total": len(stale_message_ids),
            "deleted_total": len(deleted_ids),
            "missing_total": len(missing_ids),
        },
    }


def _write_output(path_text: str, payload: dict[str, object]) -> Path:
    output_path = Path(path_text).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.threads_only and args.sync_only:
        parser.error("--threads-only and --sync-only cannot be used together")

    resolved_config_path = _resolve_config_path(args.config)
    config = load_config(str(resolved_config_path))
    task_root = config.resolve_task_root(resolved_config_path.parent)
    client = MailClient(config)

    stale_thread_ids: list[str] = []
    stale_sync_ids: list[str] = []

    if not args.sync_only:
        stale_thread_ids = collect_stale_thread_status_message_ids(task_root)
    if not args.threads_only:
        system_messages = [
            SystemMessageRef(message_id=item.message_id, subject=item.subject)
            for item in client.list_system_message_headers(mailbox=args.mailbox)
        ]
        stale_sync_ids = collect_stale_sync_message_ids(system_messages)

    stale_message_ids = _dedupe_message_ids([*stale_thread_ids, *stale_sync_ids])
    print(f"Task root: {task_root}")
    print(f"Mailbox: {args.mailbox}")
    print(f"Stale thread status mails: {len(stale_thread_ids)}")
    print(f"Stale [SYNC] replies: {len(stale_sync_ids)}")

    if not stale_message_ids:
        print("No stale system mails found.")
        output_payload = _build_output_payload(
            config_path=resolved_config_path,
            task_root=task_root,
            mailbox=args.mailbox,
            dry_run=bool(args.dry_run),
            stale_thread_ids=stale_thread_ids,
            stale_sync_ids=stale_sync_ids,
            stale_message_ids=stale_message_ids,
            deleted_ids=[],
            missing_ids=[],
        )
        if args.output:
            output_path = _write_output(args.output, output_payload)
            print(f"Wrote scan record to {output_path}")
        return 0

    action = "Would delete" if args.dry_run else "Deleting"
    print(f"{action} {len(stale_message_ids)} message(s):")
    for message_id in stale_message_ids:
        print(f"- {message_id}")

    deleted_ids: list[str] = []
    missing_ids: list[str] = []
    if args.dry_run:
        missing_ids = list(stale_message_ids)
    else:
        deleted_ids = client.delete_messages_by_message_ids(stale_message_ids, mailbox=args.mailbox)
        print(f"Deleted {len(deleted_ids)} message(s).")
        missing_ids = [message_id for message_id in stale_message_ids if message_id not in deleted_ids]
        if missing_ids:
            print(f"Not found in mailbox: {len(missing_ids)}")
            for message_id in missing_ids:
                print(f"- {message_id}")

    output_payload = _build_output_payload(
        config_path=resolved_config_path,
        task_root=task_root,
        mailbox=args.mailbox,
        dry_run=bool(args.dry_run),
        stale_thread_ids=stale_thread_ids,
        stale_sync_ids=stale_sync_ids,
        stale_message_ids=stale_message_ids,
        deleted_ids=deleted_ids,
        missing_ids=missing_ids,
    )
    if args.output:
        output_path = _write_output(args.output, output_payload)
        print(f"Wrote scan record to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
