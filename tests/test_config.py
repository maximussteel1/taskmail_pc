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
    assert config.new_task_max_age_minutes == 0
    assert config.max_active_sessions == 4
    assert config.max_active_sessions_per_workspace == 4
    assert config.max_running_sessions == 4
    assert config.max_running_sessions_per_workspace == 2
    assert config.task_root == "tasks"
    assert config.project_sync_roots == ["D:\\projects", "E:\\projects"]
    assert config.opencode_transport_default == "sdk"
    assert config.codex_transport_default == "sdk"
    assert config.codex_sdk_sidecar_command == ""
    assert config.spawn_active_session_windows is False
    assert config.active_session_window_refresh_seconds == 5
    assert config.active_session_window_buffer_lines == 1000
    assert config.active_session_window_history_limit == 12
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
    assert config.control_plane_mode == "hybrid"
    assert config.relay_mailbox_lease_mode == "disabled"
    assert config.relay_mailbox_lease_ttl_seconds == 45
    assert config.external_delivery_backend_preference == "file_surface"


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
                "new_task_max_age_minutes: 75",
                "max_active_sessions: 4",
                "max_active_sessions_per_workspace: 2",
                "max_running_sessions: 3",
                "max_running_sessions_per_workspace: 1",
                "auto_create_workdir: true",
                "spawn_active_session_windows: true",
                "active_session_window_refresh_seconds: 7",
                "active_session_window_buffer_lines: 600",
                "active_session_window_history_limit: 20",
                "opencode_transport_default: cli",
                "codex_transport_default: cli",
                "codex_sdk_sidecar_command: node scripts/codex_sdk_sidecar/dist/index.js",
                "outbound_transport: relay",
                "relay_url: wss://relay.example.com/relay",
                "relay_transport_token: relay-secret",
                "relay_timeout_seconds: 22",
                "relay_verify_tls: false",
                "relay_auto_fallback_email: true",
                "control_plane_mode: vps_only",
                "relay_mailbox_lease_mode: strict",
                "relay_mailbox_lease_ttl_seconds: 75",
                "external_delivery_backend_preference: file_surface",
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
    assert config.new_task_max_age_minutes == 75
    assert config.max_active_sessions == 4
    assert config.max_active_sessions_per_workspace == 2
    assert config.max_running_sessions == 3
    assert config.max_running_sessions_per_workspace == 1
    assert config.task_root == "runtime_tasks"
    assert config.auto_create_workdir is True
    assert config.spawn_active_session_windows is True
    assert config.active_session_window_refresh_seconds == 7
    assert config.active_session_window_buffer_lines == 600
    assert config.active_session_window_history_limit == 20
    assert config.spawn_monitor_windows is True
    assert config.monitor_window_refresh_seconds == 7
    assert config.monitor_window_buffer_lines == 600
    assert config.monitor_window_history_limit == 20
    assert config.opencode_transport_default == "cli"
    assert config.codex_transport_default == "cli"
    assert config.codex_sdk_sidecar_command == "node scripts/codex_sdk_sidecar/dist/index.js"
    assert config.outbound_transport == "relay"
    assert config.relay_url == "wss://relay.example.com/relay"
    assert config.relay_transport_token == "relay-secret"
    assert config.relay_timeout_seconds == 22
    assert config.relay_verify_tls is False
    assert config.relay_auto_fallback_email is True
    assert config.control_plane_mode == "vps_only"
    assert config.relay_mailbox_lease_mode == "strict"
    assert config.relay_mailbox_lease_ttl_seconds == 75
    assert config.external_delivery_backend_preference == "file_surface"
    assert not hasattr(config, "prune_old_status_mails")
    assert config.project_sync_roots == ["D:\\custom_projects", "E:\\more_projects"]


