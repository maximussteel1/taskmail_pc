from __future__ import annotations

import os

import pytest

from mail_runner.relay_server.deploy import RelayDeploymentConfig, relay_bundle_members, render_env_file, render_systemd_unit
from scripts.deploy_relay_server import _scp_base_args, _ssh_base_args


def test_render_env_file_includes_relay_runtime_values() -> None:
    config = RelayDeploymentConfig(
        bind_host="0.0.0.0",
        port=8787,
        log_level="debug",
        server_name="relay-dev",
        task_root="/opt/mail_runner_relay/shared/task_root",
        smtp_host="smtp.example.com",
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_addr="bot@example.com",
    )

    env_text = render_env_file(config, transport_token="relay-secret")

    assert "MAIL_RELAY_HOST=0.0.0.0" in env_text
    assert "MAIL_RELAY_PORT=8787" in env_text
    assert "MAIL_RELAY_TOKEN=relay-secret" in env_text
    assert "MAIL_RELAY_STATE_DIR=/opt/mail_runner_relay/shared/state" in env_text
    assert "MAIL_RUNNER_TASK_ROOT=/opt/mail_runner_relay/shared/task_root" in env_text
    assert "MAIL_RELAY_SMTP_HOST=smtp.example.com" in env_text
    assert "MAIL_RELAY_SMTP_USER=bot@example.com" in env_text
    assert "MAIL_RELAY_FROM_ADDR=bot@example.com" in env_text
    assert "MAIL_RELAY_LOG_LEVEL=DEBUG" in env_text
    assert env_text.endswith("\n")


def test_render_env_file_includes_taskmail_direct_bridge_values_when_enabled() -> None:
    config = RelayDeploymentConfig(
        bind_host="0.0.0.0",
        port=8787,
        smtp_host="smtp.example.com",
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_name="TaskMail Bridge",
        taskmail_direct_from_addr="taskmail-user@example.com",
        taskmail_direct_smtp_host="smtp.user.example.com",
        taskmail_direct_smtp_port=587,
        taskmail_direct_smtp_user="taskmail-user@example.com",
        taskmail_direct_smtp_password="user-secret",
    )

    env_text = render_env_file(config, transport_token="relay-secret")

    assert "MAIL_RELAY_TASKMAIL_BOT_MAILBOX_ADDR=bot@example.com" in env_text
    assert "MAIL_RELAY_TASKMAIL_DIRECT_FROM_NAME=TaskMail Bridge" in env_text
    assert "MAIL_RELAY_TASKMAIL_DIRECT_FROM_ADDR=taskmail-user@example.com" in env_text
    assert "MAIL_RELAY_TASKMAIL_DIRECT_SMTP_HOST=smtp.user.example.com" in env_text
    assert "MAIL_RELAY_TASKMAIL_DIRECT_SMTP_PORT=587" in env_text
    assert "MAIL_RELAY_TASKMAIL_DIRECT_SMTP_USER=taskmail-user@example.com" in env_text
    assert "MAIL_RELAY_TASKMAIL_DIRECT_SMTP_PASSWORD=user-secret" in env_text


def test_render_systemd_unit_points_to_current_release_and_env_file() -> None:
    config = RelayDeploymentConfig(
        service_name="mail-runner-relay",
        remote_base_dir="/opt/mail_runner_relay",
        env_file_path="/etc/mail-runner-relay.env",
        run_user="ubuntu",
        smtp_host="smtp.example.com",
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_addr="bot@example.com",
    )

    unit_text = render_systemd_unit(config)

    assert "Description=Mail Runner Relay Server" in unit_text
    assert "User=ubuntu" in unit_text
    assert "WorkingDirectory=/opt/mail_runner_relay/current" in unit_text
    assert "EnvironmentFile=/etc/mail-runner-relay.env" in unit_text
    assert "ExecStart=/opt/mail_runner_relay/venv/bin/python -m mail_runner.relay_server.app" in unit_text


def test_relay_bundle_members_cover_package_root_and_relay_directory() -> None:
    assert relay_bundle_members() == [
        "mail_runner",
        "requirements.txt",
    ]


def test_relay_deployment_config_rejects_partial_taskmail_direct_bridge() -> None:
    with pytest.raises(
        ValueError,
        match="taskmail_bot_mailbox_addr and taskmail_direct_from_addr must be provided together",
    ):
        RelayDeploymentConfig(
            smtp_host="smtp.example.com",
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
        )


def test_relay_deployment_config_rejects_partial_taskmail_direct_smtp() -> None:
    with pytest.raises(
        ValueError,
        match="taskmail_direct_smtp_host, taskmail_direct_smtp_user, and taskmail_direct_smtp_password must be provided together",
    ):
        RelayDeploymentConfig(
            smtp_host="smtp.example.com",
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="relay@example.com",
            taskmail_bot_mailbox_addr="bot@example.com",
            taskmail_direct_from_addr="taskmail-user@example.com",
            taskmail_direct_smtp_host="smtp.user.example.com",
        )


def test_deploy_relay_server_ssh_and_scp_ignore_proxy_and_jump_settings(tmp_path) -> None:
    key_path = tmp_path / "work_bot.pem"
    key_path.write_text("demo", encoding="utf-8")

    ssh_args = _ssh_base_args("ubuntu", "relay.example.com", key_path)
    scp_args = _scp_base_args("ubuntu", "relay.example.com", key_path)
    expected_config_path = "NUL" if os.name == "nt" else "/dev/null"

    assert ssh_args[:8] == [
        "ssh",
        "-F",
        expected_config_path,
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
    ]
    assert scp_args[:8] == [
        "scp",
        "-F",
        expected_config_path,
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
    ]
