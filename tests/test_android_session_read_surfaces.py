from __future__ import annotations

import asyncio
import json
import logging
import threading
from urllib.parse import urlencode

import requests
import websockets
from websockets.exceptions import ConnectionClosed

from mail_runner.relay_server.android_session_history_facade import (
    ANDROID_SESSION_HISTORY_PATH,
    ANDROID_SESSION_HISTORY_SCHEMA_VERSION,
)
from mail_runner.relay_server.android_session_snapshot_facade import ANDROID_SESSION_SNAPSHOT_PATH
from mail_runner.relay_server.android_sessions_facade import ANDROID_SESSIONS_PATH
from mail_runner.relay_server.app import build_http_server, build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.projection_store import (
    ProjectionRoundUpsert,
    ProjectionSessionBatch,
    ProjectionSessionUpsert,
)
from mail_runner.relay_server.session_store import InMemorySessionStore, PersistentSessionStore
from mail_runner.thread_store import build_workspace_id


def _get_json(url: str, *, auth_token: str) -> requests.Response:
    return requests.get(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {auth_token}",
        },
        timeout=5,
    )


def _canonical_snapshot_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": payload["schema_version"],
        "locator": payload["locator"],
        "session": payload["session"],
        "session_snapshot": payload["session_snapshot"],
    }


def _relay_action_events(caplog) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for record in caplog.records:
        if record.name != "mail_runner.relay_server.app":
            continue
        if not record.message.startswith("relay_action "):
            continue
        events.append(json.loads(record.message.split(" ", 1)[1]))
    return events


def _projection_batch(
    *,
    version: int,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    snapshot_status: str,
    list_status: str,
    last_summary: str,
    updated_at: str,
) -> ProjectionSessionBatch:
    return ProjectionSessionBatch(
        batch_id=f"batch:{version}",
        connection_epoch=1,
        sent_at=updated_at,
        session=ProjectionSessionUpsert(
            idempotency_key=f"session:{version}",
            projection_version=version,
            pc_id="pc_alpha",
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            session_name="Android read surfaces",
            backend="codex",
            backend_transport="sdk",
            profile="default",
            permission="default",
            repo_path="E:\\projects\\android_task_manager",
            workdir="feature/taskmail",
            list_status=list_status,
            snapshot_status=snapshot_status,
            lifecycle="active",
            current_task_id="task_001",
            queued_task_id=None,
            pending_task_count=0,
            last_summary=last_summary,
            last_active_at=updated_at,
            last_progress_at=updated_at,
            paused_from_status=None,
            backend_session_id="backend-session-001",
            backend_session_resumable=True,
            question_state=None,
            timeline_items=[],
            created_at="2026-03-29T10:00:00",
            updated_at=updated_at,
            source_updated_at=updated_at,
        ),
        rounds=[
            ProjectionRoundUpsert(
                idempotency_key=f"round:{version}",
                round_id="hist_round_task_001",
                task_id="task_001",
                round_sort_at=updated_at,
                created_at=updated_at,
                status=snapshot_status,
                speaker_label="Codex",
                input_text="Audit the relay-only Android read surfaces.",
                process_items=[],
                result_text=last_summary,
                input_attachments=[],
                result_attachments=[],
                source_updated_at=updated_at,
                projection_version=version,
            )
        ],
        closeouts=[],
    )


def _seed_projection_store(
    runtime,
    *,
    version: int,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    snapshot_status: str,
    list_status: str,
    last_summary: str,
    updated_at: str,
) -> None:
    assert runtime.projection_store is not None
    runtime.projection_store.apply_session_batch(
        _projection_batch(
            version=version,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            snapshot_status=snapshot_status,
            list_status=list_status,
            last_summary=last_summary,
            updated_at=updated_at,
        )
    )


