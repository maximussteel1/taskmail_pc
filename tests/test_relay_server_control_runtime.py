from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import websockets

from mail_runner.config import AppConfig
from mail_runner.models import ThreadState
from mail_runner.relay_server.auth import token_fingerprint
from mail_runner.relay_server.control_protocol import (
    CONTROL_BOOTSTRAP_COMMAND_TYPE,
    CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
    CONTROL_POST_CREATION_PAYLOAD_SCHEMA,
    CONTROL_POST_CREATION_REPLY_COMMAND_TYPE,
    CONTROL_POST_CREATION_STATUS_COMMAND_TYPE,
    CONTROL_SESSION_ACTION_RESULT_TYPE,
    CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
    CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
    ControlCommandAckMessage,
    ControlEventMessage,
    ControlHelloAckMessage,
    ControlPongMessage,
    ControlResultMessage,
    parse_control_server_message,
)
from mail_runner.relay_server.direct_actions import RelayTaskMailDirectProjectSyncHandler
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.session_store import PersistentSessionStore
from mail_runner.relay_server.app import build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.protocol import RelayErrorMessage
from mail_runner.relay_server.transport_probe import RelayTaskMailTransportProbeHandler
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_thread_state
from mail_runner.transport_probe_mail import (
    TRANSPORT_PROBE_MAIL_HEADER,
    TRANSPORT_PROBE_MAIL_HEADER_VALUE,
    TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION,
    TRANSPORT_PROBE_OBSERVATION_SURFACE,
    TRANSPORT_PROBE_PACKET_ID_HEADER,
    TRANSPORT_PROBE_REQUEST_ID_HEADER,
    TRANSPORT_PROBE_ID_HEADER,
    TRANSPORT_PROBE_TRACE_ID_HEADER,
)


def test_control_runtime_exposes_bootstrap_v2_ack_result_replay_and_ping(tmp_path) -> None:
    asyncio.run(_run_control_bootstrap_runtime_test(tmp_path))


def test_control_runtime_rejects_unsupported_payload_schema(tmp_path) -> None:
    asyncio.run(_run_control_unsupported_schema_runtime_test(tmp_path))


def test_control_runtime_exposes_transport_probe_event_result_and_replay(tmp_path) -> None:
    asyncio.run(_run_control_transport_probe_runtime_test(tmp_path))


def test_control_runtime_exposes_current_session_status_result_and_replay(tmp_path) -> None:
    asyncio.run(_run_control_post_creation_status_runtime_test(tmp_path))


def test_control_runtime_exposes_current_session_reply_result_and_replay(tmp_path) -> None:
    asyncio.run(_run_control_post_creation_reply_runtime_test(tmp_path))


def test_control_runtime_in_vps_only_mode_only_exposes_bootstrap_v2_and_rejects_mail_bridge_commands(
    tmp_path,
    monkeypatch,
) -> None:
    sync_root = tmp_path / "sync_root"
    monkeypatch.setattr(
        "mail_runner.relay_server.app.load_config",
        lambda: AppConfig(control_plane_mode="vps_only", project_sync_roots=[str(sync_root)]),
    )
    asyncio.run(_run_control_vps_only_active_mode_boundary_runtime_test(tmp_path, sync_root=sync_root))


class _FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return f"<transport-probe-{len(self.sent_messages)}@example.com>"


@dataclass
class _FakeMonotonic:
    value: float

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


