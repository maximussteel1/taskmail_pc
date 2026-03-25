"""Deployment helpers for the VPS relay bootstrap path."""

from __future__ import annotations

from dataclasses import dataclass

_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(slots=True)
class RelayDeploymentConfig:
    service_name: str = "mail-runner-relay"
    remote_base_dir: str = "/opt/mail_runner_relay"
    env_file_path: str = "/etc/mail-runner-relay.env"
    bind_host: str = "0.0.0.0"
    port: int = 8787
    log_level: str = "INFO"
    server_name: str = "mail-runner-relay"
    run_user: str = "ubuntu"
    python_bin: str = "python3"
    state_dir: str = ""
    task_root: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    from_name: str = "Mail Runner Relay"
    from_addr: str = ""
    taskmail_bot_mailbox_addr: str = ""
    taskmail_direct_from_name: str = "TaskMail User"
    taskmail_direct_from_addr: str = ""
    taskmail_direct_smtp_host: str = ""
    taskmail_direct_smtp_port: int = 465
    taskmail_direct_smtp_user: str = ""
    taskmail_direct_smtp_password: str = ""
    tls_certfile: str = ""
    tls_keyfile: str = ""

    def __post_init__(self) -> None:
        self.service_name = _require_text(self.service_name, "service_name")
        self.remote_base_dir = _require_text(self.remote_base_dir, "remote_base_dir")
        self.env_file_path = _require_text(self.env_file_path, "env_file_path")
        self.bind_host = _require_text(self.bind_host, "bind_host")
        if not isinstance(self.port, int) or not (1 <= self.port <= 65535):
            raise ValueError("port must be an integer between 1 and 65535")
        self.log_level = _require_text(self.log_level, "log_level").upper()
        if self.log_level not in _ALLOWED_LOG_LEVELS:
            allowed = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            raise ValueError(f"log_level must be one of: {allowed}")
        self.server_name = _require_text(self.server_name, "server_name")
        self.run_user = _require_text(self.run_user, "run_user")
        self.python_bin = _require_text(self.python_bin, "python_bin")
        self.state_dir = self.state_dir.strip() or f"{self.remote_base_dir}/shared/state"
        if self.task_root:
            self.task_root = _require_text(self.task_root, "task_root")
        self.smtp_host = _require_text(self.smtp_host, "smtp_host")
        if not isinstance(self.smtp_port, int) or not (1 <= self.smtp_port <= 65535):
            raise ValueError("smtp_port must be an integer between 1 and 65535")
        self.smtp_user = _require_text(self.smtp_user, "smtp_user")
        self.smtp_password = _require_text(self.smtp_password, "smtp_password")
        self.from_name = _require_text(self.from_name, "from_name")
        self.from_addr = _require_text(self.from_addr, "from_addr")
        if self.taskmail_bot_mailbox_addr:
            self.taskmail_bot_mailbox_addr = _require_text(
                self.taskmail_bot_mailbox_addr,
                "taskmail_bot_mailbox_addr",
            )
        if self.taskmail_direct_from_addr:
            self.taskmail_direct_from_addr = _require_text(
                self.taskmail_direct_from_addr,
                "taskmail_direct_from_addr",
            )
        if not isinstance(self.taskmail_direct_smtp_port, int) or not (1 <= self.taskmail_direct_smtp_port <= 65535):
            raise ValueError("taskmail_direct_smtp_port must be an integer between 1 and 65535")
        if self.taskmail_direct_smtp_host:
            self.taskmail_direct_smtp_host = _require_text(
                self.taskmail_direct_smtp_host,
                "taskmail_direct_smtp_host",
            )
        if self.taskmail_direct_smtp_user:
            self.taskmail_direct_smtp_user = _require_text(
                self.taskmail_direct_smtp_user,
                "taskmail_direct_smtp_user",
            )
        if self.taskmail_direct_smtp_password:
            self.taskmail_direct_smtp_password = _require_text(
                self.taskmail_direct_smtp_password,
                "taskmail_direct_smtp_password",
            )
        self.taskmail_direct_from_name = _require_text(
            self.taskmail_direct_from_name,
            "taskmail_direct_from_name",
        )
        if bool(self.taskmail_bot_mailbox_addr) != bool(self.taskmail_direct_from_addr):
            raise ValueError(
                "taskmail_bot_mailbox_addr and taskmail_direct_from_addr must be provided together"
            )
        if any((self.taskmail_direct_smtp_host, self.taskmail_direct_smtp_user, self.taskmail_direct_smtp_password)):
            if not all((self.taskmail_direct_smtp_host, self.taskmail_direct_smtp_user, self.taskmail_direct_smtp_password)):
                raise ValueError(
                    "taskmail_direct_smtp_host, taskmail_direct_smtp_user, and taskmail_direct_smtp_password must be provided together"
                )
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise ValueError("tls_certfile and tls_keyfile must be provided together")

    @property
    def current_dir(self) -> str:
        return f"{self.remote_base_dir}/current"

    @property
    def venv_python(self) -> str:
        return f"{self.remote_base_dir}/venv/bin/python"

    @property
    def stdout_log_path(self) -> str:
        return f"{self.remote_base_dir}/shared/logs/{self.service_name}.stdout.log"

    @property
    def stderr_log_path(self) -> str:
        return f"{self.remote_base_dir}/shared/logs/{self.service_name}.stderr.log"

    @property
    def unit_path(self) -> str:
        return f"/etc/systemd/system/{self.service_name}.service"


