"""Reusable relay bootstrap probes and helpers."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime

import websockets
from websockets.exceptions import InvalidHandshake, InvalidMessage

from ..relay_server import (
    ProtocolValidationError,
    RelayErrorMessage,
    RelayHelloAckMessage,
    parse_server_message,
    token_fingerprint,
)

_DEFAULT_CLIENT_ID = "pc-local"
_DEFAULT_CLIENT_VERSION = "0.1.0-dev"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass(slots=True)
class RelayHealthProbeResult:
    url: str
    ok: bool
    http_status: int | None = None
    payload: dict[str, object] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class RelayBootstrapProbeResult:
    relay_url: str
    health_url: str | None
    success: bool
    handshake_status: str
    health: RelayHealthProbeResult | None = None
    connection_id: str | None = None
    server_time: str | None = None
    heartbeat_seconds: int | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.health is not None:
            payload["health"] = self.health.to_dict()
        return payload


def build_hello_payload(
    *,
    client_id: str = _DEFAULT_CLIENT_ID,
    client_version: str = _DEFAULT_CLIENT_VERSION,
    transport_token: str,
) -> dict[str, str]:
    normalized_client_id = str(client_id or _DEFAULT_CLIENT_ID).strip() or _DEFAULT_CLIENT_ID
    normalized_client_version = str(client_version or _DEFAULT_CLIENT_VERSION).strip() or _DEFAULT_CLIENT_VERSION
    normalized_transport_token = str(transport_token or "").strip()
    return {
        "message_type": "hello",
        "client_id": normalized_client_id,
        "client_version": normalized_client_version,
        "transport_token_id": token_fingerprint(normalized_transport_token),
        "sent_at": _timestamp(),
    }


def derive_healthz_url(relay_url: str) -> str:
    normalized = str(relay_url or "").strip()
    if not normalized:
        raise ValueError("relay_url must be a non-empty string")
    parsed = urllib.parse.urlsplit(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError("relay_url must use ws:// or wss://")

    if parsed.path.endswith("/relay"):
        path = f"{parsed.path[:-6]}/healthz" if parsed.path != "/relay" else "/healthz"
    elif not parsed.path or parsed.path == "/":
        path = "/healthz"
    else:
        path = "/healthz"

    http_scheme = "https" if scheme == "wss" else "http"
    return urllib.parse.urlunsplit((http_scheme, parsed.netloc, path, "", ""))


def probe_healthz(
    health_url: str,
    *,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
    ca_file: str | None = None,
) -> RelayHealthProbeResult:
    normalized_url = str(health_url or "").strip()
    context = _build_http_ssl_context(normalized_url, verify_tls=verify_tls, ca_file=ca_file)
    request = urllib.request.Request(normalized_url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout_seconds)), context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return RelayHealthProbeResult(
                url=normalized_url,
                ok=(response.status == 200 and isinstance(payload, dict) and payload.get("status") == "ok"),
                http_status=response.status,
                payload=payload if isinstance(payload, dict) else None,
            )
    except urllib.error.HTTPError as exc:
        payload = _parse_http_error_payload(exc)
        return RelayHealthProbeResult(
            url=normalized_url,
            ok=False,
            http_status=int(exc.code),
            payload=payload,
            error_type=type(exc).__name__,
            error_message=f"HTTP {exc.code}: {exc.reason}",
        )
    except urllib.error.URLError as exc:
        reason = exc.reason
        return RelayHealthProbeResult(
            url=normalized_url,
            ok=False,
            error_type=type(reason).__name__ if reason is not None else type(exc).__name__,
            error_message=str(reason or exc),
        )
    except Exception as exc:
        return RelayHealthProbeResult(
            url=normalized_url,
            ok=False,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def probe_relay_bootstrap(
    *,
    relay_url: str,
    transport_token: str,
    client_id: str = _DEFAULT_CLIENT_ID,
    client_version: str = _DEFAULT_CLIENT_VERSION,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
    ca_file: str | None = None,
    health_url: str | None = None,
) -> RelayBootstrapProbeResult:
    return asyncio.run(
        async_probe_relay_bootstrap(
            relay_url=relay_url,
            transport_token=transport_token,
            client_id=client_id,
            client_version=client_version,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
            ca_file=ca_file,
            health_url=health_url,
        )
    )


async def async_probe_relay_bootstrap(
    *,
    relay_url: str,
    transport_token: str,
    client_id: str = _DEFAULT_CLIENT_ID,
    client_version: str = _DEFAULT_CLIENT_VERSION,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
    ca_file: str | None = None,
    health_url: str | None = None,
) -> RelayBootstrapProbeResult:
    normalized_relay_url = str(relay_url or "").strip()
    normalized_transport_token = str(transport_token or "").strip()
    if not normalized_relay_url or not normalized_transport_token:
        return RelayBootstrapProbeResult(
            relay_url=normalized_relay_url,
            health_url=None,
            success=False,
            handshake_status="not_configured",
            error_message="relay_url and transport_token are required",
        )

    resolved_health_url = str(health_url or "").strip() or derive_healthz_url(normalized_relay_url)
    health_result = probe_healthz(
        resolved_health_url,
        timeout_seconds=timeout_seconds,
        verify_tls=verify_tls,
        ca_file=ca_file,
    )
    return await _probe_websocket_bootstrap(
        relay_url=normalized_relay_url,
        transport_token=normalized_transport_token,
        client_id=client_id,
        client_version=client_version,
        timeout_seconds=timeout_seconds,
        verify_tls=verify_tls,
        ca_file=ca_file,
        health_result=health_result,
    )


async def _probe_websocket_bootstrap(
    *,
    relay_url: str,
    transport_token: str,
    client_id: str,
    client_version: str,
    timeout_seconds: int,
    verify_tls: bool,
    ca_file: str | None,
    health_result: RelayHealthProbeResult,
) -> RelayBootstrapProbeResult:
    ssl_context = _build_websocket_ssl_context(relay_url, verify_tls=verify_tls, ca_file=ca_file)
    try:
        async with websockets.connect(
            relay_url,
            ssl=ssl_context,
            open_timeout=max(1, int(timeout_seconds)),
            close_timeout=max(1, int(timeout_seconds)),
            extra_headers={"Authorization": f"Bearer {transport_token}"},
            max_size=4 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    build_hello_payload(
                        client_id=client_id,
                        client_version=client_version,
                        transport_token=transport_token,
                    ),
                    ensure_ascii=False,
                )
            )
            raw_response = await asyncio.wait_for(websocket.recv(), timeout=max(1, int(timeout_seconds)))
            return _result_from_server_payload(
                relay_url=relay_url,
                health_result=health_result,
                payload=_parse_json_mapping(raw_response),
            )
    except Exception as exc:
        return RelayBootstrapProbeResult(
            relay_url=relay_url,
            health_url=health_result.url,
            success=False,
            handshake_status=_classify_websocket_exception(exc),
            health=health_result,
            error_message=str(exc),
        )


def _result_from_server_payload(
    *,
    relay_url: str,
    health_result: RelayHealthProbeResult,
    payload: dict[str, object] | None,
) -> RelayBootstrapProbeResult:
    if payload is None:
        return RelayBootstrapProbeResult(
            relay_url=relay_url,
            health_url=health_result.url,
            success=False,
            handshake_status="invalid_json",
            health=health_result,
            error_message="bootstrap response was not valid JSON",
        )
    try:
        parsed = parse_server_message(payload)
    except ProtocolValidationError as exc:
        return RelayBootstrapProbeResult(
            relay_url=relay_url,
            health_url=health_result.url,
            success=False,
            handshake_status="unexpected_response",
            health=health_result,
            error_message=str(exc),
        )

    if isinstance(parsed, RelayHelloAckMessage):
        return RelayBootstrapProbeResult(
            relay_url=relay_url,
            health_url=health_result.url,
            success=True,
            handshake_status="hello_ack",
            health=health_result,
            connection_id=parsed.connection_id,
            server_time=parsed.server_time,
            heartbeat_seconds=parsed.heartbeat_seconds,
        )
    if isinstance(parsed, RelayErrorMessage):
        return RelayBootstrapProbeResult(
            relay_url=relay_url,
            health_url=health_result.url,
            success=False,
            handshake_status=str(parsed.code),
            health=health_result,
            error_code=parsed.code,
            error_message=parsed.message,
        )
    return RelayBootstrapProbeResult(
        relay_url=relay_url,
        health_url=health_result.url,
        success=False,
        handshake_status="unexpected_response",
        health=health_result,
        error_message="bootstrap response did not map to hello_ack or error",
    )


def _build_http_ssl_context(url: str, *, verify_tls: bool, ca_file: str | None) -> ssl.SSLContext | None:
    if not str(url or "").strip().lower().startswith("https://"):
        return None
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif ca_file:
        context.load_verify_locations(ca_file)
    return context


def _build_websocket_ssl_context(relay_url: str, *, verify_tls: bool, ca_file: str | None) -> ssl.SSLContext | None:
    if str(relay_url or "").strip().lower().startswith("ws://"):
        return None
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif ca_file:
        context.load_verify_locations(ca_file)
    return context


def _parse_json_mapping(text: str) -> dict[str, object] | None:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_http_error_payload(exc: urllib.error.HTTPError) -> dict[str, object] | None:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _classify_websocket_exception(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        if "wrong version number" in message or "unknown protocol" in message:
            return "scheme_mismatch"
        return "tls_failure"
    if isinstance(exc, InvalidMessage):
        if "did not receive a valid http response" in message:
            return "scheme_mismatch"
        return "invalid_http_response"
    if isinstance(exc, InvalidHandshake):
        return "invalid_handshake"
    if isinstance(exc, OSError):
        return "connect_failure"
    return "unknown_error"
