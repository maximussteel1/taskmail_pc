"""Reply context assembly helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig
from .mail_attachments import attachment_summary_lines
from .models import MailEnvelope, QuestionAnswer, QuestionItem, RunResult, TaskSnapshot, ThreadState
from .question_utils import effective_pending_questions, effective_question_set_id
from .quote_extractor import extract_reply_delta
from .state_capsule import parse_question_capsules, parse_state_capsule
from .status import THREAD_STATUS_RUNNING
from .workspace import WorkspaceManager


def _workspace(task_root: str | Path | None = None) -> WorkspaceManager:
    root = task_root if task_root is not None else AppConfig().resolve_task_root()
    return WorkspaceManager(root)


def _load_latest_snapshot(workspace: WorkspaceManager, state: ThreadState) -> TaskSnapshot:
    if state.status == THREAD_STATUS_RUNNING and state.queued_snapshot_file:
        return workspace.load_snapshot(state.thread_id, state.queued_snapshot_file)
    return workspace.load_snapshot(state.thread_id, state.last_task_snapshot_file)


def _load_latest_result(workspace: WorkspaceManager, state: ThreadState) -> RunResult | None:
    if not state.history_files:
        return None
    return workspace.load_run_result(state.thread_id, state.history_files[-1])


def build_context(
    envelope: MailEnvelope,
    thread_state: ThreadState,
    task_root: str | Path | None = None,
) -> dict[str, Any]:
    workspace = _workspace(task_root)
    capsule_state = parse_state_capsule(envelope.body_text)
    question_capsules = parse_question_capsules(envelope.body_text)
    reply_delta = extract_reply_delta(envelope.body_text)
    latest_snapshot = _load_latest_snapshot(workspace, thread_state)
    latest_result = _load_latest_result(workspace, thread_state)
    pending_questions = effective_pending_questions(thread_state, fallback_task_id=latest_snapshot.task_id)
    if not pending_questions and question_capsules:
        pending_questions = [
            QuestionItem(
                question_set_id=str(capsule.get("question_set_id") or capsule.get("question_id") or f"question_{latest_snapshot.task_id}"),
                question_id=str(capsule.get("question_id") or f"question_{index + 1}"),
                question_type=str(capsule.get("question_type") or ("single_choice" if capsule.get("choices") else "short_text")),
                question_text=str(capsule.get("question_text") or ""),
                required=bool(capsule.get("required", True)),
                choices=list(capsule.get("choices", [])),
                choice_labels=dict(capsule.get("choice_labels", {})),
            )
            for index, capsule in enumerate(question_capsules)
            if str(capsule.get("question_text") or "").strip()
        ]
    pending_question = {
        "question_id": None,
        "question_text": None,
        "choices": [],
    }
    if pending_questions:
        first_question = pending_questions[-1]
        pending_question = {
            "question_id": first_question.question_id,
            "question_text": first_question.question_text,
            "choices": list(first_question.choices),
        }
    return {
        "envelope": envelope,
        "thread_state": thread_state,
        "latest_snapshot": latest_snapshot,
        "latest_result": latest_result,
        "capsule_state": capsule_state,
        "question_capsules": question_capsules,
        "pending_question": pending_question,
        "pending_question_set_id": effective_question_set_id(thread_state, fallback_task_id=latest_snapshot.task_id),
        "pending_questions": pending_questions,
        "collected_answers": list(thread_state.collected_answers or []),
        "reply_delta": reply_delta,
        "raw_user_text": reply_delta,
        "incoming_attachments": list(envelope.attachments),
        "incoming_attachment_paths": [item.saved_path for item in envelope.attachments if item.saved_path],
        "incoming_attachment_summary": attachment_summary_lines(envelope.attachments),
    }
