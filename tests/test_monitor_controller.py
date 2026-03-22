"""Controller-script regression tests for focused monitor launch."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_monitor_controller_accepts_empty_worker_arg_list_and_exits_for_missing_thread(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    task_root = tmp_path / "tasks"
    task_root.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "mail_config.loop_30s.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "monitor_mail_runner_controller.ps1"),
            "-ProjectRoot",
            str(project_root),
            "-RuntimeDir",
            str(runtime_dir),
            "-ConfigPath",
            str(config_path),
            "-TaskRoot",
            str(task_root),
            "-ThreadId",
            "thread_missing",
            "-ExitWhenThreadNotActive",
            "-Iterations",
            "1",
        ],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (runtime_dir / "monitor_window_state").exists()
