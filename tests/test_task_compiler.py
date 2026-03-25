"""Task compilation tests for Phase 3."""

from __future__ import annotations

from mail_runner.models import ParsedMailAction, QuestionAnswer, QuestionItem, TaskSnapshot, ThreadState
from mail_runner.status import BACKEND_CODEX, BACKEND_OPENCODE, THREAD_STATUS_DONE, THREAD_STATUS_FAILED
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
            backend=BACKEND_CODEX,
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
    assert compiled.backend == BACKEND_CODEX
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


def test_compile_task_inherits_existing_permission_when_reply_omits_it() -> None:
    state = _thread_state()
    state.permission = "highest"

    compiled = compile_task(
        ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            raw_user_text="Please continue with the cleanup.",
        ),
        state,
        _snapshot(),
        task_id="task_005_permission_inherit",
        now="2026-03-12T12:41:00",
    )

    assert compiled is not None
    assert compiled.permission == "highest"


def test_compile_task_allows_explicit_default_permission_override() -> None:
    state = _thread_state()
    state.permission = "highest"

    compiled = compile_task(
        ParsedMailAction(
            action="UPDATE_TASK",
            confidence=0.9,
            permission="default",
            task_text_delta="Only analyze the issue.",
            raw_user_text="Permission: default",
        ),
        state,
        _snapshot(),
        task_id="task_005_permission_default",
        now="2026-03-12T12:41:30",
    )

    assert compiled is not None
    assert compiled.permission == "default"


def test_compile_task_answers_multi_question_with_canonical_summary() -> None:
    state = _thread_state()
    state.pending_question_set_id = "phase2"
    state.pending_questions = [
        QuestionItem(
            question_set_id="phase2",
            question_id="phase2_entry_position",
            question_type="single_choice",
            question_text="Where should the entry go?",
            choices=["top", "below"],
            choice_labels={"top": "Top", "below": "Below"},
        ),
        QuestionItem(
            question_set_id="phase2",
            question_id="phase2_icon_strings",
            question_type="single_choice",
            question_text="Who provides strings?",
            choices=["provide", "reuse"],
            choice_labels={"provide": "You provide", "reuse": "Reuse existing"},
        ),
    ]
    state.collected_answers = [
        QuestionAnswer(
            question_id="phase2_entry_position",
            value="below",
            raw_value="账户列表下方",
        )
    ]
    compiled = compile_task(
        ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=0.95,
            question_answers=[
                QuestionAnswer(
                    question_id="phase2_icon_strings",
                    value="provide",
                    raw_value="你提供",
                )
            ],
            used_structured_answers=True,
            raw_user_text="Answers:\nphase2_icon_strings: provide",
        ),
        state,
        _snapshot(),
        task_id="task_008",
        now="2026-03-12T12:55:00",
    )

    assert compiled is not None
    assert "Resolved answers for question set phase2:" in compiled.task_text
    assert "- phase2_entry_position: below" in compiled.turn_text
    assert "- phase2_icon_strings: provide" in compiled.turn_text


def test_compile_task_resumes_native_session_without_rewriting_task_text() -> None:
    state = _thread_state()
    state.backend_session_id = "native-session-001"
    state.backend_session_resumable = True
    compiled = compile_task(
        ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            raw_user_text="Please continue with the cleanup.",
        ),
        state,
        _snapshot(),
        task_id="task_006",
        now="2026-03-12T12:45:00",
    )

    assert compiled is not None
    assert compiled.run_mode == "resume"
    assert compiled.backend_session_id == "native-session-001"
    assert compiled.backend_transport == "cli"
    assert compiled.turn_text == "Please continue with the cleanup."
    assert compiled.task_text == "Original task text"


def test_compile_task_preserves_sdk_transport_for_codex_continuation() -> None:
    snapshot = _snapshot()
    snapshot.backend = BACKEND_CODEX
    snapshot.backend_transport = "sdk"
    state = _thread_state()
    state.backend = BACKEND_CODEX
    state.backend_transport = "sdk"
    state.backend_session_id = "sdk-thread-001"
    state.backend_session_resumable = True

    compiled = compile_task(
        ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            raw_user_text="Please continue with the cleanup.",
        ),
        state,
        snapshot,
        task_id="task_006_sdk",
        now="2026-03-12T12:45:10",
    )

    assert compiled is not None
    assert compiled.backend == BACKEND_CODEX
    assert compiled.backend_transport == "sdk"
    assert compiled.backend_session_id == "sdk-thread-001"
    assert compiled.run_mode == "resume"


