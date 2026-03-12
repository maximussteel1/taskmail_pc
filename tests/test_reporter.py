"""Reporter tests."""

from __future__ import annotations

from mail_runner.models import RunResult, TaskSnapshot, ThreadState
from mail_runner.reporter import (
    MAIL_STATUS_DONE,
    MAIL_STATUS_FAILED,
    MAIL_STATUS_QUESTION,
    build_status_mail,
    build_status_subject,
)
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_FAILED, RUN_STATUS_SUCCESS, THREAD_STATUS_DONE, THREAD_STATUS_FAILED


def test_build_status_subject_and_mail_include_capsule() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        last_summary="Completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    result = RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-12T12:00:01",
        finished_at="2026-03-12T12:00:05",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir=None,
        changed_files=[],
        tests_passed=None,
        error_message=None,
    )

    subject = build_status_subject(MAIL_STATUS_DONE, "Demo task")
    body = build_status_mail(MAIL_STATUS_DONE, state, task_snapshot=snapshot, result=result)

    assert subject == "[DONE] Demo task"
    assert "Status: DONE" in body
    assert "Summary: Completed successfully." in body
    assert "---TASK-STATE-BEGIN---" in body
    assert "task_id: task_001" in body


def test_build_failed_status_mail_includes_user_error_message() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_002",
        last_task_snapshot_file="snapshots/task_002.json",
        status=THREAD_STATUS_FAILED,
        history_files=["runs/task_002/result.json"],
        last_summary="attempt to write a readonly database",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:10:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_002",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect",
        acceptance=[],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    result = RunResult(
        task_id="task_002",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_FAILED,
        exit_code=1,
        started_at="2026-03-12T12:05:00",
        finished_at="2026-03-12T12:10:00",
        stdout_file="runs/task_002/stdout.log",
        stderr_file="runs/task_002/stderr.log",
        summary_file="runs/task_002/summary.md",
        artifacts_dir=None,
        changed_files=[],
        tests_passed=None,
        error_message="attempt to write a readonly database",
    )

    body = build_status_mail(MAIL_STATUS_FAILED, state, task_snapshot=snapshot, result=result)

    assert "Status: FAILED" in body
    assert "Error: attempt to write a readonly database" in body
    assert "Summary: attempt to write a readonly database" in body


def test_build_question_mail_includes_question_capsule() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<question@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_003",
        last_task_snapshot_file="snapshots/task_003.json",
        status="awaiting_user_input",
        history_files=["runs/task_003/result.json"],
        last_summary="Should I update both files?",
        pending_question_id="question_task_003",
        pending_question_text="Should I update both files?",
        pending_choices=["yes", "no"],
        awaiting_since="2026-03-12T12:15:00",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:15:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_003",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect",
        acceptance=[],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    body = build_status_mail(MAIL_STATUS_QUESTION, state, task_snapshot=snapshot)

    assert "Status: QUESTION" in body
    assert "Question: Should I update both files?" in body
    assert "---TASK-QUESTION-BEGIN---" in body
