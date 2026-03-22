"""Outbound status-mail workflow helpers."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..artifact_resolver import (
    project_run_artifacts_to_outgoing_attachments,
    resolve_run_artifacts,
    write_artifact_index,
)
from ..canonical_run_summary import write_run_canonical_summary
from ..config import AppConfig
from ..external_delivery import prepare_external_deliveries
from ..mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from ..mail_retention import is_prunable_thread_status_subject
from ..models import OutgoingAttachment, RunResult, TaskSnapshot, ThreadState
from ..status import RUN_STATUS_AWAITING_USER_INPUT, RUN_STATUS_SUCCESS
from ..thread_store import save_raw_mail, save_thread_state
from .contract import OutboundDispatchRequest, TransportReceipt
from .dispatcher import build_dispatcher
from .journal import OutboundJournal
from .packet_builder import build_outbound_dispatch_request, build_task_run_packet
from .renderer import render_status_mail

LOGGER = logging.getLogger(__name__)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def build_references(message_id: str | None, existing: list[str]) -> list[str]:
    references = list(existing)
    if message_id and message_id not in references:
        references.append(message_id)
    return references


def _default_reply_headers(state: ThreadState) -> tuple[str | None, list[str]]:
    reply_to = state.latest_message_id or state.root_message_id
    references = build_references(state.root_message_id, [])
    references = build_references(state.latest_message_id, references)
    return reply_to, references


def _store_outgoing_mail(
    task_root: Path,
    config: AppConfig,
    state: ThreadState,
    *,
    to_addr: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: str | None,
    references: list[str],
    html_body: str | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> None:
    save_raw_mail(
        state.thread_id,
        {
            "message_id": message_id,
            "subject": subject,
            "from_addr": config.from_addr or config.smtp_user or config.imap_user,
            "to_addr": to_addr,
            "date": _timestamp(),
            "in_reply_to": in_reply_to,
            "references": list(references),
            "body_text": body,
            "html_body": html_body,
            "attachments": [
                {
                    "path": item.path,
                    "name": item.name,
                    "content_type": item.content_type,
                    "attach": item.attach,
                    "inline": item.inline,
                    "content_id": item.content_id,
                    "caption": item.caption,
                }
                for item in (attachments or [])
            ],
            "raw_headers": {
                "Subject": subject,
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
                "Message-ID": message_id,
            },
        },
        task_root,
    )
    if in_reply_to is None and state.root_message_id.startswith("local-root:"):
        state.root_message_id = message_id
    state.latest_message_id = message_id
    state.updated_at = _timestamp()
    save_thread_state(state, task_root)


def _list_previous_status_message_ids(task_root: Path, state: ThreadState, *, keep_message_id: str) -> list[str]:
    mail_dir = task_root / state.thread_id / "mail"
    if not mail_dir.exists():
        return []

    message_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw_path in sorted(mail_dir.glob("raw_*.json")):
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.warning("Unable to parse stored mail payload: %s", raw_path)
            continue
        if not isinstance(payload, dict):
            continue
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id or message_id == keep_message_id or message_id in seen_ids:
            continue
        raw_headers = payload.get("raw_headers") or {}
        if not isinstance(raw_headers, dict):
            continue
        if str(raw_headers.get(SYSTEM_MESSAGE_HEADER) or "").strip() != SYSTEM_MESSAGE_HEADER_VALUE:
            continue
        subject = str(payload.get("subject") or raw_headers.get("Subject") or "").strip()
        if not is_prunable_thread_status_subject(subject):
            continue
        seen_ids.add(message_id)
        message_ids.append(message_id)
    return message_ids


def _prune_previous_status_mails(mail_client: Any, task_root: Path, state: ThreadState, *, keep_message_id: str) -> None:
    delete_fn = getattr(mail_client, "delete_messages_by_message_ids", None)
    if not callable(delete_fn):
        return
    previous_message_ids = _list_previous_status_message_ids(task_root, state, keep_message_id=keep_message_id)
    if not previous_message_ids:
        return
    try:
        deleted_ids = list(delete_fn(previous_message_ids, mailbox="INBOX") or [])
    except Exception:
        LOGGER.exception("Unable to prune old status mails for thread %s", state.thread_id)
        return
    if deleted_ids:
        LOGGER.info(
            "Pruned %s old status mails from INBOX for thread %s",
            len(deleted_ids),
            state.thread_id,
        )


def _normalize_captured_output(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "").strip()


def _load_captured_reply(task_root: Path, result: RunResult | None) -> str | None:
    if result is None:
        return None

    candidate_rel_paths = [result.stdout_file]
    if result.status not in {RUN_STATUS_SUCCESS, RUN_STATUS_AWAITING_USER_INPUT}:
        candidate_rel_paths.append(result.stderr_file)

    for rel_path in candidate_rel_paths:
        candidate = task_root / result.thread_id / rel_path
        if not candidate.exists():
            continue
        content = _normalize_captured_output(candidate.read_text(encoding="utf-8", errors="replace"))
        if content:
            return content
    return None


def _build_dispatcher_for_transport(config: AppConfig, mail_client: Any, *, transport_name: str):
    return build_dispatcher(
        transport_name=transport_name,
        mail_client=mail_client,
        relay_url=config.relay_url,
        relay_transport_token=config.relay_transport_token,
        relay_client_id=config.relay_client_id,
        relay_client_version=config.relay_client_version,
        relay_timeout_seconds=config.relay_timeout_seconds,
        relay_verify_tls=config.relay_verify_tls,
        relay_ca_file=config.relay_ca_file or None,
    )


def _send_dispatch_request(
    config: AppConfig,
    mail_client: Any,
    dispatch_request: OutboundDispatchRequest,
) -> tuple[TransportReceipt, list[TransportReceipt]]:
    attempts: list[TransportReceipt] = []
    primary_transport = config.outbound_transport
    primary_receipt = _build_dispatcher_for_transport(
        config,
        mail_client,
        transport_name=primary_transport,
    ).send(dispatch_request)
    attempts.append(primary_receipt)
    if primary_receipt.success:
        return primary_receipt, attempts

    if primary_transport == "relay" and config.relay_auto_fallback_email:
        fallback_receipt = _build_dispatcher_for_transport(
            config,
            mail_client,
            transport_name="email",
        ).send(dispatch_request)
        attempts.append(fallback_receipt)
        return fallback_receipt, attempts

    return primary_receipt, attempts


def send_status_update(
    mail_client: Any,
    config: AppConfig,
    task_root: Path,
    *,
    to_addr: str,
    subject_text: str,
    status_label: str,
    state: ThreadState,
    task_snapshot: TaskSnapshot,
    result: RunResult | None = None,
    intro: str | None = None,
    reply_message_id: str | None = None,
    references: list[str] | None = None,
    reply_to_existing: bool = True,
    summary_override: str | None = None,
    reply_override: str | None = None,
) -> str | None:
    try:
        question_id = result.question_id if result and result.question_id else state.pending_question_id
        question_text = result.question_text if result and result.question_text else state.pending_question_text
        pending_choices = list(result.pending_choices) if result and result.pending_choices else list(state.pending_choices)
        pending_questions = list(result.pending_questions) if result and result.pending_questions else list(state.pending_questions)
        collected_answers = list(state.collected_answers)
        question_set_id = result.question_set_id if result and result.question_set_id else state.pending_question_set_id
        captured_reply = reply_override if reply_override is not None else _load_captured_reply(task_root, result)
        resolved_artifacts, skipped_attachments = resolve_run_artifacts(task_root, state, result)
        write_artifact_index(task_root, result, resolved_artifacts, skipped_attachments)
        resolved_attachments = project_run_artifacts_to_outgoing_attachments(resolved_artifacts)
        mail_artifacts, resolved_attachments, external_deliveries, delivery_notices = prepare_external_deliveries(
            config,
            artifacts=resolved_artifacts,
            attachments=resolved_attachments,
            result=result,
        )
        effective_notices = [*skipped_attachments, *delivery_notices]
        rendered_mail = render_status_mail(
            status_label=status_label,
            subject_text=subject_text,
            state=state,
            task_snapshot=task_snapshot,
            attachments=resolved_attachments,
            result=result,
            captured_reply=captured_reply,
            intro=intro,
            question_id=question_id,
            question_text=question_text,
            pending_choices=pending_choices,
            question_set_id=question_set_id,
            pending_questions=pending_questions,
            collected_answers=collected_answers,
            artifacts=mail_artifacts,
            external_deliveries=external_deliveries,
            skipped_messages=effective_notices,
            summary_override=summary_override,
        )
        if not reply_to_existing:
            reply_message_id = None
            references = []
        elif reply_message_id is None:
            reply_message_id, references = _default_reply_headers(state)
        elif references is None:
            references = build_references(reply_message_id, [])
        packet = build_task_run_packet(
            rendered_mail=rendered_mail,
            state=state,
            task_snapshot=task_snapshot,
            status_label=status_label,
            attachments=resolved_attachments,
            result=result,
        )
        dispatch_request = build_outbound_dispatch_request(
            packet=packet,
            to_addr=to_addr,
            subject=rendered_mail.subject,
            in_reply_to=reply_message_id,
            references=references,
            headers={SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
        )
        receipt, attempt_receipts = _send_dispatch_request(config, mail_client, dispatch_request)
        try:
            journal = OutboundJournal(task_root)
            for attempt_receipt in attempt_receipts:
                journal.record_attempt(
                    state=state,
                    request=dispatch_request,
                    receipt=attempt_receipt,
                )
        except Exception:
            LOGGER.exception("Unable to record outbound delivery attempt for thread %s", state.thread_id)

        sent_message_id = receipt.transport_message_id if receipt.success else None
        if sent_message_id:
            _store_outgoing_mail(
                task_root,
                config,
                state,
                to_addr=dispatch_request.to_addr,
                subject=dispatch_request.subject,
                body=dispatch_request.packet.text_fallback,
                message_id=sent_message_id,
                in_reply_to=dispatch_request.in_reply_to,
                references=list(dispatch_request.references),
                html_body=dispatch_request.packet.html,
                attachments=dispatch_request.packet.attachments,
            )
            _prune_previous_status_mails(mail_client, task_root, state, keep_message_id=sent_message_id)
        elif receipt.error_message:
            LOGGER.error(
                "Outbound send failed for thread %s via %s: %s",
                state.thread_id,
                receipt.transport_name,
                receipt.error_message,
            )
        if result is not None:
            try:
                write_run_canonical_summary(
                    task_root,
                    state,
                    result,
                    terminal_mail_message_id=sent_message_id,
                    terminal_mail_subject=dispatch_request.subject,
                )
            except Exception:
                LOGGER.exception("Unable to write canonical run summary for thread %s", state.thread_id)
        return sent_message_id
    except Exception:
        LOGGER.exception("Unable to send status mail for task %s", state.current_task_id)
        return None
