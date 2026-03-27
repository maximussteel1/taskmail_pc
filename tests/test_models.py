"""Model validation tests for Phase 0."""

from __future__ import annotations

from datetime import datetime

import pytest

from mail_runner.models import (
    MailEnvelope,
    ModelValidationError,
    ParsedMailAction,
    RunResult,
    SessionState,
    TaskSnapshot,
    ThreadState,
    WorkspaceState,
)


def test_mail_envelope_accepts_valid_input() -> None:
    envelope = MailEnvelope(
        message_id="<msg-1@example.com>",
        subject="[OC] Example task",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date=datetime.utcnow(),
        imap_uid=101,
        imap_uid_validity=777,
        references=["<root@example.com>"],
        body_text="hello",
        raw_headers={"Subject": "[OC] Example task"},
    )

    assert envelope.subject == "[OC] Example task"
    assert envelope.imap_uid == 101
    assert envelope.imap_uid_validity == 777


def test_parsed_mail_action_rejects_invalid_confidence() -> None:
    with pytest.raises(ModelValidationError):
        ParsedMailAction(
            action="NEW_TASK",
            confidence=1.5,
            backend="opencode",
            raw_user_text="do something",
        )


def test_task_snapshot_rejects_non_positive_timeout() -> None:
    with pytest.raises(ModelValidationError):
        TaskSnapshot(
            task_id="task_001",
            thread_id="thread_001",
            backend="codex",
            repo_path="D:\\repo",
            workdir=None,
            task_text="Analyze this repo",
            timeout_minutes=0,
            mode="modify",
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:00:00",
        )


def test_thread_state_rejects_invalid_status() -> None:
    with pytest.raises(ModelValidationError):
        ThreadState(
            thread_id="thread_001",
            root_message_id="<root@example.com>",
            latest_message_id="<latest@example.com>",
            subject_norm="[OC] subject",
            backend="opencode",
            repo_path="D:\\repo",
            workdir=None,
            current_task_id="task_001",
            last_task_snapshot_file="snapshots\\task_001.json",
            status="broken",  # type: ignore[arg-type]
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:00:00",
        )


def test_run_result_requires_log_paths() -> None:
    with pytest.raises(ModelValidationError):
        RunResult(
            task_id="task_001",
            thread_id="thread_001",
            backend="codex",
            status="success",
            exit_code=0,
            started_at="2026-03-12T10:00:00",
            finished_at="2026-03-12T10:00:01",
            stdout_file="",
            stderr_file="stderr.log",
        )


def test_workspace_state_accepts_valid_input() -> None:
    workspace = WorkspaceState(
        workspace_id="workspace_123",
        repo_path="D:\\repo",
        workdir="src",
        workspace_norm="d:/repo|src",
        session_ids=["thread_001"],
        active_session_ids=["thread_001"],
        active_session_id="thread_001",
        created_at="2026-03-12T10:00:00",
        updated_at="2026-03-12T10:05:00",
    )

    assert workspace.workspace_id == "workspace_123"
    assert workspace.active_session_ids == ["thread_001"]


def test_session_state_rejects_invalid_status() -> None:
    with pytest.raises(ModelValidationError):
        SessionState(
            session_id="thread_001",
            workspace_id="workspace_123",
            thread_id="thread_001",
            session_name="Demo task",
            session_norm="demo task",
            backend="opencode",
            repo_path="D:\\repo",
            workdir="src",
            status="broken",  # type: ignore[arg-type]
            current_task_id="task_001",
            last_task_snapshot_file="snapshots/task_001.json",
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:05:00",
        )
