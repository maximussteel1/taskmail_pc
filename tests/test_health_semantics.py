from __future__ import annotations

import json

from mail_runner.health_semantics import derive_session_health, derive_thread_health
from mail_runner.models import SessionState, ThreadState
from mail_runner.thread_store import build_workspace_id, build_workspace_norm


def _thread_state(
    *,
    status: str,
    updated_at: str,
    last_progress_at: str | None = None,
    backend_transport: str = "cli",
    current_task_id: str = "task_001",
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
        current_task_id=current_task_id,
        last_task_snapshot_file=f"snapshots/{current_task_id}.json",
        status=status,
        history_files=[],
        lifecycle="active",
        last_progress_at=last_progress_at,
        workspace_id=workspace_id,
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id="thread_001",
        session_name="Demo Thread",
        session_norm="demo-thread",
        backend_transport=backend_transport,
        created_at="2026-03-18T10:00:00",
        updated_at=updated_at,
    )


def _session_state(
    *,
    status: str,
    updated_at: str,
    last_progress_at: str | None = None,
) -> SessionState:
    repo_path = "E:\\repo"
    workdir = "."
    workspace_id = build_workspace_id(repo_path, workdir)
    return SessionState(
        session_id="thread_001",
        workspace_id=workspace_id,
        thread_id="thread_001",
        session_name="Demo Thread",
        session_norm="demo-thread",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        status=status,
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        lifecycle="active",
        last_progress_at=last_progress_at,
        created_at="2026-03-18T10:00:00",
        updated_at=updated_at,
    )


def test_running_session_becomes_suspected_stuck_after_threshold() -> None:
    session = _session_state(status="running", updated_at="2026-03-18T10:00:00")

    health = derive_session_health(session, host_alive=True, now="2026-03-18T10:05:01")

    assert health.status == "suspected_stuck"
    assert health.idle_seconds == 301


def test_active_queued_session_without_host_is_orphaned() -> None:
    session = _session_state(status="queued", updated_at="2026-03-18T10:00:00")

    health = derive_session_health(session, host_alive=False, now="2026-03-18T10:05:01")

    assert health.status == "orphaned"


def test_waiting_user_thread_becomes_stale_not_stuck() -> None:
    thread = _thread_state(status="awaiting_user_input", updated_at="2026-03-18T10:00:00")

    health = derive_thread_health(thread, host_alive=True, now="2026-03-18T10:05:01")

    assert health.status == "stale"
    assert health.idle_seconds == 301


def test_sdk_stream_progress_prevents_false_stuck(tmp_path) -> None:
    thread = _thread_state(
        status="running",
        updated_at="2026-03-18T10:00:00",
        last_progress_at="2026-03-18T10:00:00",
        backend_transport="sdk",
        current_task_id="task_run",
    )
    stream_path = tmp_path / "thread_001" / "runs" / "task_run" / "stream.events.jsonl"
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps(
            {
                "ts": "2026-03-18T10:04:30",
                "seq": 1,
                "thread_id": "thread_001",
                "task_id": "task_run",
                "backend": "codex",
                "backend_transport": "sdk",
                "kind": "assistant.delta",
                "delta": "still working",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    health = derive_thread_health(thread, host_alive=True, task_root=tmp_path, now="2026-03-18T10:05:01")

    assert health.status == "normal"
    assert health.last_progress_at == "2026-03-18T10:04:30"
    assert health.idle_seconds == 31
