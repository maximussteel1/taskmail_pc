"""Shared helpers for thread/session health classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import SessionState, ThreadState
from .stream_events import load_stream_events, stream_events_path
from .status import (
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_PAUSED,
    THREAD_STATUS_RUNNING,
)

HEALTH_STALE_AFTER_SECONDS = 300


@dataclass(frozen=True, slots=True)
class DerivedHealth:
    status: str
    last_progress_at: str | None
    idle_seconds: int | None
    reason: str | None = None


def derive_session_health(
    session: SessionState,
    *,
    host_alive: bool,
    now: str | datetime | None = None,
) -> DerivedHealth:
    execution_state = _session_execution_state(session)
    last_progress_at = session.last_progress_at or session.updated_at
    idle_seconds = _idle_seconds(last_progress_at, now)
    return _classify_health(
        lifecycle=session.lifecycle,
        execution_state=execution_state,
        host_alive=host_alive,
        last_progress_at=last_progress_at,
        idle_seconds=idle_seconds,
    )


def derive_thread_health(
    thread: ThreadState,
    *,
    host_alive: bool,
    session: SessionState | None = None,
    task_root: Path | None = None,
    now: str | datetime | None = None,
) -> DerivedHealth:
    lifecycle = session.lifecycle if session is not None else thread.lifecycle
    execution_state = _thread_execution_state(thread, session=session)
    last_progress_at = thread.last_progress_at or thread.updated_at
    stream_progress_at = _load_stream_progress_at(task_root, thread) if task_root is not None else None
    last_progress_at = _latest_timestamp(last_progress_at, stream_progress_at)
    idle_seconds = _idle_seconds(last_progress_at, now)
    return _classify_health(
        lifecycle=lifecycle,
        execution_state=execution_state,
        host_alive=host_alive,
        last_progress_at=last_progress_at,
        idle_seconds=idle_seconds,
    )


def _classify_health(
    *,
    lifecycle: str,
    execution_state: str,
    host_alive: bool,
    last_progress_at: str | None,
    idle_seconds: int | None,
) -> DerivedHealth:
    if lifecycle != "active":
        return DerivedHealth(status="normal", last_progress_at=last_progress_at, idle_seconds=idle_seconds)
    if execution_state in {"queued", "running"} and not host_alive:
        return DerivedHealth(
            status="orphaned",
            last_progress_at=last_progress_at,
            idle_seconds=idle_seconds,
            reason="host is not alive while active work is still pending",
        )
    if idle_seconds is None or idle_seconds <= HEALTH_STALE_AFTER_SECONDS:
        return DerivedHealth(status="normal", last_progress_at=last_progress_at, idle_seconds=idle_seconds)
    if execution_state == "running":
        return DerivedHealth(
            status="suspected_stuck",
            last_progress_at=last_progress_at,
            idle_seconds=idle_seconds,
            reason=f"no execution progress for {idle_seconds}s while the session is running",
        )
    if execution_state in {"queued", "waiting_user", "paused", "idle"}:
        return DerivedHealth(
            status="stale",
            last_progress_at=last_progress_at,
            idle_seconds=idle_seconds,
            reason=f"no new progress for {idle_seconds}s while the session is {execution_state}",
        )
    return DerivedHealth(status="normal", last_progress_at=last_progress_at, idle_seconds=idle_seconds)


def _session_execution_state(session: SessionState) -> str:
    if session.lifecycle != "active":
        return "terminal"
    if session.status == "running":
        return "running"
    if session.status == "queued":
        return "queued"
    if session.status == "waiting_user":
        return "waiting_user"
    if session.status == "paused":
        return "paused"
    if session.status in {"done", "failed", "killed", "archived"}:
        return "terminal"
    return "idle"


def _thread_execution_state(thread: ThreadState, *, session: SessionState | None) -> str:
    if session is not None:
        return _session_execution_state(session)
    if thread.lifecycle != "active":
        return "terminal"
    if thread.status == THREAD_STATUS_RUNNING:
        return "running"
    if thread.status == THREAD_STATUS_ACCEPTED:
        return "queued"
    if thread.status == THREAD_STATUS_AWAITING_USER_INPUT:
        return "waiting_user"
    if thread.status == THREAD_STATUS_PAUSED:
        return "paused"
    if thread.status in {"done", "failed", "killed"}:
        return "terminal"
    return "idle"


def _load_stream_progress_at(task_root: Path, thread: ThreadState) -> str | None:
    if thread.backend_transport != "sdk":
        return None
    task_id = str(thread.current_task_id or "").strip()
    if not task_id:
        return None
    try:
        events = load_stream_events(stream_events_path(task_root, thread.thread_id, task_id))
    except Exception:
        return None
    for event in reversed(events):
        ts = str(event.ts or "").strip()
        if ts:
            return ts
    return None


def _idle_seconds(value: str | None, now: str | datetime | None) -> int | None:
    last_progress_dt = _parse_timestamp(value)
    now_dt = _parse_timestamp(now) if isinstance(now, str) else now
    if last_progress_dt is None:
        return None
    if now_dt is None:
        now_dt = datetime.now(last_progress_dt.tzinfo)
    return max(0, int((now_dt - last_progress_dt).total_seconds()))


def _latest_timestamp(left: str | None, right: str | None) -> str | None:
    left_dt = _parse_timestamp(left)
    right_dt = _parse_timestamp(right)
    if left_dt is None:
        return right
    if right_dt is None:
        return left
    return right if right_dt >= left_dt else left


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
