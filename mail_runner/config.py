"""Configuration loading for the mail runner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_PREFIX = "MAIL_RUNNER_"

_INT_FIELDS = {
    "imap_port",
    "smtp_port",
    "poll_seconds",
    "default_timeout_minutes",
}

_FLOAT_FIELDS = {
    "mock_sleep_seconds",
}

_MAP_FIELDS = {
    "opencode_profile_models",
    "codex_profile_models",
}


@dataclass(slots=True)
class AppConfig:
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    poll_seconds: int = 30
    task_root: str = "tasks"
    default_timeout_minutes: int = 60
    opencode_command: str = ""
    codex_command: str = ""
    from_name: str = "Mail Runner"
    from_addr: str = ""
    mock_sleep_seconds: float = 1.0
    opencode_profile_models: dict[str, str] = field(default_factory=dict)
    codex_profile_models: dict[str, str] = field(default_factory=dict)

    def resolve_task_root(self, base_dir: str | Path | None = None) -> Path:
        root = Path(base_dir) if base_dir is not None else PROJECT_ROOT
        task_root = Path(self.task_root)
        return task_root if task_root.is_absolute() else (root / task_root)


def _coerce_value(field_name: str, value: Any) -> Any:
    if value is None:
        return {} if field_name in _MAP_FIELDS else None
    if field_name in _MAP_FIELDS:
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} must be a mapping of profile names to model ids")
        coerced: dict[str, str] = {}
        for key, item in value.items():
            coerced[str(key).strip()] = str(item).strip()
        return coerced
    if field_name in _INT_FIELDS:
        return int(value)
    if field_name in _FLOAT_FIELDS:
        return float(value)
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
        if field_name in data:
            values[field_name] = _coerce_value(field_name, data[field_name])
        else:
            values[field_name] = default_value

        env_name = f"{ENV_PREFIX}{field_name.upper()}"
        if field_name not in _MAP_FIELDS and env_name in os.environ:
            values[field_name] = _coerce_value(field_name, os.environ[env_name])

    return AppConfig(**values)
