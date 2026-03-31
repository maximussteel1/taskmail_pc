from __future__ import annotations

import pytest

from mail_runner.relay_server.projection_store import (
    ProjectionAttachmentUpsert,
    ProjectionCloseoutUpsert,
    ProjectionProbeObservationUpsert,
    ProjectionRoundUpsert,
    ProjectionSessionBatch,
    ProjectionSessionUpsert,
    ProjectionStoreConflictError,
    RelayProjectionStore,
)


def _session_upsert(
    *,
    version: int,
    batch_id: str,
    session_idempotency_key: str,
    list_status: str,
    snapshot_status: str,
    last_summary: str,
    pending_task_count: int,
    updated_at: str,
    current_task_id: str = "task_001",
    queued_task_id: str | None = None,
) -> ProjectionSessionUpsert:
    return ProjectionSessionUpsert(
        idempotency_key=session_idempotency_key,
        projection_version=version,
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
        session_name="Alpha session",
        backend="codex",
        backend_transport="sdk",
        profile="default",
        permission="default",
        repo_path="E:\\projects\\alpha",
        workdir="main",
        list_status=list_status,
        snapshot_status=snapshot_status,
        lifecycle="active",
        current_task_id=current_task_id,
        queued_task_id=queued_task_id,
        pending_task_count=pending_task_count,
        last_summary=last_summary,
        last_active_at=updated_at,
        last_progress_at=updated_at,
        paused_from_status=None,
        backend_session_id="backend-session-alpha",
        backend_session_resumable=True,
        question_state=None,
        timeline_items=[],
        created_at="2026-03-27T10:00:00",
        updated_at=updated_at,
        source_updated_at=updated_at,
    )


def _round_upsert(
    *,
    version: int,
    task_id: str,
    round_id: str,
    round_sort_at: str,
    created_at: str,
    status: str,
    input_text: str | None,
    result_text: str | None,
    input_attachment_specs: list[tuple[int, str]],
    result_attachment_specs: list[tuple[int, str]],
) -> ProjectionRoundUpsert:
    return ProjectionRoundUpsert(
        idempotency_key=f"round:{round_id}:v{version}",
        round_id=round_id,
        task_id=task_id,
        round_sort_at=round_sort_at,
        created_at=created_at,
        status=status,
        speaker_label="TaskMail",
        input_text=input_text,
        process_items=[
            {
                "item_id": f"process:{round_id}",
                "kind": "system",
                "created_at": created_at,
                "updated_at": created_at,
                "status": "completed" if status in {"done", "awaiting_user_input", "paused", "failed", "killed"} else "streaming",
                "text": f"process:{round_id}",
            }
        ],
        result_text=result_text,
        input_attachments=[
            ProjectionAttachmentUpsert(
                attachment_id=f"{round_id}:input:{display_name}",
                display_name=display_name,
                content_type="text/markdown",
                size_bytes=10,
                is_image=False,
                ordinal=ordinal,
            )
            for ordinal, display_name in input_attachment_specs
        ],
        result_attachments=[
            ProjectionAttachmentUpsert(
                attachment_id=f"{round_id}:result:{display_name}",
                display_name=display_name,
                content_type="image/png",
                size_bytes=12,
                is_image=True,
                ordinal=ordinal,
            )
            for ordinal, display_name in result_attachment_specs
        ],
        source_updated_at=created_at,
        projection_version=version,
    )


def _batch(
    *,
    version: int,
    batch_id: str,
    session_idempotency_key: str,
    list_status: str,
    snapshot_status: str,
    last_summary: str,
    pending_task_count: int,
    updated_at: str,
    rounds: list[ProjectionRoundUpsert] | None = None,
    closeouts: list[ProjectionCloseoutUpsert] | None = None,
) -> ProjectionSessionBatch:
    return ProjectionSessionBatch(
        batch_id=batch_id,
        connection_epoch=1,
        sent_at=updated_at,
        session=_session_upsert(
            version=version,
            batch_id=batch_id,
            session_idempotency_key=session_idempotency_key,
            list_status=list_status,
            snapshot_status=snapshot_status,
            last_summary=last_summary,
            pending_task_count=pending_task_count,
            updated_at=updated_at,
            queued_task_id="task_queued" if list_status == "queued" else None,
            current_task_id="task_001" if list_status != "queued" else "task_queued",
        ),
        rounds=rounds or [],
        closeouts=closeouts or [],
    )


