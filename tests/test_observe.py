"""Minimal observability CLI tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mail_runner.host_state import HOST_STATUS_RUNNING, HostStateStore
from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from mail_runner.models import RunResult, ThreadState
from mail_runner.observe import main
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_raw_mail, save_thread_state
from mail_runner.workspace import WorkspaceManager


def _write_runtime_config(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "mail_config.loop_30s.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    return config_path


def _thread_state(
    *,
    thread_id: str,
    status: str,
    lifecycle: str = "active",
    repo_path: str = "E:\\repo",
    workdir: str | None = ".",
    current_task_id: str,
    queued_task_id: str | None = None,
    history_files: list[str] | None = None,
    backend_session_id: str | None = None,
    backend_session_resumable: bool = False,
    last_summary: str | None = None,
    updated_at: str = "2099-03-17T01:00:00",
    backend_transport: str = "cli",
) -> ThreadState:
    workspace_id = build_workspace_id(repo_path, workdir)
    return ThreadState(
        thread_id=thread_id,
        root_message_id=f"<{thread_id}-root@example.com>",
        latest_message_id=f"<{thread_id}-latest@example.com>",
        subject_norm=f"subject-{thread_id}",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=current_task_id,
        last_task_snapshot_file=f"snapshots/{current_task_id}.json",
        status=status,
        history_files=list(history_files or []),
        last_summary=last_summary,
        lifecycle=lifecycle,
        workspace_id=workspace_id,
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id=thread_id,
        session_name=f"Session {thread_id}",
        session_norm=f"session-{thread_id}",
        backend_session_id=backend_session_id,
        backend_session_resumable=backend_session_resumable,
        backend_transport=backend_transport,
        queued_task_id=queued_task_id,
        queued_snapshot_file=(f"snapshots/{queued_task_id}.json" if queued_task_id else None),
        created_at="2099-03-17T00:59:00",
        updated_at=updated_at,
    )


def test_status_reports_host_and_runtime_counts(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    HostStateStore(runtime_dir).write(
        status=HOST_STATUS_RUNNING,
        pid=os.getpid(),
        started_at="2099-03-17T00:50:00",
        config_path=str(config_path),
        runtime_dir=str(runtime_dir),
    )
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="running",
            current_task_id="task_run",
            queued_task_id="task_follow",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )
    save_thread_state(
        _thread_state(
            thread_id="thread_002",
            status="accepted",
            current_task_id="task_queue",
            updated_at="2099-03-17T01:00:02",
        ),
        task_root,
    )
    save_thread_state(
        _thread_state(
            thread_id="thread_003",
            status="failed",
            current_task_id="task_failed",
            updated_at="2099-03-17T01:00:01",
        ),
        task_root,
    )

    exit_code = main(["--config", str(config_path), "--runtime-dir", str(runtime_dir), "status"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Host: running" in output
    assert "PID Alive: yes" in output
    assert f"Config: {config_path.resolve()}" in output
    assert f"Task Root: {task_root.resolve()}" in output
    assert "Threads Total: 3" in output
    assert "Sessions Total: 3" in output
    assert "Active Sessions: 3" in output
    assert "Ended Sessions: 0" in output
    assert "Running Sessions: 1" in output
    assert "Queue Items: 2" in output
    assert "Stale Sessions: 0" in output
    assert "Suspected Stuck Sessions: 0" in output
    assert "Orphaned Sessions: 0" in output
    assert "Failed Threads: 1" in output


def test_list_running_shows_running_sessions(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="running",
            current_task_id="task_run",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )
    save_thread_state(
        _thread_state(
            thread_id="thread_002",
            status="done",
            current_task_id="task_done",
            updated_at="2099-03-17T01:00:02",
        ),
        task_root,
    )

    exit_code = main(["--config", str(config_path), "list-running"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "thread_001 | session=thread_001 | lifecycle=active | backend=codex | transport=cli | task=task_run | health=normal" in output
    assert "thread_002" not in output


def test_list_running_hides_ended_sessions(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    ended = _thread_state(
        thread_id="thread_001",
        status="running",
        current_task_id="task_run",
        updated_at="2099-03-17T01:00:03",
    )
    ended.lifecycle = "ended"
    save_thread_state(ended, task_root)

    exit_code = main(["--config", str(config_path), "list-running"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.strip() == "(none)"


def test_list_queue_shows_queued_sessions_and_follow_up_items(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="running",
            current_task_id="task_run",
            queued_task_id="task_follow",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )
    save_thread_state(
        _thread_state(
            thread_id="thread_002",
            status="accepted",
            current_task_id="task_queue",
            updated_at="2099-03-17T01:00:02",
        ),
        task_root,
    )

    exit_code = main(["--config", str(config_path), "list-queue"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "follow-up | thread=thread_001 | session=thread_001 | backend=codex | transport=cli | task=task_follow" in output
    assert "queued-session | thread=thread_002 | session=thread_002 | backend=codex | transport=cli | task=task_queue" in output


def test_show_thread_reports_latest_result_details(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    workspace = WorkspaceManager(task_root)
    thread_state = _thread_state(
        thread_id="thread_001",
        status="done",
        current_task_id="task_done",
        history_files=["runs/task_done/result.json"],
        backend_session_id="session-123",
        backend_session_resumable=True,
        last_summary="Done summary",
        updated_at="2099-03-17T01:00:03",
    )
    save_thread_state(thread_state, task_root)
    workspace.save_run_result(
        "thread_001",
        "task_done",
        RunResult(
            task_id="task_done",
            thread_id="thread_001",
            backend="codex",
            status="success",
            exit_code=0,
            started_at="2099-03-17T00:59:10",
            finished_at="2099-03-17T00:59:20",
            stdout_file="runs/task_done/stdout.log",
            stderr_file="runs/task_done/stderr.log",
            summary_file="runs/task_done/summary.md",
            artifacts_dir="runs/task_done/artifacts",
            backend_session_id="session-123",
            backend_session_resumable=True,
        ),
    )

    exit_code = main(["--config", str(config_path), "show-thread", "thread_001"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Thread ID: thread_001" in output
    assert "Status: done" in output
    assert "Lifecycle: active" in output
    assert "Health: normal" in output
    assert "Last Progress At: 2099-03-17T01:00:03" in output
    assert "Queued Task ID: -" in output
    assert "Backend Transport: cli" in output
    assert "Backend Session ID: session-123" in output
    assert "Latest Run Status: success" in output
    assert "Latest Run Exit Code: 0" in output
    assert "Latest Run Summary File: runs/task_done/summary.md" in output


def test_show_thread_live_merges_transcript_and_live_stream(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    workspace = WorkspaceManager(task_root)
    thread_state = _thread_state(
        thread_id="thread_001",
        status="running",
        current_task_id="task_run",
        backend_transport="sdk",
        updated_at="2099-03-17T01:00:03",
    )
    save_thread_state(thread_state, task_root)
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<user-1@example.com>",
            "subject": "Re: [RUNNING][S:thread_001] Demo",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2099-03-17T01:00:00",
            "body_text": "Please continue the refactor.",
            "raw_headers": {},
        },
        task_root,
    )
    stream_path = workspace.run_file_path("thread_001", "task_run", "stream.events.jsonl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2099-03-17T01:00:04",
                        "seq": 1,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "turn.started",
                        "text": "Turn started",
                        "status": "running",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2099-03-17T01:00:05",
                        "seq": 2,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.delta",
                        "delta": "I am applying the patch now.",
                        "text": "I am applying the patch now.",
                        "item_type": "agent_message",
                        "status": "streaming",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2099-03-17T01:00:06",
                        "seq": 3,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "tool.started",
                        "text": "pytest -q",
                        "item_type": "command_execution",
                        "status": "running",
                        "payload": {"command": "pytest -q"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["--config", str(config_path), "show-thread-live", "thread_001"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Thread ID: thread_001" in output
    assert "Backend Transport: sdk" in output
    assert "Health: normal" in output
    assert "Last Progress At: 2099-03-17T01:00:06" in output
    assert "Please continue the refactor." in output
    assert "I am applying the patch now." in output
    assert "turn.started | Turn started" in output
    assert "tool.started | pytest -q" not in output


def test_show_thread_live_handles_missing_stream_log(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="running",
            current_task_id="task_run",
            backend_transport="sdk",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<assistant-1@example.com>",
            "subject": "[STATUS][S:thread_001] Demo",
            "from_addr": "runner@example.com",
            "to_addr": "user@example.com",
            "date": "2099-03-17T01:00:00",
            "body_text": "Status: RUNNING",
            "raw_headers": {SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
        },
        task_root,
    )

    exit_code = main(["--config", str(config_path), "show-thread-live", "thread_001"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Live Stream: unavailable" in output
    assert "Assistant [STATUS]" in output


def test_follow_thread_live_replays_recent_transcript_and_live_stream(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="running",
            current_task_id="task_run",
            backend_transport="sdk",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<user-1@example.com>",
            "subject": "Demo",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2099-03-17T01:00:00",
            "body_text": "Please continue the refactor.",
        },
        task_root,
    )
    stream_path = task_root / "thread_001" / "runs" / "task_run" / "stream.events.jsonl"
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2099-03-17T01:00:04",
                        "seq": 1,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.delta",
                        "delta": "I am applying the patch now.",
                        "text": "I am applying the patch now.",
                        "item_type": "agent_message",
                        "status": "streaming",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2099-03-17T01:00:06",
                        "seq": 2,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "tool.started",
                        "text": "pytest -q",
                        "item_type": "command_execution",
                        "status": "running",
                        "payload": {"command": "pytest -q"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--runtime-dir",
            str(runtime_dir),
            "follow-thread-live",
            "thread_001",
            "--iterations",
            "1",
            "--poll-seconds",
            "0",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Live Thread Monitor: thread_001" in output
    assert "Please continue the refactor." in output
    assert "I am applying the patch now." in output
    assert "tool.started | pytest -q" not in output


def test_follow_thread_live_keeps_active_done_thread_open_until_iteration_limit(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="done",
            current_task_id="task_done",
            backend_transport="sdk",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--runtime-dir",
            str(runtime_dir),
            "follow-thread-live",
            "thread_001",
            "--iterations",
            "1",
            "--poll-seconds",
            "0",
            "--exit-when-inactive",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Live Thread Monitor: thread_001" in output
    assert "monitor closed" not in output


def test_follow_thread_live_closes_for_non_active_thread(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    exit_state_path = runtime_dir / "follow_exit.json"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="done",
            lifecycle="ended",
            current_task_id="task_done",
            backend_transport="sdk",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--runtime-dir",
            str(runtime_dir),
            "follow-thread-live",
            "thread_001",
            "--iterations",
            "1",
            "--poll-seconds",
            "0",
            "--exit-when-inactive",
            "--exit-state-path",
            str(exit_state_path),
        ]
    )

    output = capsys.readouterr().out
    exit_state = json.loads(exit_state_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert "monitor closed: session is no longer active." in output
    assert exit_state == {"reason": "inactive", "thread_id": "thread_001"}


def test_follow_thread_live_writes_iteration_exit_state(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    exit_state_path = runtime_dir / "follow_iterations.json"
    save_thread_state(
        _thread_state(
            thread_id="thread_001",
            status="running",
            current_task_id="task_run",
            backend_transport="sdk",
            updated_at="2099-03-17T01:00:03",
        ),
        task_root,
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--runtime-dir",
            str(runtime_dir),
            "follow-thread-live",
            "thread_001",
            "--iterations",
            "1",
            "--poll-seconds",
            "0",
            "--exit-state-path",
            str(exit_state_path),
        ]
    )

    capsys.readouterr()
    exit_state = json.loads(exit_state_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert exit_state == {"reason": "iterations", "thread_id": "thread_001"}


def test_show_thread_returns_nonzero_for_missing_thread(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)

    exit_code = main(["--config", str(config_path), "show-thread", "thread_999"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Thread not found: thread_999" in output
