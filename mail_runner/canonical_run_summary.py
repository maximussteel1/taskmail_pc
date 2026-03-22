"""Per-run canonical summary export for Phase 4 parity checks."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .models import RunResult, ThreadState
from .session_action_closeout import ACTION_TYPE_HEADER, target_session_identity_from_headers
from .workspace import WorkspaceManager

_RAW_MAIL_RE = re.compile(r"^raw_(?P<index>\d+)\.json$")
_DIRECT_HEADER = "X-TaskMail-Direct"
_REQUEST_ID_HEADER = "X-TaskMail-Relay-Request-Id"
_PACKET_ID_HEADER = "X-TaskMail-Relay-Packet-Id"
_SUMMARY_FILENAME = "canonical_summary.json"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _raw_index(raw_path: Path) -> int:
    match = _RAW_MAIL_RE.match(raw_path.name)
    if not match:
        raise ValueError(f"Unsupported raw mail filename: {raw_path.name}")
    return int(match.group("index"))


def _normalized_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iter_user_mail_payloads(task_root: str | Path, thread_id: str) -> list[dict[str, Any]]:
    workspace = WorkspaceManager(task_root)
    mail_dir = workspace.mail_dir(thread_id)
    if not mail_dir.exists():
        return []

    payloads: list[dict[str, Any]] = []
    for raw_path in sorted(mail_dir.glob("raw_*.json"), key=_raw_index):
        payload = workspace.load_json(raw_path)
        raw_headers = payload.get("raw_headers") or {}
        if not isinstance(raw_headers, dict):
            raw_headers = {}
        if str(raw_headers.get(SYSTEM_MESSAGE_HEADER) or "").strip() == SYSTEM_MESSAGE_HEADER_VALUE:
            continue
        payloads.append(payload)
    return payloads


def _select_ingress_payload(task_root: str | Path, state: ThreadState) -> dict[str, Any] | None:
    payloads = _iter_user_mail_payloads(task_root, state.thread_id)
    if not payloads:
        return None
    return payloads[-1]


def build_run_canonical_summary(
    task_root: str | Path,
    state: ThreadState,
    result: RunResult,
    *,
    terminal_mail_message_id: str | None,
    terminal_mail_subject: str | None,
) -> dict[str, Any]:
    ingress_payload = _select_ingress_payload(task_root, state)
    ingress_headers = ingress_payload.get("raw_headers") if isinstance(ingress_payload, dict) else {}
    if not isinstance(ingress_headers, dict):
        ingress_headers = {}

    request_id = str(ingress_headers.get(_REQUEST_ID_HEADER) or "").strip() or None
    packet_id = str(ingress_headers.get(_PACKET_ID_HEADER) or "").strip() or None
    is_direct_bridge = str(ingress_headers.get(_DIRECT_HEADER) or "").strip() == "1"
    ingress_message_id = None
    if isinstance(ingress_payload, dict):
        ingress_message_id = str(ingress_payload.get("message_id") or "").strip() or None
    action_type = _normalized_text(ingress_headers.get(ACTION_TYPE_HEADER))
    target_session_identity = target_session_identity_from_headers(ingress_headers)

    return {
        "version": 1,
        "thread_id": state.thread_id,
        "task_id": result.task_id,
        "run_status": result.status,
        "ingress_type": "direct_bridge" if is_direct_bridge else "mail",
        "ingress_message_id": ingress_message_id,
        "request_id": request_id,
        "packet_id": packet_id,
        "action_type": action_type,
        "target_session_identity": target_session_identity,
        "last_summary": state.last_summary,
        "terminal_mail_message_id": terminal_mail_message_id,
        "terminal_mail_subject": terminal_mail_subject,
        "generated_at": _timestamp(),
    }


def write_run_canonical_summary(
    task_root: str | Path,
    state: ThreadState,
    result: RunResult,
    *,
    terminal_mail_message_id: str | None,
    terminal_mail_subject: str | None,
) -> Path:
    workspace = WorkspaceManager(task_root)
    payload = build_run_canonical_summary(
        task_root,
        state,
        result,
        terminal_mail_message_id=terminal_mail_message_id,
        terminal_mail_subject=terminal_mail_subject,
    )
    return workspace.save_json(
        workspace.run_file_path(state.thread_id, result.task_id, _SUMMARY_FILENAME),
        payload,
    )
