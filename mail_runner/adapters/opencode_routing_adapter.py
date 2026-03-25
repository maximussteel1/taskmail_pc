"""Routes OpenCode work between CLI and SDK transports."""

from __future__ import annotations

from .transport_routing_adapter import TransportRoutingAdapter


class OpenCodeRoutingAdapter(TransportRoutingAdapter):
    """OpenCode-specific transport router."""

