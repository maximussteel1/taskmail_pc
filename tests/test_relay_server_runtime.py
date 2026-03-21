from __future__ import annotations

import asyncio
import json
import urllib.request

from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket, TransportReceipt
from mail_runner.outbound.relay_transport import RelayTransport
from mail_runner.relay_server.app import start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.session_store import PersistentSessionStore


def test_relay_transport_sends_packet_to_websocket_runtime_and_healthz(tmp_path) -> None:
    asyncio.run(_run_websocket_runtime_test(tmp_path))


async def _run_websocket_runtime_test(tmp_path) -> None:
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
            sent_at="2026-03-20T16:30:00",
            transport_message_id="<relay-runtime@example.com>",
        ),
        clock=lambda: "2026-03-20T16:30:00",
    )
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
    )
    try:
        host, port = server.sockets[0].getsockname()[:2]
        payload, status = await asyncio.to_thread(_fetch_healthz, host, port)
        assert status == 200
        assert payload["status"] == "ok"
        assert payload["packet_count"] == 0

        request = OutboundDispatchRequest(
            packet=TaskRunPacket(
                packet_id="packet:remote:001",
                task_id="task_001",
                created_at="2026-03-20T16:30:00",
                message_kind="status_update",
                content_format="text/plain+text/html",
                html="<html><body>Done.</body></html>",
                text_fallback="Status: DONE\n",
                state_patch={"thread_id": "thread_001"},
                client_trace_id="task_001",
            ),
            to_addr="user@example.com",
            subject="[DONE][S:thread_001] Demo task",
        )

        receipt = await asyncio.to_thread(
            lambda: RelayTransport(
                relay_url=f"ws://{host}:{port}/relay",
                transport_token="relay-secret",
                client_id="pc-001",
                client_version="0.1.0",
            ).send(request)
        )

        stored_packet = packet_store.get_packet("packet:remote:001")
        assert receipt.success is True
        assert receipt.transport_message_id == "<relay-runtime@example.com>"
        assert stored_packet is not None
        assert stored_packet.delivery_status == "delivered"
        assert stored_packet.transport_message_id == "<relay-runtime@example.com>"
    finally:
        server.close()
        await server.wait_closed()


def _fetch_healthz(host: str, port: int) -> tuple[dict, int]:
    with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=5) as response:
        return json.loads(response.read().decode("utf-8")), response.status
