"""Shared execution capability normalization and validation helpers."""

from __future__ import annotations

from typing import Any


def _normalize_text(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _normalized_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        normalized = _normalize_text(item)
        if normalized is not None:
            items.append(normalized)
    return items


def normalize_capabilities(capabilities: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(capabilities or {})
    return {
        "supported_backends": _normalized_string_list(data.get("supported_backends")),
        "profile_catalogs": {
            backend: _normalized_string_list(entries)
            for raw_backend, entries in dict(data.get("profile_catalogs") or {}).items()
            if (backend := _normalize_text(raw_backend)) is not None
        },
        "permission_modes": _normalized_string_list(data.get("permission_modes")),
        "backend_transport_modes": {
            backend: _normalized_string_list(entries)
            for raw_backend, entries in dict(data.get("backend_transport_modes") or {}).items()
            if (backend := _normalize_text(raw_backend)) is not None
        },
    }


def intersect_capability_lists(left: list[str], right: list[str]) -> list[str]:
    if not left:
        return list(right)
    if not right:
        return list(left)
    right_set = set(right)
    return [item for item in left if item in right_set]


def compute_effective_capabilities(
    *,
    pc_capabilities: dict[str, Any] | None,
    workspace_capabilities: dict[str, Any] | None,
) -> dict[str, Any]:
    node_caps = normalize_capabilities(pc_capabilities)
    workspace_caps = normalize_capabilities(workspace_capabilities)
    supported_backends = intersect_capability_lists(
        node_caps["supported_backends"],
        workspace_caps["supported_backends"],
    )
    profile_catalogs: dict[str, list[str]] = {}
    backend_transport_modes: dict[str, list[str]] = {}
    for backend in supported_backends:
        profile_catalogs[backend] = intersect_capability_lists(
            node_caps["profile_catalogs"].get(backend, []),
            workspace_caps["profile_catalogs"].get(backend, []),
        )
        backend_transport_modes[backend] = intersect_capability_lists(
            node_caps["backend_transport_modes"].get(backend, []),
            workspace_caps["backend_transport_modes"].get(backend, []),
        )
    return {
        "supported_backends": supported_backends,
        "profile_catalogs": profile_catalogs,
        "permission_modes": intersect_capability_lists(
            node_caps["permission_modes"],
            workspace_caps["permission_modes"],
        ),
        "backend_transport_modes": backend_transport_modes,
    }


def empty_capabilities() -> dict[str, Any]:
    return {
        "supported_backends": [],
        "profile_catalogs": {},
        "permission_modes": [],
        "backend_transport_modes": {},
    }


def build_route_admission(
    *,
    allowed: bool,
    reason_code: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "allowed": bool(allowed),
        "reason_code": reason_code,
        "reason": reason,
    }


def resolve_workspace_route_admission(
    *,
    pc_status: str | None,
    workspace_presence: str | None,
    effective_capabilities: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_pc_status = _normalize_text(pc_status) or "unknown"
    normalized_presence = _normalize_text(workspace_presence) or "present"
    normalized_capabilities = normalize_capabilities(effective_capabilities)

    if normalized_presence == "missing":
        return build_route_admission(
            allowed=False,
            reason_code="workspace_unavailable",
            reason="Workspace is no longer present on the target PC.",
        )
    if normalized_pc_status == "offline":
        return build_route_admission(
            allowed=False,
            reason_code="pc_offline",
            reason="Workspace is attached to an offline PC.",
        )
    if normalized_pc_status == "unknown":
        return build_route_admission(
            allowed=False,
            reason_code="unknown",
            reason="Workspace route status is currently unknown.",
        )
    if normalized_presence == "stale":
        return build_route_admission(
            allowed=False,
            reason_code="inventory_stale",
            reason="Workspace inventory is currently stale.",
        )
    if not normalized_capabilities["supported_backends"]:
        return build_route_admission(
            allowed=False,
            reason_code="unsupported_backend",
            reason="Workspace does not currently expose any supported backend for new sessions.",
        )
    return build_route_admission(allowed=True)


def resolve_pc_route_admission(
    *,
    pc_status: str | None,
    workspaces: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_pc_status = _normalize_text(pc_status) or "unknown"
    if normalized_pc_status == "offline":
        return build_route_admission(
            allowed=False,
            reason_code="pc_offline",
            reason="Target PC is currently offline.",
        )
    if normalized_pc_status == "unknown":
        return build_route_admission(
            allowed=False,
            reason_code="unknown",
            reason="Target PC route status is currently unknown.",
        )
    if any(bool(dict(workspace.get("route_admission") or {}).get("allowed")) for workspace in workspaces):
        return build_route_admission(allowed=True)
    if not workspaces:
        return build_route_admission(
            allowed=False,
            reason_code="workspace_unavailable",
            reason="Target PC has no available workspace inventory.",
        )

    reason_codes = {
        _normalize_text(dict(workspace.get("route_admission") or {}).get("reason_code"))
        for workspace in workspaces
    }
    reason_codes.discard(None)
    if reason_codes == {"workspace_unavailable"}:
        return build_route_admission(
            allowed=False,
            reason_code="workspace_unavailable",
            reason="Target PC currently has no available workspace inventory.",
        )
    if reason_codes == {"unsupported_backend"}:
        return build_route_admission(
            allowed=False,
            reason_code="unsupported_backend",
            reason="Target PC currently exposes no backend that can accept new sessions.",
        )
    if reason_codes == {"inventory_stale"}:
        return build_route_admission(
            allowed=False,
            reason_code="inventory_stale",
            reason="Target PC workspace inventory is currently stale.",
        )
    return build_route_admission(
        allowed=False,
        reason_code="admission_blocked",
        reason="Target PC currently has no workspace that can accept new sessions.",
    )


def validate_execution_policy(
    *,
    command_type: str | None,
    execution_policy: dict[str, Any] | None,
    effective_capabilities: dict[str, Any] | None,
) -> tuple[str, str] | None:
    policy = dict(execution_policy or {})
    normalized_command_type = _normalize_text(command_type) or ""
    normalized_capabilities = normalize_capabilities(effective_capabilities)

    backend = _normalize_text(policy.get("backend"))
    if backend is None:
        if normalized_command_type == "new_task":
            return ("unsupported_backend", "new_task requires execution_policy.backend")
        return None
    if backend not in normalized_capabilities["supported_backends"]:
        return ("unsupported_backend", f"backend is not supported by target pc/workspace: {backend}")

    profile = _normalize_text(policy.get("profile"))
    if profile is not None:
        profile_catalog = normalized_capabilities["profile_catalogs"].get(backend, [])
        if profile not in profile_catalog:
            return ("unsupported_profile", f"profile is not supported by target pc/workspace: {backend}/{profile}")

    permission = _normalize_text(policy.get("permission"))
    if permission is not None and permission not in normalized_capabilities["permission_modes"]:
        return ("unsupported_permission", f"permission is not supported by target pc/workspace: {permission}")

    backend_transport = _normalize_text(policy.get("backend_transport"))
    if backend_transport is not None:
        supported_transports = normalized_capabilities["backend_transport_modes"].get(backend, [])
        if backend_transport not in supported_transports:
            return (
                "unsupported_backend_transport",
                f"backend_transport is not supported by target pc/workspace: {backend}/{backend_transport}",
            )
    return None


__all__ = [
    "build_route_admission",
    "compute_effective_capabilities",
    "empty_capabilities",
    "intersect_capability_lists",
    "normalize_capabilities",
    "resolve_pc_route_admission",
    "resolve_workspace_route_admission",
    "validate_execution_policy",
]
