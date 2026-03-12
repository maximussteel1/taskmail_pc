"""Task compilation tests for Phase 3."""

from __future__ import annotations

from mail_runner.models import ParsedMailAction, TaskSnapshot, ThreadState
from mail_runner.status import BACKEND_OPENCODE, THREAD_STATUS_DONE
from mail_runner.task_compiler import compile_task


def _snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile="fast",
        repo_path="D:\\repo",
        workdir="src",
        task_text="Original task text",
        acceptance=["pytest passes"],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )


def _thread_state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        profile="fast",
        history_files=["runs/task_001/result.json"],
        last_summary="Completed",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )


def test_compile_task_updates_selected_fields_and_keeps_profile() -> None:
    compiled = compile_task(
        ParsedMailAction(
            action="UPDATE_TASK",
            confidence=0.9,
            task_text_delta="Only analyze the issue.",
            timeout_minutes=120,
            mode="analysis_only",
            raw_user_text="Timeout: 120",
        ),
        _thread_state(),
        _snapshot(),
        task_id="task_002",
        now="2026-03-12T12:10:00",
    )

    assert compiled is not None
    assert compiled.task_id == "task_002"
    assert compiled.task_text == "Only analyze the issue."
    assert compiled.timeout_minutes == 120
    assert compiled.mode == "analysis_only"
    assert compiled.profile == "fast"


def test_compile_task_appends_context_and_reruns() -> None:
    appended = compile_task(
        ParsedMailAction(action="APPEND_CONTEXT", confidence=0.6, raw_user_text="Additional details."),
        _thread_state(),
        _snapshot(),
        task_id="task_003",
        now="2026-03-12T12:20:00",
    )
    rerun = compile_task(
        ParsedMailAction(action="RERUN", confidence=0.9, raw_user_text="rerun"),
        _thread_state(),
        _snapshot(),
        task_id="task_004",
        now="2026-03-12T12:30:00",
    )

    assert appended is not None
    assert "Additional context from reply:" in appended.task_text
    assert rerun is not None
    assert rerun.task_text == "Original task text"
    assert rerun.profile == "fast"


def test_compile_task_answers_pending_question_and_updates_profile() -> None:
    state = _thread_state()
    state.pending_question_id = "question_task_001"
    state.pending_question_text = "Should I update both modules?"
    compiled = compile_task(
        ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=0.9,
            profile="strong",
            raw_user_text="Yes, update both modules.",
        ),
        state,
        _snapshot(),
        task_id="task_005",
        now="2026-03-12T12:40:00",
    )

    assert compiled is not None
    assert compiled.profile == "strong"
    assert "Answer to pending question (question_task_001):" in compiled.task_text
    assert "Yes, update both modules." in compiled.task_text
