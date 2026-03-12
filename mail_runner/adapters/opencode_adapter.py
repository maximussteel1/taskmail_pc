"""Thin OpenCode CLI wrapper."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from .base import WorkerAdapter
from .cli_common import BaseCliAdapter, DEMO_COMMAND, build_demo_command


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
        command = [
            *resolved.prefix,
            "run",
            "Execute the attached prompt.txt exactly.",
            "--dir",
            str(cwd),
            "--file",
            str(prompt_path),
        ]
        if model_name:
            command.extend(["-m", model_name])
        return command, None, " ".join(command)
