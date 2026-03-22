"""Shared helpers for session continuation, resume, and monitor semantics."""

from __future__ import annotations

from .models import ThreadState
from .status import (
    THREAD_STATUS_KILLED,
    THREAD_STATUS_PAUSED,
)


def effective_thread_status(state: ThreadState) -> str:
    """Return the effective pre-pause status when a thread is paused."""

    if state.status == THREAD_STATUS_PAUSED and state.paused_from_status:
        return state.paused_from_status
    return state.status


def thread_can_attempt_resume(state: ThreadState) -> bool:
    """Whether the thread still has enough native context to resume."""

    if not state.backend_session_id:
        return False
    return bool(state.backend_session_resumable or effective_thread_status(state) == THREAD_STATUS_KILLED)


def thread_can_continue_without_resume(state: ThreadState) -> bool:
    """Whether a plain reply should behave like an in-place continuation."""

    return bool(state.lifecycle == "active" and state.status != THREAD_STATUS_PAUSED)


def thread_monitor_should_stay_open(state: ThreadState) -> bool:
    """Whether a focused monitor window should stay attached to this thread."""

    return state.lifecycle == "active"


def thread_monitor_exit_reason(state: ThreadState) -> str:
    """Human-readable explanation for why a focused monitor can close."""

    return "session is no longer active"
