"""Reply context assembly helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import MailEnvelope, RunResult, TaskSnapshot, ThreadState
from .quote_extractor import extract_reply_delta
from .state_capsule import parse_question_capsule, parse_state_capsule
from .workspace import WorkspaceManager


def _workspace(task_root: str | Path | None = None) -> WorkspaceManager:
    root = task_root if task_root is not None else AppConfig().resolve_task_root()
    return WorkspaceManager(root)


def _load_latest_snapshot(workspace: WorkspaceManager, state: ThreadState) -> TaskSnapshot:
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
    question_capsule = parse_question_capsule(envelope.body_text)
    reply_delta = extract_reply_delta(envelope.body_text)
    latest_snapshot = _load_latest_snapshot(workspace, thread_state)
    latest_result = _load_latest_result(workspace, thread_state)
    pending_question = {
        "question_id": thread_state.pending_question_id,
        "question_text": thread_state.pending_question_text,
        "choices": list(thread_state.pending_choices),
    }
    if not pending_question["question_text"] and question_capsule is not None:
        pending_question = {
            "question_id": question_capsule.get("question_id"),
            "question_text": question_capsule.get("question_text"),
            "choices": list(question_capsule.get("choices", [])),
        }
    return {
        "envelope": envelope,
        "thread_state": thread_state,
        "latest_snapshot": latest_snapshot,
        "latest_result": latest_result,
        "capsule_state": capsule_state,
        "question_capsule": question_capsule,
        "pending_question": pending_question,
        "reply_delta": reply_delta,
        "raw_user_text": reply_delta,
    }
