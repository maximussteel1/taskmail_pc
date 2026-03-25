from __future__ import annotations

import pytest

from mail_runner.relay_server.workspace_inventory_store import (
    InMemoryWorkspaceInventoryStore,
    WorkspaceInventoryConflictError,
)


def _workspace(repo_path: str) -> dict[str, object]:
    return {
        "workspace_id": "workspace_001",
        "workspace_norm": "workspace_norm_001",
        "repo_path": repo_path,
        "workdir": None,
        "display_name": "repo_a",
        "source": "project_sync_roots",
        "capabilities": {
            "supported_backends": ["codex"],
            "profile_catalogs": {"codex": ["fast", "strong"]},
            "permission_modes": ["default", "highest"],
            "backend_transport_modes": {"codex": ["cli", "sdk"]},
        },
    }


def test_workspace_inventory_store_allows_same_workspace_id_on_different_pcs() -> None:
    store = InMemoryWorkspaceInventoryStore()

    store.replace_snapshot(
        pc_id="pc_home",
        snapshot_id="snapshot_001",
        workspaces=[_workspace("E:\\projects\\repo_a")],
        updated_at="2026-03-25T10:00:00",
    )
    store.replace_snapshot(
        pc_id="pc_office",
        snapshot_id="snapshot_002",
        workspaces=[_workspace("D:\\projects\\repo_a")],
        updated_at="2026-03-25T10:01:00",
    )

    assert store.count() == 2
    assert store.get_workspace("pc_home", "workspace_001").repo_path == "E:\\projects\\repo_a"
    assert store.get_workspace("pc_office", "workspace_001").repo_path == "D:\\projects\\repo_a"


def test_workspace_inventory_store_rejects_identity_mismatch_on_same_pc() -> None:
    store = InMemoryWorkspaceInventoryStore()
    store.replace_snapshot(
        pc_id="pc_home",
        snapshot_id="snapshot_001",
        workspaces=[_workspace("E:\\projects\\repo_a")],
        updated_at="2026-03-25T10:00:00",
    )

    with pytest.raises(WorkspaceInventoryConflictError, match="workspace_id changed repo_path/workdir"):
        store.replace_snapshot(
            pc_id="pc_home",
            snapshot_id="snapshot_002",
            workspaces=[_workspace("E:\\projects\\repo_b")],
            updated_at="2026-03-25T10:01:00",
        )
