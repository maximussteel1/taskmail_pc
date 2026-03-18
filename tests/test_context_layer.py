"""Context assembly tests for Phase 3."""

from __future__ import annotations

from dataclasses import asdict

from mail_runner.context_layer import build_context
from mail_runner.models import MailAttachment, MailEnvelope, RunResult, TaskSnapshot, ThreadState
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS, THREAD_STATUS_DONE
from mail_runner.workspace import WorkspaceManager


def test_build_context_loads_latest_snapshot_result_and_reply_delta(tmp_path) -> None:
    workspace = WorkspaceManager(tmp_path / "tasks")
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile="fast",
        repo_path="D:\\repo",
        workdir="src",
        task_text="Original task text",
        acceptance=["pytest passes"],
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
    workspace.save_snapshot(snapshot)
    workspace.save_run_result("thread_001", "task_001", result)
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        profile="fast",
        history_files=["runs/task_001/result.json"],
        last_summary="Completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )
    workspace.save_json(workspace.thread_state_path("thread_001"), asdict(state))
    envelope = MailEnvelope(
        message_id="<reply@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="补充一点，这个脚本会被 report_main.py 调用。\n\n> old quote",
    )

    context = build_context(envelope, state, tmp_path / "tasks")

    assert context["latest_snapshot"].task_id == "task_001"
    assert context["latest_result"].status == RUN_STATUS_SUCCESS
    assert context["reply_delta"] == "补充一点，这个脚本会被 report_main.py 调用。"
    assert context["capsule_state"] is None
    assert context["pending_question"]["question_text"] is None
def test_build_context_exposes_incoming_attachment_paths(tmp_path) -> None:
    workspace = WorkspaceManager(tmp_path / "tasks")
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile="fast",
        repo_path="D:\\repo",
        workdir="src",
        task_text="Original task text",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    workspace.save_snapshot(snapshot)
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        profile="fast",
        history_files=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )
    workspace.save_json(workspace.thread_state_path("thread_001"), asdict(state))
    envelope = MailEnvelope(
        message_id="<reply@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T12:10:00",
        body_text="See attachment.",
        attachments=[
            MailAttachment(
                filename="photo.png",
                content_type="image/png",
                size_bytes=4,
                saved_path="E:\\repo\\_mailin_20260314_001__photo.png",
                content_bytes=b"png!",
            )
        ],
    )

    context = build_context(envelope, state, tmp_path / "tasks")

    assert context["incoming_attachment_paths"] == ["E:\\repo\\_mailin_20260314_001__photo.png"]
    assert context["incoming_attachment_summary"][0] == "New incoming attachments materialized in workdir:"