def _store(tmp_path) -> RelayProjectionStore:
    return RelayProjectionStore(tmp_path / "projection.sqlite3")


def test_session_batch_replay_is_noop(tmp_path) -> None:
    store = _store(tmp_path)
    batch = _batch(
        version=1,
        batch_id="batch-1",
        session_idempotency_key="sess:alpha:v1",
        list_status="queued",
        snapshot_status="queued",
        last_summary="Queued for pickup.",
        pending_task_count=1,
        updated_at="2026-03-27T10:00:00",
        rounds=[
            _round_upsert(
                version=1,
                task_id="task_001",
                round_id="round_001",
                round_sort_at="2026-03-27T10:00:00",
                created_at="2026-03-27T10:00:00",
                status="queued",
                input_text="Kick off the task.",
                result_text=None,
                input_attachment_specs=[(2, "later.md"), (1, "earlier.md")],
                result_attachment_specs=[],
            )
        ],
    )

    assert store.apply_session_batch(batch) is True
    assert store.apply_session_batch(batch) is False
    assert store.get_projection_version(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    ) == 1

    snapshot = store.get_session_snapshot(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )
    assert snapshot is not None
    assert snapshot["session"]["status"] == "queued"
    assert snapshot["session_snapshot"]["latest_session_action"] is None
    assert len(snapshot["session_snapshot"]["history_rounds"]) == 1


def test_same_idempotency_key_with_different_payload_conflicts(tmp_path) -> None:
    store = _store(tmp_path)
    batch = _batch(
        version=1,
        batch_id="batch-1",
        session_idempotency_key="sess:alpha:v1",
        list_status="queued",
        snapshot_status="queued",
        last_summary="Queued for pickup.",
        pending_task_count=1,
        updated_at="2026-03-27T10:00:00",
    )
    assert store.apply_session_batch(batch) is True

    conflicting_batch = _batch(
        version=1,
        batch_id="batch-2",
        session_idempotency_key="sess:alpha:v1",
        list_status="running",
        snapshot_status="running",
        last_summary="Changed summary with same idempotency key.",
        pending_task_count=0,
        updated_at="2026-03-27T10:05:00",
    )

    with pytest.raises(ProjectionStoreConflictError) as excinfo:
        store.apply_session_batch(conflicting_batch)
    assert excinfo.value.code in {"receipt_conflict", "session_conflict"}


def test_new_version_overwrites_old_state_and_ignores_stale_replay(tmp_path) -> None:
    store = _store(tmp_path)
    version1 = _batch(
        version=1,
        batch_id="batch-v1",
        session_idempotency_key="sess:alpha:v1",
        list_status="queued",
        snapshot_status="queued",
        last_summary="Queued for pickup.",
        pending_task_count=1,
        updated_at="2026-03-27T10:00:00",
        rounds=[
            _round_upsert(
                version=1,
                task_id="task_001",
                round_id="round_001",
                round_sort_at="2026-03-27T10:00:00",
                created_at="2026-03-27T10:00:00",
                status="queued",
                input_text="Kick off the task.",
                result_text="Still queued.",
                input_attachment_specs=[(1, "brief.md")],
                result_attachment_specs=[],
            )
        ],
    )
    version2 = _batch(
        version=2,
        batch_id="batch-v2",
        session_idempotency_key="sess:alpha:v2",
        list_status="running",
        snapshot_status="running",
        last_summary="Now running.",
        pending_task_count=0,
        updated_at="2026-03-27T10:10:00",
        rounds=[
            _round_upsert(
                version=2,
                task_id="task_001",
                round_id="round_001",
                round_sort_at="2026-03-27T10:00:00",
                created_at="2026-03-27T10:10:00",
                status="running",
                input_text="Kick off the task.",
                result_text="Now running.",
                input_attachment_specs=[(1, "brief.md")],
                result_attachment_specs=[(1, "progress.png")],
            )
        ],
    )
    stale_version1 = _batch(
        version=1,
        batch_id="batch-v1-stale",
        session_idempotency_key="sess:alpha:v1-stale",
        list_status="paused",
        snapshot_status="paused",
        last_summary="Stale state.",
        pending_task_count=0,
        updated_at="2026-03-27T10:20:00",
    )

    assert store.apply_session_batch(version1) is True
    assert store.apply_session_batch(version2) is True
    assert store.apply_session_batch(stale_version1) is False
    assert store.get_projection_version(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    ) == 2

    snapshot = store.get_session_snapshot(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )
    assert snapshot is not None
    assert snapshot["session"]["status"] == "running"
    assert snapshot["session_snapshot"]["status"] == "running"
    assert snapshot["session_snapshot"]["last_summary"] == "Now running."
    assert snapshot["session_snapshot"]["history_rounds"][0]["result"]["attachments"][0]["display_name"] == "progress.png"


