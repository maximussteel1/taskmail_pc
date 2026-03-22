"""Direct post-creation TaskMail action helpers for relay packet handling."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..mail_io import MailClient
from ..models import MailEnvelope, ThreadState
from ..outbound.contract import TransportReceipt
from ..parser import parse_subject
from ..reporter import MAIL_STATUS_PAUSED, MAIL_STATUS_STATUS, build_status_subject
from ..runner import SerialTaskRunner
from ..session_action_closeout import (
    ACTION_TYPE_HEADER,
    TARGET_SESSION_ID_HEADER,
    TARGET_THREAD_ID_HEADER,
    TARGET_WORKSPACE_ID_HEADER,
    build_target_session_identity,
    write_session_action_closeout,
)
from ..status import THREAD_STATUS_AWAITING_USER_INPUT, THREAD_STATUS_PAUSED
from ..state_capsule import render_state_capsule
from ..thread_store import load_session_state, load_thread_state
from .config import RelayServerConfig
from .direct_actions import RelayDirectActionError, RelayDirectPacketHandler
from .packet_store import AcceptedRelayPacket
from .protocol import RelayErrorMessage, RelayPacketAckMessage, RelayPacketMessage

LOGGER = logging.getLogger(__name__)
_POST_CREATION_SCHEMA_VERSION = "post-creation-session-action-contract-v1"
_DIRECT_CHANNEL = "taskmail_android_direct"
_DIRECT_ORIGIN_CLIENT = "android_taskmail"
_FALLBACK_POLICY_MAIL = "mail"
_DIRECT_ACTION_STATUS = "status"
_DIRECT_ACTION_REPLY = "reply"
_CURRENT_SESSION_SCOPE = "current_session"
_BOT_MESSAGE_ID_DOMAIN = "mail-runner.local"
_SAFE_MESSAGE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_STRUCTURED_ANSWER_RE = re.compile(r"(?im)^\s*answers\s*:\s*$|^\s*question_id\s*[:：]")
_LEADING_COMMAND_RE = re.compile(r"^\s*/([a-z][a-z0-9_-]*)\b", re.IGNORECASE)

_DIRECT_POST_CREATION_FALLBACK_REQUIRED_ERROR_CODES = frozenset(
    {"unsupported_action", "direct_temporarily_unavailable"}
)
_DIRECT_POST_CREATION_HARD_STOP_ERROR_CODES = frozenset(
    {
        "invalid_payload",
        "validation_failed",
        "unauthorized",
        "session_identity_unresolved",
        "session_identity_mismatch",
        "current_session_only_violation",
    }
)

DIRECT_POST_CREATION_OUTCOME_ACCEPTED = "accepted"
DIRECT_POST_CREATION_OUTCOME_FALLBACK_REQUIRED = "fallback_required"
DIRECT_POST_CREATION_OUTCOME_HARD_STOP = "hard_stop"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelayDirectActionError("invalid_payload", f"{field_name} must be a non-empty string")
    return value.strip()


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RelayDirectActionError("invalid_payload", f"{field_name} must be a dict")
    return dict(value)


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RelayDirectActionError("invalid_payload", f"{field_name} must be a string when present")
    text = value.strip()
    return text or None


def _build_direct_message_id(packet_id: str) -> str:
    normalized = _SAFE_MESSAGE_ID_RE.sub("-", str(packet_id or "").strip()).strip("-.")
    if not normalized:
        normalized = "packet"
    return f"<relay-direct-{normalized}@{_BOT_MESSAGE_ID_DOMAIN}>"


def _coerce_task_root(task_root: str | Path | None) -> Path | None:
    if task_root is None:
        return None
    normalized = str(task_root).strip()
    if not normalized:
        return None
    return Path(normalized)


def _post_creation_action_from_payloads(
    task_run_packet: dict[str, Any],
    dispatch_metadata: dict[str, Any],
) -> str | None:
    task_action = str(task_run_packet.get("action") or "").strip().lower()
    dispatch_action = str(dispatch_metadata.get("action") or "").strip().lower()
    if task_action and dispatch_action and task_action != dispatch_action:
        return None
    resolved = task_action or dispatch_action
    if resolved in {_DIRECT_ACTION_STATUS, _DIRECT_ACTION_REPLY}:
        return resolved
    return None


def _looks_like_post_creation_packet(task_run_packet: dict[str, Any], dispatch_metadata: dict[str, Any]) -> bool:
    task_schema = str(task_run_packet.get("schema_version") or "").strip()
    dispatch_schema = str(dispatch_metadata.get("schema_version") or "").strip()
    channel = str(dispatch_metadata.get("channel") or "").strip()
    action = _post_creation_action_from_payloads(task_run_packet, dispatch_metadata)
    return any(
        item
        for item in (
            task_schema == _POST_CREATION_SCHEMA_VERSION,
            dispatch_schema == _POST_CREATION_SCHEMA_VERSION,
            channel == _DIRECT_CHANNEL and action is not None,
        )
    )


def is_taskmail_post_creation_packet(message: RelayPacketMessage) -> bool:
    return _looks_like_post_creation_packet(message.task_run_packet, message.dispatch_metadata)


def is_taskmail_post_creation_action_packet(message: RelayPacketMessage, *, action: str) -> bool:
    if not _looks_like_post_creation_packet(message.task_run_packet, message.dispatch_metadata):
        return False
    return _post_creation_action_from_payloads(message.task_run_packet, message.dispatch_metadata) == action


def classify_direct_post_creation_error_code(error_code: str | None) -> str | None:
    normalized = str(error_code or "").strip()
    if not normalized:
        return None
    if normalized in _DIRECT_POST_CREATION_FALLBACK_REQUIRED_ERROR_CODES:
        return DIRECT_POST_CREATION_OUTCOME_FALLBACK_REQUIRED
    if normalized in _DIRECT_POST_CREATION_HARD_STOP_ERROR_CODES:
        return DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    return None


def classify_direct_post_creation_server_outcome(message: RelayPacketAckMessage | RelayErrorMessage) -> str:
    if isinstance(message, RelayPacketAckMessage):
        if message.accepted:
            return DIRECT_POST_CREATION_OUTCOME_ACCEPTED
        error_code = message.error_code
    elif isinstance(message, RelayErrorMessage):
        error_code = message.code
    else:
        raise TypeError("message must be RelayPacketAckMessage or RelayErrorMessage")
    outcome = classify_direct_post_creation_error_code(error_code)
    if outcome is None:
        raise ValueError(f"unsupported direct post-creation error code: {str(error_code or '').strip() or '<missing>'}")
    return outcome


@dataclass(slots=True)
class DirectCurrentSessionStatusPayload:
    request_id: str
    workspace_id: str
    session_id: str
    thread_id: str | None


@dataclass(slots=True)
class DirectCurrentSessionReplyPayload:
    request_id: str
    workspace_id: str
    session_id: str
    thread_id: str | None
    reply_text: str


def _reject_v1_attachments(task_payload: dict[str, Any]) -> None:
    if "attachments" not in task_payload:
        return
    attachments = task_payload.get("attachments")
    if attachments is None:
        return
    if not isinstance(attachments, list):
        raise RelayDirectActionError("invalid_payload", "task_run_packet.attachments must be a list when present")
    if attachments:
        raise RelayDirectActionError(
            "validation_failed",
            "attachment-bearing direct post-creation TaskMail actions are not available in v1",
        )


def _validate_direct_plain_reply_text(reply_text: str) -> str:
    normalized = _require_text(reply_text, "task_run_packet.reply.reply_text")
    if _STRUCTURED_ANSWER_RE.search(normalized):
        raise RelayDirectActionError(
            "validation_failed",
            "structured answer replies are not available in direct post-creation reply v1",
        )
    if _LEADING_COMMAND_RE.match(normalized):
        raise RelayDirectActionError(
            "validation_failed",
            "slash-command replies are not available in direct post-creation reply v1",
        )
    return normalized


def _parse_post_creation_common_payload(
    *,
    client_trace_id: str,
    task_run_packet: dict[str, Any],
    dispatch_metadata: dict[str, Any],
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    task_payload = _require_mapping(task_run_packet, "task_run_packet")
    dispatch_payload = _require_mapping(dispatch_metadata, "dispatch_metadata")

    task_schema = _require_text(task_payload.get("schema_version"), "task_run_packet.schema_version")
    dispatch_schema = _require_text(dispatch_payload.get("schema_version"), "dispatch_metadata.schema_version")
    if task_schema != _POST_CREATION_SCHEMA_VERSION or dispatch_schema != _POST_CREATION_SCHEMA_VERSION:
        raise RelayDirectActionError(
            "unsupported_action",
            f"only {_POST_CREATION_SCHEMA_VERSION} is supported for direct post-creation TaskMail actions",
        )

    task_action = _require_text(task_payload.get("action"), "task_run_packet.action").lower()
    dispatch_action = _require_text(dispatch_payload.get("action"), "dispatch_metadata.action").lower()
    if task_action != dispatch_action:
        raise RelayDirectActionError("invalid_payload", "task_run_packet.action must equal dispatch_metadata.action")
    if task_action not in {_DIRECT_ACTION_STATUS, _DIRECT_ACTION_REPLY}:
        raise RelayDirectActionError("unsupported_action", "unsupported post-creation direct TaskMail action")

    channel = _require_text(dispatch_payload.get("channel"), "dispatch_metadata.channel")
    if channel != _DIRECT_CHANNEL:
        raise RelayDirectActionError("invalid_payload", f"dispatch_metadata.channel must be {_DIRECT_CHANNEL}")

    fallback_policy = _require_text(dispatch_payload.get("fallback_policy"), "dispatch_metadata.fallback_policy")
    if fallback_policy != _FALLBACK_POLICY_MAIL:
        raise RelayDirectActionError("invalid_payload", "dispatch_metadata.fallback_policy must be mail")

    request_id = _require_text(task_payload.get("request_id"), "task_run_packet.request_id")
    if _require_text(client_trace_id, "client_trace_id") != request_id:
        raise RelayDirectActionError("invalid_payload", "client_trace_id must equal task_run_packet.request_id")

    origin = _require_mapping(task_payload.get("origin"), "task_run_packet.origin")
    origin_client = _require_text(origin.get("client"), "task_run_packet.origin.client")
    if origin_client != _DIRECT_ORIGIN_CLIENT:
        raise RelayDirectActionError("invalid_payload", f"origin.client must be {_DIRECT_ORIGIN_CLIENT}")

    target = _require_mapping(task_payload.get("target"), "task_run_packet.target")
    target_scope = _require_text(target.get("scope"), "task_run_packet.target.scope")
    if target_scope != _CURRENT_SESSION_SCOPE:
        raise RelayDirectActionError(
            "current_session_only_violation",
            "post-creation direct TaskMail actions currently support current_session only",
        )
    return task_payload, task_action, request_id, target


def _parse_direct_status_payload(
    *,
    client_trace_id: str,
    task_run_packet: dict[str, Any],
    dispatch_metadata: dict[str, Any],
) -> DirectCurrentSessionStatusPayload:
    task_payload, task_action, request_id, target = _parse_post_creation_common_payload(
        client_trace_id=client_trace_id,
        task_run_packet=task_run_packet,
        dispatch_metadata=dispatch_metadata,
    )
    if task_action != _DIRECT_ACTION_STATUS:
        raise RelayDirectActionError(
            "unsupported_action",
            "only direct action status is supported in the current post-creation TaskMail slice",
        )

    _reject_v1_attachments(task_payload)

    status_payload = _require_mapping(task_payload.get("status"), "task_run_packet.status")
    if status_payload:
        raise RelayDirectActionError("invalid_payload", "task_run_packet.status must be an empty object in v1")

    return DirectCurrentSessionStatusPayload(
        request_id=request_id,
        workspace_id=_require_text(target.get("workspace_id"), "task_run_packet.target.workspace_id"),
        session_id=_require_text(target.get("session_id"), "task_run_packet.target.session_id"),
        thread_id=_optional_text(target.get("thread_id"), "task_run_packet.target.thread_id"),
    )


def _parse_direct_reply_payload(
    *,
    client_trace_id: str,
    task_run_packet: dict[str, Any],
    dispatch_metadata: dict[str, Any],
) -> DirectCurrentSessionReplyPayload:
    task_payload, task_action, request_id, target = _parse_post_creation_common_payload(
        client_trace_id=client_trace_id,
        task_run_packet=task_run_packet,
        dispatch_metadata=dispatch_metadata,
    )
    if task_action != _DIRECT_ACTION_REPLY:
        raise RelayDirectActionError(
            "unsupported_action",
            "only direct action reply is supported in the current post-creation TaskMail slice",
        )

    _reject_v1_attachments(task_payload)

    reply_payload = _require_mapping(task_payload.get("reply"), "task_run_packet.reply")
    reply_text = _validate_direct_plain_reply_text(str(reply_payload.get("reply_text") or ""))

    return DirectCurrentSessionReplyPayload(
        request_id=request_id,
        workspace_id=_require_text(target.get("workspace_id"), "task_run_packet.target.workspace_id"),
        session_id=_require_text(target.get("session_id"), "task_run_packet.target.session_id"),
        thread_id=_optional_text(target.get("thread_id"), "task_run_packet.target.thread_id"),
        reply_text=reply_text,
    )


def _resolve_current_session_thread_state(
    payload: DirectCurrentSessionStatusPayload | DirectCurrentSessionReplyPayload,
    task_root: Path,
) -> ThreadState:
    try:
        session_state = load_session_state(payload.workspace_id, payload.session_id, task_root)
    except FileNotFoundError as exc:
        raise RelayDirectActionError(
            "session_identity_unresolved",
            "could not resolve a session for the requested workspace/session locator",
        ) from exc
    try:
        thread_state = load_thread_state(session_state.thread_id, task_root)
    except FileNotFoundError as exc:
        raise RelayDirectActionError(
            "session_identity_unresolved",
            "session_state exists but backing thread_state is missing",
        ) from exc

    if session_state.workspace_id != payload.workspace_id:
        raise RelayDirectActionError(
            "session_identity_mismatch",
            "workspace_id does not match the resolved canonical workspace for this session",
        )
    if session_state.session_id != payload.session_id:
        raise RelayDirectActionError(
            "session_identity_mismatch",
            "session_id does not match the resolved canonical session",
        )
    if session_state.thread_id != thread_state.thread_id:
        raise RelayDirectActionError(
            "session_identity_unresolved",
            "session_state and thread_state do not resolve to the same canonical thread",
        )
    if payload.thread_id is not None and payload.thread_id != thread_state.thread_id:
        raise RelayDirectActionError(
            "session_identity_mismatch",
            "thread_id does not match the resolved canonical thread",
        )
    return thread_state


def _validate_plain_reply_target_state(target_state: ThreadState) -> None:
    if target_state.status == THREAD_STATUS_AWAITING_USER_INPUT:
        raise RelayDirectActionError(
            "validation_failed",
            "direct plain reply is unavailable while the session is awaiting user input",
        )
    if target_state.status == THREAD_STATUS_PAUSED:
        raise RelayDirectActionError(
            "validation_failed",
            "direct plain reply is unavailable while the session is paused",
        )


def _build_status_subject(*, session_id: str, subject_text: str) -> str:
    return f"Re: [S:{session_id}] {subject_text}".strip()


def _subject_text_for_target_state(target_state: ThreadState) -> str:
    return target_state.session_name or target_state.subject_norm or target_state.thread_id


def _build_post_creation_target_identity(
    payload: DirectCurrentSessionStatusPayload | DirectCurrentSessionReplyPayload,
    target_state: ThreadState | None,
) -> dict[str, str] | None:
    return build_target_session_identity(
        workspace_id=payload.workspace_id if target_state is None else target_state.workspace_id,
        session_id=payload.session_id if target_state is None else target_state.session_id,
        thread_id=payload.thread_id if target_state is None else target_state.thread_id,
    )


def _build_post_creation_headers(
    *,
    packet_id: str,
    request_id: str,
    action_type: str,
    target_session_identity: dict[str, str] | None,
) -> dict[str, str]:
    headers = {
        "X-TaskMail-Direct": "1",
        "X-TaskMail-Relay-Packet-Id": packet_id,
        "X-TaskMail-Relay-Request-Id": request_id,
        ACTION_TYPE_HEADER: action_type,
    }
    if target_session_identity is not None:
        if target_session_identity.get("workspace_id") is not None:
            headers[TARGET_WORKSPACE_ID_HEADER] = target_session_identity["workspace_id"]
        if target_session_identity.get("session_id") is not None:
            headers[TARGET_SESSION_ID_HEADER] = target_session_identity["session_id"]
        if target_session_identity.get("thread_id") is not None:
            headers[TARGET_THREAD_ID_HEADER] = target_session_identity["thread_id"]
    return headers


def _build_status_body(state: ThreadState | dict[str, Any]) -> str:
    return f"/status\n\n{render_state_capsule(state)}\n"


def _build_status_html(subject: str, body: str) -> str:
    return (
        "<html><body><article class=\"task-mail\">"
        f"<h1>{escape(subject)}</h1>"
        f"<pre>{escape(body)}</pre>"
        "</article></body></html>"
    )


def _build_reply_body(reply_text: str, capsule_state: ThreadState | dict[str, Any]) -> str:
    return f"{reply_text.strip()}\n\n{render_state_capsule(capsule_state)}\n"


def _build_thread_reply_chain(state: ThreadState) -> tuple[str | None, list[str]]:
    reply_to = str(state.latest_message_id or state.root_message_id or "").strip() or None
    references: list[str] = []
    for candidate in (state.root_message_id, state.latest_message_id):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in references:
            references.append(normalized)
    return reply_to, references


def _status_label_for_current_session_query(target_state: ThreadState) -> str:
    return MAIL_STATUS_PAUSED if target_state.status == THREAD_STATUS_PAUSED else MAIL_STATUS_STATUS


def _build_post_creation_status_closeout_subject(target_state: ThreadState) -> str:
    return build_status_subject(
        _status_label_for_current_session_query(target_state),
        _subject_text_for_target_state(target_state),
        target_state.session_id or target_state.thread_id,
    )


def _build_status_envelope(
    *,
    packet_id: str,
    payload: DirectCurrentSessionStatusPayload,
    from_addr: str,
    to_addr: str,
    date: str,
    subject_text: str,
    capsule_state: ThreadState | dict[str, Any],
    target_session_identity: dict[str, str] | None,
) -> MailEnvelope:
    subject = _build_status_subject(session_id=payload.session_id, subject_text=subject_text)
    return MailEnvelope(
        message_id=_build_direct_message_id(packet_id),
        subject=subject,
        from_addr=from_addr,
        to_addr=to_addr,
        date=date,
        body_text=_build_status_body(capsule_state),
        raw_headers={
            "Subject": subject,
            **_build_post_creation_headers(
                packet_id=packet_id,
                request_id=payload.request_id,
                action_type=_DIRECT_ACTION_STATUS,
                target_session_identity=target_session_identity,
            ),
        },
    )


class RelayTaskMailDirectCurrentSessionStatusHandler:
    """Accepts direct current-session /status packets and reuses the existing-thread mail path locally."""

    transport_name = "relay_direct_post_creation_status"

    def __init__(
        self,
        *,
        config: AppConfig,
        task_root: str | Path,
        mail_client: Any,
        runner: SerialTaskRunner,
        recipient_addr: str,
        background: bool = True,
    ) -> None:
        self._config = config
        self._task_root = Path(task_root)
        self._mail_client = mail_client
        self._runner = runner
        self._recipient_addr = _require_text(recipient_addr, "recipient_addr")
        self._background = bool(background)

    def matches(self, message: RelayPacketMessage) -> bool:
        return is_taskmail_post_creation_action_packet(message, action=_DIRECT_ACTION_STATUS)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        _parse_direct_status_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        payload = _parse_direct_status_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        target_state = _resolve_current_session_thread_state(payload, self._task_root)
        bot_addr = (
            str(self._config.from_addr or "").strip()
            or str(self._config.smtp_user or "").strip()
            or str(self._config.imap_user or "").strip()
        )
        if not bot_addr:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "bot mailbox address is not configured for direct TaskMail status acceptance",
            )

        target_session_identity = _build_post_creation_target_identity(payload, target_state)
        subject_text = _subject_text_for_target_state(target_state)
        envelope = _build_status_envelope(
            packet_id=packet.packet_id,
            payload=payload,
            from_addr=self._recipient_addr,
            to_addr=bot_addr,
            date=packet.received_at,
            subject_text=subject_text,
            capsule_state=target_state,
            target_session_identity=target_session_identity,
        )
        subject_info = parse_subject(envelope.subject)
        from ..app import _process_existing_thread_mail

        handled = _process_existing_thread_mail(
            envelope,
            subject_info,
            {
                "workspace_id": payload.workspace_id,
                "session_id": payload.session_id,
                "thread_id": target_state.thread_id,
            },
            self._config,
            self._task_root,
            self._mail_client,
            self._runner,
            background=self._background,
        )
        if not handled:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "direct TaskMail current-session status could not be processed",
            )

        try:
            write_session_action_closeout(
                self._task_root,
                thread_id=target_state.thread_id,
                action_type=_DIRECT_ACTION_STATUS,
                request_id=payload.request_id,
                ingress_message_id=envelope.message_id,
                packet_id=packet.packet_id,
                terminal_mail_subject=_build_post_creation_status_closeout_subject(target_state),
                last_summary=target_state.last_summary,
                target_session_identity=target_session_identity,
            )
        except Exception:
            LOGGER.exception(
                "Unable to write direct current-session status closeout. thread=%s request_id=%s",
                target_state.thread_id,
                payload.request_id,
            )
        return TransportReceipt(
            success=True,
            transport_name=self.transport_name,
            sent_at=_timestamp(),
        )


class RelayTaskMailDirectCurrentSessionStatusMailBridge:
    """Bridges accepted direct current-session /status packets into the bot mailbox mail ingress."""

    transport_name = "relay_direct_post_creation_mail_bridge"

    def __init__(
        self,
        config: RelayServerConfig,
        *,
        mail_client: MailClient | None = None,
        task_root: str | Path | None = None,
    ) -> None:
        self._config = config
        self._mail_client = mail_client or MailClient(config.to_taskmail_direct_mail_config())
        self._bot_mailbox_addr = _require_text(config.taskmail_bot_mailbox_addr, "taskmail_bot_mailbox_addr")
        self._task_root = _coerce_task_root(task_root)

    def matches(self, message: RelayPacketMessage) -> bool:
        return is_taskmail_post_creation_action_packet(message, action=_DIRECT_ACTION_STATUS)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        payload = _parse_direct_status_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )
        if self._task_root is not None:
            _resolve_current_session_thread_state(payload, self._task_root)

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        payload = _parse_direct_status_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        target_state = _resolve_current_session_thread_state(payload, self._task_root) if self._task_root is not None else None
        target_session_identity = _build_post_creation_target_identity(payload, target_state)
        subject = _build_status_subject(
            session_id=payload.session_id,
            subject_text=(
                _subject_text_for_target_state(target_state)
                if target_state is not None
                else payload.session_id
            ),
        )
        body = _build_status_body(
            target_state
            if target_state is not None
            else {
                "workspace_id": payload.workspace_id,
                "session_id": payload.session_id,
                "thread_id": payload.thread_id or "",
            }
        )
        html_body = _build_status_html(subject, body)
        try:
            message_id = self._mail_client.send_mail(
                to_addr=self._bot_mailbox_addr,
                subject=subject,
                body=body,
                html_body=html_body,
                headers=_build_post_creation_headers(
                    packet_id=packet.packet_id,
                    request_id=payload.request_id,
                    action_type=_DIRECT_ACTION_STATUS,
                    target_session_identity=target_session_identity,
                ),
            )
        except Exception as exc:
            message = str(exc).strip() or "failed to bridge direct TaskMail status packet into bot mailbox mail ingress"
            raise RelayDirectActionError("direct_temporarily_unavailable", message) from exc

        if target_state is not None and self._task_root is not None:
            try:
                write_session_action_closeout(
                    self._task_root,
                    thread_id=target_state.thread_id,
                    action_type=_DIRECT_ACTION_STATUS,
                    request_id=payload.request_id,
                    ingress_message_id=message_id,
                    packet_id=packet.packet_id,
                    terminal_mail_subject=_build_post_creation_status_closeout_subject(target_state),
                    last_summary=target_state.last_summary,
                    target_session_identity=target_session_identity,
                )
            except Exception:
                LOGGER.exception(
                    "Unable to write direct current-session status closeout. thread=%s request_id=%s",
                    target_state.thread_id,
                    payload.request_id,
                )
        return TransportReceipt(
            success=True,
            transport_name=self.transport_name,
            sent_at=_timestamp(),
            transport_message_id=message_id,
        )


class RelayTaskMailDirectCurrentSessionReplyHandler:
    """Accepts direct current-session plain reply packets and reuses the existing-thread mail path locally."""

    transport_name = "relay_direct_post_creation_reply"

    def __init__(
        self,
        *,
        config: AppConfig,
        task_root: str | Path,
        mail_client: Any,
        runner: SerialTaskRunner,
        recipient_addr: str,
        background: bool = True,
    ) -> None:
        self._config = config
        self._task_root = Path(task_root)
        self._mail_client = mail_client
        self._runner = runner
        self._recipient_addr = _require_text(recipient_addr, "recipient_addr")
        self._background = bool(background)

    def matches(self, message: RelayPacketMessage) -> bool:
        return is_taskmail_post_creation_action_packet(message, action=_DIRECT_ACTION_REPLY)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        _parse_direct_reply_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        payload = _parse_direct_reply_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        target_state = _resolve_current_session_thread_state(payload, self._task_root)
        _validate_plain_reply_target_state(target_state)
        bot_addr = (
            str(self._config.from_addr or "").strip()
            or str(self._config.smtp_user or "").strip()
            or str(self._config.imap_user or "").strip()
        )
        if not bot_addr:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "bot mailbox address is not configured for direct TaskMail reply acceptance",
            )

        subject_text = target_state.session_name or target_state.subject_norm or target_state.thread_id
        reply_to, references = _build_thread_reply_chain(target_state)
        subject = _build_status_subject(session_id=payload.session_id, subject_text=subject_text)
        target_session_identity = _build_post_creation_target_identity(payload, target_state)
        envelope = MailEnvelope(
            message_id=_build_direct_message_id(packet.packet_id),
            subject=subject,
            from_addr=self._recipient_addr,
            to_addr=bot_addr,
            date=packet.received_at,
            in_reply_to=reply_to,
            references=references,
            body_text=_build_reply_body(payload.reply_text, target_state),
            raw_headers={
                "Subject": subject,
                **_build_post_creation_headers(
                    packet_id=packet.packet_id,
                    request_id=payload.request_id,
                    action_type=_DIRECT_ACTION_REPLY,
                    target_session_identity=target_session_identity,
                ),
            },
        )
        subject_info = parse_subject(envelope.subject)
        from ..app import _process_existing_thread_mail

        handled = _process_existing_thread_mail(
            envelope,
            subject_info,
            {
                "workspace_id": payload.workspace_id,
                "session_id": payload.session_id,
                "thread_id": target_state.thread_id,
            },
            self._config,
            self._task_root,
            self._mail_client,
            self._runner,
            background=self._background,
        )
        if not handled:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "direct TaskMail current-session reply could not be processed",
            )

        return TransportReceipt(
            success=True,
            transport_name=self.transport_name,
            sent_at=_timestamp(),
        )


class RelayTaskMailDirectCurrentSessionReplyMailBridge:
    """Bridges accepted direct current-session plain reply packets into the bot mailbox mail ingress."""

    transport_name = "relay_direct_post_creation_reply_mail_bridge"

    def __init__(
        self,
        config: RelayServerConfig,
        *,
        mail_client: MailClient | None = None,
        task_root: str | Path | None = None,
    ) -> None:
        self._config = config
        self._mail_client = mail_client or MailClient(config.to_taskmail_direct_mail_config())
        self._bot_mailbox_addr = _require_text(config.taskmail_bot_mailbox_addr, "taskmail_bot_mailbox_addr")
        self._task_root = _coerce_task_root(task_root)

    def matches(self, message: RelayPacketMessage) -> bool:
        return is_taskmail_post_creation_action_packet(message, action=_DIRECT_ACTION_REPLY)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        payload = _parse_direct_reply_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )
        if self._task_root is not None:
            target_state = _resolve_current_session_thread_state(payload, self._task_root)
            _validate_plain_reply_target_state(target_state)

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        payload = _parse_direct_reply_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        target_state = _resolve_current_session_thread_state(payload, self._task_root) if self._task_root is not None else None
        reply_to: str | None = None
        references: list[str] | None = None
        if target_state is not None:
            _validate_plain_reply_target_state(target_state)
            reply_to, references = _build_thread_reply_chain(target_state)
        target_session_identity = _build_post_creation_target_identity(payload, target_state)
        subject = _build_status_subject(
            session_id=payload.session_id,
            subject_text=(
                _subject_text_for_target_state(target_state)
                if target_state is not None
                else payload.session_id
            ),
        )
        body = _build_reply_body(
            payload.reply_text,
            target_state
            if target_state is not None
            else {
                "workspace_id": payload.workspace_id,
                "session_id": payload.session_id,
                "thread_id": payload.thread_id or "",
            },
        )
        html_body = _build_status_html(subject, body)
        try:
            message_id = self._mail_client.send_mail(
                to_addr=self._bot_mailbox_addr,
                subject=subject,
                body=body,
                html_body=html_body,
                in_reply_to=reply_to,
                references=references,
                headers=_build_post_creation_headers(
                    packet_id=packet.packet_id,
                    request_id=payload.request_id,
                    action_type=_DIRECT_ACTION_REPLY,
                    target_session_identity=target_session_identity,
                ),
            )
        except Exception as exc:
            message = str(exc).strip() or "failed to bridge direct TaskMail reply packet into bot mailbox mail ingress"
            raise RelayDirectActionError("direct_temporarily_unavailable", message) from exc

        return TransportReceipt(
            success=True,
            transport_name=self.transport_name,
            sent_at=_timestamp(),
            transport_message_id=message_id,
        )
