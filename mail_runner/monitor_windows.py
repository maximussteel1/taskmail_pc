"""Windows monitor-window launcher for focused per-thread runtime views."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from .models import RunResult, TaskSnapshot, ThreadState

LOGGER = logging.getLogger(__name__)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
MonitorLauncher = Callable[[list[str], int, Path], object]


def _default_launcher(command: list[str], creationflags: int, cwd: Path) -> object:
    return subprocess.Popen(command, cwd=str(cwd), creationflags=creationflags)


def _process_is_alive(handle: object | None) -> bool:
    if handle is None:
        return False
    poll = getattr(handle, "poll", None)
    if not callable(poll):
        return True
    return poll() is None


class MonitorWindowManager:
    """Opens one focused monitor window per running thread on Windows."""

    def __init__(
        self,
        *,
        enabled: bool,
        project_root: str | Path,
        task_root: str | Path,
        config_path: str | Path | None = None,
        runtime_dir: str | Path | None = None,
        refresh_seconds: int = 5,
        buffer_lines: int = 1000,
        history_limit: int = 12,
        launcher: MonitorLauncher | None = None,
    ) -> None:
        self.enabled = bool(enabled and os.name == "nt")
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.config_path = None if config_path is None else Path(config_path).resolve()
        self.runtime_dir = None if runtime_dir is None else Path(runtime_dir).resolve()
        self.refresh_seconds = max(1, int(refresh_seconds))
        self.buffer_lines = max(1, int(buffer_lines))
        self.history_limit = max(1, int(history_limit))
        self._launcher = launcher or _default_launcher
        self._windows: dict[str, object] = {}

    @property
    def script_path(self) -> Path:
        return self.project_root / "scripts" / "monitor_mail_runner.ps1"

    def on_run_started(self, state: ThreadState, snapshot: TaskSnapshot) -> None:
        if not self.enabled:
            return
        if not self.script_path.exists():
            LOGGER.warning("Monitor launcher script not found: %s", self.script_path)
            return
        self._prune_exited_windows()
        thread_id = state.thread_id or snapshot.thread_id
        if _process_is_alive(self._windows.get(thread_id)):
            return
        command = self._build_command(thread_id)
        try:
            self._windows[thread_id] = self._launcher(command, CREATE_NEW_CONSOLE, self.project_root)
        except Exception:
            LOGGER.exception("Unable to open monitor window for thread %s", thread_id)

    def on_run_finished(self, state: ThreadState, result: RunResult) -> None:
        del state, result
        if not self.enabled:
            return
        self._prune_exited_windows()

    def _prune_exited_windows(self) -> None:
        for thread_id, handle in list(self._windows.items()):
            if not _process_is_alive(handle):
                self._windows.pop(thread_id, None)

    def _build_command(self, thread_id: str) -> list[str]:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-ProjectRoot",
            str(self.project_root),
            "-TaskRoot",
            str(self.task_root),
            "-RefreshSeconds",
            str(self.refresh_seconds),
            "-MaxBufferLines",
            str(self.buffer_lines),
            "-HistoryLimit",
            str(self.history_limit),
            "-ThreadId",
            thread_id,
            "-WindowTitle",
            f"Mail Runner Monitor {thread_id}",
            "-ExitWhenThreadNotRunning",
        ]
        if self.config_path is not None:
            command.extend(["-ConfigPath", str(self.config_path)])
        if self.runtime_dir is not None:
            command.extend(["-RuntimeDir", str(self.runtime_dir)])
        return command
