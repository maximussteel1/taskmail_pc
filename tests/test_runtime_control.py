"""Runtime control tests for local PC-side kill requests."""

from __future__ import annotations

import time
from pathlib import Path

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.app import _process_runtime_thread_close_requests, _process_runtime_thread_kill_requests
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import TaskSnapshot
from mail_runner.runner import SerialTaskRunner
from mail_runner.runtime_control import (
    list_runner_restart_request_paths,
    list_thread_close_request_paths,
    list_thread_kill_request_paths,
    main,
    read_runner_restart_request,
    read_thread_close_request,
    read_thread_kill_request,
    write_thread_close_request,
    write_runner_restart_request,
    write_thread_kill_request,
)
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_KILLED, THREAD_STATUS_DONE, THREAD_STATUS_RUNNING
from mail_runner.thread_store import create_thread, load_thread_state


def _create_thread_state(
    task_root: Path,
    *,
    status: str,
    task_id: str = "task_001",
    lifecycle: str = "active",
    backend_session_id: str | None = None,
    backend_session_resumable: bool = False,
) -> None:
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="runtime-control",
        session_name="runtime-control",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id=task_id,
        last_task_snapshot_file=f"snapshots/{task_id}.json",
        task_root=task_root,
        status=status,
        history_files=[],
        last_summary=None,
        lifecycle=lifecycle,
        backend_session_id=backend_session_id,
        backend_session_resumable=backend_session_resumable,
        created_at="2026-03-18T21:00:00",
        updated_at="2026-03-18T21:00:00",
    )


def _snapshot(task_id: str = "task_001") -> TaskSnapshot:
    return TaskSnapshot(
        task_id=task_id,
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        permission=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Long running mock task.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-18T21:00:00",
        updated_at="2026-03-18T21:00:00",
    )


