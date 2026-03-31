from __future__ import annotations

import os

import pytest

from mail_runner.relay_server.config import RelayServerConfig, load_relay_server_config


def test_load_relay_server_config_reads_explicit_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIL_RUNNER_TASK_ROOT", raising=False)
    config = load_relay_server_config(
        host="0.0.0.0",
        port=9797,
        transport_token="relay-secret",
        log_level="debug",
        server_name="relay-dev",
    )

    assert config == RelayServerConfig(
        host="0.0.0.0",
        port=9797,
        transport_token="relay-secret",
        android_app_token="",
        state_dir="relay_state",
        pc_control_credentials_path="",
        task_root="",
        smtp_host="",
        smtp_port=465,
        smtp_user="",
        smtp_password="",
        from_name="Mail Runner Relay",
        from_addr="",
        tls_certfile=None,
        tls_keyfile=None,
        log_level="DEBUG",
        server_name="relay-dev",
        action_log_enabled=True,
    )


def test_load_relay_server_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIL_RUNNER_TASK_ROOT", raising=False)
    monkeypatch.setenv("MAIL_RELAY_HOST", "127.0.0.2")
    monkeypatch.setenv("MAIL_RELAY_PORT", "9898")
    monkeypatch.setenv("MAIL_RELAY_TOKEN", "env-token")
    monkeypatch.setenv("MAIL_RELAY_LOG_LEVEL", "warning")
    monkeypatch.setenv("MAIL_RELAY_SERVER_NAME", "relay-env")

    config = load_relay_server_config()

    assert config == RelayServerConfig(
        host="127.0.0.2",
        port=9898,
        transport_token="env-token",
        android_app_token="",
        state_dir="relay_state",
        pc_control_credentials_path="",
        task_root="",
        smtp_host="",
        smtp_port=465,
        smtp_user="",
        smtp_password="",
        from_name="Mail Runner Relay",
        from_addr="",
        tls_certfile=None,
        tls_keyfile=None,
        log_level="WARNING",
        server_name="relay-env",
        action_log_enabled=True,
    )


def test_load_relay_server_config_reads_taskmail_direct_bridge_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIL_RUNNER_TASK_ROOT", raising=False)
    monkeypatch.setenv("MAIL_RELAY_TOKEN", "env-token")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_BOT_MAILBOX_ADDR", "bot@example.com")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_DIRECT_FROM_NAME", "TaskMail Bridge")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_DIRECT_FROM_ADDR", "taskmail-user@example.com")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_DIRECT_SMTP_HOST", "smtp.user.example.com")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_DIRECT_SMTP_PORT", "587")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_DIRECT_SMTP_USER", "taskmail-user@example.com")
    monkeypatch.setenv("MAIL_RELAY_TASKMAIL_DIRECT_SMTP_PASSWORD", "user-secret")

    config = load_relay_server_config()

    assert config.taskmail_bot_mailbox_addr == "bot@example.com"
    assert config.taskmail_direct_from_name == "TaskMail Bridge"
    assert config.taskmail_direct_from_addr == "taskmail-user@example.com"
    assert config.taskmail_direct_smtp_host == "smtp.user.example.com"
    assert config.taskmail_direct_smtp_port == 587
    assert config.taskmail_direct_smtp_user == "taskmail-user@example.com"
    assert config.taskmail_direct_smtp_password == "user-secret"
    assert config.taskmail_direct_ingress_enabled is True


def test_load_relay_server_config_reads_android_app_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_RELAY_TOKEN", "env-token")
    monkeypatch.setenv("MAIL_RELAY_ANDROID_APP_TOKEN", "android-secret")

    config = load_relay_server_config()

    assert config.android_app_token == "android-secret"
    assert config.action_log_enabled is True


def test_load_relay_server_config_reads_action_log_enabled_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_RELAY_TOKEN", "env-token")
    monkeypatch.setenv("MAIL_RELAY_ACTION_LOG_ENABLED", "false")

    config = load_relay_server_config()

    assert config.action_log_enabled is False


def test_load_relay_server_config_reads_task_root_from_mail_runner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_RELAY_TOKEN", "env-token")
    monkeypatch.setenv("MAIL_RUNNER_TASK_ROOT", "/srv/mail-runner/tasks")

    config = load_relay_server_config()

    assert config.task_root == "/srv/mail-runner/tasks"


def test_load_relay_server_config_reads_android_projection_store_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_RELAY_TOKEN", "env-token")
    monkeypatch.setenv("MAIL_RELAY_ANDROID_PROJECTION_STORE_PATH", "/srv/mail-runner/projection.sqlite3")

    config = load_relay_server_config()

    assert config.android_projection_store_path == "/srv/mail-runner/projection.sqlite3"
    assert config.resolved_android_projection_store_path == "/srv/mail-runner/projection.sqlite3"


def test_relay_server_config_defaults_projection_store_path_under_state_dir() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
        state_dir="relay_state",
    )

    assert config.android_projection_store_path == ""
    assert config.resolved_android_projection_store_path == os.path.join(
        "relay_state",
        "android_projection_store.sqlite3",
    )


def test_relay_server_config_rejects_missing_transport_token() -> None:
    with pytest.raises(ValueError, match="transport_token must be a non-empty string"):
        RelayServerConfig(host="127.0.0.1", port=8787, transport_token="")


def test_relay_server_config_rejects_partial_taskmail_direct_bridge() -> None:
    with pytest.raises(
        ValueError,
        match="taskmail_bot_mailbox_addr and taskmail_direct_from_addr must be provided together",
    ):
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            taskmail_bot_mailbox_addr="bot@example.com",
        )


def test_taskmail_direct_mail_config_uses_dedicated_smtp_when_present() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
        smtp_host="smtp.relay.example.com",
        smtp_user="relay@example.com",
        smtp_password="relay-secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
        taskmail_direct_smtp_host="smtp.user.example.com",
        taskmail_direct_smtp_port=587,
        taskmail_direct_smtp_user="taskmail-user@example.com",
        taskmail_direct_smtp_password="user-secret",
    )

    mail_config = config.to_taskmail_direct_mail_config()

    assert mail_config.smtp_host == "smtp.user.example.com"
    assert mail_config.smtp_port == 587
    assert mail_config.smtp_user == "taskmail-user@example.com"
    assert mail_config.smtp_password == "user-secret"
    assert mail_config.from_addr == "taskmail-user@example.com"


def test_relay_server_config_rejects_partial_taskmail_direct_smtp() -> None:
    with pytest.raises(
        ValueError,
        match="taskmail_direct_smtp_host, taskmail_direct_smtp_user, and taskmail_direct_smtp_password must be provided together",
    ):
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
            taskmail_direct_smtp_host="smtp.user.example.com",
        )