def test_round_and_attachment_reading_are_sorted(tmp_path) -> None:
    store = _store(tmp_path)
    batch = _batch(
        version=1,
        batch_id="batch-sort",
        session_idempotency_key="sess:alpha:v1",
        list_status="running",
        snapshot_status="running",
        last_summary="Latest summary.",
        pending_task_count=0,
        updated_at="2026-03-27T11:00:00",
        rounds=[
            _round_upsert(
                version=1,
                task_id="task_001",
                round_id="round_001",
                round_sort_at="2026-03-27T10:00:00",
                created_at="2026-03-27T10:00:00",
                status="done",
                input_text="First round.",
                result_text="First result.",
                input_attachment_specs=[(2, "later-a.md"), (1, "earlier-a.md")],
                result_attachment_specs=[(2, "later-a.png"), (1, "earlier-a.png")],
            ),
            _round_upsert(
                version=1,
                task_id="task_002",
                round_id="round_002",
                round_sort_at="2026-03-27T11:00:00",
                created_at="2026-03-27T11:00:00",
                status="done",
                input_text="Second round.",
                result_text="Second result.",
                input_attachment_specs=[(2, "later-b.md"), (1, "earlier-b.md")],
                result_attachment_specs=[(2, "later-b.png"), (1, "earlier-b.png")],
            ),
        ],
    )

    assert store.apply_session_batch(batch) is True
    history_rounds = store.list_session_history_rounds(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )
    assert [item["round_number"] for item in history_rounds] == [2, 1]
    assert [item["round_id"] for item in history_rounds] == ["round_002", "round_001"]
    assert [item["input"]["attachments"][0]["display_name"] for item in history_rounds] == [
        "earlier-b.md",
        "earlier-a.md",
    ]
    assert [item["result"]["attachments"][0]["display_name"] for item in history_rounds] == [
        "earlier-b.png",
        "earlier-a.png",
    ]


