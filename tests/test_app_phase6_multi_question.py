"""Phase 6 multi-question integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.app import process_once
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import MailEnvelope, QuestionItem, RunResult, TaskSnapshot
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


class MultiQuestionThenSuccessAdapter(WorkerAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.tasks: list[TaskSnapshot] = []

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.calls += 1
        self.tasks.append(task)
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        (run_path / "stderr.log").write_text("", encoding="utf-8")
        if self.calls == 1:
            (run_path / "stdout.log").write_text("Need more answers.\n", encoding="utf-8")
            (run_path / "summary.md").write_text("Need more answers.\n", encoding="utf-8")
            return RunResult(
                task_id=task.task_id,
                thread_id=task.thread_id,
                backend=task.backend,
                status=RUN_STATUS_AWAITING_USER_INPUT,
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
                question_id="phase2_device_validation",
                question_text="Device validation requirement?",
                pending_choices=["acceptable", "device_required"],
                question_set_id="phase2",
                pending_questions=[
                    QuestionItem(
                        question_set_id="phase2",
                        question_id="phase2_entry_position",
                        question_type="single_choice",
                        question_text="Where should the entry go?",
                        choices=["top", "below"],
                        choice_labels={"top": "账户列表上方", "below": "账户列表下方"},
                    ),
                    QuestionItem(
                        question_set_id="phase2",
                        question_id="phase2_icon_strings",
                        question_type="single_choice",
                        question_text="Who provides strings?",
                        choices=["provide", "reuse"],
                        choice_labels={"provide": "你提供", "reuse": "复用现有"},
                    ),
                    QuestionItem(
                        question_set_id="phase2",
                        question_id="phase2_k9_support",
                        question_type="single_choice",
                        question_text="Support K-9 too?",
                        choices=["both", "thunderbird_only"],
                        choice_labels={"both": "两者都需要", "thunderbird_only": "仅 Thunderbird"},
                    ),
                    QuestionItem(
                        question_set_id="phase2",
                        question_id="phase2_device_validation",
                        question_type="single_choice",
                        question_text="Device validation requirement?",
                        choices=["acceptable", "device_required"],
                        choice_labels={"acceptable": "可接受", "device_required": "必须设备验证"},
                    ),
                ],
                backend_session_id="native-session-001",
                backend_session_resumable=True,
            )
        (run_path / "stdout.log").write_text("Completed successfully.\n", encoding="utf-8")
        (run_path / "summary.md").write_text("Completed successfully.\n", encoding="utf-8")
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-12T12:10:01",
            finished_at="2026-03-12T12:10:05",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=None,
            backend_session_id="native-session-001",
            backend_session_resumable=True,
        )

    def kill(self, task_id: str) -> bool:
        return False


def test_process_once_handles_multi_question_partial_then_complete_answers(tmp_path) -> None:
    adapter = MultiQuestionThenSuccessAdapter()
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

    partial_reply = MailEnvelope(
        message_id="<reply-partial@example.com>",
        subject="Re: [QUESTION] Demo task",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T12:05:00",
        in_reply_to=first_state["latest_message_id"],
        references=[first_state["root_message_id"], first_state["latest_message_id"]],
        body_text="Answers:\nphase2_entry_position: below\nphase2_icon_strings: provide",
        raw_headers={"Subject": "Re: [QUESTION] Demo task"},
    )
    client.set_envelopes([partial_reply])
    second_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    second_state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))

    final_reply = MailEnvelope(
        message_id="<reply-final@example.com>",
        subject="Re: [QUESTION] Demo task",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T12:08:00",
        in_reply_to=second_state["latest_message_id"],
        references=[second_state["root_message_id"], second_state["latest_message_id"]],
        body_text="Answers:\nphase2_k9_support: thunderbird_only\nphase2_device_validation: acceptable",
        raw_headers={"Subject": "Re: [QUESTION] Demo task"},
    )
    client.set_envelopes([final_reply])
    third_stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    third_state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    latest_snapshot = json.loads(
        (tmp_path / "tasks" / "thread_001" / third_state["last_task_snapshot_file"]).read_text(encoding="utf-8")
    )

    assert first_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert second_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert third_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert first_state["status"] == "awaiting_user_input"
    assert len(first_state["pending_questions"]) == 4
    assert second_state["status"] == "awaiting_user_input"
    assert [item["question_id"] for item in second_state["collected_answers"]] == [
        "phase2_entry_position",
        "phase2_icon_strings",
    ]
    assert third_state["status"] == "done"
    assert adapter.tasks[-1].run_mode == "resume"
    assert "Resolved answers for question set phase2:" in adapter.tasks[-1].turn_text
    assert "- phase2_device_validation: acceptable" in adapter.tasks[-1].turn_text
    assert "Resolved answers for question set phase2:" in latest_snapshot["task_text"]
