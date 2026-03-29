"""Configuration loading for the mail runner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .status import BACKEND_CODEX, BACKEND_OPENCODE, BACKEND_TRANSPORT_CLI, BACKEND_TRANSPORT_SDK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_PREFIX = "MAIL_RUNNER_"

_INT_FIELDS = {
    "imap_port",
    "smtp_port",
    "poll_seconds",
    "imap_idle_renew_seconds",
    "default_timeout_minutes",
    "new_task_max_age_minutes",
    "max_active_sessions",
    "max_active_sessions_per_workspace",
    "max_running_sessions",
    "max_running_sessions_per_workspace",
    "active_session_window_refresh_seconds",
    "active_session_window_buffer_lines",
    "active_session_window_history_limit",
    "external_delivery_threshold_mb",
    "cos_presign_expire_seconds",
    "relay_timeout_seconds",
    "relay_mailbox_lease_ttl_seconds",
}

_FLOAT_FIELDS = {
    "mock_sleep_seconds",
}

_BOOL_FIELDS = {
    "auto_create_workdir",
    "enable_web_search",
    "spawn_active_session_windows",
    "relay_verify_tls",
    "relay_auto_fallback_email",
}

_MAP_FIELDS = {
    "opencode_profile_models",
    "codex_profile_models",
}

_LIST_FIELDS = {
    "project_sync_roots",
}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "max_active_sessions": ("max_concurrent_runs",),
    "max_running_sessions": ("max_concurrent_runs",),
    "max_running_sessions_per_workspace": ("max_active_sessions_per_workspace",),
    "spawn_active_session_windows": ("spawn_monitor_windows",),
    "active_session_window_refresh_seconds": ("monitor_window_refresh_seconds",),
    "active_session_window_buffer_lines": ("monitor_window_buffer_lines",),
    "active_session_window_history_limit": ("monitor_window_history_limit",),
}


@dataclass(slots=True)
class AppConfig:
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_receive_mode: str = "auto"
    imap_idle_renew_seconds: int = 25 * 60
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    poll_seconds: int = 30
    task_root: str = "tasks"
    default_timeout_minutes: int = 60
    new_task_max_age_minutes: int = 0
    max_active_sessions: int = 4
    max_active_sessions_per_workspace: int | None = None
    max_running_sessions: int | None = None
    max_running_sessions_per_workspace: int | None = 2
    auto_create_workdir: bool = False
    enable_web_search: bool = False
    spawn_active_session_windows: bool = False
    active_session_window_refresh_seconds: int = 5
    active_session_window_buffer_lines: int = 1000
    active_session_window_history_limit: int = 12
    opencode_command: str = ""
    codex_command: str = ""
    opencode_transport_default: str = BACKEND_TRANSPORT_SDK
    codex_transport_default: str = BACKEND_TRANSPORT_SDK
    codex_sdk_sidecar_command: str = ""
    codex_sdk_sidecar_workdir: str = ""
    outbound_transport: str = "email"
    relay_url: str = ""
    relay_transport_token: str = ""
    relay_client_id: str = "pc-local"
    relay_client_version: str = "0.1.0-dev"
    relay_timeout_seconds: int = 15
    relay_verify_tls: bool = True
    relay_ca_file: str = ""
    relay_auto_fallback_email: bool = False
    control_plane_mode: str = "hybrid"
    relay_mailbox_lease_mode: str = "disabled"
    relay_mailbox_lease_ttl_seconds: int = 45
    external_delivery_backend_preference: str = "file_surface"
    from_name: str = "Mail Runner"
    from_addr: str = ""
    mock_sleep_seconds: float = 1.0
    opencode_profile_models: dict[str, str] = field(default_factory=dict)
    codex_profile_models: dict[str, str] = field(default_factory=dict)
    project_sync_roots: list[str] = field(default_factory=lambda: ["D:\\projects", "E:\\projects"])
    cos_region: str = ""
    cos_bucket: str = ""
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_object_prefix: str = "mail-runner"
    external_delivery_threshold_mb: int = 20
    cos_presign_expire_seconds: int = 7 * 24 * 60 * 60

    def __post_init__(self) -> None:
        self.max_active_sessions = max(1, int(self.max_active_sessions))
        if self.max_active_sessions_per_workspace is None:
            self.max_active_sessions_per_workspace = self.max_active_sessions
        else:
            self.max_active_sessions_per_workspace = max(1, int(self.max_active_sessions_per_workspace))
        if self.max_running_sessions is None:
            self.max_running_sessions = self.max_active_sessions
        else:
            self.max_running_sessions = max(1, int(self.max_running_sessions))
        if self.max_running_sessions_per_workspace is None:
            self.max_running_sessions_per_workspace = self.max_running_sessions
        else:
            self.max_running_sessions_per_workspace = max(1, int(self.max_running_sessions_per_workspace))

    def resolve_task_root(self, base_dir: str | Path | None = None) -> Path:
        root = Path(base_dir) if base_dir is not None else PROJECT_ROOT
        task_root = Path(self.task_root)
        return task_root if task_root.is_absolute() else (root / task_root)

    def default_transport_for_backend(self, backend: str) -> str:
        if backend == BACKEND_OPENCODE:
            return self.opencode_transport_default
        if backend == BACKEND_CODEX:
            return self.codex_transport_default
        return BACKEND_TRANSPORT_CLI

    @property
    def mail_ingress_enabled(self) -> bool:
        return self.control_plane_mode != "vps_only"

    @property
    def pc_control_sidecar_enabled(self) -> bool:
        return self.control_plane_mode != "mail_first"

    @property
    def spawn_monitor_windows(self) -> bool:
        return self.spawn_active_session_windows

    @property
    def monitor_window_refresh_seconds(self) -> int:
        return self.active_session_window_refresh_seconds

    @property
    def monitor_window_buffer_lines(self) -> int:
        return self.active_session_window_buffer_lines

    @property
    def monitor_window_history_limit(self) -> int:
        return self.active_session_window_history_limit


def _coerce_value(field_name: str, value: Any) -> Any:
    if value is None:
        if field_name in _MAP_FIELDS:
            return {}
        if field_name in _LIST_FIELDS:
            return []
        return None
    if field_name in _MAP_FIELDS:
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} must be a mapping of profile names to model ids")
        coerced: dict[str, str] = {}
        for key, item in value.items():
            coerced[str(key).strip()] = str(item).strip()
        return coerced
    if field_name in _LIST_FIELDS:
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(";") if item.strip()]
        raise ValueError(f"{field_name} must be a list of paths")
    if field_name in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{field_name} must be a boolean value")
    if field_name in _INT_FIELDS:
        return int(value)
    if field_name in _FLOAT_FIELDS:
        return float(value)
    if field_name in {"opencode_transport_default", "codex_transport_default"}:
        normalized = str(value).strip().lower()
        if normalized not in {BACKEND_TRANSPORT_CLI, BACKEND_TRANSPORT_SDK}:
            raise ValueError(f"{field_name} must be either 'cli' or 'sdk'")
        return normalized
    if field_name == "imap_receive_mode":
        normalized = str(value).strip().lower()
        if normalized not in {"auto", "poll", "idle"}:
            raise ValueError("imap_receive_mode must be one of 'auto', 'poll', or 'idle'")
        return normalized
    if field_name == "outbound_transport":
        normalized = str(value).strip().lower()
        if normalized not in {"email", "relay"}:
            raise ValueError("outbound_transport must be either 'email' or 'relay'")
        return normalized
    if field_name == "control_plane_mode":
        normalized = str(value).strip().lower()
        if normalized not in {"mail_first", "hybrid", "vps_only"}:
            raise ValueError("control_plane_mode must be one of 'mail_first', 'hybrid', or 'vps_only'")
        return normalized
    if field_name == "relay_mailbox_lease_mode":
        normalized = str(value).strip().lower()
        if normalized not in {"disabled", "strict", "degraded"}:
            raise ValueError("relay_mailbox_lease_mode must be one of 'disabled', 'strict', or 'degraded'")
        return normalized
    if field_name == "external_delivery_backend_preference":
        normalized = str(value).strip().lower()
        if normalized not in {"auto", "cos", "file_surface"}:
            raise ValueError("external_delivery_backend_preference must be one of 'auto', 'cos', or 'file_surface'")
        return normalized
    return str(value)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return raw


def load_config(path: str | None = None) -> AppConfig:
    config_path = Path(path) if path else Path(os.getenv(f"{ENV_PREFIX}CONFIG", DEFAULT_CONFIG_PATH))
    data = _load_yaml(config_path)

    values: dict[str, Any] = {}
    defaults = AppConfig()
    for field_info in fields(AppConfig):
        field_name = field_info.name
        default_value = getattr(defaults, field_name)
        alias_names = _FIELD_ALIASES.get(field_name, ())
        candidate_names = (field_name, *alias_names)

        values[field_name] = default_value
        for candidate_name in candidate_names:
            if candidate_name in data:
                values[field_name] = _coerce_value(field_name, data[candidate_name])
                break

        env_name = f"{ENV_PREFIX}{field_name.upper()}"
        alias_env_names = tuple(f"{ENV_PREFIX}{alias_name.upper()}" for alias_name in alias_names)
        if field_name not in _MAP_FIELDS:
            for candidate_env_name in (env_name, *alias_env_names):
                if candidate_env_name in os.environ:
                    values[field_name] = _coerce_value(field_name, os.environ[candidate_env_name])
                    break

    return AppConfig(**values)
