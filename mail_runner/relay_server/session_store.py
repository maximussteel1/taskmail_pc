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
    active_subscription_id: str | None = None
    subscribed_workspace_id: str | None = None
    subscribed_session_id: str | None = None
    subscribed_thread_id: str | None = None
    last_subscription_sequence: int = 0

    def __post_init__(self) -> None:
        self.connection_id = _require_text(self.connection_id, "connection_id")
        self.client_id = _require_text(self.client_id, "client_id")
        self.connected_at = _require_text(self.connected_at, "connected_at")
        self.last_seen_at = _require_text(self.last_seen_at, "last_seen_at")
        if self.closed_at is not None:
            self.closed_at = _require_text(self.closed_at, "closed_at")
        if self.active_subscription_id is not None:
            self.active_subscription_id = _require_text(self.active_subscription_id, "active_subscription_id")
        if self.subscribed_workspace_id is not None:
            self.subscribed_workspace_id = _require_text(self.subscribed_workspace_id, "subscribed_workspace_id")
        if self.subscribed_session_id is not None:
            self.subscribed_session_id = _require_text(self.subscribed_session_id, "subscribed_session_id")
        if self.subscribed_thread_id is not None:
            self.subscribed_thread_id = _require_text(self.subscribed_thread_id, "subscribed_thread_id")
        if not isinstance(self.last_subscription_sequence, int) or self.last_subscription_sequence < 0:
            raise ValueError("last_subscription_sequence must be a non-negative integer")


def _session_sequence_key(workspace_id: str, session_id: str) -> str:
    return f"{_require_text(workspace_id, 'workspace_id')}::{_require_text(session_id, 'session_id')}"


def _clear_subscription_fields(session: RelaySession) -> None:
    session.active_subscription_id = None
    session.subscribed_workspace_id = None
    session.subscribed_session_id = None
    session.subscribed_thread_id = None
    session.last_subscription_sequence = 0


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, RelaySession] = {}
        self._session_sequences: dict[str, int] = {}
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
            _clear_subscription_fields(existing)
            return existing

    def remove_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            removed = self._sessions.pop(connection_id, None)
            if removed is not None:
                _clear_subscription_fields(removed)
            return removed

    def get_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            return self._sessions.get(connection_id)

    def upsert_subscription(
        self,
        connection_id: str,
        *,
        subscription_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
        last_sequence: int,
    ) -> RelaySession | None:
        if not isinstance(last_sequence, int) or last_sequence <= 0:
            raise ValueError("last_sequence must be a positive integer")
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            existing.active_subscription_id = _require_text(subscription_id, "subscription_id")
            existing.subscribed_workspace_id = _require_text(workspace_id, "workspace_id")
            existing.subscribed_session_id = _require_text(session_id, "session_id")
            existing.subscribed_thread_id = _require_text(thread_id, "thread_id")
            existing.last_subscription_sequence = last_sequence
            return existing

    def clear_subscription(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            _clear_subscription_fields(existing)
            return existing

    def reserve_session_sequence(
        self,
        workspace_id: str,
        session_id: str,
        *,
        minimum_next_sequence: int = 1,
    ) -> int:
        if not isinstance(minimum_next_sequence, int) or minimum_next_sequence <= 0:
            raise ValueError("minimum_next_sequence must be a positive integer")
        sequence_key = _session_sequence_key(workspace_id, session_id)
        with self._lock:
            next_sequence = max(self._session_sequences.get(sequence_key, 0) + 1, minimum_next_sequence)
            self._session_sequences[sequence_key] = next_sequence
            return next_sequence

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
        self._session_sequences: dict[str, int] = {}
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._sessions_path, default={"sessions": [], "session_sequences": {}})
        sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
        for item in sessions:
            session = RelaySession(**item)
            self._sessions[session.connection_id] = session
        raw_sequences = payload.get("session_sequences", {}) if isinstance(payload, dict) else {}
        if isinstance(raw_sequences, dict):
            for key, value in raw_sequences.items():
                sequence_key = _require_text(str(key), "session_sequences.key")
                if not isinstance(value, int) or value < 0:
                    raise ValueError("session_sequences values must be non-negative integers")
                self._session_sequences[sequence_key] = value

    def _save(self) -> None:
        payload = {
            "version": 2,
            "sessions": [asdict(item) for item in sorted(self._sessions.values(), key=lambda item: item.connection_id)],
            "session_sequences": dict(sorted(self._session_sequences.items())),
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
            _clear_subscription_fields(existing)
            self._save()
            return existing

    def remove_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            removed = self._sessions.pop(connection_id, None)
            if removed is not None:
                _clear_subscription_fields(removed)
            self._save()
            return removed

    def get_session(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            return self._sessions.get(connection_id)

    def upsert_subscription(
        self,
        connection_id: str,
        *,
        subscription_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
        last_sequence: int,
    ) -> RelaySession | None:
        if not isinstance(last_sequence, int) or last_sequence <= 0:
            raise ValueError("last_sequence must be a positive integer")
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            existing.active_subscription_id = _require_text(subscription_id, "subscription_id")
            existing.subscribed_workspace_id = _require_text(workspace_id, "workspace_id")
            existing.subscribed_session_id = _require_text(session_id, "session_id")
            existing.subscribed_thread_id = _require_text(thread_id, "thread_id")
            existing.last_subscription_sequence = last_sequence
            self._save()
            return existing

    def clear_subscription(self, connection_id: str) -> RelaySession | None:
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is None:
                return None
            _clear_subscription_fields(existing)
            self._save()
            return existing

    def reserve_session_sequence(
        self,
        workspace_id: str,
        session_id: str,
        *,
        minimum_next_sequence: int = 1,
    ) -> int:
        if not isinstance(minimum_next_sequence, int) or minimum_next_sequence <= 0:
            raise ValueError("minimum_next_sequence must be a positive integer")
        sequence_key = _session_sequence_key(workspace_id, session_id)
        with self._lock:
            next_sequence = max(self._session_sequences.get(sequence_key, 0) + 1, minimum_next_sequence)
            self._session_sequences[sequence_key] = next_sequence
            self._save()
            return next_sequence

    def list_sessions(self) -> list[RelaySession]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda item: item.connection_id)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)
