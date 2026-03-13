"""Filesystem workspace helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import RunResult, TaskSnapshot


class WorkspaceManager:
    """Creates and resolves the local task directory structure."""

    def __init__(self, task_root: str | Path) -> None:
        self.task_root = Path(task_root)

    def ensure_layout(self) -> None:
        self.task_root.mkdir(parents=True, exist_ok=True)
        self.scheduler_meta_dir().mkdir(parents=True, exist_ok=True)
        self.workspaces_dir().mkdir(parents=True, exist_ok=True)

    def thread_dir(self, thread_id: str) -> Path:
        return self.task_root / thread_id

    def thread_state_path(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "thread_state.json"

    def scheduler_meta_dir(self) -> Path:
        return self.task_root / "_scheduler"

    def workspaces_dir(self) -> Path:
        return self.scheduler_meta_dir() / "workspaces"

    def workspace_dir(self, workspace_id: str) -> Path:
        return self.workspaces_dir() / workspace_id

    def workspace_state_path(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "workspace_state.json"

    def workspace_sessions_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "sessions"

    def session_state_path(self, workspace_id: str, session_id: str) -> Path:
        return self.workspace_sessions_dir(workspace_id) / f"{session_id}.json"

    def snapshots_dir(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "snapshots"

    def runs_dir(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "runs"

    def mail_dir(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "mail"

    def ensure_thread_layout(self, thread_id: str) -> Path:
        thread_dir = self.thread_dir(thread_id)
        self.ensure_layout()
        self.snapshots_dir(thread_id).mkdir(parents=True, exist_ok=True)
        self.runs_dir(thread_id).mkdir(parents=True, exist_ok=True)
        self.mail_dir(thread_id).mkdir(parents=True, exist_ok=True)
        return thread_dir

    def ensure_workspace_layout(self, workspace_id: str) -> Path:
        workspace_dir = self.workspace_dir(workspace_id)
        self.ensure_layout()
        self.workspace_sessions_dir(workspace_id).mkdir(parents=True, exist_ok=True)
        return workspace_dir

    def snapshot_path(self, thread_id: str, task_id: str) -> Path:
        return self.snapshots_dir(thread_id) / f"{task_id}.json"

    def run_dir(self, thread_id: str, task_id: str) -> Path:
        return self.runs_dir(thread_id) / task_id

    def run_file_path(self, thread_id: str, task_id: str, filename: str) -> Path:
        return self.run_dir(thread_id, task_id) / filename

    def create_run_dir(self, thread_id: str, task_id: str, exist_ok: bool = False) -> Path:
        run_dir = self.run_dir(thread_id, task_id)
        self.ensure_thread_layout(thread_id)
        run_dir.mkdir(parents=True, exist_ok=exist_ok)
        return run_dir

    def write_text(self, path: str | Path, content: str) -> Path:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def save_json(self, path: str | Path, payload: dict) -> Path:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        file_path.write_text(rendered, encoding="utf-8")
        return file_path

    def load_json(self, path: str | Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def save_snapshot(self, snapshot: TaskSnapshot) -> Path:
        self.ensure_thread_layout(snapshot.thread_id)
        return self.save_json(self.snapshot_path(snapshot.thread_id, snapshot.task_id), asdict(snapshot))

    def load_snapshot(self, thread_id: str, relative_path: str) -> TaskSnapshot:
        return TaskSnapshot(**self.load_json(self.thread_dir(thread_id) / relative_path))

    def save_run_result(self, thread_id: str, task_id: str, result: RunResult) -> Path:
        self.ensure_thread_layout(thread_id)
        return self.save_json(self.run_file_path(thread_id, task_id, "result.json"), asdict(result))

    def load_run_result(self, thread_id: str, relative_path: str) -> RunResult:
        return RunResult(**self.load_json(self.thread_dir(thread_id) / relative_path))

    def to_thread_relative(self, thread_id: str, path: str | Path) -> str:
        return Path(path).relative_to(self.thread_dir(thread_id)).as_posix()
