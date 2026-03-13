"""Configuration loading tests for Phase 0."""

from __future__ import annotations

from pathlib import Path

from mail_runner.config import load_config


def test_load_config_uses_defaults_when_file_is_missing(tmp_path: Path) -> None:
    config = load_config(str(tmp_path / "missing.yaml"))

    assert config.poll_seconds == 30
    assert config.default_timeout_minutes == 60
    assert config.max_concurrent_runs == 2
    assert config.task_root == "tasks"


def test_load_config_reads_yaml_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "imap_host: imap.example.com",
                "imap_port: 1993",
                "poll_seconds: 45",
                "task_root: runtime_tasks",
                "max_concurrent_runs: 4",
                "auto_create_workdir: true",
                "prune_old_status_mails: true",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.imap_host == "imap.example.com"
    assert config.imap_port == 1993
    assert config.poll_seconds == 45
    assert config.max_concurrent_runs == 4
    assert config.task_root == "runtime_tasks"
    assert config.auto_create_workdir is True
    assert config.prune_old_status_mails is True


def test_environment_variables_override_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "poll_seconds: 10\nmax_concurrent_runs: 1\nauto_create_workdir: false\nprune_old_status_mails: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIL_RUNNER_POLL_SECONDS", "90")
    monkeypatch.setenv("MAIL_RUNNER_MAX_CONCURRENT_RUNS", "3")
    monkeypatch.setenv("MAIL_RUNNER_FROM_NAME", "Integration Runner")
    monkeypatch.setenv("MAIL_RUNNER_AUTO_CREATE_WORKDIR", "true")
    monkeypatch.setenv("MAIL_RUNNER_PRUNE_OLD_STATUS_MAILS", "true")

    config = load_config(str(config_path))

    assert config.poll_seconds == 90
    assert config.max_concurrent_runs == 3
    assert config.from_name == "Integration Runner"
    assert config.auto_create_workdir is True
    assert config.prune_old_status_mails is True


def test_load_config_reads_profile_model_mappings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "opencode_profile_models:",
                "  fast: provider/op-fast",
                "codex_profile_models:",
                "  strong: gpt-5-codex",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.opencode_profile_models == {"fast": "provider/op-fast"}
    assert config.codex_profile_models == {"strong": "gpt-5-codex"}