def test_closeout_and_probe_upserts_do_not_touch_session_head(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = _batch(
        version=1,
        batch_id="batch-baseline",
        session_idempotency_key="sess:alpha:v1",
        list_status="running",
        snapshot_status="running",
        last_summary="Base summary.",
        pending_task_count=0,
        updated_at="2026-03-27T10:00:00",
    )
    assert store.apply_session_batch(baseline) is True

    snapshot_before = store.get_session_snapshot(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )
    assert snapshot_before is not None

    closeout_batch = _batch(
        version=1,
        batch_id="batch-closeout",
        session_idempotency_key="sess:alpha:v1",
        list_status="running",
        snapshot_status="running",
        last_summary="Base summary.",
        pending_task_count=0,
        updated_at="2026-03-27T10:00:00",
        closeouts=[
            ProjectionCloseoutUpsert(
                idempotency_key="closeout:v1",
                closeout_key="closeout-1",
                task_id="task_001",
                request_id="request-1",
                packet_id="packet-1",
                receipt_id="receipt-1",
                action_type="status",
                target_session_identity={
                    "pc_id": "pc_alpha",
                    "workspace_id": "workspace_alpha",
                    "session_id": "session_alpha",
                    "thread_id": "thread_alpha",
                },
                last_summary="Closeout metadata only.",
                terminal_mail_message_id="mail-1",
                terminal_mail_subject="Subject",
                generated_at="2026-03-27T10:01:00",
                source_updated_at="2026-03-27T10:01:00",
                projection_version=1,
            )
        ],
    )
    assert store.apply_session_batch(closeout_batch) is True

    probe = ProjectionProbeObservationUpsert(
        idempotency_key="probe:1",
        probe_id="probe-1",
        summary_text="Observed probe payload.",
        observation_status="observed",
        observed_at="2026-03-27T10:02:00",
        payload={"probe_id": "probe-1", "result": "ok"},
        pc_id="pc_alpha",
        request_id="request-probe-1",
        packet_id="packet-probe-1",
        receipt_id="receipt-probe-1",
        mailbox_message_id="mail-1",
    )
    assert store.upsert_probe_observation(probe) is True
    assert store.upsert_probe_observation(probe) is False

    snapshot_after = store.get_session_snapshot(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )
    assert snapshot_after is not None
    assert snapshot_after["session"] == snapshot_before["session"]
    assert snapshot_after["session_snapshot"]["status"] == "running"
    assert snapshot_after["session_snapshot"]["latest_session_action"] is None
    assert store.get_projection_version(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    ) == 1


def test_live_process_is_exposed_in_snapshot_and_advances_last_progress(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = _batch(
        version=1,
        batch_id="batch-live-output",
        session_idempotency_key="sess:alpha:v1",
        list_status="running",
        snapshot_status="running",
        last_summary="Base summary.",
        pending_task_count=0,
        updated_at="2026-03-27T10:00:00",
    )

    assert store.apply_session_batch(baseline) is True
    assert store.upsert_session_live_process(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        command_id="cmd_live_001",
        stream_id="thread_alpha:task_001",
        task_id="task_001",
        last_seq=3,
        items=[
            {
                "item_id": "process:thread_alpha:task_001:1",
                "kind": "assistant",
                "created_at": "2026-03-27T10:00:12",
                "updated_at": "2026-03-27T10:00:12",
                "status": "streaming",
                "text": "Streaming assistant output.",
            }
        ],
        updated_at="2026-03-27T10:00:12",
        status="streaming",
    ) is True

    snapshot = store.get_session_snapshot(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )

    assert snapshot is not None
    assert snapshot["session_snapshot"]["live_process"] == {
        "status": "streaming",
        "updated_at": "2026-03-27T10:00:12",
        "items": [
            {
                "item_id": "process:thread_alpha:task_001:1",
                "kind": "assistant",
                "created_at": "2026-03-27T10:00:12",
                "updated_at": "2026-03-27T10:00:12",
                "status": "streaming",
                "text": "Streaming assistant output.",
            }
        ],
    }
    assert snapshot["session_snapshot"]["last_progress_at"] == "2026-03-27T10:00:12"


def test_stable_round_materialization_clears_completed_live_process(tmp_path) -> None:
    store = _store(tmp_path)
    running_batch = _batch(
        version=1,
        batch_id="batch-live-output-running",
        session_idempotency_key="sess:alpha:v1",
        list_status="running",
        snapshot_status="running",
        last_summary="Still running.",
        pending_task_count=0,
        updated_at="2026-03-27T10:00:00",
    )

    assert store.apply_session_batch(running_batch) is True
    assert store.upsert_session_live_process(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        command_id="cmd_live_002",
        stream_id="thread_alpha:task_001",
        task_id="task_001",
        last_seq=4,
        items=[
            {
                "item_id": "process:thread_alpha:task_001:1",
                "kind": "assistant",
                "created_at": "2026-03-27T10:00:15",
                "updated_at": "2026-03-27T10:00:15",
                "status": "completed",
                "text": "Completed but not yet materialized.",
            }
        ],
        updated_at="2026-03-27T10:00:15",
        status="completed",
    ) is True

    terminal_batch = _batch(
        version=2,
        batch_id="batch-live-output-terminal",
        session_idempotency_key="sess:alpha:v2",
        list_status="done",
        snapshot_status="done",
        last_summary="Stable result is now materialized.",
        pending_task_count=0,
        updated_at="2026-03-27T10:00:20",
        rounds=[
            _round_upsert(
                version=2,
                task_id="task_001",
                round_id="round_001",
                round_sort_at="2026-03-27T10:00:20",
                created_at="2026-03-27T10:00:20",
                status="done",
                input_text="Kick off the task.",
                result_text="Stable result is now materialized.",
                input_attachment_specs=[],
                result_attachment_specs=[],
            )
        ],
    )

    assert store.apply_session_batch(terminal_batch) is True

    snapshot = store.get_session_snapshot(
        pc_id="pc_alpha",
        workspace_id="workspace_alpha",
        session_id="session_alpha",
        thread_id="thread_alpha",
    )

    assert snapshot is not None
    assert snapshot["session_snapshot"]["live_process"] is None
