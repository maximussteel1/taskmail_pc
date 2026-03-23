"""Post-creation session-action closeout helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .workspace import WorkspaceManager

SESSION_ACTION_CLOSEOUT_FILENAME = "session_action_closeout.json"
ACTION_TYPE_HEADER = "X-TaskMail-Action-Type"
TARGET_WORKSPACE_ID_HEADER = "X-TaskMail-Target-Workspace-Id"
TARGET_SESSION_ID_HEADER = "X-TaskMail-Target-Session-Id"
TARGET_THREAD_ID_HEADER = "X-TaskMail-Target-Thread-Id"
RECEIPT_ID_HEADER = "X-TaskMail-Relay-Receipt-Id"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalized_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_target_session_identity(
    *,
    workspace_id: str | None,
    session_id: str | None,
    thread_id: str | None,
) -> dict[str, str] | None:
    payload = {
        "workspace_id": _normalized_text(workspace_id),
        "session_id": _normalized_text(session_id),
        "thread_id": _normalized_text(thread_id),
    }
    if all(value is None for value in payload.values()):
        return None
    return {key: value for key, value in payload.items() if value is not None}


def target_session_identity_from_headers(raw_headers: Any) -> dict[str, str] | None:
    if not isinstance(raw_headers, dict):
        return None
    return build_target_session_identity(
        workspace_id=raw_headers.get(TARGET_WORKSPACE_ID_HEADER),
        session_id=raw_headers.get(TARGET_SESSION_ID_HEADER),
        thread_id=raw_headers.get(TARGET_THREAD_ID_HEADER),
    )


def build_session_action_closeout(
    *,
    thread_id: str,
    action_type: str,
    request_id: str,
    ingress_message_id: str | None,
    packet_id: str | None,
    receipt_id: str | None,
    terminal_mail_subject: str | None,
    last_summary: str | None,
    target_session_identity: dict[str, str] | None,
    terminal_mail_message_id: str | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "thread_id": _normalized_text(thread_id),
        "action_type": _normalized_text(action_type),
        "target_session_identity": target_session_identity or None,
        "ingress_type": "direct_bridge",
        "request_id": _normalized_text(request_id),
        "ingress_message_id": _normalized_text(ingress_message_id),
        "packet_id": _normalized_text(packet_id),
        "receipt_id": _normalized_text(receipt_id),
        "last_summary": _normalized_text(last_summary),
        "terminal_mail_message_id": _normalized_text(terminal_mail_message_id),
        "terminal_mail_subject": _normalized_text(terminal_mail_subject),
        "generated_at": _timestamp(),
    }


def load_session_action_closeout(
    task_root: str | Path,
    *,
    thread_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    workspace = WorkspaceManager(task_root)
    path = workspace.session_action_file_path(
        thread_id,
        request_id,
        SESSION_ACTION_CLOSEOUT_FILENAME,
    )
    if not path.exists():
        return None
    payload = workspace.load_json(path)
    if not isinstance(payload, dict):
        return None
    return payload


def write_session_action_closeout(
    task_root: str | Path,
    *,
    thread_id: str,
    action_type: str,
    request_id: str,
    ingress_message_id: str | None,
    packet_id: str | None,
    receipt_id: str | None,
    terminal_mail_subject: str | None,
    last_summary: str | None,
    target_session_identity: dict[str, str] | None,
    terminal_mail_message_id: str | None = None,
) -> Path:
    workspace = WorkspaceManager(task_root)
    payload = build_session_action_closeout(
        thread_id=thread_id,
        action_type=action_type,
        request_id=request_id,
        ingress_message_id=ingress_message_id,
        packet_id=packet_id,
        receipt_id=receipt_id,
        terminal_mail_subject=terminal_mail_subject,
        last_summary=last_summary,
        target_session_identity=target_session_identity,
        terminal_mail_message_id=terminal_mail_message_id,
    )
    return workspace.save_json(
        workspace.session_action_file_path(
            thread_id,
            request_id,
            SESSION_ACTION_CLOSEOUT_FILENAME,
        ),
        payload,
    )


def upsert_session_action_closeout(
    task_root: str | Path,
    *,
    thread_id: str,
    action_type: str,
    request_id: str,
    ingress_message_id: str | None,
    packet_id: str | None,
    receipt_id: str | None,
    terminal_mail_subject: str | None,
    last_summary: str | None,
    target_session_identity: dict[str, str] | None,
    terminal_mail_message_id: str | None = None,
) -> Path:
    workspace = WorkspaceManager(task_root)
    payload = build_session_action_closeout(
        thread_id=thread_id,
        action_type=action_type,
        request_id=request_id,
        ingress_message_id=ingress_message_id,
        packet_id=packet_id,
        receipt_id=receipt_id,
        terminal_mail_subject=terminal_mail_subject,
        last_summary=last_summary,
        target_session_identity=target_session_identity,
        terminal_mail_message_id=terminal_mail_message_id,
    )
    existing = load_session_action_closeout(
        task_root,
        thread_id=thread_id,
        request_id=request_id,
    )
    if isinstance(existing, dict):
        merged = dict(existing)
        for key, value in payload.items():
            if key == "generated_at":
                merged[key] = value
                continue
            if key == "target_session_identity":
                if value is not None:
                    merged[key] = value
                continue
            if value is not None:
                merged[key] = value
        payload = merged
    return workspace.save_json(
        workspace.session_action_file_path(
            thread_id,
            request_id,
            SESSION_ACTION_CLOSEOUT_FILENAME,
        ),
        payload,
    )
