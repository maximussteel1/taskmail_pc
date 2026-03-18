"""Phase 7 paused-protocol integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.app import process_once
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import MailEnvelope, RunResult, TaskSnapshot
from mail_runner.runner import SerialTaskRunner
from mail_runner.status import (
    BACKEND_OPENCODE,
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_PAUSED,
)
from mail_runner.thread_store import build_workspace_id, load_session_state, load_thread_state, save_thread_state


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


class RecordingAdapter(MockAdapter):
    def __init__(self) -> None:
        super().__init__(sleep_seconds=0)
        self.snapshots: list[TaskSnapshot] = []

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.snapshots.append(task)
        return super().run(task, run_dir)


class QuestionThenSuccessAdapter(WorkerAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.tasks: list[TaskSnapshot] = []

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.calls += 1
        self.tasks.append(task)
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        (run_path / "stdout.log").write_text("stdout", encoding="utf-8")
        (run_path / "stderr.log").write_text("", encoding="utf-8")
        summary_line = "Should I update both modules?" if self.calls == 1 else "Updated both modules successfully."
        (run_path / "summary.md").write_text(summary_line + "\n", encoding="utf-8")
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_AWAITING_USER_INPUT if self.calls == 1 else RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-12T12:00:01",
            finished_at="2026-03-12T12:00:05",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=None,
            question_id="question_task_001" if self.calls == 1 else None,
            question_text="Should I update both modules?" if self.calls == 1 else None,
            pending_choices=["yes", "no"] if self.calls == 1 else [],
            backend_session_id="native-session-001",
            backend_session_resumable=True,
        )

    def kill(self, task_id: str) -> bool:
        return False


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


def test_process_once_pause_marks_thread_and_session_paused_and_blocks_plain_reply(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    client = FakeMailClient(
        [
            MailEnvelope(
                message_id="<pause@example.com>",
                subject="Re: [DONE][S:thread_001] Demo task",
                from_addr="user@example.com",
                to_addr="user@example.com",
                date="2026-03-12T12:10:00",
                in_reply_to="<done@example.com>",
                references=["<root@example.com>", "<done@example.com>"],
                body_text="/pause",
                raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
            )
        ]
    )
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    pause_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    paused_state = load_thread_state("thread_001", tmp_path / "tasks")
    session_state = load_session_state(build_workspace_id("D:\\repo", "src"), "thread_001", tmp_path / "tasks")

    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<paused-plain-reply@example.com>",
                subject="Re: [PAUSED][S:thread_001] demo task",
                from_addr="user@example.com",
                to_addr="user@example.com",
                date="2026-03-12T12:11:00",
                in_reply_to=paused_state.latest_message_id,
                references=[paused_state.root_message_id, paused_state.latest_message_id],
                body_text="Please continue with the cleanup.",
                raw_headers={"Subject": "Re: [PAUSED][S:thread_001] demo task"},
            )
        ]
    )

    blocked_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert pause_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert blocked_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert paused_state.status == THREAD_STATUS_PAUSED
    assert paused_state.paused_from_status == "done"
    assert session_state.status == "paused"
    assert len(adapter.snapshots) == 1
    assert [item["subject"] for item in client.sent_messages] == [
        "[PAUSED][S:thread_001] demo task",
        "[PAUSED][S:thread_001] demo task",
    ]
    assert "latest reply was not applied" in client.sent_messages[-1]["body"]


def test_process_once_resume_continues_paused_thread(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    client = FakeMailClient(
        [
            MailEnvelope(
                message_id="<pause@example.com>",
                subject="Re: [DONE][S:thread_001] Demo task",
                from_addr="user@example.com",
                to_addr="user@example.com",
                date="2026-03-12T12:10:00",
                in_reply_to="<done@example.com>",
                references=["<root@example.com>", "<done@example.com>"],
                body_text="/pause",
                raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
            )
        ]
    )
    process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    paused_state = load_thread_state("thread_001", tmp_path / "tasks")

    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<resume@example.com>",
                subject="Re: [PAUSED][S:thread_001] demo task",
                from_addr="user@example.com",
                to_addr="user@example.com",
                date="2026-03-12T12:12:00",
                in_reply_to=paused_state.latest_message_id,
                references=[paused_state.root_message_id, paused_state.latest_message_id],
                body_text="/resume\nPlease continue with the cleanup.",
                raw_headers={"Subject": "Re: [PAUSED][S:thread_001] demo task"},
            )
        ]
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    final_state = load_thread_state("thread_001", tmp_path / "tasks")

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].turn_text == "Please continue with the cleanup."
    assert final_state.status == "done"
    assert final_state.paused_from_status is None
    assert [item["subject"] for item in client.sent_messages] == [
        "[PAUSED][S:thread_001] demo task",
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]


def test_process_once_resume_without_answer_reopens_paused_question_thread(tmp_path) -> None:
    adapter = QuestionThenSuccessAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    client = FakeMailClient(
        [
            MailEnvelope(
                message_id="<root@example.com>",
                subject="[OC] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:00:00",
                body_text="Repo: D:\\repo\nTask:\nInspect both modules.\n",
                raw_headers={"Subject": "[OC] Demo task"},
            )
        ]
    )

    first_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    waiting_state = load_thread_state("thread_001", tmp_path / "tasks")
    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<pause-question@example.com>",
                subject="Re: [QUESTION][S:thread_001] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:05:00",
                in_reply_to=waiting_state.latest_message_id,
                references=[waiting_state.root_message_id, waiting_state.latest_message_id],
                body_text="/pause",
                raw_headers={"Subject": "Re: [QUESTION][S:thread_001] Demo task"},
            )
        ]
    )
    pause_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    paused_state = load_thread_state("thread_001", tmp_path / "tasks")

    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<resume-question@example.com>",
                subject="Re: [PAUSED][S:thread_001] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:06:00",
                in_reply_to=paused_state.latest_message_id,
                references=[paused_state.root_message_id, paused_state.latest_message_id],
                body_text="/resume",
                raw_headers={"Subject": "Re: [PAUSED][S:thread_001] Demo task"},
            )
        ]
    )
    resume_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    resumed_state = load_thread_state("thread_001", tmp_path / "tasks")

    assert first_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert pause_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert resume_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert waiting_state.status == THREAD_STATUS_AWAITING_USER_INPUT
    assert paused_state.status == THREAD_STATUS_PAUSED
    assert paused_state.paused_from_status == THREAD_STATUS_AWAITING_USER_INPUT
    assert resumed_state.status == THREAD_STATUS_AWAITING_USER_INPUT
    assert resumed_state.paused_from_status is None
    assert adapter.calls == 1
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Demo task",
        "[RUNNING][S:thread_001] Demo task",
        "[QUESTION][S:thread_001] Demo task",
        "[PAUSED][S:thread_001] Demo task",
        "[QUESTION][S:thread_001] Demo task",
    ]
    assert "no longer paused" in client.sent_messages[-1]["body"]


def test_process_once_resume_without_answer_reactivates_ended_paused_question_thread(tmp_path) -> None:
    adapter = QuestionThenSuccessAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    client = FakeMailClient(
        [
            MailEnvelope(
                message_id="<root@example.com>",
                subject="[OC] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:00:00",
                body_text="Repo: D:\\repo\nTask:\nInspect both modules.\n",
                raw_headers={"Subject": "[OC] Demo task"},
            )
        ]
    )

    process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    waiting_state = load_thread_state("thread_001", tmp_path / "tasks")
    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<pause-question-ended@example.com>",
                subject="Re: [QUESTION][S:thread_001] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:05:00",
                in_reply_to=waiting_state.latest_message_id,
                references=[waiting_state.root_message_id, waiting_state.latest_message_id],
                body_text="/pause",
                raw_headers={"Subject": "Re: [QUESTION][S:thread_001] Demo task"},
            )
        ]
    )
    process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    paused_state = load_thread_state("thread_001", tmp_path / "tasks")
    paused_state.lifecycle = "ended"
    save_thread_state(paused_state, tmp_path / "tasks")

    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<resume-question-ended@example.com>",
                subject="Re: [PAUSED][S:thread_001] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:06:00",
                in_reply_to=paused_state.latest_message_id,
                references=[paused_state.root_message_id, paused_state.latest_message_id],
                body_text="/resume",
                raw_headers={"Subject": "Re: [PAUSED][S:thread_001] Demo task"},
            )
        ]
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    resumed_state = load_thread_state("thread_001", tmp_path / "tasks")

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert resumed_state.lifecycle == "active"
    assert resumed_state.status == THREAD_STATUS_AWAITING_USER_INPUT
    assert resumed_state.paused_from_status is None
