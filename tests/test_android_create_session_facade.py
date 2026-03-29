from __future__ import annotations

import asyncio
import base64
import threading
from datetime import datetime
from pathlib import Path

import requests

from mail_runner.config import AppConfig
from mail_runner.pc_control_plane_client import PcControlPlaneClient
from mail_runner.relay_server.android_create_session_facade import ANDROID_CREATE_SESSION_PATH
from mail_runner.relay_server.android_session_action_facade import ANDROID_SESSION_ACTION_PATH
from mail_runner.relay_server.app import build_http_server, build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_control_protocol import (
    build_pc_hello,
    parse_pc_control_client_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import InMemorySessionStore, PersistentSessionStore
from mail_runner.thread_store import create_thread, load_thread_state, save_thread_state
from mail_runner.workspace import WorkspaceManager


class _StubRunner:
    def __init__(self, *, active_count: int = 0, queued_count: int = 0) -> None:
        self._active_count = active_count
        self._queued_count = queued_count
        self.snapshots = []

    def active_count(self) -> int:
        return self._active_count

    def queued_count(self) -> int:
        return self._queued_count

    def start_background_task(self, snapshot, **_kwargs):
        self.snapshots.append(snapshot)
        return None


class _StatePersistingRunner:
    def __init__(self, task_root: Path) -> None:
        self.workspace = WorkspaceManager(task_root)
        self.snapshots = []

    def active_count(self) -> int:
        return 0

    def queued_count(self) -> int:
        return 0

    def start_background_task(
        self,
        snapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        session_name: str | None = None,
        on_accepted=None,
        on_running=None,
        on_finished=None,
    ):
        self.snapshots.append(snapshot)
        snapshot_path = self.workspace.save_snapshot(snapshot)
        snapshot_rel = self.workspace.to_thread_relative(snapshot.thread_id, snapshot_path)
        state_path = self.workspace.thread_state_path(snapshot.thread_id)
        if state_path.exists():
            state = load_thread_state(snapshot.thread_id, self.workspace.task_root)
            state.backend = snapshot.backend
            state.profile = snapshot.profile
            state.permission = snapshot.permission
            state.repo_path = snapshot.repo_path
            state.workdir = snapshot.workdir
            state.current_task_id = snapshot.task_id
            state.last_task_snapshot_file = snapshot_rel
            state.status = "accepted"
            state.lifecycle = "active"
            state.last_active_at = snapshot.updated_at
            state.last_progress_at = snapshot.updated_at
            state.backend_transport = snapshot.backend_transport
            if snapshot.canonical_reply_recipient is not None:
                state.canonical_reply_recipient = snapshot.canonical_reply_recipient
            state.updated_at = snapshot.updated_at
            save_thread_state(state, self.workspace.task_root)
        else:
            state = create_thread(
                thread_id=snapshot.thread_id,
                root_message_id=root_message_id or f"local-root:{snapshot.thread_id}",
                latest_message_id=latest_message_id or f"local-latest:{snapshot.thread_id}",
                subject_norm=subject_norm or snapshot.thread_id,
                backend=snapshot.backend,
                profile=snapshot.profile,
                permission=snapshot.permission,
                repo_path=snapshot.repo_path,
                workdir=snapshot.workdir,
                current_task_id=snapshot.task_id,
                last_task_snapshot_file=snapshot_rel,
                task_root=self.workspace.task_root,
                status="accepted",
                history_files=[],
                last_summary=None,
                lifecycle="active",
                last_active_at=snapshot.updated_at,
                pending_question_id=None,
                pending_question_text=None,
                pending_choices=[],
                pending_question_set_id=None,
                pending_questions=[],
                collected_answers=[],
                awaiting_since=None,
                paused_from_status=None,
                session_id=snapshot.thread_id,
                session_name=session_name or snapshot.thread_id,
                session_norm=(session_name or snapshot.thread_id).lower(),
                canonical_reply_recipient=snapshot.canonical_reply_recipient,
                backend_session_id=snapshot.backend_session_id,
                backend_session_resumable=False,
                backend_transport=snapshot.backend_transport,
                queued_task_id=None,
                queued_snapshot_file=None,
                created_at=snapshot.created_at,
                updated_at=snapshot.updated_at,
                last_progress_at=snapshot.updated_at,
            )
        if on_accepted is not None:
            on_accepted(state)
        return state


class _FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)
        return f"<sent-{len(self.sent_messages)}@example.com>"


