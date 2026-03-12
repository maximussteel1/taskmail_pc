"""Phase 3 app integration tests."""

from __future__ import annotations

import json

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.app import _process_batch, process_once
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import MailEnvelope, TaskSnapshot
from mail_runner.runner import SerialTaskRunner
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_KILLED


class FakeMailClient:
    def __init__(self, envelopes):
        self._envelopes = list(envelopes)
        self.sent_messages: list[dict] = []
        self._sent_count = 0

    def set_envelopes(self, envelopes) -> None:
        self._envelopes = list(envelopes)

    def fetch_unseen_messages(self):
        return list(self._envelopes)

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        self._sent_count += 1
        return f"<sent-{self._sent_count}@example.com>"


def _setup_existing_thread(task_root, dispatcher: Dispatcher) -> None:
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor the module.",
        acceptance=["pytest passes"],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    runner.run_task_snapshot(
        snapshot,
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
    )


def test_process_once_handles_status_query_reply(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-status@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="现在状态如何？",
        raw_headers={"Subject": "Re: [DONE] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS] demo task"]
    snapshots = list((tmp_path / "tasks" / "thread_001" / "snapshots").glob("*.json"))
    assert len(snapshots) == 1


def test_process_once_handles_update_reply_and_generates_new_snapshot(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-update@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="Timeout: 120\nMode: analysis_only\nTask:\nOnly analyze the issue.",
        raw_headers={"Subject": "Re: [DONE] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    snapshot_path = tmp_path / "tasks" / "thread_001" / state["last_task_snapshot_file"]
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED] demo task",
        "[RUNNING] demo task",
        "[DONE] demo task",
    ]
    assert snapshot_payload["timeout_minutes"] == 120
    assert snapshot_payload["mode"] == "analysis_only"
    assert snapshot_payload["task_text"] == "Only analyze the issue."


def test_background_batch_handles_kill_reply(tmp_path) -> None:
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(tmp_path / "tasks", dispatcher)
    client = FakeMailClient(
        [
            MailEnvelope(
                message_id="<root@example.com>",
                subject="[OC] Demo task",
                from_addr="user@example.com",
                to_addr="user@example.com",
                date="2026-03-12T12:20:00",
                body_text="Repo: D:\\repo\nTask:\nLong running task.\n",
                raw_headers={"Subject": "[OC] Demo task"},
            )
        ]
    )

    first_stats = _process_batch(config, tmp_path / "tasks", client, runner, background=True)
    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    kill_reply = MailEnvelope(
        message_id="<kill@example.com>",
        subject="Re: [RUNNING] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:20:01",
        in_reply_to=state["latest_message_id"],
        references=[state["root_message_id"], state["latest_message_id"]],
        body_text="终止当前任务",
        raw_headers={"Subject": "Re: [RUNNING] Demo task"},
    )
    client.set_envelopes([kill_reply])

    second_stats = _process_batch(config, tmp_path / "tasks", client, runner, background=True)
    result = runner.wait_for_active()

    assert first_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert second_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert result is not None
    assert result.status == RUN_STATUS_KILLED
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED] Demo task",
        "[RUNNING] Demo task",
        "[KILLED] Demo task",
    ]
