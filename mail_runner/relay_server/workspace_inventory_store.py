"""Workspace inventory stores for the PC control plane."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict")
    return dict(value)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(slots=True)
class PcWorkspaceRecord:
    pc_id: str
    workspace_id: str
    workspace_norm: str | None
    repo_path: str
    workdir: str | None
    display_name: str
    source: str | None
    capabilities: dict[str, Any]
    updated_at: str

    def __post_init__(self) -> None:
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.workspace_id = _require_text(self.workspace_id, "workspace_id")
        self.workspace_norm = _require_optional_text(self.workspace_norm, "workspace_norm")
        self.repo_path = _require_text(self.repo_path, "repo_path")
        self.workdir = _require_optional_text(self.workdir, "workdir")
        self.display_name = _require_text(self.display_name, "display_name")
        self.source = _require_optional_text(self.source, "source")
        self.capabilities = _require_mapping(self.capabilities, "capabilities")
        self.updated_at = _require_text(self.updated_at, "updated_at")

    @property
    def inventory_key(self) -> str:
        return f"{self.pc_id}::{self.workspace_id}"


class WorkspaceInventoryConflictError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


class InMemoryWorkspaceInventoryStore:
    def __init__(self) -> None:
        self._items: dict[str, PcWorkspaceRecord] = {}
        self._lock = Lock()

    def replace_snapshot(
        self,
        *,
        pc_id: str,
        snapshot_id: str,
        workspaces: list[dict[str, Any]],
        updated_at: str,
    ) -> list[PcWorkspaceRecord]:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        _require_text(snapshot_id, "snapshot_id")
        normalized_updated_at = _require_text(updated_at, "updated_at")
        new_items: dict[str, PcWorkspaceRecord] = {}
        for item in workspaces:
            record = PcWorkspaceRecord(
                pc_id=normalized_pc_id,
                workspace_id=item.get("workspace_id"),
                workspace_norm=item.get("workspace_norm"),
                repo_path=item.get("repo_path"),
                workdir=item.get("workdir"),
                display_name=item.get("display_name"),
                source=item.get("source"),
                capabilities=item.get("capabilities") or {},
                updated_at=normalized_updated_at,
            )
            if record.inventory_key in new_items:
                raise WorkspaceInventoryConflictError(
                    "duplicate_workspace",
                    f"duplicate workspace_id in snapshot: {record.workspace_id}",
                )
            new_items[record.inventory_key] = record
        with self._lock:
            for inventory_key, record in new_items.items():
                existing = self._items.get(inventory_key)
                if existing is not None and (
                    existing.repo_path != record.repo_path or existing.workdir != record.workdir
                ):
                    raise WorkspaceInventoryConflictError(
                        "workspace_identity_mismatch",
                        f"workspace_id changed repo_path/workdir for {inventory_key}",
                    )
            stale_keys = [key for key in self._items if key.startswith(f"{normalized_pc_id}::") and key not in new_items]
            for stale_key in stale_keys:
                self._items.pop(stale_key, None)
            self._items.update(new_items)
            items = [item for item in self._items.values() if item.pc_id == normalized_pc_id]
        return sorted(items, key=lambda item: (item.pc_id, item.workspace_id))

    def list_workspaces(self, *, pc_id: str | None = None) -> list[PcWorkspaceRecord]:
        with self._lock:
            items = list(self._items.values())
        if pc_id is not None:
            normalized_pc_id = _require_text(pc_id, "pc_id")
            items = [item for item in items if item.pc_id == normalized_pc_id]
        return sorted(items, key=lambda item: (item.pc_id, item.workspace_id))

    def get_workspace(self, pc_id: str, workspace_id: str) -> PcWorkspaceRecord | None:
        inventory_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(workspace_id, 'workspace_id')}"
        with self._lock:
            return self._items.get(inventory_key)

    def count(self) -> int:
        with self._lock:
            return len(self._items)


class PersistentWorkspaceInventoryStore(InMemoryWorkspaceInventoryStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._items: dict[str, PcWorkspaceRecord] = {}
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._path, default={"workspaces": []})
        items = payload.get("workspaces", []) if isinstance(payload, dict) else []
        for item in items:
            record = PcWorkspaceRecord(**item)
            self._items[record.inventory_key] = record

    def _save(self) -> None:
        payload = {
            "version": 1,
            "workspaces": [
                asdict(item)
                for item in sorted(self._items.values(), key=lambda value: (value.pc_id, value.workspace_id))
            ],
        }
        _write_json(self._path, payload)

    def replace_snapshot(self, **kwargs) -> list[PcWorkspaceRecord]:
        records = super().replace_snapshot(**kwargs)
        self._save()
        return records
