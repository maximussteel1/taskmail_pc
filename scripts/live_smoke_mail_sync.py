"""Live mailbox smoke test for the first-mail [SYNC] project-folder entry."""

from __future__ import annotations

import argparse
import imaplib
import json
import secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_smoke_mail_roundtrip import (  # type: ignore[import-not-found]
    _envelope_to_dict,
    _scan_recent_messages,
    _send_and_record_mail,
    _timestamp_slug,
    _write_json,
)
from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.app import process_once
from mail_runner.config import PROJECT_ROOT, load_config
from mail_runner.dispatcher import Dispatcher
from mail_runner.mail_io import (
    SYSTEM_MESSAGE_HEADER,
    SYSTEM_MESSAGE_HEADER_VALUE,
    MailClient,
)
from mail_runner.project_folder_sync import ProjectFolderRootListing, list_project_folders


class SingleEnvelopeClient:
    """Feed one mailbox envelope into process_once while delegating real replies."""

    def __init__(self, envelope, real_client: MailClient) -> None:
        self._envelope = envelope
        self._real_client = real_client
        self.sent_messages: list[dict[str, Any]] = []

    def fetch_unseen_messages(self):
        return [self._envelope]

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return self._real_client.send_mail(**kwargs)


def _wait_for_mail(
    config,
    predicate: Callable[[Any], bool],
    *,
    timeout_seconds: int,
    interval_seconds: int,
    scan_limit: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for item in _scan_recent_messages(config, scan_limit=scan_limit):
            if predicate(item["envelope"]):
                return item
        time.sleep(interval_seconds)
    raise TimeoutError("Timed out waiting for mailbox message.")


def _mark_message_seen(config, *, message_id: str) -> None:
    client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
    try:
        client.login(config.imap_user, config.imap_password)
        status, _ = client.select("INBOX")
        if status != "OK":
            raise RuntimeError("Unable to select INBOX.")
        status, data = client.search(None, "HEADER", "Message-ID", f'"{message_id}"')
        if status != "OK":
            raise RuntimeError(f"Unable to locate mailbox message by Message-ID: {message_id}")
        for raw_id in data[0].split():
            client.store(raw_id, "+FLAGS", "\\Seen")
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass


def _build_expected_checks(listings: list[ProjectFolderRootListing], body_text: str) -> dict[str, Any]:
    root_checks: list[dict[str, Any]] = []
    sample_entry_checks: list[dict[str, Any]] = []
    for listing in listings:
        root_path = listing.root_path
        if listing.available:
            root_ok = f"- {root_path} | available | " in body_text
            sample_entry = listing.entries[0] if listing.entries else None
            sample_ok = (
                f"- {sample_entry.name} | {sample_entry.path}" in body_text
                if sample_entry is not None
                else "- (no folders found)" in body_text
            )
            root_checks.append(
                {
                    "root_path": root_path,
                    "expected": "available",
                    "ok": root_ok,
                }
            )
            sample_entry_checks.append(
                {
                    "root_path": root_path,
                    "sample_entry": asdict(sample_entry) if sample_entry is not None else None,
                    "ok": sample_ok,
                }
            )
            continue
        unavailable_line = f"- {root_path} | unavailable | {listing.error or 'unknown error'}"
        root_checks.append(
            {
                "root_path": root_path,
                "expected": "unavailable",
                "error": listing.error,
                "ok": unavailable_line in body_text,
            }
        )

    return {
        "root_checks": root_checks,
        "sample_entry_checks": sample_entry_checks,
        "roots_ok": all(item["ok"] for item in root_checks),
        "samples_ok": all(item["ok"] for item in sample_entry_checks),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live mailbox smoke test for the first-mail [SYNC] project-folder entry."
    )
    parser.add_argument("--config", "-c", help="Path to the mail runner config file")
    parser.add_argument(
        "--sender-config",
        help="Optional sender mailbox config. Defaults to --config for single-mailbox smoke tests.",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=int,
        default=180,
        help="How long to wait for request/reply messages in the mailbox.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=5,
        help="Mailbox poll interval.",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=200,
        help="How many recent inbox messages to scan each poll.",
    )
    parser.add_argument(
        "--mail-fetch-mode",
        choices=("real", "inject"),
        default="real",
        help="Use the real MailClient inbox fetch path or inject the matched request directly into process_once.",
    )
    parser.add_argument(
        "--to-addr",
        help="Destination mailbox address. Defaults to imap_user/from_addr/smtp_user.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "_tmp_live_mail_sync_smoke"),
        help="Directory where smoke-test artifacts are written.",
    )
    parser.add_argument(
        "--run-name",
        help="Optional fixed subject suffix. Default is sync + timestamp + random token.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    bot_config = load_config(args.config)
    sender_config = load_config(args.sender_config) if args.sender_config else bot_config
    config_base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT
    to_addr = args.to_addr or bot_config.imap_user or bot_config.from_addr or bot_config.smtp_user
    if not to_addr:
        raise SystemExit("Unable to determine destination address. Pass --to-addr or set mail config credentials.")

    run_token = args.run_name or f"sync-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir) / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    bot_config.task_root = str(run_dir / "tasks")

    sender_client = MailClient(sender_config)
    bot_client = MailClient(bot_config)
    subject = f"[SYNC] live-sync-{run_token}"
    listings = list_project_folders(list(bot_config.project_sync_roots or []))

    summary: dict[str, Any] = {
        "run_token": run_token,
        "subject": subject,
        "to_addr": to_addr,
        "bot_mailbox": bot_config.from_addr or bot_config.smtp_user or bot_config.imap_user,
        "sender_mailbox": sender_config.from_addr or sender_config.smtp_user or sender_config.imap_user,
        "project_sync_roots": list(bot_config.project_sync_roots or []),
        "listings": [asdict(item) for item in listings],
        "mail_fetch_mode": args.mail_fetch_mode,
        "output_dir": str(run_dir),
        "passed": False,
    }

    try:
        request_message_id = _send_and_record_mail(
            client=sender_client,
            to_addr=to_addr,
            subject=subject,
            body="live sync smoke\n",
            output_dir=run_dir,
            step_name="request",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "request",
            },
        )
        request_item = _wait_for_mail(
            bot_config,
            lambda env: env.message_id == request_message_id,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
        )
        request_envelope = request_item["envelope"]
        _write_json(
            run_dir / "request_received.json",
            {
                "imap_id": request_item["imap_id"],
                "mail": _envelope_to_dict(request_envelope),
            },
        )
        if args.mail_fetch_mode == "inject":
            _mark_message_seen(bot_config, message_id=request_message_id)
            client = SingleEnvelopeClient(request_envelope, bot_client)
            dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
            stats = process_once(bot_config, base_dir=config_base_dir, mail_client=client, dispatcher=dispatcher)
        else:
            stats = process_once(bot_config, base_dir=config_base_dir)

        reply_item = _wait_for_mail(
            sender_config,
            lambda env: env.raw_headers.get(SYSTEM_MESSAGE_HEADER) == SYSTEM_MESSAGE_HEADER_VALUE
            and env.in_reply_to == request_message_id
            and env.subject.strip() == "[SYNC] Project Folder List",
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
        )
        reply_envelope = reply_item["envelope"]
        _write_json(
            run_dir / "reply_received.json",
            {
                "imap_id": reply_item["imap_id"],
                "mail": _envelope_to_dict(reply_envelope),
            },
        )

        body_text = reply_envelope.body_text
        listing_checks = _build_expected_checks(listings, body_text)
        tasks_root = run_dir / "tasks"
        thread_dirs = [path.name for path in tasks_root.glob("thread_*") if path.is_dir()] if tasks_root.exists() else []
        checks = {
            "processed": stats["processed"] >= 1 and stats["failed"] == 0,
            "reply_subject": reply_envelope.subject.strip() == "[SYNC] Project Folder List",
            "reply_to_request": reply_envelope.in_reply_to == request_message_id,
            "contains_intro": "Project folder sync completed. No task was created." in body_text,
            "contains_start_hint": "To start a task, send a new [OC] or [CX] mail and copy one path into Repo:." in body_text,
            "no_state_capsule": "---TASK-STATE-BEGIN---" not in body_text,
            "no_question_capsule": "---TASK-QUESTION-BEGIN---" not in body_text,
            "no_thread_dirs_created": thread_dirs == [],
            "roots_ok": listing_checks["roots_ok"],
            "samples_ok": listing_checks["samples_ok"],
        }

        summary.update(
            {
                "request_message_id": request_message_id,
                "reply_message_id": reply_envelope.message_id,
                "stats": stats,
                "thread_dirs": thread_dirs,
                "checks": checks,
                "listing_checks": listing_checks,
            }
        )
        summary["passed"] = all(checks.values())
        return 0 if summary["passed"] else 1
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        _write_json(run_dir / "result.json", summary)
        print(f"result: {run_dir / 'result.json'}")
        print(json.dumps(summary.get("checks") or {}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
