"""Node stores for the PC control plane."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
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


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(slots=True)
class PcNodeRecord:
    pc_id: str
    display_name: str
    auth_credential_id: str
    current_connection_id: str
    current_connection_epoch: int
    connected_at: str
    last_seen_at: str
    status: str
    client_version: str
    host_fingerprint: str | None = None
    runtime_fingerprint: str | None = None
    capabilities: dict[str, Any] | None = None
    active_run_count: int = 0
    workspace_count: int = 0
    load_hint: str = "normal"
    updated_at: str | None = None

    def __post_init__(self) -> None:
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.display_name = _require_text(self.display_name, "display_name")
        self.auth_credential_id = _require_text(self.auth_credential_id, "auth_credential_id")
        self.current_connection_id = _require_text(self.current_connection_id, "current_connection_id")
        if not isinstance(self.current_connection_epoch, int) or self.current_connection_epoch <= 0:
            raise ValueError("current_connection_epoch must be a positive integer")
        self.connected_at = _require_text(self.connected_at, "connected_at")
        self.last_seen_at = _require_text(self.last_seen_at, "last_seen_at")
        self.status = _require_text(self.status, "status")
        if self.status not in {"online", "stale", "offline"}:
            raise ValueError("status must be one of: online, stale, offline")
        self.client_version = _require_text(self.client_version, "client_version")
        self.host_fingerprint = _require_optional_text(self.host_fingerprint, "host_fingerprint")
        self.runtime_fingerprint = _require_optional_text(self.runtime_fingerprint, "runtime_fingerprint")
        self.capabilities = {} if self.capabilities is None else _require_mapping(self.capabilities, "capabilities")
        if not isinstance(self.active_run_count, int) or self.active_run_count < 0:
            raise ValueError("active_run_count must be a non-negative integer")
        if not isinstance(self.workspace_count, int) or self.workspace_count < 0:
            raise ValueError("workspace_count must be a non-negative integer")
        self.load_hint = _require_text(self.load_hint, "load_hint")
        self.updated_at = _require_optional_text(self.updated_at, "updated_at")


class PcNodeFenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


class InMemoryPcNodeStore:
    def __init__(self) -> None:
        self._nodes: dict[str, PcNodeRecord] = {}
        self._lock = Lock()

    def register_connection(
        self,
        *,
        pc_id: str,
        display_name: str,
        auth_credential_id: str,
        connection_id: str,
        connected_at: str,
        last_seen_at: str,
        client_version: str,
        host_fingerprint: str | None,
        runtime_fingerprint: str | None,
        capabilities: dict[str, Any],
    ) -> PcNodeRecord:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        with self._lock:
            existing = self._nodes.get(normalized_pc_id)
            next_epoch = 1 if existing is None else existing.current_connection_epoch + 1
            record = PcNodeRecord(
                pc_id=normalized_pc_id,
                display_name=display_name,
                auth_credential_id=auth_credential_id,
                current_connection_id=connection_id,
                current_connection_epoch=next_epoch,
                connected_at=connected_at,
                last_seen_at=last_seen_at,
                status="online",
                client_version=client_version,
                host_fingerprint=host_fingerprint,
                runtime_fingerprint=runtime_fingerprint,
                capabilities=capabilities,
                active_run_count=existing.active_run_count if existing is not None else 0,
                workspace_count=existing.workspace_count if existing is not None else 0,
                load_hint=existing.load_hint if existing is not None else "normal",
                updated_at=connected_at,
            )
            self._nodes[normalized_pc_id] = record
            return record

    def touch_connection(
        self,
        *,
        pc_id: str,
        connection_id: str,
        connection_epoch: int,
        last_seen_at: str,
        active_run_count: int | None = None,
        workspace_count: int | None = None,
        load_hint: str | None = None,
    ) -> PcNodeRecord:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_connection_id = _require_text(connection_id, "connection_id")
        if not isinstance(connection_epoch, int) or connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        with self._lock:
            existing = self._nodes.get(normalized_pc_id)
            if existing is None:
                raise PcNodeFenceError("unknown_pc", f"pc_id not found: {normalized_pc_id}")
            if existing.current_connection_epoch != connection_epoch:
                raise PcNodeFenceError("stale_connection_epoch", f"stale connection_epoch for {normalized_pc_id}")
            if existing.current_connection_id != normalized_connection_id:
                raise PcNodeFenceError("connection_id_mismatch", f"connection_id mismatch for {normalized_pc_id}")
            existing.last_seen_at = _require_text(last_seen_at, "last_seen_at")
            existing.status = "online"
            existing.updated_at = existing.last_seen_at
            if active_run_count is not None:
                if not isinstance(active_run_count, int) or active_run_count < 0:
                    raise ValueError("active_run_count must be a non-negative integer")
                existing.active_run_count = active_run_count
            if workspace_count is not None:
                if not isinstance(workspace_count, int) or workspace_count < 0:
                    raise ValueError("workspace_count must be a non-negative integer")
                existing.workspace_count = workspace_count
            if load_hint is not None:
                existing.load_hint = _require_text(load_hint, "load_hint")
            return existing

    def close_connection(
        self,
        *,
        pc_id: str,
        connection_id: str,
        connection_epoch: int,
        closed_at: str,
    ) -> PcNodeRecord | None:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_connection_id = _require_text(connection_id, "connection_id")
        if not isinstance(connection_epoch, int) or connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        with self._lock:
            existing = self._nodes.get(normalized_pc_id)
            if existing is None:
                return None
            if (
                existing.current_connection_epoch != connection_epoch
                or existing.current_connection_id != normalized_connection_id
            ):
                return existing
            existing.status = "offline"
            existing.last_seen_at = _require_text(closed_at, "closed_at")
            existing.updated_at = existing.last_seen_at
            return existing

    def get_node(
        self,
        pc_id: str,
        *,
        now: str | None = None,
        stale_after_seconds: int = 60,
        offline_after_seconds: int = 180,
    ) -> PcNodeRecord | None:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        with self._lock:
            existing = self._nodes.get(normalized_pc_id)
            if existing is None:
                return None
            return self._project_status(
                existing,
                now=now,
                stale_after_seconds=stale_after_seconds,
                offline_after_seconds=offline_after_seconds,
            )

    def list_nodes(
        self,
        *,
        now: str | None = None,
        stale_after_seconds: int = 60,
        offline_after_seconds: int = 180,
    ) -> list[PcNodeRecord]:
        with self._lock:
            return [
                self._project_status(
                    item,
                    now=now,
                    stale_after_seconds=stale_after_seconds,
                    offline_after_seconds=offline_after_seconds,
                )
                for item in sorted(self._nodes.values(), key=lambda value: value.pc_id)
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._nodes)

    @staticmethod
    def _project_status(
        record: PcNodeRecord,
        *,
        now: str | None,
        stale_after_seconds: int,
        offline_after_seconds: int,
    ) -> PcNodeRecord:
        projected = PcNodeRecord(**asdict(record))
        current_time = _parse_timestamp(now) if now else None
        seen_at = _parse_timestamp(record.last_seen_at)
        if current_time is not None and seen_at is not None:
            if current_time - seen_at >= timedelta(seconds=max(offline_after_seconds, stale_after_seconds + 1)):
                projected.status = "offline"
            elif current_time - seen_at >= timedelta(seconds=max(1, stale_after_seconds)):
                projected.status = "stale"
            elif record.status != "offline":
                projected.status = "online"
        return projected


class PersistentPcNodeStore(InMemoryPcNodeStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._nodes: dict[str, PcNodeRecord] = {}
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._path, default={"nodes": []})
        nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
        for item in nodes:
            node = PcNodeRecord(**item)
            self._nodes[node.pc_id] = node

    def _save(self) -> None:
        payload = {
            "version": 1,
            "nodes": [asdict(item) for item in sorted(self._nodes.values(), key=lambda item: item.pc_id)],
        }
        _write_json(self._path, payload)

    def register_connection(self, **kwargs) -> PcNodeRecord:
        record = super().register_connection(**kwargs)
        self._save()
        return record

    def touch_connection(self, **kwargs) -> PcNodeRecord:
        record = super().touch_connection(**kwargs)
        self._save()
        return record

    def close_connection(self, **kwargs) -> PcNodeRecord | None:
        record = super().close_connection(**kwargs)
        self._save()
        return record
