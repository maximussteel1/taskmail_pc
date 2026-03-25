from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_command_store import (
    InMemoryPcCommandStore,
    PcCommandConflictError,
    PcCommandRecord,
)


def _record(command_id: str, *, task_text: str = "Refactor floor_shear.py") -> PcCommandRecord:
    return PcCommandRecord(
        pc_id="pc_home",
        workspace_id="workspace_001",
        command_id=command_id,
        command_type="new_task",
        session_id=None,
        trace_id=f"trace:{command_id}",
        dispatch_message_id=f"msg:{command_id}",
        created_at="2026-03-25T10:00:00",
        execution_policy={
            "backend": "codex",
            "profile": "strong",
            "permission": "highest",
            "backend_transport": "sdk",
        },
        command_payload={"task_text": task_text},
    )


def test_pc_command_store_upsert_is_idempotent_for_same_command_id() -> None:
    store = InMemoryPcCommandStore()

    first, created_first = store.upsert_dispatch(_record("cmd_001"))
    second, created_second = store.upsert_dispatch(_record("cmd_001"))

    assert created_first is True
    assert created_second is False
    assert first.command_id == second.command_id == "cmd_001"
    assert store.count() == 1


def test_pc_command_store_rejects_conflicting_payload_for_same_command_id() -> None:
    store = InMemoryPcCommandStore()
    store.upsert_dispatch(_record("cmd_001"))

    with pytest.raises(PcCommandConflictError, match="command_id already exists"):
        store.upsert_dispatch(_record("cmd_001", task_text="Do something else"))


def test_pc_command_store_records_ack_and_stops_redelivery_for_same_epoch() -> None:
    store = InMemoryPcCommandStore()
    store.upsert_dispatch(_record("cmd_001"))

    first_batch = store.collect_pending_dispatches(
        pc_id="pc_home",
        connection_epoch=1,
        dispatched_at="2026-03-25T10:00:01",
    )
    second_batch = store.collect_pending_dispatches(
        pc_id="pc_home",
        connection_epoch=1,
        dispatched_at="2026-03-25T10:00:02",
    )
    store.record_ack(
        pc_id="pc_home",
        command_id="cmd_001",
        ack_status="accepted",
        ack_message_id="msg_ack_001",
        acked_at="2026-03-25T10:00:03",
    )
    third_batch = store.collect_pending_dispatches(
        pc_id="pc_home",
        connection_epoch=2,
        dispatched_at="2026-03-25T10:00:04",
    )

    assert len(first_batch) == 1
    assert second_batch == []
    assert third_batch == []
    assert store.get_command("pc_home", "cmd_001").ack_status == "accepted"
