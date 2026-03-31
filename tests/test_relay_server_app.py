from __future__ import annotations

import json
import hashlib
import threading
import urllib.request
from datetime import datetime

import requests

from mail_runner.config import AppConfig
from mail_runner.relay_server.app import build_health_payload, build_http_server, build_runtime_relay
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.direct_actions import (
    RelayTaskMailDirectNewTaskMailBridge,
    RelayTaskMailDirectProjectSyncHandler,
    RelayTaskMailDirectProjectSyncMailBridge,
)
from mail_runner.relay_server.post_creation_actions import (
    RelayTaskMailDirectCurrentSessionReplyMailBridge,
    RelayTaskMailDirectCurrentSessionStatusMailBridge,
)
from mail_runner.relay_server.packet_store import InMemoryAcceptedPacketStore
from mail_runner.relay_server.pc_control_protocol import (
    build_command_dispatch,
    build_ingress_candidate,
    build_mailbox_lease,
    build_pc_hello,
    build_terminal_outcome,
    build_thread_binding,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import InMemorySessionStore
from mail_runner.relay_server.transport_probe import RelayTaskMailTransportProbeHandler


def _pc_control_capabilities() -> dict[str, object]:
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


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def test_build_health_payload_reports_server_shape() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
    )
    store = InMemorySessionStore()

    payload = build_health_payload(config, store, listening_host="127.0.0.1", listening_port=9000)

    assert payload["status"] == "ok"
    assert payload["service"] == "mail-runner-relay"
    assert payload["listen"] == {"host": "127.0.0.1", "port": 9000}
    assert payload["session_count"] == 0
    assert payload["action_logging_enabled"] is True
    assert payload["taskmail_direct_ingress_enabled"] is False
    assert payload["task_root"] == {
        "configured_path": None,
        "exists": False,
        "is_dir": False,
        "scheduler_present": False,
        "thread_count": 0,
    }
    assert "taskmail_direct_negative_hook_enabled" not in payload
    assert len(payload["auth"]["transport_token_id"]) == 12


def test_build_health_payload_reports_task_root_diagnostics(tmp_path) -> None:
    task_root = tmp_path / "shared_task_root"
    (task_root / "_scheduler").mkdir(parents=True)
    (task_root / "thread_001").mkdir()
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
        task_root=str(task_root),
    )

    payload = build_health_payload(config, InMemorySessionStore())

    assert payload["task_root"] == {
        "configured_path": str(task_root),
        "exists": True,
        "is_dir": True,
        "scheduler_present": True,
        "thread_count": 1,
    }


def test_build_health_payload_reports_action_logging_disabled_when_configured() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
        action_log_enabled=False,
    )

    payload = build_health_payload(config, InMemorySessionStore())

    assert payload["action_logging_enabled"] is False


def test_build_health_payload_reports_direct_ingress_disabled_in_vps_only_mode() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )

    payload = build_health_payload(
        config,
        InMemorySessionStore(),
        runner_config=AppConfig(control_plane_mode="vps_only"),
    )

    assert payload["taskmail_direct_ingress_enabled"] is False


