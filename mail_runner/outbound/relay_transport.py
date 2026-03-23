"""Relay transport helpers for loopback and remote WebSocket delivery."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import ssl
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import websockets

from ..relay_server import (
    LoopbackRelayServer,
    ProtocolValidationError,
    RelayErrorMessage,
    RelayHelloAckMessage,
    RelayPacketAckMessage,
    parse_server_message,
)
from .relay_bootstrap import build_hello_payload
from .contract import OutboundDispatchRequest, TransportReceipt

_TRANSPORT_NAME = "relay"
_NOT_CONFIGURED_MESSAGE = "Relay transport is selected, but no relay loopback or transport token is configured."
_DEFAULT_CLIENT_ID = "pc-local"
_DEFAULT_CLIENT_VERSION = "0.1.0-dev"
_WEBSOCKETS_CONNECT_SUPPORTS_PROXY = "proxy" in inspect.signature(websockets.connect).parameters


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class RelayTransport:
    def __init__(
        self,
        server: LoopbackRelayServer | None = None,
        *,
        relay_url: str | None = None,
        transport_token: str | None = None,
        client_id: str = _DEFAULT_CLIENT_ID,
        client_version: str = _DEFAULT_CLIENT_VERSION,
        timeout_seconds: int = 15,
        verify_tls: bool = True,
        ca_file: str | None = None,
    ) -> None:
        self._server = server
        self._relay_url = str(relay_url or "").strip()
        self._transport_token = str(transport_token or "").strip()
        self._client_id = str(client_id or _DEFAULT_CLIENT_ID).strip() or _DEFAULT_CLIENT_ID
        self._client_version = str(client_version or _DEFAULT_CLIENT_VERSION).strip() or _DEFAULT_CLIENT_VERSION
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._verify_tls = bool(verify_tls)
        self._ca_file = str(ca_file or "").strip() or None

    def send(self, request: OutboundDispatchRequest) -> TransportReceipt:
        if not self._transport_token or (self._server is None and not self._relay_url):
            return self._failure_receipt(_NOT_CONFIGURED_MESSAGE)
        if self._server is not None:
            return self._send_loopback(request)
        return asyncio.run(self._send_remote(request))

    def _send_loopback(self, request: OutboundDispatchRequest) -> TransportReceipt:
        hello_response = self._server.handle_client_message(
            build_hello_payload(
                client_id=self._client_id,
                client_version=self._client_version,
                transport_token=self._transport_token,
            ),
            provided_token=self._transport_token,
        )
        hello_message = self._parse_server_response(hello_response)
        if isinstance(hello_message, RelayErrorMessage):
            return self._failure_receipt(f"{hello_message.code}: {hello_message.message}")
        if not isinstance(hello_message, RelayHelloAckMessage):
            return self._failure_receipt("relay handshake did not return hello_ack")

        packet_response = self._server.handle_client_message(
            {
                "message_type": "packet",
                "packet_id": request.packet.packet_id,
                "client_trace_id": request.packet.client_trace_id or request.packet.task_id,
                "task_run_packet": _serialize_task_run_packet(request.packet),
                "dispatch_metadata": _serialize_dispatch_metadata(request),
                "sent_at": _timestamp(),
            },
            connection_id=hello_message.connection_id,
        )
        packet_message = self._parse_server_response(packet_response)
        return self._receipt_from_packet_response(packet_message)

    async def _send_remote(self, request: OutboundDispatchRequest) -> TransportReceipt:
        ssl_context = self._build_client_ssl_context()
        try:
            async with websockets.connect(
                self._relay_url,
                ssl=ssl_context,
                open_timeout=self._timeout_seconds,
                close_timeout=self._timeout_seconds,
                extra_headers={"Authorization": f"Bearer {self._transport_token}"},
                max_size=32 * 1024 * 1024,
                **_direct_websocket_connect_kwargs(),
            ) as websocket:
                await websocket.send(
                    json.dumps(
                        build_hello_payload(
                            client_id=self._client_id,
                            client_version=self._client_version,
                            transport_token=self._transport_token,
                        ),
                        ensure_ascii=False,
                    )
                )
                hello_message = self._parse_server_response(json.loads(await self._recv_with_timeout(websocket)))
                if isinstance(hello_message, RelayErrorMessage):
                    return self._failure_receipt(f"{hello_message.code}: {hello_message.message}")
                if not isinstance(hello_message, RelayHelloAckMessage):
                    return self._failure_receipt("relay handshake did not return hello_ack")

                await websocket.send(
                    json.dumps(
                        {
                            "message_type": "packet",
                            "packet_id": request.packet.packet_id,
                            "client_trace_id": request.packet.client_trace_id or request.packet.task_id,
                            "task_run_packet": _serialize_task_run_packet(request.packet),
                            "dispatch_metadata": _serialize_dispatch_metadata(request),
                            "sent_at": _timestamp(),
                        },
                        ensure_ascii=False,
                    )
                )
                packet_message = self._parse_server_response(json.loads(await self._recv_with_timeout(websocket)))
                return self._receipt_from_packet_response(packet_message)
        except Exception as exc:
            return self._failure_receipt(f"{type(exc).__name__}: {exc}")

    async def _recv_with_timeout(self, websocket) -> str:
        return await asyncio.wait_for(websocket.recv(), timeout=self._timeout_seconds)

    def _build_client_ssl_context(self) -> ssl.SSLContext | None:
        if self._relay_url.startswith("ws://"):
            return None
        context = ssl.create_default_context()
        if not self._verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif self._ca_file:
            context.load_verify_locations(self._ca_file)
        return context

    def _receipt_from_packet_response(
        self,
        packet_message: RelayPacketAckMessage | RelayErrorMessage | RelayHelloAckMessage | None,
    ) -> TransportReceipt:
        if isinstance(packet_message, RelayErrorMessage):
            return self._failure_receipt(f"{packet_message.code}: {packet_message.message}")
        if not isinstance(packet_message, RelayPacketAckMessage):
            return self._failure_receipt("relay packet submission did not return packet_ack")
        if not packet_message.accepted:
            return self._failure_receipt(packet_message.error_message or "relay rejected packet")
        return TransportReceipt(
            success=True,
            transport_name=_TRANSPORT_NAME,
            sent_at=_timestamp(),
            transport_message_id=packet_message.transport_message_id or packet_message.receipt_id,
        )

    @staticmethod
    def _parse_server_response(payload: dict[str, object]) -> RelayHelloAckMessage | RelayPacketAckMessage | RelayErrorMessage | None:
        try:
            parsed = parse_server_message(payload)
        except ProtocolValidationError:
            return None
        if isinstance(parsed, (RelayHelloAckMessage, RelayPacketAckMessage, RelayErrorMessage)):
            return parsed
        return None

    @staticmethod
    def _failure_receipt(message: str) -> TransportReceipt:
        return TransportReceipt(
            success=False,
            transport_name=_TRANSPORT_NAME,
            sent_at=_timestamp(),
            error_message=message,
        )


def _direct_websocket_connect_kwargs() -> dict[str, object]:
    if _WEBSOCKETS_CONNECT_SUPPORTS_PROXY:
        return {"proxy": None}
    return {}


def _serialize_task_run_packet(packet) -> dict[str, object]:
    return {
        "packet_id": packet.packet_id,
        "task_id": packet.task_id,
        "created_at": packet.created_at,
        "message_kind": packet.message_kind,
        "content_format": packet.content_format,
        "html": packet.html,
        "text_fallback": packet.text_fallback,
        "attachments": [_serialize_attachment(item) for item in packet.attachments],
        "parent_packet_id": packet.parent_packet_id,
        "state_patch": dict(packet.state_patch),
        "client_trace_id": packet.client_trace_id,
    }


def _serialize_attachment(attachment) -> dict[str, object]:
    payload = asdict(attachment)
    data = Path(attachment.path).read_bytes()
    payload["content_bytes_b64"] = base64.b64encode(data).decode("ascii")
    return payload


def _serialize_dispatch_metadata(request: OutboundDispatchRequest) -> dict[str, object]:
    return {
        "to_addr": request.to_addr,
        "subject": request.subject,
        "in_reply_to": request.in_reply_to,
        "references": list(request.references),
        "headers": dict(request.headers),
    }
