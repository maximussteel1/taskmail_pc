"""Relay server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import AppConfig

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(slots=True)
class RelayServerConfig:
    host: str
    port: int
    transport_token: str
    state_dir: str = "relay_state"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    from_name: str = "Mail Runner Relay"
    from_addr: str = ""
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    log_level: str = "INFO"
    server_name: str = "mail-runner-relay"

    def __post_init__(self) -> None:
        _require_text(self.host, "host")
        if not isinstance(self.port, int) or not (0 <= self.port <= 65535):
            raise ValueError("port must be an integer between 0 and 65535")
        _require_text(self.transport_token, "transport_token")
        _require_text(self.state_dir, "state_dir")
        if not isinstance(self.smtp_port, int) or not (1 <= self.smtp_port <= 65535):
            raise ValueError("smtp_port must be an integer between 1 and 65535")
        if self.smtp_host:
            _require_text(self.smtp_host, "smtp_host")
        if self.smtp_user:
            _require_text(self.smtp_user, "smtp_user")
        if self.smtp_password:
            _require_text(self.smtp_password, "smtp_password")
        if self.from_name:
            _require_text(self.from_name, "from_name")
        if self.from_addr:
            _require_text(self.from_addr, "from_addr")
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise ValueError("tls_certfile and tls_keyfile must be provided together")
        if self.tls_certfile is not None:
            _require_text(self.tls_certfile, "tls_certfile")
        if self.tls_keyfile is not None:
            _require_text(self.tls_keyfile, "tls_keyfile")
        _require_text(self.log_level, "log_level")
        self.log_level = self.log_level.strip().upper()
        if self.log_level not in _LOG_LEVELS:
            allowed = ", ".join(sorted(_LOG_LEVELS))
            raise ValueError(f"log_level must be one of: {allowed}")
        _require_text(self.server_name, "server_name")

    def to_mail_config(self) -> AppConfig:
        _require_text(self.smtp_host, "smtp_host")
        _require_text(self.smtp_user, "smtp_user")
        _require_text(self.smtp_password, "smtp_password")
        _require_text(self.from_name, "from_name")
        _require_text(self.from_addr, "from_addr")
        return AppConfig(
            smtp_host=self.smtp_host,
            smtp_port=self.smtp_port,
            smtp_user=self.smtp_user,
            smtp_password=self.smtp_password,
            from_name=self.from_name,
            from_addr=self.from_addr,
        )


def load_relay_server_config(
    *,
    host: str | None = None,
    port: int | str | None = None,
    transport_token: str | None = None,
    state_dir: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int | str | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    from_name: str | None = None,
    from_addr: str | None = None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
    log_level: str | None = None,
    server_name: str | None = None,
) -> RelayServerConfig:
    raw_port = port if port is not None else os.getenv("MAIL_RELAY_PORT", "8787")
    raw_smtp_port = smtp_port if smtp_port is not None else os.getenv("MAIL_RELAY_SMTP_PORT", "465")
    return RelayServerConfig(
        host=(host or os.getenv("MAIL_RELAY_HOST", "127.0.0.1")).strip(),
        port=int(raw_port),
        transport_token=(transport_token or os.getenv("MAIL_RELAY_TOKEN", "")).strip(),
        state_dir=(state_dir or os.getenv("MAIL_RELAY_STATE_DIR", "relay_state")).strip(),
        smtp_host=(smtp_host or os.getenv("MAIL_RELAY_SMTP_HOST", "")).strip(),
        smtp_port=int(raw_smtp_port),
        smtp_user=(smtp_user or os.getenv("MAIL_RELAY_SMTP_USER", "")).strip(),
        smtp_password=(smtp_password or os.getenv("MAIL_RELAY_SMTP_PASSWORD", "")).strip(),
        from_name=(from_name or os.getenv("MAIL_RELAY_FROM_NAME", "Mail Runner Relay")).strip(),
        from_addr=(from_addr or os.getenv("MAIL_RELAY_FROM_ADDR", "")).strip(),
        tls_certfile=(tls_certfile or os.getenv("MAIL_RELAY_TLS_CERTFILE", "")).strip() or None,
        tls_keyfile=(tls_keyfile or os.getenv("MAIL_RELAY_TLS_KEYFILE", "")).strip() or None,
        log_level=(log_level or os.getenv("MAIL_RELAY_LOG_LEVEL", "INFO")).strip(),
        server_name=(server_name or os.getenv("MAIL_RELAY_SERVER_NAME", "mail-runner-relay")).strip(),
    )
