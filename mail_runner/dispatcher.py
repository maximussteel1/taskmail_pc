"""Adapter dispatching."""

from __future__ import annotations

from .adapters.base import WorkerAdapter
from .models import RunResult, TaskSnapshot
from .status import BACKEND_CODEX, BACKEND_OPENCODE


class Dispatcher:
    """Selects a backend adapter for a task snapshot."""

    def __init__(self, opencode_adapter: WorkerAdapter, codex_adapter: WorkerAdapter) -> None:
        self._opencode_adapter = opencode_adapter
        self._codex_adapter = codex_adapter

    def dispatch(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        if task.backend == BACKEND_OPENCODE:
            return self._opencode_adapter.run(task, run_dir)
        if task.backend == BACKEND_CODEX:
            return self._codex_adapter.run(task, run_dir)
        raise ValueError(f"Unsupported backend: {task.backend}")

    def kill(self, backend: str, task_id: str) -> bool:
        if backend == BACKEND_OPENCODE:
            return self._opencode_adapter.kill(task_id)
        if backend == BACKEND_CODEX:
            return self._codex_adapter.kill(task_id)
        raise ValueError(f"Unsupported backend: {backend}")
