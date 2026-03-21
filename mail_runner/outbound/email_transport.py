"""Email transport wrapper for outbound dispatch requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contract import OutboundDispatchRequest, TransportReceipt

_TRANSPORT_NAME = "email"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class EmailTransport:
    def __init__(self, mail_client: Any) -> None:
        self._mail_client = mail_client

    def send(self, request: OutboundDispatchRequest) -> TransportReceipt:
        try:
            transport_message_id = self._mail_client.send_mail(
                to_addr=request.to_addr,
                subject=request.subject,
                body=request.packet.text_fallback,
                attachments=request.packet.attachments,
                in_reply_to=request.in_reply_to,
                references=request.references,
                headers=request.headers,
                html_body=request.packet.html,
            )
        except Exception as exc:
            return TransportReceipt(
                success=False,
                transport_name=_TRANSPORT_NAME,
                sent_at=_timestamp(),
                error_message=f"{type(exc).__name__}: {exc}",
            )

        normalized_message_id = str(transport_message_id or "").strip()
        if not normalized_message_id:
            return TransportReceipt(
                success=False,
                transport_name=_TRANSPORT_NAME,
                sent_at=_timestamp(),
                error_message="Email transport returned an empty message id.",
            )
        return TransportReceipt(
            success=True,
            transport_name=_TRANSPORT_NAME,
            sent_at=_timestamp(),
            transport_message_id=normalized_message_id,
        )
