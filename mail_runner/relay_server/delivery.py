"""Relay packet delivery helpers."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from ..mail_io import MailClient
from ..models import OutgoingAttachment
from ..outbound.contract import OutboundDispatchRequest, TaskRunPacket, TransportReceipt
from ..outbound.email_transport import EmailTransport
from .config import RelayServerConfig
from .packet_store import AcceptedRelayPacket

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: str, *, fallback: str) -> str:
    text = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._")
    return text or fallback


def _materialize_attachment(
    packet: AcceptedRelayPacket,
    attachment_index: int,
    payload: dict[str, Any],
    state_dir: Path,
) -> OutgoingAttachment:
    attachment_payload = dict(payload)
    content_b64 = str(attachment_payload.pop("content_bytes_b64", "") or "").strip()
    attachment_path = str(attachment_payload.get("path") or "").strip()
    attachment_name = str(attachment_payload.get("name") or Path(attachment_path or "attachment.bin").name).strip() or "attachment.bin"

    if content_b64:
        data = base64.b64decode(content_b64.encode("ascii"))
        packet_dir = state_dir / "materialized_attachments" / _safe_segment(packet.packet_id, fallback="packet")
        packet_dir.mkdir(parents=True, exist_ok=True)
        target_path = packet_dir / f"{attachment_index:03d}_{attachment_name}"
        target_path.write_bytes(data)
        attachment_path = str(target_path)

    if not attachment_path:
        raise ValueError(f"Relay attachment is missing path and content payload: {attachment_name}")

    return OutgoingAttachment(
        path=attachment_path,
        name=attachment_payload.get("name"),
        content_type=attachment_payload.get("content_type"),
        attach=bool(attachment_payload.get("attach", True)),
        inline=bool(attachment_payload.get("inline", False)),
        content_id=attachment_payload.get("content_id"),
        caption=attachment_payload.get("caption"),
    )


def build_dispatch_request_from_packet(packet: AcceptedRelayPacket, *, state_dir: str | Path) -> OutboundDispatchRequest:
    task_payload = dict(packet.task_run_packet)
    attachment_payloads = list(task_payload.pop("attachments", []) or [])
    task_payload["attachments"] = [
        _materialize_attachment(packet, index + 1, dict(item), Path(state_dir))
        for index, item in enumerate(attachment_payloads)
    ]
    dispatch_payload = dict(packet.dispatch_metadata)
    return OutboundDispatchRequest(
        packet=TaskRunPacket(**task_payload),
        to_addr=str(dispatch_payload.get("to_addr") or ""),
        subject=str(dispatch_payload.get("subject") or ""),
        in_reply_to=dispatch_payload.get("in_reply_to"),
        references=list(dispatch_payload.get("references") or []),
        headers=dict(dispatch_payload.get("headers") or {}),
    )


class RelayPacketDeliverer:
    def __init__(self, config: RelayServerConfig, *, mail_client: MailClient | None = None) -> None:
        self._config = config
        self._mail_client = mail_client or MailClient(config.to_mail_config())
        self._transport = EmailTransport(self._mail_client)

    def deliver(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        request = build_dispatch_request_from_packet(packet, state_dir=self._config.state_dir)
        return self._transport.send(request)