def test_runtime_control_main_queues_request_for_running_thread(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    _create_thread_state(task_root, status=THREAD_STATUS_RUNNING)

    exit_code = main(
        [
            "request-thread-kill",
            "thread_001",
            "--runtime-dir",
            str(runtime_dir),
            "--task-root",
            str(task_root),
            "--source",
            "pytest",
        ]
    )

    output = capsys.readouterr().out
    request_paths = list_thread_kill_request_paths(runtime_dir)

    assert exit_code == 0
    assert "Queued local kill request for thread thread_001 task task_001" in output
    assert len(request_paths) == 1
    assert read_thread_kill_request(request_paths[0])["source"] == "pytest"


def test_runtime_control_main_rejects_non_running_thread(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    _create_thread_state(task_root, status=THREAD_STATUS_DONE)

    exit_code = main(
        [
            "request-thread-kill",
            "thread_001",
            "--runtime-dir",
            str(runtime_dir),
            "--task-root",
            str(task_root),
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "is not currently running" in output
    assert list_thread_kill_request_paths(runtime_dir) == []


def test_runtime_control_main_queues_close_request_for_resumable_thread(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    _create_thread_state(
        task_root,
        status=THREAD_STATUS_DONE,
        backend_session_id="sdk-thread-001",
        backend_session_resumable=True,
    )

    exit_code = main(
        [
            "request-thread-close",
            "thread_001",
            "--runtime-dir",
            str(runtime_dir),
            "--task-root",
            str(task_root),
            "--source",
            "pytest",
        ]
    )

    output = capsys.readouterr().out
    request_paths = list_thread_close_request_paths(runtime_dir)

    assert exit_code == 0
    assert "Queued local close request for thread thread_001 task task_001" in output
    assert len(request_paths) == 1
    assert read_thread_close_request(request_paths[0])["source"] == "pytest"


def test_runtime_control_main_rejects_close_request_for_unmonitorable_thread(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    _create_thread_state(task_root, status=THREAD_STATUS_DONE)

    exit_code = main(
        [
            "request-thread-close",
            "thread_001",
            "--runtime-dir",
            str(runtime_dir),
            "--task-root",
            str(task_root),
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "is not currently monitorable" in output
    assert list_thread_close_request_paths(runtime_dir) == []


def test_write_runner_restart_request_round_trips_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"

    request_path = write_runner_restart_request(
        runtime_dir,
        source="mail",
        thread_id="thread_073",
        message_id="<reply@example.com>",
    )

    request_paths = list_runner_restart_request_paths(runtime_dir)
    assert request_paths == [request_path]
    payload = read_runner_restart_request(request_path)
    assert payload["source"] == "mail"
    assert payload["thread_id"] == "thread_073"
    assert payload["message_id"] == "<reply@example.com>"


def test_read_runner_restart_request_accepts_utf8_bom(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    request_dir = runtime_dir / "runner_restart_requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / "restart.json"
    request_path.write_text(
        '{"request_id":"req_001","source":"smoke","requested_at":"2026-03-20T09:00:00"}\n',
        encoding="utf-8-sig",
    )

    payload = read_runner_restart_request(request_path)

    assert payload["request_id"] == "req_001"
    assert payload["source"] == "smoke"


def test_process_runtime_thread_kill_requests_kills_matching_active_thread(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5)))

    runner.start_background_task(_snapshot())
    time.sleep(0.1)
    request_path = write_thread_kill_request(
        runtime_dir,
        thread_id="thread_001",
        task_id="task_001",
        source="pytest",
    )

    stats = _process_runtime_thread_kill_requests(runner, runtime_dir=runtime_dir)
    result = runner.wait_for_active()

    assert stats == {"seen": 1, "accepted": 1, "ignored": 0, "invalid": 0}
    assert not request_path.exists()
    assert result is not None
    assert result.status == RUN_STATUS_KILLED


def test_process_runtime_thread_kill_requests_ignores_stale_task_id(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5)))

    runner.start_background_task(_snapshot())
    time.sleep(0.1)
    request_path = write_thread_kill_request(
        runtime_dir,
        thread_id="thread_001",
        task_id="task_999",
        source="pytest",
    )

    stats = _process_runtime_thread_kill_requests(runner, runtime_dir=runtime_dir)
    assert stats == {"seen": 1, "accepted": 0, "ignored": 1, "invalid": 0}
    assert not request_path.exists()

    assert runner.kill("task_001") is True
    result = runner.wait_for_active()
    assert result is not None
    assert result.status == RUN_STATUS_KILLED


def test_process_runtime_thread_close_requests_kills_then_ends_matching_thread(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5)))

    runner.start_background_task(_snapshot())
    time.sleep(0.1)
    request_path = write_thread_close_request(
        runtime_dir,
        thread_id="thread_001",
        task_id="task_001",
        source="pytest",
    )

    first_stats = _process_runtime_thread_close_requests(runner, runtime_dir=runtime_dir)
    result = runner.wait_for_active()
    runner.collect_finished()
    second_stats = _process_runtime_thread_close_requests(runner, runtime_dir=runtime_dir)
    state = load_thread_state("thread_001", task_root)

    assert first_stats == {"seen": 1, "completed": 0, "pending": 1, "ignored": 0, "invalid": 0}
    assert result is not None
    assert result.status == RUN_STATUS_KILLED
    assert second_stats == {"seen": 1, "completed": 1, "pending": 0, "ignored": 0, "invalid": 0}
    assert not request_path.exists()
    assert state.status == RUN_STATUS_KILLED
    assert state.lifecycle == "ended"


def test_process_runtime_thread_close_requests_ends_resumable_nonrunning_thread(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    task_root = tmp_path / "tasks"
    _create_thread_state(
        task_root,
        status=THREAD_STATUS_DONE,
        backend_session_id="sdk-thread-001",
        backend_session_resumable=True,
    )
    request_path = write_thread_close_request(
        runtime_dir,
        thread_id="thread_001",
        task_id="task_001",
        source="pytest",
    )
    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(), MockAdapter()))

    stats = _process_runtime_thread_close_requests(runner, runtime_dir=runtime_dir)
    state = load_thread_state("thread_001", task_root)

    assert stats == {"seen": 1, "completed": 1, "pending": 0, "ignored": 0, "invalid": 0}
    assert not request_path.exists()
    assert state.status == THREAD_STATUS_DONE
    assert state.lifecycle == "ended"
