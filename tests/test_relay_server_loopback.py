from __future__ import annotations

from mail_runner.models import ThreadState
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.phase3_subscription import ThreadStorePhase3SessionDetailProvider
from mail_runner.relay_server.protocol import (
    RelayErrorMessage,
    RelayHelloAckMessage,
    RelayPacketAckMessage,
    RelaySessionUpdateMessage,
    parse_server_message,
)
from mail_runner.relay_server.auth import token_fingerprint
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_thread_state


def test_loopback_server_accepts_authenticated_hello_and_packet_idempotently() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(
        config,
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        clock=lambda: "2026-03-20T14:20:00",
    )

    hello_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "pc-001",
                "client_version": "0.1.0",
                "transport_token_id": token_fingerprint("relay-secret"),
                "sent_at": "2026-03-20T14:20:00",
            },
            provided_token="relay-secret",
        )
    )
    packet_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "packet",
                "packet_id": "packet:001",
                "client_trace_id": "task_001",
                "task_run_packet": {"packet_id": "packet:001", "task_id": "task_001"},
                "dispatch_metadata": {"subject": "Demo task"},
                "sent_at": "2026-03-20T14:20:01",
            },
            connection_id="conn:pc-001",
        )
    )
    repeated_packet_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "packet",
                "packet_id": "packet:001",
                "client_trace_id": "task_001",
                "task_run_packet": {"packet_id": "packet:001", "task_id": "task_001"},
                "dispatch_metadata": {"subject": "Demo task"},
                "sent_at": "2026-03-20T14:20:02",
            },
            connection_id="conn:pc-001",
        )
    )

    assert isinstance(hello_response, RelayHelloAckMessage)
    assert hello_response.connection_id == "conn:pc-001"
    assert isinstance(packet_response, RelayPacketAckMessage)
    assert packet_response.accepted is True
    assert packet_response.receipt_id == "receipt:packet:001"
    assert isinstance(repeated_packet_response, RelayPacketAckMessage)
    assert repeated_packet_response.receipt_id == "receipt:packet:001"
    assert server.session_store.count() == 1
    assert server.packet_store.count() == 1


def test_loopback_server_rejects_invalid_transport_token() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(config, clock=lambda: "2026-03-20T14:21:00")

    response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "pc-001",
                "client_version": "0.1.0",
                "transport_token_id": "badbadbadbad",
                "sent_at": "2026-03-20T14:21:00",
            },
            provided_token="wrong-secret",
        )
    )

    assert isinstance(response, RelayErrorMessage)
    assert response.code == "unauthorized"


def test_loopback_server_accepts_phase3_subscribe_and_emits_snapshot(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    save_thread_state(thread_state, task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(
        config,
        phase3_session_detail_provider=ThreadStorePhase3SessionDetailProvider(task_root=task_root),
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        subscription_id_factory=lambda session_id: f"sub:{session_id}",
        clock=lambda: "2026-03-21T22:33:03",
    )

    hello_response = parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "android-taskmail",
                "client_version": "0.1.0",
                "transport_token_id": token_fingerprint("relay-secret"),
                "sent_at": "2026-03-21T22:33:00",
            },
            provided_token="relay-secret",
        )
    )
    responses = [
        parse_server_message(item)
        for item in server.handle_client_message_batch(
            _phase3_subscribe_packet(),
            connection_id="conn:android-taskmail",
        )
    ]
    stored_session = server.session_store.get_session("conn:android-taskmail")

    assert isinstance(hello_response, RelayHelloAckMessage)
    assert isinstance(responses[0], RelayPacketAckMessage)
    assert responses[0].accepted is True
    assert isinstance(responses[1], RelaySessionUpdateMessage)
    assert responses[1].subscription_id == "sub:session_001"
    assert responses[1].session_snapshot["status"] == "running"
    assert stored_session is not None
    assert stored_session.active_subscription_id == "sub:session_001"
    assert stored_session.last_subscription_sequence == 1


def test_loopback_server_rejects_phase3_subscribe_when_workspace_identity_is_unresolved() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(
        config,
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        clock=lambda: "2026-03-21T22:34:02",
    )
    parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "android-taskmail",
                "client_version": "0.1.0",
                "transport_token_id": token_fingerprint("relay-secret"),
                "sent_at": "2026-03-21T22:34:00",
            },
            provided_token="relay-secret",
        )
    )

    responses = [
        parse_server_message(item)
        for item in server.handle_client_message_batch(
            _phase3_subscribe_packet(
                {
                    "repo_path": "E:\\projects\\android_task_manager",
                    "session_id": "session_001",
                    "last_known_sequence": 0,
                    "reason": "detail_open",
                }
            ),
            connection_id="conn:android-taskmail",
        )
    ]

    assert len(responses) == 1
    assert isinstance(responses[0], RelayPacketAckMessage)
    assert responses[0].accepted is False
    assert responses[0].error_code == "workspace_identity_unresolved"


def test_loopback_server_collects_live_phase3_deltas_after_runtime_state_change(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    save_thread_state(thread_state, task_root)
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
    )
    server = LoopbackRelayServer(
        config,
        phase3_session_detail_provider=ThreadStorePhase3SessionDetailProvider(task_root=task_root),
        connection_id_factory=lambda client_id: f"conn:{client_id}",
        receipt_id_factory=lambda packet_id: f"receipt:{packet_id}",
        subscription_id_factory=lambda session_id: f"sub:{session_id}",
        clock=lambda: "2026-03-21T22:33:03",
    )
    parse_server_message(
        server.handle_client_message(
            {
                "message_type": "hello",
                "client_id": "android-taskmail",
                "client_version": "0.1.0",
                "transport_token_id": token_fingerprint("relay-secret"),
                "sent_at": "2026-03-21T22:33:00",
            },
            provided_token="relay-secret",
        )
    )
    subscribe_responses = server.handle_client_message_batch(
        _phase3_subscribe_packet(),
        connection_id="conn:android-taskmail",
    )
    assert len(subscribe_responses) == 2

    thread_state.status = "done"
    thread_state.last_summary = "Completed after broadcaster push."
    thread_state.last_progress_at = "2026-03-21T22:35:00"
    thread_state.updated_at = "2026-03-21T22:35:00"
    save_thread_state(thread_state, task_root)

    responses = [
        parse_server_message(item)
        for item in server.collect_subscription_updates(
            "conn:android-taskmail",
            now="2026-03-21T22:35:00",
        )
    ]
    stored_session = server.session_store.get_session("conn:android-taskmail")

    assert len(responses) == 2
    assert isinstance(responses[0], RelaySessionUpdateMessage)
    assert responses[0].session_delta["delta_type"] == "state_transition"
    assert responses[0].session_delta["state_transition"]["status"] == "done"
    assert isinstance(responses[1], RelaySessionUpdateMessage)
    assert responses[1].session_delta["delta_type"] == "timeline_append"
    assert responses[1].session_delta["timeline_items"][0]["item_type"] == "terminal_summary"
    assert stored_session is not None
    assert stored_session.last_subscription_sequence == 3


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


def _phase3_subscribe_packet(subscription: dict[str, object] | None = None) -> dict[str, object]:
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
            "subscription": subscription
            or {
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
