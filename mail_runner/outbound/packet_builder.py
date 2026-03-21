"""Builders for outbound packet and dispatch request handoff."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Callable

from ..models import OutgoingAttachment, RunResult, TaskSnapshot, ThreadState
from .contract import OutboundDispatchRequest, TaskRunPacket
from .renderer import RenderedStatusMail

_CONTENT_FORMAT = "text/plain+text/html"
_MESSAGE_KIND = "status_update"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _default_packet_id(task_id: str) -> str:
    return f"packet:{task_id}:{secrets.token_hex(4)}"


def _build_state_patch(
    *,
    status_label: str,
    state: ThreadState,
    result: RunResult | None,
) -> dict[str, str]:
    state_patch = {
        "thread_id": state.thread_id,
        "session_id": state.session_id or state.thread_id,
        "thread_status": state.status,
        "status_label": status_label,
        "backend": state.backend,
    }
    if result is not None:
        state_patch["run_status"] = result.status
    return state_patch


def build_task_run_packet(
    *,
    rendered_mail: RenderedStatusMail,
    state: ThreadState,
    task_snapshot: TaskSnapshot,
    status_label: str,
    attachments: list[OutgoingAttachment] | None = None,
    result: RunResult | None = None,
    created_at: str | None = None,
    packet_id_factory: Callable[[str], str] | None = None,
    parent_packet_id: str | None = None,
    client_trace_id: str | None = None,
) -> TaskRunPacket:
    packet_created_at = created_at or _timestamp()
    resolved_packet_id_factory = packet_id_factory or _default_packet_id
    resolved_client_trace_id = client_trace_id or (result.task_id if result is not None else task_snapshot.task_id)
    return TaskRunPacket(
        packet_id=resolved_packet_id_factory(task_snapshot.task_id),
        task_id=task_snapshot.task_id,
        created_at=packet_created_at,
        message_kind=_MESSAGE_KIND,
        content_format=_CONTENT_FORMAT,
        html=rendered_mail.html_body,
        text_fallback=rendered_mail.plain_body,
        attachments=list(attachments or []),
        parent_packet_id=parent_packet_id,
        state_patch=_build_state_patch(status_label=status_label, state=state, result=result),
        client_trace_id=resolved_client_trace_id,
    )


def build_outbound_dispatch_request(
    *,
    packet: TaskRunPacket,
    to_addr: str,
    subject: str,
    in_reply_to: str | None,
    references: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> OutboundDispatchRequest:
    return OutboundDispatchRequest(
        packet=packet,
        to_addr=to_addr,
        subject=subject,
        in_reply_to=in_reply_to,
        references=list(references or []),
        headers=dict(headers or {}),
    )
