"""Retention helpers for live mailbox system messages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .reporter import (
    MAIL_STATUS_ACCEPTED,
    MAIL_STATUS_DONE,
    MAIL_STATUS_FAILED,
    MAIL_STATUS_KILLED,
    MAIL_STATUS_PAUSED,
    MAIL_STATUS_QUESTION,
    MAIL_STATUS_RUNNING,
    MAIL_STATUS_STATUS,
)

THREAD_PREFIX = "thread_"
SYNC_PROJECT_FOLDER_LIST_SUBJECT = "[SYNC] Project Folder List"
_STATUS_SUBJECT_RE = re.compile(r"^\[(?P<status>[A-Z]+)\]")
PRUNABLE_THREAD_STATUS_LABELS = {
    MAIL_STATUS_ACCEPTED,
    MAIL_STATUS_RUNNING,
    MAIL_STATUS_STATUS,
}
ACTION_REQUIRED_THREAD_STATUS_LABELS = {
    MAIL_STATUS_PAUSED,
    MAIL_STATUS_QUESTION,
}
RECEIPT_THREAD_STATUS_LABELS = {
    MAIL_STATUS_DONE,
    MAIL_STATUS_FAILED,
    MAIL_STATUS_KILLED,
}
THREAD_STATUS_LABELS = (
    PRUNABLE_THREAD_STATUS_LABELS
    | ACTION_REQUIRED_THREAD_STATUS_LABELS
    | RECEIPT_THREAD_STATUS_LABELS
)


def classify_thread_status_subject(subject: str) -> str | None:
    match = _STATUS_SUBJECT_RE.match(str(subject or "").strip())
    if match is None:
        return None
    label = match.group("status")
    if label in PRUNABLE_THREAD_STATUS_LABELS:
        return "progress"
    if label in ACTION_REQUIRED_THREAD_STATUS_LABELS:
        return "action_required"
    if label in RECEIPT_THREAD_STATUS_LABELS:
        return "receipt"
    return None


def is_thread_status_subject(subject: str) -> bool:
    return classify_thread_status_subject(subject) is not None


def is_replaceable_thread_status_subject(subject: str) -> bool:
    return classify_thread_status_subject(subject) == "progress"


def is_prunable_thread_status_subject(subject: str) -> bool:
    return is_replaceable_thread_status_subject(subject)


def is_sync_project_folder_reply_subject(subject: str) -> bool:
    return str(subject or "").strip() == SYNC_PROJECT_FOLDER_LIST_SUBJECT


def collect_stale_thread_status_message_ids(task_root: Path) -> list[str]:
    message_ids: list[str] = []
    seen_ids: set[str] = set()
    for thread_dir in sorted(path for path in task_root.glob(f"{THREAD_PREFIX}*") if path.is_dir()):
        mail_dir = thread_dir / "mail"
        if not mail_dir.exists():
            continue
        thread_status_items: list[tuple[str, str]] = []
        thread_seen_ids: set[str] = set()
        for raw_path in sorted(mail_dir.glob("raw_*.json")):
            try:
                payload = json.loads(raw_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            raw_headers = payload.get("raw_headers") or {}
            if not isinstance(raw_headers, dict):
                continue
            if str(raw_headers.get(SYSTEM_MESSAGE_HEADER) or "").strip() != SYSTEM_MESSAGE_HEADER_VALUE:
                continue
            message_id = str(payload.get("message_id") or "").strip()
            if not message_id or message_id in thread_seen_ids:
                continue
            subject = str(payload.get("subject") or raw_headers.get("Subject") or "").strip()
            classification = classify_thread_status_subject(subject)
            if classification is None:
                continue
            thread_seen_ids.add(message_id)
            thread_status_items.append((message_id, classification))
        if not thread_status_items:
            continue
        progress_ids = [message_id for message_id, classification in thread_status_items if classification == "progress"]
        if not progress_ids:
            continue
        latest_classification = thread_status_items[-1][1]
        stale_progress_ids = progress_ids[:-1] if latest_classification == "progress" else progress_ids
        for stale_message_id in stale_progress_ids:
            if stale_message_id in seen_ids:
                continue
            seen_ids.add(stale_message_id)
            message_ids.append(stale_message_id)
    return message_ids


def collect_stale_sync_message_ids(messages: Iterable[SystemMessageRef]) -> list[str]:
    sync_message_ids: list[str] = []
    seen_ids: set[str] = set()
    for message in messages:
        message_id = str(message.message_id or "").strip()
        if not message_id or message_id in seen_ids:
            continue
        if not is_sync_project_folder_reply_subject(message.subject):
            continue
        seen_ids.add(message_id)
        sync_message_ids.append(message_id)
    return sync_message_ids[:-1]


@dataclass(frozen=True, slots=True)
class SystemMessageRef:
    message_id: str
    subject: str
