from __future__ import annotations

import threading
from datetime import datetime, timedelta

import requests

from mail_runner.relay_server.android_environment_inventory_facade import (
    ANDROID_ENVIRONMENT_INVENTORY_PATH,
    ANDROID_ENVIRONMENT_INVENTORY_SCHEMA_VERSION,
)
from mail_runner.relay_server.app import build_http_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.pc_control_protocol import (
    build_command_dispatch,
    build_ingress_candidate,
    build_mailbox_lease,
    build_pc_hello,
    build_thread_binding,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import InMemorySessionStore
from mail_runner.thread_store import build_workspace_id, build_workspace_norm


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _pc_capabilities() -> dict[str, object]:
    return {
        "streaming": True,
        "artifact_manifest": True,
        "workspace_snapshot": True,
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
    }


def _workspace_capabilities() -> dict[str, object]:
    return {
        "supported_backends": ["codex"],
        "profile_catalogs": {
            "codex": ["strong"],
        },
        "permission_modes": ["default"],
        "backend_transport_modes": {
            "codex": ["sdk"],
        },
    }


def _get_inventory(
    url: str,
    *,
    auth_token: str | None,
    params: dict[str, object] | None = None,
) -> requests.Response:
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return requests.get(url, headers=headers, params=params, timeout=5)


def _register_pc(runtime, *, pc_id: str = "pc_home") -> tuple[str, int, str]:
    sent_at = _now()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id=f"msg_hello:{pc_id}",
            trace_id=f"trace_hello:{pc_id}",
            pc_id=pc_id,
            sent_at=sent_at,
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_pc_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="relay-secret")
    return connection_id, connection_epoch, sent_at


def _replace_snapshot(
    runtime,
    *,
    pc_id: str,
    connection_id: str,
    connection_epoch: int,
    workspaces: list[dict[str, object]],
    snapshot_id: str = "snapshot_001",
    sent_at: str | None = None,
) -> str:
    effective_sent_at = sent_at or _now()
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id=f"msg_snapshot:{pc_id}:{snapshot_id}",
            trace_id=f"trace_snapshot:{pc_id}:{snapshot_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=effective_sent_at,
            snapshot_id=snapshot_id,
            workspaces=workspaces,
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None
    return effective_sent_at


def _workspace_entry(workspace_id: str = "workspace_android") -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "workspace_norm": "e:/projects/android_task_manager",
        "repo_path": "E:\\projects\\android_task_manager",
        "workdir": "feature/taskmail",
        "display_name": "android_task_manager",
        "source": "project_sync_roots",
        "capabilities": _workspace_capabilities(),
    }


def _workspace_entry_without_supported_backends(workspace_id: str = "workspace_blocked") -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "workspace_norm": "e:/projects/android_task_manager_blocked",
        "repo_path": "E:\\projects\\android_task_manager_blocked",
        "workdir": "feature/taskmail",
        "display_name": "android_task_manager_blocked",
        "source": "project_sync_roots",
        "capabilities": {
            "supported_backends": ["claude"],
            "profile_catalogs": {"claude": ["strong"]},
            "permission_modes": ["default"],
            "backend_transport_modes": {"claude": ["sdk"]},
        },
    }


def _binding_workspace_entry(*, repo_path: str, workdir: str | None) -> dict[str, object]:
    return {
        "workspace_id": build_workspace_id(repo_path, workdir),
        "workspace_norm": build_workspace_norm(repo_path, workdir),
        "repo_path": repo_path,
        "workdir": workdir,
        "display_name": "android_task_manager",
        "source": "project_sync_roots",
        "capabilities": _workspace_capabilities(),
    }


def _commit_thread_binding(
    runtime,
    *,
    pc_id: str,
    connection_id: str,
    connection_epoch: int,
    repo_path: str,
    workdir: str | None,
    sent_at: str,
) -> str:
    mailbox_key = "imap://bot@example.com@imap.example.com/INBOX"
    lease_holder_id = f"runner:{pc_id}:binding"
    lease = parse_pc_control_client_message(
        build_mailbox_lease(
            message_id=f"msg_lease:{pc_id}",
            trace_id=f"trace_lease:{pc_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_lease:{pc_id}",
            operation="acquire",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_ttl_seconds=45,
            config_fingerprint="cfg_001",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
        )
    )
    lease_response = parse_pc_control_server_message(runtime.handle_mailbox_lease(lease, connection_id=connection_id))
    assert lease_response.payload["lease_status"] == "active"

    ingress = parse_pc_control_client_message(
        build_ingress_candidate(
            message_id=f"msg_ingress:{pc_id}",
            trace_id=f"trace_ingress:{pc_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_ingress:{pc_id}",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_epoch=1,
            folder="INBOX",
            uid_validity=777,
            uid=101,
            ingress_message_id=f"<ingress:{pc_id}@example.com>",
            in_reply_to=None,
            references_hash="refs_hash_001",
            from_addr="user@example.com",
            subject="[OC] Demo",
            subject_norm="demo",
            raw_date="Wed, 25 Mar 2026 10:00:00 +0800",
            classification="new_task",
            candidate_status="ready",
        )
    )
    ingress_response = parse_pc_control_server_message(
        runtime.handle_ingress_candidate(ingress, connection_id=connection_id)
    )
    assert ingress_response.payload["decision"] == "accepted"

    binding = parse_pc_control_client_message(
        build_thread_binding(
            message_id=f"msg_binding:{pc_id}",
            trace_id=f"trace_binding:{pc_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_binding:{pc_id}",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_epoch=1,
            ingress_id=ingress_response.payload["ingress_id"],
            root_message_id=f"<ingress:{pc_id}@example.com>",
            thread_id=f"thread:{pc_id}",
            session_id=f"thread:{pc_id}",
            repo_path=repo_path,
            workdir=workdir,
            subject_norm="demo",
        )
    )
    binding_response = parse_pc_control_server_message(runtime.handle_thread_binding(binding, connection_id=connection_id))
    assert binding_response.payload["binding_status"] == "committed"
    return build_workspace_id(repo_path, workdir)


