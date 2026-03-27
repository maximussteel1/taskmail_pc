from __future__ import annotations

import asyncio
import json
import urllib.request
from datetime import datetime

import requests
import websockets

from mail_runner.config import AppConfig
from mail_runner.file_surface import derive_file_surface_url, upload_artifact_to_file_surface
from mail_runner.models import RunArtifact, RunResult, ThreadState
from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket, TransportReceipt
from mail_runner.outbound.relay_bootstrap import build_hello_payload
from mail_runner.outbound.relay_transport import RelayTransport
from mail_runner.relay_server.app import build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.direct_actions import (
    RelayTaskMailDirectNewTaskMailBridge,
    RelayTaskMailDirectProjectSyncHandler,
    RelayTaskMailDirectProjectSyncMailBridge,
)
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_control_protocol import (
    build_command_ack,
    build_command_event,
    build_command_result,
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.post_creation_actions import (
    RelayTaskMailDirectCurrentSessionReplyMailBridge,
    RelayTaskMailDirectCurrentSessionStatusMailBridge,
)
from mail_runner.relay_server.phase3_subscription import ThreadStorePhase3SessionDetailProvider
from mail_runner.relay_server.protocol import (
    RelayBootstrapResultMessage,
    RelayErrorMessage,
    RelayHelloAckMessage,
    RelayPacketAckMessage,
    RelaySessionUpdateMessage,
    parse_server_message,
)
from mail_runner.relay_server.session_store import PersistentSessionStore
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS, THREAD_STATUS_PAUSED
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, load_thread_state, save_thread_state


def _pc_control_capabilities() -> dict[str, object]:
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


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def test_relay_transport_sends_packet_to_websocket_runtime_and_healthz(tmp_path) -> None:
    asyncio.run(_run_websocket_runtime_test(tmp_path))


def test_unified_runtime_exposes_file_surface_on_same_port_as_websocket(tmp_path) -> None:
    asyncio.run(_run_unified_file_surface_runtime_test(tmp_path))


def test_taskmail_direct_new_task_packet_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_runtime_test(tmp_path))


def test_taskmail_direct_new_task_packet_with_none_fallback_policy_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_runtime_test(tmp_path, fallback_policy="none"))


def test_taskmail_direct_project_sync_packet_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_project_sync_runtime_test(tmp_path))


def test_taskmail_direct_project_sync_v2_packet_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_project_sync_v2_runtime_test(tmp_path))


def test_taskmail_direct_current_session_status_packet_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_status_runtime_test(tmp_path))


def test_taskmail_direct_current_session_status_packet_with_none_fallback_policy_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_status_runtime_test(tmp_path, fallback_policy="none"))


def test_taskmail_direct_current_session_reply_packet_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_reply_runtime_test(tmp_path))


def test_taskmail_direct_current_session_reply_packet_with_none_fallback_policy_reaches_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_taskmail_direct_reply_runtime_test(tmp_path, fallback_policy="none"))


def test_runtime_relay_hard_stops_direct_reply_for_paused_session_when_task_root_is_configured(tmp_path) -> None:
    asyncio.run(_run_runtime_direct_reply_paused_hard_stop_test(tmp_path))


def test_phase3_subscribe_packet_reaches_websocket_runtime_and_emits_snapshot(tmp_path) -> None:
    asyncio.run(_run_phase3_subscribe_runtime_test(tmp_path))


def test_phase3_subscription_pushes_live_deltas_over_websocket_runtime(tmp_path) -> None:
    asyncio.run(_run_phase3_live_delta_runtime_test(tmp_path))


def test_pc_control_operator_dispatch_reaches_unified_runtime_and_closes_result(tmp_path) -> None:
    asyncio.run(_run_pc_control_operator_dispatch_runtime_test(tmp_path))


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


async def _run_taskmail_direct_runtime_test(tmp_path, *, fallback_policy: str = "mail") -> None:
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

            await websocket.send(json.dumps(_canonical_direct_packet(fallback_policy=fallback_policy), ensure_ascii=False))
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


