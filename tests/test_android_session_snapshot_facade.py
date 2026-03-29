from __future__ import annotations

import base64
import threading
import time
from datetime import datetime

import requests

from mail_runner.models import QuestionItem
from mail_runner.relay_server.android_session_action_facade import ANDROID_SESSION_ACTION_PATH
from mail_runner.relay_server.android_session_snapshot_facade import (
    ANDROID_SESSION_SNAPSHOT_PATH,
    ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION,
)
from mail_runner.relay_server.app import build_http_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.pc_control_protocol import (
    build_ingress_candidate,
    build_mailbox_lease,
    build_pc_hello,
    build_thread_binding,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import InMemorySessionStore
from mail_runner.status import BACKEND_CODEX, THREAD_STATUS_AWAITING_USER_INPUT, THREAD_STATUS_RUNNING
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, create_thread


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _pc_capabilities() -> dict[str, object]:
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


def _workspace_entry(*, repo_path: str, workdir: str | None) -> dict[str, object]:
    return {
        "workspace_id": build_workspace_id(repo_path, workdir),
        "workspace_norm": build_workspace_norm(repo_path, workdir),
        "repo_path": repo_path,
        "workdir": workdir,
        "display_name": repo_path.rstrip("\\/").split("\\")[-1].split("/")[-1],
        "source": "project_sync_roots",
        "capabilities": _pc_capabilities(),
    }


def _register_pc(runtime, *, pc_id: str) -> tuple[str, int]:
    sent_at = _now()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id=f"msg_hello:{pc_id}",
            trace_id=f"trace_hello:{pc_id}",
            pc_id=pc_id,
            sent_at=sent_at,
            display_name=pc_id,
            client_version="0.1.0",
            host_fingerprint=f"host:{pc_id}",
            runtime_fingerprint=f"runtime:{pc_id}",
            capabilities=_pc_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="relay-secret")
    return connection_id, connection_epoch


def _replace_snapshot(
    runtime,
    *,
    pc_id: str,
    connection_id: str,
    connection_epoch: int,
    workspaces: list[dict[str, object]],
    snapshot_id: str,
) -> None:
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id=f"msg_snapshot:{pc_id}:{snapshot_id}",
            trace_id=f"trace_snapshot:{pc_id}:{snapshot_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=_now(),
            snapshot_id=snapshot_id,
            workspaces=workspaces,
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None


def _commit_thread_binding(
    runtime,
    *,
    pc_id: str,
    connection_id: str,
    connection_epoch: int,
    thread_id: str,
    session_id: str,
    repo_path: str,
    workdir: str | None,
) -> None:
    sent_at = _now()
    mailbox_key = "imap://bot@example.com@imap.example.com/INBOX"
    lease_holder_id = f"runner:{pc_id}:binding"
    lease = parse_pc_control_client_message(
        build_mailbox_lease(
            message_id=f"msg_lease:{pc_id}",
            trace_id=f"trace_lease:{pc_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_lease:{pc_id}",
            operation="acquire",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_ttl_seconds=45,
            config_fingerprint="cfg_001",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
        )
    )
    lease_response = parse_pc_control_server_message(runtime.handle_mailbox_lease(lease, connection_id=connection_id))
    assert lease_response.payload["lease_status"] == "active"

    ingress = parse_pc_control_client_message(
        build_ingress_candidate(
            message_id=f"msg_ingress:{pc_id}:{thread_id}",
            trace_id=f"trace_ingress:{pc_id}:{thread_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_ingress:{pc_id}:{thread_id}",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_epoch=1,
            folder="INBOX",
            uid_validity=777,
            uid=101,
            ingress_message_id=f"<ingress:{thread_id}@example.com>",
            in_reply_to=None,
            references_hash="refs_hash_001",
            from_addr="user@example.com",
            subject="[OC] Demo",
            subject_norm="demo",
            raw_date="Thu, 27 Mar 2026 09:00:00 +0800",
            classification="new_task",
            candidate_status="ready",
        )
    )
    ingress_response = parse_pc_control_server_message(
        runtime.handle_ingress_candidate(ingress, connection_id=connection_id)
    )
    assert ingress_response.payload["decision"] == "accepted"

    binding = parse_pc_control_client_message(
        build_thread_binding(
            message_id=f"msg_binding:{pc_id}:{thread_id}",
            trace_id=f"trace_binding:{pc_id}:{thread_id}",
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            sent_at=sent_at,
            request_id=f"request_binding:{pc_id}:{thread_id}",
            mailbox_key=mailbox_key,
            lease_holder_id=lease_holder_id,
            lease_epoch=1,
            ingress_id=ingress_response.payload["ingress_id"],
            root_message_id=f"<ingress:{thread_id}@example.com>",
            thread_id=thread_id,
            session_id=session_id,
            repo_path=repo_path,
            workdir=workdir,
            subject_norm="demo",
        )
    )
    binding_response = parse_pc_control_server_message(runtime.handle_thread_binding(binding, connection_id=connection_id))
    assert binding_response.payload["binding_status"] == "committed"


