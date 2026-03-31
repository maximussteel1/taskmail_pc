from __future__ import annotations

import asyncio
import base64
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from mail_runner.config import AppConfig
from mail_runner.models import QuestionItem, ThreadState
from mail_runner.pc_control_plane_client import PcControlPlaneClient
from mail_runner.relay_server.android_session_action_facade import ANDROID_SESSION_ACTION_PATH
from mail_runner.relay_server.app import build_http_server, build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_control_protocol import (
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_client_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import InMemorySessionStore, PersistentSessionStore
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_thread_state
from mail_runner.workspace import WorkspaceManager


class _TaskRootRunner:
    def __init__(self, task_root: Path) -> None:
        self.workspace = WorkspaceManager(task_root)

    def active_count(self) -> int:
        return 0

    def queued_count(self) -> int:
        return 0


def _post_session_action(url: str, payload: dict[str, object]) -> requests.Response:
    return _post_session_action_with_token(url, payload, auth_token="android-secret")


def _post_session_action_with_token(
    url: str,
    payload: dict[str, object],
    *,
    auth_token: str | None,
) -> requests.Response:
    headers = {}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return requests.post(url, headers=headers, json=payload, timeout=5)


def _workspace_item(*, repo_path: str, workdir: str | None) -> dict[str, object]:
    return {
        "workspace_id": build_workspace_id(repo_path, workdir),
        "workspace_norm": build_workspace_norm(repo_path, workdir),
        "repo_path": repo_path,
        "workdir": workdir,
        "display_name": "android_task_manager",
        "source": "project_sync_roots",
        "capabilities": {
            "streaming": True,
            "artifact_manifest": True,
            "workspace_snapshot": True,
            "supported_backends": ["codex"],
            "profile_catalogs": {"codex": ["default", "strong"]},
            "permission_modes": ["default", "highest"],
            "backend_transport_modes": {"codex": ["cli", "sdk"]},
        },
    }


def _create_existing_thread(
    task_root,
    *,
    thread_id: str = "thread_001",
    session_id: str = "session_001",
    repo_path: str = "E:\\projects\\android_task_manager",
    workdir: str | None = "feature/taskmail/internal",
    session_name: str = "Phase 3 detail bridge",
    status: str = "done",
    last_summary: str = "Completed.",
    pending_questions: list[QuestionItem] | None = None,
) -> ThreadState:
    state = ThreadState(
        thread_id=thread_id,
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="phase 3 detail bridge",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id="task_001",
        last_task_snapshot_file="runs/current_snapshot.json",
        status=status,
        profile="strong",
        permission="highest",
        last_summary=last_summary,
        lifecycle="active",
        last_active_at="2026-03-29T10:00:20",
        last_progress_at="2026-03-29T10:00:20",
        workspace_id=build_workspace_id(repo_path, workdir),
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id=session_id,
        session_name=session_name,
        session_norm="phase 3 detail bridge",
        backend_transport="sdk",
        pending_questions=list(pending_questions or []),
        created_at="2026-03-29T10:00:20",
        updated_at="2026-03-29T10:00:20",
    )
    save_thread_state(state, task_root)
    return state


def _register_online_pc_with_workspace(runtime, *, workspace: dict[str, object]) -> tuple[str, int]:
    now = datetime.now().replace(microsecond=0).isoformat()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at=now,
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=workspace["capabilities"],
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="relay-secret")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_snapshot_001",
            trace_id="trace_snapshot_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at=now,
            snapshot_id="snapshot_001",
            workspaces=[workspace],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None
    return connection_id, connection_epoch


