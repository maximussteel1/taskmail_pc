from __future__ import annotations

from dataclasses import dataclass

import pytest

from mail_runner.relay_server.android_projection_store_facade import (
    AndroidProjectionStoreFacadeError,
    build_android_session_history_from_projection_store,
    build_android_session_snapshot_from_projection_store,
    build_android_sessions_snapshot_from_projection_store,
)
from mail_runner.relay_server.android_session_history_facade import ANDROID_SESSION_HISTORY_SCHEMA_VERSION
from mail_runner.relay_server.android_session_snapshot_facade import ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION
from mail_runner.relay_server.android_sessions_facade import ANDROID_SESSIONS_SCHEMA_VERSION
from mail_runner.thread_store import build_workspace_id


@dataclass(slots=True)
class _FakeProjectionStore:
    sessions: list[dict[str, object]]
    history_rounds_by_session_key: dict[str, list[dict[str, object]]]
    live_process_by_session_key: dict[str, dict[str, object] | None] | None = None

    def list_sessions(self, pc_id: str | None = None) -> list[dict[str, object]]:
        if pc_id is None:
            return [dict(item) for item in self.sessions]
        return [dict(item) for item in self.sessions if item.get("pc_id") == pc_id]

    def list_history_rounds(self, *, session_key: str) -> list[dict[str, object]]:
        return [dict(item) for item in self.history_rounds_by_session_key.get(session_key, [])]

    def get_live_process(self, *, session_key: str) -> dict[str, object] | None:
        if self.live_process_by_session_key is None:
            return None
        payload = self.live_process_by_session_key.get(session_key)
        return None if payload is None else dict(payload)


def _session_record(
    *,
    pc_id: str,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    repo_path: str,
    workdir: str | None,
    session_name: str,
    lifecycle: str,
    list_status: str,
    snapshot_status: str,
    updated_at: str,
    last_progress_at: str,
    last_active_at: str | None = None,
    queued_task_id: str | None = None,
    pending_task_count: int | None = None,
) -> dict[str, object]:
    return {
        "session_key": f"{pc_id}::{workspace_id}::{session_id}::{thread_id}",
        "pc_id": pc_id,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "session_name": session_name,
        "backend": "codex",
        "backend_transport": "sdk",
        "profile": "default",
        "permission": "default",
        "repo_path": repo_path,
        "workdir": workdir,
        "list_status": list_status,
        "snapshot_status": snapshot_status,
        "lifecycle": lifecycle,
        "current_task_id": f"task_{session_id}",
        "queued_task_id": queued_task_id,
        "pending_task_count": pending_task_count,
        "last_summary": f"summary:{session_id}",
        "last_active_at": last_active_at or updated_at,
        "last_progress_at": last_progress_at,
        "backend_session_id": f"backend:{session_id}",
        "backend_session_resumable": True,
        "question_state_json": None,
        "timeline_items_json": [],
        "created_at": "2026-03-29T10:00:00",
        "updated_at": updated_at,
    }


def _round_record(
    *,
    task_id: str,
    round_sort_at: str,
    status: str,
    speaker_label: str,
    input_text: str | None,
    result_text: str | None,
    input_attachments: list[dict[str, object]] | None = None,
    result_attachments: list[dict[str, object]] | None = None,
    process_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "round_id": f"hist_round_{task_id}",
        "round_sort_at": round_sort_at,
        "status": status,
        "speaker_label": speaker_label,
        "input_text": input_text,
        "input_attachments": input_attachments or [],
        "process_items": process_items or [],
        "result_text": result_text,
        "result_attachments": result_attachments or [],
    }


