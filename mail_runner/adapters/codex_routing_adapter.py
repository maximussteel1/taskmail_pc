"""Routes Codex work between CLI and SDK transports."""

from __future__ import annotations

from .transport_routing_adapter import TransportRoutingAdapter


class CodexRoutingAdapter(TransportRoutingAdapter):
    """Codex-specific transport router."""
