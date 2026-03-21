from __future__ import annotations

import json

from mail_runner.config import AppConfig
from mail_runner.models import TaskSnapshot, ThreadState
from mail_runner.outbound.journal import delivery_attempts_path
from mail_runner.outbound.service import build_references, send_status_update
from mail_runner.reporter import MAIL_STATUS_STATUS
from mail_runner.status import THREAD_STATUS_DONE


class FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return "<sent-1@example.com>"


class FailingMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        raise RuntimeError("smtp down")


def _state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend="opencode",
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        last_summary="Mock run completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )


def _snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="opencode",
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect the module.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )


def test_build_references_appends_unique_message_id() -> None:
    assert build_references("<done@example.com>", ["<root@example.com>"]) == [
        "<root@example.com>",
        "<done@example.com>",
    ]
    assert build_references("<done@example.com>", ["<root@example.com>", "<done@example.com>"]) == [
        "<root@example.com>",
        "<done@example.com>",
    ]


def test_send_status_update_sends_and_stores_status_mail(tmp_path) -> None:
    client = FakeMailClient()
    config = AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root="tasks")
    state = _state()
    snapshot = _snapshot()
    task_root = tmp_path / "tasks"

    message_id = send_status_update(
        client,
        config,
        task_root,
        to_addr="user@example.com",
        subject_text="Demo task",
        status_label=MAIL_STATUS_STATUS,
        state=state,
        task_snapshot=snapshot,
        summary_override="Current local status only.",
    )

    assert message_id == "<sent-1@example.com>"
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] Demo task"]
    assert client.sent_messages[0]["in_reply_to"] == "<done@example.com>"
    assert client.sent_messages[0]["references"] == ["<root@example.com>", "<done@example.com>"]
    assert "Current local status only." in client.sent_messages[0]["body"]
    assert '<article class="task-mail"' in client.sent_messages[0]["html_body"]

    raw_mail_dir = tmp_path / "tasks" / "thread_001" / "mail"
    raw_payloads = sorted(raw_mail_dir.glob("raw_*.json"))
    assert len(raw_payloads) == 1
    payload = json.loads(raw_payloads[0].read_text(encoding="utf-8"))
    assert payload["message_id"] == "<sent-1@example.com>"
    assert payload["subject"] == "[STATUS][S:thread_001] Demo task"
    assert payload["raw_headers"]["Message-ID"] == "<sent-1@example.com>"
    assert state.latest_message_id == "<sent-1@example.com>"

    journal_path = delivery_attempts_path(task_root, state.thread_id)
    attempts = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert attempts == [
        {
            "packet_id": attempts[0]["packet_id"],
            "thread_id": "thread_001",
            "task_id": "task_001",
            "transport_name": "email",
            "sent_at": attempts[0]["sent_at"],
            "success": True,
            "to_addr": "user@example.com",
            "subject": "[STATUS][S:thread_001] Demo task",
            "transport_message_id": "<sent-1@example.com>",
            "error_message": None,
            "client_trace_id": "task_001",
        }
    ]


def test_send_status_update_records_failed_delivery_attempt(tmp_path) -> None:
    client = FailingMailClient()
    config = AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root="tasks")
    state = _state()
    snapshot = _snapshot()
    task_root = tmp_path / "tasks"

    message_id = send_status_update(
        client,
        config,
        task_root,
        to_addr="user@example.com",
        subject_text="Demo task",
        status_label=MAIL_STATUS_STATUS,
        state=state,
        task_snapshot=snapshot,
        summary_override="Current local status only.",
    )

    assert message_id is None
    assert len(client.sent_messages) == 1
    raw_mail_dir = task_root / "thread_001" / "mail"
    assert not raw_mail_dir.exists()

    journal_path = delivery_attempts_path(task_root, state.thread_id)
    attempts = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert attempts == [
        {
            "packet_id": attempts[0]["packet_id"],
            "thread_id": "thread_001",
            "task_id": "task_001",
            "transport_name": "email",
            "sent_at": attempts[0]["sent_at"],
            "success": False,
            "to_addr": "user@example.com",
            "subject": "[STATUS][S:thread_001] Demo task",
            "transport_message_id": None,
            "error_message": "RuntimeError: smtp down",
            "client_trace_id": "task_001",
        }
    ]


def test_send_status_update_falls_back_to_email_when_relay_fails(tmp_path) -> None:
    client = FakeMailClient()
    config = AppConfig(
        from_addr="bot@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        outbound_transport="relay",
        relay_auto_fallback_email=True,
    )
    state = _state()
    snapshot = _snapshot()
    task_root = tmp_path / "tasks"

    message_id = send_status_update(
        client,
        config,
        task_root,
        to_addr="user@example.com",
        subject_text="Demo task",
        status_label=MAIL_STATUS_STATUS,
        state=state,
        task_snapshot=snapshot,
        summary_override="Current local status only.",
    )

    assert message_id == "<sent-1@example.com>"
    assert len(client.sent_messages) == 1

    journal_path = delivery_attempts_path(task_root, state.thread_id)
    attempts = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [item["transport_name"] for item in attempts] == ["relay", "email"]
    assert attempts[0]["success"] is False
    assert attempts[1]["success"] is True
