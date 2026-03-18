"""Thin OpenCode CLI wrapper."""

from __future__ import annotations

import copy
import json
import subprocess
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
        return env

    def _highest_permission_overrides(self) -> dict[str, object]:
        permission: dict[str, object] = {
            "edit": "allow",
            "bash": "allow",
            "webfetch": "allow",
            "doom_loop": "allow",
            "external_directory": "allow",
        }
        if self._config.enable_web_search:
            permission["websearch"] = "allow"
        return {"permission": permission}

    def _load_effective_config(
        self,
        *,
        resolved: _ResolvedCommand,
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, object]:
        command = [*resolved.prefix, "debug", "config"]
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise RuntimeError("OpenCode debug config failed while preparing permission overlay.")
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("OpenCode debug config returned a non-object payload.")
        return payload

    def _merge_permission_config(self, base_config: dict[str, object]) -> dict[str, object]:
        merged = copy.deepcopy(base_config)
        existing_permission = merged.get("permission")
        merged_permission = dict(existing_permission) if isinstance(existing_permission, dict) else {}
        merged_permission.update(self._highest_permission_overrides()["permission"])  # type: ignore[arg-type]
        merged["permission"] = merged_permission
        return merged

    def _write_permission_overlay_config(
        self,
        *,
        resolved: _ResolvedCommand,
        cwd: Path,
        run_path: Path,
        env: dict[str, str],
    ) -> Path:
        run_path.mkdir(parents=True, exist_ok=True)
        base_config = self._load_effective_config(resolved=resolved, cwd=cwd, env=env)
        merged_config = self._merge_permission_config(base_config)
        config_path = run_path / "opencode_permission_overlay.json"
        config_path.write_text(json.dumps(merged_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return config_path

    def _build_subprocess_env(
        self,
        *,
        task,
        resolved,
        cwd: Path,
        run_path: Path | None = None,
        artifacts_dir: Path | None = None,
        incoming_attachments_json: Path | None = None,
        prompt_path: Path | None = None,
        allow_permission_override: bool = True,
    ) -> dict[str, str] | None:
        env = super()._build_subprocess_env(
            task=task,
            resolved=resolved,
            cwd=cwd,
            run_path=run_path,
            artifacts_dir=artifacts_dir,
            incoming_attachments_json=incoming_attachments_json,
            prompt_path=prompt_path,
            allow_permission_override=allow_permission_override,
        )
        if env is None:
            return None
        if resolved.prefix != [DEMO_COMMAND]:
            env.pop("OPENCODE_CONFIG", None)
            if allow_permission_override and task.permission == "highest":
                overlay_path = self._write_permission_overlay_config(
                    resolved=resolved,
                    cwd=cwd,
                    run_path=run_path or cwd,
                    env=env,
                )
                env["OPENCODE_CONFIG"] = str(overlay_path)
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
            env=self._build_subprocess_env(
                task=task,
                resolved=resolved,
                cwd=cwd,
                run_path=cwd,
                artifacts_dir=cwd / "artifacts",
                incoming_attachments_json=cwd / "incoming_attachments.json",
                allow_permission_override=False,
            ),
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
            command = [
                *resolved.prefix,
                "run",
                "Execute the attached prompt.txt exactly.",
                "--session",
                task.backend_session_id,
                "--dir",
                str(cwd),
                "--file",
                str(prompt_path),
            ]
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
