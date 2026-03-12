"""Bootstrap tests for the Phase 0 entrypoint."""

from __future__ import annotations

from pathlib import Path

from mail_runner.app import main


def test_main_bootstrap_exits_successfully(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("task_root: tmp_tasks\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert (tmp_path / "tmp_tasks").exists()