async def _run_control_vps_only_active_mode_boundary_runtime_test(tmp_path, *, sync_root) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    (task_root / "_scheduler").mkdir(parents=True)
    (sync_root / "alpha").mkdir(parents=True)

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
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    _control_hello_payload(
                        supported_payload_schemas=[
                            CONTROL_POST_CREATION_PAYLOAD_SCHEMA,
                            CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
                            CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
                        ]
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, ControlHelloAckMessage)
            assert hello_ack.accepted_payload_schemas == [CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA]

            bootstrap_payload = _control_sync_project_folders_command(
                packet_id="android-control:sync-project-folders:req_vps_only_bootstrap",
                request_id="req_vps_only_bootstrap",
            )
            await websocket.send(json.dumps(bootstrap_payload, ensure_ascii=False))
            bootstrap_ack = parse_control_server_message(json.loads(await websocket.recv()))
            bootstrap_result = parse_control_server_message(json.loads(await websocket.recv()))
            bootstrap_packet = packet_store.get_packet("android-control:sync-project-folders:req_vps_only_bootstrap")

            assert isinstance(bootstrap_ack, ControlCommandAckMessage)
            assert bootstrap_ack.accepted is True
            assert isinstance(bootstrap_result, ControlResultMessage)
            assert bootstrap_result.result_type == "sync_project_folders_result"
            assert bootstrap_result.payload["sync_project_folders_result"]["roots"][0]["entries"] == [
                {
                    "name": "alpha",
                    "path": str(sync_root / "alpha"),
                }
            ]
            assert bootstrap_packet is not None
            assert bootstrap_packet.delivery_status == "delivered"

            await websocket.send(
                json.dumps(
                    _control_post_creation_status_command(
                        workspace_id="workspace_001",
                        session_id="session_001",
                        thread_id="thread_001",
                    ),
                    ensure_ascii=False,
                )
            )
            status_error = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(status_error, RelayErrorMessage)
            assert status_error.code == "unsupported_action"
            assert packet_store.get_packet("android-control:session-action:req_status_001") is None

            await websocket.send(
                json.dumps(
                    _control_post_creation_reply_command(
                        workspace_id="workspace_001",
                        session_id="session_001",
                        thread_id="thread_001",
                    ),
                    ensure_ascii=False,
                )
            )
            reply_error = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(reply_error, RelayErrorMessage)
            assert reply_error.code == "unsupported_action"
            assert packet_store.get_packet("android-control:session-action:req_reply_001") is None

            await websocket.send(json.dumps(_control_transport_probe_command(), ensure_ascii=False))
            probe_error = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(probe_error, RelayErrorMessage)
            assert probe_error.code == "unsupported_action"
            assert packet_store.get_packet("android-control:transport-probe:probe_req_001") is None
    finally:
        server.close()
        await server.wait_closed()


async def _run_control_bootstrap_runtime_test(tmp_path) -> None:
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
            f"ws://{host}:{port}/control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    _control_hello_payload(
                        supported_payload_schemas=["future-schema", CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA]
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, ControlHelloAckMessage)
            assert hello_ack.transport_token_id == token_fingerprint("relay-secret")
            assert hello_ack.accepted_payload_schemas == [CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA]

            await websocket.send(json.dumps(_control_ping_payload(), ensure_ascii=False))
            pong = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(pong, ControlPongMessage)

            command_payload = _control_sync_project_folders_command()
            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            command_ack = parse_control_server_message(json.loads(await websocket.recv()))
            result = parse_control_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-control:sync-project-folders:req_002")

            assert isinstance(command_ack, ControlCommandAckMessage)
            assert command_ack.accepted is True
            assert isinstance(result, ControlResultMessage)
            assert result.result_type == "sync_project_folders_result"
            assert result.receipt_id == command_ack.receipt_id
            assert result.payload["sync_project_folders_result"]["roots"][0]["entries"] == [
                {
                    "name": "alpha",
                    "path": str(sync_root / "alpha"),
                }
            ]
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert stored_packet.dispatch_metadata["control_trace"] == {
                "trace_id": "trace-002",
                "probe_id": "probe-002",
            }
            assert stored_packet.dispatch_metadata["control_related"] == {
                "ui_surface": "bootstrap_sheet",
            }

            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            replay_ack = parse_control_server_message(json.loads(await websocket.recv()))
            replay_result = parse_control_server_message(json.loads(await websocket.recv()))

            assert isinstance(replay_ack, ControlCommandAckMessage)
            assert isinstance(replay_result, ControlResultMessage)
            assert replay_ack.receipt_id == command_ack.receipt_id
            assert replay_result.result_id == result.result_id
            assert replay_result.payload == result.payload
    finally:
        server.close()
        await server.wait_closed()


async def _run_control_unsupported_schema_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    sync_root.mkdir(parents=True)

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
            f"ws://{host}:{port}/control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(json.dumps(_control_hello_payload(), ensure_ascii=False))
            hello_ack = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, ControlHelloAckMessage)

            await websocket.send(
                json.dumps(
                    _control_sync_project_folders_command(
                        packet_id="android-control:sync-project-folders:req_unsupported",
                        request_id="req_unsupported",
                        payload_schema="future-schema",
                    ),
                    ensure_ascii=False,
                )
            )
            error = parse_control_server_message(json.loads(await websocket.recv()))

            assert isinstance(error, RelayErrorMessage)
            assert error.code == "unsupported_action"
            assert packet_store.get_packet("android-control:sync-project-folders:req_unsupported") is None
    finally:
        server.close()
        await server.wait_closed()


