"""Status mail rendering helpers."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import RunResult, TaskSnapshot, ThreadState
from .state_capsule import render_question_capsule, render_state_capsule

MAIL_STATUS_ACCEPTED = "ACCEPTED"
MAIL_STATUS_RUNNING = "RUNNING"
MAIL_STATUS_DONE = "DONE"
MAIL_STATUS_FAILED = "FAILED"
MAIL_STATUS_STATUS = "STATUS"
MAIL_STATUS_KILLED = "KILLED"
MAIL_STATUS_QUESTION = "QUESTION"


def build_status_subject(status_label: str, subject_text: str, session_id: str | None = None) -> str:
    subject = subject_text.strip()
    session_tag = f"[S:{session_id.strip()}]" if session_id and session_id.strip() else ""
    return f"[{status_label}]{session_tag} {subject}".rstrip()


def _build_capsule_state(
    state: ThreadState | dict[str, Any],
    task_snapshot: TaskSnapshot | None = None,
) -> dict[str, Any]:
    state_dict = asdict(state) if isinstance(state, ThreadState) else dict(state)
    return {
        "thread_id": state_dict.get("thread_id", ""),
        "workspace_id": state_dict.get("workspace_id", ""),
        "session_id": state_dict.get("session_id", ""),
        "session_name": state_dict.get("session_name", "") or state_dict.get("subject_norm", ""),
        "task_id": task_snapshot.task_id if task_snapshot is not None else (state_dict.get("current_task_id") or state_dict.get("task_id", "")),
        "backend": state_dict.get("backend", ""),
        "repo_path": state_dict.get("repo_path", ""),
        "workdir": state_dict.get("workdir", ""),
        "mode": task_snapshot.mode if task_snapshot is not None else state_dict.get("mode", ""),
        "status": state_dict.get("status", ""),
        "last_summary": state_dict.get("last_summary", ""),
    }


def build_status_mail(
    status_label: str,
    state: ThreadState | dict[str, Any],
    *,
    task_snapshot: TaskSnapshot | None = None,
    result: RunResult | None = None,
    captured_reply: str | None = None,
    intro: str | None = None,
    question_id: str | None = None,
    question_text: str | None = None,
    pending_choices: list[str] | None = None,
) -> str:
    state_dict = asdict(state) if isinstance(state, ThreadState) else dict(state)
    lines: list[str] = []
    if intro:
        lines.append(intro)
        lines.append("")
    lines.extend(
        [
            f"Status: {status_label}",
            f"Session ID: {state_dict.get('session_id') or state_dict.get('thread_id', '')}",
            f"Thread ID: {state_dict.get('thread_id', '')}",
            f"Task ID: {task_snapshot.task_id if task_snapshot is not None else (state_dict.get('current_task_id') or state_dict.get('task_id', ''))}",
            f"Backend: {state_dict.get('backend', '')}",
            f"Repo: {state_dict.get('repo_path', '')}",
            f"Workdir: {state_dict.get('workdir') or ''}",
        ]
    )
    if result is not None:
        lines.append(f"Exit Code: {result.exit_code if result.exit_code is not None else ''}")
        if result.error_message:
            lines.append(f"Error: {result.error_message}")
    if captured_reply:
        lines.extend(
            [
                "",
                "Reply:",
                captured_reply.rstrip(),
            ]
        )
    elif state_dict.get("last_summary"):
        lines.append(f"Summary: {state_dict['last_summary']}")
    resolved_question_text = question_text or state_dict.get("pending_question_text") or ""
    resolved_question_id = question_id or state_dict.get("pending_question_id") or ""
    resolved_choices = list(pending_choices or state_dict.get("pending_choices") or [])
    if resolved_question_text:
        lines.extend(
            [
                "",
                f"Question ID: {resolved_question_id}",
                f"Question: {resolved_question_text}",
            ]
        )
        if resolved_choices:
            lines.append(f"Choices: {' | '.join(resolved_choices)}")
    lines.append("")
    lines.append(render_state_capsule(_build_capsule_state(state, task_snapshot)))
    if resolved_question_text:
        lines.append("")
        lines.append(
            render_question_capsule(
                {
                    "question_id": resolved_question_id,
                    "question_text": resolved_question_text,
                    "choices": resolved_choices,
                }
            )
        )
    return "\n".join(lines).strip() + "\n"
