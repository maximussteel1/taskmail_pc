"""Task snapshot compilation helpers."""

from __future__ import annotations

from dataclasses import replace

from .models import ParsedMailAction, TaskSnapshot, ThreadState

_APPEND_PREFIX = "Additional context from reply:"


def _append_context_text(base_text: str, delta: str) -> str:
    clean_delta = delta.strip()
    if not clean_delta:
        return base_text
    if not base_text.strip():
        return f"{_APPEND_PREFIX}\n{clean_delta}"
    return f"{base_text.rstrip()}\n\n{_APPEND_PREFIX}\n{clean_delta}"


def compile_task(
    action: ParsedMailAction,
    thread_state: ThreadState,
    latest_snapshot: TaskSnapshot,
    *,
    task_id: str | None = None,
    now: str | None = None,
    thread_id: str | None = None,
) -> TaskSnapshot | None:
    if action.action in {"STATUS_QUERY", "KILL", "UNKNOWN", "LIST_SESSIONS"}:
        return None

    new_task_id = task_id or latest_snapshot.task_id
    timestamp = now or latest_snapshot.updated_at
    backend = action.backend or latest_snapshot.backend
    profile = action.profile if action.profile is not None else thread_state.profile
    next_thread_id = thread_id or latest_snapshot.thread_id

    if action.action == "RERUN":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
        )

    if action.action == "UPDATE_TASK":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            task_text=action.task_text_delta or latest_snapshot.task_text,
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
        )

    if action.action == "APPEND_CONTEXT":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            task_text=_append_context_text(latest_snapshot.task_text, action.raw_user_text),
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
        )

    if action.action == "NEW_SESSION":
        task_text = action.task_text_delta or latest_snapshot.task_text
        if not action.task_text_delta and action.raw_user_text.strip():
            task_text = _append_context_text(task_text, action.raw_user_text)
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            task_text=task_text,
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
        )

    if action.action == "CONTINUE_SESSION":
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            task_text=action.task_text_delta or latest_snapshot.task_text,
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            run_mode="resume",
            backend_session_id=thread_state.backend_session_id,
            turn_text=action.raw_user_text.strip() or "Continue the previous task.",
        )

    if action.action == "ANSWER_QUESTION":
        base_text = action.task_text_delta or latest_snapshot.task_text
        question_id = thread_state.pending_question_id or f"question_{latest_snapshot.task_id}"
        question_text = thread_state.pending_question_text or "Additional clarification requested by the backend."
        answer_block = (
            f"Answer to pending question ({question_id}):\n"
            f"Question: {question_text}\n"
            f"Answer: {action.raw_user_text.strip()}"
        )
        return replace(
            latest_snapshot,
            task_id=new_task_id,
            thread_id=next_thread_id,
            updated_at=timestamp,
            created_at=timestamp,
            backend=backend,
            profile=profile,
            task_text=_append_context_text(base_text, answer_block),
            acceptance=latest_snapshot.acceptance if action.acceptance_delta is None else list(action.acceptance_delta),
            timeout_minutes=action.timeout_minutes or latest_snapshot.timeout_minutes,
            mode=action.mode or latest_snapshot.mode,
            run_mode="resume",
            backend_session_id=thread_state.backend_session_id,
            turn_text=action.raw_user_text.strip() or question_text,
        )

    return None
