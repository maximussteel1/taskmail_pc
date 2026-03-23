from __future__ import annotations

import asyncio
import urllib.request

from mail_runner.outbound.relay_bootstrap import derive_healthz_url, probe_healthz, probe_relay_bootstrap
from mail_runner.outbound.contract import TransportReceipt
from mail_runner.relay_server.app import start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.session_store import PersistentSessionStore


def test_derive_healthz_url_from_plaintext_relay_url() -> None:
    assert derive_healthz_url("ws://127.0.0.1:8787/relay") == "http://127.0.0.1:8787/healthz"


def test_probe_healthz_disables_environment_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"ok","tls_enabled":false}'

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:9000")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:9001")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.example:9002")
    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    result = probe_healthz("http://relay.example.test/healthz", timeout_seconds=7)

    assert result.ok is True
    assert result.payload == {"status": "ok", "tls_enabled": False}
    proxy_handlers = [handler for handler in captured["handlers"] if isinstance(handler, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    assert captured["timeout"] == 7


def test_probe_relay_bootstrap_reports_hello_ack_for_plaintext_runtime(tmp_path) -> None:
    result = asyncio.run(
        _probe_runtime(
            tmp_path,
            relay_url_builder=lambda host, port: f"ws://{host}:{port}/relay",
            transport_token="relay-secret",
        )
    )

    assert result.success is True
    assert result.handshake_status == "hello_ack"
    assert result.health is not None
    assert result.health.ok is True
    assert result.health.payload is not None
    assert result.health.payload["tls_enabled"] is False
    assert result.connection_id is not None
    assert result.heartbeat_seconds == 30


def test_probe_relay_bootstrap_reports_unauthorized_for_invalid_token(tmp_path) -> None:
    result = asyncio.run(
        _probe_runtime(
            tmp_path,
            relay_url_builder=lambda host, port: f"ws://{host}:{port}/relay",
            transport_token="wrong-secret",
        )
    )

    assert result.success is False
    assert result.handshake_status == "unauthorized"
    assert result.error_code == "unauthorized"
    assert result.error_message == "transport token mismatch"
    assert result.health is not None
    assert result.health.ok is True


def test_probe_relay_bootstrap_classifies_scheme_mismatch_for_wss_against_plaintext_runtime(tmp_path) -> None:
    result = asyncio.run(
        _probe_runtime(
            tmp_path,
            relay_url_builder=lambda host, port: f"wss://{host}:{port}/relay",
            transport_token="relay-secret",
            verify_tls=False,
        )
    )

    assert result.success is False
    assert result.handshake_status == "scheme_mismatch"
    assert result.health is not None
    assert result.health.error_type in {"SSLError", None}


async def _probe_runtime(
    tmp_path,
    *,
    relay_url_builder,
    transport_token: str,
    verify_tls: bool = True,
):
    state_dir = tmp_path / "relay_state"
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    relay = LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        delivery_callback=lambda packet: TransportReceipt(
            success=True,
            transport_name="relay_smtp",
            sent_at="2026-03-21T11:30:00",
            transport_message_id="<relay-bootstrap@example.com>",
        ),
        clock=lambda: "2026-03-21T11:30:00",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        return await asyncio.to_thread(
            lambda: probe_relay_bootstrap(
                relay_url=relay_url_builder(host, port),
                transport_token=transport_token,
                client_id="pc-bootstrap-probe",
                client_version="0.1.0",
                timeout_seconds=5,
                verify_tls=verify_tls,
            )
        )
    finally:
        server.close()
        await server.wait_closed()
