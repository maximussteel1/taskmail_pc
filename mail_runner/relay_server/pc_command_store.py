"""Command ledger stores for the PC control plane."""

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


def _require_optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _load_json(path: Path, default):
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(slots=True)
class PcCommandRecord:
    pc_id: str
    workspace_id: str
    command_id: str
    command_type: str
    trace_id: str
    dispatch_message_id: str
    created_at: str
    execution_policy: dict[str, Any]
    command_payload: dict[str, Any]
    session_id: str | None = None
    status: str = "queued"
    dispatched_connection_epoch: int | None = None
    dispatched_at: str | None = None
    ack_status: str | None = None
    queue_position: int | None = None
    reason: str | None = None
    error_code: str | None = None
    ack_message_id: str | None = None
    acked_at: str | None = None

    def __post_init__(self) -> None:
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.workspace_id = _require_text(self.workspace_id, "workspace_id")
        self.command_id = _require_text(self.command_id, "command_id")
        self.command_type = _require_text(self.command_type, "command_type")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        self.dispatch_message_id = _require_text(self.dispatch_message_id, "dispatch_message_id")
        self.created_at = _require_text(self.created_at, "created_at")
        self.execution_policy = _require_mapping(self.execution_policy, "execution_policy")
        self.command_payload = _require_mapping(self.command_payload, "command_payload")
        self.session_id = _require_optional_text(self.session_id, "session_id")
        self.status = _require_text(self.status, "status")
        if self.status not in {"queued", "dispatched", "acknowledged"}:
            raise ValueError("status must be one of: queued, dispatched, acknowledged")
        self.dispatched_connection_epoch = _require_optional_int(
            self.dispatched_connection_epoch,
            "dispatched_connection_epoch",
            minimum=1,
        )
        self.dispatched_at = _require_optional_text(self.dispatched_at, "dispatched_at")
        self.ack_status = _require_optional_text(self.ack_status, "ack_status")
        self.queue_position = _require_optional_int(self.queue_position, "queue_position", minimum=1)
        self.reason = _require_optional_text(self.reason, "reason")
        self.error_code = _require_optional_text(self.error_code, "error_code")
        self.ack_message_id = _require_optional_text(self.ack_message_id, "ack_message_id")
        self.acked_at = _require_optional_text(self.acked_at, "acked_at")

    @property
    def command_key(self) -> str:
        return f"{self.pc_id}::{self.command_id}"


class PcCommandConflictError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


class PcCommandUnknownError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


class InMemoryPcCommandStore:
    def __init__(self) -> None:
        self._records: dict[str, PcCommandRecord] = {}
        self._lock = Lock()

    def upsert_dispatch(self, record: PcCommandRecord) -> tuple[PcCommandRecord, bool]:
        with self._lock:
            existing = self._records.get(record.command_key)
            if existing is None:
                self._records[record.command_key] = record
                return record, True
            immutable_keys = (
                "pc_id",
                "workspace_id",
                "command_id",
                "command_type",
                "session_id",
                "execution_policy",
                "command_payload",
            )
            for key in immutable_keys:
                if getattr(existing, key) != getattr(record, key):
                    raise PcCommandConflictError(
                        "command_id_conflict",
                        f"command_id already exists with different payload: {record.command_key}",
                    )
            return existing, False

    def collect_pending_dispatches(
        self,
        *,
        pc_id: str,
        connection_epoch: int,
        dispatched_at: str,
        limit: int = 50,
    ) -> list[PcCommandRecord]:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_dispatched_at = _require_text(dispatched_at, "dispatched_at")
        if not isinstance(connection_epoch, int) or connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            selected: list[PcCommandRecord] = []
            for record in sorted(self._records.values(), key=lambda item: (item.created_at, item.command_id)):
                if record.pc_id != normalized_pc_id or record.ack_status is not None:
                    continue
                if record.dispatched_connection_epoch == connection_epoch:
                    continue
                record.status = "dispatched"
                record.dispatched_connection_epoch = connection_epoch
                record.dispatched_at = normalized_dispatched_at
                selected.append(record)
                if len(selected) >= limit:
                    break
            return [PcCommandRecord(**asdict(item)) for item in selected]

    def record_ack(
        self,
        *,
        pc_id: str,
        command_id: str,
        ack_status: str,
        ack_message_id: str,
        acked_at: str,
        queue_position: int | None = None,
        reason: str | None = None,
        error_code: str | None = None,
    ) -> PcCommandRecord:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        normalized_ack_status = _require_text(ack_status, "ack_status")
        normalized_ack_message_id = _require_text(ack_message_id, "ack_message_id")
        normalized_acked_at = _require_text(acked_at, "acked_at")
        normalized_queue_position = _require_optional_int(queue_position, "queue_position", minimum=1)
        normalized_reason = _require_optional_text(reason, "reason")
        normalized_error_code = _require_optional_text(error_code, "error_code")
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                raise PcCommandUnknownError("unknown_command", f"command_id not found: {command_key}")
            if existing.ack_status is not None:
                if (
                    existing.ack_status != normalized_ack_status
                    or existing.queue_position != normalized_queue_position
                    or existing.reason != normalized_reason
                    or existing.error_code != normalized_error_code
                ):
                    raise PcCommandConflictError(
                        "ack_conflict",
                        f"command_ack does not match the existing ack for {command_key}",
                    )
                return PcCommandRecord(**asdict(existing))
            existing.status = "acknowledged"
            existing.ack_status = normalized_ack_status
            existing.queue_position = normalized_queue_position
            existing.reason = normalized_reason
            existing.error_code = normalized_error_code
            existing.ack_message_id = normalized_ack_message_id
            existing.acked_at = normalized_acked_at
            return PcCommandRecord(**asdict(existing))

    def get_command(self, pc_id: str, command_id: str) -> PcCommandRecord | None:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                return None
            return PcCommandRecord(**asdict(existing))

    def list_commands(self, *, pc_id: str | None = None) -> list[PcCommandRecord]:
        normalized_pc_id = _require_text(pc_id, "pc_id") if pc_id is not None else None
        with self._lock:
            items = [PcCommandRecord(**asdict(item)) for item in self._records.values()]
        if normalized_pc_id is not None:
            items = [item for item in items if item.pc_id == normalized_pc_id]
        return sorted(items, key=lambda item: (item.pc_id, item.created_at, item.command_id))

    def count(self) -> int:
        with self._lock:
            return len(self._records)


class PersistentPcCommandStore(InMemoryPcCommandStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._records: dict[str, PcCommandRecord] = {}
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._path, default={"commands": []})
        commands = payload.get("commands", []) if isinstance(payload, dict) else []
        for item in commands:
            record = PcCommandRecord(**item)
            self._records[record.command_key] = record

    def _save(self) -> None:
        payload = {
            "version": 1,
            "commands": [asdict(item) for item in self.list_commands()],
        }
        _write_json(self._path, payload)

    def upsert_dispatch(self, record: PcCommandRecord) -> tuple[PcCommandRecord, bool]:
        updated = super().upsert_dispatch(record)
        self._save()
        return updated

    def collect_pending_dispatches(self, **kwargs) -> list[PcCommandRecord]:
        items = super().collect_pending_dispatches(**kwargs)
        self._save()
        return items

    def record_ack(self, **kwargs) -> PcCommandRecord:
        record = super().record_ack(**kwargs)
        self._save()
        return record
