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


def test_extract_reply_delta_trims_chinese_original_message_block() -> None:
    body = "\n".join(
        [
            "你是谁？",
            "---原始邮件---",
            '发件人: "Task_runner"<jiangchun@tongji.edu.cn>',
            "发送时间: 2026年3月12日(周四) 晚上11:24",
            '收件人: "jiangchun"<jiangchun@tongji.edu.cn>;',
            "主题: [DONE][S:thread_013] remote_proj 测试",
            "Status: DONE",
        ]
    )

    assert extract_reply_delta(body) == "你是谁？"


def test_extract_reply_delta_trims_five_dash_chinese_original_message_block() -> None:
    body = "\n".join(
        [
            "扫描是否使用了everything工具？为什么我发现很多云盘的文件被拉下来了？",
            "",
            "-----原始邮件-----",
            '发件人:"jiangchun@tongji.edu.cn" <jiangchun@tongji.edu.cn>',
            "发送时间:2026-03-13 11:24:04 (星期五)",
            "收件人: Task_runner <jiangchun@tongji.edu.cn>",
            "主题: 回复：[DONE][S:thread_019] 看看文件结构",
            "",
            "kill",
        ]
    )

    assert extract_reply_delta(body) == "扫描是否使用了everything工具？为什么我发现很多云盘的文件被拉下来了？"


def test_extract_reply_delta_trims_inline_chinese_original_message_marker() -> None:
    body = "\n".join(
        [
            "刚才发生了什么？是否用了everything工具？为什么我看到云盘文件被拉下来了？ -----原始邮件-----",
            '发件人:"jiangchun@tongji.edu.cn" <jiangchun@tongji.edu.cn>',
            "发送时间:2026-03-13 11:04:08 (星期五)",
            "收件人: Task_runner <jiangchun@tongji.edu.cn>",
            "主题: 回复：[DONE][S:thread_019] 看看文件结构",
        ]
    )

    assert extract_reply_delta(body) == "刚才发生了什么？是否用了everything工具？为什么我看到云盘文件被拉下来了？"
