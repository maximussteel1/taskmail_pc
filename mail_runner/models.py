"""Core data models for the mail runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .status import BACKEND_NAMES, BACKEND_TRANSPORT_CLI, BACKEND_TRANSPORT_NAMES, CURRENT_RUN_STATUSES, CURRENT_THREAD_STATUSES

BackendName = Literal["opencode", "codex"]
BackendTransport = Literal["cli", "sdk"]
LifecycleState = Literal["active", "ended"]
QuestionType = Literal["single_choice", "boolean", "short_text"]
ArtifactKind = Literal["image", "file"]
ExternalDeliveryProvider = Literal["cos"]
PermissionLevel = Literal["default", "highest"]
MailAction = Literal[
    "NEW_TASK",
    "NEW_SESSION",
    "CONTINUE_SESSION",
    "RESUME_SESSION",
    "END_SESSION",
    "PAUSE_SESSION",
    "UPDATE_TASK",
    "APPEND_CONTEXT",
    "ANSWER_QUESTION",
    "LIST_SESSIONS",
    "STATUS_QUERY",
    "RERUN",
    "KILL",
    "UNKNOWN",
]
TaskMode = Literal["modify", "analysis_only"]
TaskRunMode = Literal["new", "resume"]
ThreadStatus = Literal["idle", "accepted", "running", "done", "failed", "killed", "awaiting_user_input", "paused"]
RunStatus = Literal["success", "failed", "killed", "awaiting_user_input", "paused"]
SessionStatus = Literal["queued", "running", "waiting_user", "paused", "done", "failed", "killed", "archived"]

_BACKENDS = set(BACKEND_NAMES)
_BACKEND_TRANSPORTS = set(BACKEND_TRANSPORT_NAMES)
_LIFECYCLE_STATES = {"active", "ended"}
_ACTIONS = {
    "NEW_TASK",
    "NEW_SESSION",
    "CONTINUE_SESSION",
    "RESUME_SESSION",
    "END_SESSION",
    "PAUSE_SESSION",
    "UPDATE_TASK",
    "APPEND_CONTEXT",
    "ANSWER_QUESTION",
    "LIST_SESSIONS",
    "STATUS_QUERY",
    "RERUN",
    "KILL",
    "UNKNOWN",
}
_MODES = {"modify", "analysis_only"}
_TASK_RUN_MODES = {"new", "resume"}
_THREAD_STATUSES = set(CURRENT_THREAD_STATUSES)
_RUN_STATUSES = set(CURRENT_RUN_STATUSES)
_SESSION_STATUSES = {"queued", "running", "waiting_user", "paused", "done", "failed", "killed", "archived"}
_QUESTION_TYPES = {"single_choice", "boolean", "short_text"}
_ARTIFACT_KINDS = {"image", "file"}
_EXTERNAL_DELIVERY_PROVIDERS = {"cos"}
_PERMISSION_LEVELS = {"default", "highest"}


class ModelValidationError(ValueError):
    """Raised when a model receives invalid input."""


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string")


def _require_optional_text(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _require_text(value, field_name)


def _require_string_list(values: list[str], field_name: str) -> None:
    if not isinstance(values, list):
        raise ModelValidationError(f"{field_name} must be a list[str]")
    for item in values:
        _require_text(item, field_name)


def _require_literal(value: str, field_name: str, allowed: set[str]) -> None:
    if value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ModelValidationError(f"{field_name} must be one of: {allowed_text}")


def _require_optional_literal(value: str | None, field_name: str, allowed: set[str]) -> None:
    if value is None:
        return
    _require_literal(value, field_name, allowed)


@dataclass(slots=True)
class QuestionItem:
    question_set_id: str
    question_id: str
    question_type: QuestionType
    question_text: str
    required: bool = True
    choices: list[str] = field(default_factory=list)
    choice_labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.question_set_id, "question_set_id")
        _require_text(self.question_id, "question_id")
        _require_literal(self.question_type, "question_type", _QUESTION_TYPES)
        _require_text(self.question_text, "question_text")
        if not isinstance(self.required, bool):
            raise ModelValidationError("required must be a bool")
        _require_string_list(self.choices, "choices")
        if not isinstance(self.choice_labels, dict):
            raise ModelValidationError("choice_labels must be a dict[str, str]")
        normalized_choice_labels: dict[str, str] = {}
        for key, value in self.choice_labels.items():
            _require_text(str(key), "choice_labels key")
            if not isinstance(value, str):
                raise ModelValidationError("choice_labels values must be strings")
            normalized_choice_labels[str(key)] = value
        self.choice_labels = normalized_choice_labels


@dataclass(slots=True)
class QuestionAnswer:
    question_id: str
    value: str
    raw_value: str

    def __post_init__(self) -> None:
        _require_text(self.question_id, "question_id")
        _require_text(self.value, "value")
        _require_text(self.raw_value, "raw_value")


def _require_question_item_list(values: list[QuestionItem] | list[dict], field_name: str) -> list[QuestionItem]:
    if not isinstance(values, list):
        raise ModelValidationError(f"{field_name} must be a list[QuestionItem]")
    normalized: list[QuestionItem] = []
    for item in values:
        if isinstance(item, QuestionItem):
            normalized.append(item)
            continue
        if isinstance(item, dict):
            normalized.append(QuestionItem(**item))
            continue
        raise ModelValidationError(f"{field_name} must contain QuestionItem-compatible entries")
    return normalized


def _require_question_answer_list(values: list[QuestionAnswer] | list[dict], field_name: str) -> list[QuestionAnswer]:
    if not isinstance(values, list):
        raise ModelValidationError(f"{field_name} must be a list[QuestionAnswer]")
    normalized: list[QuestionAnswer] = []
    for item in values:
        if isinstance(item, QuestionAnswer):
            normalized.append(item)
            continue
        if isinstance(item, dict):
            normalized.append(QuestionAnswer(**item))
            continue
        raise ModelValidationError(f"{field_name} must contain QuestionAnswer-compatible entries")
    return normalized


@dataclass(slots=True)
class MailAttachment:
    filename: str
    content_type: str
    size_bytes: int
    saved_path: str | None = None
    raw_saved_path: str | None = None
    content_id: str | None = None
    is_inline: bool = False
    sha256: str | None = None
    content_bytes: bytes = b""

    def __post_init__(self) -> None:
        _require_text(self.filename, "filename")
        _require_text(self.content_type, "content_type")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ModelValidationError("size_bytes must be a non-negative integer")
        _require_optional_text(self.saved_path, "saved_path")
        _require_optional_text(self.raw_saved_path, "raw_saved_path")
        _require_optional_text(self.content_id, "content_id")
        if not isinstance(self.is_inline, bool):
            raise ModelValidationError("is_inline must be a bool")
        _require_optional_text(self.sha256, "sha256")
        if not isinstance(self.content_bytes, (bytes, bytearray)):
            raise ModelValidationError("content_bytes must be bytes")
        self.content_bytes = bytes(self.content_bytes)


@dataclass(slots=True)
class OutgoingAttachment:
    path: str
    name: str | None = None
    content_type: str | None = None
    attach: bool = True
    inline: bool = False
    content_id: str | None = None
    caption: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.path, "path")
        _require_optional_text(self.name, "name")
        _require_optional_text(self.content_type, "content_type")
        if not isinstance(self.attach, bool):
            raise ModelValidationError("attach must be a bool")
        if not isinstance(self.inline, bool):
            raise ModelValidationError("inline must be a bool")
        _require_optional_text(self.content_id, "content_id")
        _require_optional_text(self.caption, "caption")


@dataclass(slots=True)
class ExternalDelivery:
    artifact_id: str
    name: str
    provider: ExternalDeliveryProvider
    url: str
    expires_at: str
    object_key: str
    size_bytes: int
    content_type: str
    bucket: str
    path: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "artifact_id")
        _require_text(self.name, "name")
        _require_literal(self.provider, "provider", _EXTERNAL_DELIVERY_PROVIDERS)
        _require_text(self.url, "url")
        _require_text(self.expires_at, "expires_at")
        _require_text(self.object_key, "object_key")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ModelValidationError("size_bytes must be a non-negative integer")
        _require_text(self.content_type, "content_type")
        _require_text(self.bucket, "bucket")
        _require_optional_text(self.path, "path")


@dataclass(slots=True)
class RunArtifact:
    artifact_id: str
    path: str
    name: str
    kind: ArtifactKind
    content_type: str
    source: str
    attach: bool = True
    inline_preview: bool = False
    caption: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "artifact_id")
        _require_text(self.path, "path")
        _require_text(self.name, "name")
        _require_literal(self.kind, "kind", _ARTIFACT_KINDS)
        _require_text(self.content_type, "content_type")
        _require_text(self.source, "source")
        if not isinstance(self.attach, bool):
            raise ModelValidationError("attach must be a bool")
        if not isinstance(self.inline_preview, bool):
            raise ModelValidationError("inline_preview must be a bool")
        _require_optional_text(self.caption, "caption")


@dataclass(slots=True)
class MailEnvelope:
    message_id: str
    subject: str
    from_addr: str
    to_addr: str
    date: datetime | str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    body_text: str = ""
    attachments: list[MailAttachment] = field(default_factory=list)
    raw_headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.message_id, "message_id")
        _require_text(self.subject, "subject")
        _require_text(self.from_addr, "from_addr")
        _require_text(self.to_addr, "to_addr")
        if not isinstance(self.date, (datetime, str)):
            raise ModelValidationError("date must be a datetime or string")
        _require_optional_text(self.in_reply_to, "in_reply_to")
        _require_string_list(self.references, "references")
        if not isinstance(self.body_text, str):
            raise ModelValidationError("body_text must be a string")
        if not isinstance(self.attachments, list):
            raise ModelValidationError("attachments must be a list[MailAttachment]")
        normalized_attachments: list[MailAttachment] = []
        for item in self.attachments:
            if isinstance(item, MailAttachment):
                normalized_attachments.append(item)
                continue
            if not isinstance(item, dict):
                raise ModelValidationError("attachments must contain MailAttachment items")
            normalized_attachments.append(MailAttachment(**item))
        self.attachments = normalized_attachments
        if not isinstance(self.raw_headers, dict):
            raise ModelValidationError("raw_headers must be a dict[str, str]")
        for key, value in self.raw_headers.items():
            _require_text(key, "raw_headers key")
            if not isinstance(value, str):
                raise ModelValidationError("raw_headers values must be strings")


@dataclass(slots=True)
class ParsedMailAction:
    action: MailAction
    confidence: float
    backend: BackendName | None = None
    profile: str | None = None
    permission: PermissionLevel | None = None
    task_text_delta: str | None = None
    acceptance_delta: list[str] | None = None
    timeout_minutes: int | None = None
    mode: TaskMode | None = None
    raw_user_text: str = ""
    target_session_id: str | None = None
    question_answers: list[QuestionAnswer] = field(default_factory=list)
    missing_question_ids: list[str] = field(default_factory=list)
    invalid_answer_messages: list[str] = field(default_factory=list)
    used_structured_answers: bool = False
    notes: str | None = None

    def __post_init__(self) -> None:
        _require_literal(self.action, "action", _ACTIONS)
        if not isinstance(self.confidence, (float, int)) or not 0.0 <= float(self.confidence) <= 1.0:
            raise ModelValidationError("confidence must be between 0.0 and 1.0")
        self.confidence = float(self.confidence)
        if self.backend is not None:
            _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
        _require_optional_literal(self.permission, "permission", _PERMISSION_LEVELS)
        _require_optional_text(self.task_text_delta, "task_text_delta")
        if self.acceptance_delta is not None:
            _require_string_list(self.acceptance_delta, "acceptance_delta")
        if self.timeout_minutes is not None and self.timeout_minutes <= 0:
            raise ModelValidationError("timeout_minutes must be positive")
        if self.mode is not None:
            _require_literal(self.mode, "mode", _MODES)
        if not isinstance(self.raw_user_text, str):
            raise ModelValidationError("raw_user_text must be a string")
        _require_optional_text(self.target_session_id, "target_session_id")
        self.question_answers = _require_question_answer_list(self.question_answers, "question_answers")
        _require_string_list(self.missing_question_ids, "missing_question_ids")
        _require_string_list(self.invalid_answer_messages, "invalid_answer_messages")
        if not isinstance(self.used_structured_answers, bool):
            raise ModelValidationError("used_structured_answers must be a bool")
        _require_optional_text(self.notes, "notes")


@dataclass(slots=True)
class TaskSnapshot:
    task_id: str
    thread_id: str
    backend: BackendName
    repo_path: str
    workdir: str | None
    task_text: str
    profile: str | None = None
    permission: PermissionLevel | None = None
    acceptance: list[str] = field(default_factory=list)
    timeout_minutes: int = 60
    mode: TaskMode = "modify"
    attachments: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    run_mode: TaskRunMode = "new"
    backend_session_id: str | None = None
    turn_text: str | None = None
    backend_transport: BackendTransport = BACKEND_TRANSPORT_CLI

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.thread_id, "thread_id")
        _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
        _require_optional_literal(self.permission, "permission", _PERMISSION_LEVELS)
        _require_text(self.repo_path, "repo_path")
        _require_optional_text(self.workdir, "workdir")
        _require_text(self.task_text, "task_text")
        _require_string_list(self.acceptance, "acceptance")
        if self.timeout_minutes <= 0:
            raise ModelValidationError("timeout_minutes must be positive")
        _require_literal(self.mode, "mode", _MODES)
        _require_string_list(self.attachments, "attachments")
        _require_text(self.created_at, "created_at")
        _require_text(self.updated_at, "updated_at")
        _require_literal(self.run_mode, "run_mode", _TASK_RUN_MODES)
        _require_optional_text(self.backend_session_id, "backend_session_id")
        _require_optional_text(self.turn_text, "turn_text")
        _require_literal(self.backend_transport, "backend_transport", _BACKEND_TRANSPORTS)


@dataclass(slots=True)
class WorkspaceState:
    workspace_id: str
    repo_path: str
    workdir: str | None
    workspace_norm: str
    session_ids: list[str] = field(default_factory=list)
    active_session_id: str | None = None
    queued_session_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _require_text(self.workspace_id, "workspace_id")
        _require_text(self.repo_path, "repo_path")
        _require_optional_text(self.workdir, "workdir")
        _require_text(self.workspace_norm, "workspace_norm")
        _require_string_list(self.session_ids, "session_ids")
        _require_optional_text(self.active_session_id, "active_session_id")
        _require_string_list(self.queued_session_ids, "queued_session_ids")
        _require_text(self.created_at, "created_at")
        _require_text(self.updated_at, "updated_at")


@dataclass(slots=True)
class SessionState:
    session_id: str
    workspace_id: str
    thread_id: str
    session_name: str
    session_norm: str
    backend: BackendName
    profile: str | None = None
    permission: PermissionLevel | None = None
    repo_path: str = ""
    workdir: str | None = None
    status: SessionStatus = "queued"
    current_task_id: str = ""
    last_task_snapshot_file: str = ""
    queued_task_id: str | None = None
    queued_snapshot_file: str | None = None
    pending_task_count: int = 0
    history_files: list[str] = field(default_factory=list)
    last_summary: str | None = None
    lifecycle: LifecycleState = "active"
    last_active_at: str | None = None
    last_progress_at: str | None = None
    backend_session_id: str | None = None
    backend_session_resumable: bool = False
    backend_transport: BackendTransport = BACKEND_TRANSPORT_CLI
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _require_text(self.session_id, "session_id")
        _require_text(self.workspace_id, "workspace_id")
        _require_text(self.thread_id, "thread_id")
        _require_text(self.session_name, "session_name")
        _require_text(self.session_norm, "session_norm")
        _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
        _require_optional_literal(self.permission, "permission", _PERMISSION_LEVELS)
        _require_text(self.repo_path, "repo_path")
        _require_optional_text(self.workdir, "workdir")
        _require_literal(self.status, "status", _SESSION_STATUSES)
        _require_text(self.current_task_id, "current_task_id")
        _require_text(self.last_task_snapshot_file, "last_task_snapshot_file")
        _require_optional_text(self.queued_task_id, "queued_task_id")
        _require_optional_text(self.queued_snapshot_file, "queued_snapshot_file")
        if not isinstance(self.pending_task_count, int) or self.pending_task_count < 0:
            raise ModelValidationError("pending_task_count must be a non-negative integer")
        _require_string_list(self.history_files, "history_files")
        _require_optional_text(self.last_summary, "last_summary")
        _require_literal(self.lifecycle, "lifecycle", _LIFECYCLE_STATES)
        _require_optional_text(self.last_active_at, "last_active_at")
        _require_optional_text(self.last_progress_at, "last_progress_at")
        _require_optional_text(self.backend_session_id, "backend_session_id")
        if not isinstance(self.backend_session_resumable, bool):
            raise ModelValidationError("backend_session_resumable must be a bool")
        _require_literal(self.backend_transport, "backend_transport", _BACKEND_TRANSPORTS)
        _require_text(self.created_at, "created_at")
        _require_text(self.updated_at, "updated_at")
        if self.last_active_at is None:
            self.last_active_at = self.updated_at
        if self.last_progress_at is None:
            self.last_progress_at = self.updated_at


@dataclass(slots=True)
class ThreadState:
    thread_id: str
    root_message_id: str
    latest_message_id: str
    subject_norm: str
    backend: BackendName
    repo_path: str
    workdir: str | None
    current_task_id: str
    last_task_snapshot_file: str
    status: ThreadStatus
    profile: str | None = None
    permission: PermissionLevel | None = None
    history_files: list[str] = field(default_factory=list)
    last_summary: str | None = None
    lifecycle: LifecycleState = "active"
    last_active_at: str | None = None
    last_progress_at: str | None = None
    pending_question_id: str | None = None
    pending_question_text: str | None = None
    pending_choices: list[str] = field(default_factory=list)
    pending_question_set_id: str | None = None
    pending_questions: list[QuestionItem] = field(default_factory=list)
    collected_answers: list[QuestionAnswer] = field(default_factory=list)
    awaiting_since: str | None = None
    paused_from_status: ThreadStatus | None = None
    workspace_id: str | None = None
    workspace_norm: str | None = None
    session_id: str | None = None
    session_name: str | None = None
    session_norm: str | None = None
    backend_session_id: str | None = None
    backend_session_resumable: bool = False
    backend_transport: BackendTransport = BACKEND_TRANSPORT_CLI
    queued_task_id: str | None = None
    queued_snapshot_file: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _require_text(self.thread_id, "thread_id")
        _require_text(self.root_message_id, "root_message_id")
        _require_text(self.latest_message_id, "latest_message_id")
        _require_text(self.subject_norm, "subject_norm")
        _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
        _require_optional_literal(self.permission, "permission", _PERMISSION_LEVELS)
        _require_text(self.repo_path, "repo_path")
        _require_optional_text(self.workdir, "workdir")
        _require_text(self.current_task_id, "current_task_id")
        _require_text(self.last_task_snapshot_file, "last_task_snapshot_file")
        _require_literal(self.status, "status", _THREAD_STATUSES)
        _require_string_list(self.history_files, "history_files")
        _require_optional_text(self.last_summary, "last_summary")
        _require_literal(self.lifecycle, "lifecycle", _LIFECYCLE_STATES)
        _require_optional_text(self.last_active_at, "last_active_at")
        _require_optional_text(self.last_progress_at, "last_progress_at")
        _require_optional_text(self.pending_question_id, "pending_question_id")
        _require_optional_text(self.pending_question_text, "pending_question_text")
        _require_string_list(self.pending_choices, "pending_choices")
        _require_optional_text(self.pending_question_set_id, "pending_question_set_id")
        self.pending_questions = _require_question_item_list(self.pending_questions, "pending_questions")
        self.collected_answers = _require_question_answer_list(self.collected_answers, "collected_answers")
        _require_optional_text(self.awaiting_since, "awaiting_since")
        if self.paused_from_status is not None:
            _require_literal(self.paused_from_status, "paused_from_status", _THREAD_STATUSES)
        _require_optional_text(self.workspace_id, "workspace_id")
        _require_optional_text(self.workspace_norm, "workspace_norm")
        _require_optional_text(self.session_id, "session_id")
        _require_optional_text(self.session_name, "session_name")
        _require_optional_text(self.session_norm, "session_norm")
        _require_optional_text(self.backend_session_id, "backend_session_id")
        if not isinstance(self.backend_session_resumable, bool):
            raise ModelValidationError("backend_session_resumable must be a bool")
        _require_literal(self.backend_transport, "backend_transport", _BACKEND_TRANSPORTS)
        _require_optional_text(self.queued_task_id, "queued_task_id")
        _require_optional_text(self.queued_snapshot_file, "queued_snapshot_file")
        _require_text(self.created_at, "created_at")
        _require_text(self.updated_at, "updated_at")
        if self.last_active_at is None:
            self.last_active_at = self.updated_at
        if self.last_progress_at is None:
            self.last_progress_at = self.updated_at


@dataclass(slots=True)
class RunResult:
    task_id: str
    thread_id: str
    backend: BackendName
    status: RunStatus
    exit_code: int | None
    started_at: str
    finished_at: str | None
    stdout_file: str
    stderr_file: str
    summary_file: str | None = None
    artifacts_dir: str | None = None
    changed_files: list[str] = field(default_factory=list)
    tests_passed: bool | None = None
    error_type: str | None = None
    error_message: str | None = None
    question_id: str | None = None
    question_text: str | None = None
    pending_choices: list[str] = field(default_factory=list)
    question_set_id: str | None = None
    pending_questions: list[QuestionItem] = field(default_factory=list)
    backend_session_id: str | None = None
    backend_session_resumable: bool = False
    backend_transport: BackendTransport = BACKEND_TRANSPORT_CLI

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.thread_id, "thread_id")
        _require_literal(self.backend, "backend", _BACKENDS)
        _require_literal(self.status, "status", _RUN_STATUSES)
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise ModelValidationError("exit_code must be an int or None")
        _require_text(self.started_at, "started_at")
        _require_optional_text(self.finished_at, "finished_at")
        _require_text(self.stdout_file, "stdout_file")
        _require_text(self.stderr_file, "stderr_file")
        _require_optional_text(self.summary_file, "summary_file")
        _require_optional_text(self.artifacts_dir, "artifacts_dir")
        _require_string_list(self.changed_files, "changed_files")
        if self.tests_passed is not None and not isinstance(self.tests_passed, bool):
            raise ModelValidationError("tests_passed must be a bool or None")
        _require_optional_text(self.error_type, "error_type")
        _require_optional_text(self.error_message, "error_message")
        _require_optional_text(self.question_id, "question_id")
        _require_optional_text(self.question_text, "question_text")
        _require_string_list(self.pending_choices, "pending_choices")
        _require_optional_text(self.question_set_id, "question_set_id")
        self.pending_questions = _require_question_item_list(self.pending_questions, "pending_questions")
        _require_optional_text(self.backend_session_id, "backend_session_id")
        if not isinstance(self.backend_session_resumable, bool):
            raise ModelValidationError("backend_session_resumable must be a bool")
        _require_literal(self.backend_transport, "backend_transport", _BACKEND_TRANSPORTS)