def _create_session(
    task_root,
    *,
    thread_id: str,
    session_id: str | None = None,
    repo_path: str,
    workdir: str | None,
    session_name: str,
    status: str,
    updated_at: str,
    pending_questions: list[QuestionItem] | None = None,
) -> None:
    create_thread(
        thread_id=thread_id,
        root_message_id=f"<root:{thread_id}@example.com>",
        latest_message_id=f"<latest:{thread_id}@example.com>",
        subject_norm=session_name.lower().replace(" ", "-"),
        backend=BACKEND_CODEX,
        profile="default",
        permission="default",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=f"task_{thread_id}",
        last_task_snapshot_file=f"snapshots/{thread_id}.json",
        task_root=task_root,
        status=status,
        history_files=[],
        last_summary=f"summary:{thread_id}",
        lifecycle="active",
        last_active_at=updated_at,
        last_progress_at=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
        session_id=session_id or thread_id,
        session_name=session_name,
        backend_transport="sdk",
        pending_questions=list(pending_questions or []),
    )


def _get_snapshot(
    url: str,
    *,
    auth_token: str | None,
    params: dict[str, object] | None = None,
) -> requests.Response:
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return requests.get(url, headers=headers, params=params, timeout=5)


def _post_session_action(
    url: str,
    *,
    auth_token: str | None,
    payload: dict[str, object],
) -> requests.Response:
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return requests.post(url, headers=headers, json=payload, timeout=5)


def _start_command_ack_worker(
    runtime,
    *,
    expected_command_type: str,
    ack_status: str,
):
    errors: list[BaseException] = []

    def _worker() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            for record in runtime.command_store.list_commands():
                if record.command_type != expected_command_type:
                    continue
                if record.ack_status is not None:
                    return
                runtime.command_store.record_ack(
                    pc_id=record.pc_id,
                    command_id=record.command_id,
                    ack_status=ack_status,
                    ack_message_id=f"ack:{record.command_id}",
                    acked_at="2026-03-29T10:00:30",
                )
                return
            time.sleep(0.05)
        errors.append(AssertionError("timed out waiting for session-action facade command dispatch"))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread, errors


