"""Phase 6 app integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.app import process_once
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import MailEnvelope, RunResult, TaskSnapshot
from mail_runner.status import (
    BACKEND_OPENCODE,
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_SUCCESS,
)


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


def test_process_once_handles_question_then_answer_flow(tmp_path) -> None:
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
    first_state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))

    reply = MailEnvelope(
        message_id="<reply@example.com>",
        subject="Re: [QUESTION] Demo task",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T12:05:00",
        in_reply_to=first_state["latest_message_id"],
        references=[first_state["root_message_id"], first_state["latest_message_id"]],
        body_text="Profile: strong\nYes, update both modules.",
        raw_headers={"Subject": "Re: [QUESTION] Demo task"},
    )
    client.set_envelopes([reply])

    second_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    second_state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    latest_snapshot = json.loads(
        (tmp_path / "tasks" / "thread_001" / second_state["last_task_snapshot_file"]).read_text(encoding="utf-8")
    )

    assert first_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert second_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert first_state["status"] == "awaiting_user_input"
    assert first_state["pending_question_text"] == "Should I update both modules?"
    assert second_state["status"] == "done"
    assert adapter.tasks[1].run_mode == "resume"
    assert adapter.tasks[1].backend_session_id == "native-session-001"
    assert adapter.tasks[1].turn_text == "Profile: strong\nYes, update both modules."
    assert latest_snapshot["profile"] == "strong"
    assert "Answer to pending question (question_task_001):" in latest_snapshot["task_text"]
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Demo task",
        "[RUNNING][S:thread_001] Demo task",
        "[QUESTION][S:thread_001] Demo task",
        "[ACCEPTED][S:thread_001] Demo task",
        "[RUNNING][S:thread_001] Demo task",
        "[DONE][S:thread_001] Demo task",
    ]


def test_process_once_returns_status_for_rerun_while_waiting(tmp_path) -> None:
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
    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    client.set_envelopes(
        [
            MailEnvelope(
                message_id="<reply-rerun@example.com>",
                subject="Re: [QUESTION] Demo task",
                from_addr="user@example.com",
                to_addr="runner@example.com",
                date="2026-03-12T12:05:00",
                in_reply_to=state["latest_message_id"],
                references=[state["root_message_id"], state["latest_message_id"]],
                body_text="/rerun",
                raw_headers={"Subject": "Re: [QUESTION] Demo task"},
            )
        ]
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert client.sent_messages[-1]["subject"] == "[STATUS][S:thread_001] Demo task"
    assert "awaiting an answer to the pending question" in client.sent_messages[-1]["body"]
