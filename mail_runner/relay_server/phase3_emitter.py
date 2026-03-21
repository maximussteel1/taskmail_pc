"""Phase 3 direct inbound emitter skeleton for runtime state projection."""

from __future__ import annotations

from typing import Any

from ..models import SessionState, ThreadState
from ..question_utils import effective_pending_questions
from ..status import (
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_DONE,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_KILLED,
    THREAD_STATUS_PAUSED,
    THREAD_STATUS_RUNNING,
)
from .protocol import ProtocolValidationError, build_session_update

_WIRE_STATUSES = {"queued", "running", "awaiting_user_input", "paused", "done", "failed", "killed"}
_WIRE_TERMINAL_STATUSES = {"done", "failed", "killed"}
_WIRE_PAUSED_FROM_STATUSES = {"queued", "awaiting_user_input", "done", "failed", "killed"}


def _require_matching_identity(session_state: SessionState, thread_state: ThreadState) -> None:
    if thread_state.workspace_id and thread_state.workspace_id != session_state.workspace_id:
        raise ProtocolValidationError("session_state.workspace_id and thread_state.workspace_id must match")
    if thread_state.session_id and thread_state.session_id != session_state.session_id:
        raise ProtocolValidationError("session_state.session_id and thread_state.session_id must match")
    if thread_state.thread_id != session_state.thread_id:
        raise ProtocolValidationError("session_state.thread_id and thread_state.thread_id must match")


def _event_at(session_state: SessionState, thread_state: ThreadState, fallback: str) -> str:
    return (
        session_state.last_progress_at
        or thread_state.last_progress_at
        or session_state.last_active_at
        or thread_state.last_active_at
        or fallback
    )


def _timeline_token(value: str) -> str:
    token = "".join(char if char.isalnum() else "_" for char in value.strip())
    return token.strip("_") or "event"


def normalize_phase3_wire_status(session_state: SessionState, thread_state: ThreadState) -> str:
    _require_matching_identity(session_state, thread_state)
    if session_state.status == "archived":
        raise ProtocolValidationError("session_state.status='archived' is out of scope for Phase 3 v1")

    if thread_state.status == THREAD_STATUS_ACCEPTED or session_state.status == "queued":
        return "queued"
    if thread_state.status == THREAD_STATUS_AWAITING_USER_INPUT or session_state.status == "waiting_user":
        return "awaiting_user_input"
    if thread_state.status in {
        THREAD_STATUS_RUNNING,
        THREAD_STATUS_PAUSED,
        THREAD_STATUS_DONE,
        THREAD_STATUS_FAILED,
        THREAD_STATUS_KILLED,
    }:
        return str(thread_state.status)
    if session_state.status in _WIRE_STATUSES:
        return str(session_state.status)
    raise ProtocolValidationError(
        f"unable to normalize SessionState.status={session_state.status!r} and ThreadState.status={thread_state.status!r}"
    )


def normalize_phase3_paused_from_status(thread_state: ThreadState) -> str | None:
    paused_from_status = thread_state.paused_from_status
    if paused_from_status is None:
        return None
    if paused_from_status == THREAD_STATUS_ACCEPTED:
        return "queued"
    if paused_from_status == THREAD_STATUS_AWAITING_USER_INPUT:
        return "awaiting_user_input"
    if paused_from_status in {THREAD_STATUS_DONE, THREAD_STATUS_FAILED, THREAD_STATUS_KILLED}:
        return str(paused_from_status)
    return None


def build_phase3_question_state(session_state: SessionState, thread_state: ThreadState) -> dict[str, Any] | None:
    _require_matching_identity(session_state, thread_state)
    questions = effective_pending_questions(thread_state, fallback_task_id=session_state.current_task_id)
    if not questions:
        return None
    question_set_id = questions[0].question_set_id
    return {
        "question_set_id": question_set_id,
        "question_count": len(questions),
        "questions": [
            {
                "question_id": item.question_id,
                "question_text": item.question_text,
                "question_type": item.question_type,
                "required": item.required,
                "choices": list(item.choices),
                "choice_labels": dict(item.choice_labels),
            }
            for item in questions
        ],
    }


