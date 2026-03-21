from __future__ import annotations

from mail_runner.models import QuestionItem, SessionState, ThreadState
from mail_runner.relay_server.phase3_emitter import (
    build_phase3_assistant_reply_preview_item,
    build_phase3_session_snapshot_update,
    build_phase3_state_transition_update,
    build_phase3_timeline_append_update,
    normalize_phase3_wire_status,
    project_phase3_session_snapshot,
)
from mail_runner.relay_server.protocol import RelaySessionUpdateMessage, parse_server_message


def _build_session_state(*, status: str = "running", last_summary: str | None = "Running.") -> SessionState:
    return SessionState(
        session_id="session_001",
        workspace_id="workspace_001",
        thread_id="thread_001",
        session_name="Phase 3 detail bridge",
        session_norm="phase 3 detail bridge",
        backend="codex",
        repo_path="E:\\repo",
        workdir="src",
        status=status,
        current_task_id="task_001",
        last_task_snapshot_file="snapshot_001.json",
        last_summary=last_summary,
        lifecycle="active",
        last_active_at="2026-03-21T23:00:00",
        last_progress_at="2026-03-21T23:00:00",
        created_at="2026-03-21T22:59:00",
        updated_at="2026-03-21T23:00:00",
    )


def _build_thread_state(
    *,
    status: str = "running",
    pending_questions: list[QuestionItem] | None = None,
    paused_from_status: str | None = None,
    last_summary: str | None = "Running.",
) -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="phase 3 detail bridge",
        backend="codex",
        repo_path="E:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshot_001.json",
        status=status,
        last_summary=last_summary,
        lifecycle="active",
        last_active_at="2026-03-21T23:00:00",
        last_progress_at="2026-03-21T23:00:00",
        pending_questions=list(pending_questions or []),
        paused_from_status=paused_from_status,
        workspace_id="workspace_001",
        workspace_norm="e:/repo|src",
        session_id="session_001",
        session_name="Phase 3 detail bridge",
        session_norm="phase 3 detail bridge",
        created_at="2026-03-21T22:59:00",
        updated_at="2026-03-21T23:00:00",
    )


def test_normalize_phase3_wire_status_maps_accepted_and_waiting_states() -> None:
    queued_session = _build_session_state(status="queued", last_summary="Queued.")
    accepted_thread = _build_thread_state(status="accepted", last_summary="Queued.")
    waiting_session = _build_session_state(status="waiting_user", last_summary="Need input.")
    waiting_thread = _build_thread_state(status="awaiting_user_input", last_summary="Need input.")

    assert normalize_phase3_wire_status(queued_session, accepted_thread) == "queued"
    assert normalize_phase3_wire_status(waiting_session, waiting_thread) == "awaiting_user_input"


def test_project_phase3_session_snapshot_includes_single_question_state_and_default_prompt_item() -> None:
    question = QuestionItem(
        question_set_id="qset_branch_choice",
        question_id="q_branch",
        question_type="single_choice",
        question_text="Which branch should I use?",
        required=True,
        choices=["main", "release"],
        choice_labels={"main": "Main branch", "release": "Release branch"},
    )
    session = _build_session_state(status="waiting_user", last_summary="Need one answer before continuing.")
    thread = _build_thread_state(
        status="awaiting_user_input",
        pending_questions=[question],
        last_summary="Need one answer before continuing.",
    )

    snapshot = project_phase3_session_snapshot(session, thread, emitted_at="2026-03-21T23:00:00")

    assert snapshot["status"] == "awaiting_user_input"
    assert snapshot["question_state"]["questions"][0]["question_id"] == "q_branch"
    assert snapshot["timeline_items"][0]["item_type"] == "question_prompt"
    assert snapshot["timeline_items"][0]["business_event_key"] == "question/qset_branch_choice/2026-03-21T23:00:00"


def test_build_phase3_session_snapshot_update_is_parseable_and_emits_paused_hint() -> None:
    session = _build_session_state(status="paused", last_summary="Paused after completion.")
    thread = _build_thread_state(status="paused", paused_from_status="done", last_summary="Paused after completion.")

    payload = build_phase3_session_snapshot_update(
        subscription_id="sub-001",
        session_state=session,
        thread_state=thread,
        update_id="sessupd:session_001:1",
        sequence=1,
        sent_at="2026-03-21T23:00:00",
    )
    parsed = parse_server_message(payload)

    assert isinstance(parsed, RelaySessionUpdateMessage)
    assert parsed.session_snapshot["status"] == "paused"
    assert parsed.session_snapshot["paused_from_status"] == "done"
    assert parsed.session_snapshot["timeline_items"][0]["item_type"] == "paused_hint"


def test_build_phase3_state_transition_and_timeline_append_updates_are_parseable() -> None:
    question = QuestionItem(
        question_set_id="qset_branch_choice",
        question_id="q_branch",
        question_type="single_choice",
        question_text="Which branch should I use?",
        required=True,
        choices=["main", "release"],
        choice_labels={"main": "Main branch", "release": "Release branch"},
    )
    session = _build_session_state(status="waiting_user", last_summary="Need one answer before continuing.")
    thread = _build_thread_state(
        status="awaiting_user_input",
        pending_questions=[question],
        last_summary="Need one answer before continuing.",
    )

    transition_payload = build_phase3_state_transition_update(
        subscription_id="sub-001",
        session_state=session,
        thread_state=thread,
        update_id="sessupd:session_001:2",
        sequence=2,
        sent_at="2026-03-21T23:01:00",
    )
    append_payload = build_phase3_timeline_append_update(
        subscription_id="sub-001",
        session_state=session,
        update_id="sessupd:session_001:3",
        sequence=3,
        sent_at="2026-03-21T23:02:00",
        timeline_items=[
            build_phase3_assistant_reply_preview_item(
                text="Preview from emitter skeleton.",
                created_at="2026-03-21T23:02:00",
            )
        ],
    )

    parsed_transition = parse_server_message(transition_payload)
    parsed_append = parse_server_message(append_payload)

    assert isinstance(parsed_transition, RelaySessionUpdateMessage)
    assert parsed_transition.session_delta["delta_type"] == "state_transition"
    assert parsed_transition.session_delta["state_transition"]["status"] == "awaiting_user_input"
    assert isinstance(parsed_append, RelaySessionUpdateMessage)
    assert parsed_append.session_delta["delta_type"] == "timeline_append"
    assert parsed_append.session_delta["timeline_items"][0]["item_type"] == "assistant_reply_preview"
