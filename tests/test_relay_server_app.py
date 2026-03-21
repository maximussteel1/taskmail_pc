from __future__ import annotations

import json
import threading
import urllib.request

from mail_runner.relay_server.app import build_health_payload, build_http_server, build_runtime_relay
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.direct_actions import RelayTaskMailDirectNewTaskMailBridge
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
    assert "taskmail_direct_negative_hook_enabled" not in payload
    assert len(payload["auth"]["transport_token_id"]) == 12


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


def test_build_runtime_relay_enables_taskmail_direct_bridge_when_configured(tmp_path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
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

    assert relay._direct_packet_handler is not None
    assert isinstance(relay._direct_packet_handler, RelayTaskMailDirectNewTaskMailBridge)