async def _run_control_transport_probe_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    (sync_root / "alpha").mkdir(parents=True)
    fake_mail_client = _FakeMailClient()
    monotonic = _FakeMonotonic(100.0)

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        task_root=str(tmp_path / "shared_task_root"),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        direct_packet_handlers=[
            RelayTaskMailTransportProbeHandler(
                config,
                mail_client=fake_mail_client,
                clock=lambda: "2026-03-24T10:00:00",
                monotonic_fn=monotonic,
                sleep_fn=monotonic.sleep,
                observation_loader=lambda _probe_id: _transport_probe_observation(
                    transport_message_id="<transport-probe-1@example.com>",
                ),
            ),
            RelayTaskMailDirectProjectSyncHandler(
                config=AppConfig(project_sync_roots=[str(sync_root)]),
                clock=lambda: "2026-03-24T10:00:00",
            ),
        ],
        clock=lambda: "2026-03-24T10:00:00",
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
            f"ws://{host}:{port}/control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    _control_hello_payload(
                        supported_payload_schemas=[
                            CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
                            CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
                        ]
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, ControlHelloAckMessage)
            assert hello_ack.accepted_payload_schemas == [
                CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
                CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
            ]

            command_payload = _control_transport_probe_command()
            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            command_ack = parse_control_server_message(json.loads(await websocket.recv()))
            messages = [parse_control_server_message(json.loads(await websocket.recv())) for _ in range(9)]
            stored_packet = packet_store.get_packet("android-control:transport-probe:probe_req_001")

            assert isinstance(command_ack, ControlCommandAckMessage)
            assert command_ack.accepted is True
            assert command_ack.transport_message_id == "<transport-probe-1@example.com>"
            assert [item.event_type for item in messages[:-1]] == [
                "vps_probe_packet_received",
                "vps_probe_packet_accepted",
                "vps_probe_bridge_started",
                "vps_probe_bridge_finished",
                "vps_probe_observation_started",
                "vps_probe_observation_observed",
                "vps_probe_result_started",
                "vps_probe_result_finished",
            ]
            assert all(isinstance(item, ControlEventMessage) for item in messages[:-1])
            result = messages[-1]
            assert isinstance(result, ControlResultMessage)
            assert result.result_type == "transport_probe_result"
            assert result.status == "completed"
            assert result.payload["outcome"] == "observed"
            assert result.receipt_id == command_ack.receipt_id
            assert result.payload["delivery"]["transport_message_id"] == "<transport-probe-1@example.com>"
            assert result.payload["observation"]["status"] == "observed"
            assert result.payload["observation_scope"] == "pc_mailbox_ingress"
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert stored_packet.attempt_count == 1
            assert fake_mail_client.sent_messages[0]["subject"] == "[TPROBE][A2P][MAIL] probe-transport-001"
            assert fake_mail_client.sent_messages[0]["headers"]["X-TaskMail-Transport-Probe"] == "1"

            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            replay_ack = parse_control_server_message(json.loads(await websocket.recv()))
            replay_messages = [parse_control_server_message(json.loads(await websocket.recv())) for _ in range(9)]

            assert isinstance(replay_ack, ControlCommandAckMessage)
            assert replay_ack.receipt_id == command_ack.receipt_id
            assert [item.event_id for item in replay_messages[:-1]] == [item.event_id for item in messages[:-1]]
            replay_result = replay_messages[-1]
            assert isinstance(replay_result, ControlResultMessage)
            assert replay_result.result_id == result.result_id
            assert replay_result.payload == result.payload
            assert len(fake_mail_client.sent_messages) == 1
    finally:
        server.close()
        await server.wait_closed()