def test_android_session_snapshot_requires_dedicated_android_app_token(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="relay-secret",
            params={"thread_id": "thread_001"},
        )
        payload = response.json()

        assert response.status_code == 401
        assert payload["error_code"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_returns_waiting_snapshot_with_binding_resolved_pc_id(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\alpha"
    workdir = "main"
    question = QuestionItem(
        question_set_id="qset_branch",
        question_id="q_branch",
        question_type="single_choice",
        question_text="Which branch should I use?",
        required=True,
        choices=["main", "release"],
        choice_labels={"main": "Main", "release": "Release"},
    )
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Alpha task",
        status=THREAD_STATUS_AWAITING_USER_INPUT,
        updated_at="2026-03-27T10:01:00",
        pending_questions=[question],
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_alpha")
    _replace_snapshot(
        runtime,
        pc_id="pc_alpha",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_alpha",
    )
    _commit_thread_binding(
        runtime,
        pc_id="pc_alpha",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        thread_id="thread_001",
        session_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={
                "workspace_id": build_workspace_id(repo_path, workdir),
                "session_id": "thread_001",
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["schema_version"] == ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION
        assert payload["locator"] == {
            "pc_id": "pc_alpha",
            "workspace_id": build_workspace_id(repo_path, workdir),
            "session_id": "thread_001",
            "thread_id": "thread_001",
        }
        assert payload["session"]["status"] == "waiting_user"
        assert payload["session"]["pc_id"] == "pc_alpha"
        assert payload["session_snapshot"]["status"] == "awaiting_user_input"
        assert payload["session_snapshot"]["question_state"]["questions"][0]["question_id"] == "q_branch"
        assert payload["session_snapshot"]["timeline_items"][0]["item_type"] == "question_prompt"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_supports_thread_id_only_lookup(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\beta"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Beta task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_beta")
    _replace_snapshot(
        runtime,
        pc_id="pc_beta",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_beta",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["locator"]["workspace_id"] == workspace_id
        assert payload["locator"]["thread_id"] == "thread_001"
        assert payload["locator"]["pc_id"] == "pc_beta"
        assert payload["session_snapshot"]["status"] == "running"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_exposes_latest_session_action_command_continuity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\beta"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Beta task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_beta")
    _replace_snapshot(
        runtime,
        pc_id="pc_beta",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_beta",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ack_thread, ack_errors = _start_command_ack_worker(
        runtime,
        expected_command_type="reply",
        ack_status="accepted",
    )
    try:
        host, port = server.server_address[:2]
        action_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            auth_token="android-secret",
            payload={
                "request_id": "req_reply_001",
                "action": "reply",
                "target": {
                    "workspace_id": workspace_id,
                    "session_id": "thread_001",
                    "thread_id": "thread_001",
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
        action_payload = action_response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors
        assert action_response.status_code == 200

        snapshot_response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        snapshot_payload = snapshot_response.json()

        assert snapshot_response.status_code == 200
        latest_session_action = snapshot_payload["session_snapshot"]["latest_session_action"]
        assert latest_session_action["command_id"] == action_payload["command_id"]
        assert latest_session_action["action_type"] == "reply"
        assert latest_session_action["submit_ack"]["ack_status"] == "accepted"
        assert latest_session_action["pc_id"] == "pc_beta"
        assert "session_action_result" not in latest_session_action
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_exposes_latest_pause_command_continuity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\beta"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Beta task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_beta")
    _replace_snapshot(
        runtime,
        pc_id="pc_beta",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_beta",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ack_thread, ack_errors = _start_command_ack_worker(
        runtime,
        expected_command_type="pause",
        ack_status="accepted",
    )
    try:
        host, port = server.server_address[:2]
        action_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            auth_token="android-secret",
            payload={
                "request_id": "req_pause_001",
                "action": "pause",
                "target": {
                    "workspace_id": workspace_id,
                    "session_id": "thread_001",
                    "thread_id": "thread_001",
                },
                "pause": {},
            },
        )
        action_payload = action_response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors
        assert action_response.status_code == 200

        snapshot_response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        snapshot_payload = snapshot_response.json()

        assert snapshot_response.status_code == 200
        latest_session_action = snapshot_payload["session_snapshot"]["latest_session_action"]
        assert latest_session_action["command_id"] == action_payload["command_id"]
        assert latest_session_action["action_type"] == "pause"
        assert latest_session_action["submit_ack"]["ack_status"] == "accepted"
        assert latest_session_action["pc_id"] == "pc_beta"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_exposes_latest_answers_command_continuity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\beta"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    question = QuestionItem(
        question_set_id="qs_release",
        question_id="q_branch",
        question_type="single_choice",
        question_text="Select the release branch.",
        choices=["main", "release"],
    )
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Beta task",
        status=THREAD_STATUS_AWAITING_USER_INPUT,
        updated_at="2026-03-27T10:01:00",
        pending_questions=[question],
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_beta")
    _replace_snapshot(
        runtime,
        pc_id="pc_beta",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_beta",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ack_thread, ack_errors = _start_command_ack_worker(
        runtime,
        expected_command_type="answers",
        ack_status="accepted",
    )
    try:
        host, port = server.server_address[:2]
        action_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            auth_token="android-secret",
            payload={
                "request_id": "req_answers_001",
                "action": "answers",
                "target": {
                    "workspace_id": workspace_id,
                    "session_id": "thread_001",
                    "thread_id": "thread_001",
                },
                "answers": {
                    "question_answers": [
                        {"question_id": "q_branch", "value": "release"},
                    ]
                },
            },
        )
        action_payload = action_response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors
        assert action_response.status_code == 200

        snapshot_response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        snapshot_payload = snapshot_response.json()

        assert snapshot_response.status_code == 200
        latest_session_action = snapshot_payload["session_snapshot"]["latest_session_action"]
        assert latest_session_action["command_id"] == action_payload["command_id"]
        assert latest_session_action["action_type"] == "answers"
        assert latest_session_action["submit_ack"]["ack_status"] == "accepted"
        assert latest_session_action["pc_id"] == "pc_beta"
        assert "session_action_result" not in latest_session_action
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_exposes_latest_attachment_continuation_command_continuity(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\beta"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Beta task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_beta")
    _replace_snapshot(
        runtime,
        pc_id="pc_beta",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_beta",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    attachment_bytes = b"attachment:v1"
    ack_thread, ack_errors = _start_command_ack_worker(
        runtime,
        expected_command_type="attachment_continuation",
        ack_status="accepted",
    )
    try:
        host, port = server.server_address[:2]
        action_response = _post_session_action(
            f"http://{host}:{port}{ANDROID_SESSION_ACTION_PATH}",
            auth_token="android-secret",
            payload={
                "request_id": "req_attachment_cont_001",
                "action": "attachment_continuation",
                "target": {
                    "workspace_id": workspace_id,
                    "session_id": "thread_001",
                    "thread_id": "thread_001",
                },
                "attachment_continuation": {
                    "attachments": [
                        {
                            "name": "wireframe.png",
                            "content_type": "image/png",
                            "size_bytes": len(attachment_bytes),
                            "content_bytes_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                        }
                    ]
                },
            },
        )
        action_payload = action_response.json()
        ack_thread.join(timeout=5)
        assert not ack_errors
        assert action_response.status_code == 200

        snapshot_response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        snapshot_payload = snapshot_response.json()

        assert snapshot_response.status_code == 200
        latest_session_action = snapshot_payload["session_snapshot"]["latest_session_action"]
        assert latest_session_action["command_id"] == action_payload["command_id"]
        assert latest_session_action["action_type"] == "attachment_continuation"
        assert latest_session_action["submit_ack"]["ack_status"] == "accepted"
        assert latest_session_action["pc_id"] == "pc_beta"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_returns_null_pc_id_when_workspace_resolution_is_ambiguous(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\shared"
    workdir = "main"
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Shared task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )

    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    home_connection_id, home_connection_epoch = _register_pc(runtime, pc_id="pc_home")
    _replace_snapshot(
        runtime,
        pc_id="pc_home",
        connection_id=home_connection_id,
        connection_epoch=home_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_home",
    )
    office_connection_id, office_connection_epoch = _register_pc(runtime, pc_id="pc_office")
    _replace_snapshot(
        runtime,
        pc_id="pc_office",
        connection_id=office_connection_id,
        connection_epoch=office_connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_office",
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["locator"]["pc_id"] is None
        assert payload["session"]["pc_id"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_returns_not_found_for_unknown_session(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={
                "workspace_id": "workspace_missing",
                "session_id": "session_missing",
            },
        )
        payload = response.json()

        assert response.status_code == 404
        assert payload["error_code"] == "session_not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_supports_session_id_only_lookup(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\gamma"
    workdir = "main"
    workspace_id = build_workspace_id(repo_path, workdir)
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Gamma task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    connection_id, connection_epoch = _register_pc(runtime, pc_id="pc_gamma")
    _replace_snapshot(
        runtime,
        pc_id="pc_gamma",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
        workspaces=[_workspace_entry(repo_path=repo_path, workdir=workdir)],
        snapshot_id="snapshot_gamma",
    )
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"session_id": "thread_001"},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["locator"] == {
            "pc_id": "pc_gamma",
            "workspace_id": workspace_id,
            "session_id": "thread_001",
            "thread_id": "thread_001",
        }
        assert payload["session_snapshot"]["status"] == "running"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_returns_binding_unresolved_for_ambiguous_session_id(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _create_session(
        task_root,
        thread_id="thread_alpha",
        session_id="session_shared",
        repo_path="E:\\projects\\alpha",
        workdir="main",
        session_name="Alpha task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )
    _create_session(
        task_root,
        thread_id="thread_beta",
        session_id="session_shared",
        repo_path="E:\\projects\\beta",
        workdir="main",
        session_name="Beta task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:02:00",
    )
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"session_id": "session_shared"},
        )
        payload = response.json()

        assert response.status_code == 409
        assert payload["error_code"] == "session_binding_unresolved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_returns_identity_conflict_for_mismatched_workspace_locator(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    repo_path = "E:\\projects\\alpha"
    workdir = "main"
    _create_session(
        task_root,
        thread_id="thread_001",
        repo_path=repo_path,
        workdir=workdir,
        session_name="Alpha task",
        status=THREAD_STATUS_RUNNING,
        updated_at="2026-03-27T10:01:00",
    )
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={
                "workspace_id": "workspace_wrong",
                "session_id": "thread_001",
            },
        )
        payload = response.json()

        assert response.status_code == 409
        assert payload["error_code"] == "workspace_identity_mismatch"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_android_session_snapshot_returns_task_root_unavailable_when_unconfigured(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        android_app_token="android-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = _get_snapshot(
            f"http://{host}:{port}{ANDROID_SESSION_SNAPSHOT_PATH}",
            auth_token="android-secret",
            params={"thread_id": "thread_001"},
        )
        payload = response.json()

        assert response.status_code == 503
        assert payload["error_code"] == "task_root_unavailable"
        assert payload["retryable"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
