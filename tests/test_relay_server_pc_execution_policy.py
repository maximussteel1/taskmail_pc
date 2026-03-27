from __future__ import annotations

from mail_runner.relay_server.pc_execution_policy import (
    resolve_pc_route_admission,
    resolve_workspace_route_admission,
    compute_effective_capabilities,
    validate_execution_policy,
)


def test_compute_effective_capabilities_intersects_pc_and_workspace_caps() -> None:
    effective = compute_effective_capabilities(
        pc_capabilities={
            "supported_backends": ["codex", "opencode"],
            "profile_catalogs": {
                "codex": ["fast", "strong"],
                "opencode": ["fast", "strong"],
            },
            "permission_modes": ["default", "highest"],
            "backend_transport_modes": {
                "codex": ["cli", "sdk"],
                "opencode": ["cli"],
            },
        },
        workspace_capabilities={
            "supported_backends": ["codex"],
            "profile_catalogs": {
                "codex": ["strong"],
            },
            "permission_modes": ["default"],
            "backend_transport_modes": {
                "codex": ["sdk"],
            },
        },
    )

    assert effective == {
        "supported_backends": ["codex"],
        "profile_catalogs": {"codex": ["strong"]},
        "permission_modes": ["default"],
        "backend_transport_modes": {"codex": ["sdk"]},
    }


def test_validate_execution_policy_returns_stable_rejection_codes() -> None:
    effective = {
        "supported_backends": ["codex"],
        "profile_catalogs": {"codex": ["strong"]},
        "permission_modes": ["default"],
        "backend_transport_modes": {"codex": ["sdk"]},
    }

    assert validate_execution_policy(
        command_type="new_task",
        execution_policy={},
        effective_capabilities=effective,
    ) == ("unsupported_backend", "new_task requires execution_policy.backend")
    assert validate_execution_policy(
        command_type="new_task",
        execution_policy={"backend": "opencode"},
        effective_capabilities=effective,
    ) == ("unsupported_backend", "backend is not supported by target pc/workspace: opencode")
    assert validate_execution_policy(
        command_type="new_task",
        execution_policy={"backend": "codex", "profile": "fast"},
        effective_capabilities=effective,
    ) == ("unsupported_profile", "profile is not supported by target pc/workspace: codex/fast")
    assert validate_execution_policy(
        command_type="new_task",
        execution_policy={"backend": "codex", "profile": "strong", "permission": "highest"},
        effective_capabilities=effective,
    ) == ("unsupported_permission", "permission is not supported by target pc/workspace: highest")
    assert validate_execution_policy(
        command_type="new_task",
        execution_policy={
            "backend": "codex",
            "profile": "strong",
            "permission": "default",
            "backend_transport": "cli",
        },
        effective_capabilities=effective,
    ) == (
        "unsupported_backend_transport",
        "backend_transport is not supported by target pc/workspace: codex/cli",
    )
    assert (
        validate_execution_policy(
            command_type="new_task",
            execution_policy={
                "backend": "codex",
                "profile": "strong",
                "permission": "default",
                "backend_transport": "sdk",
            },
            effective_capabilities=effective,
        )
        is None
    )


def test_route_admission_resolvers_surface_stable_inventory_reason_codes() -> None:
    assert resolve_workspace_route_admission(
        pc_status="online",
        workspace_presence="present",
        effective_capabilities={
            "supported_backends": [],
            "profile_catalogs": {},
            "permission_modes": ["default"],
            "backend_transport_modes": {},
        },
    ) == {
        "allowed": False,
        "reason_code": "unsupported_backend",
        "reason": "Workspace does not currently expose any supported backend for new sessions.",
    }
    assert resolve_workspace_route_admission(
        pc_status="online",
        workspace_presence="missing",
        effective_capabilities=None,
    ) == {
        "allowed": False,
        "reason_code": "workspace_unavailable",
        "reason": "Workspace is no longer present on the target PC.",
    }
    assert resolve_pc_route_admission(
        pc_status="online",
        workspaces=[
            {
                "route_admission": {
                    "allowed": False,
                    "reason_code": "unsupported_backend",
                    "reason": "Workspace does not currently expose any supported backend for new sessions.",
                }
            }
        ],
    ) == {
        "allowed": False,
        "reason_code": "unsupported_backend",
        "reason": "Target PC currently exposes no backend that can accept new sessions.",
    }