def test_build_android_sessions_snapshot_from_projection_store_filters_active_and_include_ended() -> None:
    repo_path = "E:\\projects\\alpha"
    workdir = "main"
    workspace_id = build_workspace_id(repo_path, workdir)
    store = _FakeProjectionStore(
        sessions=[
            _session_record(
                pc_id="pc_alpha",
                workspace_id=workspace_id,
                session_id="session_active_new",
                thread_id="thread_active_new",
                repo_path=repo_path,
                workdir=workdir,
                session_name="Active New",
                lifecycle="active",
                list_status="running",
                snapshot_status="running",
                updated_at="2026-03-29T10:02:00",
                last_progress_at="2026-03-29T10:02:00",
            ),
            _session_record(
                pc_id="pc_alpha",
                workspace_id=workspace_id,
                session_id="session_active_old",
                thread_id="thread_active_old",
                repo_path=repo_path,
                workdir=workdir,
                session_name="Active Old",
                lifecycle="active",
                list_status="queued",
                snapshot_status="queued",
                updated_at="2026-03-29T10:01:00",
                last_progress_at="2026-03-29T10:01:00",
            ),
            _session_record(
                pc_id="pc_alpha",
                workspace_id=workspace_id,
                session_id="session_ended",
                thread_id="thread_ended",
                repo_path=repo_path,
                workdir=workdir,
                session_name="Ended",
                lifecycle="ended",
                list_status="done",
                snapshot_status="done",
                updated_at="2026-03-29T10:03:00",
                last_progress_at="2026-03-29T10:03:00",
            ),
        ],
        history_rounds_by_session_key={},
    )

    default_payload = build_android_sessions_snapshot_from_projection_store(projection_store=store)
    ended_payload = build_android_sessions_snapshot_from_projection_store(
        projection_store=store,
        include_ended=True,
    )

    assert default_payload["schema_version"] == ANDROID_SESSIONS_SCHEMA_VERSION
    assert default_payload["session_count"] == 2
    assert [item["session_id"] for item in default_payload["sessions"]] == [
        "session_active_new",
        "session_active_old",
    ]
    assert [item["lifecycle"] for item in default_payload["sessions"]] == ["active", "active"]

    assert ended_payload["schema_version"] == ANDROID_SESSIONS_SCHEMA_VERSION
    assert ended_payload["session_count"] == 3
    assert [item["session_id"] for item in ended_payload["sessions"]] == [
        "session_active_new",
        "session_active_old",
        "session_ended",
    ]


def test_build_android_session_snapshot_from_projection_store_resolves_locator_and_injects_latest_session_action() -> None:
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    session_record = _session_record(
        pc_id="pc_alpha",
        workspace_id=workspace_id,
        session_id="session_001",
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Projection session",
        lifecycle="active",
        list_status="running",
        snapshot_status="running",
        updated_at="2026-03-29T10:02:00",
        last_progress_at="2026-03-29T10:02:00",
    )
    store = _FakeProjectionStore(
        sessions=[session_record],
        history_rounds_by_session_key={
            session_record["session_key"]: [
                _round_record(
                    task_id="task_001",
                    round_sort_at="2026-03-29T10:00:00",
                    status="done",
                    speaker_label="Codex",
                    input_text="Draft the first tree.",
                    result_text="Done.",
                    input_attachments=[
                        {
                            "attachment_id": "input_a",
                            "display_name": "brief.md",
                            "content_type": "text/markdown",
                            "size_bytes": 11,
                            "is_image": False,
                        }
                    ],
                    result_attachments=[
                        {
                            "attachment_id": "result_a",
                            "display_name": "tree.png",
                            "content_type": "image/png",
                            "size_bytes": 7,
                            "is_image": True,
                        }
                    ],
                    process_items=[
                        {
                            "item_id": "proc_001",
                            "created_at": "2026-03-29T10:00:30",
                            "status": "done",
                            "text": "Projected from durable result.",
                        }
                    ],
                )
            ]
        },
    )

    resolver_calls: list[dict[str, object]] = []

    def _latest_session_action_resolver(locator: dict[str, object]) -> dict[str, object] | None:
        resolver_calls.append(dict(locator))
        return {
            "command_id": "cmd_001",
            "action_type": "reply",
            "submit_ack": {"ack_status": "accepted", "queue_position": 1, "reason": None, "error_code": None},
            "result_status": "done",
        }

    payload = build_android_session_snapshot_from_projection_store(
        projection_store=store,
        query={
            "workspace_id": [workspace_id],
            "repo_path": [repo_path],
            "workdir": [workdir],
            "session_id": ["session_001"],
            "thread_id": ["thread_001"],
        },
        latest_session_action_resolver=_latest_session_action_resolver,
    )

    assert payload["schema_version"] == ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION
    assert payload["locator"] == {
        "pc_id": "pc_alpha",
        "workspace_id": workspace_id,
        "session_id": "session_001",
        "thread_id": "thread_001",
    }
    assert payload["session"]["pc_id"] == "pc_alpha"
    assert payload["session_snapshot"]["status"] == "running"
    assert payload["session_snapshot"]["latest_session_action"] == {
        "command_id": "cmd_001",
        "action_type": "reply",
        "submit_ack": {"ack_status": "accepted", "queue_position": 1, "reason": None, "error_code": None},
        "result_status": "done",
    }
    assert resolver_calls == [
        {
            "pc_id": "pc_alpha",
            "workspace_id": workspace_id,
            "session_id": "session_001",
            "thread_id": "thread_001",
            "session_key": session_record["session_key"],
            "repo_path": repo_path,
            "workdir": workdir,
        }
    ]


