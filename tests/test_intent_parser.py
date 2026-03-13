"""Rule-based intent parsing tests for Phase 3."""

from __future__ import annotations

from mail_runner.models import ThreadState
from mail_runner.intent_parser import parse_action
from mail_runner.status import BACKEND_OPENCODE, THREAD_STATUS_AWAITING_USER_INPUT


def test_parse_action_identifies_status_rerun_and_kill() -> None:
    assert parse_action({"reply_delta": "现在状态如何？"}).action == "STATUS_QUERY"
    assert parse_action({"reply_delta": "请重新跑一次"}).action == "RERUN"
    assert parse_action({"reply_delta": "终止当前任务"}).action == "KILL"


def test_parse_action_defaults_plain_reply_to_continue_session() -> None:
    action = parse_action({"reply_delta": "Timeout: 120\nMode: analysis_only\nProfile: strong\nTask:\nOnly analyze the issue."})

    assert action.action == "CONTINUE_SESSION"
    assert action.timeout_minutes == 120
    assert action.mode == "analysis_only"
    assert action.profile == "strong"
    assert action.task_text_delta == "Only analyze the issue."


def test_parse_action_defaults_free_text_reply_to_continue_session() -> None:
    action = parse_action({"reply_delta": "补充一点，这个脚本会被 report_main.py 调用。"})

    assert action.action == "CONTINUE_SESSION"
    assert action.raw_user_text == "补充一点，这个脚本会被 report_main.py 调用。"


def test_parse_action_uses_answer_question_in_waiting_state() -> None:
    thread_state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<question@example.com>",
        subject_norm="demo task",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_AWAITING_USER_INPUT,
        pending_question_id="question_task_001",
        pending_question_text="Should I update both files?",
        pending_choices=["yes", "no"],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": "Use strong.\nYes, update both files.",
            "thread_state": thread_state,
        }
    )

    assert action.action == "ANSWER_QUESTION"
    assert action.profile == "strong"


def test_parse_action_supports_resume_and_sessions_commands() -> None:
    resume = parse_action({"reply_delta": "/resume\nPlease continue with the cleanup."})
    sessions = parse_action({"reply_delta": "/sessions"})

    assert resume.action == "CONTINUE_SESSION"
    assert resume.raw_user_text == "Please continue with the cleanup."
    assert sessions.action == "LIST_SESSIONS"


def test_parse_action_does_not_confuse_skill_with_kill() -> None:
    action = parse_action({"reply_delta": "你是否能看一下你有什么skill以及你能动用哪些工具，关键是我想知道你是否具有联网搜索的能力"})

    assert action.action == "CONTINUE_SESSION"
