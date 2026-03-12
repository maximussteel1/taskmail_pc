"""Mock adapter tests for Phase 1."""

from __future__ import annotations

from mail_runner.adapters.mock_adapter import MockAdapter, SUMMARY_LINE
from mail_runner.models import TaskSnapshot
from mail_runner.status import BACKEND_CODEX, RUN_STATUS_SUCCESS


def test_mock_adapter_writes_prompt_logs_and_summary(tmp_path) -> None:
    task = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_CODEX,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Understand the current structure.",
        acceptance=["produce summary"],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T11:30:00",
        updated_at="2026-03-12T11:30:00",
    )
    run_dir = tmp_path / "tasks" / "thread_001" / "runs" / "task_001"

    result = MockAdapter(sleep_seconds=0).run(task, str(run_dir))

    assert result.status == RUN_STATUS_SUCCESS
    assert (run_dir / "prompt.txt").exists()
    assert (run_dir / "stdout.log").exists()
    assert (run_dir / "stderr.log").exists()
    assert (run_dir / "summary.md").read_text(encoding="utf-8").splitlines()[0] == SUMMARY_LINE
    assert result.summary_file == "runs/task_001/summary.md"