async def _run_pc_control_operator_dispatch_runtime_test(tmp_path) -> None:
    now = _now()
    state_dir = tmp_path / "relay_state"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    pc_runtime = build_pc_control_runtime(config)
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        delivery_callback=lambda _packet: TransportReceipt(
            success=True,
            transport_name="relay_smtp",
            sent_at="2026-03-25T23:30:00",
            transport_message_id="<relay-runtime@example.com>",
        ),
        clock=lambda: "2026-03-25T23:30:00",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
        pc_control_runtime=pc_runtime,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/pc-control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    build_pc_hello(
                        message_id="hello_001",
                        trace_id="trace_hello_001",
                        pc_id="pc-home",
                        sent_at=now,
                        display_name="Home PC",
                        client_version="0.1.0",
                        host_fingerprint="host_001",
                        runtime_fingerprint="runtime_001",
                        capabilities=_pc_control_capabilities(),
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_pc_control_server_message(json.loads(await websocket.recv()))
            assert hello_ack.payload["accepted"] is True
            connection_epoch = hello_ack.connection_epoch

            await websocket.send(
                json.dumps(
                    build_workspace_snapshot(
                        message_id="snapshot_001",
                        trace_id="trace_snapshot_001",
                        pc_id="pc-home",
                        connection_epoch=connection_epoch,
                        sent_at=now,
                        snapshot_id="snapshot_001",
                        workspaces=[
                            {
                                "workspace_id": "workspace_001",
                                "workspace_norm": "e:/projects/mail_based_task_manager",
                                "repo_path": "E:\\projects\\mail_based_task_manager",
                                "workdir": None,
                                "display_name": "mail_based_task_manager",
                                "source": "project_sync_roots",
                                "capabilities": _pc_control_capabilities(),
                            }
                        ],
                    ),
                    ensure_ascii=False,
                )
            )

            response = await asyncio.to_thread(
                requests.post,
                f"http://{host}:{port}/debug/pc-control/dispatch",
                headers={"Authorization": "Bearer relay-secret"},
                json={
                    "pc_id": "pc-home",
                    "workspace_id": "workspace_001",
                    "command_id": "cmd_live_001",
                    "command_type": "status",
                    "session_id": "thread_001",
                    "execution_policy": {
                        "backend": "codex",
                        "profile": "default",
                        "permission": "default",
                        "backend_transport": "sdk",
                    },
                    "payload": {},
                },
                timeout=5,
            )
            assert response.status_code == 200

            dispatch = parse_pc_control_server_message(json.loads(await asyncio.wait_for(websocket.recv(), timeout=2.0)))
            assert dispatch.payload["command_id"] == "cmd_live_001"

            await websocket.send(
                json.dumps(
                    build_command_ack(
                        message_id="ack_001",
                        trace_id="trace_ack_001",
                        pc_id="pc-home",
                        connection_epoch=connection_epoch,
                        sent_at=now,
                        command_id="cmd_live_001",
                        ack_status="accepted",
                        queue_position=None,
                        reason=None,
                        error_code=None,
                    ),
                    ensure_ascii=False,
                )
            )
            await websocket.send(
                json.dumps(
                    build_command_event(
                        message_id="event_001",
                        trace_id="trace_event_001",
                        pc_id="pc-home",
                        connection_epoch=connection_epoch,
                        sent_at=now,
                        command_id="cmd_live_001",
                        event_id="event_live_001",
                        event_type="running",
                        summary="running",
                        effective_execution={
                            "backend": "codex",
                            "profile": "default",
                            "permission": "default",
                            "backend_transport": "sdk",
                            "resolved_model": "gpt-5-codex",
                        },
                        event_payload={},
                    ),
                    ensure_ascii=False,
                )
            )
            await websocket.send(
                json.dumps(
                    build_command_result(
                        message_id="result_001",
                        trace_id="trace_result_001",
                        pc_id="pc-home",
                        connection_epoch=connection_epoch,
                        sent_at=now,
                        command_id="cmd_live_001",
                        result_id="result_live_001",
                        final_status="done",
                        summary="done",
                        structured_payload={"kind": "status_snapshot"},
                        effective_execution={
                            "backend": "codex",
                            "profile": "default",
                            "permission": "default",
                            "backend_transport": "sdk",
                            "resolved_model": "gpt-5-codex",
                        },
                        error_code=None,
                        error_message=None,
                    ),
                    ensure_ascii=False,
                )
            )

            async def _result_recorded() -> bool:
                record = pc_runtime.command_store.get_command("pc-home", "cmd_live_001")
                return record is not None and record.result is not None and record.latest_event_type == "running"

            for _ in range(20):
                if await _result_recorded():
                    break
                await asyncio.sleep(0.1)
            record = pc_runtime.command_store.get_command("pc-home", "cmd_live_001")
            assert record is not None
            assert record.ack_status == "accepted"
            assert record.final_status == "done"
            assert record.result is not None
            assert record.result.final_status == "done"
    finally:
        server.close()
        await server.wait_closed()


async def _run_unified_file_surface_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    artifact_path = tmp_path / "relay-preview.png"
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nunified-relay")
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
            sent_at="2026-03-23T16:30:00",
            transport_message_id="<relay-unified@example.com>",
        ),
        clock=lambda: "2026-03-23T16:30:00",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        receipt = await asyncio.to_thread(
            lambda: RelayTransport(
                relay_url=f"ws://{host}:{port}/relay",
                transport_token="relay-secret",
                client_id="pc-001",
                client_version="0.1.0",
            ).send(
                OutboundDispatchRequest(
                    packet=TaskRunPacket(
                        packet_id="packet:unified:001",
                        task_id="task_001",
                        created_at="2026-03-23T16:30:00",
                        message_kind="status_update",
                        content_format="text/plain+text/html",
                        html="<html><body>Done.</body></html>",
                        text_fallback="Status: DONE\n",
                        state_patch={"thread_id": "thread_001"},
                        client_trace_id="task_001",
                    ),
                    to_addr="user@example.com",
                    subject="[DONE][S:thread_001] Unified relay smoke",
                )
            )
        )
        assert receipt.success is True

        upload_result = await asyncio.to_thread(
            lambda: upload_artifact_to_file_surface(
                task_root,
                RunResult(
                    task_id="task_001",
                    thread_id="thread_001",
                    backend=BACKEND_OPENCODE,
                    status=RUN_STATUS_SUCCESS,
                    exit_code=0,
                    started_at="2026-03-23T16:30:00",
                    finished_at="2026-03-23T16:31:00",
                    stdout_file="runs/task_001/stdout.log",
                    stderr_file="runs/task_001/stderr.log",
                    summary_file="runs/task_001/summary.md",
                    artifacts_dir="runs/task_001/artifacts",
                    changed_files=[],
                    tests_passed=True,
                    error_message=None,
                ),
                RunArtifact(
                    artifact_id="artifact-preview",
                    path=str(artifact_path),
                    name="relay-preview.png",
                    kind="image",
                    content_type="image/png",
                    source="directory_fallback",
                    attach=True,
                    inline_preview=False,
                ),
                file_surface_url=derive_file_surface_url(f"ws://{host}:{port}/relay"),
                transport_token="relay-secret",
                role="attachment",
                timeout_seconds=5,
            )
        )
        assert upload_result.success is True
        assert upload_result.descriptor is not None

        download_url = str(upload_result.descriptor["artifact"]["download_url"])
        downloaded, status = await asyncio.to_thread(
            _fetch_authenticated_bytes,
            f"http://{host}:{port}{download_url}",
            "relay-secret",
        )
        assert status == 200
        assert downloaded == artifact_path.read_bytes()
    finally:
        server.close()
        await server.wait_closed()


