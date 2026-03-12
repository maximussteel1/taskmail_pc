"""Runner happy-path tests for Phase 1."""

from __future__ import annotations

import json
import time

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import TaskSnapshot
from mail_runner.runner import SerialTaskRunner, main
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_KILLED, RUN_STATUS_SUCCESS, THREAD_STATUS_DONE


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
                "mock_sleep_seconds: 0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["--snapshot", str(seed_path), "--task-root", str(tmp_path / "tasks"), "--config", str(config_path)])

    assert exit_code == 0