def test_http_server_exposes_healthz_json() -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
    )
    store = InMemorySessionStore()
    server = build_http_server(config, session_store=store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["listen"]["port"] == port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_health_reports_direct_ingress_disabled_in_vps_only_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mail_runner.relay_server.app.load_config", lambda: AppConfig(control_plane_mode="vps_only"))
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["taskmail_direct_ingress_enabled"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_file_surface_upload_roundtrip(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        content = b"\x89PNG\r\n\x1a\nchart"
        digest = hashlib.sha256(content).hexdigest()
        metadata = {
            "artifact_id": "artifact-chart",
            "name": "chart.png",
            "kind": "image",
            "role": "attachment",
            "mime_type": "image/png",
            "byte_size": len(content),
            "sha256": digest,
        }
        response = requests.post(
            f"http://{host}:{port}/v1/files",
            headers={"Authorization": "Bearer secret-token"},
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "file": ("chart.png", content, "image/png"),
            },
            timeout=5,
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["schema_version"] == "taskmail-control-artifact-contract-v1"
        assert payload["artifact"]["artifact_id"] == "artifact-chart"
        assert payload["artifact"]["file_id"] == payload["file_id"]
        assert payload["artifact"]["sha256"] == digest

        metadata_response = requests.get(
            f"http://{host}:{port}{payload['artifact']['metadata_url']}",
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )
        assert metadata_response.status_code == 200
        assert metadata_response.json()["artifact"]["download_url"] == payload["artifact"]["download_url"]

        content_response = requests.get(
            f"http://{host}:{port}{payload['artifact']['download_url']}",
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )
        assert content_response.status_code == 200
        assert content_response.content == content
        assert content_response.headers["Content-Type"] == "image/png"
        assert content_response.headers["ETag"] == digest
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_file_surface_rejects_missing_transport_token(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        content = b"abc"
        digest = hashlib.sha256(content).hexdigest()
        metadata = {
            "name": "demo.txt",
            "kind": "file",
            "role": "attachment",
            "mime_type": "text/plain",
            "byte_size": len(content),
            "sha256": digest,
        }
        response = requests.post(
            f"http://{host}:{port}/v1/files",
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "file": ("demo.txt", content, "text/plain"),
            },
            timeout=5,
        )
        payload = response.json()

        assert response.status_code == 401
        assert payload["error_code"] == "unauthorized"
        assert payload["retryable"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_pc_control_operator_dispatch_enqueues_command(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    now = _now()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="hello_001",
            trace_id="trace_hello_001",
            pc_id="pc-home",
            sent_at=now,
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_pc_control_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="secret-token")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="snapshot_001",
            trace_id="trace_snapshot_001",
            pc_id="pc-home",
            connection_epoch=connection_epoch,
            sent_at=now,
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "e:/projects/mail_based_task_manager",
                    "repo_path": "E:\\projects\\mail_based_task_manager",
                    "workdir": None,
                    "display_name": "mail_based_task_manager",
                    "source": "project_sync_roots",
                    "capabilities": _pc_control_capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = requests.post(
            f"http://{host}:{port}/debug/pc-control/dispatch",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "pc_id": "pc-home",
                "workspace_id": "workspace_001",
                "command_id": "cmd_001",
                "command_type": "status",
                "session_id": "thread_001",
                "execution_policy": {
                    "backend": "codex",
                    "profile": "default",
                    "permission": "default",
                    "backend_transport": "sdk",
                },
                "payload": {},
            },
            timeout=5,
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["status"] == "accepted"
        assert payload["command"]["command_id"] == "cmd_001"
        assert payload["record"]["status"] == "queued"

        record = runtime.command_store.get_command("pc-home", "cmd_001")
        assert record is not None
        pending = runtime.collect_pending_dispatches(
            pc_id="pc-home",
            connection_id=connection_id,
            connection_epoch=connection_epoch,
        )
        assert len(pending) == 1
        parsed_dispatch = parse_pc_control_server_message(pending[0])
        assert parsed_dispatch.payload["command_id"] == "cmd_001"
        assert parsed_dispatch.payload["workspace_id"] == "workspace_001"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_pc_control_operator_read_side_lists_nodes_workspaces_and_commands(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    now = _now()
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="hello_001",
            trace_id="trace_hello_001",
            pc_id="pc-home",
            sent_at=now,
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_pc_control_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="secret-token")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="snapshot_001",
            trace_id="trace_snapshot_001",
            pc_id="pc-home",
            connection_epoch=connection_epoch,
            sent_at=now,
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "e:/projects/mail_based_task_manager",
                    "repo_path": "E:\\projects\\mail_based_task_manager",
                    "workdir": None,
                    "display_name": "mail_based_task_manager",
                    "source": "project_sync_roots",
                    "capabilities": _pc_control_capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None
    runtime.enqueue_command(
        parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_cmd_001",
                trace_id="trace_cmd_001",
                pc_id="pc-home",
                connection_epoch=connection_epoch,
                sent_at=now,
                command_id="cmd_001",
                command_type="status",
                workspace_id="workspace_001",
                session_id="thread_001",
                execution_policy={
                    "backend": "codex",
                    "profile": "default",
                    "permission": "default",
                    "backend_transport": "sdk",
                },
                command_payload={},
            )
        )
    )

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        nodes_response = requests.get(
            f"http://{host}:{port}/debug/pc-control/nodes",
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )
        workspaces_response = requests.get(
            f"http://{host}:{port}/debug/pc-control/workspaces?pc_id=pc-home",
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )
        commands_response = requests.get(
            f"http://{host}:{port}/debug/pc-control/commands?pc_id=pc-home",
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )
        command_detail_response = requests.get(
            f"http://{host}:{port}/debug/pc-control/commands?pc_id=pc-home&command_id=cmd_001",
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )

        nodes_payload = nodes_response.json()
        workspaces_payload = workspaces_response.json()
        commands_payload = commands_response.json()
        command_detail_payload = command_detail_response.json()

        assert nodes_response.status_code == 200
        assert nodes_payload["nodes"][0]["pc_id"] == "pc-home"
        assert workspaces_response.status_code == 200
        assert workspaces_payload["workspaces"][0]["workspace_id"] == "workspace_001"
        assert commands_response.status_code == 200
        assert commands_payload["commands"][0]["command_id"] == "cmd_001"
        assert command_detail_response.status_code == 200
        assert command_detail_payload["command"]["command_id"] == "cmd_001"
        assert command_detail_payload["commands"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_pc_control_operator_read_side_exposes_lease_ingress_and_terminal_outcome(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    runtime = build_pc_control_runtime(config)
    now = _now()
    mailbox_key = "imap://bot@example.com@imap.example.com/INBOX"
    hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="hello_001",
            trace_id="trace_hello_001",
            pc_id="pc-home",
            sent_at=now,
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_pc_control_capabilities(),
        )
    )
    _response, connection_id, connection_epoch = runtime.handle_hello(hello, provided_token="secret-token")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="snapshot_001",
            trace_id="trace_snapshot_001",
            pc_id="pc-home",
            connection_epoch=connection_epoch,
            sent_at=now,
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "e:/projects/mail_based_task_manager",
                    "repo_path": "E:\\projects\\mail_based_task_manager",
                    "workdir": None,
                    "display_name": "mail_based_task_manager",
                    "source": "project_sync_roots",
                    "capabilities": _pc_control_capabilities(),
                }
            ],
    )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None
    lease_sent_at = _now()
    ingress_sent_at = _now()
    binding_sent_at = _now()
    outcome_sent_at = _now()

    lease_response = parse_pc_control_server_message(
        runtime.handle_mailbox_lease(
            parse_pc_control_client_message(
                build_mailbox_lease(
                    message_id="msg_lease_001",
                    trace_id="trace_lease_001",
                    pc_id="pc-home",
                    connection_epoch=connection_epoch,
                    sent_at=lease_sent_at,
                    request_id="request_lease_001",
                    operation="acquire",
                    mailbox_key=mailbox_key,
                    lease_holder_id="runner:pc-home:abc123",
                    lease_ttl_seconds=45,
                    config_fingerprint="cfg_001",
                    host_fingerprint="host_001",
                    runtime_fingerprint="runtime_001",
                )
            ),
            connection_id=connection_id,
        )
    )
    assert lease_response.payload["lease_status"] == "active"

    ingress_response = parse_pc_control_server_message(
        runtime.handle_ingress_candidate(
            parse_pc_control_client_message(
                build_ingress_candidate(
                    message_id="msg_ingress_001",
                    trace_id="trace_ingress_001",
                    pc_id="pc-home",
                    connection_epoch=connection_epoch,
                    sent_at=ingress_sent_at,
                    request_id="request_ingress_001",
                    mailbox_key=mailbox_key,
                    lease_holder_id="runner:pc-home:abc123",
                    lease_epoch=1,
                    folder="INBOX",
                    uid_validity=777,
                    uid=101,
                    ingress_message_id="<ingress@example.com>",
                    in_reply_to=None,
                    references_hash="refs_hash_001",
                    from_addr="user@example.com",
                    subject="[OC] Demo",
                    subject_norm="demo",
                    raw_date="Thu, 27 Mar 2026 09:00:00 +0800",
                    classification="new_task",
                    candidate_status="ready",
                    taskmail_request_id="req_ingress_001",
                    packet_id="packet_ingress_001",
                )
            ),
            connection_id=connection_id,
        )
    )
    assert ingress_response.payload["decision"] == "accepted"

    binding_response = parse_pc_control_server_message(
        runtime.handle_thread_binding(
            parse_pc_control_client_message(
                build_thread_binding(
                    message_id="msg_binding_001",
                    trace_id="trace_binding_001",
                    pc_id="pc-home",
                    connection_epoch=connection_epoch,
                    sent_at=binding_sent_at,
                    request_id="request_binding_001",
                    mailbox_key=mailbox_key,
                    lease_holder_id="runner:pc-home:abc123",
                    lease_epoch=1,
                    ingress_id=ingress_response.payload["ingress_id"],
                    root_message_id="<ingress@example.com>",
                    thread_id="thread_001",
                    session_id="thread_001",
                    repo_path="E:\\projects\\mail_based_task_manager",
                    workdir=None,
                    subject_norm="demo",
                )
            ),
            connection_id=connection_id,
        )
    )
    assert binding_response.payload["binding_status"] == "committed"

    outcome_response = parse_pc_control_server_message(
        runtime.handle_terminal_outcome(
            parse_pc_control_client_message(
                build_terminal_outcome(
                    message_id="msg_outcome_001",
                    trace_id="trace_outcome_001",
                    pc_id="pc-home",
                    connection_epoch=connection_epoch,
                    sent_at=outcome_sent_at,
                    request_id="request_outcome_001",
                    mailbox_key=mailbox_key,
                    lease_holder_id="runner:pc-home:abc123",
                    lease_epoch=1,
                    thread_id="thread_001",
                    task_id="task_001",
                    run_status="success",
                    generated_at=outcome_sent_at,
                    last_summary="done",
                    terminal_mail_message_id="<done@example.com>",
                    terminal_mail_subject="[DONE][S:thread_001] Demo",
                    taskmail_request_id="req_ingress_001",
                    packet_id="packet_ingress_001",
                    source_ingress_id=ingress_response.payload["ingress_id"],
                )
            ),
            connection_id=connection_id,
        )
    )
    assert outcome_response.payload["outcome_status"] == "committed"

    server = build_http_server(config, session_store=InMemorySessionStore(), pc_control_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        lease_http = requests.get(
            f"http://{host}:{port}/debug/pc-control/lease",
            headers={"Authorization": "Bearer secret-token"},
            params={"mailbox_key": mailbox_key},
            timeout=5,
        )
        ingress_http = requests.get(
            f"http://{host}:{port}/debug/pc-control/ingress",
            headers={"Authorization": "Bearer secret-token"},
            params={"ingress_id": ingress_response.payload["ingress_id"]},
            timeout=5,
        )
        outcome_http = requests.get(
            f"http://{host}:{port}/debug/pc-control/terminal-outcome",
            headers={"Authorization": "Bearer secret-token"},
            params={"thread_id": "thread_001"},
            timeout=5,
        )

        lease_payload = lease_http.json()
        ingress_payload = ingress_http.json()
        outcome_payload = outcome_http.json()

        assert lease_http.status_code == 200
        assert lease_payload["lease"]["mailbox_key"] == mailbox_key
        assert lease_payload["lease"]["status"] == "active"
        assert lease_payload["lease_events"][0]["operation"] == "acquired"
        assert ingress_http.status_code == 200
        assert ingress_payload["ingress"]["message_id"] == "<ingress@example.com>"
        assert ingress_payload["ingress"]["thread_id"] == "thread_001"
        assert ingress_payload["ingress"]["taskmail_request_id"] == "req_ingress_001"
        assert outcome_http.status_code == 200
        assert outcome_payload["terminal_outcome"]["thread_id"] == "thread_001"
        assert outcome_payload["terminal_outcome"]["source_ingress_id"] == ingress_response.payload["ingress_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_file_surface_returns_payload_too_large_error(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(
        config,
        session_store=InMemorySessionStore(),
        file_upload_limit_bytes=4,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        content = b"abcdef"
        digest = hashlib.sha256(content).hexdigest()
        metadata = {
            "artifact_id": "artifact-demo",
            "name": "demo.txt",
            "kind": "file",
            "role": "attachment",
            "mime_type": "text/plain",
            "byte_size": len(content),
            "sha256": digest,
        }
        response = requests.post(
            f"http://{host}:{port}/v1/files",
            headers={"Authorization": "Bearer secret-token"},
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "file": ("demo.txt", content, "text/plain"),
            },
            timeout=5,
        )
        payload = response.json()

        assert response.status_code == 413
        assert payload["error_code"] == "payload_too_large"
        assert payload["artifact_id"] == "artifact-demo"
        assert payload["max_bytes"] == 4
        assert payload["observed_bytes"] == len(content)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_build_runtime_relay_enables_taskmail_direct_bridge_when_configured(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    relay = build_runtime_relay(
        config,
        session_store=InMemorySessionStore(),
        packet_store=InMemoryAcceptedPacketStore(),
    )

    assert relay._direct_packet_handler is None
    assert len(relay._direct_packet_handlers) == 6
    assert isinstance(relay._direct_packet_handlers[0], RelayTaskMailDirectNewTaskMailBridge)
    assert isinstance(relay._direct_packet_handlers[1], RelayTaskMailDirectCurrentSessionStatusMailBridge)
    assert isinstance(relay._direct_packet_handlers[2], RelayTaskMailDirectCurrentSessionReplyMailBridge)
    assert isinstance(relay._direct_packet_handlers[3], RelayTaskMailDirectProjectSyncHandler)
    assert isinstance(relay._direct_packet_handlers[4], RelayTaskMailDirectProjectSyncMailBridge)
    assert isinstance(relay._direct_packet_handlers[5], RelayTaskMailTransportProbeHandler)
    assert relay._direct_packet_handlers[1]._task_root == task_root
    assert relay._direct_packet_handlers[2]._task_root == task_root


def test_build_runtime_relay_disables_mail_bridge_handlers_in_vps_only_mode(tmp_path, monkeypatch) -> None:
    task_root = tmp_path / "tasks"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
        task_root=str(task_root),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    monkeypatch.setattr("mail_runner.relay_server.app.load_config", lambda: AppConfig(control_plane_mode="vps_only"))

    relay = build_runtime_relay(
        config,
        session_store=InMemorySessionStore(),
        packet_store=InMemoryAcceptedPacketStore(),
    )

    assert relay._direct_packet_handler is None
    assert len(relay._direct_packet_handlers) == 1
    assert isinstance(relay._direct_packet_handlers[0], RelayTaskMailDirectProjectSyncHandler)

