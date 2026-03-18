"""Helpers for multi-question waiting state and canonical answer handling."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from .models import QuestionAnswer, QuestionItem, RunResult, ThreadState


def _normalize_text(value: str) -> str:
    return " ".join(str(value).split()).strip().lower()


def synthesize_legacy_question(
    *,
    question_id: str | None,
    question_text: str | None,
    pending_choices: list[str] | None,
    fallback_task_id: str,
) -> QuestionItem | None:
    normalized_text = (question_text or "").strip()
    if not normalized_text:
        return None
    choices = [item for item in list(pending_choices or []) if str(item).strip()]
    return QuestionItem(
        question_set_id=(question_id or f"question_{fallback_task_id}"),
        question_id=(question_id or f"question_{fallback_task_id}"),
        question_type="single_choice" if choices else "short_text",
        question_text=normalized_text,
        required=True,
        choices=choices,
        choice_labels={},
    )


def effective_pending_questions(
    thread_state: ThreadState | None = None,
    result: RunResult | None = None,
    *,
    fallback_task_id: str | None = None,
) -> list[QuestionItem]:
    if result is not None and result.pending_questions:
        return list(result.pending_questions)
    if thread_state is not None and thread_state.pending_questions:
        return list(thread_state.pending_questions)
    if thread_state is not None:
        legacy = synthesize_legacy_question(
            question_id=thread_state.pending_question_id,
            question_text=thread_state.pending_question_text,
            pending_choices=thread_state.pending_choices,
            fallback_task_id=fallback_task_id or thread_state.current_task_id,
        )
        return [legacy] if legacy is not None else []
    if result is not None:
        legacy = synthesize_legacy_question(
            question_id=result.question_id,
            question_text=result.question_text,
            pending_choices=result.pending_choices,
            fallback_task_id=fallback_task_id or result.task_id,
        )
        return [legacy] if legacy is not None else []
    return []


def effective_question_set_id(
    thread_state: ThreadState | None = None,
    result: RunResult | None = None,
    *,
    fallback_task_id: str | None = None,
) -> str | None:
    if result is not None and result.question_set_id:
        return result.question_set_id
    if thread_state is not None and thread_state.pending_question_set_id:
        return thread_state.pending_question_set_id
    questions = effective_pending_questions(thread_state, result, fallback_task_id=fallback_task_id)
    if questions:
        return questions[0].question_set_id
    return None


def merge_question_answers(
    existing: Iterable[QuestionAnswer],
    incoming: Iterable[QuestionAnswer],
) -> list[QuestionAnswer]:
    merged: dict[str, QuestionAnswer] = {}
    order: list[str] = []
    for item in list(existing) + list(incoming):
        if item.question_id not in merged:
            order.append(item.question_id)
        merged[item.question_id] = item
    return [merged[question_id] for question_id in order]


def answers_by_id(answers: Iterable[QuestionAnswer]) -> dict[str, QuestionAnswer]:
    return {item.question_id: item for item in answers}


def missing_required_question_ids(
    questions: Iterable[QuestionItem],
    answers: Iterable[QuestionAnswer],
) -> list[str]:
    answer_map = answers_by_id(answers)
    return [item.question_id for item in questions if item.required and item.question_id not in answer_map]


def canonical_answer_summary(
    question_set_id: str,
    answers: Iterable[QuestionAnswer],
) -> str:
    lines = [f"Resolved answers for question set {question_set_id}:"]
    for answer in answers:
        lines.append(f"- {answer.question_id}: {answer.value}")
    lines.extend(["", "Continue the task with these answers."])
    return "\n".join(lines)


def canonical_answer_context(
    question_set_id: str,
    questions: Iterable[QuestionItem],
    answers: Iterable[QuestionAnswer],
) -> str:
    question_map = {item.question_id: item for item in questions}
    lines = [f"Resolved answers for question set {question_set_id}:"]
    for answer in answers:
        question_text = question_map.get(answer.question_id).question_text if answer.question_id in question_map else ""
        lines.append(f"- {answer.question_id}: {answer.value}")
        if question_text:
            lines.append(f"  Question: {question_text}")
    return "\n".join(lines)


def question_item_as_dict(item: QuestionItem) -> dict[str, object]:
    return asdict(item)

