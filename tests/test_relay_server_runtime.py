from __future__ import annotations

import asyncio
import json
import urllib.request

import websockets

from mail_runner.models import ThreadState
from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket, TransportReceipt
from mail_runner.outbound.relay_bootstrap import build_hello_payload
from mail_runner.outbound.relay_transport import RelayTransport
from mail_runner.relay_server.app import start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.direct_actions import RelayTaskMailDirectNewTaskMailBridge
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.phase3_subscription import ThreadStorePhase3SessionDetailProvider
from mail_runner.relay_server.protocol import (
    RelayHelloAckMessage,
    RelayPacketAckMessage,
    RelaySessionUpdateMessage,
    parse_server_message,
)
from mail_runner.relay_server.session_store import PersistentSessionStore
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_thread_state


def test_relay_transport_sends_packet_to_websocket_runtime_and_healthz(tmp_path) -> None:
    asyncio.run(_run_websocket_runtime_test(tmp_path))


def test_taskmail_direct_new_task_packet_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_runtime_test(tmp_path))


def test_phase3_subscribe_packet_reaches_websocket_runtime_and_emits_snapshot(tmp_path) -> None:
    asyncio.run(_run_phase3_subscribe_runtime_test(tmp_path))


def test_phase3_subscription_pushes_live_deltas_over_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_phase3_live_delta_runtime_test(tmp_path))


async def _run_websocket_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        delivery_callback=lambda packet: TransportReceipt(
            success=True,
            transport_name="relay_smtp",
            sent_at="2026-03-20T16:30:00",
            transport_message_id="<relay-runtime@example.com>",
        ),
        clock=lambda: "2026-03-20T16:30:00",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        payload, status = await asyncio.to_thread(_fetch_healthz, host, port)
        assert status == 200
        assert payload["status"] == "ok"
        assert payload["packet_count"] == 0

        request = OutboundDispatchRequest(
            packet=TaskRunPacket(
                packet_id="packet:remote:001",
                task_id="task_001",
                created_at="2026-03-20T16:30:00",
                message_kind="status_update",
                content_format="text/plain+text/html",
                html="<html><body>Done.</body></html>",
                text_fallback="Status: DONE\n",
                state_patch={"thread_id": "thread_001"},
                client_trace_id="task_001",
            ),
            to_addr="user@example.com",
            subject="[DONE][S:thread_001] Demo task",
        )

        receipt = await asyncio.to_thread(
            lambda: RelayTransport(
                relay_url=f"ws://{host}:{port}/relay",
                transport_token="relay-secret",
                client_id="pc-001",
                client_version="0.1.0",
            ).send(request)
        )

        stored_packet = packet_store.get_packet("packet:remote:001")
        assert receipt.success is True
        assert receipt.transport_message_id == "<relay-runtime@example.com>"
        assert stored_packet is not None
        assert stored_packet.delivery_status == "delivered"
        assert stored_packet.transport_message_id == "<relay-runtime@example.com>"
    finally:
        server.close()
        await server.wait_closed()


async def _run_taskmail_direct_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    fake_mail_client = _FakeMailClient()
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        direct_packet_handler=RelayTaskMailDirectNewTaskMailBridge(config, mail_client=fake_mail_client),
        clock=lambda: "2026-03-21T12:30:00",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/relay",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    build_hello_payload(
                        client_id="android-taskmail",
                        client_version="0.1.0",
                        transport_token="relay-secret",
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, RelayHelloAckMessage)

            await websocket.send(json.dumps(_canonical_direct_packet(), ensure_ascii=False))
            packet_ack = parse_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-taskmail:new-task:req_001")

            assert isinstance(packet_ack, RelayPacketAckMessage)
            assert packet_ack.accepted is True
            assert packet_ack.transport_message_id == "<direct-bridge-1@example.com>"
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert fake_mail_client.calls[0]["to_addr"] == "bot@example.com"
    finally:
        server.close()
        await server.wait_closed()


async def _run_phase3_subscribe_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    save_thread_state(_build_thread_state(), task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        phase3_session_detail_provider=ThreadStorePhase3SessionDetailProvider(task_root=task_root),
        subscription_id_factory=lambda session_id: f"sub:{session_id}",
        clock=lambda: "2026-03-21T22:33:03",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/relay",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    build_hello_payload(
                        client_id="android-taskmail",
                        client_version="0.1.0",
                        transport_token="relay-secret",
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, RelayHelloAckMessage)

            await websocket.send(json.dumps(_phase3_subscribe_packet(), ensure_ascii=False))
            subscribe_ack = parse_server_message(json.loads(await websocket.recv()))
            session_update = parse_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-taskmail:subscribe-detail:req_001")
            stored_session = session_store.get_session(hello_ack.connection_id)

            assert isinstance(subscribe_ack, RelayPacketAckMessage)
            assert subscribe_ack.accepted is True
            assert isinstance(session_update, RelaySessionUpdateMessage)
            assert session_update.subscription_id == "sub:session_001"
            assert session_update.session_snapshot["status"] == "running"
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert stored_session is not None
            assert stored_session.active_subscription_id == "sub:session_001"
            assert stored_session.last_subscription_sequence == 1
    finally:
        server.close()
        await server.wait_closed()


