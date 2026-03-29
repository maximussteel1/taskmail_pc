"""Persistent request-id ledger for the Android session-action facade."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_int(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


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
class AndroidSessionActionRequestRecord:
    request_id: str
    request_fingerprint: str
    response_status_code: int
    response_payload: dict[str, Any]
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        self.request_id = _require_text(self.request_id, "request_id")
        self.request_fingerprint = _require_text(self.request_fingerprint, "request_fingerprint")
        self.response_status_code = _require_int(self.response_status_code, "response_status_code", minimum=100)
        self.response_payload = _require_mapping(self.response_payload, "response_payload")
        self.created_at = _require_text(self.created_at, "created_at")
        self.updated_at = _require_text(self.updated_at, "updated_at")


class InMemoryAndroidSessionActionRequestStore:
    def __init__(self) -> None:
        self._records: dict[str, AndroidSessionActionRequestRecord] = {}
        self._lock = Lock()

    def get_request(self, request_id: str) -> AndroidSessionActionRequestRecord | None:
        normalized_request_id = _require_text(request_id, "request_id")
        with self._lock:
            existing = self._records.get(normalized_request_id)
            if existing is None:
                return None
            return AndroidSessionActionRequestRecord(**asdict(existing))

    def upsert_response(self, record: AndroidSessionActionRequestRecord) -> tuple[AndroidSessionActionRequestRecord, bool]:
        if not isinstance(record, AndroidSessionActionRequestRecord):
            raise ValueError("record must be an AndroidSessionActionRequestRecord")
        with self._lock:
            existing = self._records.get(record.request_id)
            if existing is None:
                self._records[record.request_id] = record
                return record, True
            if existing.request_fingerprint != record.request_fingerprint:
                return AndroidSessionActionRequestRecord(**asdict(existing)), False
            return AndroidSessionActionRequestRecord(**asdict(existing)), False

    def list_requests(self) -> list[AndroidSessionActionRequestRecord]:
        with self._lock:
            items = [AndroidSessionActionRequestRecord(**asdict(item)) for item in self._records.values()]
        return sorted(items, key=lambda item: (item.created_at, item.request_id))


class PersistentAndroidSessionActionRequestStore(InMemoryAndroidSessionActionRequestStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._records: dict[str, AndroidSessionActionRequestRecord] = {}
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._path, default={"requests": []})
        requests = payload.get("requests", []) if isinstance(payload, dict) else []
        for item in requests:
            record = AndroidSessionActionRequestRecord(**item)
            self._records[record.request_id] = record

    def _save(self) -> None:
        payload = {
            "version": 1,
            "requests": [asdict(item) for item in self.list_requests()],
        }
        _write_json(self._path, payload)

    def upsert_response(self, record: AndroidSessionActionRequestRecord) -> tuple[AndroidSessionActionRequestRecord, bool]:
        stored_record, created = super().upsert_response(record)
        if created:
            self._save()
        return stored_record, created
