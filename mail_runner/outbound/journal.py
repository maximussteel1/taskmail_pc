"""Local journal for outbound delivery attempts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..models import ThreadState
from .contract import DeliveryAttempt, OutboundDispatchRequest, TransportReceipt


def delivery_attempts_path(task_root: Path, thread_id: str) -> Path:
    return Path(task_root) / thread_id / "outbound" / "delivery_attempts.jsonl"


class OutboundJournal:
    def __init__(self, task_root: Path) -> None:
        self._task_root = Path(task_root)

    def record_attempt(
        self,
        *,
        state: ThreadState,
        request: OutboundDispatchRequest,
        receipt: TransportReceipt,
    ) -> DeliveryAttempt:
        attempt = DeliveryAttempt(
            packet_id=request.packet.packet_id,
            thread_id=state.thread_id,
            task_id=request.packet.task_id,
            transport_name=receipt.transport_name,
            sent_at=receipt.sent_at,
            success=receipt.success,
            to_addr=request.to_addr,
            subject=request.subject,
            transport_message_id=receipt.transport_message_id,
            error_message=receipt.error_message,
            client_trace_id=request.packet.client_trace_id,
        )
        path = delivery_attempts_path(self._task_root, state.thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(attempt), ensure_ascii=False) + "\n")
        return attempt
