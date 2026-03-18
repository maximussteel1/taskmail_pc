from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from mail_runner.config import AppConfig
from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "prune_stale_status_mails.py"
    spec = importlib.util.spec_from_file_location("prune_stale_status_mails", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_raw_mail(task_root: Path, thread_id: str, raw_index: int, payload: dict[str, object]) -> None:
    mail_dir = task_root / thread_id / "mail"
    mail_dir.mkdir(parents=True, exist_ok=True)
    (mail_dir / f"raw_{raw_index:03d}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_prune_stale_status_mails_writes_output_record(tmp_path, monkeypatch, capsys) -> None:
    module = _load_script_module()
    task_root = tmp_path / "tasks"
    output_path = tmp_path / "cleanup.json"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    _write_raw_mail(
        task_root,
        "thread_001",
        1,
        {
            "message_id": "<accepted@example.com>",
            "subject": "[ACCEPTED][S:thread_001] Demo",
            "raw_headers": {
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
                "Subject": "[ACCEPTED][S:thread_001] Demo",
            },
        },
    )
    _write_raw_mail(
        task_root,
        "thread_001",
        2,
        {
            "message_id": "<done@example.com>",
            "subject": "[DONE][S:thread_001] Demo",
            "raw_headers": {
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
                "Subject": "[DONE][S:thread_001] Demo",
            },
        },
    )

    class FakeClient:
        def __init__(self, config) -> None:
            self.config = config

        def list_system_message_headers(self, mailbox: str = "INBOX"):
            return [
                SimpleNamespace(message_id="<sync-old@example.com>", subject="[SYNC] Project Folder List"),
                SimpleNamespace(message_id="<sync-new@example.com>", subject="[SYNC] Project Folder List"),
            ]

        def delete_messages_by_message_ids(self, message_ids, mailbox: str = "INBOX"):
            assert mailbox == "INBOX"
            assert list(message_ids) == ["<accepted@example.com>", "<sync-old@example.com>"]
            return ["<accepted@example.com>"]

    monkeypatch.setattr(
        module,
        "load_config",
        lambda _path: AppConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="user",
            imap_password="pass",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_addr="runner@example.com",
            task_root=str(task_root),
        ),
    )
    monkeypatch.setattr(module, "MailClient", FakeClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prune_stale_status_mails.py",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ],
    )

    assert module.main() == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["stale_thread_status_message_ids"] == ["<accepted@example.com>"]
    assert payload["stale_sync_message_ids"] == ["<sync-old@example.com>"]
    assert payload["stale_message_ids"] == ["<accepted@example.com>", "<sync-old@example.com>"]
    assert payload["deleted_message_ids"] == ["<accepted@example.com>"]
    assert payload["missing_message_ids"] == ["<sync-old@example.com>"]
    assert payload["counts"] == {
        "stale_thread_status_mails": 1,
        "stale_sync_replies": 1,
        "stale_total": 2,
        "deleted_total": 1,
        "missing_total": 1,
    }

    output = capsys.readouterr().out
    assert "Deleting 2 message(s):" in output
    assert "Deleted 1 message(s)." in output
    assert "Wrote scan record to" in output
