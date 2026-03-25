"""Runner happy-path tests for Phase 1."""

from __future__ import annotations

import json
import time

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import TaskSnapshot
from mail_runner.runner import SerialTaskRunner, main
from mail_runner.status import (
    BACKEND_CODEX,
    BACKEND_OPENCODE,
    RUN_STATUS_KILLED,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_DONE,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_RUNNING,
)
from mail_runner.thread_store import build_workspace_id, create_thread, load_thread_state, load_workspace_state
from mail_runner.workspace import WorkspaceManager


class RecordingMonitorWindowManager:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.finished: list[tuple[str, str, str]] = []

    def on_run_started(self, state, snapshot) -> None:
        self.started.append((state.thread_id, snapshot.task_id))

    def on_run_finished(self, state, result) -> None:
        self.finished.append((state.thread_id, result.task_id, result.status))


def _seed_payload() -> dict:
    return {
        "backend": "opencode",
        "repo_path": "D:\\repo",
        "workdir": "src",
        "task_text": "Refactor the module without changing the API.",
        "acceptance": ["pytest passes", "brief summary"],
        "timeout_minutes": 30,
        "mode": "modify",
    }


def _snapshot(task_id: str, thread_id: str, *, repo_path: str = "D:\\repo", workdir: str | None = "src") -> TaskSnapshot:
    return TaskSnapshot(
        task_id=task_id,
        thread_id=thread_id,
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path=repo_path,
        workdir=workdir,
        task_text=f"Task body for {task_id}.",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:30:00",
        updated_at="2026-03-12T12:30:00",
    )


def _persist_snapshot(task_root, snapshot: TaskSnapshot) -> None:
    WorkspaceManager(task_root).save_snapshot(snapshot)


def test_serial_task_runner_happy_path(tmp_path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_seed_payload()), encoding="utf-8")
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    runner = SerialTaskRunner(task_root, dispatcher)

    result = runner.start(seed_path)
    thread_dir = task_root / "thread_001"
    state = json.loads((thread_dir / "thread_state.json").read_text(encoding="utf-8"))

    assert result.status == RUN_STATUS_SUCCESS
    assert state["status"] == THREAD_STATUS_DONE
    assert state["current_task_id"] == result.task_id
    assert state["history_files"] == [f"runs/{result.task_id}/result.json"]
    assert (thread_dir / "snapshots" / f"{result.task_id}.json").exists()
    assert (thread_dir / "runs" / result.task_id / "result.json").exists()


def test_runner_main_returns_zero_for_success(tmp_path, monkeypatch) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_seed_payload()), encoding="utf-8")
    task_root = tmp_path / "tasks"

    import mail_runner.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_build_dispatcher",
        lambda config=None: Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0)),
    )

    exit_code = main(["--snapshot", str(seed_path), "--task-root", str(task_root)])

    assert exit_code == 0


def test_serial_task_runner_background_kill(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Long running mock task.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:30:00",
        updated_at="2026-03-12T12:30:00",
    )

    runner.start_background_task(snapshot)
    time.sleep(0.1)

    assert runner.kill("task_001") is True
    result = runner.wait_for_active()

    assert result is not None
    assert result.status == RUN_STATUS_KILLED


def test_serial_task_runner_can_kill_by_thread_id(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Long running mock task.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:30:00",
        updated_at="2026-03-12T12:30:00",
    )

    runner.start_background_task(snapshot)
    time.sleep(0.1)

    assert runner.kill_thread("thread_001", expected_task_id="task_001") is True
    result = runner.wait_for_active()

    assert result is not None
    assert result.status == RUN_STATUS_KILLED


def test_serial_task_runner_does_not_kill_thread_when_task_id_mismatches(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Long running mock task.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:30:00",
        updated_at="2026-03-12T12:30:00",
    )

    runner.start_background_task(snapshot)
    time.sleep(0.1)

    assert runner.kill_thread("thread_001", expected_task_id="task_999") is False
    assert runner.kill("task_001") is True
    result = runner.wait_for_active()

    assert result is not None
    assert result.status == RUN_STATUS_KILLED


