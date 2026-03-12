"""Phase 2 app integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.app import process_once
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import MailEnvelope, RunResult, TaskSnapshot
from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.status import RUN_STATUS_SUCCESS


class FakeMailClient:
    def __init__(self, envelopes):
        self._envelopes = list(envelopes)
        self.sent_messages: list[dict] = []
        self._sent_count = 0

    def fetch_unseen_messages(self):
        return list(self._envelopes)

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        self._sent_count += 1
        return f"<sent-{self._sent_count}@example.com>"


def test_process_once_runs_new_task_happy_path(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="\n".join(
            [
                "Repo: D:\\repo",
                "Workdir: src",
                "",
                "Task:",
                "Refactor the module.",
                "",
                "Acceptance:",
                "1. pytest passes",
            ]
        ),
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    task_root = tmp_path / "tasks" / "thread_001"
    state = json.loads((task_root / "thread_state.json").read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED] Demo task",
        "[RUNNING] Demo task",
        "[DONE] Demo task",
    ]
    assert (task_root / "mail" / "raw_001.json").exists()
    assert state["status"] == "done"
    assert (task_root / "runs" / state["current_task_id"] / "result.json").exists()


def test_process_once_skips_unmatched_reply_mail(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<reply@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<root@example.com>",
        references=["<root@example.com>"],
        body_text="Repo: D:\\repo\nTask:\nRefactor the module.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 0, "skipped": 1, "failed": 0}
    assert client.sent_messages == []


class SummaryAdapter(WorkerAdapter):
    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        (run_path / "stdout.log").write_text("Repository is minimal. No files were modified.\n", encoding="utf-8")
        (run_path / "stderr.log").write_text("", encoding="utf-8")
        (run_path / "summary.md").write_text(
            "Repository is minimal. No files were modified.\n\nBackend: OpenCode\n",
            encoding="utf-8",
        )
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-12T12:20:01",
            finished_at="2026-03-12T12:20:03",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=None,
        )

    def kill(self, task_id: str) -> bool:
        return False


def test_process_once_uses_user_summary_in_done_mail(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root-summary@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:20:00",
        body_text="\n".join(
            [
                "Repo: D:\\repo",
                "",
                "Task:",
                "Inspect the repository.",
            ]
        ),
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(SummaryAdapter(), SummaryAdapter())
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert state["last_summary"] == "Repository is minimal. No files were modified."
    assert "Summary: Repository is minimal. No files were modified." in client.sent_messages[-1]["body"]
