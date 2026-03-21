"""Monitor-window launcher tests."""

from __future__ import annotations

from pathlib import Path

from mail_runner.models import RunResult, TaskSnapshot, ThreadState
from mail_runner.monitor_windows import MonitorWindowManager


class FakeProcess:
    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


def _snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="codex",
        profile=None,
        permission=None,
        repo_path="E:\\repo",
        workdir="src",
        task_text="Monitor this task.",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-18T01:00:00",
        updated_at="2026-03-18T01:00:00",
        backend_transport="sdk",
    )


def _state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="monitor-thread",
        backend="codex",
        repo_path="E:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status="running",
        history_files=[],
        workspace_id="workspace_001",
        workspace_norm="workspace_001",
        session_id="thread_001",
        session_name="monitor-thread",
        session_norm="monitor-thread",
        created_at="2026-03-18T01:00:00",
        updated_at="2026-03-18T01:00:00",
        lifecycle="active",
        last_active_at="2026-03-18T01:00:00",
    )


def _result() -> RunResult:
    return RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend="codex",
        status="success",
        exit_code=0,
        started_at="2026-03-18T01:00:00",
        finished_at="2026-03-18T01:00:10",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir="runs/task_001/artifacts",
        backend_transport="sdk",
    )


def test_monitor_window_manager_does_not_launch_when_disabled(tmp_path: Path) -> None:
    launched: list[list[str]] = []
    manager = MonitorWindowManager(
        enabled=False,
        project_root=tmp_path,
        task_root=tmp_path / "tasks",
        launcher=lambda command, creationflags, cwd: launched.append(command),
    )

    manager.on_run_started(_state(), _snapshot())

    assert launched == []


def test_monitor_window_manager_launches_focused_thread_window(tmp_path: Path) -> None:
    launched: list[tuple[list[str], int, Path]] = []
    project_root = tmp_path / "repo"
    script_dir = project_root / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "monitor_mail_runner.ps1").write_text("# test\n", encoding="utf-8")

    def launcher(command: list[str], creationflags: int, cwd: Path) -> object:
        launched.append((command, creationflags, cwd))
        return FakeProcess()

    manager = MonitorWindowManager(
        enabled=True,
        project_root=project_root,
        task_root=tmp_path / "tasks",
        config_path=project_root / "mail_config.bot.local.yaml",
        runtime_dir=project_root / "_tmp_live_mail_runner",
        refresh_seconds=9,
        buffer_lines=640,
        history_limit=24,
        launcher=launcher,
    )

    manager.on_run_started(_state(), _snapshot())
    manager.on_run_started(_state(), _snapshot())

    assert len(launched) == 1
    command, creationflags, cwd = launched[0]
    assert command[0].lower() == "powershell.exe"
    assert "-ThreadId" in command
    assert "thread_001" in command
    assert "-TaskRoot" in command
    assert str((tmp_path / "tasks").resolve()) in command
    assert "-MaxBufferLines" in command
    assert "640" in command
    assert "-HistoryLimit" in command
    assert "24" in command
    assert "-ExitWhenThreadNotRunning" in command
    assert "-WindowTitle" in command
    assert "Mail Runner Monitor thread_001" in command
    assert creationflags >= 0
    assert cwd == project_root.resolve()


def test_monitor_window_manager_reopens_after_previous_window_exits(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    script_dir = project_root / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "monitor_mail_runner.ps1").write_text("# test\n", encoding="utf-8")
    launches: list[FakeProcess] = []

    def launcher(command: list[str], creationflags: int, cwd: Path) -> object:
        del command, creationflags, cwd
        process = FakeProcess(exit_code=0 if launches else None)
        launches.append(process)
        return process

    manager = MonitorWindowManager(
        enabled=True,
        project_root=project_root,
        task_root=tmp_path / "tasks",
        launcher=launcher,
    )

    manager.on_run_started(_state(), _snapshot())
    launches[0].exit_code = 0
    manager.on_run_finished(_state(), _result())
    manager.on_run_started(_state(), _snapshot())

    assert len(launches) == 2
