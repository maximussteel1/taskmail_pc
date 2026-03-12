"""Reply quote trimming tests for Phase 3."""

from __future__ import annotations

from mail_runner.quote_extractor import extract_reply_delta


def test_extract_reply_delta_trims_common_quote_blocks() -> None:
    body = "\n".join(
        [
            "Please switch to analysis only.",
            "",
            "On Thu, Mar 12, 2026 at 12:00 PM Runner <runner@example.com> wrote:",
            "> Previous content",
        ]
    )

    assert extract_reply_delta(body) == "Please switch to analysis only."


def test_extract_reply_delta_removes_state_capsule_and_keeps_new_text() -> None:
    body = "\n".join(
        [
            "补充一点，这个脚本会被 report_main.py 调用。",
            "",
            "---TASK-STATE-BEGIN---",
            "thread_id: thread_001",
            "task_id: task_001",
            "---TASK-STATE-END---",
            "",
            "> older quoted content",
        ]
    )

    assert extract_reply_delta(body) == "补充一点，这个脚本会被 report_main.py 调用。"


def test_extract_reply_delta_removes_question_capsule() -> None:
    body = "\n".join(
        [
            "答案是：请同时更新两个模块。",
            "",
            "---TASK-QUESTION-BEGIN---",
            "question_id: q_001",
            "question_text: Should I update both files?",
            "choices: yes | no",
            "---TASK-QUESTION-END---",
        ]
    )

    assert extract_reply_delta(body) == "答案是：请同时更新两个模块。"
