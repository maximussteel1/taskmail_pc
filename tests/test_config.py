"""Configuration loading tests for Phase 0."""

from __future__ import annotations

from pathlib import Path

from mail_runner.config import load_config


def test_load_config_uses_defaults_when_file_is_missing(tmp_path: Path) -> None:
    config = load_config(str(tmp_path / "missing.yaml"))

    assert config.poll_seconds == 30
    assert config.imap_receive_mode == "auto"
    assert config.imap_idle_renew_seconds == 1500
    assert config.default_timeout_minutes == 60
    assert config.max_active_sessions == 4
    assert config.max_active_sessions_per_workspace == 2
    assert config.task_root == "tasks"
    assert config.project_sync_roots == ["D:\\projects", "E:\\projects"]
    assert config.codex_transport_default == "sdk"
    assert config.codex_sdk_sidecar_command == ""
    assert config.spawn_monitor_windows is False
    assert config.monitor_window_refresh_seconds == 5
    assert config.monitor_window_buffer_lines == 1000
    assert config.monitor_window_history_limit == 12
    assert config.outbound_transport == "email"
    assert config.relay_url == ""
    assert config.relay_transport_token == ""
    assert config.relay_timeout_seconds == 15
    assert config.relay_verify_tls is True
    assert config.relay_auto_fallback_email is False


def test_load_config_reads_yaml_values_and_ignores_removed_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "imap_host: imap.example.com",
                "imap_port: 1993",
                "poll_seconds: 45",
                "imap_receive_mode: idle",
                "imap_idle_renew_seconds: 900",
                "task_root: runtime_tasks",
                "max_active_sessions: 4",
                "max_active_sessions_per_workspace: 2",
                "auto_create_workdir: true",
                "spawn_monitor_windows: true",
                "monitor_window_refresh_seconds: 7",
                "monitor_window_buffer_lines: 600",
                "monitor_window_history_limit: 20",
                "codex_transport_default: cli",
                "codex_sdk_sidecar_command: node scripts/codex_sdk_sidecar/dist/index.js",
                "outbound_transport: relay",
                "relay_url: wss://relay.example.com/relay",
                "relay_transport_token: relay-secret",
                "relay_timeout_seconds: 22",
                "relay_verify_tls: false",
                "relay_auto_fallback_email: true",
                "prune_old_status_mails: true",
                "project_sync_roots:",
                "  - D:\\custom_projects",
                "  - E:\\more_projects",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.imap_host == "imap.example.com"
    assert config.imap_port == 1993
    assert config.poll_seconds == 45
    assert config.imap_receive_mode == "idle"
    assert config.imap_idle_renew_seconds == 900
    assert config.max_active_sessions == 4
    assert config.max_active_sessions_per_workspace == 2
    assert config.task_root == "runtime_tasks"
    assert config.auto_create_workdir is True
    assert config.spawn_monitor_windows is True
    assert config.monitor_window_refresh_seconds == 7
    assert config.monitor_window_buffer_lines == 600
    assert config.monitor_window_history_limit == 20
    assert config.codex_transport_default == "cli"
    assert config.codex_sdk_sidecar_command == "node scripts/codex_sdk_sidecar/dist/index.js"
    assert config.outbound_transport == "relay"
    assert config.relay_url == "wss://relay.example.com/relay"
    assert config.relay_transport_token == "relay-secret"
    assert config.relay_timeout_seconds == 22
    assert config.relay_verify_tls is False
    assert config.relay_auto_fallback_email is True
    assert not hasattr(config, "prune_old_status_mails")
    assert config.project_sync_roots == ["D:\\custom_projects", "E:\\more_projects"]


def test_environment_variables_override_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "poll_seconds: 10\nmax_active_sessions: 1\nmax_active_sessions_per_workspace: 1\nauto_create_workdir: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIL_RUNNER_POLL_SECONDS", "90")
    monkeypatch.setenv("MAIL_RUNNER_IMAP_RECEIVE_MODE", "poll")
    monkeypatch.setenv("MAIL_RUNNER_IMAP_IDLE_RENEW_SECONDS", "600")
    monkeypatch.setenv("MAIL_RUNNER_MAX_ACTIVE_SESSIONS", "3")
    monkeypatch.setenv("MAIL_RUNNER_MAX_ACTIVE_SESSIONS_PER_WORKSPACE", "2")
    monkeypatch.setenv("MAIL_RUNNER_FROM_NAME", "Integration Runner")
    monkeypatch.setenv("MAIL_RUNNER_AUTO_CREATE_WORKDIR", "true")
    monkeypatch.setenv("MAIL_RUNNER_SPAWN_MONITOR_WINDOWS", "true")
    monkeypatch.setenv("MAIL_RUNNER_MONITOR_WINDOW_REFRESH_SECONDS", "9")
    monkeypatch.setenv("MAIL_RUNNER_MONITOR_WINDOW_BUFFER_LINES", "700")
    monkeypatch.setenv("MAIL_RUNNER_MONITOR_WINDOW_HISTORY_LIMIT", "18")
    monkeypatch.setenv("MAIL_RUNNER_PROJECT_SYNC_ROOTS", "D:\\alpha;E:\\beta")
    monkeypatch.setenv("MAIL_RUNNER_CODEX_TRANSPORT_DEFAULT", "cli")

    config = load_config(str(config_path))

    assert config.poll_seconds == 90
    assert config.imap_receive_mode == "poll"
    assert config.imap_idle_renew_seconds == 600
    assert config.max_active_sessions == 3
    assert config.max_active_sessions_per_workspace == 2
    assert config.from_name == "Integration Runner"
    assert config.auto_create_workdir is True
    assert config.spawn_monitor_windows is True
    assert config.monitor_window_refresh_seconds == 9
    assert config.monitor_window_buffer_lines == 700
    assert config.monitor_window_history_limit == 18
    assert config.codex_transport_default == "cli"
    assert not hasattr(config, "prune_old_status_mails")
    assert config.project_sync_roots == ["D:\\alpha", "E:\\beta"]


def test_load_config_accepts_legacy_max_concurrent_runs_keys(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("max_concurrent_runs: 3\n", encoding="utf-8")
    monkeypatch.setenv("MAIL_RUNNER_MAX_CONCURRENT_RUNS", "5")

    config = load_config(str(config_path))

    assert config.max_active_sessions == 5


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


def test_load_config_reads_cos_delivery_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "cos_region: ap-shanghai",
                "cos_bucket: mailbot-1412015279",
                "cos_secret_id: secret-id",
                "cos_secret_key: secret-key",
                "cos_object_prefix: mail-runner",
                "external_delivery_threshold_mb: 25",
                "cos_presign_expire_seconds: 86400",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.cos_region == "ap-shanghai"
    assert config.cos_bucket == "mailbot-1412015279"
    assert config.cos_secret_id == "secret-id"
    assert config.cos_secret_key == "secret-key"
    assert config.cos_object_prefix == "mail-runner"
    assert config.external_delivery_threshold_mb == 25
    assert config.cos_presign_expire_seconds == 86400
