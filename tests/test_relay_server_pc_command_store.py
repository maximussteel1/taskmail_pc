from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_command_store import (
    PcArtifactManifestRecord,
    InMemoryPcCommandStore,
    PcCommandConflictError,
    PcCommandEventRecord,
    PcOutputChunkRecord,
    PcCommandRecord,
    PcCommandResultRecord,
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


def test_pc_command_store_event_and_result_are_idempotent_across_transport_retries() -> None:
    store = InMemoryPcCommandStore()
    store.upsert_dispatch(_record("cmd_001"))

    first_event, created_event = store.record_event(
        pc_id="pc_home",
        command_id="cmd_001",
        event=PcCommandEventRecord(
            event_id="event:cmd_001:running",
            event_type="running",
            event_message_id="msg_evt_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:10",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            payload={"thread_id": "thread_cmd_001"},
        ),
    )
    second_event, created_event_retry = store.record_event(
        pc_id="pc_home",
        command_id="cmd_001",
        event=PcCommandEventRecord(
            event_id="event:cmd_001:running",
            event_type="running",
            event_message_id="msg_evt_002",
            trace_id="trace:cmd_001",
            connection_epoch=2,
            sent_at="2026-03-25T10:01:10",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            payload={"thread_id": "thread_cmd_001"},
        ),
    )
    first_result, created_result = store.record_result(
        pc_id="pc_home",
        command_id="cmd_001",
        result=PcCommandResultRecord(
            result_id="result:cmd_001",
            result_message_id="msg_res_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:20",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={"kind": "run_result", "task_id": "task_cmd_001"},
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        ),
    )
    second_result, created_result_retry = store.record_result(
        pc_id="pc_home",
        command_id="cmd_001",
        result=PcCommandResultRecord(
            result_id="result:cmd_001",
            result_message_id="msg_res_002",
            trace_id="trace:cmd_001",
            connection_epoch=2,
            sent_at="2026-03-25T10:01:20",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={"kind": "run_result", "task_id": "task_cmd_001"},
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        ),
    )

    assert created_event is True
    assert created_event_retry is False
    assert len(first_event.events) == 1
    assert len(second_event.events) == 1
    assert first_event.events[0].event_message_id == "msg_evt_001"
    assert created_result is True
    assert created_result_retry is False
    assert first_result.final_status == "done"
    assert second_result.result is not None
    assert second_result.result.result_message_id == "msg_res_001"


def test_pc_command_store_rejects_conflicting_event_or_result_for_same_identity() -> None:
    store = InMemoryPcCommandStore()
    store.upsert_dispatch(_record("cmd_001"))
    store.record_event(
        pc_id="pc_home",
        command_id="cmd_001",
        event=PcCommandEventRecord(
            event_id="event:cmd_001:running",
            event_type="running",
            event_message_id="msg_evt_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:10",
            summary="command is running on the local runner",
            payload={"thread_id": "thread_cmd_001"},
        ),
    )
    store.record_result(
        pc_id="pc_home",
        command_id="cmd_001",
        result=PcCommandResultRecord(
            result_id="result:cmd_001",
            result_message_id="msg_res_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:20",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={"kind": "run_result", "task_id": "task_cmd_001"},
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        ),
    )

    with pytest.raises(PcCommandConflictError, match="event_id does not match"):
        store.record_event(
            pc_id="pc_home",
            command_id="cmd_001",
            event=PcCommandEventRecord(
                event_id="event:cmd_001:running",
                event_type="running",
                event_message_id="msg_evt_002",
                trace_id="trace:cmd_001",
                connection_epoch=1,
                sent_at="2026-03-25T10:00:11",
                summary="command is doing something else",
                payload={"thread_id": "thread_cmd_001"},
            ),
        )

    with pytest.raises(PcCommandConflictError, match="existing canonical result"):
        store.record_result(
            pc_id="pc_home",
            command_id="cmd_001",
            result=PcCommandResultRecord(
                result_id="result:cmd_001",
                result_message_id="msg_res_002",
                trace_id="trace:cmd_001",
                connection_epoch=1,
                sent_at="2026-03-25T10:00:21",
                final_status="failed",
                summary="Mock run failed.",
                structured_payload={"kind": "run_result", "task_id": "task_cmd_001"},
                effective_execution={
                    "backend": "codex",
                    "profile": "strong",
                    "permission": "highest",
                    "backend_transport": "sdk",
                    "resolved_model": "gpt-5-codex",
                },
            ),
        )


def test_pc_command_store_output_chunks_are_idempotent_by_stream_id_and_seq() -> None:
    store = InMemoryPcCommandStore()
    store.upsert_dispatch(_record("cmd_001"))

    first_record, created_first = store.record_output_chunk(
        pc_id="pc_home",
        command_id="cmd_001",
        chunk=PcOutputChunkRecord(
            output_chunk_id="output:cmd_001:thread_cmd_001:1",
            output_message_id="msg_out_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:12",
            stream_id="thread_cmd_001:task_cmd_001",
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        ),
    )
    second_record, created_second = store.record_output_chunk(
        pc_id="pc_home",
        command_id="cmd_001",
        chunk=PcOutputChunkRecord(
            output_chunk_id="output:cmd_001:thread_cmd_001:1",
            output_message_id="msg_out_002",
            trace_id="trace:cmd_001",
            connection_epoch=2,
            sent_at="2026-03-25T10:01:12",
            stream_id="thread_cmd_001:task_cmd_001",
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        ),
    )

    assert created_first is True
    assert created_second is False
    assert len(first_record.output_chunks) == 1
    assert len(second_record.output_chunks) == 1
    assert second_record.output_chunks[0].output_message_id == "msg_out_001"


def test_pc_command_store_rejects_conflicting_output_chunk_or_artifact_manifest() -> None:
    store = InMemoryPcCommandStore()
    store.upsert_dispatch(_record("cmd_001"))
    store.record_output_chunk(
        pc_id="pc_home",
        command_id="cmd_001",
        chunk=PcOutputChunkRecord(
            output_chunk_id="output:cmd_001:thread_cmd_001:1",
            output_message_id="msg_out_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:12",
            stream_id="thread_cmd_001:task_cmd_001",
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        ),
    )
    store.record_artifact_manifest(
        pc_id="pc_home",
        command_id="cmd_001",
        manifest=PcArtifactManifestRecord(
            manifest_id="artifact_manifest:cmd_001",
            manifest_message_id="msg_art_001",
            trace_id="trace:cmd_001",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:20",
            artifacts_root="runs/task_cmd_001/artifacts",
            source="manifest",
            artifacts=[
                {
                    "artifact_id": "artifact-preview",
                    "kind": "image",
                    "name": "preview.png",
                    "content_type": "image/png",
                    "size": 8,
                    "download_ref": None,
                    "download_ref_source": None,
                }
            ],
        ),
    )

    with pytest.raises(PcCommandConflictError, match="existing stream_id/seq"):
        store.record_output_chunk(
            pc_id="pc_home",
            command_id="cmd_001",
            chunk=PcOutputChunkRecord(
                output_chunk_id="output:cmd_001:thread_cmd_001:1",
                output_message_id="msg_out_002",
                trace_id="trace:cmd_001",
                connection_epoch=1,
                sent_at="2026-03-25T10:00:13",
                stream_id="thread_cmd_001:task_cmd_001",
                stream_id_source="derived_from_run_identity",
                seq=1,
                kind="assistant.delta",
                delta="Different",
                status="streaming",
            ),
        )

    with pytest.raises(PcCommandConflictError, match="existing canonical manifest"):
        store.record_artifact_manifest(
            pc_id="pc_home",
            command_id="cmd_001",
            manifest=PcArtifactManifestRecord(
                manifest_id="artifact_manifest:cmd_001",
                manifest_message_id="msg_art_002",
                trace_id="trace:cmd_001",
                connection_epoch=1,
                sent_at="2026-03-25T10:00:21",
                artifacts_root="runs/task_cmd_001/artifacts",
                source="manifest",
                artifacts=[
                    {
                        "artifact_id": "artifact-preview",
                        "kind": "image",
                        "name": "preview.png",
                        "content_type": "image/png",
                        "size": 16,
                        "download_ref": None,
                        "download_ref_source": None,
                    }
                ],
            ),
        )
