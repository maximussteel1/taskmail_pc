"""Codex transport routing tests."""

from __future__ import annotations

from dataclasses import dataclass

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.adapters.codex_routing_adapter import CodexRoutingAdapter
from mail_runner.models import RunResult, TaskSnapshot


def _snapshot(*, transport: str) -> TaskSnapshot:
    return TaskSnapshot(
        task_id=f"task_{transport}",
        thread_id="thread_001",
        backend="codex",
        repo_path="D:\\repo",
        workdir=None,
        task_text="Inspect the repository.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-18T10:00:00",
        updated_at="2026-03-18T10:00:00",
        backend_transport=transport,
    )


@dataclass
class _RecordingAdapter(WorkerAdapter):
    name: str
    calls: list[str]

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.calls.append(f"run:{self.name}:{task.task_id}")
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status="success",
            exit_code=0,
            started_at="2026-03-18T10:00:01",
            finished_at="2026-03-18T10:00:02",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            backend_transport=task.backend_transport,
        )

    def kill(self, task_id: str) -> bool:
        self.calls.append(f"kill:{self.name}:{task_id}")
        return True


def test_codex_routing_adapter_selects_cli_or_sdk_by_transport() -> None:
    cli_calls: list[str] = []
    sdk_calls: list[str] = []
    adapter = CodexRoutingAdapter(
        cli_adapter=_RecordingAdapter("cli", cli_calls),
        sdk_adapter=_RecordingAdapter("sdk", sdk_calls),
    )

    cli_result = adapter.run(_snapshot(transport="cli"), "run-cli")
    sdk_result = adapter.run(_snapshot(transport="sdk"), "run-sdk")

    assert cli_calls == ["run:cli:task_cli"]
    assert sdk_calls == ["run:sdk:task_sdk"]
    assert cli_result.backend_transport == "cli"
    assert sdk_result.backend_transport == "sdk"