def test_compile_task_preserves_sdk_transport_for_opencode_continuation() -> None:
    snapshot = _snapshot()
    snapshot.backend_transport = "sdk"
    state = _thread_state()
    state.backend_transport = "sdk"
    state.backend_session_id = "sdk-thread-oc-001"
    state.backend_session_resumable = True

    compiled = compile_task(
        ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=1.0,
            raw_user_text="TOKEN-123",
        ),
        state,
        snapshot,
        task_id="task_006_sdk_opencode",
        now="2026-03-12T12:45:12",
    )

    assert compiled is not None
    assert compiled.backend == BACKEND_OPENCODE
    assert compiled.backend_transport == "sdk"
    assert compiled.backend_session_id == "sdk-thread-oc-001"
    assert compiled.run_mode == "resume"


def test_compile_task_uses_backend_default_transport_when_switching_backend() -> None:
    compiled = compile_task(
        ParsedMailAction(
            action="UPDATE_TASK",
            confidence=0.95,
            backend=BACKEND_CODEX,
            task_text_delta="Inspect the codebase and report findings.",
        ),
        _thread_state(),
        _snapshot(),
        task_id="task_006_switch_backend",
        now="2026-03-12T12:45:13",
        default_transport_for_backend=lambda backend: "sdk" if backend == BACKEND_CODEX else "cli",
    )

    assert compiled is not None
    assert compiled.backend == BACKEND_CODEX
    assert compiled.backend_transport == "sdk"


def test_compile_task_handles_explicit_resume_action() -> None:
    state = _thread_state()
    state.backend_session_id = "native-session-001"
    state.backend_session_resumable = True
    compiled = compile_task(
        ParsedMailAction(
            action="RESUME_SESSION",
            confidence=1.0,
            raw_user_text="Please continue with the cleanup.",
        ),
        state,
        _snapshot(),
        task_id="task_006_resume",
        now="2026-03-12T12:45:15",
    )

    assert compiled is not None
    assert compiled.run_mode == "resume"
    assert compiled.backend_session_id == "native-session-001"
    assert compiled.turn_text == "Please continue with the cleanup."


def test_compile_task_can_fallback_to_new_run_for_failed_resume_recovery() -> None:
    state = _thread_state()
    state.status = THREAD_STATUS_FAILED
    state.backend_session_id = None
    state.backend_session_resumable = False

    compiled = compile_task(
        ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            raw_user_text="Please continue with the cleanup.",
        ),
        state,
        _snapshot(),
        task_id="task_006_recovery",
        now="2026-03-12T12:45:30",
        fallback_to_new_run=True,
    )

    assert compiled is not None
    assert compiled.run_mode == "new"
    assert compiled.backend_session_id is None
    assert compiled.turn_text is None
    assert "Additional context from reply:" in compiled.task_text
    assert "Please continue with the cleanup." in compiled.task_text


def test_compile_task_merges_incoming_attachment_paths_into_resume_turn() -> None:
    state = _thread_state()
    state.backend_session_id = "native-session-001"
    state.backend_session_resumable = True
    snapshot = _snapshot()
    snapshot.attachments = ["E:\\repo\\existing.txt"]

    compiled = compile_task(
        ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            raw_user_text="Please continue with the cleanup.",
        ),
        state,
        snapshot,
        task_id="task_006b",
        now="2026-03-12T12:46:00",
        incoming_attachment_paths=["E:\\repo\\_mailin_20260314_001__photo.png"],
    )

    assert compiled is not None
    assert compiled.attachments == [
        "E:\\repo\\existing.txt",
        "E:\\repo\\_mailin_20260314_001__photo.png",
    ]
    assert "New incoming attachments materialized in workdir:" in compiled.turn_text
    assert "_mailin_20260314_001__photo.png" in compiled.turn_text


def test_compile_task_can_replay_answer_as_new_run_without_native_session() -> None:
    state = _thread_state()
    state.status = THREAD_STATUS_FAILED
    state.pending_question_id = "question_task_001"
    state.pending_question_text = "Should I update both modules?"

    compiled = compile_task(
        ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=0.9,
            raw_user_text="Yes, update both modules.",
        ),
        state,
        _snapshot(),
        task_id="task_006_answer_recovery",
        now="2026-03-12T12:46:30",
        fallback_to_new_run=True,
    )

    assert compiled is not None
    assert compiled.run_mode == "new"
    assert compiled.backend_session_id is None
    assert compiled.turn_text is None
    assert "Answer to pending question (question_task_001):" in compiled.task_text
    assert "Yes, update both modules." in compiled.task_text


def test_compile_task_builds_new_session_snapshot_with_new_thread() -> None:
    compiled = compile_task(
        ParsedMailAction(
            action="NEW_SESSION",
            confidence=0.8,
            raw_user_text="Also organize the logs directory.",
        ),
        _thread_state(),
        _snapshot(),
        task_id="task_007",
        thread_id="thread_002",
        now="2026-03-12T12:50:00",
    )

    assert compiled is not None
    assert compiled.thread_id == "thread_002"
    assert compiled.run_mode == "new"
    assert compiled.backend_session_id is None
    assert "Also organize the logs directory." in compiled.task_text
