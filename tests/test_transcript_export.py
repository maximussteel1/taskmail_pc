from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from mail_runner.state_capsule import render_state_capsule
from mail_runner.thread_store import save_raw_mail
from mail_runner.transcript_export import build_thread_transcript, render_transcript_markdown


def test_build_thread_transcript_extracts_user_and_assistant_turns(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_id = "thread_001"
    capsule = render_state_capsule({"thread_id": thread_id})

    save_raw_mail(
        thread_id,
        {
            "message_id": "<user-1@example.com>",
            "subject": "Re: [DONE][S:thread_001] Demo",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2026-03-13T09:00:00",
            "body_text": f"请继续整理日志目录。\n\n{capsule}",
            "raw_headers": {},
        },
        task_root,
    )
    save_raw_mail(
        thread_id,
        {
            "message_id": "<assistant-1@example.com>",
            "subject": "[DONE][S:thread_001] Demo",
            "from_addr": "runner@example.com",
            "to_addr": "user@example.com",
            "date": "2026-03-13T09:01:00",
            "body_text": (
                "Status: DONE\n"
                "Session ID: thread_001\n"
                "Thread ID: thread_001\n"
                "Task ID: task_001\n"
                "Backend: opencode\n"
                "Repo: E:\\repo\n"
                "Workdir: src\n"
                "\n"
                "Reply:\n"
                "已经整理完日志目录，并补了说明。\n"
                "\n"
                f"{capsule}\n"
            ),
            "raw_headers": {SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
        },
        task_root,
    )

    turns = build_thread_transcript(thread_id, task_root)

    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].content == "请继续整理日志目录。"
    assert turns[1].status == "DONE"
    assert turns[1].content == "已经整理完日志目录，并补了说明。"

    rendered = render_transcript_markdown(thread_id, turns)
    assert "# Thread Transcript: thread_001" in rendered
    assert "## 001. User" in rendered
    assert "## 002. Assistant [DONE]" in rendered


def test_build_thread_transcript_uses_system_intro_when_reply_block_missing(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_id = "thread_002"

    save_raw_mail(
        thread_id,
        {
            "message_id": "<assistant-2@example.com>",
            "subject": "[STATUS][S:thread_002] Demo",
            "from_addr": "runner@example.com",
            "to_addr": "user@example.com",
            "date": "2026-03-13T09:05:00",
            "body_text": (
                "No running task is available to kill for this thread.\n\n"
                "Status: STATUS\n"
                "Session ID: thread_002\n"
                "Thread ID: thread_002\n"
            ),
            "raw_headers": {SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
        },
        task_root,
    )

    turns = build_thread_transcript(thread_id, task_root)

    assert len(turns) == 1
    assert turns[0].role == "assistant"
    assert turns[0].status == "STATUS"
    assert turns[0].content == "No running task is available to kill for this thread."


def test_export_thread_conversation_script_writes_markdown(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    output_path = tmp_path / "conversation.md"
    thread_id = "thread_003"

    save_raw_mail(
        thread_id,
        {
            "message_id": "<user-3@example.com>",
            "subject": "Re: [DONE][S:thread_003] Demo",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2026-03-13T09:10:00",
            "body_text": "帮我把这轮对话导出来。",
            "raw_headers": {},
        },
        task_root,
    )

    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_thread_conversation.py",
            thread_id,
            "--task-root",
            str(task_root),
            "--output",
            str(output_path),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    rendered = output_path.read_text(encoding="utf-8")
    assert "Thread Transcript: thread_003" in rendered
    assert "帮我把这轮对话导出来。" in rendered
