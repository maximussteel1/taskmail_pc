"""Persistence helpers for tracked Codex SDK sidecar processes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROCESS_RECORD_FILENAME = "codex_sidecar_process.json"


@dataclass(slots=True)
class CodexSidecarProcessRecord:
    path: Path
    pid: int
    task_id: str
    thread_id: str
    started_at: str
    run_dir: str
    repo_path: str
    workdir: str
    command: list[str]
    adapter: str = "codex_sdk"


def process_record_path(run_dir: str | Path) -> Path:
    return Path(run_dir).resolve() / PROCESS_RECORD_FILENAME


def write_process_record(
    run_dir: str | Path,
    *,
    pid: int,
    task_id: str,
    thread_id: str,
    started_at: str,
    repo_path: str,
    workdir: str,
    command: list[str],
    adapter: str = "codex_sdk",
) -> Path:
    path = process_record_path(run_dir)
    payload = {
        "adapter": adapter,
        "pid": int(pid),
        "task_id": str(task_id),
        "thread_id": str(thread_id),
        "started_at": str(started_at),
        "run_dir": str(Path(run_dir).resolve()),
        "repo_path": str(repo_path),
        "workdir": str(workdir),
        "command": list(command),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def remove_process_record(run_dir: str | Path) -> None:
    path = process_record_path(run_dir)
    if path.exists():
        path.unlink()


def load_process_record(path: str | Path) -> CodexSidecarProcessRecord | None:
    record_path = Path(path).resolve()
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    try:
        pid = int(payload.get("pid"))
    except Exception:
        return None
    if pid <= 0:
        return None

    command = payload.get("command")
    if not isinstance(command, list):
        command = []

    return CodexSidecarProcessRecord(
        path=record_path,
        pid=pid,
        task_id=str(payload.get("task_id") or "").strip(),
        thread_id=str(payload.get("thread_id") or "").strip(),
        started_at=str(payload.get("started_at") or "").strip(),
        run_dir=str(payload.get("run_dir") or "").strip(),
        repo_path=str(payload.get("repo_path") or "").strip(),
        workdir=str(payload.get("workdir") or "").strip(),
        command=[str(part) for part in command],
        adapter=str(payload.get("adapter") or "codex_sdk").strip() or "codex_sdk",
    )


def iter_process_record_paths(task_root: str | Path) -> list[Path]:
    root = Path(task_root).resolve()
    if not root.exists():
        return []
    return sorted(root.glob(f"*/runs/*/{PROCESS_RECORD_FILENAME}"))


def iter_process_records(task_root: str | Path) -> list[CodexSidecarProcessRecord]:
    records: list[CodexSidecarProcessRecord] = []
    for path in iter_process_record_paths(task_root):
        record = load_process_record(path)
        if record is not None:
            records.append(record)
    return records