async def _run_control_post_creation_status_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    thread_state.status = "done"
    thread_state.last_summary = "Completed."
    thread_state.updated_at = "2026-03-24T12:00:00"
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
    relay._direct_packet_handlers[1]._mail_client = fake_mail_client
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    _control_hello_payload(
                        supported_payload_schemas=[CONTROL_POST_CREATION_PAYLOAD_SCHEMA]
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, ControlHelloAckMessage)
            assert hello_ack.accepted_payload_schemas == [CONTROL_POST_CREATION_PAYLOAD_SCHEMA]

            command_payload = _control_post_creation_status_command(
                workspace_id=thread_state.workspace_id,
                session_id=thread_state.session_id or thread_state.thread_id,
                thread_id=thread_state.thread_id,
            )
            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            command_ack = parse_control_server_message(json.loads(await websocket.recv()))
            result = parse_control_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-control:session-action:req_status_001")

            assert isinstance(command_ack, ControlCommandAckMessage)
            assert command_ack.accepted is True
            assert command_ack.transport_message_id == "<transport-probe-1@example.com>"
            assert isinstance(result, ControlResultMessage)
            assert result.result_type == CONTROL_SESSION_ACTION_RESULT_TYPE
            assert result.status == "completed"
            assert result.receipt_id == command_ack.receipt_id
            assert result.related == {
                "ui_surface": "session_sheet",
                "trace_id": "trace-status-001",
                "request_id": "req_status_001",
                "packet_id": "android-control:session-action:req_status_001",
                "receipt_id": command_ack.receipt_id,
                "result_id": "session-action-result:req_status_001",
            }
            assert result.payload["session_action_result"] == {
                "action_type": "status",
                "result_scope": "mail_ingress_submission",
                "canonical_outcome_via": "mail",
                "delivery_status": "submitted",
                "submitted_at": result.sent_at,
                "transport_message_id": "<transport-probe-1@example.com>",
                "session_action_closeout": {
                    "action_type": "status",
                    "target_session_identity": {
                        "workspace_id": thread_state.workspace_id,
                        "session_id": thread_state.session_id,
                        "thread_id": thread_state.thread_id,
                    },
                    "ingress_type": "direct_bridge",
                    "request_id": "req_status_001",
                    "ingress_message_id": "<transport-probe-1@example.com>",
                    "packet_id": "android-control:session-action:req_status_001",
                    "receipt_id": command_ack.receipt_id,
                    "last_summary": "Completed.",
                    "terminal_mail_message_id": None,
                    "terminal_mail_subject": "[STATUS][S:session_001] Phase 3 detail bridge",
                },
            }
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert len(stored_packet.server_messages) == 1
            assert fake_mail_client.sent_messages[0]["subject"] == "Re: [S:session_001] Phase 3 detail bridge"

            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            replay_ack = parse_control_server_message(json.loads(await websocket.recv()))
            replay_result = parse_control_server_message(json.loads(await websocket.recv()))

            assert isinstance(replay_ack, ControlCommandAckMessage)
            assert replay_ack.receipt_id == command_ack.receipt_id
            assert isinstance(replay_result, ControlResultMessage)
            assert replay_result.result_id == result.result_id
            assert replay_result.payload == result.payload
            assert len(fake_mail_client.sent_messages) == 1
    finally:
        server.close()
        await server.wait_closed()


