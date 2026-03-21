from __future__ import annotations

import pytest

from mail_runner.relay_server.config import RelayServerConfig, load_relay_server_config


def test_load_relay_server_config_reads_explicit_values() -> None:
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
        state_dir="relay_state",
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
    )


def test_load_relay_server_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
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
        state_dir="relay_state",
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
    )


def test_relay_server_config_rejects_missing_transport_token() -> None:
    with pytest.raises(ValueError, match="transport_token must be a non-empty string"):
        RelayServerConfig(host="127.0.0.1", port=8787, transport_token="")
