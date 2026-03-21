"""Rule-based intent parsing tests for Phase 3."""

from __future__ import annotations

from mail_runner.models import QuestionItem, ThreadState
from mail_runner.intent_parser import parse_action
from mail_runner.status import BACKEND_OPENCODE, THREAD_STATUS_AWAITING_USER_INPUT, THREAD_STATUS_PAUSED


def test_parse_action_identifies_explicit_status_rerun_and_kill_commands() -> None:
    assert parse_action({"reply_delta": "/status"}).action == "STATUS_QUERY"
    assert parse_action({"reply_delta": "/last"}).action == "LAST_RESULT_QUERY"
    assert parse_action({"reply_delta": "/restart-runner"}).action == "RESTART_RUNNER"
    assert parse_action({"reply_delta": "/rerun"}).action == "RERUN"
    assert parse_action({"reply_delta": "/kill"}).action == "KILL"


def test_parse_action_does_not_guess_control_commands_from_natural_language() -> None:
    assert parse_action({"reply_delta": "现在状态如何？"}).action == "CONTINUE_SESSION"
    assert parse_action({"reply_delta": "请重新跑一次"}).action == "CONTINUE_SESSION"
    assert parse_action({"reply_delta": "终止当前任务"}).action == "CONTINUE_SESSION"


def test_parse_action_defaults_plain_reply_to_continue_session() -> None:
    action = parse_action(
        {
            "reply_delta": "Timeout: 120\nMode: analysis_only\nProfile: strong\nPermission: highest\nTask:\nOnly analyze the issue."
        }
    )

    assert action.action == "CONTINUE_SESSION"
    assert action.timeout_minutes == 120
    assert action.mode == "analysis_only"
    assert action.profile == "strong"
    assert action.permission == "highest"
    assert action.task_text_delta == "Only analyze the issue."


def test_parse_action_defaults_free_text_reply_to_continue_session() -> None:
    action = parse_action({"reply_delta": "补充一点，这个脚本会被 report_main.py 调用。"})

    assert action.action == "CONTINUE_SESSION"
    assert action.raw_user_text == "补充一点，这个脚本会被 report_main.py 调用。"


def test_parse_action_does_not_treat_multi_sentence_status_word_reply_as_status_query() -> None:
    action = parse_action(
        {
            "reply_delta": "在刚才的工作中，你有重新拉起服务吗？我现在正在使用邮件和你对话，你感觉目前的工作状态正常吗？"
        }
    )

    assert action.action == "CONTINUE_SESSION"


def test_parse_action_does_not_treat_long_product_question_with_progress_word_as_status_query() -> None:
    action = parse_action(
        {
            "reply_delta": (
                "那么具体到这个项目，也就是说在我打开tasks这个页面的时候，可以让它以十秒的频率去拉取吗？"
                "即使不能十秒，当我打开这个页面的时候，意味着我关注这个任务的进展最多，我也希望可以支持一分钟一次的自动刷新"
            )
        }
    )

    assert action.action == "CONTINUE_SESSION"


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


def test_parse_action_supports_continue_command_with_target_session() -> None:
    action = parse_action(
        {
            "reply_delta": "/continue thread_002\nPermission: highest\nTimeout: 120\nTask:\nOnly analyze the issue."
        }
    )

    assert action.action == "CONTINUE_SESSION"
    assert action.target_session_id == "thread_002"
    assert action.permission == "highest"
    assert action.timeout_minutes == 120
    assert action.task_text_delta == "Only analyze the issue."


def test_parse_action_supports_last_command_with_target_session() -> None:
    action = parse_action({"reply_delta": "/last thread_002"})

    assert action.action == "LAST_RESULT_QUERY"
    assert action.target_session_id == "thread_002"


def test_parse_action_supports_resume_with_structured_permission_override() -> None:
    resume = parse_action({"reply_delta": "/resume\nPermission: highest\nPlease continue with the cleanup."})

    assert resume.action == "CONTINUE_SESSION"
    assert resume.permission == "highest"


def test_parse_action_does_not_treat_plain_use_the_phrase_as_profile() -> None:
    action = parse_action(
        {
            "reply_delta": "/resume\nPermission: highest\nUse the real backend permissions of this run. Do not ask for approval."
        }
    )

    assert action.action == "CONTINUE_SESSION"
    assert action.permission == "highest"
    assert action.profile is None


def test_parse_action_supports_pause_command() -> None:
    paused = parse_action({"reply_delta": "/pause"})

    assert paused.action == "PAUSE_SESSION"


