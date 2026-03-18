"""Host lifecycle tests for the mail runner service wrapper."""

from __future__ import annotations

from pathlib import Path

from mail_runner.host import (
    HOST_EXIT_ALREADY_RUNNING,
    HOST_EXIT_FAILED,
    HOST_EXIT_OK,
    main,
    resolve_config_path,
    resolve_runtime_dir,
    run_host,
)
from mail_runner.host_lock import RuntimeHostLock
from mail_runner.host_state import load_host_state


def test_run_host_writes_stopped_state_for_clean_loop_exit(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    captured: dict[str, object] = {}

    def fake_loop_runner(config, *, base_dir=None) -> None:
        captured["task_root"] = config.task_root
        captured["base_dir"] = base_dir

    exit_code = run_host(
        config_path=str(config_path),
        runtime_dir=str(runtime_dir),
        configure_logging_fn=lambda: None,
        loop_runner=fake_loop_runner,
    )

    state = load_host_state(runtime_dir)
    assert exit_code == HOST_EXIT_OK
    assert captured["task_root"] == "tasks"
    assert captured["base_dir"] == config_path.resolve().parent
    assert state is not None
    assert state["status"] == "stopped"
    assert state["config_path"] == str(config_path.resolve())
    assert state["runtime_dir"] == str(runtime_dir.resolve())
    assert state["exit_reason"] == "Run loop returned normally."


def test_run_host_writes_failed_state_for_loop_exception(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"

    def fake_loop_runner(config, *, base_dir=None) -> None:
        raise RuntimeError("boom")

    exit_code = run_host(
        config_path=str(config_path),
        runtime_dir=str(runtime_dir),
        configure_logging_fn=lambda: None,
        loop_runner=fake_loop_runner,
    )

    state = load_host_state(runtime_dir)
    assert exit_code == HOST_EXIT_FAILED
    assert state is not None
    assert state["status"] == "failed"
    assert state["exit_reason"] == "RuntimeError: boom"


def test_run_host_refuses_duplicate_runtime_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    lock = RuntimeHostLock(runtime_dir)
    lock.acquire()

    try:
        exit_code = run_host(
            config_path=str(config_path),
            runtime_dir=str(runtime_dir),
            configure_logging_fn=lambda: None,
            loop_runner=lambda config, *, base_dir=None: None,
        )
    finally:
        lock.release()

    assert exit_code == HOST_EXIT_ALREADY_RUNNING
    assert load_host_state(runtime_dir) is None


def test_runtime_host_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    first = RuntimeHostLock(runtime_dir)
    second = RuntimeHostLock(runtime_dir)

    first.acquire()
    first.release()
    second.acquire()
    second.release()


def test_host_main_parses_cli_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_host(**kwargs) -> int:
        captured.update(kwargs)
        return 7

    monkeypatch.setattr("mail_runner.host.run_host", fake_run_host)

    exit_code = main(["--config", "mail_config.yaml", "--runtime-dir", "runtime"])

    assert exit_code == 7
    assert captured == {"config_path": "mail_config.yaml", "runtime_dir": "runtime"}


def test_resolve_helpers_return_absolute_paths(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert resolve_config_path("config.yaml") == config_path.resolve()
    assert resolve_runtime_dir("runtime") == (tmp_path / "runtime").resolve()