def test_serial_task_runner_keeps_backend_session_resumable_after_kill(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Long running mock task.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:30:00",
        updated_at="2026-03-12T12:30:00",
    )

    runner.start_background_task(snapshot)
    time.sleep(0.1)
    assert runner.kill("task_001") is True
    result = runner.wait_for_active()
    state = load_thread_state("thread_001", task_root)

    assert result is not None
    assert result.status == RUN_STATUS_KILLED
    assert state.backend_session_id == "mock-session-opencode-thread_001"
    assert state.backend_session_resumable is True


def test_serial_task_runner_queues_following_session_in_same_workspace(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher, max_active_sessions_per_workspace=1)
    first = _snapshot("task_001", "thread_001")
    second = _snapshot("task_002", "thread_002")

    runner.start_background_task(first)
    time.sleep(0.05)
    runner.start_background_task(second)

    workspace_state = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    second_state = load_thread_state("thread_002", task_root)

    assert runner.queued_count() == 1
    assert workspace_state.active_session_ids == ["thread_001"]
    assert workspace_state.active_session_id == "thread_001"
    assert workspace_state.queued_session_ids == ["thread_002"]
    assert second_state.status == "accepted"
    assert second_state.current_task_id == "task_002"

    runner.wait_until_idle()

    final_workspace = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    first_state = load_thread_state("thread_001", task_root)
    second_state = load_thread_state("thread_002", task_root)

    assert final_workspace.active_session_id is None
    assert final_workspace.active_session_ids == []
    assert final_workspace.queued_session_ids == []
    assert first_state.status == THREAD_STATUS_DONE
    assert second_state.status == THREAD_STATUS_DONE


def test_serial_task_runner_keeps_pending_follow_up_for_running_session(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher)
    first = _snapshot("task_001", "thread_001")
    second = _snapshot("task_002", "thread_001")

    runner.start_background_task(first)
    time.sleep(0.05)
    runner.start_background_task(second)

    state = load_thread_state("thread_001", task_root)

    assert state.status == "running"
    assert state.current_task_id == "task_001"
    assert state.queued_task_id == "task_002"
    assert state.queued_snapshot_file == "snapshots/task_002.json"

    runner.wait_until_idle()

    final_state = load_thread_state("thread_001", task_root)
    session_workspace = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)

    assert final_state.status == THREAD_STATUS_DONE
    assert final_state.current_task_id == "task_002"
    assert final_state.queued_task_id is None
    assert len(final_state.history_files) == 2
    assert session_workspace.active_session_id is None


def test_serial_task_runner_runs_different_workspaces_concurrently(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher, max_active_sessions=2)
    first = _snapshot("task_001", "thread_001", workdir="src_a")
    second = _snapshot("task_002", "thread_002", workdir="src_b")

    runner.start_background_task(first)
    runner.start_background_task(second)
    time.sleep(0.05)

    first_state = load_thread_state("thread_001", task_root)
    second_state = load_thread_state("thread_002", task_root)
    first_workspace = load_workspace_state(build_workspace_id("D:\\repo", "src_a"), task_root)
    second_workspace = load_workspace_state(build_workspace_id("D:\\repo", "src_b"), task_root)

    assert runner.active_count() == 2
    assert runner.queued_count() == 0
    assert first_state.status == THREAD_STATUS_RUNNING
    assert second_state.status == THREAD_STATUS_RUNNING
    assert first_workspace.active_session_id == "thread_001"
    assert second_workspace.active_session_id == "thread_002"

    runner.wait_until_idle()

    assert load_thread_state("thread_001", task_root).status == THREAD_STATUS_DONE
    assert load_thread_state("thread_002", task_root).status == THREAD_STATUS_DONE


def test_serial_task_runner_runs_two_sessions_in_same_workspace_when_workspace_cap_allows_it(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher, max_active_sessions=4, max_active_sessions_per_workspace=2)
    first = _snapshot("task_001", "thread_001")
    second = _snapshot("task_002", "thread_002")

    runner.start_background_task(first)
    runner.start_background_task(second)
    time.sleep(0.05)

    workspace_state = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    first_state = load_thread_state("thread_001", task_root)
    second_state = load_thread_state("thread_002", task_root)

    assert runner.active_count() == 2
    assert runner.queued_count() == 0
    assert sorted(workspace_state.active_session_ids) == ["thread_001", "thread_002"]
    assert workspace_state.active_session_id == "thread_001"
    assert workspace_state.queued_session_ids == []
    assert first_state.status == THREAD_STATUS_RUNNING
    assert second_state.status == THREAD_STATUS_RUNNING

    runner.wait_until_idle()

    final_workspace = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    assert final_workspace.active_session_ids == []
    assert final_workspace.active_session_id is None
    assert load_thread_state("thread_001", task_root).status == THREAD_STATUS_DONE
    assert load_thread_state("thread_002", task_root).status == THREAD_STATUS_DONE


