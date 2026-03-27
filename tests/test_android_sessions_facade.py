from __future__ import annotations

import threading
from datetime import datetime

import requests

from mail_runner.relay_server.android_sessions_facade import (
    ANDROID_SESSIONS_PATH,
    ANDROID_SESSIONS_SCHEMA_VERSION,
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
from mail_runner.status import BACKEND_CODEX, THREAD_STATUS_DONE, THREAD_STATUS_RUNNING
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, create_thread


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _pc_capabilities() -> dict[str, object]:
    return {
        "streaming": True,
        "artifact_manifest": True,
        "workspace_snapshot": True,
        "supported_backends": ["codex", "opencode"],
        "profile_catalogs": {"codex": ["default"], "opencode": ["default"]},
        "permission_modes": ["default", "highest"],
        "backend_transport_modes": {
            "codex": ["cli", "sdk"],
            "opencode": ["cli", "sdk"],
        },
    }


def _workspace_entry(*, repo_path: str, workdir: str | None) -> dict[str, object]:
    return {
        "workspace_id": build_workspace_id(repo_path, workdir),
        "workspace_norm": build_workspace_norm(repo_path, workdir),
        "repo_path": repo_path,
        "workdir": workdir,
        "display_name": repo_path.rstrip("\\/").split("\\")[-1].split("/")[-1],
        "source": "project_sync_roots",
        "capabilities": _pc_capabilities(),
    }


def _register_pc(runtime, *, pc_id: str) -> tuple[str, int]:
    sent_at = _now()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id=f"msg_hello:{pc_id}",
            trace_id=f"trace_hello:{pc_id}",
            pc_id=pc_id,
            sent_at=sent_at,
            display_name=pc_id,
            client_version="0.1.0",
            host_fingerprint=f"host:{pc_id}",
            runtime_fingerprint=f"runtime:{pc_id}",
            capabilities=_pc_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="relay-secret")
    return connection_id, connection_epoch


def _replace_snapshot(
    runtime,
    *,
    pc_id: str,
    connection_id: str,
    connection_epoch: int,
    workspaces: list[dict[str, object]],
    snapshot_id: str,
) -> None:
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id=f"msg_snapshot:{pc_id}:{snapshot_id}",
            trace_id=f"trace_snapshot:{pc_id}:{snapshot_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=_now(),
            snapshot_id=snapshot_id,
            workspaces=workspaces,
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None


def _commit_thread_binding(
    runtime,
    *,
    pc_id: str,
    connection_id: str,
    connection_epoch: int,
    thread_id: str,
    session_id: str,
    repo_path: str,
    workdir: str | None,
) -> None:
    sent_at = _now()
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
            message_id=f"msg_ingress:{pc_id}:{thread_id}",
            trace_id=f"trace_ingress:{pc_id}:{thread_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_ingress:{pc_id}:{thread_id}",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_epoch=1,
            folder="INBOX",
            uid_validity=777,
            uid=101,
            ingress_message_id=f"<ingress:{thread_id}@example.com>",
            in_reply_to=None,
            references_hash="refs_hash_001",
            from_addr="user@example.com",
            subject="[OC] Demo",
            subject_norm="demo",
            raw_date="Thu, 27 Mar 2026 09:00:00 +0800",
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
            message_id=f"msg_binding:{pc_id}:{thread_id}",
            trace_id=f"trace_binding:{pc_id}:{thread_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_binding:{pc_id}:{thread_id}",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_epoch=1,
            ingress_id=ingress_response.payload["ingress_id"],
            root_message_id=f"<ingress:{thread_id}@example.com>",
            thread_id=thread_id,
            session_id=session_id,
            repo_path=repo_path,
            workdir=workdir,
            subject_norm="demo",
        )
    )
    binding_response = parse_pc_control_server_message(runtime.handle_thread_binding(binding, connection_id=connection_id))
    assert binding_response.payload["binding_status"] == "committed"