def _start_ack_worker(
    runtime,
    *,
    expected_command_type: str,
    ack_status: str,
    queue_position: int | None = None,
    reason: str | None = None,
    error_code: str | None = None,
):
    errors: list[BaseException] = []

    def _worker() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            for record in runtime.command_store.list_commands():
                if record.command_type != expected_command_type:
                    continue
                if record.ack_status is not None:
                    return
                runtime.command_store.record_ack(
                    pc_id=record.pc_id,
                    command_id=record.command_id,
                    ack_status=ack_status,
                    ack_message_id=f"ack:{record.command_id}",
                    acked_at="2026-03-29T10:00:30",
                    queue_position=queue_position,
                    reason=reason,
                    error_code=error_code,
                )
                return
            time.sleep(0.05)
        errors.append(AssertionError("timed out waiting for facade command dispatch"))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread, errors


async def _wait_until_async(predicate, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not satisfied before the timeout")


def test_android_session_action_requires_dedicated_android_app_token(tmp_path) -> None:
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
        response = _post_session_action_with_token(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_001",
                "action": "reply",
                "target": {
                    "workspace_id": "workspace_001",
                    "session_id": "session_001",
                },
                "reply": {
                    "reply_text": "Please continue.",
                },
            },
            auth_token="relay-secret",
        )
        payload = response.json()

        assert response.status_code == 401
        assert payload["error_code"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_accepts_missing_recipient_binding(tmp_path) -> None:
    async def _run() -> None:
        task_root = tmp_path / "tasks"
        thread_state = _create_existing_thread(task_root)
        workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
        state_dir = tmp_path / "relay_state"
        config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            android_app_token="android-secret",
            state_dir=str(state_dir),
            task_root=str(task_root),
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="bot@example.com",
        )
        session_store = PersistentSessionStore(state_dir)
        packet_store = PersistentAcceptedPacketStore(state_dir)
        runtime = build_pc_control_runtime(config)
        relay = build_runtime_relay(
            config,
            session_store=session_store,
            packet_store=packet_store,
        )
        server = await start_relay_server(
            config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=runtime,
        )
        client = None
        try:
            host, port = server.sockets[0].getsockname()[:2]
            app_config = AppConfig(
                relay_url=f"ws://{host}:{port}/relay",
                relay_transport_token="relay-secret",
                relay_client_id="pc_home",
                relay_client_version="0.1.0",
                from_addr="bot@example.com",
            )
            client = PcControlPlaneClient(
                relay_url=app_config.relay_url,
                transport_token=app_config.relay_transport_token,
                pc_id=app_config.relay_client_id,
                client_version=app_config.relay_client_version,
                display_name="pc_home",
                config=app_config,
                runner=_TaskRootRunner(task_root),
                workspace_provider=lambda: [workspace],
            )
            client.start()
            await _wait_until_async(
                lambda: (
                    runtime.node_store.get_node("pc_home") is not None
                    and len(runtime.workspace_store.list_workspaces(pc_id="pc_home")) == 1
                ),
                timeout_seconds=5,
            )

            response = await asyncio.to_thread(
                _post_session_action,
                f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
                {
                    "request_id": "req_reply_missing_binding_001",
                    "action": "reply",
                    "target": {
                        "session_id": thread_state.session_id,
                    },
                    "reply": {
                        "reply_text": "Please continue.",
                    },
                },
            )
            payload = response.json()

            assert response.status_code == 200
            assert payload["status"] == "accepted"
            assert payload["submit_ack"]["ack_status"] == "accepted"
            assert payload["submit_ack"]["error_code"] is None
        finally:
            if client is not None:
                client.stop()
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_android_session_action_supports_session_id_only_target_lookup(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        ack_thread, ack_errors = _start_ack_worker(
            runtime,
            expected_command_type="reply",
            ack_status="accepted",
        )
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_001",
                "action": "reply",
                "target": {
                    "session_id": thread_state.session_id,
                },
                "reply": {
                    "reply_text": "Please continue.",
                },
            },
        )
        payload = response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors

        assert response.status_code == 200
        assert payload["status"] == "accepted"
        assert payload["target_session_identity"] == {
            "pc_id": "pc_home",
            "workspace_id": thread_state.workspace_id,
            "session_id": thread_state.session_id,
            "thread_id": thread_state.thread_id,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_returns_session_not_found_for_unknown_target(tmp_path) -> None:
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
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_001",
                "action": "reply",
                "target": {
                    "workspace_id": "workspace_missing",
                    "session_id": "session_missing",
                },
                "reply": {
                    "reply_text": "Please continue.",
                },
            },
        )
        payload = response.json()

        assert response.status_code == 404
        assert payload["error_code"] == "session_not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_returns_identity_mismatch_for_wrong_supporting_workspace(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
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
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_workspace_mismatch_001",
                "action": "reply",
                "target": {
                    "workspace_id": "workspace_wrong",
                    "session_id": thread_state.session_id,
                },
                "reply": {
                    "reply_text": "Please continue.",
                },
            },
        )
        payload = response.json()

        assert response.status_code == 409
        assert payload["error_code"] == "session_identity_mismatch"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_returns_binding_unresolved_for_ambiguous_session_id(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _create_existing_thread(
        task_root,
        thread_id="thread_alpha",
        session_id="session_shared",
        repo_path="E:\\projects\\alpha",
        workdir="main",
        session_name="Alpha task",
    )
    _create_existing_thread(
        task_root,
        thread_id="thread_beta",
        session_id="session_shared",
        repo_path="E:\\projects\\beta",
        workdir="main",
        session_name="Beta task",
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
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_ambiguous_001",
                "action": "reply",
                "target": {
                    "session_id": "session_shared",
                },
                "reply": {
                    "reply_text": "Please continue.",
                },
            },
        )
        payload = response.json()

        assert response.status_code == 409
        assert payload["error_code"] == "session_binding_unresolved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_replays_same_request_id_for_rejected_submit_ack(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_online_pc_with_workspace(runtime, workspace=workspace)
    runtime.close_connection(pc_id="pc_home", connection_id=connection_id, connection_epoch=connection_epoch)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = {
        "request_id": "req_reply_rejected_001",
        "action": "reply",
        "target": {
            "workspace_id": thread_state.workspace_id,
            "session_id": thread_state.session_id,
            "thread_id": thread_state.thread_id,
        },
        "reply": {
            "reply_text": "Please continue with the cleanup.",
        },
    }
    try:
        host, port = server.server_address[:2]
        first_response = _post_session_action(f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}", payload)
        first_payload = first_response.json()
        second_response = _post_session_action(f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}", payload)
        second_payload = second_response.json()

        assert first_response.status_code == 200
        assert first_payload["status"] == "rejected"
        assert first_payload["submit_ack"]["error_code"] == "pc_offline"
        assert second_response.status_code == 200
        assert second_payload == first_payload
        assert runtime.command_store.count() == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_rejects_request_id_conflict_after_rejected_submit_ack(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_online_pc_with_workspace(runtime, workspace=workspace)
    runtime.close_connection(pc_id="pc_home", connection_id=connection_id, connection_epoch=connection_epoch)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        rejected_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_rejected_001",
                "action": "reply",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
        assert rejected_response.status_code == 200

        conflict_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_rejected_001",
                "action": "reply",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Use a different continuation.",
                },
            },
        )
        payload = conflict_response.json()

        assert conflict_response.status_code == 409
        assert payload["error_code"] == "request_id_conflict"
        assert runtime.command_store.count() == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_status_roundtrip_returns_submit_ack_and_target_identity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ack_thread, ack_errors = _start_ack_worker(
        runtime,
        expected_command_type="status",
        ack_status="accepted_but_queued",
        queue_position=1,
    )
    try:
        host, port = server.server_address[:2]
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_status_001",
                "action": "status",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "status": {},
            },
        )
        payload = response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors

        assert response.status_code == 200
        assert payload["status"] == "accepted_but_queued"
        assert payload["submit_ack"]["ack_status"] == "accepted_but_queued"
        assert payload["submit_ack"]["queue_position"] == 1
        assert payload["target_session_identity"] == {
            "pc_id": "pc_home",
            "workspace_id": thread_state.workspace_id,
            "session_id": thread_state.session_id,
            "thread_id": thread_state.thread_id,
        }
        record = runtime.command_store.get_command("pc_home", payload["command_id"])
        assert record is not None
        assert record.command_type == "status"
        assert record.command_payload == {
            "target": {
                "scope": "current_session",
                "workspace_id": thread_state.workspace_id,
                "session_id": thread_state.session_id,
                "thread_id": thread_state.thread_id,
            },
            "status": {},
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_control_actions_roundtrip_return_submit_ack_and_target_identity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        for action in ("pause", "resume", "kill", "end"):
            ack_thread, ack_errors = _start_ack_worker(
                runtime,
                expected_command_type=action,
                ack_status="accepted",
            )
            response = _post_session_action(
                f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
                {
                "request_id": f"req_{action}_001",
                "action": action,
                "target": {
                        "workspace_id": thread_state.workspace_id,
                        "session_id": thread_state.session_id,
                        "thread_id": thread_state.thread_id,
                    },
                    action: {},
                },
            )
            payload = response.json()
            ack_thread.join(timeout=5)
            assert not ack_errors

            assert response.status_code == 200
            assert payload["status"] == "accepted"
            assert payload["submit_ack"]["ack_status"] == "accepted"
            assert payload["target_session_identity"] == {
                "pc_id": "pc_home",
                "workspace_id": thread_state.workspace_id,
                "session_id": thread_state.session_id,
                "thread_id": thread_state.thread_id,
            }
            record = runtime.command_store.get_command("pc_home", payload["command_id"])
            assert record is not None
            assert record.command_type == action
            assert record.command_payload == {
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                action: {},
            }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_rejects_non_empty_pause_payload(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_pause_invalid_001",
                "action": "pause",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "pause": {
                    "reason": "user_requested",
                },
            },
        )
        payload = response.json()

        assert response.status_code == 400
        assert payload["error_code"] == "invalid_payload"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_answers_roundtrip_returns_submit_ack_and_target_identity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(
        task_root,
        status="awaiting_user_input",
        last_summary="Waiting for answers.",
        pending_questions=[
            QuestionItem(
                question_set_id="qs_release",
                question_id="q_branch",
                question_type="single_choice",
                question_text="Select the release branch.",
                choices=["main", "release"],
            ),
            QuestionItem(
                question_set_id="qs_release",
                question_id="q_env",
                question_type="short_text",
                question_text="Which environment should be targeted?",
            ),
        ],
    )
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        ack_thread, ack_errors = _start_ack_worker(
            runtime,
            expected_command_type="answers",
            ack_status="accepted",
        )
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_answers_001",
                "action": "answers",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "answers": {
                    "question_answers": [
                        {"question_id": "q_env", "value": "staging"},
                        {"question_id": "q_branch", "value": "release"},
                    ]
                },
            },
        )
        payload = response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors

        assert response.status_code == 200
        assert payload["status"] == "accepted"
        assert payload["submit_ack"]["ack_status"] == "accepted"
        assert payload["target_session_identity"] == {
            "pc_id": "pc_home",
            "workspace_id": thread_state.workspace_id,
            "session_id": thread_state.session_id,
            "thread_id": thread_state.thread_id,
        }
        record = runtime.command_store.get_command("pc_home", payload["command_id"])
        assert record is not None
        assert record.command_type == "answers"
        assert record.command_payload == {
            "target": {
                "scope": "current_session",
                "workspace_id": thread_state.workspace_id,
                "session_id": thread_state.session_id,
                "thread_id": thread_state.thread_id,
            },
            "answers": {
                "question_answers": [
                    {"question_id": "q_branch", "value": "release"},
                    {"question_id": "q_env", "value": "staging"},
                ]
            },
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_rejects_empty_answers_payload(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_answers_invalid_001",
                "action": "answers",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "answers": {
                    "question_answers": [],
                },
            },
        )
        payload = response.json()

        assert response.status_code == 400
        assert payload["error_code"] == "invalid_payload"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_attachment_continuation_roundtrip_returns_submit_ack_and_target_identity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    attachment_bytes = b"attachment:v1"
    try:
        host, port = server.server_address[:2]
        ack_thread, ack_errors = _start_ack_worker(
            runtime,
            expected_command_type="attachment_continuation",
            ack_status="accepted",
        )
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_attachment_cont_001",
                "action": "attachment_continuation",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "attachment_continuation": {
                    "reply_text": "Please continue after reviewing the attachment.",
                    "attachments": [
                        {
                            "name": "wireframe.png",
                            "content_type": "image/png",
                            "size_bytes": len(attachment_bytes),
                            "content_bytes_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                        }
                    ],
                },
            },
        )
        payload = response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors

        assert response.status_code == 200
        assert payload["status"] == "accepted"
        assert payload["submit_ack"]["ack_status"] == "accepted"
        assert payload["target_session_identity"] == {
            "pc_id": "pc_home",
            "workspace_id": thread_state.workspace_id,
            "session_id": thread_state.session_id,
            "thread_id": thread_state.thread_id,
        }
        record = runtime.command_store.get_command("pc_home", payload["command_id"])
        assert record is not None
        assert record.command_type == "attachment_continuation"
        assert record.command_payload == {
            "target": {
                "scope": "current_session",
                "workspace_id": thread_state.workspace_id,
                "session_id": thread_state.session_id,
                "thread_id": thread_state.thread_id,
            },
            "attachment_continuation": {
                "reply_text": "Please continue after reviewing the attachment.",
                "attachments": [
                    {
                        "name": "wireframe.png",
                        "content_type": "image/png",
                        "size_bytes": len(attachment_bytes),
                        "content_bytes_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                    }
                ],
            },
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_rejects_empty_attachment_continuation_payload(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_attachment_cont_invalid_001",
                "action": "attachment_continuation",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "attachment_continuation": {
                    "attachments": [],
                },
            },
        )
        payload = response.json()

        assert response.status_code == 400
        assert payload["error_code"] == "invalid_payload"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_replays_same_request_id_with_same_response(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ack_thread, ack_errors = _start_ack_worker(
        runtime,
        expected_command_type="reply",
        ack_status="accepted",
    )
    payload = {
        "request_id": "req_reply_001",
        "action": "reply",
        "target": {
            "workspace_id": thread_state.workspace_id,
            "session_id": thread_state.session_id,
            "thread_id": thread_state.thread_id,
        },
        "reply": {
            "reply_text": "Please continue with the cleanup.",
        },
    }
    try:
        host, port = server.server_address[:2]
        first_response = _post_session_action(f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}", payload)
        first_payload = first_response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors

        second_response = _post_session_action(f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}", payload)
        second_payload = second_response.json()

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert second_payload == first_payload
        assert runtime.command_store.count() == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_action_rejects_request_id_conflict_for_different_payload(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _create_existing_thread(task_root)
    workspace = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc_with_workspace(runtime, workspace=workspace)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ack_thread, ack_errors = _start_ack_worker(
        runtime,
        expected_command_type="reply",
        ack_status="accepted",
    )
    try:
        host, port = server.server_address[:2]
        first_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_001",
                "action": "reply",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
        ack_thread.join(timeout=5)
        assert not ack_errors
        assert first_response.status_code == 200

        conflict_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            {
                "request_id": "req_reply_001",
                "action": "reply",
                "target": {
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Use a different continuation.",
                },
            },
        )
        payload = conflict_response.json()

        assert conflict_response.status_code == 409
        assert payload["error_code"] == "request_id_conflict"
        assert runtime.command_store.count() == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