def test_serial_task_runner_queues_third_session_after_workspace_cap_is_reached(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(task_root, dispatcher, max_active_sessions=4, max_active_sessions_per_workspace=2)
    first = _snapshot("task_001", "thread_001")
    second = _snapshot("task_002", "thread_002")
    third = _snapshot("task_003", "thread_003")

    runner.start_background_task(first)
    runner.start_background_task(second)
    time.sleep(0.05)
    runner.start_background_task(third)

    workspace_state = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    third_state = load_thread_state("thread_003", task_root)

    assert runner.active_count() == 2
    assert runner.queued_count() == 1
    assert sorted(workspace_state.active_session_ids) == ["thread_001", "thread_002"]
    assert workspace_state.queued_session_ids == ["thread_003"]
    assert third_state.status == THREAD_STATUS_ACCEPTED
    assert third_state.current_task_id == "task_003"

    runner.wait_until_idle()

    final_workspace = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    assert final_workspace.active_session_ids == []
    assert final_workspace.active_session_id is None
    assert final_workspace.queued_session_ids == []
    assert load_thread_state("thread_003", task_root).status == THREAD_STATUS_DONE


def test_serial_task_runner_notifies_monitor_window_manager(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    monitor = RecordingMonitorWindowManager()
    runner = SerialTaskRunner(task_root, dispatcher, monitor_window_manager=monitor)
    snapshot = _snapshot("task_001", "thread_001")

    runner.start_background_task(snapshot)
    runner.wait_until_idle()

    assert monitor.started == [("thread_001", "task_001")]
    assert monitor.finished == [("thread_001", "task_001", RUN_STATUS_SUCCESS)]


def test_serial_task_runner_recovers_accepted_queue_on_restart(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    snapshot = _snapshot("task_queued", "thread_001")
    _persist_snapshot(task_root, snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="demo task",
        session_name="Demo task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path=snapshot.repo_path,
        workdir=snapshot.workdir,
        current_task_id=snapshot.task_id,
        last_task_snapshot_file=f"snapshots/{snapshot.task_id}.json",
        task_root=task_root,
        status=THREAD_STATUS_ACCEPTED,
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0)), max_active_sessions=2)

    assert runner.queued_count() == 1

    runner.dispatch_ready()
    runner.wait_until_idle()

    assert load_thread_state("thread_001", task_root).status == THREAD_STATUS_DONE


def test_serial_task_runner_recovery_uses_callback_factory(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    snapshot = _snapshot("task_queued", "thread_001")
    _persist_snapshot(task_root, snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<accepted@example.com>",
        subject_norm="demo task",
        session_name="Demo task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path=snapshot.repo_path,
        workdir=snapshot.workdir,
        current_task_id=snapshot.task_id,
        last_task_snapshot_file=f"snapshots/{snapshot.task_id}.json",
        task_root=task_root,
        status=THREAD_STATUS_ACCEPTED,
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    callback_events: list[tuple[str, str]] = []

    def build_callbacks(state, recovered_snapshot):
        assert state.thread_id == "thread_001"
        assert recovered_snapshot.task_id == "task_queued"

        def on_running(running_state) -> None:
            callback_events.append(("running", running_state.current_task_id))

        def on_finished(final_state, result) -> None:
            callback_events.append((final_state.status, result.status))

        return on_running, on_finished

    runner = SerialTaskRunner(
        task_root,
        Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0)),
        max_active_sessions=2,
        recovery_callback_factory=build_callbacks,
    )

    runner.dispatch_ready()
    runner.wait_until_idle()

    assert callback_events == [("running", "task_queued"), (THREAD_STATUS_DONE, RUN_STATUS_SUCCESS)]


def test_serial_task_runner_marks_running_task_failed_on_restart(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    snapshot = _snapshot("task_running", "thread_001")
    _persist_snapshot(task_root, snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="demo task",
        session_name="Demo task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path=snapshot.repo_path,
        workdir=snapshot.workdir,
        current_task_id=snapshot.task_id,
        last_task_snapshot_file=f"snapshots/{snapshot.task_id}.json",
        task_root=task_root,
        status=THREAD_STATUS_RUNNING,
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0)), max_active_sessions=2)
    state = load_thread_state("thread_001", task_root)

    assert runner.queued_count() == 0
    assert state.status == THREAD_STATUS_FAILED
    assert state.last_summary == "Runner restarted while task was running."


