"""Phase 2 direct TaskMail action helpers for relay packet handling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Protocol

from ..config import AppConfig
from ..mail_io import MailClient
from ..models import MailEnvelope
from ..outbound.contract import TransportReceipt
from ..parser import parse_subject
from ..runner import SerialTaskRunner
from ..status import BACKEND_CODEX, BACKEND_OPENCODE
from .config import RelayServerConfig
from .packet_store import AcceptedRelayPacket
from .protocol import RelayErrorMessage, RelayPacketAckMessage, RelayPacketMessage

_PHASE2_SCHEMA_VERSION = "phase2-direct-outbound-contract-v1"
_DIRECT_CHANNEL = "taskmail_android_direct"
_DIRECT_ACTION_NEW_TASK = "new_task"
_DIRECT_ORIGIN_CLIENT = "android_taskmail"
_FALLBACK_POLICY_MAIL = "mail"
_DIRECT_TRANSPORT_NAME = "relay_direct_new_task"
_BOT_MESSAGE_ID_DOMAIN = "mail-runner.local"
_SAFE_MESSAGE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SUPPORTED_BACKENDS = {BACKEND_OPENCODE, BACKEND_CODEX}
_SUPPORTED_MODES = {"modify", "analysis_only"}
_SUPPORTED_PERMISSIONS = {"default", "highest"}
_DIRECT_NEW_TASK_FALLBACK_CLASSIFIED_ERROR_CODES = frozenset({"unsupported_action", "direct_temporarily_unavailable"})
_DIRECT_NEW_TASK_HARD_REJECTION_ERROR_CODES = frozenset({"invalid_payload", "validation_failed", "unauthorized"})

DIRECT_NEW_TASK_OUTCOME_ACCEPTED = "accepted"
DIRECT_NEW_TASK_OUTCOME_FALLBACK_CLASSIFIED_REJECTION = "fallback_classified_rejection"
DIRECT_NEW_TASK_OUTCOME_HARD_REJECTION = "hard_rejection"


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


def _normalize_acceptance(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RelayDirectActionError("invalid_payload", "new_task.acceptance must be a list[str]")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RelayDirectActionError("invalid_payload", "new_task.acceptance items must be non-empty strings")
        normalized.append(item.strip())
    return normalized


def _normalize_timeout_minutes(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise RelayDirectActionError("invalid_payload", "new_task.timeout_minutes must be a positive integer")
    return value


def _normalize_mode(value: Any) -> str:
    if value is None:
        return "modify"
    if not isinstance(value, str):
        raise RelayDirectActionError("invalid_payload", "new_task.mode must be a string when present")
    mode = value.strip().lower()
    if mode not in _SUPPORTED_MODES:
        raise RelayDirectActionError("invalid_payload", "new_task.mode must be 'modify' or 'analysis_only'")
    return mode


def _normalize_permission(value: Any) -> str | None:
    text = _optional_text(value, "new_task.permission")
    if text is None:
        return None
    normalized = text.lower()
    if normalized not in _SUPPORTED_PERMISSIONS:
        raise RelayDirectActionError("invalid_payload", "new_task.permission must be 'default' or 'highest'")
    return normalized


def _looks_like_direct_packet(task_run_packet: dict[str, Any], dispatch_metadata: dict[str, Any]) -> bool:
    task_schema = str(task_run_packet.get("schema_version") or "").strip()
    dispatch_schema = str(dispatch_metadata.get("schema_version") or "").strip()
    channel = str(dispatch_metadata.get("channel") or "").strip()
    action = str(task_run_packet.get("action") or dispatch_metadata.get("action") or "").strip()
    return any(
        item
        for item in (
            task_schema == _PHASE2_SCHEMA_VERSION,
            dispatch_schema == _PHASE2_SCHEMA_VERSION,
            channel == _DIRECT_CHANNEL,
            action == _DIRECT_ACTION_NEW_TASK,
        )
    )


def is_taskmail_direct_packet(message: RelayPacketMessage) -> bool:
    return _looks_like_direct_packet(message.task_run_packet, message.dispatch_metadata)


def classify_direct_new_task_error_code(error_code: str | None) -> str | None:
    normalized = str(error_code or "").strip()
    if not normalized:
        return None
    if normalized in _DIRECT_NEW_TASK_FALLBACK_CLASSIFIED_ERROR_CODES:
        return DIRECT_NEW_TASK_OUTCOME_FALLBACK_CLASSIFIED_REJECTION
    if normalized in _DIRECT_NEW_TASK_HARD_REJECTION_ERROR_CODES:
        return DIRECT_NEW_TASK_OUTCOME_HARD_REJECTION
    return None


def classify_direct_new_task_server_outcome(message: RelayPacketAckMessage | RelayErrorMessage) -> str:
    if isinstance(message, RelayPacketAckMessage):
        if message.accepted:
            return DIRECT_NEW_TASK_OUTCOME_ACCEPTED
        error_code = message.error_code
    elif isinstance(message, RelayErrorMessage):
        error_code = message.code
    else:
        raise TypeError("message must be RelayPacketAckMessage or RelayErrorMessage")
    outcome = classify_direct_new_task_error_code(error_code)
    if outcome is None:
        raise ValueError(f"unsupported direct new_task error code: {str(error_code or '').strip() or '<missing>'}")
    return outcome


@dataclass(slots=True)
class DirectNewTaskPayload:
    backend: str
    repo_path: str
    workdir: str | None
    task_text: str
    subject_title: str
    timeout_minutes: int | None
    mode: str
    profile: str | None
    permission: str | None
    acceptance: list[str]
    request_id: str
    sender_account_uuid: str | None


class RelayDirectActionError(Exception):
    """Structured direct-action rejection that maps to a relay error frame."""

    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


class RelayDirectPacketHandler(Protocol):
    def matches(self, message: RelayPacketMessage) -> bool: ...

    def validate_packet(self, message: RelayPacketMessage) -> None: ...

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt: ...


class RelayTaskMailDirectNewTaskHandler:
    """Accepts the first direct Android TaskMail new_task packet and reuses the mail-side task start path."""

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
        return is_taskmail_direct_packet(message)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        _parse_direct_new_task_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )
        if "attachments" in message.task_run_packet:
            attachments = message.task_run_packet.get("attachments") or []
            if attachments:
                raise RelayDirectActionError(
                    "unsupported_action",
                    "attachment-bearing direct TaskMail actions are not available in phase2 v1",
                )

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        payload = _parse_direct_new_task_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        bot_addr = (
            str(self._config.from_addr or "").strip()
            or str(self._config.smtp_user or "").strip()
            or str(self._config.imap_user or "").strip()
        )
        if not bot_addr:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "bot mailbox address is not configured for direct TaskMail acceptance",
            )

        subject = _new_task_subject(payload.backend, payload.subject_title)
        envelope = MailEnvelope(
            message_id=_build_direct_message_id(packet.packet_id),
            subject=subject,
            from_addr=self._recipient_addr,
            to_addr=bot_addr,
            date=packet.received_at,
            body_text=_build_initial_task_body(payload),
            raw_headers={
                "Subject": subject,
                "X-TaskMail-Direct": "1",
                "X-TaskMail-Relay-Packet-Id": packet.packet_id,
                "X-TaskMail-Relay-Request-Id": payload.request_id,
            },
        )
        subject_info = parse_subject(subject)
        from ..app import _process_new_task_mail

        started = _process_new_task_mail(
            envelope,
            subject_info,
            self._config,
            self._task_root,
            self._mail_client,
            self._runner,
            background=self._background,
        )
        if not started:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "direct TaskMail new_task could not be started",
            )

        return TransportReceipt(
            success=True,
            transport_name=_DIRECT_TRANSPORT_NAME,
            sent_at=_timestamp(),
        )


class RelayTaskMailDirectNewTaskMailBridge:
    """Bridges accepted direct new_task packets back into the current bot-mailbox ingress path."""

    def __init__(self, config: RelayServerConfig, *, mail_client: MailClient | None = None) -> None:
        self._config = config
        self._mail_client = mail_client or MailClient(config.to_taskmail_direct_mail_config())
        self._bot_mailbox_addr = _require_text(config.taskmail_bot_mailbox_addr, "taskmail_bot_mailbox_addr")

    def matches(self, message: RelayPacketMessage) -> bool:
        return is_taskmail_direct_packet(message)

    def validate_packet(self, message: RelayPacketMessage) -> None:
        _parse_direct_new_task_payload(
            client_trace_id=message.client_trace_id,
            task_run_packet=message.task_run_packet,
            dispatch_metadata=message.dispatch_metadata,
        )
        if "attachments" in message.task_run_packet:
            attachments = message.task_run_packet.get("attachments") or []
            if attachments:
                raise RelayDirectActionError(
                    "unsupported_action",
                    "attachment-bearing direct TaskMail actions are not available in phase2 v1",
                )

    def handle_accepted_packet(self, packet: AcceptedRelayPacket) -> TransportReceipt:
        payload = _parse_direct_new_task_payload(
            client_trace_id=packet.client_trace_id,
            task_run_packet=packet.task_run_packet,
            dispatch_metadata=packet.dispatch_metadata,
        )
        subject = _new_task_subject(payload.backend, payload.subject_title)
        body = _build_initial_task_body(payload)
        html_body = _build_initial_task_html(subject, body)
        try:
            message_id = self._mail_client.send_mail(
                to_addr=self._bot_mailbox_addr,
                subject=subject,
                body=body,
                html_body=html_body,
                headers={
                    "X-TaskMail-Direct": "1",
                    "X-TaskMail-Relay-Packet-Id": packet.packet_id,
                    "X-TaskMail-Relay-Request-Id": payload.request_id,
                },
            )
        except Exception as exc:
            message = str(exc).strip() or "failed to bridge direct TaskMail packet into bot mailbox mail ingress"
            raise RelayDirectActionError("direct_temporarily_unavailable", message) from exc

        return TransportReceipt(
            success=True,
            transport_name="relay_direct_mail_bridge",
            sent_at=_timestamp(),
            transport_message_id=message_id,
        )


def _parse_direct_new_task_payload(
    *,
    client_trace_id: str,
    task_run_packet: dict[str, Any],
    dispatch_metadata: dict[str, Any],
) -> DirectNewTaskPayload:
    task_payload = _require_mapping(task_run_packet, "task_run_packet")
    dispatch_payload = _require_mapping(dispatch_metadata, "dispatch_metadata")

    task_schema = _require_text(task_payload.get("schema_version"), "task_run_packet.schema_version")
    dispatch_schema = _require_text(dispatch_payload.get("schema_version"), "dispatch_metadata.schema_version")
    if task_schema != _PHASE2_SCHEMA_VERSION or dispatch_schema != _PHASE2_SCHEMA_VERSION:
        raise RelayDirectActionError(
            "unsupported_action",
            f"only {_PHASE2_SCHEMA_VERSION} is supported for direct TaskMail actions",
        )

    task_action = _require_text(task_payload.get("action"), "task_run_packet.action").lower()
    dispatch_action = _require_text(dispatch_payload.get("action"), "dispatch_metadata.action").lower()
    if task_action != _DIRECT_ACTION_NEW_TASK or dispatch_action != _DIRECT_ACTION_NEW_TASK:
        raise RelayDirectActionError("unsupported_action", "only direct action new_task is supported in phase2 v1")

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
    sender_account_uuid = _optional_text(origin.get("sender_account_uuid"), "task_run_packet.origin.sender_account_uuid")

    new_task = _require_mapping(task_payload.get("new_task"), "task_run_packet.new_task")
    backend = _require_text(new_task.get("backend"), "new_task.backend").lower()
    if backend not in _SUPPORTED_BACKENDS:
        raise RelayDirectActionError("invalid_payload", "new_task.backend must be codex or opencode")

    return DirectNewTaskPayload(
        backend=backend,
        repo_path=_require_text(new_task.get("repo_path"), "new_task.repo_path"),
        workdir=_optional_text(new_task.get("workdir"), "new_task.workdir"),
        task_text=_require_text(new_task.get("task_text"), "new_task.task_text"),
        subject_title=_require_text(new_task.get("subject_title"), "new_task.subject_title"),
        timeout_minutes=_normalize_timeout_minutes(new_task.get("timeout_minutes")),
        mode=_normalize_mode(new_task.get("mode")),
        profile=_optional_text(new_task.get("profile"), "new_task.profile"),
        permission=_normalize_permission(new_task.get("permission")),
        acceptance=_normalize_acceptance(new_task.get("acceptance")),
        request_id=request_id,
        sender_account_uuid=sender_account_uuid,
    )


def _build_initial_task_body(payload: DirectNewTaskPayload) -> str:
    lines = [f"Repo: {payload.repo_path}"]
    if payload.workdir:
        lines.append(f"Workdir: {payload.workdir}")
    if payload.timeout_minutes is not None:
        lines.append(f"Timeout: {payload.timeout_minutes}")
    if payload.mode:
        lines.append(f"Mode: {payload.mode}")
    if payload.profile:
        lines.append(f"Profile: {payload.profile}")
    if payload.permission:
        lines.append(f"Permission: {payload.permission}")
    lines.extend(("", "Task:"))
    lines.extend(payload.task_text.splitlines() or [payload.task_text])
    if payload.acceptance:
        lines.extend(("", "Acceptance:"))
        lines.extend(f"- {item}" for item in payload.acceptance)
    return "\n".join(lines).strip() + "\n"


def _new_task_subject(backend: str, subject_title: str) -> str:
    prefix = "[CX]" if backend == BACKEND_CODEX else "[OC]"
    return f"{prefix} {subject_title}".strip()


def _build_initial_task_html(subject: str, body: str) -> str:
    return (
        "<html><body><article class=\"task-mail\">"
        f"<h1>{escape(subject)}</h1>"
        f"<pre>{escape(body)}</pre>"
        "</article></body></html>"
    )


def _build_direct_message_id(packet_id: str) -> str:
    normalized = _SAFE_MESSAGE_ID_RE.sub("-", str(packet_id or "").strip()).strip("-.")
    if not normalized:
        normalized = "packet"
    return f"<relay-direct-{normalized}@{_BOT_MESSAGE_ID_DOMAIN}>"