def _post_create_session(url: str, payload: dict[str, object]) -> requests.Response:
    return _post_create_session_with_token(url, payload, auth_token="android-secret")


def _post_session_action(url: str, payload: dict[str, object]) -> requests.Response:
    headers = {"Authorization": "Bearer android-secret"}
    return requests.post(url, headers=headers, json=payload, timeout=5)


def _post_create_session_with_token(
    url: str,
    payload: dict[str, object],
    *,
    auth_token: str | None,
) -> requests.Response:
    headers = {}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=5,
    )


def _register_online_pc(runtime) -> None:
    hello_message = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at=datetime.now().replace(microsecond=0).isoformat(),
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities={
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
            },
        )
    )
    runtime.handle_hello(hello_message, provided_token="relay-secret")


async def _wait_until(predicate, *, timeout_seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not satisfied before the timeout")


async def _run_create_session_roundtrip_test(
    tmp_path,
    *,
    runner: _StubRunner,
    execution_policy: dict[str, object],
    attachments: list[dict[str, object]] | None = None,
    canonical_reply_recipient: str = "user@example.com",
    workspace_provider=None,
    codex_profile_models: dict[str, str] | None = None,
    heartbeat_interval_seconds: int = 1,
    snapshot_interval_seconds: int | None = None,
) -> dict[str, object]:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    repo_dir = sync_root / "alpha"
    repo_dir.mkdir(parents=True)

    relay_config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(state_dir),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_name="TaskMail Relay",
        from_addr="bot@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    pc_runtime = build_pc_control_runtime(relay_config)
    relay = build_runtime_relay(
        relay_config,
        session_store=session_store,
        packet_store=packet_store,
    )
    server = await start_relay_server(
        relay_config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
        pc_control_runtime=pc_runtime,
    )
    client = None
    try:
        host, port = server.sockets[0].getsockname()[:2]
        app_config = AppConfig(
            relay_url=f"ws://{host}:{port}/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
            project_sync_roots=[str(sync_root)],
            codex_profile_models=dict(codex_profile_models or {}),
        )
        client = PcControlPlaneClient(
            relay_url=app_config.relay_url,
            transport_token=app_config.relay_transport_token,
            pc_id=app_config.relay_client_id,
            client_version=app_config.relay_client_version,
            display_name="pc_home",
            config=app_config,
            runner=runner,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            snapshot_interval_seconds=(
                heartbeat_interval_seconds if snapshot_interval_seconds is None else snapshot_interval_seconds
            ),
            workspace_provider=workspace_provider,
        )
        client.start()
        await _wait_until(
            lambda: (
                pc_runtime.node_store.get_node("pc_home") is not None
                and len(pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")) == 1
            ),
            timeout_seconds=5,
        )
        workspace = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")[0]

        response = await asyncio.to_thread(
            _post_create_session,
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": workspace.workspace_id,
                "prompt": "Refactor floor_shear.py",
                "canonical_reply_recipient": canonical_reply_recipient,
                "execution_policy": execution_policy,
                "mode": "modify",
                "timeout_seconds": 181,
                "acceptance": ["pytest passes", "brief summary"],
                "attachments": attachments,
                "repo_path": workspace.repo_path,
                "source": "android-ui",
            },
        )
        payload = response.json()
        await _wait_until(lambda: len(runner.snapshots) == 1 or payload["status"] == "rejected", timeout_seconds=5)
        return {
            "response": response,
            "payload": payload,
            "runtime": pc_runtime,
            "workspace": workspace,
            "runner": runner,
        }
    finally:
        if client is not None:
            client.stop()
        server.close()
        await server.wait_closed()


def test_android_create_session_requires_prompt_when_posting_http(tmp_path) -> None:
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
        response = _post_create_session(
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": "workspace_001",
                "execution_policy": {"backend": "codex"},
            },
        )
        payload = response.json()

        assert response.status_code == 400
        assert payload["error_code"] == "invalid_payload"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_create_session_requires_execution_policy_when_posting_http(tmp_path) -> None:
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
        response = _post_create_session(
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": "workspace_001",
                "prompt": "Refactor floor_shear.py",
                "canonical_reply_recipient": "user@example.com",
            },
        )
        payload = response.json()

        assert response.status_code == 400
        assert payload["error_code"] == "invalid_payload"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_create_session_requires_canonical_reply_recipient_when_posting_http(tmp_path) -> None:
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
        response = _post_create_session(
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": "workspace_001",
                "prompt": "Refactor floor_shear.py",
                "execution_policy": {"backend": "codex"},
            },
        )
        payload = response.json()

        assert response.status_code == 400
        assert payload["error_code"] == "invalid_payload"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_create_session_requires_dedicated_android_app_token(tmp_path) -> None:
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
        response = _post_create_session_with_token(
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": "workspace_001",
                "prompt": "Refactor floor_shear.py",
                "canonical_reply_recipient": "user@example.com",
                "execution_policy": {"backend": "codex"},
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


def test_android_create_session_maps_pc_offline_to_rejected_submit_ack(tmp_path) -> None:
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
        response = _post_create_session(
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": "workspace_001",
                "prompt": "Refactor floor_shear.py",
                "canonical_reply_recipient": "user@example.com",
                "execution_policy": {"backend": "codex"},
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["status"] == "rejected"
        assert payload["submit_ack"]["ack_status"] == "rejected"
        assert payload["submit_ack"]["error_code"] == "pc_offline"
        assert "session_binding" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_create_session_maps_workspace_unavailable_to_rejected_submit_ack(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    _register_online_pc(runtime)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _post_create_session(
            f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
            {
                "pc_id": "pc_home",
                "workspace_id": "workspace_missing",
                "prompt": "Refactor floor_shear.py",
                "canonical_reply_recipient": "user@example.com",
                "execution_policy": {"backend": "codex"},
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["status"] == "rejected"
        assert payload["submit_ack"]["ack_status"] == "rejected"
        assert payload["submit_ack"]["error_code"] == "workspace_unavailable"
        assert "session_binding" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_create_session_roundtrip_returns_submit_ack_and_session_binding(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "default",
                "backend_transport": "sdk",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]
    runtime = result["runtime"]
    runner = result["runner"]

    assert response.status_code == 200
    assert payload["status"] == "accepted"
    assert payload["submit_ack"]["ack_status"] == "accepted"
    assert payload["session_binding"]["pc_id"] == "pc_home"
    record = runtime.command_store.get_command("pc_home", payload["command_id"])
    assert record is not None
    assert record.command_type == "new_task"
    assert record.session_id == payload["session_binding"]["session_id"]
    assert record.command_payload["task_text"] == "Refactor floor_shear.py"
    assert record.command_payload["canonical_reply_recipient"] == "user@example.com"
    assert record.command_payload["timeout_seconds"] == 181
    assert record.command_payload["timeout_minutes"] == 4
    assert record.command_payload["acceptance"] == ["pytest passes", "brief summary"]
    assert record.command_payload["source"] == "android-ui"
    assert len(runner.snapshots) == 1
    assert runner.snapshots[0].thread_id == payload["session_binding"]["session_id"]
    assert runner.snapshots[0].task_text == "Refactor floor_shear.py"
    assert runner.snapshots[0].timeout_minutes == 4
    assert runner.snapshots[0].backend_transport == "sdk"
    assert runner.snapshots[0].canonical_reply_recipient == "user@example.com"


def test_android_create_session_binds_canonical_reply_recipient_for_immediate_session_action(tmp_path) -> None:
    async def _run() -> None:
        state_dir = tmp_path / "relay_state"
        sync_root = tmp_path / "sync_root"
        repo_dir = sync_root / "alpha"
        repo_dir.mkdir(parents=True)
        task_root = tmp_path / "tasks"
        runner = _StatePersistingRunner(task_root)
        mail_client = _FakeMailClient()

        relay_config = RelayServerConfig(
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
            from_name="TaskMail Relay",
            from_addr="bot@example.com",
        )
        session_store = PersistentSessionStore(state_dir)
        packet_store = PersistentAcceptedPacketStore(state_dir)
        pc_runtime = build_pc_control_runtime(relay_config)
        relay = build_runtime_relay(
            relay_config,
            session_store=session_store,
            packet_store=packet_store,
        )
        server = await start_relay_server(
            relay_config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=pc_runtime,
        )
        client = None
        try:
            host, port = server.sockets[0].getsockname()[:2]
            app_config = AppConfig(
                relay_url=f"ws://{host}:{port}/relay",
                relay_transport_token="relay-secret",
                relay_client_id="pc_home",
                relay_client_version="0.1.0",
                project_sync_roots=[str(sync_root)],
                from_addr="bot@example.com",
            )
            client = PcControlPlaneClient(
                relay_url=app_config.relay_url,
                transport_token=app_config.relay_transport_token,
                pc_id=app_config.relay_client_id,
                client_version=app_config.relay_client_version,
                display_name="pc_home",
                config=app_config,
                runner=runner,
                mail_client=mail_client,
                heartbeat_interval_seconds=1,
                snapshot_interval_seconds=1,
            )
            client.start()
            await _wait_until(
                lambda: (
                    pc_runtime.node_store.get_node("pc_home") is not None
                    and len(pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")) == 1
                ),
                timeout_seconds=5,
            )
            workspace = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")[0]

            create_response = await asyncio.to_thread(
                _post_create_session,
                f"http://{host}:{port}{ANDROID_CREATE_SESSION_PATH}",
                {
                    "pc_id": "pc_home",
                    "workspace_id": workspace.workspace_id,
                    "prompt": "Refactor floor_shear.py",
                    "canonical_reply_recipient": "user@example.com",
                    "execution_policy": {
                        "backend": "codex",
                        "profile": "default",
                        "permission": "default",
                        "backend_transport": "sdk",
                    },
                    "repo_path": workspace.repo_path,
                    "mode": "modify",
                    "source": "android-ui",
                },
            )
            create_payload = create_response.json()
            assert create_response.status_code == 200
            assert create_payload["status"] == "accepted"

            session_id = create_payload["session_binding"]["session_id"]
            await _wait_until(
                lambda: runner.workspace.thread_state_path(session_id).exists(),
                timeout_seconds=5,
            )
            thread_state = load_thread_state(session_id, task_root)
            assert thread_state.canonical_reply_recipient == "user@example.com"
            mail_dir = task_root / session_id / "mail"
            assert not mail_dir.exists() or list(mail_dir.glob("raw_*.json")) == []

            session_action_response = await asyncio.to_thread(
                _post_session_action,
                f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
                {
                    "request_id": "req_reply_after_create_001",
                    "action": "reply",
                    "target": {
                        "session_id": session_id,
                    },
                    "reply": {
                        "reply_text": "Please continue with the cleanup.",
                    },
                },
            )
            session_action_payload = session_action_response.json()

            assert session_action_response.status_code == 200
            assert session_action_payload["status"] == "accepted"
            assert session_action_payload["submit_ack"]["ack_status"] == "accepted"
            assert session_action_payload["target_session_identity"]["session_id"] == session_id
        finally:
            if client is not None:
                client.stop()
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_android_create_session_roundtrip_does_not_wait_for_heartbeat(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "default",
                "backend_transport": "sdk",
            },
            heartbeat_interval_seconds=60,
            snapshot_interval_seconds=60,
        )
    )
    response = result["response"]
    payload = result["payload"]
    runtime = result["runtime"]

    assert response.status_code == 200
    assert payload["status"] == "accepted"
    assert payload["submit_ack"]["ack_status"] == "accepted"
    assert payload["session_binding"]["pc_id"] == "pc_home"
    assert runtime.command_store.get_command("pc_home", payload["command_id"]) is not None


def test_android_create_session_roundtrip_remains_available_in_vps_only_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mail_runner.relay_server.app.load_config", lambda: AppConfig(control_plane_mode="vps_only"))

    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "default",
                "backend_transport": "sdk",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]
    runtime = result["runtime"]
    workspace = result["workspace"]
    runner = result["runner"]

    assert response.status_code == 200
    assert payload["status"] == "accepted"
    assert payload["submit_ack"]["ack_status"] == "accepted"
    assert payload["session_binding"]["pc_id"] == "pc_home"
    assert payload["session_binding"]["workspace_id"] == workspace.workspace_id
    record = runtime.command_store.get_command("pc_home", payload["command_id"])
    assert record is not None
    assert record.command_type == "new_task"
    assert record.session_id == payload["session_binding"]["session_id"]
    assert len(runner.snapshots) == 1
    assert runner.snapshots[0].thread_id == payload["session_binding"]["session_id"]


def test_android_create_session_roundtrip_returns_accepted_but_queued_with_binding(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(active_count=1),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "default",
                "backend_transport": "sdk",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]

    assert response.status_code == 200
    assert payload["status"] == "accepted_but_queued"
    assert payload["submit_ack"]["ack_status"] == "accepted_but_queued"
    assert payload["submit_ack"]["queue_position"] == 1
    assert payload["session_binding"]["pc_id"] == "pc_home"


def test_android_create_session_roundtrip_surfaces_profile_model_unresolved_without_binding(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "strong",
                "permission": "default",
                "backend_transport": "sdk",
            },
            codex_profile_models={"strong": ""},
        )
    )
    response = result["response"]
    payload = result["payload"]

    assert response.status_code == 200
    assert payload["status"] == "rejected"
    assert payload["submit_ack"]["ack_status"] == "rejected"
    assert payload["submit_ack"]["error_code"] == "profile_model_unresolved"
    assert "session_binding" not in payload


def test_android_create_session_roundtrip_surfaces_unsupported_backend_without_binding(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "claude",
                "permission": "default",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]

    assert response.status_code == 200
    assert payload["status"] == "rejected"
    assert payload["submit_ack"]["ack_status"] == "rejected"
    assert payload["submit_ack"]["error_code"] == "unsupported_backend"
    assert "session_binding" not in payload


def test_android_create_session_roundtrip_surfaces_unsupported_profile_without_binding(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "ghost",
                "permission": "default",
                "backend_transport": "sdk",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]

    assert response.status_code == 200
    assert payload["status"] == "rejected"
    assert payload["submit_ack"]["ack_status"] == "rejected"
    assert payload["submit_ack"]["error_code"] == "unsupported_profile"
    assert "session_binding" not in payload


def test_android_create_session_roundtrip_surfaces_unsupported_permission_without_binding(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "dangerous",
                "backend_transport": "sdk",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]

    assert response.status_code == 200
    assert payload["status"] == "rejected"
    assert payload["submit_ack"]["ack_status"] == "rejected"
    assert payload["submit_ack"]["error_code"] == "unsupported_permission"
    assert "session_binding" not in payload


def test_android_create_session_maps_unsupported_backend_transport_to_unsupported_backend(tmp_path) -> None:
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "default",
                "backend_transport": "http",
            },
        )
    )
    response = result["response"]
    payload = result["payload"]

    assert response.status_code == 200
    assert payload["status"] == "rejected"
    assert payload["submit_ack"]["ack_status"] == "rejected"
    assert payload["submit_ack"]["error_code"] == "unsupported_backend"
    assert "session_binding" not in payload


