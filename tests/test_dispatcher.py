"""Dispatcher routing tests for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import RunResult, TaskSnapshot
from mail_runner.status import BACKEND_CODEX, BACKEND_OPENCODE, RUN_STATUS_SUCCESS


def _snapshot(backend: str) -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=backend,
        repo_path="D:\\repo",
        workdir=None,
        task_text="Analyze the repo.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T11:25:00",
        updated_at="2026-03-12T11:25:00",
    )


@dataclass
class RecordingAdapter(WorkerAdapter):
    name: str
    calls: list[str]

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.calls.append(f"{self.name}:{task.backend}:{run_dir}")
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-12T11:25:01",
            finished_at="2026-03-12T11:25:02",
            stdout_file="runs/task_001/stdout.log",
            stderr_file="runs/task_001/stderr.log",
            summary_file="runs/task_001/summary.md",
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=None,
        )

    def kill(self, task_id: str) -> bool:
        return False


def test_dispatcher_routes_by_backend() -> None:
    op_calls: list[str] = []
    cx_calls: list[str] = []
    dispatcher = Dispatcher(
        opencode_adapter=RecordingAdapter("op", op_calls),
        codex_adapter=RecordingAdapter("cx", cx_calls),
    )

    dispatcher.dispatch(_snapshot(BACKEND_OPENCODE), "run-op")
    dispatcher.dispatch(_snapshot(BACKEND_CODEX), "run-cx")

    assert op_calls == ["op:opencode:run-op"]
    assert cx_calls == ["cx:codex:run-cx"]