def test_environment_variables_override_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "poll_seconds: 10\n"
            "max_active_sessions: 1\n"
            "max_active_sessions_per_workspace: 1\n"
            "max_running_sessions: 1\n"
            "max_running_sessions_per_workspace: 1\n"
            "auto_create_workdir: false\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIL_RUNNER_POLL_SECONDS", "90")
    monkeypatch.setenv("MAIL_RUNNER_IMAP_RECEIVE_MODE", "poll")
    monkeypatch.setenv("MAIL_RUNNER_IMAP_IDLE_RENEW_SECONDS", "600")
    monkeypatch.setenv("MAIL_RUNNER_NEW_TASK_MAX_AGE_MINUTES", "120")
    monkeypatch.setenv("MAIL_RUNNER_MAX_ACTIVE_SESSIONS", "3")
    monkeypatch.setenv("MAIL_RUNNER_MAX_ACTIVE_SESSIONS_PER_WORKSPACE", "2")
    monkeypatch.setenv("MAIL_RUNNER_MAX_RUNNING_SESSIONS", "4")
    monkeypatch.setenv("MAIL_RUNNER_MAX_RUNNING_SESSIONS_PER_WORKSPACE", "1")
    monkeypatch.setenv("MAIL_RUNNER_FROM_NAME", "Integration Runner")
    monkeypatch.setenv("MAIL_RUNNER_AUTO_CREATE_WORKDIR", "true")
    monkeypatch.setenv("MAIL_RUNNER_SPAWN_ACTIVE_SESSION_WINDOWS", "true")
    monkeypatch.setenv("MAIL_RUNNER_ACTIVE_SESSION_WINDOW_REFRESH_SECONDS", "9")
    monkeypatch.setenv("MAIL_RUNNER_ACTIVE_SESSION_WINDOW_BUFFER_LINES", "700")
    monkeypatch.setenv("MAIL_RUNNER_ACTIVE_SESSION_WINDOW_HISTORY_LIMIT", "18")
    monkeypatch.setenv("MAIL_RUNNER_PROJECT_SYNC_ROOTS", "D:\\alpha;E:\\beta")
    monkeypatch.setenv("MAIL_RUNNER_OPENCODE_TRANSPORT_DEFAULT", "cli")
    monkeypatch.setenv("MAIL_RUNNER_CODEX_TRANSPORT_DEFAULT", "cli")
    monkeypatch.setenv("MAIL_RUNNER_CONTROL_PLANE_MODE", "mail_first")
    monkeypatch.setenv("MAIL_RUNNER_RELAY_MAILBOX_LEASE_MODE", "degraded")
    monkeypatch.setenv("MAIL_RUNNER_RELAY_MAILBOX_LEASE_TTL_SECONDS", "90")

    config = load_config(str(config_path))

    assert config.poll_seconds == 90
    assert config.imap_receive_mode == "poll"
    assert config.imap_idle_renew_seconds == 600
    assert config.new_task_max_age_minutes == 120
    assert config.max_active_sessions == 3
    assert config.max_active_sessions_per_workspace == 2
    assert config.max_running_sessions == 4
    assert config.max_running_sessions_per_workspace == 1
    assert config.from_name == "Integration Runner"
    assert config.auto_create_workdir is True
    assert config.spawn_active_session_windows is True
    assert config.active_session_window_refresh_seconds == 9
    assert config.active_session_window_buffer_lines == 700
    assert config.active_session_window_history_limit == 18
    assert config.spawn_monitor_windows is True
    assert config.monitor_window_refresh_seconds == 9
    assert config.monitor_window_buffer_lines == 700
    assert config.monitor_window_history_limit == 18
    assert config.opencode_transport_default == "cli"
    assert config.codex_transport_default == "cli"
    assert config.control_plane_mode == "mail_first"
    assert config.relay_mailbox_lease_mode == "degraded"
    assert config.relay_mailbox_lease_ttl_seconds == 90
    assert not hasattr(config, "prune_old_status_mails")
    assert config.project_sync_roots == ["D:\\alpha", "E:\\beta"]


def test_load_config_accepts_legacy_max_concurrent_runs_keys(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("max_concurrent_runs: 3\n", encoding="utf-8")
    monkeypatch.setenv("MAIL_RUNNER_MAX_CONCURRENT_RUNS", "5")

    config = load_config(str(config_path))

    assert config.max_active_sessions == 5
    assert config.max_running_sessions == 5


def test_load_config_allows_split_active_and_running_workspace_caps(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "max_active_sessions_per_workspace: 2\nmax_running_sessions_per_workspace: 1\n",
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.max_active_sessions_per_workspace == 2
    assert config.max_running_sessions_per_workspace == 1


def test_load_config_accepts_legacy_monitor_window_keys(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "spawn_monitor_windows: false",
                "monitor_window_refresh_seconds: 4",
                "monitor_window_buffer_lines: 222",
                "monitor_window_history_limit: 11",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIL_RUNNER_SPAWN_MONITOR_WINDOWS", "true")
    monkeypatch.setenv("MAIL_RUNNER_MONITOR_WINDOW_REFRESH_SECONDS", "13")
    monkeypatch.setenv("MAIL_RUNNER_MONITOR_WINDOW_BUFFER_LINES", "333")
    monkeypatch.setenv("MAIL_RUNNER_MONITOR_WINDOW_HISTORY_LIMIT", "14")

    config = load_config(str(config_path))

    assert config.spawn_active_session_windows is True
    assert config.active_session_window_refresh_seconds == 13
    assert config.active_session_window_buffer_lines == 333
    assert config.active_session_window_history_limit == 14


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


def test_load_config_rejects_unknown_external_delivery_backend_preference(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("external_delivery_backend_preference: relay_only\n", encoding="utf-8")

    try:
        load_config(str(config_path))
    except ValueError as exc:
        assert "external_delivery_backend_preference" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("load_config should reject unknown external_delivery_backend_preference")


def test_load_config_rejects_unknown_control_plane_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("control_plane_mode: relay_only\n", encoding="utf-8")

    try:
        load_config(str(config_path))
    except ValueError as exc:
        assert "control_plane_mode" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("load_config should reject unknown control_plane_mode")