def test_android_sessions_http_surface_reads_projection_store(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
    session_id = "session_001"
    thread_id = "thread_001"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(state_dir),
    )
    runtime = build_pc_control_runtime(config)
    _seed_projection_store(
        runtime,
        version=1,
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        snapshot_status="running",
        list_status="running",
        last_summary="Projection-store list view is live.",
        updated_at="2026-03-29T10:00:00",
    )
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_json(
            f"http://{host}:{port}{ANDROID_SESSIONS_PATH}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["session_count"] == 1
        assert payload["sessions"][0]["pc_id"] == "pc_alpha"
        assert payload["sessions"][0]["workspace_id"] == workspace_id
        assert payload["sessions"][0]["session_id"] == session_id
        assert payload["sessions"][0]["thread_id"] == thread_id
        assert payload["sessions"][0]["status"] == "running"
        assert payload["sessions"][0]["last_summary"] == "Projection-store list view is live."
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_history_http_surface_returns_authoritative_rounds(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
    session_id = "session_001"
    thread_id = "thread_001"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(state_dir),
    )
    runtime = build_pc_control_runtime(config)
    _seed_projection_store(
        runtime,
        version=1,
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        snapshot_status="running",
        list_status="running",
        last_summary="Projection-store history is live.",
        updated_at="2026-03-29T10:00:00",
    )
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        query = urlencode({"workspace_id": workspace_id, "session_id": session_id})
        response = _get_json(
            f"http://{host}:{port}{ANDROID_SESSION_HISTORY_PATH}?{query}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["schema_version"] == ANDROID_SESSION_HISTORY_SCHEMA_VERSION
        assert payload["locator"]["pc_id"] == "pc_alpha"
        assert payload["locator"]["workspace_id"] == workspace_id
        assert payload["locator"]["session_id"] == session_id
        assert payload["locator"]["thread_id"] == thread_id
        assert payload["session"]["session_id"] == session_id
        assert len(payload["history_rounds"]) == 1
        assert payload["history_rounds"][0]["status"] == "running"
        assert payload["history_rounds"][0]["result"]["text"] == "Projection-store history is live."
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_http_surface_reads_projection_store(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
    session_id = "session_001"
    thread_id = "thread_001"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(state_dir),
    )
    runtime = build_pc_control_runtime(config)
    _seed_projection_store(
        runtime,
        version=1,
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        snapshot_status="running",
        list_status="running",
        last_summary="Projection-store snapshot is live.",
        updated_at="2026-03-29T10:00:00",
    )
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        query = urlencode({"workspace_id": workspace_id, "session_id": session_id})
        response = _get_json(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}?{query}",
            auth_token="android-secret",
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["locator"]["pc_id"] == "pc_alpha"
        assert payload["locator"]["workspace_id"] == workspace_id
        assert payload["locator"]["session_id"] == session_id
        assert payload["locator"]["thread_id"] == thread_id
        assert payload["session_snapshot"]["status"] == "running"
        assert payload["session_snapshot"]["last_summary"] == "Projection-store snapshot is live."
        assert "live_process" in payload["session_snapshot"]
        assert payload["session_snapshot"]["live_process"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_updates_websocket_pushes_live_process_without_session_head_version_bump(tmp_path) -> None:
    async def _run() -> None:
        state_dir = tmp_path / "relay_state"
        workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
        session_id = "session_001"
        thread_id = "thread_001"
        config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            android_app_token="android-secret",
            state_dir=str(state_dir),
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="bot@example.com",
        )
        session_store = PersistentSessionStore(state_dir)
        packet_store = PersistentAcceptedPacketStore(state_dir)
        runtime = build_pc_control_runtime(config)
        _seed_projection_store(
            runtime,
            version=1,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            snapshot_status="running",
            list_status="running",
            last_summary="Projection-store websocket is live.",
            updated_at="2026-03-29T10:00:00",
        )
        relay = build_runtime_relay(
            config,
            session_store=session_store,
            packet_store=packet_store,
        )
        relay._phase3_broadcast_interval_seconds = 0.2
        server = await start_relay_server(
            config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=runtime,
        )
        try:
            host, port = server.sockets[0].getsockname()[:2]
            query = urlencode({"workspace_id": workspace_id, "session_id": session_id})
            async with websockets.connect(
                f"ws://{host}:{port}/v1/android/session-updates?{query}",
                open_timeout=15,
                close_timeout=15,
                extra_headers={"Authorization": "Bearer android-secret"},
                max_size=32 * 1024 * 1024,
            ) as websocket:
                initial_payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                assert initial_payload["message_type"] == "session_snapshot"
                assert initial_payload["payload"]["session_snapshot"]["live_process"] is None

                assert runtime.projection_store is not None
                assert runtime.projection_store.upsert_session_live_process(
                    pc_id="pc_alpha",
                    workspace_id=workspace_id,
                    session_id=session_id,
                    command_id="cmd_live_001",
                    stream_id="thread_001:task_001",
                    task_id="task_001",
                    last_seq=2,
                    items=[
                        {
                            "item_id": "process:thread_001:task_001:1",
                            "kind": "assistant",
                            "created_at": "2026-03-29T10:01:00",
                            "updated_at": "2026-03-29T10:01:00",
                            "status": "streaming",
                            "text": "Streaming assistant output.",
                        }
                    ],
                    updated_at="2026-03-29T10:01:00",
                    status="streaming",
                ) is True

                updated_payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                assert updated_payload["message_type"] == "session_snapshot"
                assert updated_payload["payload"]["session_snapshot"]["live_process"] == {
                    "status": "streaming",
                    "updated_at": "2026-03-29T10:01:00",
                    "items": [
                        {
                            "item_id": "process:thread_001:task_001:1",
                            "kind": "assistant",
                            "created_at": "2026-03-29T10:01:00",
                            "updated_at": "2026-03-29T10:01:00",
                            "status": "streaming",
                            "text": "Streaming assistant output.",
                        }
                    ],
                }
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_android_session_updates_websocket_pushes_snapshot_changes(tmp_path) -> None:
    async def _run() -> None:
        state_dir = tmp_path / "relay_state"
        workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
        session_id = "session_001"
        thread_id = "thread_001"
        config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            android_app_token="android-secret",
            state_dir=str(state_dir),
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="bot@example.com",
        )
        session_store = PersistentSessionStore(state_dir)
        packet_store = PersistentAcceptedPacketStore(state_dir)
        runtime = build_pc_control_runtime(config)
        _seed_projection_store(
            runtime,
            version=1,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            snapshot_status="running",
            list_status="running",
            last_summary="Projection-store websocket is live.",
            updated_at="2026-03-29T10:00:00",
        )
        relay = build_runtime_relay(
            config,
            session_store=session_store,
            packet_store=packet_store,
        )
        relay._phase3_broadcast_interval_seconds = 0.2
        server = await start_relay_server(
            config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=runtime,
        )
        try:
            host, port = server.sockets[0].getsockname()[:2]
            query = urlencode({"workspace_id": workspace_id, "session_id": session_id})
            async with websockets.connect(
                f"ws://{host}:{port}/v1/android/session-updates?{query}",
                open_timeout=15,
                close_timeout=15,
                extra_headers={"Authorization": "Bearer android-secret"},
                max_size=32 * 1024 * 1024,
            ) as websocket:
                initial_payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                assert initial_payload["message_type"] == "session_snapshot"
                assert initial_payload["payload"]["session_snapshot"]["status"] == "running"

                _seed_projection_store(
                    runtime,
                    version=2,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    thread_id=thread_id,
                    snapshot_status="done",
                    list_status="done",
                    last_summary="Projection-store websocket observed the update.",
                    updated_at="2026-03-29T10:01:00",
                )

                updated_payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                assert updated_payload["message_type"] == "session_snapshot"
                assert updated_payload["payload"]["session_snapshot"]["status"] == "done"
                assert (
                    updated_payload["payload"]["session_snapshot"]["last_summary"]
                    == "Projection-store websocket observed the update."
                )
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_android_session_updates_websocket_initial_snapshot_is_isomorphic_to_http_and_idle_is_silent(tmp_path) -> None:
    async def _run() -> None:
        state_dir = tmp_path / "relay_state"
        workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
        session_id = "session_001"
        thread_id = "thread_001"
        config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            android_app_token="android-secret",
            state_dir=str(state_dir),
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="bot@example.com",
        )
        session_store = PersistentSessionStore(state_dir)
        packet_store = PersistentAcceptedPacketStore(state_dir)
        runtime = build_pc_control_runtime(config)
        _seed_projection_store(
            runtime,
            version=1,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            snapshot_status="running",
            list_status="running",
            last_summary="Projection-store snapshot is live.",
            updated_at="2026-03-29T10:00:00",
        )
        relay = build_runtime_relay(
            config,
            session_store=session_store,
            packet_store=packet_store,
        )
        relay._phase3_broadcast_interval_seconds = 0.2
        server = await start_relay_server(
            config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=runtime,
        )
        try:
            host, port = server.sockets[0].getsockname()[:2]
            query = urlencode({"workspace_id": workspace_id, "session_id": session_id})
            http_response = await asyncio.to_thread(
                _get_json,
                f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}?{query}",
                auth_token="android-secret",
            )
            assert http_response.status_code == 200
            http_payload = http_response.json()

            async with websockets.connect(
                f"ws://{host}:{port}/v1/android/session-updates?{query}",
                open_timeout=15,
                close_timeout=15,
                extra_headers={"Authorization": "Bearer android-secret"},
                max_size=32 * 1024 * 1024,
            ) as websocket:
                initial_message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                assert initial_message["message_type"] == "session_snapshot"
                assert _canonical_snapshot_payload(initial_message["payload"]) == _canonical_snapshot_payload(
                    http_payload
                )

                try:
                    await asyncio.wait_for(websocket.recv(), timeout=0.7)
                except asyncio.TimeoutError:
                    pass
                else:  # pragma: no cover
                    raise AssertionError("session-updates pushed a duplicate snapshot without any business change")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_android_session_updates_websocket_logs_send_actions_when_enabled(tmp_path, caplog) -> None:
    async def _run() -> None:
        state_dir = tmp_path / "relay_state"
        workspace_id = build_workspace_id("E:\\projects\\android_task_manager", "feature/taskmail")
        session_id = "session_001"
        thread_id = "thread_001"
        config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            android_app_token="android-secret",
            state_dir=str(state_dir),
            action_log_enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="bot@example.com",
        )
        session_store = PersistentSessionStore(state_dir)
        packet_store = PersistentAcceptedPacketStore(state_dir)
        runtime = build_pc_control_runtime(config)
        _seed_projection_store(
            runtime,
            version=1,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            snapshot_status="running",
            list_status="running",
            last_summary="Projection-store websocket is live.",
            updated_at="2026-03-29T10:00:00",
        )
        relay = build_runtime_relay(
            config,
            session_store=session_store,
            packet_store=packet_store,
        )
        relay._phase3_broadcast_interval_seconds = 0.2
        server = await start_relay_server(
            config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=runtime,
        )
        try:
            host, port = server.sockets[0].getsockname()[:2]
            query = urlencode({"workspace_id": workspace_id, "session_id": session_id})
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="mail_runner.relay_server.app"):
                async with websockets.connect(
                    f"ws://{host}:{port}/v1/android/session-updates?{query}",
                    open_timeout=15,
                    close_timeout=15,
                    extra_headers={"Authorization": "Bearer android-secret"},
                    max_size=32 * 1024 * 1024,
                ) as websocket:
                    initial_message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                    assert initial_message["message_type"] == "session_snapshot"

                    _seed_projection_store(
                        runtime,
                        version=2,
                        workspace_id=workspace_id,
                        session_id=session_id,
                        thread_id=thread_id,
                        snapshot_status="done",
                        list_status="done",
                        last_summary="Projection-store websocket logged the update.",
                        updated_at="2026-03-29T10:02:00",
                    )
                    updated_message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                    assert updated_message["message_type"] == "session_snapshot"

            action_events = _relay_action_events(caplog)
            connect_events = [
                event for event in action_events if event["lane"] == "android_session_updates" and event["action"] == "connect"
            ]
            send_events = [
                event for event in action_events if event["lane"] == "android_session_updates" and event["action"] == "send"
            ]

            assert any(event["locator"]["session_id"] == session_id for event in connect_events)
            assert any(
                event["push_reason"] == "initial_snapshot"
                and event["locator"]["session_id"] == session_id
                and event["detail_status"] == "running"
                for event in send_events
            )
            assert any(
                event["push_reason"] == "state_changed"
                and event["locator"]["session_id"] == session_id
                and event["detail_status"] == "done"
                for event in send_events
            )
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_android_session_updates_websocket_returns_error_envelope_and_closes_for_invalid_locator(tmp_path) -> None:
    async def _run() -> None:
        state_dir = tmp_path / "relay_state"
        config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            android_app_token="android-secret",
            state_dir=str(state_dir),
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
        relay._phase3_broadcast_interval_seconds = 0.2
        server = await start_relay_server(
            config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=runtime,
        )
        try:
            host, port = server.sockets[0].getsockname()[:2]
            async with websockets.connect(
                f"ws://{host}:{port}/v1/android/session-updates",
                open_timeout=15,
                close_timeout=15,
                extra_headers={"Authorization": "Bearer android-secret"},
                max_size=32 * 1024 * 1024,
            ) as websocket:
                error_message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
                assert error_message["message_type"] == "error"
                assert error_message["payload"]["error_code"] == "invalid_payload"
                assert error_message["payload"]["retryable"] is False

                try:
                    await asyncio.wait_for(websocket.recv(), timeout=3)
                except ConnectionClosed as exc:
                    assert exc.code == 1008
                    assert exc.reason == "invalid_payload"
                else:  # pragma: no cover
                    raise AssertionError("session-updates connection stayed open after an invalid locator error")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_run())