async def _run_taskmail_direct_project_sync_runtime_test(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectProjectSyncMailBridge(config, mail_client=fake_mail_client),
        clock=lambda: "2026-03-23T12:30:00",
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

            await websocket.send(json.dumps(_canonical_direct_project_sync_packet(), ensure_ascii=False))
            packet_ack = parse_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-taskmail:sync-project-folders:req_001")

            assert isinstance(packet_ack, RelayPacketAckMessage)
            assert packet_ack.accepted is True
            assert packet_ack.transport_message_id == "<direct-bridge-1@example.com>"
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert fake_mail_client.calls[0]["subject"] == "[SYNC]"
            assert fake_mail_client.calls[0]["body"] == ""
    finally:
        server.close()
        await server.wait_closed()


async def _run_taskmail_direct_project_sync_v2_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    (sync_root / "alpha").mkdir(parents=True)
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
        direct_packet_handler=RelayTaskMailDirectProjectSyncHandler(
            config=AppConfig(project_sync_roots=[str(sync_root)]),
            clock=lambda: "2026-03-23T12:30:00",
        ),
        clock=lambda: "2026-03-23T12:30:00",
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

            await websocket.send(json.dumps(_canonical_direct_project_sync_v2_packet(), ensure_ascii=False))
            packet_ack = parse_server_message(json.loads(await websocket.recv()))
            bootstrap_result = parse_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-taskmail:sync-project-folders:req_002")

            assert isinstance(packet_ack, RelayPacketAckMessage)
            assert packet_ack.accepted is True
            assert packet_ack.transport_message_id is None
            assert isinstance(bootstrap_result, RelayBootstrapResultMessage)
            assert bootstrap_result.receipt_id == packet_ack.receipt_id
            assert bootstrap_result.sync_project_folders_result["roots"][0]["entries"] == [
                {
                    "name": "alpha",
                    "path": str(sync_root / "alpha"),
                }
            ]
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert stored_packet.server_messages == [
                {
                    "message_type": "bootstrap_result",
                    "schema_version": "taskmail-bootstrap-control-contract-v2",
                    "action": "sync_project_folders",
                    "request_id": "req_002",
                    "packet_id": "android-taskmail:sync-project-folders:req_002",
                    "receipt_id": packet_ack.receipt_id,
                    "result_id": "bootstrap-result:req_002",
                    "sent_at": "2026-03-23T12:30:00",
                    "sync_project_folders_result": bootstrap_result.sync_project_folders_result,
                }
            ]
    finally:
        server.close()
        await server.wait_closed()


async def _run_taskmail_direct_status_runtime_test(tmp_path, *, fallback_policy: str = "mail") -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    thread_state.status = "running"
    thread_state.last_summary = "Still running."
    thread_state.updated_at = "2026-03-22T12:29:00"
    thread_state.last_progress_at = thread_state.updated_at
    save_thread_state(thread_state, task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        task_root=str(task_root),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = build_runtime_relay(
        config,
        session_store=session_store,
        packet_store=packet_store,
    )
    relay._clock = lambda: "2026-03-22T12:30:00"
    fake_mail_client = _FakeMailClient()
    status_handler = relay._direct_packet_handlers[1]
    status_handler._mail_client = fake_mail_client
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

            await websocket.send(
                json.dumps(
                    _post_creation_status_packet(
                        workspace_id=thread_state.workspace_id,
                        session_id=thread_state.session_id or thread_state.thread_id,
                        thread_id=thread_state.thread_id,
                        fallback_policy=fallback_policy,
                    ),
                    ensure_ascii=False,
                )
            )
            packet_ack = parse_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-taskmail:session-action:req_001")
            closeout_payload = json.loads(
                (
                    task_root
                    / "thread_001"
                    / "session_actions"
                    / "req_001"
                    / "session_action_closeout.json"
                ).read_text(encoding="utf-8")
            )

            assert isinstance(packet_ack, RelayPacketAckMessage)
            assert packet_ack.accepted is True
            assert packet_ack.transport_message_id == "<direct-bridge-1@example.com>"
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert fake_mail_client.calls[0]["to_addr"] == "bot@example.com"
            assert fake_mail_client.calls[0]["subject"] == "Re: [S:session_001] Phase 3 detail bridge"
            assert "/status" in fake_mail_client.calls[0]["body"]
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Relay-Receipt-Id"] == packet_ack.receipt_id
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Action-Type"] == "status"
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Target-Workspace-Id"] == thread_state.workspace_id
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Target-Session-Id"] == "session_001"
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Target-Thread-Id"] == "thread_001"
            assert closeout_payload["action_type"] == "status"
            assert closeout_payload["request_id"] == "req_001"
            assert closeout_payload["receipt_id"] == packet_ack.receipt_id
            assert closeout_payload["ingress_message_id"] == "<direct-bridge-1@example.com>"
            assert closeout_payload["terminal_mail_subject"] == "[STATUS][S:session_001] Phase 3 detail bridge"
            assert closeout_payload["target_session_identity"] == {
                "workspace_id": thread_state.workspace_id,
                "session_id": "session_001",
                "thread_id": "thread_001",
            }
    finally:
        server.close()
        await server.wait_closed()


async def _run_taskmail_direct_reply_runtime_test(tmp_path, *, fallback_policy: str = "mail") -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    thread_state.status = "done"
    thread_state.last_summary = "Completed."
    thread_state.updated_at = "2026-03-23T09:38:00"
    thread_state.last_progress_at = thread_state.updated_at
    save_thread_state(thread_state, task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        task_root=str(task_root),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = build_runtime_relay(
        config,
        session_store=session_store,
        packet_store=packet_store,
    )
    relay._clock = lambda: "2026-03-23T09:40:00"
    fake_mail_client = _FakeMailClient()
    reply_handler = relay._direct_packet_handlers[2]
    reply_handler._mail_client = fake_mail_client
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

            await websocket.send(
                json.dumps(
                    _post_creation_reply_packet(
                        workspace_id=thread_state.workspace_id,
                        session_id=thread_state.session_id or thread_state.thread_id,
                        thread_id=thread_state.thread_id,
                        reply_text="Please continue with the cleanup.",
                        fallback_policy=fallback_policy,
                    ),
                    ensure_ascii=False,
                )
            )
            packet_ack = parse_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-taskmail:session-action:req_001")
            closeout_payload = json.loads(
                (
                    task_root
                    / "thread_001"
                    / "session_actions"
                    / "req_001"
                    / "session_action_closeout.json"
                ).read_text(encoding="utf-8")
            )

            assert isinstance(packet_ack, RelayPacketAckMessage)
            assert packet_ack.accepted is True
            assert packet_ack.transport_message_id == "<direct-bridge-1@example.com>"
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert fake_mail_client.calls[0]["to_addr"] == "bot@example.com"
            assert fake_mail_client.calls[0]["subject"] == "Re: [S:session_001] Phase 3 detail bridge"
            assert "Please continue with the cleanup." in fake_mail_client.calls[0]["body"]
            assert fake_mail_client.calls[0]["in_reply_to"] == "<latest@example.com>"
            assert fake_mail_client.calls[0]["references"] == ["<root@example.com>", "<latest@example.com>"]
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Relay-Receipt-Id"] == packet_ack.receipt_id
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Action-Type"] == "reply"
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Target-Workspace-Id"] == thread_state.workspace_id
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Target-Session-Id"] == "session_001"
            assert fake_mail_client.calls[0]["headers"]["X-TaskMail-Target-Thread-Id"] == "thread_001"
            assert closeout_payload["action_type"] == "reply"
            assert closeout_payload["request_id"] == "req_001"
            assert closeout_payload["receipt_id"] == packet_ack.receipt_id
            assert closeout_payload["target_session_identity"] == {
                "workspace_id": thread_state.workspace_id,
                "session_id": "session_001",
                "thread_id": "thread_001",
            }
    finally:
        server.close()
        await server.wait_closed()


async def _run_runtime_direct_reply_paused_hard_stop_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    thread_state.status = THREAD_STATUS_PAUSED
    thread_state.paused_from_status = "done"
    thread_state.updated_at = "2026-03-23T10:05:00"
    thread_state.last_progress_at = thread_state.updated_at
    save_thread_state(thread_state, task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        task_root=str(task_root),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = build_runtime_relay(
        config,
        session_store=session_store,
        packet_store=packet_store,
    )
    fake_mail_client = _FakeMailClient()
    reply_handler = relay._direct_packet_handlers[2]
    reply_handler._mail_client = fake_mail_client
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

            await websocket.send(
                json.dumps(
                    _post_creation_reply_packet(
                        workspace_id=thread_state.workspace_id,
                        session_id=thread_state.session_id or thread_state.thread_id,
                        thread_id=thread_state.thread_id,
                        reply_text="Please continue with the cleanup.",
                    ),
                    ensure_ascii=False,
                )
            )
            error = parse_server_message(json.loads(await websocket.recv()))

            assert isinstance(error, RelayErrorMessage)
            assert error.code == "validation_failed"
            assert packet_store.get_packet("android-taskmail:session-action:req_001") is None
            assert fake_mail_client.calls == []
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


def _fetch_authenticated_bytes(url: str, transport_token: str) -> tuple[bytes, int]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {transport_token}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read(), response.status


class _FakeMailClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_mail(self, **kwargs):
        self.calls.append(kwargs)
        return "<direct-bridge-1@example.com>"


def _canonical_direct_packet(*, fallback_policy: str = "mail") -> dict[str, object]:
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
            "fallback_policy": fallback_policy,
        },
        "sent_at": "2026-03-21T12:30:00",
    }


def _canonical_direct_project_sync_packet() -> dict[str, object]:
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:sync-project-folders:req_001",
        "client_trace_id": "req_001",
        "task_run_packet": {
            "schema_version": "taskmail-bootstrap-control-contract-v1",
            "action": "sync_project_folders",
            "request_id": "req_001",
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "sync_project_folders": {},
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "taskmail-bootstrap-control-contract-v1",
            "action": "sync_project_folders",
            "fallback_policy": "mail",
        },
        "sent_at": "2026-03-23T12:30:00",
    }


def _canonical_direct_project_sync_v2_packet() -> dict[str, object]:
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:sync-project-folders:req_002",
        "client_trace_id": "req_002",
        "task_run_packet": {
            "schema_version": "taskmail-bootstrap-control-contract-v2",
            "action": "sync_project_folders",
            "request_id": "req_002",
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "sync_project_folders": {},
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "taskmail-bootstrap-control-contract-v2",
            "action": "sync_project_folders",
            "fallback_policy": "mail",
        },
        "sent_at": "2026-03-23T12:30:00",
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


def _post_creation_status_packet(
    *,
    workspace_id: str,
    session_id: str,
    thread_id: str | None,
    fallback_policy: str = "mail",
) -> dict[str, object]:
    target: dict[str, object] = {
        "scope": "current_session",
        "workspace_id": workspace_id,
        "session_id": session_id,
    }
    if thread_id is not None:
        target["thread_id"] = thread_id
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:session-action:req_001",
        "client_trace_id": "req_001",
        "task_run_packet": {
            "schema_version": "post-creation-session-action-contract-v1",
            "action": "status",
            "request_id": "req_001",
            "origin": {
                "client": "android_taskmail",
            },
            "target": target,
            "status": {},
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "post-creation-session-action-contract-v1",
            "action": "status",
            "fallback_policy": fallback_policy,
        },
        "sent_at": "2026-03-22T12:30:00",
    }


def _post_creation_reply_packet(
    *,
    workspace_id: str,
    session_id: str,
    thread_id: str | None,
    reply_text: str,
    fallback_policy: str = "mail",
) -> dict[str, object]:
    packet = _post_creation_status_packet(
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        fallback_policy=fallback_policy,
    )
    packet["task_run_packet"]["action"] = "reply"
    packet["task_run_packet"]["reply"] = {"reply_text": reply_text}
    packet["task_run_packet"].pop("status", None)
    packet["dispatch_metadata"]["action"] = "reply"
    packet["sent_at"] = "2026-03-23T09:40:00"
    return packet