async def _run_control_post_creation_reply_runtime_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    thread_state.status = "done"
    thread_state.last_summary = "Completed."
    thread_state.updated_at = "2026-03-24T12:01:00"
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
    relay._direct_packet_handlers[2]._mail_client = fake_mail_client
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        async with websockets.connect(
            f"ws://{host}:{port}/control",
            open_timeout=15,
            close_timeout=15,
            extra_headers={"Authorization": "Bearer relay-secret"},
            max_size=32 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    _control_hello_payload(
                        supported_payload_schemas=[CONTROL_POST_CREATION_PAYLOAD_SCHEMA]
                    ),
                    ensure_ascii=False,
                )
            )
            hello_ack = parse_control_server_message(json.loads(await websocket.recv()))
            assert isinstance(hello_ack, ControlHelloAckMessage)
            assert hello_ack.accepted_payload_schemas == [CONTROL_POST_CREATION_PAYLOAD_SCHEMA]

            command_payload = _control_post_creation_reply_command(
                workspace_id=thread_state.workspace_id,
                session_id=thread_state.session_id or thread_state.thread_id,
                thread_id=thread_state.thread_id,
            )
            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            command_ack = parse_control_server_message(json.loads(await websocket.recv()))
            result = parse_control_server_message(json.loads(await websocket.recv()))
            stored_packet = packet_store.get_packet("android-control:session-action:req_reply_001")

            assert isinstance(command_ack, ControlCommandAckMessage)
            assert command_ack.accepted is True
            assert command_ack.transport_message_id == "<transport-probe-1@example.com>"
            assert isinstance(result, ControlResultMessage)
            assert result.result_type == CONTROL_SESSION_ACTION_RESULT_TYPE
            assert result.status == "completed"
            assert result.receipt_id == command_ack.receipt_id
            assert result.related == {
                "ui_surface": "session_sheet",
                "trace_id": "trace-reply-001",
                "request_id": "req_reply_001",
                "packet_id": "android-control:session-action:req_reply_001",
                "receipt_id": command_ack.receipt_id,
                "result_id": "session-action-result:req_reply_001",
            }
            assert result.payload["session_action_result"] == {
                "action_type": "reply",
                "result_scope": "mail_ingress_submission",
                "canonical_outcome_via": "mail",
                "delivery_status": "submitted",
                "submitted_at": result.sent_at,
                "transport_message_id": "<transport-probe-1@example.com>",
                "session_action_closeout": {
                    "action_type": "reply",
                    "target_session_identity": {
                        "workspace_id": thread_state.workspace_id,
                        "session_id": thread_state.session_id,
                        "thread_id": thread_state.thread_id,
                    },
                    "ingress_type": "direct_bridge",
                    "request_id": "req_reply_001",
                    "ingress_message_id": "<transport-probe-1@example.com>",
                    "packet_id": "android-control:session-action:req_reply_001",
                    "receipt_id": command_ack.receipt_id,
                    "last_summary": "Completed.",
                    "terminal_mail_message_id": None,
                    "terminal_mail_subject": None,
                },
            }
            assert stored_packet is not None
            assert stored_packet.delivery_status == "delivered"
            assert len(stored_packet.server_messages) == 1
            assert fake_mail_client.sent_messages[0]["subject"] == "Re: [S:session_001] Phase 3 detail bridge"
            assert "Please continue with the cleanup." in fake_mail_client.sent_messages[0]["body"]
            assert fake_mail_client.sent_messages[0]["in_reply_to"] == "<latest@example.com>"

            await websocket.send(json.dumps(command_payload, ensure_ascii=False))
            replay_ack = parse_control_server_message(json.loads(await websocket.recv()))
            replay_result = parse_control_server_message(json.loads(await websocket.recv()))

            assert isinstance(replay_ack, ControlCommandAckMessage)
            assert replay_ack.receipt_id == command_ack.receipt_id
            assert isinstance(replay_result, ControlResultMessage)
            assert replay_result.result_id == result.result_id
            assert replay_result.payload == result.payload
            assert len(fake_mail_client.sent_messages) == 1
    finally:
        server.close()
        await server.wait_closed()


def _control_hello_payload(*, supported_payload_schemas: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "message_type": "hello",
        "client_id": "android-control",
        "client_version": "0.1.0",
        "transport_token_id": token_fingerprint("relay-secret"),
        "sent_at": "2026-03-23T12:30:00",
    }
    if supported_payload_schemas is not None:
        payload["supported_payload_schemas"] = supported_payload_schemas
    return payload


def _control_ping_payload() -> dict[str, object]:
    return {
        "message_type": "ping",
        "sent_at": "2026-03-23T12:30:00",
    }


def _control_sync_project_folders_command(
    *,
    packet_id: str = "android-control:sync-project-folders:req_002",
    request_id: str = "req_002",
    payload_schema: str = CONTROL_BOOTSTRAP_PAYLOAD_SCHEMA,
) -> dict[str, object]:
    return {
        "message_type": "command",
        "request_id": request_id,
        "packet_id": packet_id,
        "command_type": CONTROL_BOOTSTRAP_COMMAND_TYPE,
        "payload_schema": payload_schema,
        "trace": {
            "trace_id": "trace-002",
            "probe_id": "probe-002",
        },
        "payload": {
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "sync_project_folders": {},
        },
        "related": {
            "ui_surface": "bootstrap_sheet",
        },
        "sent_at": "2026-03-23T12:30:00",
    }


def _control_transport_probe_command() -> dict[str, object]:
    return {
        "message_type": "command",
        "request_id": "probe_req_001",
        "packet_id": "android-control:transport-probe:probe_req_001",
        "command_type": CONTROL_TRANSPORT_PROBE_COMMAND_TYPE,
        "payload_schema": CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
        "trace": {
            "trace_id": "trace-transport-001",
            "probe_id": "probe-transport-001",
        },
        "payload": {
            "probe_id": "probe-transport-001",
            "scenario": "android_direct_ping_to_vps_to_pc",
            "direction": "android_to_pc",
            "transport_kind": "mail",
            "payload_text": "PING transport probe",
            "timeout_seconds": 180,
        },
        "related": {
            "ui_surface": "transport_probe_sheet",
        },
        "sent_at": "2026-03-24T10:00:00",
    }


