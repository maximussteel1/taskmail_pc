from __future__ import annotations

from mail_runner.pc_projection_publisher import (
    build_metadata_only_closeout_batch,
    build_session_projection_batch,
    build_transport_probe_batch,
)


def _session_state() -> dict[str, object]:
    return {
        "pc_id": "pc-01",
        "workspace_id": "workspace-01",
        "session_id": "session-01",
        "thread_id": "thread-01",
        "session_name": "Projection session",
        "backend": "opencode",
        "backend_transport": "sdk",
        "profile": "default",
        "permission": "default",
        "repo_path": "E:\\repo",
        "workdir": "feature/projection",
        "status": "running",
        "current_task_id": "task-02",
        "queued_task_id": "task-03",
        "pending_task_count": 1,
        "last_summary": "Working on the active task.",
        "last_active_at": "2026-03-29T10:00:00",
        "last_progress_at": "2026-03-29T10:01:00",
        "lifecycle": "active",
        "backend_session_id": "backend-session-01",
        "backend_session_resumable": True,
        "created_at": "2026-03-29T09:00:00",
        "updated_at": "2026-03-29T10:01:00",
    }


def _thread_state() -> dict[str, object]:
    return {
        "status": "running",
        "last_summary": "Working on the active task.",
        "last_active_at": "2026-03-29T10:00:00",
        "last_progress_at": "2026-03-29T10:01:00",
    }


def _current_round() -> dict[str, object]:
    return {
        "task_id": "task-02",
        "round_sort_at": "2026-03-29T10:01:00",
        "status": "running",
        "speaker_label": "OpenCode",
        "input_text": "Continue the task.",
        "input_attachments": [
            {
                "attachment_id": "input-1",
                "display_name": "brief.md",
                "content_type": "text/markdown",
                "size_bytes": 12,
                "is_image": False,
            }
        ],
        "process_items": [
            {
                "item_id": "process-1",
                "created_at": "2026-03-29T10:01:00",
                "status": "running",
                "text": "Analyzing the latest change set.",
            }
        ],
        "result_text": "Still working.",
        "result_attachments": [
            {
                "attachment_id": "result-1",
                "display_name": "preview.png",
                "content_type": "image/png",
                "size_bytes": 99,
            }
        ],
        "artifact_refs": [
            {
                "artifact_id": "artifact-1",
                "display_name": "preview.png",
                "content_type": "image/png",
                "size_bytes": 99,
                "file_id": "file-1",
                "download_ref": {
                    "kind": "vps_file",
                    "file_id": "file-1",
                    "metadata_url": "https://relay/files/file-1",
                    "content_url": "https://relay/files/1",
                },
                "download_ref_source": "external_delivery_index.file_surface",
                "provider": "file_surface",
                "expires_at": "2026-03-29T12:00:00",
            }
        ],
        "round_number": 999,
    }


def _closeout() -> dict[str, object]:
    return {
        "closeout_key": "closeout-01",
        "task_id": "task-02",
        "request_id": "req-01",
        "packet_id": "packet-01",
        "receipt_id": "receipt-01",
        "action_type": "status",
        "target_session_identity": {
            "workspace_id": "workspace-01",
            "session_id": "session-01",
            "thread_id": "thread-01",
        },
        "last_summary": "Working on the active task.",
        "terminal_mail_message_id": "<terminal-01@example.com>",
        "terminal_mail_subject": "Task status",
        "generated_at": "2026-03-29T10:02:00",
    }


