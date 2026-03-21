"""Relay session stores."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _load_json(path: Path, default):
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(slots=True)
class RelaySession:
    connection_id: str
    client_id: str
    connected_at: str
    last_seen_at: str
    closed_at: str | None = None

    def __post_init__(self) -> None:
        self.connection_id = _require_text(self.connection_id, "connection_id")
        self.client_id = _require_text(self.client_id, "client_id")
        self.connected_at = _require_text(self.connected_at, "connected_at")
        self.last_seen_at = _require_text(self.last_seen_at, "last_seen_at")
        if self.closed_at is not None:
            self.closed_at = _require_text(self.closed_at, "closed_at")


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, RelaySession] = {}
        self._lock = Lock()

    def upsert_session(
        self,
        *,
        connection_id: str,
        client_id: str,
        connected_at: str,
        last_seen_at: str,
    ) -> RelaySession:
        session = RelaySession(
            connection_id=connection_id,
            client_id=client_id,
            connected_at=connected_at,
            last_seen_at=last_seen_at,
        )
        with self._lock:
            self._sessions[session.connection_id] = session
        return session

    def touch_session(self, connection_id: str, *, last_seen_at: str) -> RelaySession | None:
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            existing.last_seen_at = _require_text(last_seen_at, "last_seen_at")
            return existing

    def close_session(self, connection_id: str, *, closed_at: str) -> RelaySession | None:
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            existing.closed_at = _require_text(closed_at, "closed_at")
            return existing

    def remove_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            return self._sessions.pop(connection_id, None)

    def get_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            return self._sessions.get(connection_id)

    def list_sessions(self) -> list[RelaySession]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda item: item.connection_id)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


class PersistentSessionStore:
    def __init__(self, state_dir: str | Path) -> None:
        self._state_dir = Path(state_dir)
        self._sessions_path = self._state_dir / "sessions.json"
        self._sessions: dict[str, RelaySession] = {}
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._sessions_path, default={"sessions": []})
        sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
        for item in sessions:
            session = RelaySession(**item)
            self._sessions[session.connection_id] = session

    def _save(self) -> None:
        payload = {
            "version": 1,
            "sessions": [asdict(item) for item in sorted(self._sessions.values(), key=lambda item: item.connection_id)],
        }
        _write_json(self._sessions_path, payload)

    def upsert_session(
        self,
        *,
        connection_id: str,
        client_id: str,
        connected_at: str,
        last_seen_at: str,
    ) -> RelaySession:
        session = RelaySession(
            connection_id=connection_id,
            client_id=client_id,
            connected_at=connected_at,
            last_seen_at=last_seen_at,
        )
        with self._lock:
            self._sessions[session.connection_id] = session
            self._save()
        return session

    def touch_session(self, connection_id: str, *, last_seen_at: str) -> RelaySession | None:
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            existing.last_seen_at = _require_text(last_seen_at, "last_seen_at")
            self._save()
            return existing

    def close_session(self, connection_id: str, *, closed_at: str) -> RelaySession | None:
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            existing.closed_at = _require_text(closed_at, "closed_at")
            self._save()
            return existing

    def remove_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            removed = self._sessions.pop(connection_id, None)
            self._save()
            return removed

    def get_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            return self._sessions.get(connection_id)

    def list_sessions(self) -> list[RelaySession]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda item: item.connection_id)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)