def test_build_android_session_snapshot_from_projection_store_exposes_live_output_and_advances_last_progress() -> None:
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    session_record = _session_record(
        pc_id="pc_alpha",
        workspace_id=workspace_id,
        session_id="session_live_001",
        thread_id="thread_live_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Projection live output",
        lifecycle="active",
        list_status="running",
        snapshot_status="running",
        updated_at="2026-03-29T10:02:00",
        last_progress_at="2026-03-29T10:02:00",
    )
    store = _FakeProjectionStore(
        sessions=[session_record],
        history_rounds_by_session_key={session_record["session_key"]: []},
        live_process_by_session_key={
            session_record["session_key"]: {
                "status": "streaming",
                "updated_at": "2026-03-29T10:05:00",
                "items": [
                    {
                        "item_id": "proc_live_001",
                        "kind": "assistant",
                        "created_at": "2026-03-29T10:04:58",
                        "updated_at": "2026-03-29T10:05:00",
                        "status": "streaming",
                        "text": "Streaming reply in progress.",
                    }
                ],
            }
        },
    )

    payload = build_android_session_snapshot_from_projection_store(
        projection_store=store,
        query={"session_id": ["session_live_001"]},
    )

    assert payload["session_snapshot"]["live_process"] == {
        "status": "streaming",
        "updated_at": "2026-03-29T10:05:00",
        "items": [
            {
                "item_id": "proc_live_001",
                "kind": "assistant",
                "created_at": "2026-03-29T10:04:58",
                "updated_at": "2026-03-29T10:05:00",
                "status": "streaming",
                "text": "Streaming reply in progress.",
            }
        ],
    }
    assert payload["session_snapshot"]["last_progress_at"] == "2026-03-29T10:05:00"


