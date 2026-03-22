"""Utilities for exporting stitched multi-turn thread conversations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .quote_extractor import extract_reply_delta
from .state_capsule import strip_task_capsules
from .workspace import WorkspaceManager

_STATUS_RE = re.compile(r"^\[(?P<status>[A-Z]+)\]")
_RAW_MAIL_RE = re.compile(r"^raw_(?P<index>\d+)\.json$")


@dataclass(slots=True)
class TranscriptTurn:
    index: int
    raw_file: str
    role: str
    subject: str
    date: str
    message_id: str
    status: str | None
    content: str


def _normalize_text(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").strip()


def _status_from_subject(subject: str) -> str | None:
    match = _STATUS_RE.match((subject or "").strip())
    if not match:
        return None
    return match.group("status")


def _strip_capsules(body_text: str) -> str:
    return strip_task_capsules(_normalize_text(body_text))


def _extract_system_text(body_text: str) -> str:
    text = _strip_capsules(body_text)
    if not text:
        return ""

    reply_marker = "\nReply:\n"
    if reply_marker in text:
        _, _, reply = text.partition(reply_marker)
        return reply.strip()

    summary_match = re.search(r"(?m)^Summary:\s*(.+)$", text)
    if summary_match:
        return summary_match.group(1).strip()

    lines = text.splitlines()
    status_line_index = next((idx for idx, line in enumerate(lines) if line.startswith("Status:")), None)
    if status_line_index is not None:
        intro = "\n".join(lines[:status_line_index]).strip()
        if intro:
            return intro

    question_match = re.search(r"(?m)^Question:\s*(.+)$", text)
    if question_match:
        content_lines = [f"Question: {question_match.group(1).strip()}"]
        choices_match = re.search(r"(?m)^Choices:\s*(.+)$", text)
        if choices_match:
            content_lines.append(f"Choices: {choices_match.group(1).strip()}")
        return "\n".join(content_lines)

    status_match = re.search(r"(?m)^Status:\s*(.+)$", text)
    if status_match:
        return f"Status: {status_match.group(1).strip()}"

    return text.strip()


def _extract_user_text(body_text: str) -> str:
    text = extract_reply_delta(body_text)
    if text:
        return text
    return _normalize_text(body_text)


def _raw_index(raw_path: Path) -> int:
    match = _RAW_MAIL_RE.match(raw_path.name)
    if not match:
        raise ValueError(f"Unsupported raw mail filename: {raw_path.name}")
    return int(match.group("index"))


def _iter_raw_mail_paths(task_root: str | Path, thread_id: str) -> list[Path]:
    workspace = WorkspaceManager(task_root)
    mail_dir = workspace.mail_dir(thread_id)
    if not mail_dir.exists():
        raise FileNotFoundError(f"Mail archive directory does not exist: {mail_dir}")
    return sorted(mail_dir.glob("raw_*.json"), key=_raw_index)


def build_thread_transcript(
    thread_id: str,
    task_root: str | Path,
    *,
    include_empty: bool = False,
) -> list[TranscriptTurn]:
    turns: list[TranscriptTurn] = []
    for raw_path in _iter_raw_mail_paths(task_root, thread_id):
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_headers = payload.get("raw_headers") or {}
        is_system = raw_headers.get(SYSTEM_MESSAGE_HEADER) == SYSTEM_MESSAGE_HEADER_VALUE
        role = "assistant" if is_system else "user"
        content = _extract_system_text(payload.get("body_text", "")) if is_system else _extract_user_text(payload.get("body_text", ""))
        if content or include_empty:
            turns.append(
                TranscriptTurn(
                    index=_raw_index(raw_path),
                    raw_file=raw_path.name,
                    role=role,
                    subject=_normalize_text(str(payload.get("subject", ""))),
                    date=_normalize_text(str(payload.get("date", ""))),
                    message_id=_normalize_text(str(payload.get("message_id", ""))),
                    status=_status_from_subject(str(payload.get("subject", ""))) if is_system else None,
                    content=content,
                )
            )
    return turns


def render_transcript_markdown(thread_id: str, turns: list[TranscriptTurn]) -> str:
    lines = [f"# Thread Transcript: {thread_id}", ""]
    if not turns:
        lines.append("_No transcript turns were extracted._")
        return "\n".join(lines) + "\n"

    for turn in turns:
        label = "User" if turn.role == "user" else "Assistant"
        suffix = f" [{turn.status}]" if turn.status else ""
        lines.append(f"## {turn.index:03d}. {label}{suffix}")
        if turn.date:
            lines.append(f"- Date: {turn.date}")
        if turn.subject:
            lines.append(f"- Subject: {turn.subject}")
        if turn.raw_file:
            lines.append(f"- Raw File: {turn.raw_file}")
        lines.append("")
        lines.append(turn.content or "_(empty)_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_transcript_json(turns: list[TranscriptTurn]) -> str:
    payload = [
        {
            "index": turn.index,
            "raw_file": turn.raw_file,
            "role": turn.role,
            "subject": turn.subject,
            "date": turn.date,
            "message_id": turn.message_id,
            "status": turn.status,
            "content": turn.content,
        }
        for turn in turns
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
