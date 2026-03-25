"""Workspace inventory helpers for the PC control plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .project_folder_sync import list_project_folders
from .status import BACKEND_TRANSPORT_CLI, BACKEND_TRANSPORT_SDK
from .thread_store import build_workspace_id, build_workspace_norm


@dataclass(slots=True)
class PcExecutionCapabilities:
    streaming: bool
    artifact_manifest: bool
    workspace_snapshot: bool
    supported_backends: list[str]
    profile_catalogs: dict[str, list[str]]
    permission_modes: list[str]
    backend_transport_modes: dict[str, list[str]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "streaming": self.streaming,
            "artifact_manifest": self.artifact_manifest,
            "workspace_snapshot": self.workspace_snapshot,
            "supported_backends": list(self.supported_backends),
            "profile_catalogs": {key: list(value) for key, value in self.profile_catalogs.items()},
            "permission_modes": list(self.permission_modes),
            "backend_transport_modes": {
                key: list(value) for key, value in self.backend_transport_modes.items()
            },
        }


def build_execution_capabilities(config: AppConfig) -> PcExecutionCapabilities:
    profile_catalogs = {
        "codex": sorted({key.strip().lower() for key in config.codex_profile_models} or {"default"}),
        "opencode": sorted({key.strip().lower() for key in config.opencode_profile_models} or {"default"}),
    }
    return PcExecutionCapabilities(
        streaming=True,
        artifact_manifest=True,
        workspace_snapshot=True,
        supported_backends=["codex", "opencode"],
        profile_catalogs=profile_catalogs,
        permission_modes=["default", "highest"],
        backend_transport_modes={
            "codex": [BACKEND_TRANSPORT_CLI, BACKEND_TRANSPORT_SDK],
            "opencode": [BACKEND_TRANSPORT_CLI, BACKEND_TRANSPORT_SDK],
        },
    )


def collect_workspace_inventory(config: AppConfig) -> list[dict[str, Any]]:
    capabilities = build_execution_capabilities(config).to_payload()
    inventory: list[dict[str, Any]] = []
    for listing in list_project_folders(list(config.project_sync_roots or [])):
        if not listing.available:
            continue
        for entry in listing.entries:
            inventory.append(
                {
                    "workspace_id": build_workspace_id(entry.path, None),
                    "workspace_norm": build_workspace_norm(entry.path, None),
                    "repo_path": entry.path,
                    "workdir": None,
                    "display_name": entry.name,
                    "source": "project_sync_roots",
                    "capabilities": capabilities,
                }
            )
    return inventory
