from __future__ import annotations

from mail_runner.relay_server.packet_store import InMemoryAcceptedPacketStore, PersistentAcceptedPacketStore


def test_packet_store_accepts_packets_idempotently() -> None:
    store = InMemoryAcceptedPacketStore()

    first = store.accept_packet(
        packet_id="packet:001",
        receipt_id="receipt:001",
        connection_id="conn-001",
        client_id="pc-001",
        client_trace_id="task_001",
        received_at="2026-03-20T14:10:00",
        task_run_packet={"packet_id": "packet:001"},
        dispatch_metadata={"subject": "Demo"},
    )
    second = store.accept_packet(
        packet_id="packet:001",
        receipt_id="receipt:002",
        connection_id="conn-001",
        client_id="pc-001",
        client_trace_id="task_001",
        received_at="2026-03-20T14:10:05",
        task_run_packet={"packet_id": "packet:001"},
        dispatch_metadata={"subject": "Demo"},
    )

    assert first.receipt_id == "receipt:001"
    assert second.receipt_id == "receipt:001"
    assert store.count() == 1


def test_persistent_packet_store_survives_restart_and_tracks_delivery(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    store = PersistentAcceptedPacketStore(state_dir)

    accepted = store.accept_packet(
        packet_id="packet:001",
        receipt_id="receipt:001",
        connection_id="conn-001",
        client_id="pc-001",
        client_trace_id="task_001",
        received_at="2026-03-20T14:10:00",
        task_run_packet={"packet_id": "packet:001"},
        dispatch_metadata={"subject": "Demo"},
    )
    store.mark_delivery_result(
        "packet:001",
        attempted_at="2026-03-20T14:10:01",
        transport_name="email",
        success=True,
        transport_message_id="<relay-sent@example.com>",
    )

    reloaded = PersistentAcceptedPacketStore(state_dir)
    reloaded_packet = reloaded.get_packet("packet:001")

    assert accepted.receipt_id == "receipt:001"
    assert reloaded_packet is not None
    assert reloaded_packet.delivery_status == "delivered"
    assert reloaded_packet.transport_message_id == "<relay-sent@example.com>"
    assert reloaded_packet.attempt_count == 1
    assert len(reloaded.list_delivery_attempts()) == 1


def test_persistent_packet_store_persists_error_code_for_failed_delivery(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    store = PersistentAcceptedPacketStore(state_dir)

    store.accept_packet(
        packet_id="packet:002",
        receipt_id="receipt:002",
        connection_id="conn-001",
        client_id="pc-001",
        client_trace_id="task_002",
        received_at="2026-03-20T14:11:00",
        task_run_packet={"packet_id": "packet:002"},
        dispatch_metadata={"subject": "Demo"},
    )
    store.mark_delivery_result(
        "packet:002",
        attempted_at="2026-03-20T14:11:01",
        transport_name="relay_direct_new_task",
        success=False,
        error_code="direct_temporarily_unavailable",
        error_message="bot mailbox address is not configured for direct TaskMail acceptance",
    )

    reloaded = PersistentAcceptedPacketStore(state_dir)
    reloaded_packet = reloaded.get_packet("packet:002")
    attempts = reloaded.list_delivery_attempts()

    assert reloaded_packet is not None
    assert reloaded_packet.delivery_status == "failed"
    assert reloaded_packet.last_error_code == "direct_temporarily_unavailable"
    assert reloaded_packet.last_error_message == "bot mailbox address is not configured for direct TaskMail acceptance"
    assert len(attempts) == 1
    assert attempts[0].error_code == "direct_temporarily_unavailable"