def test_android_environment_inventory_requires_dedicated_android_app_token(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="relay-secret",
        )
        payload = response.json()

        assert response.status_code == 401
        assert payload["error_code"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_returns_online_present_workspace_with_effective_capabilities(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch, _sent_at = _register_pc(runtime)
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry()],
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["schema_version"] == ANDROID_ENVIRONMENT_INVENTORY_SCHEMA_VERSION
        assert payload["inventory_state"] == "fresh"
        assert len(payload["pcs"]) == 1
        pc = payload["pcs"][0]
        assert pc["pc_id"] == "pc_home"
        assert pc["status"] == "online"
        assert pc["workspace_inventory_state"] == "fresh"
        assert pc["route_admission"]["allowed"] is True
        workspace = pc["workspaces"][0]
        assert workspace["workspace_id"] == "workspace_android"
        assert workspace["presence"] == "present"
        assert workspace["route_admission"]["allowed"] is True
        assert workspace["effective_execution_capabilities"] == {
            "supported_backends": ["codex"],
            "profile_catalogs": {"codex": ["strong"]},
            "permission_modes": ["default"],
            "backend_transport_modes": {"codex": ["sdk"]},
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_surfaces_unsupported_backend_route_admission(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch, _sent_at = _register_pc(runtime)
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry_without_supported_backends()],
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["inventory_state"] == "fresh"
        pc = payload["pcs"][0]
        assert pc["route_admission"]["allowed"] is False
        assert pc["route_admission"]["reason_code"] == "unsupported_backend"
        workspace = pc["workspaces"][0]
        assert workspace["presence"] == "present"
        assert workspace["effective_execution_capabilities"]["supported_backends"] == []
        assert workspace["route_admission"]["allowed"] is False
        assert workspace["route_admission"]["reason_code"] == "unsupported_backend"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_returns_offline_pc_and_stale_workspace(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch, _sent_at = _register_pc(runtime)
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry()],
    )
    runtime.close_connection(
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["inventory_state"] == "stale"
        pc = payload["pcs"][0]
        assert pc["status"] == "offline"
        assert pc["route_admission"]["allowed"] is False
        assert pc["route_admission"]["reason_code"] == "pc_offline"
        workspace = pc["workspaces"][0]
        assert workspace["presence"] == "stale"
        assert workspace["route_admission"]["allowed"] is False
        assert workspace["route_admission"]["reason_code"] == "pc_offline"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_backfills_missing_workspace_from_command_history(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch, sent_at = _register_pc(runtime)
    base = datetime.fromisoformat(sent_at)
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry("workspace_legacy")],
        snapshot_id="snapshot_present",
        sent_at=sent_at,
    )
    runtime.enqueue_command(
        parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_dispatch_001",
                trace_id="trace_dispatch_001",
                pc_id="pc_home",
                connection_epoch=connection_epoch,
                sent_at=sent_at,
                command_id="cmd_001",
                command_type="status",
                workspace_id="workspace_legacy",
                session_id="thread_001",
                execution_policy={},
                command_payload={
                    "repo_path": "E:\\projects\\android_task_manager",
                    "workdir": "feature/legacy",
                },
            )
        )
    )
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[],
        snapshot_id="snapshot_missing",
        sent_at=(base + timedelta(seconds=10)).isoformat(),
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["inventory_state"] == "partial"
        pc = payload["pcs"][0]
        assert pc["workspace_inventory_state"] == "stale"
        assert pc["route_admission"]["allowed"] is False
        assert pc["route_admission"]["reason_code"] == "workspace_unavailable"
        workspace = pc["workspaces"][0]
        assert workspace["workspace_id"] == "workspace_legacy"
        assert workspace["presence"] == "missing"
        assert workspace["repo_path"] == "E:\\projects\\android_task_manager"
        assert workspace["workdir"] == "feature/legacy"
        assert workspace["route_admission"]["allowed"] is False
        assert workspace["route_admission"]["reason_code"] == "workspace_unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_backfills_missing_workspace_from_thread_binding(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch, sent_at = _register_pc(runtime)
    base = datetime.fromisoformat(sent_at)
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_entry = _binding_workspace_entry(repo_path=repo_path, workdir=workdir)
    workspace_id = workspace_entry["workspace_id"]
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[workspace_entry],
        snapshot_id="snapshot_present",
        sent_at=sent_at,
    )
    assert _commit_thread_binding(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        repo_path=repo_path,
        workdir=workdir,
        sent_at=(base + timedelta(seconds=5)).isoformat(),
    ) == workspace_id
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[],
        snapshot_id="snapshot_missing",
        sent_at=(base + timedelta(seconds=10)).isoformat(),
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["inventory_state"] == "partial"
        pc = payload["pcs"][0]
        assert pc["workspace_inventory_state"] == "stale"
        assert pc["route_admission"]["allowed"] is False
        assert pc["route_admission"]["reason_code"] == "workspace_unavailable"
        workspace = pc["workspaces"][0]
        assert workspace["workspace_id"] == workspace_id
        assert workspace["presence"] == "missing"
        assert workspace["repo_path"] == repo_path
        assert workspace["workdir"] == workdir
        assert workspace["route_admission"]["allowed"] is False
        assert workspace["route_admission"]["reason_code"] == "workspace_unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_prefers_thread_binding_identity_over_command_history(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch, sent_at = _register_pc(runtime)
    base = datetime.fromisoformat(sent_at)
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_entry = _binding_workspace_entry(repo_path=repo_path, workdir=workdir)
    workspace_id = workspace_entry["workspace_id"]
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[workspace_entry],
        snapshot_id="snapshot_present",
        sent_at=sent_at,
    )
    _commit_thread_binding(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        repo_path=repo_path,
        workdir=workdir,
        sent_at=(base + timedelta(seconds=5)).isoformat(),
    )
    command_sent_at = (base + timedelta(seconds=8)).isoformat()
    runtime.enqueue_command(
        parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_dispatch_conflict",
                trace_id="trace_dispatch_conflict",
                pc_id="pc_home",
                connection_epoch=connection_epoch,
                sent_at=command_sent_at,
                command_id="cmd_conflict",
                command_type="status",
                workspace_id=workspace_id,
                session_id="thread:pc_home",
                execution_policy={},
                command_payload={
                    "repo_path": "E:\\projects\\wrong_repo",
                    "workdir": "feature/legacy",
                },
            )
        )
    )
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[],
        snapshot_id="snapshot_missing",
        sent_at=(base + timedelta(seconds=10)).isoformat(),
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        workspace = payload["pcs"][0]["workspaces"][0]
        assert workspace["workspace_id"] == workspace_id
        assert workspace["repo_path"] == repo_path
        assert workspace["workdir"] == workdir
        assert workspace["last_snapshot_at"] == command_sent_at
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_environment_inventory_honors_read_filters(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)

    home_connection_id, home_connection_epoch, home_sent_at = _register_pc(runtime, pc_id="pc_home")
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=home_connection_id,
        connection_epoch=home_connection_epoch,
        workspaces=[_workspace_entry("workspace_android")],
        snapshot_id="snapshot_home",
        sent_at=home_sent_at,
    )

    office_connection_id, office_connection_epoch, office_sent_at = _register_pc(runtime, pc_id="pc_office")
    office_base = datetime.fromisoformat(office_sent_at)
    _replace_snapshot(
        runtime,
        pc_id="pc_office",
        connection_id=office_connection_id,
        connection_epoch=office_connection_epoch,
        workspaces=[_workspace_entry("workspace_office")],
        snapshot_id="snapshot_office_present",
        sent_at=office_sent_at,
    )
    runtime.enqueue_command(
        parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_dispatch_002",
                trace_id="trace_dispatch_002",
                pc_id="pc_office",
                connection_epoch=office_connection_epoch,
                sent_at=office_sent_at,
                command_id="cmd_002",
                command_type="status",
                workspace_id="workspace_office",
                session_id="thread_002",
                execution_policy={},
                command_payload={
                    "repo_path": "E:\\projects\\office_repo",
                    "workdir": "feature/office",
                },
            )
        )
    )
    _replace_snapshot(
        runtime,
        pc_id="pc_office",
        connection_id=office_connection_id,
        connection_epoch=office_connection_epoch,
        workspaces=[],
        snapshot_id="snapshot_office_missing",
        sent_at=(office_base + timedelta(seconds=10)).isoformat(),
    )
    runtime.close_connection(
        pc_id="pc_office",
        connection_id=office_connection_id,
        connection_epoch=office_connection_epoch,
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_inventory(
            f"http://{host}:{port}{ANDROID_ENVIRONMENT_INVENTORY_PATH}",
            auth_token="android-secret",
            params={
                "include_offline": "false",
                "include_missing_workspaces": "false",
                "pc_id": "pc_home",
                "workspace_id": "workspace_android",
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["inventory_state"] == "fresh"
        assert [pc["pc_id"] for pc in payload["pcs"]] == ["pc_home"]
        assert [workspace["workspace_id"] for workspace in payload["pcs"][0]["workspaces"]] == ["workspace_android"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
