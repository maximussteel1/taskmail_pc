"""Thin OpenCode CLI wrapper."""

from __future__ import annotations

import json
import string
import subprocess
import tempfile
from pathlib import Path

from ..config import AppConfig
from .base import WorkerAdapter
from .cli_common import BaseCliAdapter, DEMO_COMMAND, _ResolvedCommand, build_demo_command, extract_backend_session_id


class OpenCodeAdapter(BaseCliAdapter, WorkerAdapter):
    """Runs OpenCode through its non-interactive CLI."""

    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__(config)

    @property
    def backend(self) -> str:
        return "opencode"

    @property
    def backend_label(self) -> str:
        return "OpenCode"

    def _configured_command(self) -> str:
        return self._config.opencode_command

    def _default_executable(self) -> str:
        return "opencode"

    def _profile_model_map(self) -> dict[str, str]:
        return self._config.opencode_profile_models

    def _dynamic_config_path(self, task) -> Path:
        root = Path(tempfile.gettempdir()) / "mail_runner_opencode" / task.thread_id / task.task_id
        root.mkdir(parents=True, exist_ok=True)
        return root / "opencode.json"

    def _normalize_path_glob(self, path: Path) -> str:
        return path.resolve(strict=False).as_posix()

    def _build_permission_config(self, cwd: Path) -> dict[str, object]:
        external_directory_rules: dict[str, str] = {}
        for drive_letter in string.ascii_uppercase:
            external_directory_rules[f"{drive_letter}:/**"] = "allow"
        external_directory_rules["C:/Program Files/**"] = "deny"
        external_directory_rules["C:/Program Files (x86)/**"] = "deny"

        cwd_glob = f"{self._normalize_path_glob(cwd)}/**"
        return {
            "$schema": "https://opencode.ai/config.json",
            "permission": {
                "external_directory": external_directory_rules,
                "edit": {
                    "*": "deny",
                    cwd_glob: "allow",
                },
                "question": "deny",
                "websearch": "allow" if self._config.enable_web_search else "deny",
                "webfetch": "allow" if self._config.enable_web_search else "deny",
            },
        }

    def _write_dynamic_config(self, task, cwd: Path) -> Path:
        config_path = self._dynamic_config_path(task)
        payload = self._build_permission_config(cwd)
        config_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return config_path

    def _build_environment_overrides(
        self,
        *,
        task,
        resolved,
        cwd: Path,
    ) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._config.enable_web_search:
            env["OPENCODE_ENABLE_EXA"] = "1"
        if resolved.prefix != [DEMO_COMMAND]:
            env["OPENCODE_CONFIG"] = str(self._write_dynamic_config(task, cwd))
        return env

    def _session_title(self, task) -> str:
        return f"mail-runner:{task.thread_id}:{task.task_id}"

    def _lookup_session_id_from_recent_sessions(self, resolved: _ResolvedCommand, cwd: Path, task) -> str | None:
        if resolved.prefix == [DEMO_COMMAND]:
            return task.backend_session_id or f"demo-session-{self.backend}-{task.thread_id}"
        command = [*resolved.prefix, "session", "list", "--format", "json", "-n", "20"]
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._build_subprocess_env(task=task, resolved=resolved, cwd=cwd),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return extract_backend_session_id(completed.stdout)
        if not isinstance(payload, list):
            return None
        for item in payload:
            if not isinstance(item, dict):
                continue
            item_title = str(item.get("title") or item.get("name") or "").strip()
            if item_title != self._session_title(task):
                continue
            for key in ("sessionID", "sessionId", "session_id", "id"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _build_backend_command(
        self,
        *,
        task,
        resolved,
        prompt_path: Path,
        cwd: Path,
    ) -> tuple[list[str], str | None, str]:
        model_name = self.resolve_profile_model(task.profile)
        if resolved.prefix == [DEMO_COMMAND]:
            command, display = build_demo_command(self.backend)
            return command, None, display
        if task.run_mode == "resume":
            if not task.backend_session_id:
                raise ValueError("OpenCode resume requires backend_session_id.")
            turn_text = task.turn_text or "Continue the previous task."
            command = [*resolved.prefix, "run", turn_text, "--session", task.backend_session_id, "--dir", str(cwd)]
            if model_name:
                command.extend(["-m", model_name])
            return command, None, " ".join(command)

        command = [
            *resolved.prefix,
            "run",
            "Execute the attached prompt.txt exactly.",
            "--dir",
            str(cwd),
            "--file",
            str(prompt_path),
            "--title",
            self._session_title(task),
        ]
        if model_name:
            command.extend(["-m", model_name])
        return command, None, " ".join(command)

    def _extract_backend_session_id(
        self,
        *,
        task,
        resolved,
        cwd: Path,
        stdout_text: str,
        stderr_text: str,
    ) -> str | None:
        session_id = extract_backend_session_id(stdout_text, stderr_text)
        if session_id:
            return session_id
        if task.run_mode == "resume":
            return task.backend_session_id
        return self._lookup_session_id_from_recent_sessions(resolved, cwd, task)
