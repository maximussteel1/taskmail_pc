from __future__ import annotations

from mail_runner.relay_server.deploy import RelayDeploymentConfig, relay_bundle_members, render_env_file, render_systemd_unit


def test_render_env_file_includes_relay_runtime_values() -> None:
    config = RelayDeploymentConfig(
        bind_host="0.0.0.0",
        port=8787,
        log_level="debug",
        server_name="relay-dev",
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
    assert "MAIL_RELAY_SMTP_HOST=smtp.example.com" in env_text
    assert "MAIL_RELAY_SMTP_USER=bot@example.com" in env_text
    assert "MAIL_RELAY_FROM_ADDR=bot@example.com" in env_text
    assert "MAIL_RELAY_LOG_LEVEL=DEBUG" in env_text
    assert env_text.endswith("\n")


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