def test_android_create_session_roundtrip_materializes_initial_attachments_into_workspace(tmp_path) -> None:
    attachment_bytes = b"diagram:v1"
    result = asyncio.run(
        _run_create_session_roundtrip_test(
            tmp_path,
            runner=_StubRunner(),
            execution_policy={
                "backend": "codex",
                "profile": "default",
                "permission": "default",
                "backend_transport": "sdk",
            },
            attachments=[
                {
                    "name": "wireframe.png",
                    "content_type": "image/png",
                    "size_bytes": len(attachment_bytes),
                    "content_bytes_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                },
            ],
        )
    )
    response = result["response"]
    payload = result["payload"]
    runtime = result["runtime"]
    runner = result["runner"]

    assert response.status_code == 200
    assert payload["status"] == "accepted"
    record = runtime.command_store.get_command("pc_home", payload["command_id"])
    assert record is not None
    assert isinstance(record.command_payload["attachments"], list)
    assert record.command_payload["attachments"][0]["name"] == "wireframe.png"
    assert len(runner.snapshots) == 1
    assert len(runner.snapshots[0].attachments) == 1

    saved_path = runner.snapshots[0].attachments[0]
    assert saved_path.endswith("wireframe.png")
    assert saved_path.startswith(str(tmp_path / "sync_root" / "alpha"))
    assert Path(saved_path).exists()
    assert Path(saved_path).read_bytes() == attachment_bytes
