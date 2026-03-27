"""Ingress-truth stores for the VPS-first PC control plane."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from ..thread_store import build_workspace_id, build_workspace_norm


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_int(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _require_optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name, minimum=minimum)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(slots=True)
class MailboxLeaseRecord:
    mailbox_key: str
    lease_holder_id: str
    pc_id: str
    lease_epoch: int
    status: str
    acquired_at: str
    renewed_at: str
    expires_at: str
    config_fingerprint: str | None = None
    host_fingerprint: str | None = None
    runtime_fingerprint: str | None = None
    last_seen_thread_id: str | None = None
    last_seen_ingress_id: str | None = None

    def __post_init__(self) -> None:
        self.mailbox_key = _require_text(self.mailbox_key, "mailbox_key")
        self.lease_holder_id = _require_text(self.lease_holder_id, "lease_holder_id")
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.lease_epoch = _require_int(self.lease_epoch, "lease_epoch", minimum=1)
        self.status = _require_text(self.status, "status")
        if self.status not in {"active", "released"}:
            raise ValueError("status must be one of: active, released")
        self.acquired_at = _require_text(self.acquired_at, "acquired_at")
        self.renewed_at = _require_text(self.renewed_at, "renewed_at")
        self.expires_at = _require_text(self.expires_at, "expires_at")
        self.config_fingerprint = _require_optional_text(self.config_fingerprint, "config_fingerprint")
        self.host_fingerprint = _require_optional_text(self.host_fingerprint, "host_fingerprint")
        self.runtime_fingerprint = _require_optional_text(self.runtime_fingerprint, "runtime_fingerprint")
        self.last_seen_thread_id = _require_optional_text(self.last_seen_thread_id, "last_seen_thread_id")
        self.last_seen_ingress_id = _require_optional_text(self.last_seen_ingress_id, "last_seen_ingress_id")


@dataclass(slots=True)
class MailboxLeaseEvent:
    event_id: str
    mailbox_key: str
    operation: str
    lease_holder_id: str
    pc_id: str
    observed_at: str
    lease_epoch: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        self.event_id = _require_text(self.event_id, "event_id")
        self.mailbox_key = _require_text(self.mailbox_key, "mailbox_key")
        self.operation = _require_text(self.operation, "operation")
        if self.operation not in {"acquired", "renewed", "released", "denied"}:
            raise ValueError("operation must be one of: acquired, renewed, released, denied")
        self.lease_holder_id = _require_text(self.lease_holder_id, "lease_holder_id")
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.observed_at = _require_text(self.observed_at, "observed_at")
        self.lease_epoch = _require_optional_int(self.lease_epoch, "lease_epoch", minimum=1)
        self.reason = _require_optional_text(self.reason, "reason")


@dataclass(slots=True)
class IngressLedgerRecord:
    ingress_id: str
    mailbox_key: str
    folder: str
    message_id: str
    from_addr: str
    subject: str
    subject_norm: str
    observed_at: str
    classification: str
    decision: str
    lease_holder_id: str
    pc_id: str
    lease_epoch: int
    uid_validity: int | None = None
    uid: int | None = None
    in_reply_to: str | None = None
    references_hash: str | None = None
    raw_date: str | None = None
    dedupe_key_uid: str | None = None
    dedupe_key_message_id: str | None = None
    decision_reason: str | None = None
    thread_id: str | None = None
    session_id: str | None = None
    taskmail_request_id: str | None = None
    packet_id: str | None = None
    accepted_at: str | None = None
    closed_at: str | None = None
    degraded_mode: bool = False

    def __post_init__(self) -> None:
        self.ingress_id = _require_text(self.ingress_id, "ingress_id")
        self.mailbox_key = _require_text(self.mailbox_key, "mailbox_key")
        self.folder = _require_text(self.folder, "folder")
        self.message_id = _require_text(self.message_id, "message_id")
        self.from_addr = _require_text(self.from_addr, "from_addr")
        self.subject = _require_text(self.subject, "subject")
        self.subject_norm = _require_text(self.subject_norm, "subject_norm")
        self.observed_at = _require_text(self.observed_at, "observed_at")
        self.classification = _require_text(self.classification, "classification")
        if self.classification not in {"new_task", "reply", "sync", "direct_kill", "system_mail", "unsupported"}:
            raise ValueError("classification must be a supported ingress classification")
        self.decision = _require_text(self.decision, "decision")
        if self.decision not in {"accepted", "duplicate", "stale", "invalid", "ignored", "lease_denied"}:
            raise ValueError("decision must be a supported ingress decision")
        self.lease_holder_id = _require_text(self.lease_holder_id, "lease_holder_id")
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.lease_epoch = _require_int(self.lease_epoch, "lease_epoch", minimum=1)
        self.uid_validity = _require_optional_int(self.uid_validity, "uid_validity", minimum=1)
        self.uid = _require_optional_int(self.uid, "uid", minimum=1)
        self.in_reply_to = _require_optional_text(self.in_reply_to, "in_reply_to")
        self.references_hash = _require_optional_text(self.references_hash, "references_hash")
        self.raw_date = _require_optional_text(self.raw_date, "raw_date")
        self.dedupe_key_uid = _require_optional_text(self.dedupe_key_uid, "dedupe_key_uid")
        self.dedupe_key_message_id = _require_optional_text(self.dedupe_key_message_id, "dedupe_key_message_id")
        self.decision_reason = _require_optional_text(self.decision_reason, "decision_reason")
        self.thread_id = _require_optional_text(self.thread_id, "thread_id")
        self.session_id = _require_optional_text(self.session_id, "session_id")
        self.taskmail_request_id = _require_optional_text(self.taskmail_request_id, "taskmail_request_id")
        self.packet_id = _require_optional_text(self.packet_id, "packet_id")
        self.accepted_at = _require_optional_text(self.accepted_at, "accepted_at")
        self.closed_at = _require_optional_text(self.closed_at, "closed_at")
        if not isinstance(self.degraded_mode, bool):
            raise ValueError("degraded_mode must be a bool")


@dataclass(slots=True)
class CanonicalThreadBindingRecord:
    ingress_id: str
    mailbox_key: str
    root_message_id: str
    thread_id: str
    session_id: str
    repo_path: str
    workdir: str | None
    subject_norm: str
    binding_created_at: str
    lease_holder_id: str
    pc_id: str
    lease_epoch: int
    workspace_id: str | None = None
    workspace_norm: str | None = None
    degraded_mode: bool = False

    def __post_init__(self) -> None:
        self.ingress_id = _require_text(self.ingress_id, "ingress_id")
        self.mailbox_key = _require_text(self.mailbox_key, "mailbox_key")
        self.root_message_id = _require_text(self.root_message_id, "root_message_id")
        self.thread_id = _require_text(self.thread_id, "thread_id")
        self.session_id = _require_text(self.session_id, "session_id")
        self.repo_path = _require_text(self.repo_path, "repo_path")
        self.workdir = _require_optional_text(self.workdir, "workdir")
        self.workspace_id = _require_optional_text(self.workspace_id, "workspace_id")
        self.workspace_norm = _require_optional_text(self.workspace_norm, "workspace_norm")
        self.workspace_id = self.workspace_id or build_workspace_id(self.repo_path, self.workdir)
        self.workspace_norm = self.workspace_norm or build_workspace_norm(self.repo_path, self.workdir)
        self.subject_norm = _require_text(self.subject_norm, "subject_norm")
        self.binding_created_at = _require_text(self.binding_created_at, "binding_created_at")
        self.lease_holder_id = _require_text(self.lease_holder_id, "lease_holder_id")
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.lease_epoch = _require_int(self.lease_epoch, "lease_epoch", minimum=1)
        if not isinstance(self.degraded_mode, bool):
            raise ValueError("degraded_mode must be a bool")


@dataclass(slots=True)
class TerminalOutcomeRecord:
    thread_id: str
    task_id: str
    run_status: str
    generated_at: str
    lease_holder_id: str
    pc_id: str
    lease_epoch: int
    last_summary: str | None = None
    terminal_mail_message_id: str | None = None
    terminal_mail_subject: str | None = None
    taskmail_request_id: str | None = None
    packet_id: str | None = None
    source_ingress_id: str | None = None
    degraded_mode: bool = False

    def __post_init__(self) -> None:
        self.thread_id = _require_text(self.thread_id, "thread_id")
        self.task_id = _require_text(self.task_id, "task_id")
        self.run_status = _require_text(self.run_status, "run_status")
        self.generated_at = _require_text(self.generated_at, "generated_at")
        self.lease_holder_id = _require_text(self.lease_holder_id, "lease_holder_id")
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.lease_epoch = _require_int(self.lease_epoch, "lease_epoch", minimum=1)
        self.last_summary = _require_optional_text(self.last_summary, "last_summary")
        self.terminal_mail_message_id = _require_optional_text(
            self.terminal_mail_message_id,
            "terminal_mail_message_id",
        )
        self.terminal_mail_subject = _require_optional_text(self.terminal_mail_subject, "terminal_mail_subject")
        self.taskmail_request_id = _require_optional_text(self.taskmail_request_id, "taskmail_request_id")
        self.packet_id = _require_optional_text(self.packet_id, "packet_id")
        self.source_ingress_id = _require_optional_text(self.source_ingress_id, "source_ingress_id")
        if not isinstance(self.degraded_mode, bool):
            raise ValueError("degraded_mode must be a bool")


class InMemoryPcIngressStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._mailbox_leases: dict[str, MailboxLeaseRecord] = {}
        self._lease_events: list[MailboxLeaseEvent] = []
        self._ingress_ledger: dict[str, IngressLedgerRecord] = {}
        self._binding_by_ingress_id: dict[str, CanonicalThreadBindingRecord] = {}
        self._binding_ingress_by_thread_id: dict[str, str] = {}
        self._terminal_outcomes: dict[str, TerminalOutcomeRecord] = {}
        self._uid_index: dict[str, str] = {}
        self._message_id_index: dict[str, str] = {}

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}:{secrets.token_hex(4)}"

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(_require_text(value, "timestamp"))
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone().replace(tzinfo=None)

    @staticmethod
    def _uid_index_key(*, mailbox_key: str, folder: str, uid_validity: int | None, uid: int | None) -> str | None:
        if uid is None:
            return None
        return f"{mailbox_key}|{folder}|{uid_validity or 0}|{uid}"

    @staticmethod
    def _message_id_index_key(*, mailbox_key: str, message_id: str) -> str:
        return f"{mailbox_key}|{message_id}"

    def _append_lease_event(
        self,
        *,
        mailbox_key: str,
        operation: str,
        lease_holder_id: str,
        pc_id: str,
        observed_at: str,
        lease_epoch: int | None,
        reason: str | None,
    ) -> MailboxLeaseEvent:
        event = MailboxLeaseEvent(
            event_id=self._next_id("lease_event"),
            mailbox_key=mailbox_key,
            operation=operation,
            lease_holder_id=lease_holder_id,
            pc_id=pc_id,
            observed_at=observed_at,
            lease_epoch=lease_epoch,
            reason=reason,
        )
        self._lease_events.append(event)
        while len(self._lease_events) > 256:
            self._lease_events.pop(0)
        return event

    def _current_lease_unlocked(self, mailbox_key: str, *, now: str) -> MailboxLeaseRecord | None:
        record = self._mailbox_leases.get(mailbox_key)
        if record is None:
            return None
        if record.status != "active":
            return record
        if self._parse_timestamp(record.expires_at) > self._parse_timestamp(now):
            return record
        expired = MailboxLeaseRecord(**asdict(record))
        expired.status = "released"
        self._mailbox_leases[mailbox_key] = expired
        return expired

    def acquire_mailbox_lease(
        self,
        *,
        mailbox_key: str,
        lease_holder_id: str,
        pc_id: str,
        acquired_at: str,
        lease_ttl_seconds: int,
        config_fingerprint: str | None,
        host_fingerprint: str | None,
        runtime_fingerprint: str | None,
        last_seen_thread_id: str | None = None,
        last_seen_ingress_id: str | None = None,
    ) -> tuple[str, MailboxLeaseRecord | None, str | None]:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        normalized_holder = _require_text(lease_holder_id, "lease_holder_id")
        normalized_pc_id = _require_text(pc_id, "pc_id")
        now = _require_text(acquired_at, "acquired_at")
        ttl_seconds = _require_int(lease_ttl_seconds, "lease_ttl_seconds", minimum=5)
        with self._lock:
            current = self._current_lease_unlocked(normalized_mailbox_key, now=now)
            expires_at = (self._parse_timestamp(now).timestamp() + ttl_seconds)
            expires_at_text = datetime.fromtimestamp(expires_at).replace(microsecond=0).isoformat()
            if current is not None and current.status == "active":
                if current.lease_holder_id != normalized_holder:
                    self._append_lease_event(
                        mailbox_key=normalized_mailbox_key,
                        operation="denied",
                        lease_holder_id=normalized_holder,
                        pc_id=normalized_pc_id,
                        observed_at=now,
                        lease_epoch=current.lease_epoch,
                        reason="mailbox lease is held by another runner",
                    )
                    return "denied", MailboxLeaseRecord(**asdict(current)), "mailbox lease is held by another runner"
                current.renewed_at = now
                current.expires_at = expires_at_text
                current.pc_id = normalized_pc_id
                current.config_fingerprint = _require_optional_text(config_fingerprint, "config_fingerprint")
                current.host_fingerprint = _require_optional_text(host_fingerprint, "host_fingerprint")
                current.runtime_fingerprint = _require_optional_text(runtime_fingerprint, "runtime_fingerprint")
                current.last_seen_thread_id = _require_optional_text(last_seen_thread_id, "last_seen_thread_id")
                current.last_seen_ingress_id = _require_optional_text(last_seen_ingress_id, "last_seen_ingress_id")
                self._append_lease_event(
                    mailbox_key=normalized_mailbox_key,
                    operation="renewed",
                    lease_holder_id=normalized_holder,
                    pc_id=normalized_pc_id,
                    observed_at=now,
                    lease_epoch=current.lease_epoch,
                    reason=None,
                )
                return "active", MailboxLeaseRecord(**asdict(current)), None
            next_epoch = 1 if current is None else current.lease_epoch + 1
            record = MailboxLeaseRecord(
                mailbox_key=normalized_mailbox_key,
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                lease_epoch=next_epoch,
                status="active",
                acquired_at=now,
                renewed_at=now,
                expires_at=expires_at_text,
                config_fingerprint=config_fingerprint,
                host_fingerprint=host_fingerprint,
                runtime_fingerprint=runtime_fingerprint,
                last_seen_thread_id=last_seen_thread_id,
                last_seen_ingress_id=last_seen_ingress_id,
            )
            self._mailbox_leases[normalized_mailbox_key] = record
            self._append_lease_event(
                mailbox_key=normalized_mailbox_key,
                operation="acquired",
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                observed_at=now,
                lease_epoch=record.lease_epoch,
                reason=None,
            )
            return "active", MailboxLeaseRecord(**asdict(record)), None

    def renew_mailbox_lease(
        self,
        *,
        mailbox_key: str,
        lease_holder_id: str,
        pc_id: str,
        lease_epoch: int,
        renewed_at: str,
        lease_ttl_seconds: int,
        last_seen_thread_id: str | None = None,
        last_seen_ingress_id: str | None = None,
    ) -> tuple[str, MailboxLeaseRecord | None, str | None]:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        normalized_holder = _require_text(lease_holder_id, "lease_holder_id")
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_epoch = _require_int(lease_epoch, "lease_epoch", minimum=1)
        now = _require_text(renewed_at, "renewed_at")
        ttl_seconds = _require_int(lease_ttl_seconds, "lease_ttl_seconds", minimum=5)
        with self._lock:
            current = self._current_lease_unlocked(normalized_mailbox_key, now=now)
            if current is None or current.status != "active":
                self._append_lease_event(
                    mailbox_key=normalized_mailbox_key,
                    operation="denied",
                    lease_holder_id=normalized_holder,
                    pc_id=normalized_pc_id,
                    observed_at=now,
                    lease_epoch=normalized_epoch,
                    reason="mailbox lease is not active",
                )
                return "denied", (None if current is None else MailboxLeaseRecord(**asdict(current))), "mailbox lease is not active"
            if current.lease_holder_id != normalized_holder or current.lease_epoch != normalized_epoch:
                self._append_lease_event(
                    mailbox_key=normalized_mailbox_key,
                    operation="denied",
                    lease_holder_id=normalized_holder,
                    pc_id=normalized_pc_id,
                    observed_at=now,
                    lease_epoch=normalized_epoch,
                    reason="mailbox lease holder or epoch mismatch",
                )
                return "denied", MailboxLeaseRecord(**asdict(current)), "mailbox lease holder or epoch mismatch"
            expires_at = (self._parse_timestamp(now).timestamp() + ttl_seconds)
            current.renewed_at = now
            current.expires_at = datetime.fromtimestamp(expires_at).replace(microsecond=0).isoformat()
            current.last_seen_thread_id = _require_optional_text(last_seen_thread_id, "last_seen_thread_id")
            current.last_seen_ingress_id = _require_optional_text(last_seen_ingress_id, "last_seen_ingress_id")
            current.pc_id = normalized_pc_id
            self._append_lease_event(
                mailbox_key=normalized_mailbox_key,
                operation="renewed",
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                observed_at=now,
                lease_epoch=current.lease_epoch,
                reason=None,
            )
            return "active", MailboxLeaseRecord(**asdict(current)), None

    def release_mailbox_lease(
        self,
        *,
        mailbox_key: str,
        lease_holder_id: str,
        pc_id: str,
        lease_epoch: int,
        released_at: str,
    ) -> tuple[str, MailboxLeaseRecord | None, str | None]:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        normalized_holder = _require_text(lease_holder_id, "lease_holder_id")
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_epoch = _require_int(lease_epoch, "lease_epoch", minimum=1)
        now = _require_text(released_at, "released_at")
        with self._lock:
            current = self._current_lease_unlocked(normalized_mailbox_key, now=now)
            if current is None:
                self._append_lease_event(
                    mailbox_key=normalized_mailbox_key,
                    operation="denied",
                    lease_holder_id=normalized_holder,
                    pc_id=normalized_pc_id,
                    observed_at=now,
                    lease_epoch=normalized_epoch,
                    reason="mailbox lease does not exist",
                )
                return "denied", None, "mailbox lease does not exist"
            if current.lease_holder_id != normalized_holder or current.lease_epoch != normalized_epoch:
                self._append_lease_event(
                    mailbox_key=normalized_mailbox_key,
                    operation="denied",
                    lease_holder_id=normalized_holder,
                    pc_id=normalized_pc_id,
                    observed_at=now,
                    lease_epoch=normalized_epoch,
                    reason="mailbox lease holder or epoch mismatch",
                )
                return "denied", MailboxLeaseRecord(**asdict(current)), "mailbox lease holder or epoch mismatch"
            current.status = "released"
            current.renewed_at = now
            current.expires_at = now
            self._append_lease_event(
                mailbox_key=normalized_mailbox_key,
                operation="released",
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                observed_at=now,
                lease_epoch=current.lease_epoch,
                reason=None,
            )
            return "released", MailboxLeaseRecord(**asdict(current)), None

    def get_mailbox_lease(self, mailbox_key: str, *, now: str | None = None) -> MailboxLeaseRecord | None:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        with self._lock:
            if now is None:
                current = self._mailbox_leases.get(normalized_mailbox_key)
            else:
                current = self._current_lease_unlocked(normalized_mailbox_key, now=_require_text(now, "now"))
            return None if current is None else MailboxLeaseRecord(**asdict(current))

    def list_lease_events(self, *, mailbox_key: str | None = None, limit: int = 20) -> list[MailboxLeaseEvent]:
        normalized_mailbox_key = _require_optional_text(mailbox_key, "mailbox_key")
        normalized_limit = _require_int(limit, "limit", minimum=1)
        with self._lock:
            items = list(self._lease_events)
            if normalized_mailbox_key is not None:
                items = [item for item in items if item.mailbox_key == normalized_mailbox_key]
            return [MailboxLeaseEvent(**asdict(item)) for item in items[-normalized_limit:]]

    def register_ingress_candidate(
        self,
        *,
        mailbox_key: str,
        lease_holder_id: str,
        pc_id: str,
        lease_epoch: int,
        folder: str,
        uid_validity: int | None,
        uid: int | None,
        message_id: str,
        in_reply_to: str | None,
        references_hash: str | None,
        from_addr: str,
        subject: str,
        subject_norm: str,
        raw_date: str | None,
        observed_at: str,
        classification: str,
        candidate_status: str,
        candidate_reason: str | None,
        taskmail_request_id: str | None,
        packet_id: str | None,
        degraded_mode: bool,
    ) -> IngressLedgerRecord:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        normalized_holder = _require_text(lease_holder_id, "lease_holder_id")
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_epoch = _require_int(lease_epoch, "lease_epoch", minimum=1)
        normalized_folder = _require_text(folder, "folder")
        normalized_message_id = _require_text(message_id, "message_id")
        normalized_from_addr = _require_text(from_addr, "from_addr")
        normalized_subject = _require_text(subject, "subject")
        normalized_subject_norm = _require_text(subject_norm, "subject_norm")
        normalized_observed_at = _require_text(observed_at, "observed_at")
        normalized_classification = _require_text(classification, "classification")
        normalized_candidate_status = _require_text(candidate_status, "candidate_status")
        if normalized_candidate_status not in {"ready", "stale", "invalid", "ignored"}:
            raise ValueError("candidate_status must be one of: ready, stale, invalid, ignored")
        if not isinstance(degraded_mode, bool):
            raise ValueError("degraded_mode must be a bool")
        normalized_uid_validity = _require_optional_int(uid_validity, "uid_validity", minimum=1)
        normalized_uid = _require_optional_int(uid, "uid", minimum=1)
        dedupe_key_uid = self._uid_index_key(
            mailbox_key=normalized_mailbox_key,
            folder=normalized_folder,
            uid_validity=normalized_uid_validity,
            uid=normalized_uid,
        )
        dedupe_key_message_id = self._message_id_index_key(
            mailbox_key=normalized_mailbox_key,
            message_id=normalized_message_id,
        )
        with self._lock:
            current = self._current_lease_unlocked(normalized_mailbox_key, now=normalized_observed_at)
            if (
                current is None
                or current.status != "active"
                or current.lease_holder_id != normalized_holder
                or current.lease_epoch != normalized_epoch
            ):
                record = IngressLedgerRecord(
                    ingress_id=self._next_id("ingress"),
                    mailbox_key=normalized_mailbox_key,
                    folder=normalized_folder,
                    uid_validity=normalized_uid_validity,
                    uid=normalized_uid,
                    message_id=normalized_message_id,
                    in_reply_to=in_reply_to,
                    references_hash=references_hash,
                    from_addr=normalized_from_addr,
                    subject=normalized_subject,
                    subject_norm=normalized_subject_norm,
                    raw_date=raw_date,
                    observed_at=normalized_observed_at,
                    classification=normalized_classification,
                    decision="lease_denied",
                    lease_holder_id=normalized_holder,
                    pc_id=normalized_pc_id,
                    lease_epoch=normalized_epoch,
                    dedupe_key_uid=dedupe_key_uid,
                    dedupe_key_message_id=dedupe_key_message_id,
                    decision_reason="mailbox lease holder or epoch mismatch",
                    taskmail_request_id=taskmail_request_id,
                    packet_id=packet_id,
                    degraded_mode=degraded_mode,
                )
                self._ingress_ledger[record.ingress_id] = record
                return IngressLedgerRecord(**asdict(record))
            existing_ingress_id = None
            if dedupe_key_uid is not None:
                existing_ingress_id = self._uid_index.get(dedupe_key_uid)
            if existing_ingress_id is None:
                existing_ingress_id = self._message_id_index.get(dedupe_key_message_id)
            if existing_ingress_id is not None:
                existing = self._ingress_ledger[existing_ingress_id]
                return IngressLedgerRecord(**asdict(existing))
            if normalized_candidate_status == "stale":
                decision = "stale"
            elif normalized_candidate_status == "invalid":
                decision = "invalid"
            elif normalized_candidate_status == "ignored":
                decision = "ignored"
            else:
                decision = "accepted"
            record = IngressLedgerRecord(
                ingress_id=self._next_id("ingress"),
                mailbox_key=normalized_mailbox_key,
                folder=normalized_folder,
                uid_validity=normalized_uid_validity,
                uid=normalized_uid,
                message_id=normalized_message_id,
                in_reply_to=in_reply_to,
                references_hash=references_hash,
                from_addr=normalized_from_addr,
                subject=normalized_subject,
                subject_norm=normalized_subject_norm,
                raw_date=raw_date,
                observed_at=normalized_observed_at,
                classification=normalized_classification,
                decision=decision,
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                lease_epoch=normalized_epoch,
                dedupe_key_uid=dedupe_key_uid,
                dedupe_key_message_id=dedupe_key_message_id,
                decision_reason=_require_optional_text(candidate_reason, "candidate_reason"),
                taskmail_request_id=taskmail_request_id,
                packet_id=packet_id,
                accepted_at=(normalized_observed_at if decision == "accepted" else None),
                degraded_mode=degraded_mode,
            )
            self._ingress_ledger[record.ingress_id] = record
            if dedupe_key_uid is not None:
                self._uid_index[dedupe_key_uid] = record.ingress_id
            self._message_id_index[dedupe_key_message_id] = record.ingress_id
            return IngressLedgerRecord(**asdict(record))

    def commit_thread_binding(
        self,
        *,
        mailbox_key: str,
        lease_holder_id: str,
        pc_id: str,
        lease_epoch: int,
        ingress_id: str,
        root_message_id: str,
        thread_id: str,
        session_id: str,
        repo_path: str,
        workdir: str | None,
        subject_norm: str,
        binding_created_at: str,
        degraded_mode: bool,
    ) -> tuple[str, CanonicalThreadBindingRecord | None, str | None]:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        normalized_holder = _require_text(lease_holder_id, "lease_holder_id")
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_epoch = _require_int(lease_epoch, "lease_epoch", minimum=1)
        normalized_ingress_id = _require_text(ingress_id, "ingress_id")
        normalized_thread_id = _require_text(thread_id, "thread_id")
        normalized_session_id = _require_text(session_id, "session_id")
        normalized_created_at = _require_text(binding_created_at, "binding_created_at")
        if not isinstance(degraded_mode, bool):
            raise ValueError("degraded_mode must be a bool")
        with self._lock:
            current = self._current_lease_unlocked(normalized_mailbox_key, now=normalized_created_at)
            if (
                current is None
                or current.status != "active"
                or current.lease_holder_id != normalized_holder
                or current.lease_epoch != normalized_epoch
            ):
                return "denied", None, "mailbox lease holder or epoch mismatch"
            ingress = self._ingress_ledger.get(normalized_ingress_id)
            if ingress is None:
                return "denied", None, "ingress_id not found"
            existing = self._binding_by_ingress_id.get(normalized_ingress_id)
            if existing is not None:
                return "duplicate", CanonicalThreadBindingRecord(**asdict(existing)), None
            existing_ingress_id = self._binding_ingress_by_thread_id.get(normalized_thread_id)
            if existing_ingress_id is not None and existing_ingress_id != normalized_ingress_id:
                return "denied", None, "thread_id is already bound to another ingress"
            record = CanonicalThreadBindingRecord(
                ingress_id=normalized_ingress_id,
                mailbox_key=normalized_mailbox_key,
                root_message_id=root_message_id,
                thread_id=normalized_thread_id,
                session_id=normalized_session_id,
                repo_path=repo_path,
                workdir=workdir,
                subject_norm=subject_norm,
                binding_created_at=normalized_created_at,
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                lease_epoch=normalized_epoch,
                degraded_mode=degraded_mode,
            )
            self._binding_by_ingress_id[normalized_ingress_id] = record
            self._binding_ingress_by_thread_id[normalized_thread_id] = normalized_ingress_id
            ingress.thread_id = normalized_thread_id
            ingress.session_id = normalized_session_id
            ingress.closed_at = normalized_created_at
            return "committed", CanonicalThreadBindingRecord(**asdict(record)), None

    def commit_terminal_outcome(
        self,
        *,
        mailbox_key: str,
        lease_holder_id: str,
        pc_id: str,
        lease_epoch: int,
        thread_id: str,
        task_id: str,
        run_status: str,
        generated_at: str,
        last_summary: str | None,
        terminal_mail_message_id: str | None,
        terminal_mail_subject: str | None,
        taskmail_request_id: str | None,
        packet_id: str | None,
        source_ingress_id: str | None,
        degraded_mode: bool,
    ) -> tuple[str, TerminalOutcomeRecord | None, str | None]:
        normalized_mailbox_key = _require_text(mailbox_key, "mailbox_key")
        normalized_holder = _require_text(lease_holder_id, "lease_holder_id")
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_epoch = _require_int(lease_epoch, "lease_epoch", minimum=1)
        normalized_thread_id = _require_text(thread_id, "thread_id")
        normalized_task_id = _require_text(task_id, "task_id")
        normalized_generated_at = _require_text(generated_at, "generated_at")
        if not isinstance(degraded_mode, bool):
            raise ValueError("degraded_mode must be a bool")
        with self._lock:
            current = self._current_lease_unlocked(normalized_mailbox_key, now=normalized_generated_at)
            if (
                current is None
                or current.status != "active"
                or current.lease_holder_id != normalized_holder
                or current.lease_epoch != normalized_epoch
            ):
                return "denied", None, "mailbox lease holder or epoch mismatch"
            binding_ingress_id = self._binding_ingress_by_thread_id.get(normalized_thread_id)
            effective_source_ingress_id = _require_optional_text(source_ingress_id, "source_ingress_id") or binding_ingress_id
            record = TerminalOutcomeRecord(
                thread_id=normalized_thread_id,
                task_id=normalized_task_id,
                run_status=run_status,
                generated_at=normalized_generated_at,
                last_summary=last_summary,
                terminal_mail_message_id=terminal_mail_message_id,
                terminal_mail_subject=terminal_mail_subject,
                taskmail_request_id=taskmail_request_id,
                packet_id=packet_id,
                source_ingress_id=effective_source_ingress_id,
                lease_holder_id=normalized_holder,
                pc_id=normalized_pc_id,
                lease_epoch=normalized_epoch,
                degraded_mode=degraded_mode,
            )
            self._terminal_outcomes[f"{normalized_thread_id}|{normalized_task_id}"] = record
            return "committed", TerminalOutcomeRecord(**asdict(record)), None

    def find_ingress(
        self,
        *,
        ingress_id: str | None = None,
        mailbox_key: str | None = None,
        message_id: str | None = None,
        uid: int | None = None,
        folder: str = "INBOX",
        uid_validity: int | None = None,
    ) -> IngressLedgerRecord | None:
        normalized_ingress_id = _require_optional_text(ingress_id, "ingress_id")
        normalized_mailbox_key = _require_optional_text(mailbox_key, "mailbox_key")
        normalized_message_id = _require_optional_text(message_id, "message_id")
        normalized_uid = _require_optional_int(uid, "uid", minimum=1)
        normalized_uid_validity = _require_optional_int(uid_validity, "uid_validity", minimum=1)
        normalized_folder = _require_text(folder, "folder")
        with self._lock:
            record: IngressLedgerRecord | None = None
            if normalized_ingress_id is not None:
                record = self._ingress_ledger.get(normalized_ingress_id)
            elif normalized_mailbox_key is not None and normalized_message_id is not None:
                ingress_key = self._message_id_index.get(
                    self._message_id_index_key(mailbox_key=normalized_mailbox_key, message_id=normalized_message_id)
                )
                record = None if ingress_key is None else self._ingress_ledger.get(ingress_key)
            elif normalized_mailbox_key is not None and normalized_uid is not None:
                uid_key = self._uid_index_key(
                    mailbox_key=normalized_mailbox_key,
                    folder=normalized_folder,
                    uid_validity=normalized_uid_validity,
                    uid=normalized_uid,
                )
                ingress_key = None if uid_key is None else self._uid_index.get(uid_key)
                record = None if ingress_key is None else self._ingress_ledger.get(ingress_key)
            return None if record is None else IngressLedgerRecord(**asdict(record))

    def list_recent_ingress(
        self,
        *,
        mailbox_key: str | None = None,
        limit: int = 20,
    ) -> list[IngressLedgerRecord]:
        normalized_mailbox_key = _require_optional_text(mailbox_key, "mailbox_key")
        normalized_limit = _require_int(limit, "limit", minimum=1)
        with self._lock:
            items = sorted(self._ingress_ledger.values(), key=lambda item: item.observed_at)
            if normalized_mailbox_key is not None:
                items = [item for item in items if item.mailbox_key == normalized_mailbox_key]
            return [IngressLedgerRecord(**asdict(item)) for item in items[-normalized_limit:]]

    def list_thread_bindings(self, *, pc_id: str | None = None) -> list[CanonicalThreadBindingRecord]:
        normalized_pc_id = _require_optional_text(pc_id, "pc_id")
        with self._lock:
            items = sorted(self._binding_by_ingress_id.values(), key=lambda item: item.binding_created_at)
            if normalized_pc_id is not None:
                items = [item for item in items if item.pc_id == normalized_pc_id]
            return [CanonicalThreadBindingRecord(**asdict(item)) for item in items]

    def find_terminal_outcome(self, *, thread_id: str) -> TerminalOutcomeRecord | None:
        normalized_thread_id = _require_text(thread_id, "thread_id")
        with self._lock:
            matches = [item for item in self._terminal_outcomes.values() if item.thread_id == normalized_thread_id]
            if not matches:
                return None
            latest = max(matches, key=lambda item: item.generated_at)
            return TerminalOutcomeRecord(**asdict(latest))

    def count_leases(self) -> int:
        with self._lock:
            return len(self._mailbox_leases)

    def count_ingress(self) -> int:
        with self._lock:
            return len(self._ingress_ledger)

    def count_bindings(self) -> int:
        with self._lock:
            return len(self._binding_by_ingress_id)

    def count_terminal_outcomes(self) -> int:
        with self._lock:
            return len(self._terminal_outcomes)


class PersistentPcIngressStore(InMemoryPcIngressStore):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._load()

    def _load(self) -> None:
        payload = _load_json(
            self._path,
            default={
                "version": 1,
                "mailbox_leases": [],
                "lease_events": [],
                "ingress_ledger": [],
                "thread_bindings": [],
                "terminal_outcomes": [],
            },
        )
        self._mailbox_leases = {}
        for item in payload.get("mailbox_leases", []) if isinstance(payload, dict) else []:
            record = MailboxLeaseRecord(**item)
            self._mailbox_leases[record.mailbox_key] = record
        self._lease_events = [MailboxLeaseEvent(**item) for item in payload.get("lease_events", [])]
        self._ingress_ledger = {}
        self._uid_index = {}
        self._message_id_index = {}
        for item in payload.get("ingress_ledger", []) if isinstance(payload, dict) else []:
            record = IngressLedgerRecord(**item)
            self._ingress_ledger[record.ingress_id] = record
            if record.dedupe_key_uid is not None:
                self._uid_index[record.dedupe_key_uid] = record.ingress_id
            if record.dedupe_key_message_id is not None:
                self._message_id_index[record.dedupe_key_message_id] = record.ingress_id
        self._binding_by_ingress_id = {}
        self._binding_ingress_by_thread_id = {}
        for item in payload.get("thread_bindings", []) if isinstance(payload, dict) else []:
            record = CanonicalThreadBindingRecord(**item)
            self._binding_by_ingress_id[record.ingress_id] = record
            self._binding_ingress_by_thread_id[record.thread_id] = record.ingress_id
        self._terminal_outcomes = {}
        for item in payload.get("terminal_outcomes", []) if isinstance(payload, dict) else []:
            record = TerminalOutcomeRecord(**item)
            self._terminal_outcomes[f"{record.thread_id}|{record.task_id}"] = record

    def _save(self) -> None:
        payload = {
            "version": 1,
            "mailbox_leases": [asdict(item) for item in sorted(self._mailbox_leases.values(), key=lambda item: item.mailbox_key)],
            "lease_events": [asdict(item) for item in self._lease_events],
            "ingress_ledger": [asdict(item) for item in sorted(self._ingress_ledger.values(), key=lambda item: item.observed_at)],
            "thread_bindings": [asdict(item) for item in sorted(self._binding_by_ingress_id.values(), key=lambda item: item.binding_created_at)],
            "terminal_outcomes": [asdict(item) for item in sorted(self._terminal_outcomes.values(), key=lambda item: item.generated_at)],
        }
        _write_json(self._path, payload)

    def acquire_mailbox_lease(self, **kwargs):
        result = super().acquire_mailbox_lease(**kwargs)
        self._save()
        return result

    def renew_mailbox_lease(self, **kwargs):
        result = super().renew_mailbox_lease(**kwargs)
        self._save()
        return result

    def release_mailbox_lease(self, **kwargs):
        result = super().release_mailbox_lease(**kwargs)
        self._save()
        return result

    def register_ingress_candidate(self, **kwargs):
        result = super().register_ingress_candidate(**kwargs)
        self._save()
        return result

    def commit_thread_binding(self, **kwargs):
        result = super().commit_thread_binding(**kwargs)
        self._save()
        return result

    def commit_terminal_outcome(self, **kwargs):
        result = super().commit_terminal_outcome(**kwargs)
        self._save()
        return result
