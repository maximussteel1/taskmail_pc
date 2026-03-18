"""Runtime mail path integration tests for CLI-backed adapters."""

from __future__ import annotations

import json

from mail_runner.adapters.codex_adapter import CodexAdapter
from mail_runner.config import AppConfig
from mail_runner.models import TaskSnapshot


def test_codex_demo_run_writes_runtime_mail_paths_and_attachment_index(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    workdir = repo_dir / "src"
    workdir.mkdir(parents=True)
    attachment = workdir / "_mailin_20260314_001__photo.png"
    attachment.write_bytes(b"png!")
    run_dir = tmp_path / "tasks" / "thread_001" / "runs" / "task_001"
    adapter = CodexAdapter(
        AppConfig(
            codex_command="demo",
            mock_sleep_seconds=0.0,
        )
    )
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="codex",
        profile=None,
        repo_path=str(repo_dir),
        workdir="src",
        task_text="Inspect the attachment.",
        acceptance=[],
        timeout_minutes=30,
        mode="analysis_only",
        attachments=[str(attachment)],
        created_at="2026-03-14T12:00:00",
        updated_at="2026-03-14T12:00:00",
    )

    result = adapter.run(snapshot, str(run_dir))

    prompt_text = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    attachments_index = json.loads((run_dir / "incoming_attachments.json").read_text(encoding="utf-8"))

    assert result.status == "success"
    assert result.artifacts_dir == "runs/task_001/artifacts"
    assert (run_dir / "artifacts").is_dir()
    assert "MAIL_RUNNER_ARTIFACTS_DIR" in prompt_text
    assert "MAIL_RUNNER_INCOMING_ATTACHMENTS_JSON" in prompt_text
    assert attachments_index == [{"path": str(attachment), "name": attachment.name}]
