"""SQLite relay-side projection store for Android-facing read models."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_int(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name, minimum=minimum)


def _require_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a bool")


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict")
    return dict(value)


def _optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _require_mapping(value, field_name)


def _require_sequence_of_mappings(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a sequence of dicts")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{index}] must be a dict")
        normalized.append(dict(item))
    return normalized


def _normalize_json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_value(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(_normalize_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _json_dumps(value: Any) -> str:
    return _canonical_json(value)


def _max_timestamp_text(first: str | None, second: str | None) -> str | None:
    normalized_first = _optional_text(first, "first")
    normalized_second = _optional_text(second, "second")
    if normalized_first is None:
        return normalized_second
    if normalized_second is None:
        return normalized_first
    return normalized_first if normalized_first >= normalized_second else normalized_second


def _session_key(*, pc_id: str, workspace_id: str, session_id: str, thread_id: str) -> str:
    return "::".join(
        [
            _require_text(pc_id, "pc_id"),
            _require_text(workspace_id, "workspace_id"),
            _require_text(session_id, "session_id"),
            _require_text(thread_id, "thread_id"),
        ]
    )


def _round_key(session_key: str, round_id: str) -> str:
    return f"{_require_text(session_key, 'session_key')}::{_require_text(round_id, 'round_id')}"


def _round_attachment_key(round_key: str, attachment_role: str, ordinal: int) -> str:
    return f"{_require_text(round_key, 'round_key')}::{_require_text(attachment_role, 'attachment_role')}::{_require_int(ordinal, 'ordinal', minimum=1)}"


class ProjectionStoreConflictError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class ProjectionAttachmentUpsert:
    attachment_id: str
    display_name: str
    content_type: str
    size_bytes: int | None = None
    is_image: bool = False
    artifact_id: str | None = None
    ordinal: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachment_id", _require_text(self.attachment_id, "attachment_id"))
        object.__setattr__(self, "display_name", _require_text(self.display_name, "display_name"))
        object.__setattr__(self, "content_type", _require_text(self.content_type, "content_type"))
        object.__setattr__(self, "size_bytes", _optional_int(self.size_bytes, "size_bytes", minimum=0))
        object.__setattr__(self, "is_image", _require_bool(self.is_image, "is_image"))
        object.__setattr__(self, "artifact_id", _optional_text(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "ordinal", _optional_int(self.ordinal, "ordinal", minimum=1))

    def as_payload(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "display_name": self.display_name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "is_image": self.is_image,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True, slots=True)
class ProjectionSessionUpsert:
    idempotency_key: str
    projection_version: int
    pc_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    session_name: str
    backend: str
    backend_transport: str | None
    profile: str
    permission: str
    repo_path: str
    workdir: str | None
    list_status: str
    snapshot_status: str
    lifecycle: str
    current_task_id: str | None
    queued_task_id: str | None
    pending_task_count: int
    last_summary: str | None
    last_active_at: str | None
    last_progress_at: str | None
    paused_from_status: str | None
    backend_session_id: str | None
    backend_session_resumable: bool
    question_state: dict[str, Any] | None = None
    timeline_items: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    source_updated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", _require_text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "projection_version", _require_int(self.projection_version, "projection_version", minimum=1))
        object.__setattr__(self, "pc_id", _require_text(self.pc_id, "pc_id"))
        object.__setattr__(self, "workspace_id", _require_text(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "session_id", _require_text(self.session_id, "session_id"))
        object.__setattr__(self, "thread_id", _require_text(self.thread_id, "thread_id"))
        object.__setattr__(self, "session_name", _require_text(self.session_name, "session_name"))
        object.__setattr__(self, "backend", _require_text(self.backend, "backend"))
        object.__setattr__(self, "backend_transport", _optional_text(self.backend_transport, "backend_transport"))
        object.__setattr__(self, "profile", _require_text(self.profile, "profile"))
        object.__setattr__(self, "permission", _require_text(self.permission, "permission"))
        object.__setattr__(self, "repo_path", _require_text(self.repo_path, "repo_path"))
        object.__setattr__(self, "workdir", _optional_text(self.workdir, "workdir"))
        object.__setattr__(self, "list_status", _require_text(self.list_status, "list_status"))
        object.__setattr__(self, "snapshot_status", _require_text(self.snapshot_status, "snapshot_status"))
        object.__setattr__(self, "lifecycle", _require_text(self.lifecycle, "lifecycle"))
        object.__setattr__(self, "current_task_id", _optional_text(self.current_task_id, "current_task_id"))
        object.__setattr__(self, "queued_task_id", _optional_text(self.queued_task_id, "queued_task_id"))
        object.__setattr__(self, "pending_task_count", _require_int(self.pending_task_count, "pending_task_count", minimum=0))
        object.__setattr__(self, "last_summary", _optional_text(self.last_summary, "last_summary"))
        object.__setattr__(self, "last_active_at", _optional_text(self.last_active_at, "last_active_at"))
        object.__setattr__(self, "last_progress_at", _optional_text(self.last_progress_at, "last_progress_at"))
        object.__setattr__(self, "paused_from_status", _optional_text(self.paused_from_status, "paused_from_status"))
        object.__setattr__(self, "backend_session_id", _optional_text(self.backend_session_id, "backend_session_id"))
        object.__setattr__(self, "backend_session_resumable", _require_bool(self.backend_session_resumable, "backend_session_resumable"))
        object.__setattr__(self, "question_state", _optional_mapping(self.question_state, "question_state"))
        object.__setattr__(self, "timeline_items", _require_sequence_of_mappings(self.timeline_items, "timeline_items"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _require_text(self.updated_at, "updated_at"))
        object.__setattr__(self, "source_updated_at", _require_text(self.source_updated_at, "source_updated_at"))

    @property
    def session_key(self) -> str:
        return _session_key(
            pc_id=self.pc_id,
            workspace_id=self.workspace_id,
            session_id=self.session_id,
            thread_id=self.thread_id,
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "pc_id": self.pc_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "session_name": self.session_name,
            "backend": self.backend,
            "backend_transport": self.backend_transport,
            "profile": self.profile,
            "permission": self.permission,
            "repo_path": self.repo_path,
            "workdir": self.workdir,
            "list_status": self.list_status,
            "snapshot_status": self.snapshot_status,
            "lifecycle": self.lifecycle,
            "current_task_id": self.current_task_id,
            "queued_task_id": self.queued_task_id,
            "pending_task_count": self.pending_task_count,
            "last_summary": self.last_summary,
            "last_active_at": self.last_active_at,
            "last_progress_at": self.last_progress_at,
            "paused_from_status": self.paused_from_status,
            "backend_session_id": self.backend_session_id,
            "backend_session_resumable": self.backend_session_resumable,
            "question_state": self.question_state,
            "timeline_items": list(self.timeline_items),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def as_snapshot_payload(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "backend": self.backend,
            "repo_path": self.repo_path,
            "workdir": self.workdir,
            "status": self.snapshot_status,
            "lifecycle": self.lifecycle,
            "last_summary": self.last_summary or self.snapshot_status.replace("_", " ").title() + ".",
            "last_active_at": self.last_active_at,
            "last_progress_at": self.last_progress_at,
            "paused_from_status": self.paused_from_status,
            "question_state": self.question_state,
            "timeline_items": list(self.timeline_items),
        }


@dataclass(frozen=True, slots=True)
class ProjectionRoundUpsert:
    idempotency_key: str
    round_id: str
    task_id: str
    round_sort_at: str
    created_at: str
    status: str
    speaker_label: str
    input_text: str | None = None
    process_items: list[dict[str, Any]] = field(default_factory=list)
    result_text: str | None = None
    input_attachments: list[ProjectionAttachmentUpsert] = field(default_factory=list)
    result_attachments: list[ProjectionAttachmentUpsert] = field(default_factory=list)
    source_updated_at: str = ""
    projection_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", _require_text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "round_id", _require_text(self.round_id, "round_id"))
        object.__setattr__(self, "task_id", _require_text(self.task_id, "task_id"))
        object.__setattr__(self, "round_sort_at", _require_text(self.round_sort_at, "round_sort_at"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "status", _require_text(self.status, "status"))
        object.__setattr__(self, "speaker_label", _require_text(self.speaker_label, "speaker_label"))
        object.__setattr__(self, "input_text", _optional_text(self.input_text, "input_text"))
        object.__setattr__(self, "process_items", _require_sequence_of_mappings(self.process_items, "process_items"))
        object.__setattr__(self, "result_text", _optional_text(self.result_text, "result_text"))
        object.__setattr__(self, "input_attachments", self._normalize_attachments(self.input_attachments, "input_attachments"))
        object.__setattr__(self, "result_attachments", self._normalize_attachments(self.result_attachments, "result_attachments"))
        object.__setattr__(self, "source_updated_at", _require_text(self.source_updated_at, "source_updated_at"))
        if self.projection_version is not None:
            object.__setattr__(self, "projection_version", _require_int(self.projection_version, "projection_version", minimum=1))

    @staticmethod
    def _normalize_attachments(
        value: Any,
        field_name: str,
    ) -> list[ProjectionAttachmentUpsert]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"{field_name} must be a sequence of ProjectionAttachmentUpsert-compatible entries")
        normalized: list[ProjectionAttachmentUpsert] = []
        for index, item in enumerate(value):
            if isinstance(item, ProjectionAttachmentUpsert):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(ProjectionAttachmentUpsert(**item))
            else:
                raise ValueError(f"{field_name}[{index}] must be a ProjectionAttachmentUpsert-compatible entry")
        return normalized

    def as_payload(self) -> dict[str, Any]:
        def _canonicalize_attachments(
            attachments: Sequence[ProjectionAttachmentUpsert],
        ) -> list[dict[str, Any]]:
            indexed = [
                (
                    attachment.ordinal if attachment.ordinal is not None else index,
                    index,
                    attachment.as_payload(),
                )
                for index, attachment in enumerate(attachments, start=1)
            ]
            indexed.sort(key=lambda item: (item[0], item[1]))
            return [payload for _, _, payload in indexed]

        return {
            "round_id": self.round_id,
            "task_id": self.task_id,
            "round_sort_at": self.round_sort_at,
            "created_at": self.created_at,
            "status": self.status,
            "speaker_label": self.speaker_label,
            "input_text": self.input_text,
            "process_items": list(self.process_items),
            "result_text": self.result_text,
            "input_attachments": _canonicalize_attachments(self.input_attachments),
            "result_attachments": _canonicalize_attachments(self.result_attachments),
            "source_updated_at": self.source_updated_at,
        }


@dataclass(frozen=True, slots=True)
class ProjectionCloseoutUpsert:
    idempotency_key: str
    closeout_key: str
    task_id: str | None
    request_id: str | None
    packet_id: str | None
    receipt_id: str | None
    action_type: str | None
    target_session_identity: dict[str, Any] | None
    last_summary: str | None
    terminal_mail_message_id: str | None
    terminal_mail_subject: str | None
    generated_at: str
    source_updated_at: str
    projection_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", _require_text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "closeout_key", _require_text(self.closeout_key, "closeout_key"))
        object.__setattr__(self, "task_id", _optional_text(self.task_id, "task_id"))
        object.__setattr__(self, "request_id", _optional_text(self.request_id, "request_id"))
        object.__setattr__(self, "packet_id", _optional_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "receipt_id", _optional_text(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "action_type", _optional_text(self.action_type, "action_type"))
        object.__setattr__(self, "target_session_identity", _optional_mapping(self.target_session_identity, "target_session_identity"))
        object.__setattr__(self, "last_summary", _optional_text(self.last_summary, "last_summary"))
        object.__setattr__(self, "terminal_mail_message_id", _optional_text(self.terminal_mail_message_id, "terminal_mail_message_id"))
        object.__setattr__(self, "terminal_mail_subject", _optional_text(self.terminal_mail_subject, "terminal_mail_subject"))
        object.__setattr__(self, "generated_at", _require_text(self.generated_at, "generated_at"))
        object.__setattr__(self, "source_updated_at", _require_text(self.source_updated_at, "source_updated_at"))
        if self.projection_version is not None:
            object.__setattr__(self, "projection_version", _require_int(self.projection_version, "projection_version", minimum=1))

    def as_payload(self) -> dict[str, Any]:
        return {
            "closeout_key": self.closeout_key,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "packet_id": self.packet_id,
            "receipt_id": self.receipt_id,
            "action_type": self.action_type,
            "target_session_identity": self.target_session_identity,
            "last_summary": self.last_summary,
            "terminal_mail_message_id": self.terminal_mail_message_id,
            "terminal_mail_subject": self.terminal_mail_subject,
            "generated_at": self.generated_at,
            "source_updated_at": self.source_updated_at,
        }


@dataclass(frozen=True, slots=True)
class ProjectionProbeObservationUpsert:
    idempotency_key: str
    probe_id: str
    summary_text: str
    observation_status: str
    observed_at: str
    payload: dict[str, Any]
    pc_id: str | None = None
    request_id: str | None = None
    packet_id: str | None = None
    receipt_id: str | None = None
    mailbox_message_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", _require_text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "probe_id", _require_text(self.probe_id, "probe_id"))
        object.__setattr__(self, "summary_text", _require_text(self.summary_text, "summary_text"))
        object.__setattr__(self, "observation_status", _require_text(self.observation_status, "observation_status"))
        object.__setattr__(self, "observed_at", _require_text(self.observed_at, "observed_at"))
        object.__setattr__(self, "payload", _require_mapping(self.payload, "payload"))
        object.__setattr__(self, "pc_id", _optional_text(self.pc_id, "pc_id"))
        object.__setattr__(self, "request_id", _optional_text(self.request_id, "request_id"))
        object.__setattr__(self, "packet_id", _optional_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "receipt_id", _optional_text(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "mailbox_message_id", _optional_text(self.mailbox_message_id, "mailbox_message_id"))

    def as_payload(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "pc_id": self.pc_id,
            "request_id": self.request_id,
            "packet_id": self.packet_id,
            "receipt_id": self.receipt_id,
            "mailbox_message_id": self.mailbox_message_id,
            "summary_text": self.summary_text,
            "observation_status": self.observation_status,
            "observed_at": self.observed_at,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class ProjectionSessionBatch:
    batch_id: str
    connection_epoch: int
    sent_at: str
    session: ProjectionSessionUpsert
    rounds: Sequence[ProjectionRoundUpsert] = field(default_factory=tuple)
    closeouts: Sequence[ProjectionCloseoutUpsert] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "batch_id", _require_text(self.batch_id, "batch_id"))
        object.__setattr__(self, "connection_epoch", _require_int(self.connection_epoch, "connection_epoch", minimum=1))
        object.__setattr__(self, "sent_at", _require_text(self.sent_at, "sent_at"))
        if not isinstance(self.session, ProjectionSessionUpsert):
            if isinstance(self.session, dict):
                object.__setattr__(self, "session", ProjectionSessionUpsert(**self.session))
            else:
                raise ValueError("session must be a ProjectionSessionUpsert")
        rounds = []
        for index, item in enumerate(self.rounds):
            if isinstance(item, ProjectionRoundUpsert):
                rounds.append(item)
            elif isinstance(item, dict):
                rounds.append(ProjectionRoundUpsert(**item))
            else:
                raise ValueError(f"rounds[{index}] must be a ProjectionRoundUpsert-compatible entry")
        object.__setattr__(self, "rounds", tuple(rounds))
        closeouts = []
        for index, item in enumerate(self.closeouts):
            if isinstance(item, ProjectionCloseoutUpsert):
                closeouts.append(item)
            elif isinstance(item, dict):
                closeouts.append(ProjectionCloseoutUpsert(**item))
            else:
                raise ValueError(f"closeouts[{index}] must be a ProjectionCloseoutUpsert-compatible entry")
        object.__setattr__(self, "closeouts", tuple(closeouts))

    @property
    def pc_id(self) -> str:
        return self.session.pc_id

    @property
    def workspace_id(self) -> str:
        return self.session.workspace_id

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def thread_id(self) -> str:
        return self.session.thread_id


class SqliteRelayProjectionStore(AbstractContextManager["SqliteRelayProjectionStore"]):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._conn = sqlite3.connect(
            ":memory:" if str(self._path) == ":memory:" else str(self._path),
            timeout=30,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def __enter__(self) -> "SqliteRelayProjectionStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS projection_ingest_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_family TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                pc_id TEXT NOT NULL,
                workspace_id TEXT,
                session_id TEXT,
                thread_id TEXT,
                projection_version INTEGER,
                payload_sha256 TEXT NOT NULL,
                connection_epoch INTEGER NOT NULL,
                source_sent_at TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                UNIQUE(message_family, idempotency_key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projection_sessions (
                session_key TEXT PRIMARY KEY,
                pc_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                session_name TEXT NOT NULL,
                backend TEXT NOT NULL,
                backend_transport TEXT,
                profile TEXT NOT NULL,
                permission TEXT NOT NULL,
                repo_path TEXT NOT NULL,
                workdir TEXT,
                list_status TEXT NOT NULL,
                snapshot_status TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                current_task_id TEXT,
                queued_task_id TEXT,
                pending_task_count INTEGER NOT NULL,
                last_summary TEXT,
                last_active_at TEXT,
                last_progress_at TEXT,
                paused_from_status TEXT,
                backend_session_id TEXT,
                backend_session_resumable INTEGER NOT NULL,
                question_state_json TEXT,
                timeline_items_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                projection_version INTEGER NOT NULL,
                source_updated_at TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                UNIQUE(pc_id, workspace_id, session_id, thread_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projection_history_rounds (
                round_key TEXT PRIMARY KEY,
                session_key TEXT NOT NULL REFERENCES projection_sessions(session_key) ON DELETE CASCADE,
                idempotency_key TEXT NOT NULL UNIQUE,
                pc_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                round_sort_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                speaker_label TEXT NOT NULL,
                input_text TEXT,
                process_items_json TEXT NOT NULL,
                result_text TEXT,
                projection_version INTEGER NOT NULL,
                source_updated_at TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                UNIQUE(pc_id, session_id, round_id),
                UNIQUE(pc_id, session_id, task_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projection_round_attachments (
                round_attachment_key TEXT PRIMARY KEY,
                round_key TEXT NOT NULL REFERENCES projection_history_rounds(round_key) ON DELETE CASCADE,
                attachment_role TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                attachment_id TEXT NOT NULL,
                artifact_id TEXT,
                display_name TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER,
                is_image INTEGER NOT NULL,
                UNIQUE(round_key, attachment_role, ordinal)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projection_closeouts (
                closeout_key TEXT PRIMARY KEY,
                session_key TEXT NOT NULL REFERENCES projection_sessions(session_key) ON DELETE CASCADE,
                idempotency_key TEXT NOT NULL UNIQUE,
                pc_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                task_id TEXT,
                request_id TEXT,
                packet_id TEXT,
                receipt_id TEXT,
                action_type TEXT,
                target_session_identity_json TEXT,
                last_summary TEXT,
                terminal_mail_message_id TEXT,
                terminal_mail_subject TEXT,
                generated_at TEXT NOT NULL,
                projection_version INTEGER NOT NULL,
                source_updated_at TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projection_session_live_process (
                session_key TEXT PRIMARY KEY REFERENCES projection_sessions(session_key) ON DELETE CASCADE,
                pc_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                task_id TEXT,
                last_seq INTEGER NOT NULL,
                status TEXT NOT NULL,
                items_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projection_probe_observations (
                probe_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                pc_id TEXT,
                request_id TEXT,
                packet_id TEXT,
                receipt_id TEXT,
                mailbox_message_id TEXT,
                summary_text TEXT NOT NULL,
                observation_status TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """,
        ]
        with self._lock, self._conn:
            for statement in statements:
                self._conn.execute(statement)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_sessions_lookup ON projection_sessions(pc_id, workspace_id, session_id, thread_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_sessions_list ON projection_sessions(lifecycle, last_progress_at DESC, updated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_history_rounds_order ON projection_history_rounds(session_key, round_sort_at, task_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_round_attachments_order ON projection_round_attachments(round_key, attachment_role, ordinal)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_closeouts_order ON projection_closeouts(session_key, generated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_session_live_process_lookup ON projection_session_live_process(pc_id, workspace_id, session_id, thread_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projection_probe_observations_order ON projection_probe_observations(observed_at DESC)"
            )

    def _fetch_one(self, query: str, params: Sequence[Any]) -> sqlite3.Row | None:
        cursor = self._conn.execute(query, params)
        return cursor.fetchone()

    def _fetch_all(self, query: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        cursor = self._conn.execute(query, params)
        return list(cursor.fetchall())

    def _record_receipt(
        self,
        *,
        message_family: str,
        idempotency_key: str,
        batch_id: str,
        pc_id: str,
        workspace_id: str | None,
        session_id: str | None,
        thread_id: str | None,
        projection_version: int | None,
        payload: Any,
        connection_epoch: int,
        source_sent_at: str,
    ) -> bool:
        payload_sha = _sha256_json(payload)
        existing = self._fetch_one(
            """
            SELECT payload_sha256
            FROM projection_ingest_receipts
            WHERE message_family = ? AND idempotency_key = ?
            """,
            (message_family, idempotency_key),
        )
        if existing is not None:
            if existing["payload_sha256"] != payload_sha:
                raise ProjectionStoreConflictError(
                    "receipt_conflict",
                    f"{message_family} receipt already exists with different payload: {idempotency_key}",
                )
            return False
        self._conn.execute(
            """
            INSERT INTO projection_ingest_receipts (
                message_family, idempotency_key, batch_id, pc_id, workspace_id, session_id, thread_id,
                projection_version, payload_sha256, connection_epoch, source_sent_at, applied_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_family,
                idempotency_key,
                batch_id,
                pc_id,
                workspace_id,
                session_id,
                thread_id,
                projection_version,
                payload_sha,
                connection_epoch,
                source_sent_at,
                source_sent_at,
            ),
        )
        return True

    def apply_session_batch(self, batch: ProjectionSessionBatch) -> bool:
        batch = self._normalize_batch(batch)
        changed = False
        with self._lock, self._conn:
            self._record_receipt(
                message_family="session_projection_batch",
                idempotency_key=batch.batch_id,
                batch_id=batch.batch_id,
                pc_id=batch.pc_id,
                workspace_id=batch.workspace_id,
                session_id=batch.session_id,
                thread_id=batch.thread_id,
                projection_version=batch.session.projection_version,
                payload={
                    "batch_id": batch.batch_id,
                    "session": batch.session.as_payload(),
                    "rounds": [round_item.as_payload() for round_item in batch.rounds],
                    "closeouts": [closeout.as_payload() for closeout in batch.closeouts],
                },
                connection_epoch=batch.connection_epoch,
                source_sent_at=batch.sent_at,
            )
            changed |= self._upsert_session(batch=batch)
            for round_item in batch.rounds:
                changed |= self._upsert_round(batch=batch, round_item=round_item)
            for closeout in batch.closeouts:
                changed |= self._upsert_closeout(batch=batch, closeout=closeout)
            changed |= self._clear_live_process_if_stable_result_materialized(batch=batch)
        return changed

    def upsert_probe_observation(self, observation: ProjectionProbeObservationUpsert, *, batch_id: str | None = None) -> bool:
        observation = self._normalize_probe_observation(observation)
        batch_id = _require_text(batch_id or observation.idempotency_key, "batch_id")
        with self._lock, self._conn:
            changed = self._record_receipt(
                message_family="transport_probe_observation_upsert",
                idempotency_key=observation.idempotency_key,
                batch_id=batch_id,
                pc_id=observation.pc_id or "unknown",
                workspace_id=None,
                session_id=None,
                thread_id=None,
                projection_version=None,
                payload=observation.as_payload(),
                connection_epoch=0,
                source_sent_at=observation.observed_at,
            )
            existing = self._fetch_one(
                "SELECT idempotency_key, payload_json FROM projection_probe_observations WHERE probe_id = ?",
                (observation.probe_id,),
            )
            if existing is not None:
                existing_payload = _json_loads(existing["payload_json"])
                if existing["idempotency_key"] == observation.idempotency_key:
                    if existing_payload != observation.as_payload():
                        raise ProjectionStoreConflictError(
                            "probe_conflict",
                            f"probe observation already exists with different payload: {observation.probe_id}",
                        )
                    return False
                if existing_payload != observation.as_payload():
                    raise ProjectionStoreConflictError(
                        "probe_conflict",
                        f"probe observation already exists with different payload: {observation.probe_id}",
                    )
                return False
            self._conn.execute(
                """
                INSERT INTO projection_probe_observations (
                    probe_id, idempotency_key, pc_id, request_id, packet_id, receipt_id, mailbox_message_id,
                    summary_text, observation_status, observed_at, payload_json, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.probe_id,
                    observation.idempotency_key,
                    observation.pc_id,
                    observation.request_id,
                    observation.packet_id,
                    observation.receipt_id,
                    observation.mailbox_message_id,
                    observation.summary_text,
                    observation.observation_status,
                    observation.observed_at,
                    _json_dumps(observation.as_payload()),
                    observation.observed_at,
                ),
            )
            return changed or True

    def list_sessions(
        self,
        *,
        pc_id: str | None = None,
        workspace_id: str | None = None,
        session_id: str | None = None,
        thread_id: str | None = None,
        include_ended: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if pc_id is not None:
            clauses.append("pc_id = ?")
            params.append(_require_text(pc_id, "pc_id"))
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(_require_text(workspace_id, "workspace_id"))
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(_require_text(session_id, "session_id"))
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(_require_text(thread_id, "thread_id"))
        if not include_ended:
            clauses.append("lifecycle = 'active'")
        query = "SELECT * FROM projection_sessions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += (
            " ORDER BY CASE WHEN lifecycle = 'active' THEN 0 ELSE 1 END, "
            "COALESCE(last_progress_at, last_active_at, updated_at) DESC, updated_at DESC, session_id ASC"
        )
        rows = self._fetch_all(query, params)
        return [self._session_row_to_list_item(row) for row in rows]

    def get_projection_version(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
    ) -> int | None:
        row = self._session_row(pc_id=pc_id, workspace_id=workspace_id, session_id=session_id, thread_id=thread_id)
        if row is None:
            return None
        return int(row["projection_version"])

    def get_session_snapshot(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
    ) -> dict[str, Any] | None:
        row = self._session_row(pc_id=pc_id, workspace_id=workspace_id, session_id=session_id, thread_id=thread_id)
        if row is None:
            return None
        rounds = self.list_session_history_rounds(
            pc_id=pc_id,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
        )
        return {
            "locator": {
                "pc_id": row["pc_id"],
                "workspace_id": row["workspace_id"],
                "session_id": row["session_id"],
                "thread_id": row["thread_id"],
            },
            "session": self._session_row_to_list_item(row),
            "session_snapshot": {
                **self._session_row_to_snapshot_item(row),
                "latest_session_action": None,
                "history_rounds": rounds,
            },
        }

    def get_session_live_process(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
    ) -> dict[str, Any] | None:
        row = self._session_row(pc_id=pc_id, workspace_id=workspace_id, session_id=session_id, thread_id=thread_id)
        if row is None:
            return None
        return self._session_live_process_snapshot_item(row["session_key"])

    def upsert_session_live_process(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        command_id: str,
        stream_id: str,
        last_seq: int,
        items: Sequence[Mapping[str, Any]],
        updated_at: str,
        status: str = "streaming",
        task_id: str | None = None,
    ) -> bool:
        normalized_items = _require_sequence_of_mappings(list(items), "items")
        if not normalized_items:
            return False
        resolved_session = self._resolve_session_row_for_live_process(
            pc_id=pc_id,
            workspace_id=workspace_id,
            session_id=session_id,
        )
        if resolved_session is None:
            return False
        payload = {
            "command_id": _require_text(command_id, "command_id"),
            "stream_id": _require_text(stream_id, "stream_id"),
            "task_id": _optional_text(task_id, "task_id"),
            "last_seq": _require_int(last_seq, "last_seq", minimum=1),
            "status": _require_text(status, "status"),
            "items": normalized_items,
            "updated_at": _require_text(updated_at, "updated_at"),
        }
        with self._lock, self._conn:
            existing = self._session_live_process_row(resolved_session["session_key"])
            if existing is not None and self._session_live_process_row_to_payload(existing) == payload:
                return False
            self._conn.execute(
                """
                INSERT INTO projection_session_live_process (
                    session_key, pc_id, workspace_id, session_id, thread_id, command_id, stream_id, task_id,
                    last_seq, status, items_json, updated_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    pc_id = excluded.pc_id,
                    workspace_id = excluded.workspace_id,
                    session_id = excluded.session_id,
                    thread_id = excluded.thread_id,
                    command_id = excluded.command_id,
                    stream_id = excluded.stream_id,
                    task_id = excluded.task_id,
                    last_seq = excluded.last_seq,
                    status = excluded.status,
                    items_json = excluded.items_json,
                    updated_at = excluded.updated_at,
                    applied_at = excluded.applied_at
                """,
                (
                    resolved_session["session_key"],
                    resolved_session["pc_id"],
                    resolved_session["workspace_id"],
                    resolved_session["session_id"],
                    resolved_session["thread_id"],
                    payload["command_id"],
                    payload["stream_id"],
                    payload["task_id"],
                    payload["last_seq"],
                    payload["status"],
                    _json_dumps(payload["items"]),
                    payload["updated_at"],
                    payload["updated_at"],
                ),
            )
            return True

    def clear_session_live_process(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
    ) -> bool:
        row = self._session_row(pc_id=pc_id, workspace_id=workspace_id, session_id=session_id, thread_id=thread_id)
        if row is None:
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM projection_session_live_process WHERE session_key = ?",
                (row["session_key"],),
            )
            return int(cursor.rowcount or 0) > 0

    def list_session_history_rounds(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
    ) -> list[dict[str, Any]]:
        row = self._session_row(pc_id=pc_id, workspace_id=workspace_id, session_id=session_id, thread_id=thread_id)
        if row is None:
            return []
        round_rows = self._fetch_all(
            """
            SELECT *
            FROM projection_history_rounds
            WHERE session_key = ?
            ORDER BY round_sort_at ASC, task_id ASC
            """,
            (row["session_key"],),
        )
        rounds: list[dict[str, Any]] = []
        for index, round_row in enumerate(round_rows, start=1):
            rounds.append(self._round_row_to_history_item(round_row, round_number=index))
        rounds.reverse()
        return rounds

    def _normalize_batch(self, batch: ProjectionSessionBatch) -> ProjectionSessionBatch:
        if isinstance(batch, ProjectionSessionBatch):
            return batch
        if isinstance(batch, dict):
            return ProjectionSessionBatch(**batch)
        raise ValueError("batch must be a ProjectionSessionBatch")

    def _normalize_probe_observation(self, observation: ProjectionProbeObservationUpsert) -> ProjectionProbeObservationUpsert:
        if isinstance(observation, ProjectionProbeObservationUpsert):
            return observation
        if isinstance(observation, dict):
            return ProjectionProbeObservationUpsert(**observation)
        raise ValueError("observation must be a ProjectionProbeObservationUpsert")

    def _session_row(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
        thread_id: str,
    ) -> sqlite3.Row | None:
        return self._fetch_one(
            """
            SELECT *
            FROM projection_sessions
            WHERE pc_id = ? AND workspace_id = ? AND session_id = ? AND thread_id = ?
            """,
            (
                _require_text(pc_id, "pc_id"),
                _require_text(workspace_id, "workspace_id"),
                _require_text(session_id, "session_id"),
                _require_text(thread_id, "thread_id"),
            ),
        )

    def _resolve_session_row_for_live_process(
        self,
        *,
        pc_id: str,
        workspace_id: str,
        session_id: str,
    ) -> sqlite3.Row | None:
        rows = self._fetch_all(
            """
            SELECT *
            FROM projection_sessions
            WHERE pc_id = ? AND workspace_id = ? AND session_id = ?
            ORDER BY CASE WHEN lifecycle = 'active' THEN 0 ELSE 1 END, updated_at DESC, thread_id ASC
            """,
            (
                _require_text(pc_id, "pc_id"),
                _require_text(workspace_id, "workspace_id"),
                _require_text(session_id, "session_id"),
            ),
        )
        if len(rows) != 1:
            return None
        return rows[0]

    def _session_row_to_list_item(self, row: sqlite3.Row) -> dict[str, Any]:
        live_process = self._session_live_process_snapshot_item(row["session_key"])
        return {
            "session_id": row["session_id"],
            "thread_id": row["thread_id"],
            "pc_id": row["pc_id"],
            "workspace_id": row["workspace_id"],
            "session_name": row["session_name"],
            "status": row["list_status"],
            "lifecycle": row["lifecycle"],
            "backend": row["backend"],
            "backend_transport": row["backend_transport"],
            "profile": row["profile"],
            "permission": row["permission"],
            "repo_path": row["repo_path"],
            "workdir": row["workdir"],
            "current_task_id": row["current_task_id"],
            "queued_task_id": row["queued_task_id"],
            "pending_task_count": row["pending_task_count"],
            "last_summary": row["last_summary"],
            "last_active_at": row["last_active_at"],
            "last_progress_at": row["last_progress_at"],
            "backend_session_id": row["backend_session_id"],
            "backend_session_resumable": bool(row["backend_session_resumable"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "live_process": live_process,
        }

    def _session_row_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "pc_id": row["pc_id"],
            "workspace_id": row["workspace_id"],
            "session_id": row["session_id"],
            "thread_id": row["thread_id"],
            "session_name": row["session_name"],
            "backend": row["backend"],
            "backend_transport": row["backend_transport"],
            "profile": row["profile"],
            "permission": row["permission"],
            "repo_path": row["repo_path"],
            "workdir": row["workdir"],
            "list_status": row["list_status"],
            "snapshot_status": row["snapshot_status"],
            "lifecycle": row["lifecycle"],
            "current_task_id": row["current_task_id"],
            "queued_task_id": row["queued_task_id"],
            "pending_task_count": row["pending_task_count"],
            "last_summary": row["last_summary"],
            "last_active_at": row["last_active_at"],
            "last_progress_at": row["last_progress_at"],
            "paused_from_status": row["paused_from_status"],
            "backend_session_id": row["backend_session_id"],
            "backend_session_resumable": bool(row["backend_session_resumable"]),
            "question_state": _json_loads(row["question_state_json"]),
            "timeline_items": _json_loads(row["timeline_items_json"]) or [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _session_row_to_snapshot_item(self, row: sqlite3.Row) -> dict[str, Any]:
        live_process = self._session_live_process_snapshot_item(row["session_key"])
        return {
            "session_name": row["session_name"],
            "backend": row["backend"],
            "repo_path": row["repo_path"],
            "workdir": row["workdir"],
            "status": row["snapshot_status"],
            "lifecycle": row["lifecycle"],
            "last_summary": row["last_summary"] or row["snapshot_status"].replace("_", " ").title() + ".",
            "last_active_at": row["last_active_at"],
            "last_progress_at": _max_timestamp_text(
                row["last_progress_at"],
                None if live_process is None else live_process["updated_at"],
            ),
            "paused_from_status": row["paused_from_status"],
            "question_state": _json_loads(row["question_state_json"]),
            "timeline_items": _json_loads(row["timeline_items_json"]) or [],
            "live_process": live_process,
        }

    def _session_live_process_row(self, session_key: str) -> sqlite3.Row | None:
        return self._fetch_one(
            "SELECT * FROM projection_session_live_process WHERE session_key = ?",
            (_require_text(session_key, "session_key"),),
        )

    def _session_live_process_snapshot_item(self, session_key: str) -> dict[str, Any] | None:
        row = self._session_live_process_row(session_key)
        if row is None:
            return None
        return {
            "status": row["status"],
            "updated_at": row["updated_at"],
            "items": _json_loads(row["items_json"]) or [],
        }

    def _session_live_process_row_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "command_id": row["command_id"],
            "stream_id": row["stream_id"],
            "task_id": row["task_id"],
            "last_seq": int(row["last_seq"]),
            "status": row["status"],
            "items": _json_loads(row["items_json"]) or [],
            "updated_at": row["updated_at"],
        }

    def _clear_live_process_if_stable_result_materialized(self, *, batch: ProjectionSessionBatch) -> bool:
        live_process_row = self._session_live_process_row(batch.session.session_key)
        if live_process_row is None:
            return False
        live_process_task_id = str(live_process_row["task_id"] or "").strip() or None
        if live_process_task_id is None:
            return False
        stable_statuses = {"awaiting_user_input", "paused", "done", "failed", "killed"}
        materialized_current_round = any(
            round_item.task_id == live_process_task_id and round_item.status in stable_statuses
            for round_item in batch.rounds
        )
        if not materialized_current_round:
            return False
        cursor = self._conn.execute(
            "DELETE FROM projection_session_live_process WHERE session_key = ?",
            (batch.session.session_key,),
        )
        return int(cursor.rowcount or 0) > 0

    def _round_row_to_history_item(self, row: sqlite3.Row, *, round_number: int) -> dict[str, Any]:
        input_attachments = self._round_attachments(row["round_key"], "input")
        result_attachments = self._round_attachments(row["round_key"], "result")
        return {
            "round_id": row["round_id"],
            "round_number": round_number,
            "created_at": row["created_at"],
            "status": row["status"],
            "speaker_label": row["speaker_label"],
            "input": {
                "text": row["input_text"],
                "attachments": input_attachments,
            },
            "process": {
                "items": _json_loads(row["process_items_json"]) or [],
            },
            "result": {
                "text": row["result_text"],
                "attachments": result_attachments,
            },
        }

    def _round_attachments(self, round_key: str, role: str) -> list[dict[str, Any]]:
        rows = self._fetch_all(
            """
            SELECT *
            FROM projection_round_attachments
            WHERE round_key = ? AND attachment_role = ?
            ORDER BY ordinal ASC
            """,
            (round_key, role),
        )
        return [
            {
                "attachment_id": row["attachment_id"],
                "display_name": row["display_name"],
                "content_type": row["content_type"],
                "size_bytes": row["size_bytes"],
                "is_image": bool(row["is_image"]),
                "ordinal": row["ordinal"],
            }
            for row in rows
        ]

    def _upsert_session(self, *, batch: ProjectionSessionBatch) -> bool:
        session = batch.session
        row = self._session_row(
            pc_id=session.pc_id,
            workspace_id=session.workspace_id,
            session_id=session.session_id,
            thread_id=session.thread_id,
        )
        payload = session.as_payload()
        if row is not None:
            existing_payload = self._session_row_to_payload(row)
            if int(row["projection_version"]) > session.projection_version:
                return False
            if int(row["projection_version"]) == session.projection_version:
                if existing_payload != payload:
                    raise ProjectionStoreConflictError(
                        "session_conflict",
                        f"session projection already exists with different payload: {session.session_key}",
                    )
                self._record_receipt(
                    message_family="session_projection_upsert",
                    idempotency_key=session.idempotency_key,
                    batch_id=batch.batch_id,
                    pc_id=session.pc_id,
                    workspace_id=session.workspace_id,
                    session_id=session.session_id,
                    thread_id=session.thread_id,
                    projection_version=session.projection_version,
                    payload=payload,
                    connection_epoch=batch.connection_epoch,
                    source_sent_at=batch.sent_at,
                )
                return False
        changed = self._record_receipt(
            message_family="session_projection_upsert",
            idempotency_key=session.idempotency_key,
            batch_id=batch.batch_id,
            pc_id=session.pc_id,
            workspace_id=session.workspace_id,
            session_id=session.session_id,
            thread_id=session.thread_id,
            projection_version=session.projection_version,
            payload=payload,
            connection_epoch=batch.connection_epoch,
            source_sent_at=batch.sent_at,
        )
        self._conn.execute(
            """
            INSERT INTO projection_sessions (
                session_key, pc_id, workspace_id, session_id, thread_id, session_name, backend, backend_transport,
                profile, permission, repo_path, workdir, list_status, snapshot_status, lifecycle, current_task_id,
                queued_task_id, pending_task_count, last_summary, last_active_at, last_progress_at,
                paused_from_status, backend_session_id, backend_session_resumable, question_state_json,
                timeline_items_json, created_at, updated_at, projection_version, source_updated_at, applied_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(session_key) DO UPDATE SET
                session_name = excluded.session_name,
                backend = excluded.backend,
                backend_transport = excluded.backend_transport,
                profile = excluded.profile,
                permission = excluded.permission,
                repo_path = excluded.repo_path,
                workdir = excluded.workdir,
                list_status = excluded.list_status,
                snapshot_status = excluded.snapshot_status,
                lifecycle = excluded.lifecycle,
                current_task_id = excluded.current_task_id,
                queued_task_id = excluded.queued_task_id,
                pending_task_count = excluded.pending_task_count,
                last_summary = excluded.last_summary,
                last_active_at = excluded.last_active_at,
                last_progress_at = excluded.last_progress_at,
                paused_from_status = excluded.paused_from_status,
                backend_session_id = excluded.backend_session_id,
                backend_session_resumable = excluded.backend_session_resumable,
                question_state_json = excluded.question_state_json,
                timeline_items_json = excluded.timeline_items_json,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                projection_version = excluded.projection_version,
                source_updated_at = excluded.source_updated_at,
                applied_at = excluded.applied_at
            """,
            (
                session.session_key,
                session.pc_id,
                session.workspace_id,
                session.session_id,
                session.thread_id,
                session.session_name,
                session.backend,
                session.backend_transport,
                session.profile,
                session.permission,
                session.repo_path,
                session.workdir,
                session.list_status,
                session.snapshot_status,
                session.lifecycle,
                session.current_task_id,
                session.queued_task_id,
                session.pending_task_count,
                session.last_summary,
                session.last_active_at,
                session.last_progress_at,
                session.paused_from_status,
                session.backend_session_id,
                int(session.backend_session_resumable),
                _json_dumps(session.question_state) if session.question_state is not None else None,
                _json_dumps(session.timeline_items),
                session.created_at,
                session.updated_at,
                session.projection_version,
                session.source_updated_at,
                batch.sent_at,
            ),
        )
        return True

    def _upsert_round(self, *, batch: ProjectionSessionBatch, round_item: ProjectionRoundUpsert) -> bool:
        session = batch.session
        projection_version = round_item.projection_version or session.projection_version
        round_key = _round_key(session.session_key, round_item.round_id)
        payload = round_item.as_payload()
        existing = self._fetch_one(
            "SELECT projection_version FROM projection_history_rounds WHERE round_key = ?",
            (round_key,),
        )
        if existing is not None:
            existing_version = int(existing["projection_version"])
            if existing_version > projection_version:
                return False
            if existing_version == projection_version:
                existing_payload = self._round_row_to_payload(round_key)
                if existing_payload != payload:
                    raise ProjectionStoreConflictError(
                        "round_conflict",
                        f"round projection already exists with different payload: {round_item.round_id}",
                    )
                return self._record_receipt(
                    message_family="session_round_upsert",
                    idempotency_key=round_item.idempotency_key,
                    batch_id=batch.batch_id,
                    pc_id=session.pc_id,
                    workspace_id=session.workspace_id,
                    session_id=session.session_id,
                    thread_id=session.thread_id,
                    projection_version=projection_version,
                    payload=payload,
                    connection_epoch=batch.connection_epoch,
                    source_sent_at=batch.sent_at,
                )
                return False
        self._record_receipt(
            message_family="session_round_upsert",
            idempotency_key=round_item.idempotency_key,
            batch_id=batch.batch_id,
            pc_id=session.pc_id,
            workspace_id=session.workspace_id,
            session_id=session.session_id,
            thread_id=session.thread_id,
            projection_version=projection_version,
            payload=payload,
            connection_epoch=batch.connection_epoch,
            source_sent_at=batch.sent_at,
        )
        self._conn.execute(
            """
            INSERT INTO projection_history_rounds (
                round_key, session_key, idempotency_key, pc_id, workspace_id, session_id, thread_id, task_id,
                round_id, round_sort_at, created_at, status, speaker_label, input_text, process_items_json,
                result_text, projection_version, source_updated_at, applied_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(round_key) DO UPDATE SET
                session_key = excluded.session_key,
                idempotency_key = excluded.idempotency_key,
                pc_id = excluded.pc_id,
                workspace_id = excluded.workspace_id,
                session_id = excluded.session_id,
                thread_id = excluded.thread_id,
                task_id = excluded.task_id,
                round_id = excluded.round_id,
                round_sort_at = excluded.round_sort_at,
                created_at = excluded.created_at,
                status = excluded.status,
                speaker_label = excluded.speaker_label,
                input_text = excluded.input_text,
                process_items_json = excluded.process_items_json,
                result_text = excluded.result_text,
                projection_version = excluded.projection_version,
                source_updated_at = excluded.source_updated_at,
                applied_at = excluded.applied_at
            """,
            (
                round_key,
                session.session_key,
                round_item.idempotency_key,
                session.pc_id,
                session.workspace_id,
                session.session_id,
                session.thread_id,
                round_item.task_id,
                round_item.round_id,
                round_item.round_sort_at,
                round_item.created_at,
                round_item.status,
                round_item.speaker_label,
                round_item.input_text,
                _json_dumps(round_item.process_items),
                round_item.result_text,
                projection_version,
                round_item.source_updated_at,
                batch.sent_at,
            ),
        )
        self._conn.execute("DELETE FROM projection_round_attachments WHERE round_key = ?", (round_key,))
        for role, attachments in (("input", round_item.input_attachments), ("result", round_item.result_attachments)):
            for fallback_ordinal, attachment in enumerate(attachments, start=1):
                ordinal = attachment.ordinal or fallback_ordinal
                self._conn.execute(
                    """
                    INSERT INTO projection_round_attachments (
                        round_attachment_key, round_key, attachment_role, ordinal, attachment_id, artifact_id,
                        display_name, content_type, size_bytes, is_image
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _round_attachment_key(round_key, role, ordinal),
                        round_key,
                        role,
                        ordinal,
                        attachment.attachment_id,
                        attachment.artifact_id,
                        attachment.display_name,
                        attachment.content_type,
                        attachment.size_bytes,
                        int(attachment.is_image),
                    ),
                )
        return True

    def _upsert_closeout(self, *, batch: ProjectionSessionBatch, closeout: ProjectionCloseoutUpsert) -> bool:
        session = batch.session
        projection_version = closeout.projection_version or session.projection_version
        payload = closeout.as_payload()
        existing = self._fetch_one(
            "SELECT projection_version FROM projection_closeouts WHERE closeout_key = ?",
            (closeout.closeout_key,),
        )
        if existing is not None:
            existing_version = int(existing["projection_version"])
            if existing_version > projection_version:
                return False
            if existing_version == projection_version:
                existing_payload = self._closeout_row_to_payload(closeout.closeout_key)
                if existing_payload != payload:
                    raise ProjectionStoreConflictError(
                        "closeout_conflict",
                        f"closeout projection already exists with different payload: {closeout.closeout_key}",
                    )
                return self._record_receipt(
                    message_family="session_closeout_upsert",
                    idempotency_key=closeout.idempotency_key,
                    batch_id=batch.batch_id,
                    pc_id=session.pc_id,
                    workspace_id=session.workspace_id,
                    session_id=session.session_id,
                    thread_id=session.thread_id,
                    projection_version=projection_version,
                    payload=payload,
                    connection_epoch=batch.connection_epoch,
                    source_sent_at=batch.sent_at,
                )
                return False
        self._record_receipt(
            message_family="session_closeout_upsert",
            idempotency_key=closeout.idempotency_key,
            batch_id=batch.batch_id,
            pc_id=session.pc_id,
            workspace_id=session.workspace_id,
            session_id=session.session_id,
            thread_id=session.thread_id,
            projection_version=projection_version,
            payload=payload,
            connection_epoch=batch.connection_epoch,
            source_sent_at=batch.sent_at,
        )
        self._conn.execute(
            """
            INSERT INTO projection_closeouts (
                closeout_key, session_key, idempotency_key, pc_id, workspace_id, session_id, thread_id,
                task_id, request_id, packet_id, receipt_id, action_type, target_session_identity_json,
                last_summary, terminal_mail_message_id, terminal_mail_subject, generated_at,
                projection_version, source_updated_at, applied_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(closeout_key) DO UPDATE SET
                session_key = excluded.session_key,
                idempotency_key = excluded.idempotency_key,
                pc_id = excluded.pc_id,
                workspace_id = excluded.workspace_id,
                session_id = excluded.session_id,
                thread_id = excluded.thread_id,
                task_id = excluded.task_id,
                request_id = excluded.request_id,
                packet_id = excluded.packet_id,
                receipt_id = excluded.receipt_id,
                action_type = excluded.action_type,
                target_session_identity_json = excluded.target_session_identity_json,
                last_summary = excluded.last_summary,
                terminal_mail_message_id = excluded.terminal_mail_message_id,
                terminal_mail_subject = excluded.terminal_mail_subject,
                generated_at = excluded.generated_at,
                projection_version = excluded.projection_version,
                source_updated_at = excluded.source_updated_at,
                applied_at = excluded.applied_at
            """,
            (
                closeout.closeout_key,
                session.session_key,
                closeout.idempotency_key,
                session.pc_id,
                session.workspace_id,
                session.session_id,
                session.thread_id,
                closeout.task_id,
                closeout.request_id,
                closeout.packet_id,
                closeout.receipt_id,
                closeout.action_type,
                _json_dumps(closeout.target_session_identity) if closeout.target_session_identity is not None else None,
                closeout.last_summary,
                closeout.terminal_mail_message_id,
                closeout.terminal_mail_subject,
                closeout.generated_at,
                projection_version,
                closeout.source_updated_at,
                batch.sent_at,
            ),
        )
        return True

    def _round_row_to_payload(self, round_key: str) -> dict[str, Any]:
        row = self._fetch_one("SELECT * FROM projection_history_rounds WHERE round_key = ?", (round_key,))
        if row is None:
            raise ProjectionStoreConflictError("round_missing", f"round not found: {round_key}")
        return {
            "round_id": row["round_id"],
            "task_id": row["task_id"],
            "round_sort_at": row["round_sort_at"],
            "created_at": row["created_at"],
            "status": row["status"],
            "speaker_label": row["speaker_label"],
            "input_text": row["input_text"],
            "process_items": _json_loads(row["process_items_json"]) or [],
            "result_text": row["result_text"],
            "input_attachments": self._round_attachments(row["round_key"], "input"),
            "result_attachments": self._round_attachments(row["round_key"], "result"),
            "source_updated_at": row["source_updated_at"],
        }

    def _closeout_row_to_payload(self, closeout_key: str) -> dict[str, Any]:
        row = self._fetch_one("SELECT * FROM projection_closeouts WHERE closeout_key = ?", (closeout_key,))
        if row is None:
            raise ProjectionStoreConflictError("closeout_missing", f"closeout not found: {closeout_key}")
        return {
            "closeout_key": row["closeout_key"],
            "task_id": row["task_id"],
            "request_id": row["request_id"],
            "packet_id": row["packet_id"],
            "receipt_id": row["receipt_id"],
            "action_type": row["action_type"],
            "target_session_identity": _json_loads(row["target_session_identity_json"]),
            "last_summary": row["last_summary"],
            "terminal_mail_message_id": row["terminal_mail_message_id"],
            "terminal_mail_subject": row["terminal_mail_subject"],
            "generated_at": row["generated_at"],
            "source_updated_at": row["source_updated_at"],
        }


RelayProjectionStore = SqliteRelayProjectionStore


__all__ = [
    "ProjectionAttachmentUpsert",
    "ProjectionCloseoutUpsert",
    "ProjectionProbeObservationUpsert",
    "ProjectionRoundUpsert",
    "ProjectionSessionBatch",
    "ProjectionSessionUpsert",
    "ProjectionStoreConflictError",
    "RelayProjectionStore",
    "SqliteRelayProjectionStore",
]