def test_serial_task_runner_promotes_follow_up_after_restart(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    running_snapshot = _snapshot("task_running", "thread_001")
    follow_up_snapshot = _snapshot("task_follow_up", "thread_001")
    _persist_snapshot(task_root, running_snapshot)
    _persist_snapshot(task_root, follow_up_snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="demo task",
        session_name="Demo task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path=running_snapshot.repo_path,
        workdir=running_snapshot.workdir,
        current_task_id=running_snapshot.task_id,
        last_task_snapshot_file=f"snapshots/{running_snapshot.task_id}.json",
        task_root=task_root,
        status=THREAD_STATUS_RUNNING,
        history_files=[],
        last_summary=None,
        queued_task_id=follow_up_snapshot.task_id,
        queued_snapshot_file=f"snapshots/{follow_up_snapshot.task_id}.json",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0)), max_active_sessions=2)
    state = load_thread_state("thread_001", task_root)

    assert runner.queued_count() == 1
    assert state.status == THREAD_STATUS_ACCEPTED
    assert state.current_task_id == "task_follow_up"
    assert state.queued_task_id is None


def test_runner_main_supports_demo_config(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    (repo_dir / "src").mkdir(parents=True)
    seed_path = tmp_path / "seed.json"
    seed_payload = _seed_payload()
    seed_payload["backend"] = "codex"
    seed_payload["repo_path"] = str(repo_dir)
    seed_path.write_text(json.dumps(seed_payload), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "task_root: tasks",
                "opencode_command: demo",
                "codex_command: demo",
                "codex_transport_default: cli",
                "mock_sleep_seconds: 0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["--snapshot", str(seed_path), "--task-root", str(tmp_path / "tasks"), "--config", str(config_path)])

    assert exit_code == 0


def test_runner_main_can_auto_create_missing_workdir(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    seed_path = tmp_path / "seed.json"
    seed_payload = _seed_payload()
    seed_payload["repo_path"] = str(repo_dir)
    seed_path.write_text(json.dumps(seed_payload), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "task_root: tasks",
                "opencode_command: demo",
                "codex_command: demo",
                "mock_sleep_seconds: 0.0",
                "auto_create_workdir: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["--snapshot", str(seed_path), "--task-root", str(tmp_path / "tasks"), "--config", str(config_path)])

    assert exit_code == 0
    assert (repo_dir / "src").is_dir()


def test_serial_task_runner_defaults_codex_seed_transport_to_sdk(tmp_path) -> None:
    seed_path = tmp_path / "seed.json"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    seed_path.write_text(
        json.dumps(
            {
                "backend": "codex",
                "repo_path": str(repo_dir),
                "task_text": "Inspect the repo.",
                "workdir": None,
            }
        ),
        encoding="utf-8",
    )
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    runner = SerialTaskRunner(task_root, dispatcher, codex_transport_default="sdk")

    result = runner.start(seed_path)
    state = load_thread_state(result.thread_id, task_root)

    assert result.backend == BACKEND_CODEX
    assert result.backend_transport == "sdk"
    assert state.backend_transport == "sdk"


def test_serial_task_runner_defaults_opencode_seed_transport_to_sdk(tmp_path) -> None:
    seed_path = tmp_path / "seed.json"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    seed_path.write_text(
        json.dumps(
            {
                "backend": "opencode",
                "repo_path": str(repo_dir),
                "task_text": "Inspect the repo.",
                "workdir": None,
            }
        ),
        encoding="utf-8",
    )
    task_root = tmp_path / "tasks"
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    runner = SerialTaskRunner(task_root, dispatcher, opencode_transport_default="sdk")

    result = runner.start(seed_path)
    state = load_thread_state(result.thread_id, task_root)

    assert result.backend == BACKEND_OPENCODE
    assert result.backend_transport == "sdk"
    assert state.backend_transport == "sdk"