def render_env_file(
    config: RelayDeploymentConfig,
    *,
    transport_token: str,
    android_app_token: str | None = None,
) -> str:
    normalized_token = _require_text(transport_token, "transport_token")
    normalized_android_app_token = str(android_app_token or "").strip()
    if normalized_android_app_token:
        normalized_android_app_token = _require_text(normalized_android_app_token, "android_app_token")
    lines = [
        f"MAIL_RELAY_HOST={config.bind_host}",
        f"MAIL_RELAY_PORT={config.port}",
        f"MAIL_RELAY_TOKEN={normalized_token}",
        f"MAIL_RELAY_STATE_DIR={config.state_dir}",
        f"MAIL_RELAY_SMTP_HOST={config.smtp_host}",
        f"MAIL_RELAY_SMTP_PORT={config.smtp_port}",
        f"MAIL_RELAY_SMTP_USER={config.smtp_user}",
        f"MAIL_RELAY_SMTP_PASSWORD={config.smtp_password}",
        f"MAIL_RELAY_FROM_NAME={config.from_name}",
        f"MAIL_RELAY_FROM_ADDR={config.from_addr}",
        f"MAIL_RELAY_TASKMAIL_DIRECT_FROM_NAME={config.taskmail_direct_from_name}",
        f"MAIL_RELAY_LOG_LEVEL={config.log_level}",
        f"MAIL_RELAY_SERVER_NAME={config.server_name}",
    ]
    if normalized_android_app_token:
        lines.append(f"MAIL_RELAY_ANDROID_APP_TOKEN={normalized_android_app_token}")
    if config.task_root:
        lines.append(f"MAIL_RUNNER_TASK_ROOT={config.task_root}")
    if config.taskmail_bot_mailbox_addr:
        lines.append(f"MAIL_RELAY_TASKMAIL_BOT_MAILBOX_ADDR={config.taskmail_bot_mailbox_addr}")
    if config.taskmail_direct_from_addr:
        lines.append(f"MAIL_RELAY_TASKMAIL_DIRECT_FROM_ADDR={config.taskmail_direct_from_addr}")
    if config.taskmail_direct_smtp_host:
        lines.append(f"MAIL_RELAY_TASKMAIL_DIRECT_SMTP_HOST={config.taskmail_direct_smtp_host}")
        lines.append(f"MAIL_RELAY_TASKMAIL_DIRECT_SMTP_PORT={config.taskmail_direct_smtp_port}")
        lines.append(f"MAIL_RELAY_TASKMAIL_DIRECT_SMTP_USER={config.taskmail_direct_smtp_user}")
        lines.append(f"MAIL_RELAY_TASKMAIL_DIRECT_SMTP_PASSWORD={config.taskmail_direct_smtp_password}")
    if config.tls_certfile:
        lines.append(f"MAIL_RELAY_TLS_CERTFILE={config.tls_certfile}")
        lines.append(f"MAIL_RELAY_TLS_KEYFILE={config.tls_keyfile}")
    return "\n".join(lines) + "\n"


def render_systemd_unit(config: RelayDeploymentConfig) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Mail Runner Relay Server",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={config.run_user}",
            f"Group={config.run_user}",
            f"WorkingDirectory={config.current_dir}",
            f"EnvironmentFile={config.env_file_path}",
            "Environment=PYTHONUNBUFFERED=1",
            f"ExecStart={config.venv_python} -m mail_runner.relay_server.app",
            "Restart=on-failure",
            "RestartSec=5",
            f"StandardOutput=append:{config.stdout_log_path}",
            f"StandardError=append:{config.stderr_log_path}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def relay_bundle_members() -> list[str]:
    return [
        "mail_runner",
        "requirements.txt",
    ]