def _enqueue_status_command(
    runtime,
    *,
    pc_id: str,
    workspace_id: str,
    session_id: str,
    repo_path: str,
    workdir: str | None,
) -> None:
    runtime.enqueue_command(
        parse_pc_control_server_message(
            build_command_dispatch(
                message_id=f"msg_dispatch:{pc_id}:{session_id}",
                trace_id=f"trace_dispatch:{pc_id}:{session_id}",
                pc_id=pc_id,
                connection_epoch=1,
                sent_at=_now(),
                command_id=f"cmd:{pc_id}:{session_id}",
                command_type="status",
                workspace_id=workspace_id,
                session_id=session_id,
                execution_policy={},
                command_payload={
                    "repo_path": repo_path,
                    "workdir": workdir,
                },
            )
        )
    )


def _create_session(
    task_root,
    *,
    thread_id: str,
    repo_path: str,
    workdir: str | None,
    session_name: str,
    status: str,
    lifecycle: str,
    updated_at: str,
) -> None:
    create_thread(
        thread_id=thread_id,
        root_message_id=f"<root:{thread_id}@example.com>",
        latest_message_id=f"<latest:{thread_id}@example.com>",
        subject_norm=session_name.lower().replace(" ", "-"),
        backend=BACKEND_CODEX,
        profile="default",
        permission="default",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=f"task_{thread_id}",
        last_task_snapshot_file=f"snapshots/{thread_id}.json",
        task_root=task_root,
        status=status,
        history_files=[],
        last_summary=f"summary:{thread_id}",
        lifecycle=lifecycle,
        last_active_at=updated_at,
        last_progress_at=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
        session_id=thread_id,
        session_name=session_name,
        backend_transport="sdk",
    )


def _get_sessions(
    url: str,
    *,
    auth_token: str | None,
    params: dict[str, object] | None = None,
) -> requests.Response:
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return requests.get(url, headers=headers, params=params, timeout=5)