def build_phase3_status_transition_item(
    *,
    status: str,
    created_at: str,
    item_id: str | None = None,
    business_event_key: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    if status not in _WIRE_STATUSES:
        raise ProtocolValidationError(f"unsupported status for status_transition item: {status}")
    return {
        "item_id": item_id or f"tl_status_{_timeline_token(status)}_{_timeline_token(created_at)}",
        "business_event_key": business_event_key or f"status/{status}/{created_at}",
        "item_type": "status_transition",
        "created_at": created_at,
        "status": status,
        "text": text,
        "question_set_id": None,
        "question_ids": [],
        "paused_from_status": None,
    }


def build_phase3_state_transition_item(
    *,
    status: str,
    created_at: str,
    item_id: str | None = None,
    business_event_key: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    return build_phase3_status_transition_item(
        status=status,
        created_at=created_at,
        item_id=item_id,
        business_event_key=business_event_key,
        text=text,
    )


def build_phase3_assistant_reply_preview_item(
    *,
    text: str,
    created_at: str,
    item_id: str | None = None,
    business_event_key: str | None = None,
) -> dict[str, Any]:
    return {
        "item_id": item_id or f"tl_reply_{_timeline_token(created_at)}",
        "business_event_key": business_event_key or f"reply/{created_at}",
        "item_type": "assistant_reply_preview",
        "created_at": created_at,
        "status": None,
        "text": text,
        "question_set_id": None,
        "question_ids": [],
        "paused_from_status": None,
    }


def build_phase3_question_prompt_item(
    question_state: dict[str, Any],
    *,
    created_at: str,
    text: str | None = None,
    item_id: str | None = None,
    business_event_key: str | None = None,
) -> dict[str, Any]:
    question_set_id = str(question_state["question_set_id"])
    question_ids = [str(item["question_id"]) for item in question_state["questions"]]
    if text is None:
        if len(question_state["questions"]) == 1:
            text = str(question_state["questions"][0]["question_text"])
        else:
            text = f"Need {len(question_ids)} answers before continuing."
    return {
        "item_id": item_id or f"tl_question_{_timeline_token(question_set_id)}_{_timeline_token(created_at)}",
        "business_event_key": business_event_key or f"question/{question_set_id}/{created_at}",
        "item_type": "question_prompt",
        "created_at": created_at,
        "status": None,
        "text": text,
        "question_set_id": question_set_id,
        "question_ids": question_ids,
        "paused_from_status": None,
    }


def build_phase3_paused_hint_item(
    *,
    paused_from_status: str,
    created_at: str,
    text: str,
    question_state: dict[str, Any] | None = None,
    item_id: str | None = None,
    business_event_key: str | None = None,
) -> dict[str, Any]:
    if paused_from_status not in _WIRE_PAUSED_FROM_STATUSES:
        raise ProtocolValidationError(f"unsupported paused_from_status for paused_hint item: {paused_from_status}")
    return {
        "item_id": item_id or f"tl_paused_{_timeline_token(paused_from_status)}_{_timeline_token(created_at)}",
        "business_event_key": business_event_key or f"paused/{paused_from_status}/{created_at}",
        "item_type": "paused_hint",
        "created_at": created_at,
        "status": None,
        "text": text,
        "question_set_id": question_state["question_set_id"] if question_state is not None else None,
        "question_ids": [item["question_id"] for item in question_state["questions"]] if question_state is not None else [],
        "paused_from_status": paused_from_status,
    }


def build_phase3_terminal_summary_item(
    *,
    status: str,
    created_at: str,
    text: str,
    item_id: str | None = None,
    business_event_key: str | None = None,
) -> dict[str, Any]:
    if status not in _WIRE_TERMINAL_STATUSES:
        raise ProtocolValidationError(f"unsupported status for terminal_summary item: {status}")
    return {
        "item_id": item_id or f"tl_terminal_{_timeline_token(status)}_{_timeline_token(created_at)}",
        "business_event_key": business_event_key or f"terminal/{status}/{created_at}",
        "item_type": "terminal_summary",
        "created_at": created_at,
        "status": status,
        "text": text,
        "question_set_id": None,
        "question_ids": [],
        "paused_from_status": None,
    }


def build_default_phase3_timeline_items(
    session_state: SessionState,
    thread_state: ThreadState,
    *,
    emitted_at: str,
    assistant_reply_preview_text: str | None = None,
) -> list[dict[str, Any]]:
    wire_status = normalize_phase3_wire_status(session_state, thread_state)
    question_state = build_phase3_question_state(session_state, thread_state)
    event_at = _event_at(session_state, thread_state, emitted_at)
    timeline_items: list[dict[str, Any]] = []
    if assistant_reply_preview_text:
        timeline_items.append(
            build_phase3_assistant_reply_preview_item(
                text=assistant_reply_preview_text,
                created_at=event_at,
            )
        )
    if wire_status == "awaiting_user_input" and question_state is not None:
        timeline_items.append(build_phase3_question_prompt_item(question_state, created_at=event_at))
    elif wire_status == "paused":
        paused_from_status = normalize_phase3_paused_from_status(thread_state)
        if paused_from_status and session_state.last_summary:
            timeline_items.append(
                build_phase3_paused_hint_item(
                    paused_from_status=paused_from_status,
                    created_at=event_at,
                    text=session_state.last_summary,
                    question_state=question_state,
                )
            )
    elif wire_status in _WIRE_TERMINAL_STATUSES and session_state.last_summary:
        timeline_items.append(
            build_phase3_terminal_summary_item(
                status=wire_status,
                created_at=event_at,
                text=session_state.last_summary,
            )
        )
    return timeline_items


def project_phase3_session_snapshot(
    session_state: SessionState,
    thread_state: ThreadState,
    *,
    emitted_at: str,
    timeline_items: list[dict[str, Any]] | None = None,
    assistant_reply_preview_text: str | None = None,
) -> dict[str, Any]:
    _require_matching_identity(session_state, thread_state)
    wire_status = normalize_phase3_wire_status(session_state, thread_state)
    return {
        "session_name": session_state.session_name,
        "backend": session_state.backend,
        "repo_path": session_state.repo_path,
        "workdir": session_state.workdir,
        "status": wire_status,
        "lifecycle": session_state.lifecycle,
        "last_summary": session_state.last_summary or wire_status.replace("_", " ").title() + ".",
        "last_active_at": session_state.last_active_at or emitted_at,
        "last_progress_at": session_state.last_progress_at or emitted_at,
        "paused_from_status": normalize_phase3_paused_from_status(thread_state),
        "question_state": build_phase3_question_state(session_state, thread_state),
        "timeline_items": (
            list(timeline_items)
            if timeline_items is not None
            else build_default_phase3_timeline_items(
                session_state,
                thread_state,
                emitted_at=emitted_at,
                assistant_reply_preview_text=assistant_reply_preview_text,
            )
        ),
    }


def project_phase3_state_transition_delta(
    session_state: SessionState,
    thread_state: ThreadState,
    *,
    emitted_at: str,
) -> dict[str, Any]:
    _require_matching_identity(session_state, thread_state)
    return {
        "delta_type": "state_transition",
        "state_transition": {
            "status": normalize_phase3_wire_status(session_state, thread_state),
            "lifecycle": session_state.lifecycle,
            "last_summary": session_state.last_summary,
            "last_active_at": session_state.last_active_at or emitted_at,
            "last_progress_at": session_state.last_progress_at or emitted_at,
            "paused_from_status": normalize_phase3_paused_from_status(thread_state),
            "question_state": build_phase3_question_state(session_state, thread_state),
        },
    }


def project_phase3_timeline_append_delta(timeline_items: list[dict[str, Any]]) -> dict[str, Any]:
    if not timeline_items:
        raise ProtocolValidationError("timeline_items must be non-empty for timeline_append deltas")
    return {
        "delta_type": "timeline_append",
        "timeline_items": list(timeline_items),
    }


def build_phase3_session_snapshot_update(
    *,
    subscription_id: str,
    session_state: SessionState,
    thread_state: ThreadState,
    update_id: str,
    sequence: int,
    sent_at: str,
    timeline_items: list[dict[str, Any]] | None = None,
    assistant_reply_preview_text: str | None = None,
) -> dict[str, Any]:
    return build_session_update(
        schema_version="phase3-direct-inbound-wire-v1",
        subscription_id=subscription_id,
        workspace_id=session_state.workspace_id,
        session_id=session_state.session_id,
        thread_id=session_state.thread_id,
        task_id=session_state.current_task_id,
        update_id=update_id,
        sequence=sequence,
        sent_at=sent_at,
        update_type="session_snapshot",
        session_snapshot=project_phase3_session_snapshot(
            session_state,
            thread_state,
            emitted_at=sent_at,
            timeline_items=timeline_items,
            assistant_reply_preview_text=assistant_reply_preview_text,
        ),
    )


def build_phase3_state_transition_update(
    *,
    subscription_id: str,
    session_state: SessionState,
    thread_state: ThreadState,
    update_id: str,
    sequence: int,
    sent_at: str,
) -> dict[str, Any]:
    return build_session_update(
        schema_version="phase3-direct-inbound-wire-v1",
        subscription_id=subscription_id,
        workspace_id=session_state.workspace_id,
        session_id=session_state.session_id,
        thread_id=session_state.thread_id,
        task_id=session_state.current_task_id,
        update_id=update_id,
        sequence=sequence,
        sent_at=sent_at,
        update_type="session_delta",
        session_delta=project_phase3_state_transition_delta(
            session_state,
            thread_state,
            emitted_at=sent_at,
        ),
    )


def build_phase3_timeline_append_update(
    *,
    subscription_id: str,
    session_state: SessionState,
    update_id: str,
    sequence: int,
    sent_at: str,
    timeline_items: list[dict[str, Any]],
) -> dict[str, Any]:
    return build_session_update(
        schema_version="phase3-direct-inbound-wire-v1",
        subscription_id=subscription_id,
        workspace_id=session_state.workspace_id,
        session_id=session_state.session_id,
        thread_id=session_state.thread_id,
        task_id=session_state.current_task_id,
        update_id=update_id,
        sequence=sequence,
        sent_at=sent_at,
        update_type="session_delta",
        session_delta=project_phase3_timeline_append_delta(timeline_items),
    )
