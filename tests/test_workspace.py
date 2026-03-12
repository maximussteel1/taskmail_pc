"""Workspace persistence tests for Phase 1."""

from __future__ import annotations

from mail_runner.models import RunResult, TaskSnapshot
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS
from mail_runner.workspace import WorkspaceManager


def _snapshot(thread_id: str = "thread_001", task_id: str = "task_001") -> TaskSnapshot:
    return TaskSnapshot(
        task_id=task_id,
        thread_id=thread_id,
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor the module.",
        acceptance=["pytest passes"],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T11:10:00",
        updated_at="2026-03-12T11:10:00",
    )


def _result(thread_id: str = "thread_001", task_id: str = "task_001") -> RunResult:
    return RunResult(
        task_id=task_id,
        thread_id=thread_id,
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-12T11:10:01",
        finished_at="2026-03-12T11:10:02",
        stdout_file=f"runs/{task_id}/stdout.log",
        stderr_file=f"runs/{task_id}/stderr.log",
        summary_file=f"runs/{task_id}/summary.md",
        artifacts_dir=None,
        changed_files=[],
        tests_passed=None,
        error_message=None,
    )


def test_workspace_creates_layout_and_saves_snapshot_and_result(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path / "tasks")

    thread_dir = manager.ensure_thread_layout("thread_001")
    snapshot_path = manager.save_snapshot(_snapshot())
    manager.create_run_dir("thread_001", "task_001")
    result_path = manager.save_run_result("thread_001", "task_001", _result())

    assert thread_dir.exists()
    assert (thread_dir / "snapshots").exists()
    assert (thread_dir / "runs").exists()
    assert (thread_dir / "mail").exists()
    assert snapshot_path.exists()
    assert result_path.exists()
    assert manager.to_thread_relative("thread_001", snapshot_path) == "snapshots/task_001.json"
    assert manager.to_thread_relative("thread_001", result_path) == "runs/task_001/result.json"
