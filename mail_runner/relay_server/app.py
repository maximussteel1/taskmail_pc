"""Runnable relay server with health and WebSocket transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import websockets

from .auth import token_fingerprint
from .config import RelayServerConfig, load_relay_server_config
from .delivery import RelayPacketDeliverer
from .direct_actions import RelayTaskMailDirectNewTaskMailBridge
from .loopback import LoopbackRelayServer
from .packet_store import InMemoryAcceptedPacketStore, PersistentAcceptedPacketStore
from .post_creation_actions import (
    RelayTaskMailDirectCurrentSessionReplyMailBridge,
    RelayTaskMailDirectCurrentSessionStatusMailBridge,
)
from .session_store import InMemorySessionStore, PersistentSessionStore

LOGGER = logging.getLogger(__name__)
_HTTP_RESPONSE_HEADERS = [("Content-Type", "application/json; charset=utf-8")]


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def build_health_payload(
    config: RelayServerConfig,
    session_store,
    *,
    packet_store=None,
    listening_host: str | None = None,
    listening_port: int | None = None,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": config.server_name,
        "listen": {
            "host": listening_host or config.host,
            "port": listening_port if listening_port is not None else config.port,
        },
        "session_count": session_store.count(),
        "packet_count": packet_store.count() if packet_store is not None else 0,
        "state_dir": config.state_dir,
        "tls_enabled": bool(config.tls_certfile),
        "taskmail_direct_ingress_enabled": config.taskmail_direct_ingress_enabled,
        "auth": {
            "transport_token_id": token_fingerprint(config.transport_token),
        },
    }


def build_runtime_relay(
    config: RelayServerConfig,
    *,
    session_store,
    packet_store,
) -> LoopbackRelayServer:
    deliverer = RelayPacketDeliverer(config)
    direct_packet_handlers: list[Any] = []
    if config.taskmail_direct_ingress_enabled:
        direct_packet_handlers = [
            RelayTaskMailDirectNewTaskMailBridge(config),
            RelayTaskMailDirectCurrentSessionStatusMailBridge(config, task_root=config.task_root or None),
            RelayTaskMailDirectCurrentSessionReplyMailBridge(config, task_root=config.task_root or None),
        ]
    return LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        delivery_callback=deliverer.deliver,
        direct_packet_handlers=direct_packet_handlers,
    )


def build_http_server(
    config: RelayServerConfig,
    *,
    session_store: InMemorySessionStore | None = None,
    packet_store: InMemoryAcceptedPacketStore | None = None,
) -> ThreadingHTTPServer:
    store = session_store or InMemorySessionStore()
    packets = packet_store or InMemoryAcceptedPacketStore()

    class RelayRequestHandler(BaseHTTPRequestHandler):
        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/healthz", "/readyz"}:
                self._write_json(
                    404,
                    {
                        "status": "not_found",
                        "path": self.path,
                    },
                )
                return
            host, port = self.server.server_address[:2]
            self._write_json(
                200,
                build_health_payload(
                    config,
                    store,
                    packet_store=packets,
                    listening_host=str(host),
                    listening_port=int(port),
                ),
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            LOGGER.debug("relay_server http: " + format, *args)

    return ThreadingHTTPServer((config.host, config.port), RelayRequestHandler)


def _extract_bearer_token(request_headers: Any) -> str:
    raw_auth = ""
    if hasattr(request_headers, "get"):
        raw_auth = str(request_headers.get("Authorization", "") or "")
    if not raw_auth.lower().startswith("bearer "):
        return ""
    return raw_auth[7:].strip()


def _build_ssl_context(config: RelayServerConfig) -> ssl.SSLContext | None:
    if not config.tls_certfile or not config.tls_keyfile:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.tls_certfile, keyfile=config.tls_keyfile)
    return context


def _json_response(status: HTTPStatus, payload: dict[str, Any]) -> tuple[HTTPStatus, list[tuple[str, str]], bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [*_HTTP_RESPONSE_HEADERS, ("Content-Length", str(len(body)))]
    return status, headers, body


async def _process_request(path: str, _request_headers: Any, *, config: RelayServerConfig, session_store, packet_store):
    if path not in {"/healthz", "/readyz"}:
        return None
    return _json_response(
        HTTPStatus.OK,
        build_health_payload(config, session_store, packet_store=packet_store),
    )


async def _websocket_handler(websocket, path: str, *, relay: LoopbackRelayServer) -> None:
    normalized_path = str(path or "").strip() or "/"
    if normalized_path != "/relay":
        await websocket.close(code=1008, reason="unsupported_path")
        return

    provided_token = _extract_bearer_token(getattr(websocket, "request_headers", {}))
    connection_id: str | None = None
    send_lock = asyncio.Lock()
    subscription_push_task: asyncio.Task | None = None

    async def _send_payload(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    try:
        async for raw_message in websocket:
            now = _timestamp()
            try:
                payload = json.loads(raw_message)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
            except Exception as exc:
                responses = [
                    {
                        "message_type": "error",
                        "code": "invalid_json",
                        "message": str(exc),
                        "sent_at": now,
                    }
                ]
            else:
                responses = relay.handle_client_message_batch(
                    payload,
                    provided_token=provided_token if connection_id is None else None,
                    connection_id=connection_id,
                )
            if not responses:
                responses = [
                    {
                        "message_type": "error",
                        "code": "server_error",
                        "message": "relay produced no response",
                        "sent_at": now,
                    }
                ]
            first_response = responses[0]
            for response in responses:
                if response.get("message_type") == "hello_ack":
                    connection_id = str(response.get("connection_id") or "").strip() or None
                    if connection_id and subscription_push_task is None:
                        subscription_push_task = asyncio.create_task(
                            _subscription_push_loop(
                                websocket,
                                relay=relay,
                                connection_id=connection_id,
                                send_payload=_send_payload,
                            )
                        )
                await _send_payload(response)
            if first_response.get("message_type") == "error" and connection_id is None:
                await websocket.close(code=1008, reason=str(first_response.get("code") or "error"))
                return
    finally:
        if subscription_push_task is not None:
            subscription_push_task.cancel()
            try:
                await subscription_push_task
            except asyncio.CancelledError:
                pass
        if connection_id:
            relay.clear_subscription_runtime_state(connection_id)
            relay.session_store.close_session(connection_id, closed_at=_timestamp())


async def _subscription_push_loop(
    websocket,
    *,
    relay: LoopbackRelayServer,
    connection_id: str,
    send_payload,
) -> None:
    try:
        while True:
            await asyncio.sleep(relay.phase3_broadcast_interval_seconds)
            responses = relay.collect_subscription_updates(connection_id)
            for response in responses:
                await send_payload(response)
    except asyncio.CancelledError:
        raise
    except websockets.ConnectionClosed:
        return
    except Exception:
        LOGGER.exception("relay subscription push loop crashed connection_id=%s", connection_id)


async def run_relay_server(
    config: RelayServerConfig,
    *,
    session_store: PersistentSessionStore | None = None,
    packet_store: PersistentAcceptedPacketStore | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    sessions = session_store or PersistentSessionStore(config.state_dir)
    packets = packet_store or PersistentAcceptedPacketStore(config.state_dir)
    relay = build_runtime_relay(
        config,
        session_store=sessions,
        packet_store=packets,
    )
    ssl_context = _build_ssl_context(config)
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=sessions,
        packet_store=packets,
        ssl_context=ssl_context,
    )
    try:
        LOGGER.info(
            "Relay server listening on %s:%s transport=%s",
            config.host,
            config.port,
            "wss" if ssl_context else "ws",
        )
        if shutdown_event is None:
            await asyncio.Future()
        else:
            await shutdown_event.wait()
    finally:
        server.close()
        await server.wait_closed()


async def start_relay_server(
    config: RelayServerConfig,
    *,
    relay: LoopbackRelayServer,
    session_store,
    packet_store,
    ssl_context: ssl.SSLContext | None = None,
):
    return await websockets.serve(
        lambda websocket, path: _websocket_handler(websocket, path, relay=relay),
        config.host,
        config.port,
        process_request=lambda path, request_headers: _process_request(
            path,
            request_headers,
            config=config,
            session_store=session_store,
            packet_store=packet_store,
        ),
        ssl=ssl_context,
        ping_interval=30,
        ping_timeout=30,
        max_size=32 * 1024 * 1024,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the mail-runner relay server.")
    parser.add_argument("--host", default=None, help="Bind host; defaults to MAIL_RELAY_HOST or 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help="Bind port; defaults to MAIL_RELAY_PORT or 8787.")
    parser.add_argument("--transport-token", default=None, help="Required relay transport token.")
    parser.add_argument("--state-dir", default=None, help="Persistent state directory for sessions and packets.")
    parser.add_argument("--smtp-host", default=None, help="SMTP host used by the relay for user-facing delivery.")
    parser.add_argument("--smtp-port", type=int, default=None, help="SMTP port used by the relay.")
    parser.add_argument("--smtp-user", default=None, help="SMTP username used by the relay.")
    parser.add_argument("--smtp-password", default=None, help="SMTP password used by the relay.")
    parser.add_argument("--from-name", default=None, help="From display name for relay-sent mail.")
    parser.add_argument("--from-addr", default=None, help="From email address for relay-sent mail.")
    parser.add_argument(
        "--taskmail-bot-mailbox-addr",
        default=None,
        help="Bot mailbox address used for TaskMail direct new_task bridge ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-from-name",
        default=None,
        help="From display name used when the relay bridges direct TaskMail packets into bot mailbox mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-from-addr",
        default=None,
        help="From email address used when the relay bridges direct TaskMail packets into bot mailbox mail ingress.",
    )
    parser.add_argument("--tls-certfile", default=None, help="TLS certificate path for WSS/HTTPS.")
    parser.add_argument("--tls-keyfile", default=None, help="TLS private key path for WSS/HTTPS.")
    parser.add_argument("--log-level", default=None, help="Log level; defaults to MAIL_RELAY_LOG_LEVEL or INFO.")
    parser.add_argument("--server-name", default=None, help="Server display name; defaults to mail-runner-relay.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_relay_server_config(
        host=args.host,
        port=args.port,
        transport_token=args.transport_token,
        state_dir=args.state_dir,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_user=args.smtp_user,
        smtp_password=args.smtp_password,
        from_name=args.from_name,
        from_addr=args.from_addr,
        taskmail_bot_mailbox_addr=args.taskmail_bot_mailbox_addr,
        taskmail_direct_from_name=args.taskmail_direct_from_name,
        taskmail_direct_from_addr=args.taskmail_direct_from_addr,
        tls_certfile=args.tls_certfile,
        tls_keyfile=args.tls_keyfile,
        log_level=args.log_level,
        server_name=args.server_name,
    )
    configure_logging(config.log_level)
    try:
        asyncio.run(run_relay_server(config))
    except KeyboardInterrupt:
        LOGGER.info("Relay server interrupted by operator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
