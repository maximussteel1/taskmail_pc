"""Adapter protocol for backend execution."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import RunResult, TaskSnapshot


class WorkerAdapter(ABC):
    """Common interface for all execution backends."""

    @abstractmethod
    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        """Run a task snapshot inside the provided directory."""

    @abstractmethod
    def kill(self, task_id: str) -> bool:
        """Attempt to stop a running task."""
