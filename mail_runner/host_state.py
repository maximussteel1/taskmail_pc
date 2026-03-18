"""Host state persistence for the long-running mail runner process."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HOST_STATE_FILENAME = "host_state.json"

HOST_STATUS_STARTING = "starting"
HOST_STATUS_RUNNING = "running"
HOST_STATUS_STOPPED = "stopped"
HOST_STATUS_FAILED = "failed"


def current_timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def host_state_path(runtime_dir: str | Path) -> Path:
    return Path(runtime_dir) / HOST_STATE_FILENAME


@dataclass(slots=True)
class HostState:
    status: str
    pid: int
    started_at: str
    updated_at: str
    config_path: str
    runtime_dir: str
    exit_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_host_state(runtime_dir: str | Path) -> dict[str, Any] | None:
    path = host_state_path(runtime_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class HostStateStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = host_state_path(self.runtime_dir)

    def write(
        self,
        *,
        status: str,
        pid: int,
        started_at: str,
        config_path: str,
        runtime_dir: str,
        exit_reason: str | None = None,
    ) -> HostState:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        state = HostState(
            status=status,
            pid=pid,
            started_at=started_at,
            updated_at=current_timestamp(),
            config_path=config_path,
            runtime_dir=runtime_dir,
            exit_reason=exit_reason,
        )
        _write_json_atomic(self.path, state.to_dict())
        return state


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.stem + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write(os.linesep)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)
