from __future__ import annotations

import pytest

from mail_runner.models import ThreadState
from mail_runner.relay_server.phase3_subscription import (
    Phase3SubscriptionError,
    ThreadStorePhase3SessionDetailProvider,
    parse_phase3_subscribe_request,
)
from mail_runner.relay_server.protocol import RelayPacketMessage, RelaySessionUpdateMessage, parse_client_message, parse_server_message
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_thread_state


def _build_thread_state(
    *,
    repo_path: str = "E:\\projects\\android_task_manager",
    workdir: str | None = "feature/taskmail/internal",
) -> ThreadState:
    workspace_id = build_workspace_id(repo_path, workdir)
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
        workspace_id=workspace_id,
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id="session_001",
        session_name="Phase 3 detail bridge",
        session_norm="phase 3 detail bridge",
        created_at="2026-03-21T22:30:00",
        updated_at="2026-03-21T22:33:03",
    )


def _subscribe_payload(subscription: dict[str, object]) -> dict[str, object]:
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
            "subscription": subscription,
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "phase3-direct-inbound-wire-v1",
            "action": "subscribe_session_detail",
        },
        "sent_at": "2026-03-21T22:33:01",
    }


def test_parse_phase3_subscribe_request_accepts_repo_workdir_thread_fallback() -> None:
    message = parse_client_message(
        _subscribe_payload(
            {
                "repo_path": "E:\\projects\\android_task_manager",
                "workdir": "feature/taskmail/internal",
                "thread_id": "thread_001",
                "last_known_sequence": 0,
                "reason": "detail_open",
            }
        )
    )

    assert isinstance(message, RelayPacketMessage)
    request = parse_phase3_subscribe_request(message)

    assert request is not None
    assert request.repo_path == "E:\\projects\\android_task_manager"
    assert request.thread_id == "thread_001"
    assert request.last_known_sequence == 0


def test_thread_store_phase3_provider_builds_initial_snapshot_from_runtime_state(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    thread_state = _build_thread_state()
    save_thread_state(thread_state, task_root)
    provider = ThreadStorePhase3SessionDetailProvider(task_root=task_root)
    request = parse_phase3_subscribe_request(
        parse_client_message(
            _subscribe_payload(
                {
                    "repo_path": thread_state.repo_path,
                    "workdir": thread_state.workdir,
                    "thread_id": thread_state.thread_id,
                    "last_known_sequence": 3,
                    "reason": "detail_refresh",
                }
            )
        )
    )

    assert request is not None
    session_state, resolved_thread_state = provider.resolve_session_detail(request)
    result = provider.build_initial_snapshot(
        request,
        subscription_id="sub-001",
        sequence=4,
        sent_at="2026-03-21T22:33:03",
    )
    parsed = parse_server_message(result.session_update)

    assert session_state.workspace_id == thread_state.workspace_id
    assert resolved_thread_state.thread_id == thread_state.thread_id
    assert isinstance(parsed, RelaySessionUpdateMessage)
    assert parsed.sequence == 4
    assert parsed.session_snapshot["status"] == "running"
    assert parsed.workspace_id == thread_state.workspace_id


def test_thread_store_phase3_provider_rejects_repo_path_without_workdir(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    save_thread_state(_build_thread_state(), task_root)
    provider = ThreadStorePhase3SessionDetailProvider(task_root=task_root)
    request = parse_phase3_subscribe_request(
        parse_client_message(
            _subscribe_payload(
                {
                    "repo_path": "E:\\projects\\android_task_manager",
                    "session_id": "session_001",
                    "last_known_sequence": 0,
                    "reason": "detail_open",
                }
            )
        )
    )

    assert request is not None
    with pytest.raises(Phase3SubscriptionError, match="repo_path alone cannot resolve a unique workspace"):
        provider.resolve_session_detail(request)


def test_thread_store_phase3_provider_uses_mail_runner_task_root_env_when_task_root_omitted(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_root = tmp_path / "relay_task_root"
    thread_state = _build_thread_state()
    save_thread_state(thread_state, task_root)
    monkeypatch.setenv("MAIL_RUNNER_TASK_ROOT", str(task_root))
    provider = ThreadStorePhase3SessionDetailProvider()
    request = parse_phase3_subscribe_request(
        parse_client_message(
            _subscribe_payload(
                {
                    "repo_path": thread_state.repo_path,
                    "workdir": thread_state.workdir,
                    "thread_id": thread_state.thread_id,
                    "last_known_sequence": 0,
                    "reason": "detail_open",
                }
            )
        )
    )

    assert request is not None
    session_state, resolved_thread_state = provider.resolve_session_detail(request)

    assert session_state.thread_id == thread_state.thread_id
    assert resolved_thread_state.thread_id == thread_state.thread_id
