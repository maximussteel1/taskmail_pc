"""Android-facing thin facade for environment inventory reads."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .pc_execution_policy import (
    build_route_admission,
    compute_effective_capabilities,
    empty_capabilities,
    normalize_capabilities,
    resolve_pc_route_admission,
    resolve_workspace_route_admission,
)
from .pc_control_runtime import PcControlRuntime

ANDROID_ENVIRONMENT_INVENTORY_PATH = "/v1/android/environment-inventory"
ANDROID_ENVIRONMENT_INVENTORY_SCHEMA_VERSION = "taskmail-android-environment-inventory-facade-v1"
DEFAULT_REFRESH_AFTER_SECONDS = 15


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalize_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _derive_display_name(*, repo_path: str | None, workspace_id: str) -> str:
    normalized_repo_path = _normalize_text(repo_path)
    if normalized_repo_path is None:
        return workspace_id
    name = Path(normalized_repo_path.rstrip("/\\")).name.strip()
    return name or normalized_repo_path


def _normalize_pc_status(status: str | None) -> str:
    normalized = _normalize_text(status)
    if normalized == "online":
        return "online"
    if normalized in {"stale", "offline"}:
        return "offline"
    return "unknown"


def _latest_workspace_timestamp(command: dict[str, Any]) -> str:
    for key in (
        "latest_event_at",
        "acked_at",
        "dispatched_at",
        "created_at",
    ):
        normalized = _normalize_text(command.get(key))
        if normalized is not None:
            return normalized
    return _timestamp()


def _workspace_record_filters_match(
    *,
    pc_id: str,
    workspace_id: str,
    pc_filter: set[str] | None,
    workspace_filter: set[str] | None,
) -> bool:
    if pc_filter is not None and pc_id not in pc_filter:
        return False
    if workspace_filter is not None and workspace_id not in workspace_filter:
        return False
    return True


def _build_missing_workspace_record(
    *,
    workspace_id: str,
    pc_id: str,
    repo_path: str | None,
    workdir: str | None,
    last_snapshot_at: str,
) -> dict[str, Any]:
    normalized_repo_path = _normalize_text(repo_path) or workspace_id
    return {
        "workspace_id": workspace_id,
        "pc_id": pc_id,
        "display_name": _derive_display_name(repo_path=normalized_repo_path, workspace_id=workspace_id),
        "repo_path": normalized_repo_path,
        "workdir": _normalize_text(workdir),
        "presence": "missing",
        "last_snapshot_at": last_snapshot_at,
        "effective_execution_capabilities": empty_capabilities(),
        "route_admission": build_route_admission(
            allowed=False,
            reason_code="workspace_unavailable",
            reason="Workspace is no longer present on the target PC.",
        ),
    }


def _collect_binding_missing_workspace_candidates(
    *,
    thread_bindings: list[dict[str, Any]],
    present_workspace_keys: set[tuple[str, str]],
    pc_filter: set[str] | None,
    workspace_filter: set[str] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for binding in thread_bindings:
        pc_id = _normalize_text(binding.get("pc_id"))
        workspace_id = _normalize_text(binding.get("workspace_id"))
        if pc_id is None or workspace_id is None:
            continue
        if not _workspace_record_filters_match(
            pc_id=pc_id,
            workspace_id=workspace_id,
            pc_filter=pc_filter,
            workspace_filter=workspace_filter,
        ):
            continue
        inventory_key = (pc_id, workspace_id)
        if inventory_key in present_workspace_keys:
            continue
        candidate = _build_missing_workspace_record(
            workspace_id=workspace_id,
            pc_id=pc_id,
            repo_path=_normalize_text(binding.get("repo_path")),
            workdir=_normalize_text(binding.get("workdir")),
            last_snapshot_at=_normalize_text(binding.get("binding_created_at")) or _timestamp(),
        )
        existing = latest_by_key.get(inventory_key)
        if existing is None or candidate["last_snapshot_at"] > existing["last_snapshot_at"]:
            latest_by_key[inventory_key] = candidate
    return latest_by_key


def _collect_command_missing_workspace_candidates(
    *,
    commands: list[dict[str, Any]],
    present_workspace_keys: set[tuple[str, str]],
    pc_filter: set[str] | None,
    workspace_filter: set[str] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for command in commands:
        pc_id = _normalize_text(command.get("pc_id"))
        workspace_id = _normalize_text(command.get("workspace_id"))
        if pc_id is None or workspace_id is None:
            continue
        if not _workspace_record_filters_match(
            pc_id=pc_id,
            workspace_id=workspace_id,
            pc_filter=pc_filter,
            workspace_filter=workspace_filter,
        ):
            continue
        inventory_key = (pc_id, workspace_id)
        if inventory_key in present_workspace_keys:
            continue
        payload = dict(command.get("command_payload") or {})
        candidate = _build_missing_workspace_record(
            workspace_id=workspace_id,
            pc_id=pc_id,
            repo_path=_normalize_text(payload.get("repo_path")),
            workdir=_normalize_text(payload.get("workdir")),
            last_snapshot_at=_latest_workspace_timestamp(command),
        )
        existing = latest_by_key.get(inventory_key)
        if existing is None or candidate["last_snapshot_at"] > existing["last_snapshot_at"]:
            latest_by_key[inventory_key] = candidate
    return latest_by_key


def _collect_missing_workspace_records(
    *,
    thread_bindings: list[dict[str, Any]],
    commands: list[dict[str, Any]],
    present_workspace_keys: set[tuple[str, str]],
    pc_filter: set[str] | None,
    workspace_filter: set[str] | None,
) -> dict[str, list[dict[str, Any]]]:
    binding_candidates = _collect_binding_missing_workspace_candidates(
        thread_bindings=thread_bindings,
        present_workspace_keys=present_workspace_keys,
        pc_filter=pc_filter,
        workspace_filter=workspace_filter,
    )
    command_candidates = _collect_command_missing_workspace_candidates(
        commands=commands,
        present_workspace_keys=present_workspace_keys,
        pc_filter=pc_filter,
        workspace_filter=workspace_filter,
    )
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {
        key: dict(record) for key, record in binding_candidates.items()
    }
    for key, record in latest_by_key.items():
        command_record = command_candidates.get(key)
        if command_record is not None and command_record["last_snapshot_at"] > record["last_snapshot_at"]:
            record["last_snapshot_at"] = command_record["last_snapshot_at"]
    for key, record in command_candidates.items():
        latest_by_key.setdefault(key, record)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in latest_by_key.values():
        grouped.setdefault(record["pc_id"], []).append(record)
    for items in grouped.values():
        items.sort(key=lambda entry: (entry["display_name"], entry["workspace_id"]))
    return grouped


def _build_present_workspace_record(
    *,
    workspace: dict[str, Any],
    pc_status: str,
    pc_capabilities: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = _normalize_text(workspace.get("workspace_id")) or "workspace_missing"
    presence = "present" if pc_status == "online" else "stale"
    effective_capabilities = compute_effective_capabilities(
        pc_capabilities=pc_capabilities,
        workspace_capabilities=dict(workspace.get("capabilities") or {}),
    )
    route_admission = resolve_workspace_route_admission(
        pc_status=pc_status,
        workspace_presence=presence,
        effective_capabilities=effective_capabilities,
    )
    return {
        "workspace_id": workspace_id,
        "pc_id": _normalize_text(workspace.get("pc_id")) or "",
        "display_name": _normalize_text(workspace.get("display_name"))
        or _derive_display_name(
            repo_path=_normalize_text(workspace.get("repo_path")),
            workspace_id=workspace_id,
        ),
        "repo_path": _normalize_text(workspace.get("repo_path")) or workspace_id,
        "workdir": _normalize_text(workspace.get("workdir")),
        "presence": presence,
        "last_snapshot_at": _normalize_text(workspace.get("updated_at")) or _timestamp(),
        "effective_execution_capabilities": effective_capabilities,
        "route_admission": route_admission,
    }


def _build_pc_route_admission(*, pc_status: str, workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    return resolve_pc_route_admission(
        pc_status=pc_status,
        workspaces=workspaces,
    )


def _build_pc_inventory_state(
    *,
    pc_status: str,
    present_workspaces: list[dict[str, Any]],
    missing_workspaces: list[dict[str, Any]],
) -> str:
    if not present_workspaces and not missing_workspaces:
        return "missing"
    if missing_workspaces or pc_status != "online":
        return "stale"
    return "fresh"


def build_android_environment_inventory_snapshot(
    *,
    pc_control_runtime: PcControlRuntime,
    include_offline: bool = True,
    include_missing_workspaces: bool = True,
    pc_ids: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    refresh_after_seconds: int = DEFAULT_REFRESH_AFTER_SECONDS,
) -> dict[str, Any]:
    generated_at = _timestamp()
    normalized_pc_filter = {
        normalized
        for item in pc_ids or []
        if (normalized := _normalize_text(item)) is not None
    } or None
    normalized_workspace_filter = {
        normalized
        for item in workspace_ids or []
        if (normalized := _normalize_text(item)) is not None
    } or None

    nodes = pc_control_runtime.list_nodes()
    workspaces = pc_control_runtime.list_workspaces()
    thread_bindings = pc_control_runtime.list_thread_bindings()
    commands = pc_control_runtime.list_commands()

    node_by_pc = {
        pc_id: node
        for node in nodes
        if (pc_id := _normalize_text(node.get("pc_id"))) is not None
        and (normalized_pc_filter is None or pc_id in normalized_pc_filter)
    }
    present_workspace_keys = {
        (pc_id, workspace_id)
        for workspace in workspaces
        if (pc_id := _normalize_text(workspace.get("pc_id"))) is not None
        and (workspace_id := _normalize_text(workspace.get("workspace_id"))) is not None
    }
    missing_workspace_records = (
        _collect_missing_workspace_records(
            thread_bindings=thread_bindings,
            commands=commands,
            present_workspace_keys=present_workspace_keys,
            pc_filter=normalized_pc_filter,
            workspace_filter=normalized_workspace_filter,
        )
        if include_missing_workspaces
        else {}
    )

    pc_ids_to_render = set(node_by_pc.keys()) | set(missing_workspace_records.keys())
    pcs: list[dict[str, Any]] = []
    any_missing_workspace = False
    any_stale_workspace = False
    any_unknown_pc = False
    any_offline_pc = False

    for pc_id in sorted(pc_ids_to_render):
        node = node_by_pc.get(pc_id)
        pc_status = _normalize_pc_status(node.get("status") if node is not None else None)
        if not include_offline and pc_status != "online":
            continue

        pc_capabilities = normalize_capabilities(dict(node.get("capabilities") or {}) if node is not None else {})
        present_workspace_records = [
            _build_present_workspace_record(
                workspace=workspace,
                pc_status=pc_status,
                pc_capabilities=pc_capabilities,
            )
            for workspace in workspaces
            if (workspace_pc_id := _normalize_text(workspace.get("pc_id"))) == pc_id
            and (workspace_id := _normalize_text(workspace.get("workspace_id"))) is not None
            and _workspace_record_filters_match(
                pc_id=pc_id,
                workspace_id=workspace_id,
                pc_filter=normalized_pc_filter,
                workspace_filter=normalized_workspace_filter,
            )
        ]
        present_workspace_records.sort(key=lambda entry: (entry["display_name"], entry["workspace_id"]))

        missing_records = list(missing_workspace_records.get(pc_id, []))
        if missing_records:
            any_missing_workspace = True
        if any(record["presence"] == "stale" for record in present_workspace_records):
            any_stale_workspace = True
        if pc_status == "unknown":
            any_unknown_pc = True
        if pc_status == "offline":
            any_offline_pc = True

        workspaces_for_pc = present_workspace_records + missing_records
        last_seen_at = _normalize_text(node.get("last_seen_at") if node is not None else None)
        if last_seen_at is None:
            last_seen_at = next(
                (record["last_snapshot_at"] for record in missing_records if record["last_snapshot_at"]),
                generated_at,
            )
        workspace_inventory_state = _build_pc_inventory_state(
            pc_status=pc_status,
            present_workspaces=present_workspace_records,
            missing_workspaces=missing_records,
        )
        pcs.append(
            {
                "pc_id": pc_id,
                "display_name": _normalize_text(node.get("display_name") if node is not None else None) or pc_id,
                "status": pc_status,
                "last_seen_at": last_seen_at,
                "workspace_inventory_state": workspace_inventory_state,
                "workspace_count": len(workspaces_for_pc),
                "pc_capabilities": pc_capabilities,
                "route_admission": _build_pc_route_admission(
                    pc_status=pc_status,
                    workspaces=workspaces_for_pc,
                ),
                "workspaces": workspaces_for_pc,
            }
        )

    inventory_state = "fresh"
    if any_missing_workspace or any_unknown_pc:
        inventory_state = "partial"
    elif any_stale_workspace or any_offline_pc:
        inventory_state = "stale"

    return {
        "schema_version": ANDROID_ENVIRONMENT_INVENTORY_SCHEMA_VERSION,
        "snapshot_id": f"env_snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": generated_at,
        "inventory_state": inventory_state,
        "refresh_after_seconds": max(1, int(refresh_after_seconds)),
        "pcs": pcs,
    }


__all__ = [
    "ANDROID_ENVIRONMENT_INVENTORY_PATH",
    "ANDROID_ENVIRONMENT_INVENTORY_SCHEMA_VERSION",
    "DEFAULT_REFRESH_AFTER_SECONDS",
    "build_android_environment_inventory_snapshot",
]
