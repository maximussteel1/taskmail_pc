from __future__ import annotations

import json
import hashlib
import threading
import urllib.request

import requests

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
from mail_runner.relay_server.session_store import InMemorySessionStore


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
    assert len(relay._direct_packet_handlers) == 5
    assert isinstance(relay._direct_packet_handlers[0], RelayTaskMailDirectNewTaskMailBridge)
    assert isinstance(relay._direct_packet_handlers[1], RelayTaskMailDirectCurrentSessionStatusMailBridge)
    assert isinstance(relay._direct_packet_handlers[2], RelayTaskMailDirectCurrentSessionReplyMailBridge)
    assert isinstance(relay._direct_packet_handlers[3], RelayTaskMailDirectProjectSyncHandler)
    assert isinstance(relay._direct_packet_handlers[4], RelayTaskMailDirectProjectSyncMailBridge)
    assert relay._direct_packet_handlers[1]._task_root == task_root
    assert relay._direct_packet_handlers[2]._task_root == task_root

