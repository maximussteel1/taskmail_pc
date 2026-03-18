"""Single-instance runtime lock for the mail runner host."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
else:
    import fcntl


class HostLockError(RuntimeError):
    """Base error for host lock failures."""


class HostAlreadyRunningError(HostLockError):
    """Raised when another host process already owns the runtime lock."""


class RuntimeHostLock:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self._handle: object | None = None
        self._file_handle: object | None = None

    @property
    def mutex_name(self) -> str:
        normalized = str(self.runtime_dir.resolve()).lower().encode("utf-8")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        return f"Local\\MailRunnerHost_{digest}"

    @property
    def lock_path(self) -> Path:
        return self.runtime_dir / "host.lock"

    def acquire(self) -> None:
        if self._handle is not None or self._file_handle is not None:
            raise HostLockError("Runtime host lock is already acquired.")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            self._acquire_windows()
            return
        self._acquire_posix()

    def release(self) -> None:
        if os.name == "nt":
            self._release_windows()
            return
        self._release_posix()

    def __enter__(self) -> "RuntimeHostLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def _acquire_windows(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = create_mutex(None, False, self.mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        if ctypes.get_last_error() == 183:
            close_handle(handle)
            raise HostAlreadyRunningError(
                f"Another host process already owns runtime_dir={self.runtime_dir}"
            )
        self._handle = handle

    def _release_windows(self) -> None:
        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(self._handle)
        self._handle = None

    def _acquire_posix(self) -> None:
        handle = self.lock_path.open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise HostAlreadyRunningError(
                f"Another host process already owns runtime_dir={self.runtime_dir}"
            ) from exc
        self._file_handle = handle

    def _release_posix(self) -> None:
        if self._file_handle is None:
            return
        fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
        self._file_handle.close()
        self._file_handle = None
