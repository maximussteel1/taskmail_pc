"""Routes Codex work between CLI and SDK transports."""

from __future__ import annotations

from threading import Lock

from ..status import BACKEND_TRANSPORT_SDK
from .base import WorkerAdapter


class CodexRoutingAdapter(WorkerAdapter):
    """Selects the Codex transport for each task and remembers active ownership for kill."""

    def __init__(self, cli_adapter: WorkerAdapter, sdk_adapter: WorkerAdapter) -> None:
        self._cli_adapter = cli_adapter
        self._sdk_adapter = sdk_adapter
        self._lock = Lock()
        self._active_adapters: dict[str, WorkerAdapter] = {}

    def run(self, task, run_dir: str):
        adapter = self._sdk_adapter if task.backend_transport == BACKEND_TRANSPORT_SDK else self._cli_adapter
        with self._lock:
            self._active_adapters[task.task_id] = adapter
        try:
            return adapter.run(task, run_dir)
        finally:
            with self._lock:
                self._active_adapters.pop(task.task_id, None)

    def kill(self, task_id: str) -> bool:
        with self._lock:
            adapter = self._active_adapters.get(task_id)
        if adapter is None:
            return False
        return adapter.kill(task_id)
