"""Thin Codex CLI wrapper."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from .base import WorkerAdapter
from .cli_common import BaseCliAdapter, DEMO_COMMAND, build_demo_command


class CodexAdapter(BaseCliAdapter, WorkerAdapter):
    """Runs Codex through its non-interactive CLI."""

    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__(config)

    @property
    def backend(self) -> str:
        return "codex"

    @property
    def backend_label(self) -> str:
        return "Codex"

    def _configured_command(self) -> str:
        return self._config.codex_command

    def _default_executable(self) -> str:
        return "codex"

    def _profile_model_map(self) -> dict[str, str]:
        return self._config.codex_profile_models

    def _build_backend_command(
        self,
        *,
        task,
        resolved,
        prompt_path: Path,
        cwd: Path,
    ) -> tuple[list[str], str | None, str]:
        prompt_text = prompt_path.read_text(encoding="utf-8")
        model_name = self.resolve_profile_model(task.profile)
        if resolved.prefix == [DEMO_COMMAND]:
            command, display = build_demo_command(self.backend)
            return command, prompt_text, display
        if task.run_mode == "resume":
            if not task.backend_session_id:
                raise ValueError("Codex resume requires backend_session_id.")
            command = [*resolved.prefix]
            if self._config.enable_web_search:
                command.append("--search")
            command.extend(
                [
                    "exec",
                    "resume",
                    "--skip-git-repo-check",
                ]
            )
            command.append("--full-auto")
            if model_name:
                command.extend(["-m", model_name])
            command.extend([task.backend_session_id, "-"])
            return command, prompt_text, " ".join(command)

        command = [*resolved.prefix]
        if self._config.enable_web_search:
            command.append("--search")
        command.extend(["exec", "--skip-git-repo-check", "--full-auto", "--cd", str(cwd)])
        if model_name:
            command.extend(["-m", model_name])
        command.append("-")
        return command, prompt_text, " ".join(command)
