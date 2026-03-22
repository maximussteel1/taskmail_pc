"""Session continuation and monitor semantics tests."""

from __future__ import annotations

from mail_runner.models import ThreadState
from mail_runner.session_semantics import (
    effective_thread_status,
    thread_can_attempt_resume,
    thread_can_continue_without_resume,
    thread_monitor_exit_reason,
    thread_monitor_should_stay_open,
)
from mail_runner.thread_store import build_workspace_id, build_workspace_norm


def _thread_state(
    *,
    status: str,
    lifecycle: str = "active",
    backend_session_id: str | None = None,
    backend_session_resumable: bool = False,
    paused_from_status: str | None = None,
) -> ThreadState:
    repo_path = "E:\\repo"
    workdir = "."
    workspace_id = build_workspace_id(repo_path, workdir)
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="demo-thread",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=status,
        history_files=[],
        lifecycle=lifecycle,
        workspace_id=workspace_id,
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id="thread_001",
        session_name="Demo Thread",
        session_norm="demo-thread",
        backend_session_id=backend_session_id,
        backend_session_resumable=backend_session_resumable,
        paused_from_status=paused_from_status,
        created_at="2026-03-18T10:00:00",
        updated_at="2026-03-18T10:00:00",
    )


def test_thread_can_attempt_resume_for_resumable_done_thread() -> None:
    state = _thread_state(status="done", backend_session_id="sdk-thread-001", backend_session_resumable=True)

    assert thread_can_attempt_resume(state) is True
    assert thread_monitor_should_stay_open(state) is True


def test_thread_can_attempt_resume_for_killed_thread_with_native_context() -> None:
    state = _thread_state(status="killed", backend_session_id="sdk-thread-001", backend_session_resumable=False)

    assert thread_can_attempt_resume(state) is True
    assert thread_monitor_should_stay_open(state) is True


def test_paused_thread_uses_pre_pause_status_and_requires_explicit_resume() -> None:
    state = _thread_state(
        status="paused",
        backend_session_id="sdk-thread-001",
        backend_session_resumable=True,
        paused_from_status="awaiting_user_input",
    )

    assert effective_thread_status(state) == "awaiting_user_input"
    assert thread_can_continue_without_resume(state) is False
    assert thread_monitor_should_stay_open(state) is True


def test_active_done_thread_keeps_monitor_open_without_resume_context() -> None:
    state = _thread_state(status="done", backend_session_id=None, backend_session_resumable=False)

    assert thread_can_attempt_resume(state) is False
    assert thread_monitor_should_stay_open(state) is True


def test_ended_thread_does_not_keep_monitor_open() -> None:
    ended = _thread_state(status="done", lifecycle="ended", backend_session_id="sdk-thread-001", backend_session_resumable=True)

    assert thread_monitor_should_stay_open(ended) is False
    assert thread_monitor_exit_reason(ended) == "session is no longer active"