def test_android_sessions_requires_dedicated_android_app_token(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="relay-secret",
        )
        payload = response.json()

        assert response.status_code == 401
        assert payload["error_code"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_sessions_returns_active_sessions_with_binding_and_unique_workspace_resolution(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_alpha = "E:\\projects\\alpha"
    repo_beta = "E:\\projects\\beta"
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_alpha,
        workdir="main",
        session_name="Alpha task",
        status=THREAD_STATUS_RUNNING,
        lifecycle="active",
        updated_at="2026-03-27T10:01:00",
    )
    _create_session(
        task_root,
        thread_id="thread_002",
        repo_path=repo_beta,
        workdir="feature/taskmail",
        session_name="Beta task",
        status=THREAD_STATUS_RUNNING,
        lifecycle="active",
        updated_at="2026-03-27T10:02:00",
    )
    _create_session(
        task_root,
        thread_id="thread_003",
        repo_path=repo_alpha,
        workdir="ended",
        session_name="Ended task",
        status=THREAD_STATUS_DONE,
        lifecycle="ended",
        updated_at="2026-03-27T10:03:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)

    alpha_connection_id, alpha_connection_epoch = _register_pc(runtime, pc_id="pc_alpha")
    _replace_snapshot(
        runtime,
        pc_id="pc_alpha",
        connection_id=alpha_connection_id,
        connection_epoch=alpha_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_alpha, workdir="main")],
        snapshot_id="snapshot_alpha",
    )
    _commit_thread_binding(
        runtime,
        pc_id="pc_alpha",
        connection_id=alpha_connection_id,
        connection_epoch=alpha_connection_epoch,
        thread_id="thread_001",
        session_id="thread_001",
        repo_path=repo_alpha,
        workdir="main",
    )

    beta_connection_id, beta_connection_epoch = _register_pc(runtime, pc_id="pc_beta")
    _replace_snapshot(
        runtime,
        pc_id="pc_beta",
        connection_id=beta_connection_id,
        connection_epoch=beta_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_beta, workdir="feature/taskmail")],
        snapshot_id="snapshot_beta",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["schema_version"] == ANDROID_SESSIONS_SCHEMA_VERSION
        assert payload["session_count"] == 2
        assert [item["session_id"] for item in payload["sessions"]] == ["thread_002", "thread_001"]
        assert payload["sessions"][0]["pc_id"] == "pc_beta"
        assert payload["sessions"][0]["workspace_id"] == build_workspace_id(repo_beta, "feature/taskmail")
        assert payload["sessions"][1]["pc_id"] == "pc_alpha"
        assert payload["sessions"][1]["workspace_id"] == build_workspace_id(repo_alpha, "main")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_sessions_resolves_pc_id_from_command_history_when_workspace_is_ambiguous(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_shared = "E:\\projects\\shared"
    workdir = "main"
    workspace_id = build_workspace_id(repo_shared, workdir)
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_shared,
        workdir=workdir,
        session_name="Shared task",
        status=THREAD_STATUS_RUNNING,
        lifecycle="active",
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)

    home_connection_id, home_connection_epoch = _register_pc(runtime, pc_id="pc_home")
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=home_connection_id,
        connection_epoch=home_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_shared, workdir=workdir)],
        snapshot_id="snapshot_home",
    )
    office_connection_id, office_connection_epoch = _register_pc(runtime, pc_id="pc_office")
    _replace_snapshot(
        runtime,
        pc_id="pc_office",
        connection_id=office_connection_id,
        connection_epoch=office_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_shared, workdir=workdir)],
        snapshot_id="snapshot_office",
    )
    _enqueue_status_command(
        runtime,
        pc_id="pc_office",
        workspace_id=workspace_id,
        session_id="thread_001",
        repo_path=repo_shared,
        workdir=workdir,
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["session_count"] == 1
        assert payload["sessions"][0]["session_id"] == "thread_001"
        assert payload["sessions"][0]["pc_id"] == "pc_office"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_sessions_returns_null_pc_id_when_resolution_is_ambiguous(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_shared = "E:\\projects\\shared"
    workdir = "main"
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_shared,
        workdir=workdir,
        session_name="Shared task",
        status=THREAD_STATUS_RUNNING,
        lifecycle="active",
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)

    home_connection_id, home_connection_epoch = _register_pc(runtime, pc_id="pc_home")
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=home_connection_id,
        connection_epoch=home_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_shared, workdir=workdir)],
        snapshot_id="snapshot_home",
    )
    office_connection_id, office_connection_epoch = _register_pc(runtime, pc_id="pc_office")
    _replace_snapshot(
        runtime,
        pc_id="pc_office",
        connection_id=office_connection_id,
        connection_epoch=office_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_shared, workdir=workdir)],
        snapshot_id="snapshot_office",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["session_count"] == 1
        assert payload["sessions"][0]["session_id"] == "thread_001"
        assert payload["sessions"][0]["pc_id"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_sessions_honor_include_ended_and_id_filters(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\alpha"
    workdir = "main"
    workspace_id = build_workspace_id(repo_path, workdir)
    _create_session(
        task_root,
        thread_id="thread_active",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Active task",
        status=THREAD_STATUS_RUNNING,
        lifecycle="active",
        updated_at="2026-03-27T10:01:00",
    )
    _create_session(
        task_root,
        thread_id="thread_ended",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Ended task",
        status=THREAD_STATUS_DONE,
        lifecycle="ended",
        updated_at="2026-03-27T10:02:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_alpha")
    _replace_snapshot(
        runtime,
        pc_id="pc_alpha",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_alpha",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        default_response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
            params={"session_id": "thread_ended"},
        )
        default_payload = default_response.json()

        ended_response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
            params={
                "include_ended": "true",
                "session_id": "thread_ended",
                "workspace_id": workspace_id,
                "pc_id": "pc_alpha",
            },
        )
        ended_payload = ended_response.json()

        assert default_response.status_code == 200
        assert default_payload["session_count"] == 0
        assert ended_response.status_code == 200
        assert ended_payload["session_count"] == 1
        assert ended_payload["sessions"][0]["session_id"] == "thread_ended"
        assert ended_payload["sessions"][0]["lifecycle"] == "ended"
        assert ended_payload["sessions"][0]["pc_id"] == "pc_alpha"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_sessions_return_task_root_unavailable_when_relay_task_root_is_unconfigured(tmp_path) -> None:
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
        response = _get_sessions(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 503
        assert payload["error_code"] == "task_root_unavailable"
        assert payload["retryable"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
