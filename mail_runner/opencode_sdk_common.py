"""Shared helpers for OpenCode SDK-backed workflows."""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opencode_ai import Opencode

from .adapters.cli_common import WINDOWS, resolve_command_prefix

DEFAULT_HOSTNAME = "127.0.0.1"


@dataclass(slots=True)
class ServerHandle:
    process: subprocess.Popen[str]
    port: int
    base_url: str
    stdout_log: Path
    stderr_log: Path
    workspace: Path


def pick_free_port(hostname: str = DEFAULT_HOSTNAME) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((hostname, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def select_provider_model(
    providers_payload: Any,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
) -> tuple[str, str]:
    providers = list(getattr(providers_payload, "providers", []) or [])
    defaults = dict(getattr(providers_payload, "default", {}) or {})
    provider_map = {str(provider.id): provider for provider in providers}

    if provider_id:
        provider = provider_map.get(provider_id)
        if provider is None:
            available = ", ".join(sorted(provider_map))
            raise ValueError(f"Unknown provider_id '{provider_id}'. Available providers: {available}")
        provider_models = dict(getattr(provider, "models", {}) or {})
        if model_id:
            if model_id not in provider_models:
                available = ", ".join(sorted(provider_models))
                raise ValueError(
                    f"Unknown model_id '{model_id}' for provider '{provider_id}'. Available models: {available}"
                )
            return provider_id, model_id
        default_model = defaults.get(provider_id)
        if isinstance(default_model, str) and default_model in provider_models:
            return provider_id, default_model
        if provider_models:
            return provider_id, next(iter(provider_models))
        raise ValueError(f"Provider '{provider_id}' does not expose any models.")

    if model_id:
        matches = [str(provider.id) for provider in providers if model_id in dict(getattr(provider, "models", {}) or {})]
        if not matches:
            raise ValueError(f"Unable to locate model_id '{model_id}' in the connected provider list.")
        if len(matches) > 1:
            joined = ", ".join(sorted(matches))
            raise ValueError(
                f"Model '{model_id}' exists under multiple providers ({joined}); pass the provider explicitly."
            )
        return matches[0], model_id

    for default_provider, default_model in defaults.items():
        provider = provider_map.get(str(default_provider))
        if provider is None:
            continue
        provider_models = dict(getattr(provider, "models", {}) or {})
        if isinstance(default_model, str) and default_model in provider_models:
            return str(default_provider), default_model

    for provider in providers:
        provider_models = dict(getattr(provider, "models", {}) or {})
        if provider_models:
            return str(provider.id), next(iter(provider_models))

    raise ValueError("No connected providers with models were found.")


def resolve_profile_provider_model(providers_payload: Any, configured_model: str | None) -> tuple[str, str]:
    model_text = str(configured_model or "").strip()
    if not model_text:
        return select_provider_model(providers_payload)
    if "/" in model_text:
        provider_id, model_id = model_text.split("/", 1)
        return select_provider_model(providers_payload, provider_id=provider_id.strip(), model_id=model_id.strip())
    return select_provider_model(providers_payload, model_id=model_text)


def part_to_record(part: Any) -> dict[str, Any]:
    if hasattr(part, "model_dump"):
        return dict(part.model_dump(by_alias=True, exclude_none=True))
    if isinstance(part, dict):
        return dict(part)
    record: dict[str, Any] = {}
    for key in ("type", "text", "tool", "id"):
        value = getattr(part, key, None)
        if value is not None:
            record[key] = value
    state = getattr(part, "state", None)
    if state is not None and hasattr(state, "model_dump"):
        record["state"] = state.model_dump(by_alias=True, exclude_none=True)
    return record


def latest_assistant_message(messages: list[Any]) -> Any:
    for message in reversed(messages):
        info = getattr(message, "info", None)
        if getattr(info, "role", None) == "assistant":
            return message
    raise RuntimeError("No assistant message was returned by the OpenCode session.")


def extract_reply_text(parts: list[Any]) -> str:
    lines: list[str] = []
    for part in parts:
        if getattr(part, "type", None) != "text":
            continue
        text = str(getattr(part, "text", "")).strip()
        if text:
            lines.append(text)
    return "\n\n".join(lines).strip()


def read_stderr_tail(path: Path, *, max_lines: int = 60) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]


def start_server(
    *,
    opencode_command: str,
    workspace: Path,
    output_dir: Path,
    port: int | None,
    env: dict[str, str] | None = None,
) -> ServerHandle:
    resolved = resolve_command_prefix(opencode_command, "opencode")
    chosen_port = port or pick_free_port()
    stdout_log = output_dir / "serve.stdout.log"
    stderr_log = output_dir / "serve.stderr.log"
    workspace.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    stdout_handle = stdout_log.open("w", encoding="utf-8", errors="replace")
    stderr_handle = stderr_log.open("w", encoding="utf-8", errors="replace")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if WINDOWS else 0
    try:
        process = subprocess.Popen(
            [*resolved.prefix, "serve", "--hostname", DEFAULT_HOSTNAME, "--port", str(chosen_port)],
            cwd=str(workspace),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
            start_new_session=not WINDOWS,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    return ServerHandle(
        process=process,
        port=chosen_port,
        base_url=f"http://{DEFAULT_HOSTNAME}:{chosen_port}",
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        workspace=workspace,
    )


def wait_for_server(server: ServerHandle, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with Opencode(base_url=server.base_url, timeout=3.0, max_retries=0) as client:
                client.config.get()
            return
        except Exception as exc:  # pragma: no cover - exercised by live smokes
            last_error = exc
            time.sleep(0.5)

    stderr_tail = "\n".join(read_stderr_tail(server.stderr_log))
    raise RuntimeError(
        "OpenCode server did not become ready at "
        f"{server.base_url}. Last error: {last_error!r}\n"
        f"Stderr tail:\n{stderr_tail}"
    )


def stop_server(server: ServerHandle) -> None:
    if WINDOWS:
        pid = server.process.pid
        if pid > 0:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                check=False,
                capture_output=True,
                text=True,
            )
        owner = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    "Get-NetTCPConnection -LocalPort "
                    f"{server.port} -State Listen -ErrorAction SilentlyContinue | "
                    "Select-Object -ExpandProperty OwningProcess"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for raw_line in owner.stdout.splitlines():
            line = raw_line.strip()
            if not line.isdigit():
                continue
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", line],
                check=False,
                capture_output=True,
                text=True,
            )
        return

    if server.process.poll() is not None:
        return
    server.process.kill()
    try:
        server.process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - best effort cleanup
        pass
