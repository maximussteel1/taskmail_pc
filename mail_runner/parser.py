"""Mail subject and initial task parsing."""

from __future__ import annotations

import re
from typing import Any

from .status import BACKEND_CODEX, BACKEND_OPENCODE

_SUBJECT_PREFIX_RE = re.compile(r"^\s*\[(OC|CX|KILL|SYNC)\]\s*(.*)$", re.IGNORECASE)
_REPLY_PREFIX_RE = re.compile(r"^\s*((re|fw|fwd)\s*:\s*|(?:回复|答复)\s*[：:]\s*)+", re.IGNORECASE)
_STATUS_PREFIX_RE = re.compile(
    r"^\s*\[(OC|CX|KILL|SYNC|ACCEPTED|RUNNING|DONE|FAILED|STATUS|KILLED|QUESTION|S:[^\]]+)\]\s*",
    re.IGNORECASE,
)
_SESSION_TAG_RE = re.compile(r"\[S:([^\]]+)\]", re.IGNORECASE)
_HEADER_RE = re.compile(r"^\s*(Repo|Workdir|Timeout|Mode|Profile|Permission|Task|Acceptance)\s*:\s*(.*)$", re.IGNORECASE)
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")


def normalize_subject(subject: str) -> str:
    collapsed = " ".join(subject.split())
    while True:
        updated = _REPLY_PREFIX_RE.sub("", collapsed).strip()
        if updated == collapsed:
            break
        collapsed = updated
    while True:
        updated = _STATUS_PREFIX_RE.sub("", collapsed).strip()
        if updated == collapsed:
            break
        collapsed = updated
    return collapsed.lower()


def extract_session_tag(subject: str) -> str | None:
    match = _SESSION_TAG_RE.search(subject or "")
    if not match:
        return None
    session_id = match.group(1).strip()
    return session_id or None


def parse_subject(subject: str) -> dict[str, Any]:
    """Parse the subject line into routing metadata."""

    match = _SUBJECT_PREFIX_RE.match(subject or "")
    if not match:
        return {
            "is_new_task": False,
            "backend": None,
            "action": "UNKNOWN",
            "subject_text": subject.strip(),
            "subject_norm": normalize_subject(subject),
        }

    prefix = match.group(1).upper()
    subject_text = match.group(2).strip()
    backend = {
        "OC": BACKEND_OPENCODE,
        "CX": BACKEND_CODEX,
    }.get(prefix)
    if prefix == "KILL":
        action = "KILL"
    elif prefix == "SYNC":
        action = "SYNC_PROJECT_FOLDERS"
    else:
        action = "NEW_TASK"
    return {
        "is_new_task": backend is not None,
        "backend": backend,
        "action": action,
        "subject_text": subject_text,
        "subject_norm": normalize_subject(subject_text),
    }


def parse_initial_task(body_text: str, default_timeout_minutes: int = 60) -> dict[str, Any]:
    """Parse the first task mail body into structured fields."""

    scalar_values: dict[str, str] = {}
    task_lines: list[str] = []
    acceptance_lines: list[str] = []
    current_section: str | None = None

    for raw_line in (body_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _HEADER_RE.match(raw_line)
        if match:
            label = match.group(1).lower()
            remainder = match.group(2).strip()
            if label in {"repo", "workdir", "timeout", "mode", "profile", "permission"}:
                scalar_values[label] = remainder
                current_section = None
            elif label == "task":
                current_section = "task"
                if remainder:
                    task_lines.append(remainder)
            elif label == "acceptance":
                current_section = "acceptance"
                if remainder:
                    acceptance_lines.append(remainder)
            continue

        if current_section == "task":
            task_lines.append(raw_line)
        elif current_section == "acceptance":
            acceptance_lines.append(raw_line)

    repo_path = scalar_values.get("repo", "").strip()
    task_text = "\n".join(line.rstrip() for line in task_lines).strip()
    if not repo_path:
        raise ValueError("Initial task mail is missing Repo.")
    if not task_text:
        raise ValueError("Initial task mail is missing Task.")

    acceptance: list[str] = []
    for raw_line in acceptance_lines:
        cleaned = _LIST_PREFIX_RE.sub("", raw_line).strip()
        if cleaned:
            acceptance.append(cleaned)

    timeout_raw = scalar_values.get("timeout", "").strip()
    timeout_minutes = int(timeout_raw) if timeout_raw else default_timeout_minutes
    mode = scalar_values.get("mode", "").strip().lower() or "modify"
    workdir = scalar_values.get("workdir", "").strip() or None
    profile = scalar_values.get("profile", "").strip().lower() or None
    permission = scalar_values.get("permission", "").strip().lower() or None

    return {
        "repo_path": repo_path,
        "workdir": workdir,
        "timeout_minutes": timeout_minutes,
        "mode": mode,
        "profile": profile,
        "permission": permission,
        "task_text": task_text,
        "acceptance": acceptance,
    }