def test_parse_action_supports_end_command() -> None:
    ended = parse_action({"reply_delta": "/end"})

    assert ended.action == "END_SESSION"


def test_parse_action_treats_resume_as_explicit_resume_when_thread_is_paused() -> None:
    thread_state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<paused@example.com>",
        subject_norm="demo task",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_PAUSED,
        paused_from_status="done",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": "/resume\nPlease continue with the cleanup.",
            "thread_state": thread_state,
        }
    )

    assert action.action == "RESUME_SESSION"
    assert action.raw_user_text == "Please continue with the cleanup."


def test_parse_action_does_not_confuse_skill_with_kill() -> None:
    action = parse_action({"reply_delta": "你是否能看一下你有什么skill以及你能动用哪些工具，关键是我想知道你是否具有联网搜索的能力"})

    assert action.action == "CONTINUE_SESSION"


def test_parse_action_requires_structured_answers_for_multiple_pending_questions() -> None:
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
        pending_question_set_id="phase2",
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
        ],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": "我觉得放在账户列表下方，然后字符串你提供。",
            "thread_state": thread_state,
            "pending_questions": thread_state.pending_questions,
        }
    )

    assert action.action == "UNKNOWN"


def test_parse_action_parses_structured_multi_question_answers() -> None:
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
        pending_question_set_id="phase2",
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
        ],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": "Answers:\nphase2_entry_position: 账户列表下方\nphase2_icon_strings: provide",
            "thread_state": thread_state,
            "pending_questions": thread_state.pending_questions,
        }
    )

    assert action.action == "ANSWER_QUESTION"
    assert action.used_structured_answers is True
    assert [item.value for item in action.question_answers] == ["below", "provide"]


def test_parse_action_treats_attachment_only_reply_as_continue_session() -> None:
    action = parse_action({"reply_delta": "", "incoming_attachments": [{"filename": "photo.png"}]})

    assert action.action == "CONTINUE_SESSION"


def test_parse_action_treats_attachment_only_reply_as_answer_when_waiting() -> None:
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
        pending_question_text="Please upload the screenshot.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": "",
            "thread_state": thread_state,
            "incoming_attachments": [{"filename": "photo.png"}],
        }
    )

    assert action.action == "ANSWER_QUESTION"


def test_parse_action_accepts_real_reply_style_without_answers_heading() -> None:
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
        pending_question_set_id="phase2",
        pending_questions=[
            QuestionItem(
                question_set_id="phase2",
                question_id="phase2_entry_position",
                question_type="single_choice",
                question_text="Where should the entry go?",
                choices=["top", "below", "section"],
                choice_labels={"top": "账户列表上方", "below": "账户列表下方（设置附近）", "section": "作为独立分区"},
            ),
            QuestionItem(
                question_set_id="phase2",
                question_id="phase2_icon_strings",
                question_type="single_choice",
                question_text="Who provides strings?",
                choices=["provide", "reuse", "placeholder"],
                choice_labels={"provide": "你提供", "reuse": "复用现有", "placeholder": "暂用占位符"},
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
                choice_labels={"acceptable": "可接受", "device_required": "必须在设备上验证"},
            ),
        ],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": (
                "question_id: phase2_entry_position\n"
                " 账户列表下方（设置附近）\n"
                "question_id: phase2_icon_strings\n"
                "你提供\n"
                "question_id: phase2_k9_support\n"
                "仅 Thunderbird\n"
                "question_id: phase2_device_validation\n"
                "可接受"
            ),
            "thread_state": thread_state,
            "pending_questions": thread_state.pending_questions,
        }
    )

    assert action.action == "ANSWER_QUESTION"
    assert [item.question_id for item in action.question_answers] == [
        "phase2_entry_position",
        "phase2_icon_strings",
        "phase2_k9_support",
        "phase2_device_validation",
    ]
    assert [item.value for item in action.question_answers] == [
        "below",
        "provide",
        "thunderbird_only",
        "acceptable",
    ]


def test_parse_action_allows_resume_with_answer_for_paused_question_thread() -> None:
    thread_state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<paused-question@example.com>",
        subject_norm="demo task",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_PAUSED,
        paused_from_status=THREAD_STATUS_AWAITING_USER_INPUT,
        pending_question_id="question_task_001",
        pending_question_text="Should I update both files?",
        pending_choices=["yes", "no"],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:05:00",
    )

    action = parse_action(
        {
            "reply_delta": "/resume\nYes, update both files.",
            "thread_state": thread_state,
        }
    )

    assert action.action == "ANSWER_QUESTION"
    assert action.raw_user_text == "Yes, update both files."