def _control_post_creation_status_command(
    *,
    workspace_id: str,
    session_id: str,
    thread_id: str | None,
) -> dict[str, object]:
    target: dict[str, object] = {
        "scope": "current_session",
        "workspace_id": workspace_id,
        "session_id": session_id,
    }
    if thread_id is not None:
        target["thread_id"] = thread_id
    return {
        "message_type": "command",
        "request_id": "req_status_001",
        "packet_id": "android-control:session-action:req_status_001",
        "command_type": CONTROL_POST_CREATION_STATUS_COMMAND_TYPE,
        "payload_schema": CONTROL_POST_CREATION_PAYLOAD_SCHEMA,
        "trace": {
            "trace_id": "trace-status-001",
        },
        "payload": {
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "target": target,
            "status": {},
        },
        "related": {
            "ui_surface": "session_sheet",
        },
        "sent_at": "2026-03-24T12:00:00",
    }


def _control_post_creation_reply_command(
    *,
    workspace_id: str,
    session_id: str,
    thread_id: str | None,
) -> dict[str, object]:
    packet = _control_post_creation_status_command(
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    packet["request_id"] = "req_reply_001"
    packet["packet_id"] = "android-control:session-action:req_reply_001"
    packet["command_type"] = CONTROL_POST_CREATION_REPLY_COMMAND_TYPE
    packet["trace"] = {
        "trace_id": "trace-reply-001",
    }
    packet["payload"]["reply"] = {
        "reply_text": "Please continue with the cleanup.",
    }
    packet["payload"].pop("status", None)
    packet["sent_at"] = "2026-03-24T12:01:00"
    return packet


def _transport_probe_observation(*, transport_message_id: str) -> dict[str, object]:
    return {
        "schema_version": TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION,
        "probe_id": "probe-transport-001",
        "request_id": "probe_req_001",
        "packet_id": "android-control:transport-probe:probe_req_001",
        "trace_id": "trace-transport-001",
        "status": "observed",
        "observation_scope": TRANSPORT_PROBE_OBSERVATION_SURFACE,
        "first_observed_at": "2026-03-24T10:00:01",
        "last_observed_at": "2026-03-24T10:00:01",
        "seen_count": 1,
        "observed_message_ids": [transport_message_id],
        "delivery": {
            "transport_message_id": transport_message_id,
            "subject": "[TPROBE][A2P][MAIL] probe-transport-001",
            "from_addr": "taskmail-user@example.com",
            "to_addr": "bot@example.com",
            "mail_date": "2026-03-24T10:00:01",
        },
        "probe_mail": {
            "schema_version": CONTROL_TRANSPORT_PROBE_PAYLOAD_SCHEMA,
            "scenario": "android_direct_ping_to_vps_to_pc",
            "direction": "android_to_pc",
            "transport_kind": "mail",
            "payload_text": "PING transport probe",
            "timeout_seconds": 180,
            "body_text": (
                "Probe-Version: taskmail-transport-probe-payload-v1\n"
                "Probe-Id: probe-transport-001\n"
                "Scenario: android_direct_ping_to_vps_to_pc\n"
                "Direction: android_to_pc\n"
                "Transport-Kind: mail\n"
                "Timeout-Seconds: 180\n"
                "Payload-Text: PING transport probe\n"
            ),
        },
        "headers": {
            TRANSPORT_PROBE_MAIL_HEADER: TRANSPORT_PROBE_MAIL_HEADER_VALUE,
            TRANSPORT_PROBE_ID_HEADER: "probe-transport-001",
            TRANSPORT_PROBE_REQUEST_ID_HEADER: "probe_req_001",
            TRANSPORT_PROBE_PACKET_ID_HEADER: "android-control:transport-probe:probe_req_001",
            TRANSPORT_PROBE_TRACE_ID_HEADER: "trace-transport-001",
        },
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
        last_active_at="2026-03-24T11:59:00",
        last_progress_at="2026-03-24T11:59:00",
        workspace_id=build_workspace_id(repo_path, workdir),
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id="session_001",
        session_name="Phase 3 detail bridge",
        session_norm="phase 3 detail bridge",
        created_at="2026-03-24T11:55:00",
        updated_at="2026-03-24T11:59:00",
    )