def test_session_projection_batch_shape_and_identity_consistency() -> None:
    batch = build_session_projection_batch(
        pc_id="pc-01",
        connection_epoch=7,
        session_state=_session_state(),
        thread_state=_thread_state(),
        projection_version=12,
        current_round=_current_round(),
        question_state={
            "question_set_id": "qset-01",
            "question_count": 1,
            "questions": [
                {
                    "question_id": "question-01",
                    "question_text": "Continue?",
                    "question_type": "short_text",
                    "required": True,
                    "choices": [],
                    "choice_labels": {},
                }
            ],
        },
        timeline_items=[
            {
                "item_id": "tl-01",
                "business_event_key": "status/running/2026-03-29T10:01:00",
                "item_type": "status_transition",
                "created_at": "2026-03-29T10:01:00",
                "status": "running",
                "text": "Running.",
                "question_set_id": None,
                "question_ids": [],
                "paused_from_status": None,
            }
        ],
        closeouts=[_closeout()],
        sent_at="2026-03-29T10:03:00",
    )

    assert batch["message_type"] == "projection_batch"
    assert batch["schema_version"] == "taskmail-pc-projection-batch-v1"
    assert batch["scope"] == "session"
    assert batch["pc_id"] == "pc-01"
    assert batch["connection_epoch"] == 7
    assert batch["workspace_id"] == "workspace-01"
    assert batch["session_id"] == "session-01"
    assert batch["thread_id"] == "thread-01"
    assert batch["projection_version"] == 12

    session_item = batch["items"][0]
    round_item = batch["items"][1]
    closeout_item = batch["items"][2]

    assert session_item["pc_id"] == batch["pc_id"]
    assert session_item["workspace_id"] == batch["workspace_id"]
    assert session_item["session_id"] == batch["session_id"]
    assert session_item["thread_id"] == batch["thread_id"]
    assert session_item["projection_version"] == batch["projection_version"]
    assert session_item["list_status"] == "running"
    assert session_item["snapshot_status"] == "running"
    assert "question_state" in session_item
    assert "timeline_items" in session_item

    assert round_item["pc_id"] == batch["pc_id"]
    assert round_item["workspace_id"] == batch["workspace_id"]
    assert round_item["session_id"] == batch["session_id"]
    assert round_item["thread_id"] == batch["thread_id"]
    assert round_item["projection_version"] == batch["projection_version"]
    assert "round_number" not in round_item
    assert round_item["artifact_refs"][0]["download_ref_source"] == "external_delivery_index.file_surface"

    assert closeout_item["pc_id"] == batch["pc_id"]
    assert closeout_item["workspace_id"] == batch["workspace_id"]
    assert closeout_item["session_id"] == batch["session_id"]
    assert closeout_item["thread_id"] == batch["thread_id"]
    assert closeout_item["projection_version"] == batch["projection_version"]
    assert closeout_item["target_session_identity"] == {
        "workspace_id": "workspace-01",
        "session_id": "session-01",
        "thread_id": "thread-01",
    }


def test_session_projection_batch_idempotency_keys_are_stable() -> None:
    batch_a = build_session_projection_batch(
        pc_id="pc-01",
        connection_epoch=7,
        session_state=_session_state(),
        thread_state=_thread_state(),
        projection_version=12,
        current_round=_current_round(),
        closeouts=[_closeout()],
        sent_at="2026-03-29T10:03:00",
    )
    batch_b = build_session_projection_batch(
        pc_id="pc-01",
        connection_epoch=7,
        session_state=_session_state(),
        thread_state=_thread_state(),
        projection_version=12,
        current_round=_current_round(),
        closeouts=[_closeout()],
        sent_at="2026-03-29T10:03:00",
    )

    assert [item["idempotency_key"] for item in batch_a["items"]] == [
        item["idempotency_key"] for item in batch_b["items"]
    ]
    assert batch_a["batch_id"] == batch_b["batch_id"]


def test_metadata_only_closeout_batch_omits_round_items() -> None:
    batch = build_metadata_only_closeout_batch(
        pc_id="pc-01",
        connection_epoch=7,
        session_state=_session_state(),
        thread_state=_thread_state(),
        projection_version=12,
        closeouts=[_closeout()],
        sent_at="2026-03-29T10:03:00",
    )

    assert batch["scope"] == "session"
    assert len(batch["items"]) == 1
    assert batch["items"][0]["type"] == "session_closeout_upsert"
    assert "round_number" not in batch["items"][0]


def test_transport_probe_batch_shape_and_stable_idempotency() -> None:
    observation = {
        "probe_id": "probe-01",
        "request_id": "req-01",
        "packet_id": "packet-01",
        "receipt_id": "receipt-01",
        "mailbox_message_id": "<probe-01@example.com>",
        "summary_text": "Observed transport probe mail.",
        "observation_status": "observed",
        "observed_at": "2026-03-29T10:04:00",
        "payload": {
            "status": "observed",
            "mailbox_key": "inbox-01",
        },
    }

    batch_a = build_transport_probe_batch(
        pc_id="pc-01",
        connection_epoch=7,
        observation=observation,
        sent_at="2026-03-29T10:04:00",
    )
    batch_b = build_transport_probe_batch(
        pc_id="pc-01",
        connection_epoch=7,
        observation=observation,
        sent_at="2026-03-29T10:04:00",
    )

    assert batch_a["scope"] == "probe"
    assert batch_a["items"][0]["type"] == "transport_probe_observation_upsert"
    assert batch_a["items"][0]["probe_id"] == "probe-01"
    assert batch_a["items"][0]["idempotency_key"] == batch_b["items"][0]["idempotency_key"]
