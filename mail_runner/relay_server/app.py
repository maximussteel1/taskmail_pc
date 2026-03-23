"""Runnable relay server with health and WebSocket transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
import threading
from datetime import datetime
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets

from ..config import load_config
from ..file_surface import FileSurfaceStore, FileSurfaceUploadError
from .auth import token_fingerprint, validate_transport_token
from .config import RelayServerConfig, load_relay_server_config
from .delivery import RelayPacketDeliverer
from .direct_actions import (
    RelayTaskMailDirectNewTaskMailBridge,
    RelayTaskMailDirectProjectSyncHandler,
    RelayTaskMailDirectProjectSyncMailBridge,
)
from .loopback import LoopbackRelayServer
from .packet_store import InMemoryAcceptedPacketStore, PersistentAcceptedPacketStore
from .post_creation_actions import (
    RelayTaskMailDirectCurrentSessionReplyMailBridge,
    RelayTaskMailDirectCurrentSessionStatusMailBridge,
)
from .session_store import InMemorySessionStore, PersistentSessionStore

LOGGER = logging.getLogger(__name__)
_HTTP_RESPONSE_HEADERS = [("Content-Type", "application/json; charset=utf-8")]
_HTTP_HEADER_LIMIT_BYTES = 64 * 1024


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _task_root_diagnostics(task_root: str | None) -> dict[str, Any]:
    configured_path = str(task_root or "").strip() or None
    if configured_path is None:
        return {
            "configured_path": None,
            "exists": False,
            "is_dir": False,
            "scheduler_present": False,
            "thread_count": 0,
        }

    path = Path(configured_path)
    exists = path.exists()
    is_dir = path.is_dir() if exists else False
    scheduler_present = (path / "_scheduler").is_dir() if is_dir else False
    thread_count = 0
    if is_dir:
        try:
            thread_count = sum(1 for item in path.iterdir() if item.is_dir() and item.name.startswith("thread_"))
        except OSError:
            thread_count = 0
    return {
        "configured_path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "scheduler_present": scheduler_present,
        "thread_count": thread_count,
    }


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
        "task_root": _task_root_diagnostics(config.task_root or None),
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
        if str(config.task_root or "").strip():
            direct_packet_handlers.append(RelayTaskMailDirectProjectSyncHandler(config=load_config()))
        direct_packet_handlers.append(RelayTaskMailDirectProjectSyncMailBridge(config))
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
    file_upload_limit_bytes: int | None = None,
) -> ThreadingHTTPServer:
    store = session_store or InMemorySessionStore()
    packets = packet_store or InMemoryAcceptedPacketStore()
    file_store = FileSurfaceStore(
        config.state_dir,
        upload_limit_bytes=file_upload_limit_bytes if file_upload_limit_bytes is not None else 32 * 1024 * 1024,
    )

    class RelayRequestHandler(BaseHTTPRequestHandler):
        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_bytes(self, status_code: int, payload: bytes, *, content_type: str, etag: str | None = None) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "private, max-age=3600")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(payload)

        def _write_upload_error(self, error: FileSurfaceUploadError) -> None:
            self._write_json(error.status_code, error.to_response_payload())

        def _require_transport_token(self) -> bool:
            provided_token = _extract_bearer_token(self.headers)
            if validate_transport_token(provided_token, config.transport_token):
                return True
            self._write_upload_error(
                FileSurfaceUploadError(
                    status_code=401,
                    error_code="unauthorized",
                    error_message="transport token mismatch",
                    retryable=False,
                )
            )
            return False

        def _normalized_path(self) -> str:
            return urlparse(self.path).path

        def do_GET(self) -> None:  # noqa: N802
            path = self._normalized_path()
            if path in {"/healthz", "/readyz"}:
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
                return
            if path.startswith("/v1/files/"):
                if not self._require_transport_token():
                    return
                remainder = path[len("/v1/files/") :]
                if remainder.endswith("/content"):
                    file_id = remainder[: -len("/content")].strip("/")
                    record = file_store.get_content(file_id)
                    if record is None:
                        self._write_json(
                            404,
                            {
                                "status": "error",
                                "error_code": "not_found",
                                "error_message": f"file_id not found: {file_id}",
                                "retryable": False,
                            },
                        )
                        return
                    metadata, content = record
                    artifact = metadata.get("artifact") or {}
                    self._write_bytes(
                        200,
                        content,
                        content_type=str(artifact.get("mime_type") or "application/octet-stream"),
                        etag=str(artifact.get("sha256") or "").strip() or None,
                    )
                    return
                file_id = remainder.strip("/")
                metadata = file_store.get_metadata(file_id)
                if metadata is None:
                    self._write_json(
                        404,
                        {
                            "status": "error",
                            "error_code": "not_found",
                            "error_message": f"file_id not found: {file_id}",
                            "retryable": False,
                        },
                    )
                    return
                self._write_json(200, metadata)
                return
            self._write_json(
                404,
                {
                    "status": "not_found",
                    "path": self.path,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            path = self._normalized_path()
            if path != "/v1/files":
                self._write_json(
                    404,
                    {
                        "status": "not_found",
                        "path": self.path,
                    },
                )
                return
            if not self._require_transport_token():
                return
            try:
                raw_length = str(self.headers.get("Content-Length", "") or "").strip()
                content_length = int(raw_length)
                if content_length < 0:
                    raise ValueError("Content-Length must be non-negative")
                body = self.rfile.read(content_length)
                form_fields = _parse_multipart_form_data(self.headers, body)
                metadata_part = form_fields.get("metadata")
                file_part = form_fields.get("file")
                if metadata_part is None or file_part is None:
                    raise FileSurfaceUploadError(
                        status_code=400,
                        error_code="invalid_metadata",
                        error_message="multipart/form-data must contain metadata and file parts",
                        retryable=False,
                    )
                metadata = json.loads(bytes(metadata_part["payload"]).decode("utf-8"))
                file_bytes = bytes(file_part["payload"])
                descriptor = file_store.store_upload(metadata, file_bytes)
            except FileSurfaceUploadError as exc:
                self._write_upload_error(exc)
                return
            except json.JSONDecodeError as exc:
                self._write_upload_error(
                    FileSurfaceUploadError(
                        status_code=400,
                        error_code="invalid_metadata",
                        error_message=f"metadata is not valid JSON: {exc}",
                        retryable=False,
                    )
                )
                return
            except ValueError as exc:
                self._write_upload_error(
                    FileSurfaceUploadError(
                        status_code=400,
                        error_code="invalid_metadata",
                        error_message=str(exc),
                        retryable=False,
                    )
                )
                return
            except Exception as exc:
                self._write_upload_error(
                    FileSurfaceUploadError(
                        status_code=500,
                        error_code="store_write_failed",
                        error_message=str(exc),
                        retryable=True,
                    )
                )
                return
            self._write_json(200, descriptor)

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


def _render_http_response(
    status: HTTPStatus | int,
    headers: list[tuple[str, str]],
    body: bytes,
) -> bytes:
    normalized_status = HTTPStatus(int(status))
    lines = [f"HTTP/1.1 {normalized_status.value} {normalized_status.phrase}\r\n"]
    seen_connection = False
    for name, value in headers:
        if name.lower() == "connection":
            seen_connection = True
        lines.append(f"{name}: {value}\r\n")
    if not seen_connection:
        lines.append("Connection: close\r\n")
    lines.append("\r\n")
    return "".join(lines).encode("iso-8859-1") + body


class _UnifiedRelayServer:
    def __init__(
        self,
        public_server: asyncio.AbstractServer,
        websocket_server,
        http_server: ThreadingHTTPServer,
        http_thread: threading.Thread,
    ) -> None:
        self._public_server = public_server
        self._websocket_server = websocket_server
        self._http_server = http_server
        self._http_thread = http_thread
        self._closed = False

    @property
    def sockets(self):
        return self._public_server.sockets

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._public_server.close()
        self._websocket_server.close()
        self._http_server.shutdown()
        self._http_server.server_close()

    async def wait_closed(self) -> None:
        await self._public_server.wait_closed()
        await self._websocket_server.wait_closed()
        await asyncio.to_thread(self._http_thread.join, 5)


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[bytes, str, str, dict[str, str]] | None:
    try:
        first_byte = await reader.read(1)
        if not first_byte:
            return None
        if not (65 <= first_byte[0] <= 90):
            raise ValueError("invalid HTTP preface")
        header_bytes = first_byte + await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        return None
    except asyncio.LimitOverrunError as exc:
        raise ValueError(f"HTTP header exceeded limit: {exc}") from exc
    if len(header_bytes) > _HTTP_HEADER_LIMIT_BYTES:
        raise ValueError("HTTP header exceeded limit")

    header_text = header_bytes.decode("iso-8859-1")
    header_lines = header_text.split("\r\n")
    request_line = header_lines[0].strip()
    if not request_line:
        return None
    try:
        method, path, _http_version = request_line.split(" ", 2)
    except ValueError as exc:
        raise ValueError("invalid HTTP request line") from exc

    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ValueError("invalid HTTP header line")
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if transfer_encoding and transfer_encoding != "identity":
        raise ValueError("transfer-encoding is not supported")

    content_length = 0
    if headers.get("content-length"):
        try:
            content_length = int(headers["content-length"])
        except ValueError as exc:
            raise ValueError("invalid Content-Length header") from exc
        if content_length < 0:
            raise ValueError("invalid Content-Length header")

    body = await reader.readexactly(content_length) if content_length else b""
    return header_bytes + body, method.upper(), path, headers


async def _pipe_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        raise
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
                await writer.drain()
        except Exception:
            pass


async def _proxy_raw_http_request(
    request_bytes: bytes,
    writer: asyncio.StreamWriter,
    *,
    target_host: str,
    target_port: int,
) -> None:
    target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    try:
        target_writer.write(request_bytes)
        await target_writer.drain()
        response_head = await target_reader.readuntil(b"\r\n\r\n")
        writer.write(response_head)
        await writer.drain()

        header_text = response_head.decode("iso-8859-1")
        response_headers: dict[str, str] = {}
        for line in header_text.split("\r\n")[1:]:
            if not line:
                continue
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            response_headers[name.strip().lower()] = value.strip()

        if response_headers.get("transfer-encoding", "").lower() == "chunked":
            while True:
                chunk_header = await target_reader.readuntil(b"\r\n")
                writer.write(chunk_header)
                await writer.drain()
                chunk_size_text = chunk_header.decode("ascii").split(";", 1)[0].strip()
                chunk_size = int(chunk_size_text, 16)
                if chunk_size == 0:
                    trailer = await target_reader.readuntil(b"\r\n")
                    writer.write(trailer)
                    await writer.drain()
                    break
                chunk_payload = await target_reader.readexactly(chunk_size + 2)
                writer.write(chunk_payload)
                await writer.drain()
            return

        content_length = response_headers.get("content-length")
        if content_length:
            remaining = int(content_length)
            while remaining > 0:
                chunk = await target_reader.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                writer.write(chunk)
                await writer.drain()
            return

        while True:
            chunk = await target_reader.read(64 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        target_writer.close()
        await target_writer.wait_closed()


async def _proxy_websocket_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    request_bytes: bytes,
    *,
    target_host: str,
    target_port: int,
) -> None:
    target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    try:
        target_writer.write(request_bytes)
        await target_writer.drain()

        upstream_task = asyncio.create_task(_pipe_stream(client_reader, target_writer))
        downstream_task = asyncio.create_task(_pipe_stream(target_reader, client_writer))
        done, pending = await asyncio.wait(
            {upstream_task, downstream_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, return_exceptions=True)
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        target_writer.close()
        await target_writer.wait_closed()


async def _handle_unified_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    config: RelayServerConfig,
    session_store,
    packet_store,
    listen_ref: dict[str, Any],
    websocket_target: tuple[str, int],
    http_target: tuple[str, int],
) -> None:
    try:
        request = await _read_http_request(reader)
        if request is None:
            return
        request_bytes, _method, raw_path, headers = request
        normalized_path = urlparse(raw_path).path
        is_websocket_upgrade = (
            normalized_path == "/relay"
            and headers.get("upgrade", "").lower() == "websocket"
            and "upgrade" in headers.get("connection", "").lower()
        )
        if normalized_path in {"/healthz", "/readyz"} and not is_websocket_upgrade:
            response = _json_response(
                HTTPStatus.OK,
                build_health_payload(
                    config,
                    session_store,
                    packet_store=packet_store,
                    listening_host=str(listen_ref.get("host") or config.host),
                    listening_port=int(listen_ref.get("port") or config.port),
                ),
            )
            writer.write(_render_http_response(*response))
            await writer.drain()
            return
        if is_websocket_upgrade:
            await _proxy_websocket_connection(
                reader,
                writer,
                request_bytes,
                target_host=websocket_target[0],
                target_port=websocket_target[1],
            )
            return
        await _proxy_raw_http_request(
            request_bytes,
            writer,
            target_host=http_target[0],
            target_port=http_target[1],
        )
    except ValueError as exc:
        response = _json_response(
            HTTPStatus.BAD_REQUEST,
            {
                "status": "error",
                "error_code": "bad_request",
                "error_message": str(exc),
                "retryable": False,
            },
        )
        writer.write(_render_http_response(*response))
        await writer.drain()
    except Exception:
        LOGGER.exception("unified relay listener failed")
        response = _json_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {
                "status": "error",
                "error_code": "server_error",
                "error_message": "internal server error",
                "retryable": True,
            },
        )
        writer.write(_render_http_response(*response))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _parse_multipart_form_data(request_headers: Any, body: bytes) -> dict[str, dict[str, Any]]:
    content_type = str(getattr(request_headers, "get", lambda *_: "")("Content-Type", "") or "")
    if "multipart/form-data" not in content_type:
        raise FileSurfaceUploadError(
            status_code=400,
            error_code="invalid_metadata",
            error_message="Content-Type must be multipart/form-data",
            retryable=False,
        )
    envelope = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    if not message.is_multipart():
        raise FileSurfaceUploadError(
            status_code=400,
            error_code="invalid_metadata",
            error_message="multipart/form-data body could not be parsed",
            retryable=False,
        )
    fields: dict[str, dict[str, Any]] = {}
    for part in message.iter_parts():
        field_name = str(part.get_param("name", header="content-disposition") or "").strip()
        if not field_name:
            continue
        fields[field_name] = {
            "filename": part.get_filename(),
            "content_type": part.get_content_type(),
            "payload": part.get_payload(decode=True) or b"",
        }
    return fields


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
    file_upload_limit_bytes: int | None = None,
):
    internal_config = replace(
        config,
        host="127.0.0.1",
        port=0,
        tls_certfile=None,
        tls_keyfile=None,
    )
    websocket_server = None
    http_server = None
    http_thread = None
    try:
        websocket_server = await _start_internal_websocket_server(
            internal_config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
        )
        websocket_host, websocket_port = websocket_server.sockets[0].getsockname()[:2]

        http_server = build_http_server(
            internal_config,
            session_store=session_store,
            packet_store=packet_store,
            file_upload_limit_bytes=file_upload_limit_bytes,
        )
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()
        http_host, http_port = http_server.server_address[:2]

        listen_ref: dict[str, Any] = {"host": config.host, "port": config.port}
        public_server = await asyncio.start_server(
            lambda reader, writer: _handle_unified_connection(
                reader,
                writer,
                config=config,
                session_store=session_store,
                packet_store=packet_store,
                listen_ref=listen_ref,
                websocket_target=(str(websocket_host), int(websocket_port)),
                http_target=(str(http_host), int(http_port)),
            ),
            config.host,
            config.port,
            ssl=ssl_context,
        )
        public_host, public_port = public_server.sockets[0].getsockname()[:2]
        listen_ref["host"] = str(public_host)
        listen_ref["port"] = int(public_port)

        return _UnifiedRelayServer(
            public_server=public_server,
            websocket_server=websocket_server,
            http_server=http_server,
            http_thread=http_thread,
        )
    except Exception:
        if websocket_server is not None:
            websocket_server.close()
            await websocket_server.wait_closed()
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if http_thread is not None:
            await asyncio.to_thread(http_thread.join, 5)
        raise


async def _start_internal_websocket_server(
    config: RelayServerConfig,
    *,
    relay: LoopbackRelayServer,
    session_store,
    packet_store,
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