async def _run_phase3_live_delta_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    save_thread_state(thread_state, task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        phase3_session_detail_provider=ThreadStorePhase3SessionDetailProvider(task_root=task_root),
        subscription_id_factory=lambda session_id: f"sub:{session_id}",
        phase3_broadcast_interval_seconds=0.05,
        clock=lambda: "2026-03-21T22:33:03",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/relay",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    build_hello_payload(
                        client_id="android-taskmail",
                        client_version="0.1.0",
                        transport_token="relay-secret",
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, RelayHelloAckMessage)

            await websocket.send(json.dumps(_phase3_subscribe_packet(), ensure_ascii=False))
            subscribe_ack = parse_server_message(json.loads(await websocket.recv()))
            session_snapshot = parse_server_message(json.loads(await websocket.recv()))

            assert isinstance(subscribe_ack, RelayPacketAckMessage)
            assert subscribe_ack.accepted is True
            assert isinstance(session_snapshot, RelaySessionUpdateMessage)
            assert session_snapshot.update_type == "session_snapshot"

            thread_state.status = "done"
            thread_state.last_summary = "Completed after live delta push."
            thread_state.last_progress_at = "2026-03-21T22:35:00"
            thread_state.updated_at = "2026-03-21T22:35:00"
            save_thread_state(thread_state, task_root)

            delta_one = parse_server_message(json.loads(await asyncio.wait_for(websocket.recv(), timeout=3)))
            delta_two = parse_server_message(json.loads(await asyncio.wait_for(websocket.recv(), timeout=3)))
            stored_session = session_store.get_session(hello_ack.connection_id)

            assert isinstance(delta_one, RelaySessionUpdateMessage)
            assert delta_one.session_delta["delta_type"] == "state_transition"
            assert delta_one.session_delta["state_transition"]["status"] == "done"
            assert isinstance(delta_two, RelaySessionUpdateMessage)
            assert delta_two.session_delta["delta_type"] == "timeline_append"
            assert delta_two.session_delta["timeline_items"][0]["item_type"] == "terminal_summary"
            assert stored_session is not None
            assert stored_session.last_subscription_sequence == 3
    finally:
        server.close()
        await server.wait_closed()


def _fetch_healthz(host: str, port: int) -> tuple[dict, int]:
    with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=5) as response:
        return json.loads(response.read().decode("utf-8")), response.status


class _FakeMailClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_mail(self, **kwargs):
        self.calls.append(kwargs)
        return "<direct-bridge-1@example.com>"


def _canonical_direct_packet() -> dict[str, object]:
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:new-task:req_001",
        "client_trace_id": "req_001",
        "task_run_packet": {
            "schema_version": "phase2-direct-outbound-contract-v1",
            "action": "new_task",
            "request_id": "req_001",
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "new_task": {
                "backend": "codex",
                "repo_path": "E:\\projects\\android_task_manager",
                "workdir": "feature/taskmail/internal",
                "task_text": "Audit the direct-send handoff path.",
                "subject_title": "Audit the direct-send handoff path",
                "timeout_minutes": 120,
                "mode": "analysis_only",
                "profile": "android",
                "permission": "highest",
                "acceptance": [
                    "List any contract mismatches.",
                    "Do not change user-facing reply semantics.",
                ],
            },
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "phase2-direct-outbound-contract-v1",
            "action": "new_task",
            "fallback_policy": "mail",
        },
        "sent_at": "2026-03-21T12:30:00",
    }


def _build_thread_state() -> ThreadState:
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail/internal"
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="phase 3 detail bridge",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id="task_001",
        last_task_snapshot_file="snapshot_001.json",
        status="running",
        last_summary="Running.",
        lifecycle="active",
        last_active_at="2026-03-21T22:33:03",
        last_progress_at="2026-03-21T22:33:03",
        workspace_id=build_workspace_id(repo_path, workdir),
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id="session_001",
        session_name="Phase 3 detail bridge",
        session_norm="phase 3 detail bridge",
        created_at="2026-03-21T22:30:00",
        updated_at="2026-03-21T22:33:03",
    )


def _phase3_subscribe_packet() -> dict[str, object]:
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail/internal"
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:subscribe-detail:req_001",
        "client_trace_id": "req_001",
        "task_run_packet": {
            "schema_version": "phase3-direct-inbound-wire-v1",
            "action": "subscribe_session_detail",
            "request_id": "req_001",
            "origin": {
                "client": "android_taskmail",
            },
            "subscription": {
                "workspace_id": build_workspace_id(repo_path, workdir),
                "session_id": "session_001",
                "last_known_sequence": 0,
                "reason": "detail_open",
            },
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "phase3-direct-inbound-wire-v1",
            "action": "subscribe_session_detail",
        },
        "sent_at": "2026-03-21T22:33:01",
    }
