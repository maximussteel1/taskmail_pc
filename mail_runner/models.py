"""Core data models for the mail runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .status import BACKEND_NAMES, CURRENT_RUN_STATUSES, CURRENT_THREAD_STATUSES

BackendName = Literal["opencode", "codex"]
MailAction = Literal[
    "NEW_TASK",
    "UPDATE_TASK",
    "APPEND_CONTEXT",
    "ANSWER_QUESTION",
    "STATUS_QUERY",
    "RERUN",
    "KILL",
    "UNKNOWN",
]
TaskMode = Literal["modify", "analysis_only"]
ThreadStatus = Literal["idle", "accepted", "running", "done", "failed", "killed", "awaiting_user_input", "paused"]
RunStatus = Literal["success", "failed", "killed", "awaiting_user_input", "paused"]

_BACKENDS = set(BACKEND_NAMES)
_ACTIONS = {
    "NEW_TASK",
    "UPDATE_TASK",
    "APPEND_CONTEXT",
    "ANSWER_QUESTION",
    "STATUS_QUERY",
    "RERUN",
    "KILL",
    "UNKNOWN",
}
_MODES = {"modify", "analysis_only"}
_THREAD_STATUSES = set(CURRENT_THREAD_STATUSES)
_RUN_STATUSES = set(CURRENT_RUN_STATUSES)


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
    task_text_delta: str | None = None
    acceptance_delta: list[str] | None = None
    timeout_minutes: int | None = None
    mode: TaskMode | None = None
    raw_user_text: str = ""
    notes: str | None = None

    def __post_init__(self) -> None:
        _require_literal(self.action, "action", _ACTIONS)
        if not isinstance(self.confidence, (float, int)) or not 0.0 <= float(self.confidence) <= 1.0:
            raise ModelValidationError("confidence must be between 0.0 and 1.0")
        self.confidence = float(self.confidence)
        if self.backend is not None:
            _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
        _require_optional_text(self.task_text_delta, "task_text_delta")
        if self.acceptance_delta is not None:
            _require_string_list(self.acceptance_delta, "acceptance_delta")
        if self.timeout_minutes is not None and self.timeout_minutes <= 0:
            raise ModelValidationError("timeout_minutes must be positive")
        if self.mode is not None:
            _require_literal(self.mode, "mode", _MODES)
        if not isinstance(self.raw_user_text, str):
            raise ModelValidationError("raw_user_text must be a string")
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
    acceptance: list[str] = field(default_factory=list)
    timeout_minutes: int = 60
    mode: TaskMode = "modify"
    attachments: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.thread_id, "thread_id")
        _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
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
    history_files: list[str] = field(default_factory=list)
    last_summary: str | None = None
    pending_question_id: str | None = None
    pending_question_text: str | None = None
    pending_choices: list[str] = field(default_factory=list)
    awaiting_since: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _require_text(self.thread_id, "thread_id")
        _require_text(self.root_message_id, "root_message_id")
        _require_text(self.latest_message_id, "latest_message_id")
        _require_text(self.subject_norm, "subject_norm")
        _require_literal(self.backend, "backend", _BACKENDS)
        _require_optional_text(self.profile, "profile")
        _require_text(self.repo_path, "repo_path")
        _require_optional_text(self.workdir, "workdir")
        _require_text(self.current_task_id, "current_task_id")
        _require_text(self.last_task_snapshot_file, "last_task_snapshot_file")
        _require_literal(self.status, "status", _THREAD_STATUSES)
        _require_string_list(self.history_files, "history_files")
        _require_optional_text(self.last_summary, "last_summary")
        _require_optional_text(self.pending_question_id, "pending_question_id")
        _require_optional_text(self.pending_question_text, "pending_question_text")
        _require_string_list(self.pending_choices, "pending_choices")
        _require_optional_text(self.awaiting_since, "awaiting_since")
        _require_text(self.created_at, "created_at")
        _require_text(self.updated_at, "updated_at")


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
    error_message: str | None = None
    question_id: str | None = None
    question_text: str | None = None
    pending_choices: list[str] = field(default_factory=list)

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
        _require_optional_text(self.error_message, "error_message")
        _require_optional_text(self.question_id, "question_id")
        _require_optional_text(self.question_text, "question_text")
        _require_string_list(self.pending_choices, "pending_choices")
