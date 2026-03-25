"""Shared helpers for live pc-control probes against a real relay host."""

from __future__ import annotations

import inspect
import json
import os
import ssl
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import websockets

from .config import AppConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE_USER = "ubuntu"
DEFAULT_REMOTE_STATE_DIR = "/opt/mail_runner_relay/shared/state/pc_control"
DEFAULT_REMOTE_KEY_PATH = PROJECT_ROOT / "work_bot.pem"
_WEBSOCKETS_CONNECT_SUPPORTS_PROXY = "proxy" in inspect.signature(websockets.connect).parameters


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def slug_text(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or ""))
    collapsed = "-".join(part for part in normalized.split("-") if part)
    return collapsed or "probe"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_ssl_context(config: AppConfig) -> ssl.SSLContext | None:
    relay_url = str(config.relay_url or "").strip()
    if relay_url.startswith("ws://"):
        return None
    context = ssl.create_default_context()
    if not bool(config.relay_verify_tls):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif str(config.relay_ca_file or "").strip():
        context.load_verify_locations(str(config.relay_ca_file))
    return context


def direct_websocket_connect_kwargs() -> dict[str, object]:
    if _WEBSOCKETS_CONNECT_SUPPORTS_PROXY:
        return {"proxy": None}
    return {}


def run_command(
    command: list[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if input_text is None:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        stdout = result.stdout
        stderr = result.stderr
    else:
        result = subprocess.run(
            command,
            input=input_text.encode("utf-8"),
            text=False,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    result.stdout = stdout
    result.stderr = stderr
    return result


def ssh_config_path_without_proxy() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def ssh_base_args(user: str, host: str, key_path: Path) -> list[str]:
    return [
        "ssh",
        "-F",
        ssh_config_path_without_proxy(),
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "IdentitiesOnly=yes",
        f"{user}@{host}",
    ]


def fetch_remote_json(
    *,
    host: str,
    user: str,
    key_path: Path,
    remote_path: str,
    timeout_seconds: int = 15,
) -> dict[str, Any] | None:
    result = run_command(
        [*ssh_base_args(user, host, key_path), f"cat {remote_path}"],
        timeout_seconds=timeout_seconds,
    )
    payload = json.loads(result.stdout)
    return payload if isinstance(payload, dict) else None
