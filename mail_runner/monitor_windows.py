"""Windows active-session-window launcher for focused per-thread runtime views."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import RunResult, TaskSnapshot, ThreadState

LOGGER = logging.getLogger(__name__)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
_WINDOW_STATE_DIRNAME = "active_session_window_state"
ActiveSessionWindowLauncher = Callable[[list[str], int, Path], object]
MonitorLauncher = ActiveSessionWindowLauncher

if os.name == "nt":
    import ctypes
    from ctypes import wintypes


@dataclass(slots=True)
class _PersistedWindowState:
    controller_pid: int | None = None
    worker_pid: int | None = None


def _default_launcher(command: list[str], creationflags: int, cwd: Path) -> object:
    return subprocess.Popen(command, cwd=str(cwd), creationflags=creationflags)


def _process_is_alive(handle: object | None) -> bool:
    if handle is None:
        return False
    poll = getattr(handle, "poll", None)
    if not callable(poll):
        return True
    return poll() is None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return None


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code_process.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _load_persisted_window_state(path: Path) -> _PersistedWindowState | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        LOGGER.warning("Unable to parse active-session window state: %s", path)
        return None
    if not isinstance(payload, dict):
        LOGGER.warning("Ignoring non-object active-session window state: %s", path)
        return None
    return _PersistedWindowState(
        controller_pid=_optional_int(payload.get("controller_pid")),
        worker_pid=_optional_int(payload.get("worker_pid")),
    )


class ActiveSessionWindowManager:
    """Opens one focused active-session window per active thread on Windows."""

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
        launcher: ActiveSessionWindowLauncher | None = None,
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
        return self.project_root / "scripts" / "monitor_mail_runner_controller.ps1"

    @property
    def state_dir(self) -> Path | None:
        if self.runtime_dir is None:
            return None
        return self.runtime_dir / _WINDOW_STATE_DIRNAME

    def on_run_started(self, state: ThreadState, snapshot: TaskSnapshot) -> None:
        if not self.enabled:
            return
        if state.lifecycle != "active":
            return
        if not self.script_path.exists():
            LOGGER.warning("Active-session window launcher script not found: %s", self.script_path)
            return
        self._prune_exited_windows()
        thread_id = state.thread_id or snapshot.thread_id
        if _process_is_alive(self._windows.get(thread_id)):
            return
        if self._persisted_window_is_alive(thread_id):
            return
        command = self._build_command(thread_id)
        try:
            self._windows[thread_id] = self._launcher(command, CREATE_NEW_CONSOLE, self.project_root)
        except Exception:
            LOGGER.exception("Unable to open active-session window for thread %s", thread_id)

    def on_run_finished(self, state: ThreadState, result: RunResult) -> None:
        del state, result
        if not self.enabled:
            return
        self._prune_exited_windows()

    def _prune_exited_windows(self) -> None:
        for thread_id, handle in list(self._windows.items()):
            if not _process_is_alive(handle):
                self._windows.pop(thread_id, None)
        self._prune_stale_persisted_window_states()

    def _window_state_path(self, thread_id: str) -> Path | None:
        state_dir = self.state_dir
        if state_dir is None:
            return None
        return state_dir / f"{thread_id}.window.json"

    def _persisted_window_state_is_alive(self, state_path: Path) -> bool:
        state = _load_persisted_window_state(state_path)
        if state is None:
            state_path.unlink(missing_ok=True)
            return False
        if _pid_is_alive(state.worker_pid) or _pid_is_alive(state.controller_pid):
            return True
        state_path.unlink(missing_ok=True)
        return False

    def _persisted_window_is_alive(self, thread_id: str) -> bool:
        state_path = self._window_state_path(thread_id)
        if state_path is None or not state_path.exists():
            return False
        return self._persisted_window_state_is_alive(state_path)

    def _prune_stale_persisted_window_states(self) -> None:
        state_dir = self.state_dir
        if state_dir is None or not state_dir.exists():
            return
        for state_path in state_dir.glob("*.window.json"):
            self._persisted_window_state_is_alive(state_path)

    def _build_command(self, thread_id: str) -> list[str]:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
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
            f"Mail Runner Active Session {thread_id}",
            "-ExitWhenThreadNotActive",
        ]
        if self.config_path is not None:
            command.extend(["-ConfigPath", str(self.config_path)])
        if self.runtime_dir is not None:
            command.extend(["-RuntimeDir", str(self.runtime_dir)])
        return command


MonitorWindowManager = ActiveSessionWindowManager