def test_build_android_session_snapshot_from_projection_store_rejects_ambiguous_locator() -> None:
    repo_path = "E:\\projects\\shared"
    workdir = "main"
    workspace_id = build_workspace_id(repo_path, workdir)
    store = _FakeProjectionStore(
        sessions=[
            _session_record(
                pc_id="pc_alpha",
                workspace_id=workspace_id,
                session_id="session_dup",
                thread_id="thread_a",
                repo_path=repo_path,
                workdir=workdir,
                session_name="Duplicate A",
                lifecycle="active",
                list_status="running",
                snapshot_status="running",
                updated_at="2026-03-29T10:02:00",
                last_progress_at="2026-03-29T10:02:00",
            ),
            _session_record(
                pc_id="pc_beta",
                workspace_id=workspace_id,
                session_id="session_dup",
                thread_id="thread_b",
                repo_path=repo_path,
                workdir=workdir,
                session_name="Duplicate B",
                lifecycle="active",
                list_status="running",
                snapshot_status="running",
                updated_at="2026-03-29T10:03:00",
                last_progress_at="2026-03-29T10:03:00",
            ),
        ],
        history_rounds_by_session_key={},
    )

    with pytest.raises(AndroidProjectionStoreFacadeError) as exc_info:
        build_android_session_snapshot_from_projection_store(
            projection_store=store,
            query={"session_id": ["session_dup"]},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "session_binding_unresolved"


def test_build_android_session_history_from_projection_store_reverses_round_order_and_natural_numbers() -> None:
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    session_record = _session_record(
        pc_id="pc_alpha",
        workspace_id=workspace_id,
        session_id="session_001",
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Projection history",
        lifecycle="active",
        list_status="running",
        snapshot_status="running",
        updated_at="2026-03-29T10:02:00",
        last_progress_at="2026-03-29T10:02:00",
    )
    store = _FakeProjectionStore(
        sessions=[session_record],
        history_rounds_by_session_key={
            session_record["session_key"]: [
                _round_record(
                    task_id="task_001",
                    round_sort_at="2026-03-29T10:00:00",
                    status="done",
                    speaker_label="Codex",
                    input_text="First task input.",
                    result_text="First task result.",
                    input_attachments=[
                        {
                            "attachment_id": "input_first",
                            "display_name": "first.md",
                            "content_type": "text/markdown",
                            "size_bytes": 5,
                            "is_image": False,
                        }
                    ],
                    result_attachments=[
                        {
                            "attachment_id": "result_first",
                            "display_name": "first.png",
                            "content_type": "image/png",
                            "size_bytes": 6,
                            "is_image": True,
                        }
                    ],
                ),
                _round_record(
                    task_id="task_002",
                    round_sort_at="2026-03-29T11:00:00",
                    status="awaiting_user_input",
                    speaker_label="Codex",
                    input_text="Second task input.",
                    result_text="Need review.",
                    input_attachments=[
                        {
                            "attachment_id": "input_first_repeat",
                            "display_name": "first.md",
                            "content_type": "text/markdown",
                            "size_bytes": 5,
                            "is_image": False,
                        },
                        {
                            "attachment_id": "input_second",
                            "display_name": "second.md",
                            "content_type": "text/markdown",
                            "size_bytes": 6,
                            "is_image": False,
                        }
                    ],
                    process_items=[
                        {
                            "item_id": "proc_second",
                            "created_at": "2026-03-29T11:00:30",
                            "status": "waiting_user",
                            "text": "Need a decision.",
                        }
                    ],
                ),
            ]
        },
    )

    payload = build_android_session_history_from_projection_store(
        projection_store=store,
        query={
            "workspace_id": [workspace_id],
            "session_id": ["session_001"],
        },
    )

    assert payload["schema_version"] == ANDROID_SESSION_HISTORY_SCHEMA_VERSION
    assert payload["locator"]["workspace_id"] == workspace_id
    assert payload["session"]["session_id"] == "session_001"
    assert [item["round_number"] for item in payload["history_rounds"]] == [2, 1]
    assert payload["history_rounds"][0]["round_id"] == "hist_round_task_002"
    assert payload["history_rounds"][0]["input"]["text"] == "Second task input."
    assert payload["history_rounds"][0]["input"]["attachments"] == [
        {
            "attachment_id": "input_second",
            "display_name": "second.md",
            "content_type": "text/markdown",
            "size_bytes": 6,
            "is_image": False,
        }
    ]
    assert payload["history_rounds"][0]["process"]["items"][0]["text"] == "Need a decision."
    assert payload["history_rounds"][1]["round_id"] == "hist_round_task_001"
    assert payload["history_rounds"][1]["input"]["attachments"][0]["display_name"] == "first.md"
    assert payload["history_rounds"][1]["result"]["attachments"][0]["display_name"] == "first.png"


def test_build_android_session_history_from_projection_store_preserves_nested_result_payload() -> None:
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    session_record = _session_record(
        pc_id="pc_alpha",
        workspace_id=workspace_id,
        session_id="session_nested_001",
        thread_id="thread_nested_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Projection nested history",
        lifecycle="active",
        list_status="done",
        snapshot_status="done",
        updated_at="2026-03-30T01:02:43",
        last_progress_at="2026-03-30T01:02:43",
    )
    store = _FakeProjectionStore(
        sessions=[session_record],
        history_rounds_by_session_key={
            session_record["session_key"]: [
                {
                    "round_id": "hist_round_task_nested",
                    "task_id": "task_nested",
                    "round_number": 1,
                    "created_at": "2026-03-30T01:02:41",
                    "status": "done",
                    "speaker_label": "TaskMail",
                    "input": {
                        "text": "What model was selected?",
                        "attachments": [],
                    },
                    "process": {
                        "items": [
                            {
                                "item_id": "proc_nested",
                                "created_at": "2026-03-30T01:02:42",
                                "status": "done",
                                "text": "Projected from relay store.",
                            }
                        ],
                    },
                    "result": {
                        "text": "qwen3-max-2026-01-23",
                        "attachments": [
                            {
                                "attachment_id": "result_nested",
                                "display_name": "selection.txt",
                                "content_type": "text/plain",
                                "size_bytes": 20,
                                "is_image": False,
                            }
                        ],
                    },
                }
            ]
        },
    )

    payload = build_android_session_history_from_projection_store(
        projection_store=store,
        query={
            "workspace_id": [workspace_id],
            "session_id": ["session_nested_001"],
        },
    )

    assert payload["history_rounds"][0]["result"]["text"] == "qwen3-max-2026-01-23"
    assert payload["history_rounds"][0]["process"]["items"][0]["text"] == "Projected from relay store."
    assert payload["history_rounds"][0]["result"]["attachments"][0]["display_name"] == "selection.txt"
