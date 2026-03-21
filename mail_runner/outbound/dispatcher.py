"""Outbound dispatch coordination."""

from __future__ import annotations

from typing import Any, Protocol

from ..relay_server import LoopbackRelayServer
from .contract import OutboundDispatchRequest, TransportReceipt
from .email_transport import EmailTransport
from .relay_transport import RelayTransport


class UnsupportedOutboundTransportError(ValueError):
    """Raised when a named outbound transport is not registered."""


class OutboundTransport(Protocol):
    def send(self, request: OutboundDispatchRequest) -> TransportReceipt:
        """Send one outbound request through a concrete transport."""


class OutboundDispatcher:
    def __init__(self, transport: OutboundTransport) -> None:
        self._transport = transport

    def send(self, request: OutboundDispatchRequest) -> TransportReceipt:
        return self._transport.send(request)


def build_transport(
    *,
    transport_name: str = "email",
    mail_client: Any | None = None,
    relay_server: LoopbackRelayServer | None = None,
    relay_url: str | None = None,
    relay_transport_token: str | None = None,
    relay_client_id: str = "pc-local",
    relay_client_version: str = "0.1.0-dev",
    relay_timeout_seconds: int = 15,
    relay_verify_tls: bool = True,
    relay_ca_file: str | None = None,
) -> OutboundTransport:
    normalized_name = str(transport_name or "email").strip().lower()
    if normalized_name == "email":
        if mail_client is None:
            raise ValueError("mail_client is required for the email transport")
        return EmailTransport(mail_client)
    if normalized_name == "relay":
        return RelayTransport(
            relay_server,
            relay_url=relay_url,
            transport_token=relay_transport_token,
            client_id=relay_client_id,
            client_version=relay_client_version,
            timeout_seconds=relay_timeout_seconds,
            verify_tls=relay_verify_tls,
            ca_file=relay_ca_file,
        )
    raise UnsupportedOutboundTransportError(f"Unsupported outbound transport: {transport_name}")


def build_dispatcher(
    *,
    transport_name: str = "email",
    mail_client: Any | None = None,
    relay_server: LoopbackRelayServer | None = None,
    relay_url: str | None = None,
    relay_transport_token: str | None = None,
    relay_client_id: str = "pc-local",
    relay_client_version: str = "0.1.0-dev",
    relay_timeout_seconds: int = 15,
    relay_verify_tls: bool = True,
    relay_ca_file: str | None = None,
) -> OutboundDispatcher:
    return OutboundDispatcher(
        build_transport(
            transport_name=transport_name,
            mail_client=mail_client,
            relay_server=relay_server,
            relay_url=relay_url,
            relay_transport_token=relay_transport_token,
            relay_client_id=relay_client_id,
            relay_client_version=relay_client_version,
            relay_timeout_seconds=relay_timeout_seconds,
            relay_verify_tls=relay_verify_tls,
            relay_ca_file=relay_ca_file,
        )
    )
