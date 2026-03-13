"""Fetch emails and output as JSON for debugging purposes."""

from __future__ import annotations

import argparse
import imaplib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.config import load_config
from mail_runner.mail_io import message_bytes_to_envelope


def fetch_mails(config, count: int | None = None, unseen_only: bool = True) -> list[dict]:
    client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
    messages: list[dict] = []
    try:
        client.login(config.imap_user, config.imap_password)
        status, _ = client.select("INBOX")
        if status != "OK":
            raise RuntimeError("Unable to select INBOX.")
        search_criteria = "UNSEEN" if unseen_only else "ALL"
        status, data = client.search(None, search_criteria)
        if status != "OK":
            raise RuntimeError("Unable to search for mails.")
        mail_ids = data[0].split()
        if count is not None and count > 0:
            mail_ids = mail_ids[-count:]
        for raw_id in mail_ids:
            status, payload = client.fetch(raw_id, "(RFC822)")
            if status != "OK" or not payload or not payload[0]:
                continue
            message_bytes = payload[0][1]
            envelope = message_bytes_to_envelope(
                message_bytes, raw_id.decode("ascii", errors="ignore")
            )
            messages.append({
                "message_id": envelope.message_id,
                "subject": envelope.subject,
                "from_addr": envelope.from_addr,
                "to_addr": envelope.to_addr,
                "date": envelope.date,
                "in_reply_to": envelope.in_reply_to,
                "references": envelope.references,
                "body_text": envelope.body_text,
                "raw_headers": envelope.raw_headers,
            })
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch emails and output as JSON")
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    parser.add_argument("--count", "-n", type=int, default=None, help="Max number of emails to fetch")
    unseen_group = parser.add_mutually_exclusive_group()
    unseen_group.add_argument("--unseen", dest="unseen", action="store_true", default=True,
                              help="Fetch only unseen emails (default)")
    unseen_group.add_argument("--all", dest="unseen", action="store_false",
                              help="Fetch all emails")
    args = parser.parse_args()

    config = load_config(args.config)
    messages = fetch_mails(config, count=args.count, unseen_only=args.unseen)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fetched {len(messages)} email(s) to {output_path}")


if __name__ == "__main__":
    main()