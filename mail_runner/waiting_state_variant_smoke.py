"""Standalone fixture smoke for multi-question waiting-state variants."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters.base import WorkerAdapter
from .app import process_once
from .config import AppConfig
from .dispatcher import Dispatcher
from .models import MailEnvelope, QuestionItem, RunResult, TaskSnapshot, ThreadState
from .question_utils import effective_pending_questions
from .status import (
    BACKEND_OPENCODE,
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_DONE,
    THREAD_STATUS_PAUSED,
)
from .thread_store import build_workspace_id, load_session_state, load_thread_state

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_waiting_state_variant_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class FakeMailClient:
    def __init__(self, envelopes: list[MailEnvelope]) -> None:
        self._envelopes = list(envelopes)
        self.sent_messages: list[dict[str, Any]] = []
        self._sent_count = 0

    def set_envelopes(self, envelopes: list[MailEnvelope]) -> None:
        self._envelopes = list(envelopes)

    def fetch_unseen_messages(self) -> list[MailEnvelope]:
        return list(self._envelopes)

    def send_mail(self, **kwargs: Any) -> str:
        self.sent_messages.append(kwargs)
        self._sent_count += 1
        return f"<sent-{self._sent_count}@example.com>"


class MultiQuestionPauseResumeAdapter(WorkerAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.tasks: list[TaskSnapshot] = []

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.calls += 1
        self.tasks.append(task)
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "prompt.txt").write_text("prompt\n", encoding="utf-8")
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
                started_at="2026-03-25T03:00:01",
                finished_at="2026-03-25T03:00:05",
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
                backend_transport=task.backend_transport,
            )
        (run_path / "stdout.log").write_text("Completed successfully.\n", encoding="utf-8")
        (run_path / "summary.md").write_text("Completed successfully.\n", encoding="utf-8")
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-25T03:10:01",
            finished_at="2026-03-25T03:10:05",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=None,
            backend_session_id="native-session-001",
            backend_session_resumable=True,
            backend_transport=task.backend_transport,
        )

    def kill(self, task_id: str) -> bool:
        return False


def _mail_envelope(
    *,
    message_id: str,
    subject: str,
    body_text: str,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> MailEnvelope:
    return MailEnvelope(
        message_id=message_id,
        subject=subject,
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-25T03:00:00",
        in_reply_to=in_reply_to,
        references=list(references or []),
        body_text=body_text,
        raw_headers={"Subject": subject},
    )


def _state_record(task_root: Path, step_name: str) -> dict[str, Any]:
    thread_state = load_thread_state("thread_001", task_root)
    session_state = load_session_state(build_workspace_id("D:\\repo", None), "thread_001", task_root)
    latest_snapshot = json.loads((task_root / "thread_001" / thread_state.last_task_snapshot_file).read_text(encoding="utf-8"))
    return {
        "step": step_name,
        "thread_state": asdict(thread_state),
        "session_state": asdict(session_state),
        "latest_snapshot": latest_snapshot,
        "effective_pending_question_ids": [
            item.question_id for item in effective_pending_questions(thread_state, fallback_task_id=thread_state.current_task_id)
        ],
    }


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run_waiting_state_variant_smoke(*, output_dir: Path, run_name: str) -> dict[str, Any]:
    run_root = output_dir / run_name
    task_root = run_root / "tasks"
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    adapter = MultiQuestionPauseResumeAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    client = FakeMailClient(
        [
            _mail_envelope(
                message_id="<root@example.com>",
                subject="[OC] Demo task",
                body_text="Repo: D:\\repo\nTask:\nInspect both modules.\n",
            )
        ]
    )
    smoke_result: dict[str, Any] = {
        "success": False,
        "run_name": run_name,
        "task_root": str(task_root),
        "steps": {},
        "sent_subjects": [],
        "cleanup": {
            "required": False,
            "cleanup_ok": True,
            "reason": "fixture smoke; no external process or listener is started",
        },
        "failures": [],
    }
    failures: list[str] = []

    first_stats = process_once(config, base_dir=run_root, mail_client=client, dispatcher=dispatcher)
    first_record = _state_record(task_root, "first_question")
    smoke_result["steps"]["first_question"] = {
        "stats": first_stats,
        **first_record,
    }
    first_thread = ThreadState(**first_record["thread_state"])
    _assert(first_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}, "Unexpected first-step stats.", failures)
    _assert(first_thread.status == THREAD_STATUS_AWAITING_USER_INPUT, "Initial run did not enter awaiting_user_input.", failures)
    _assert(len(first_thread.pending_questions) == 4, "Initial waiting state did not persist 4 pending questions.", failures)
    _assert(adapter.calls == 1, "Initial run adapter call count mismatch.", failures)

    partial_reply = _mail_envelope(
        message_id="<reply-partial@example.com>",
        subject="Re: [QUESTION] Demo task",
        body_text="Answers:\nphase2_entry_position: below\nphase2_icon_strings: provide",
        in_reply_to=first_thread.latest_message_id,
        references=[first_thread.root_message_id, first_thread.latest_message_id],
    )
    client.set_envelopes([partial_reply])
    partial_stats = process_once(config, base_dir=run_root, mail_client=client, dispatcher=dispatcher)
    partial_record = _state_record(task_root, "partial_answers")
    smoke_result["steps"]["partial_answers"] = {
        "stats": partial_stats,
        **partial_record,
    }
    partial_thread = ThreadState(**partial_record["thread_state"])
    _assert(partial_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}, "Unexpected partial-answer stats.", failures)
    _assert(partial_thread.status == THREAD_STATUS_AWAITING_USER_INPUT, "Partial answers should keep awaiting_user_input.", failures)
    _assert(
        [item["question_id"] for item in partial_record["thread_state"]["collected_answers"]]
        == ["phase2_entry_position", "phase2_icon_strings"],
        "Partial answers were not persisted in canonical order.",
        failures,
    )
    _assert(adapter.calls == 1, "Partial answers should not trigger a backend rerun.", failures)

    pause_reply = _mail_envelope(
        message_id="<pause@example.com>",
        subject="Re: [QUESTION][S:thread_001] Demo task",
        body_text="/pause",
        in_reply_to=partial_thread.latest_message_id,
        references=[partial_thread.root_message_id, partial_thread.latest_message_id],
    )
    client.set_envelopes([pause_reply])
    pause_stats = process_once(config, base_dir=run_root, mail_client=client, dispatcher=dispatcher)
    pause_record = _state_record(task_root, "pause")
    smoke_result["steps"]["pause"] = {
        "stats": pause_stats,
        **pause_record,
    }
    paused_thread = ThreadState(**pause_record["thread_state"])
    _assert(pause_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}, "Unexpected pause stats.", failures)
    _assert(paused_thread.status == THREAD_STATUS_PAUSED, "Pause did not switch thread to paused.", failures)
    _assert(
        paused_thread.paused_from_status == THREAD_STATUS_AWAITING_USER_INPUT,
        "Pause did not preserve paused_from_status=awaiting_user_input.",
        failures,
    )
    _assert(adapter.calls == 1, "Pause should not trigger a backend rerun.", failures)

    resume_without_answer = _mail_envelope(
        message_id="<resume-no-answer@example.com>",
        subject="Re: [PAUSED][S:thread_001] Demo task",
        body_text="/resume",
        in_reply_to=paused_thread.latest_message_id,
        references=[paused_thread.root_message_id, paused_thread.latest_message_id],
    )
    client.set_envelopes([resume_without_answer])
    resume_without_answer_stats = process_once(config, base_dir=run_root, mail_client=client, dispatcher=dispatcher)
    resume_without_answer_record = _state_record(task_root, "resume_without_answer")
    smoke_result["steps"]["resume_without_answer"] = {
        "stats": resume_without_answer_stats,
        **resume_without_answer_record,
    }
    reopened_thread = ThreadState(**resume_without_answer_record["thread_state"])
    _assert(
        resume_without_answer_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0},
        "Unexpected resume-without-answer stats.",
        failures,
    )
    _assert(
        reopened_thread.status == THREAD_STATUS_AWAITING_USER_INPUT,
        "Resume without answer should reopen the waiting question state.",
        failures,
    )
    _assert(reopened_thread.paused_from_status is None, "Resume without answer should clear paused_from_status.", failures)
    _assert(adapter.calls == 1, "Resume without answer should not trigger a backend rerun.", failures)

    pause_again_reply = _mail_envelope(
        message_id="<pause-again@example.com>",
        subject="Re: [QUESTION][S:thread_001] Demo task",
        body_text="/pause",
        in_reply_to=reopened_thread.latest_message_id,
        references=[reopened_thread.root_message_id, reopened_thread.latest_message_id],
    )
    client.set_envelopes([pause_again_reply])
    pause_again_stats = process_once(config, base_dir=run_root, mail_client=client, dispatcher=dispatcher)
    pause_again_record = _state_record(task_root, "pause_again")
    smoke_result["steps"]["pause_again"] = {
        "stats": pause_again_stats,
        **pause_again_record,
    }
    paused_again_thread = ThreadState(**pause_again_record["thread_state"])
    _assert(pause_again_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}, "Unexpected second pause stats.", failures)
    _assert(paused_again_thread.status == THREAD_STATUS_PAUSED, "Second pause did not switch thread to paused.", failures)
    _assert(adapter.calls == 1, "Second pause should not trigger a backend rerun.", failures)

    final_resume_reply = _mail_envelope(
        message_id="<resume-final@example.com>",
        subject="Re: [PAUSED][S:thread_001] Demo task",
        body_text=(
            "/resume\n"
            "Answers:\n"
            "phase2_k9_support: thunderbird_only\n"
            "phase2_device_validation: acceptable"
        ),
        in_reply_to=paused_again_thread.latest_message_id,
        references=[paused_again_thread.root_message_id, paused_again_thread.latest_message_id],
    )
    client.set_envelopes([final_resume_reply])
    final_stats = process_once(config, base_dir=run_root, mail_client=client, dispatcher=dispatcher)
    final_record = _state_record(task_root, "resume_with_answers")
    smoke_result["steps"]["resume_with_answers"] = {
        "stats": final_stats,
        **final_record,
        "adapter_task_run_modes": [item.run_mode for item in adapter.tasks],
        "adapter_turn_texts": [item.turn_text for item in adapter.tasks],
    }
    final_thread = ThreadState(**final_record["thread_state"])
    _assert(final_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}, "Unexpected final resume stats.", failures)
    _assert(final_thread.status == THREAD_STATUS_DONE, "Resume with answers did not finish the thread.", failures)
    _assert(final_thread.pending_questions == [], "Final thread still has pending questions.", failures)
    _assert(final_thread.collected_answers == [], "Final thread should clear collected answers.", failures)
    _assert(adapter.calls == 2, "Final resume with answers should trigger exactly one more backend run.", failures)
    _assert(adapter.tasks[-1].run_mode == "resume", "Final backend run did not use resume mode.", failures)
    _assert(
        adapter.tasks[-1].backend_session_id == "native-session-001",
        "Final backend run did not reuse the persisted backend_session_id.",
        failures,
    )
    _assert(
        "Resolved answers for question set phase2:" in (adapter.tasks[-1].turn_text or ""),
        "Final backend turn_text is missing canonical answer summary.",
        failures,
    )
    _assert(
        "- phase2_device_validation: acceptable" in (adapter.tasks[-1].turn_text or ""),
        "Final backend turn_text is missing the final canonical answer.",
        failures,
    )
    _assert(
        "Resolved answers for question set phase2:" in final_record["latest_snapshot"]["task_text"],
        "Final snapshot task_text is missing canonical answer context.",
        failures,
    )
    _assert(
        final_record["session_state"]["status"] == THREAD_STATUS_DONE,
        "Final session state did not converge to done.",
        failures,
    )

    smoke_result["sent_subjects"] = [item.get("subject") for item in client.sent_messages]
    smoke_result["failures"] = failures
    smoke_result["success"] = not failures
    smoke_result_path = run_root / "smoke_result.json"
    smoke_result["smoke_result_path"] = str(smoke_result_path)
    _write_json(smoke_result_path, smoke_result)
    return smoke_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixture smoke for waiting-state variants.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"waiting-state-variant-smoke-{_timestamp_slug()}"
    result = run_waiting_state_variant_smoke(output_dir=Path(args.output_dir), run_name=run_name)
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
