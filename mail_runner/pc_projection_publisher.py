"""Pure builders for PC -> relay projection batches."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .download_ref import normalize_download_ref

PROJECTION_BATCH_MESSAGE_TYPE = "projection_batch"
PROJECTION_BATCH_SCHEMA_VERSION = "taskmail-pc-projection-batch-v1"

_SESSION_SCOPE = "session"
_PROBE_SCOPE = "probe"
_TERMINAL_STATUSES = {"done", "failed", "killed"}
_ROUND_STATUSES = {"queued", "running", "waiting_user", "paused", "done", "failed", "killed"}
_SESSION_STATUSES = {"queued", "running", "waiting_user", "paused", "done", "failed", "killed", "archived"}


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _read(source: Any, field_name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(field_name, default)
    if hasattr(source, field_name):
        return getattr(source, field_name)
    return default


def _text(value: Any, field_name: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    raise ValueError(f"{field_name} must be a non-empty string")


def _opt_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _opt_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(value[k]) for k in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value


def _digest(payload: Any, *, prefix: str) -> str:
    rendered = json.dumps(_canonicalize(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}:{hashlib.sha256(rendered.encode('utf-8')).hexdigest()[:24]}"


def _session_status_from_thread(thread_status: str, *, queued_task_id: str | None) -> str:
    if thread_status == "accepted":
        return "queued"
    if thread_status == "awaiting_user_input":
        return "waiting_user"
    if queued_task_id and thread_status not in {"running", "awaiting_user_input"}:
        return "queued"
    return thread_status


def _wire_snapshot_status(session_status: str, thread_status: str, *, queued_task_id: str | None) -> str:
    if session_status == "archived":
        raise ValueError("session_status='archived' is out of scope for projection batches")
    if thread_status == "accepted" or session_status == "queued":
        return "queued"
    if thread_status == "awaiting_user_input" or session_status == "waiting_user":
        return "awaiting_user_input"
    if thread_status in {"running", "paused", "done", "failed", "killed"}:
        return thread_status
    if session_status in _ROUND_STATUSES:
        return "awaiting_user_input" if session_status == "waiting_user" else session_status
    if queued_task_id and thread_status not in {"running", "awaiting_user_input"}:
        return "queued"
    raise ValueError("unable to normalize session and thread status into a snapshot status")


def _backend_label(backend: Any) -> str:
    normalized = _text(backend, "backend")
    return "OpenCode" if normalized.lower() == "opencode" else normalized[:1].upper() + normalized[1:]


def _question_state(session_state: Any, thread_state: Any, current_round: Any | None) -> dict[str, Any] | None:
    questions = _read(thread_state, "pending_questions") or _read(current_round, "pending_questions")
    if questions:
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(list(questions), start=1):
            question_id = _opt_text(_read(item, "question_id")) or f"question_{index}"
            question_text = _opt_text(_read(item, "question_text")) or ""
            normalized.append(
                {
                    "question_id": question_id,
                    "question_text": question_text,
                    "question_type": _opt_text(_read(item, "question_type")) or "short_text",
                    "required": bool(_read(item, "required", True)),
                    "choices": list(_read(item, "choices", []) or []),
                    "choice_labels": dict(_read(item, "choice_labels", {}) or {}),
                }
            )
        return {
            "question_set_id": _opt_text(_read(questions[0], "question_set_id")) or normalized[0]["question_id"],
            "question_count": len(normalized),
            "questions": normalized,
        }

    question_text = _opt_text(_read(thread_state, "pending_question_text")) or _opt_text(_read(current_round, "question_text"))
    if not question_text:
        return None
    question_id = (
        _opt_text(_read(thread_state, "pending_question_id"))
        or _opt_text(_read(current_round, "question_id"))
        or f"question_{_text(_read(session_state, 'current_task_id') or _read(current_round, 'task_id'), 'current_task_id')}"
    )
    choices = list(_read(thread_state, "pending_choices", []) or _read(current_round, "pending_choices", []) or [])
    return {
        "question_set_id": _opt_text(_read(thread_state, "pending_question_set_id")) or question_id,
        "question_count": 1,
        "questions": [
            {
                "question_id": question_id,
                "question_text": question_text,
                "question_type": "single_choice" if choices else "short_text",
                "required": True,
                "choices": choices,
                "choice_labels": {},
            }
        ],
    }


def _timeline_items(
    *,
    session_state: Any,
    thread_state: Any,
    snapshot_status: str,
    question_state: dict[str, Any] | None,
    emitted_at: str,
) -> list[dict[str, Any]]:
    last_summary = _opt_text(_read(session_state, "last_summary")) or _opt_text(_read(thread_state, "last_summary"))
    event_at = (
        _opt_text(_read(session_state, "last_progress_at"))
        or _opt_text(_read(thread_state, "last_progress_at"))
        or _opt_text(_read(session_state, "last_active_at"))
        or _opt_text(_read(thread_state, "last_active_at"))
        or emitted_at
    )
    if snapshot_status == "awaiting_user_input" and question_state is not None:
        questions = question_state["questions"]
        text = questions[0]["question_text"] if len(questions) == 1 else f"Need {len(questions)} answers before continuing."
        return [
            {
                "item_id": f"tl_question_{question_state['question_set_id']}_{event_at}",
                "business_event_key": f"question/{question_state['question_set_id']}/{event_at}",
                "item_type": "question_prompt",
                "created_at": event_at,
                "status": None,
                "text": text,
                "question_set_id": question_state["question_set_id"],
                "question_ids": [item["question_id"] for item in questions],
                "paused_from_status": None,
            }
        ]
    if snapshot_status == "paused" and last_summary:
        paused_from_status = _opt_text(_read(thread_state, "paused_from_status"))
        return [
            {
                "item_id": f"tl_paused_{paused_from_status or 'unknown'}_{event_at}",
                "business_event_key": f"paused/{paused_from_status or 'unknown'}/{event_at}",
                "item_type": "paused_hint",
                "created_at": event_at,
                "status": None,
                "text": last_summary,
                "question_set_id": question_state["question_set_id"] if question_state else None,
                "question_ids": [item["question_id"] for item in question_state["questions"]] if question_state else [],
                "paused_from_status": paused_from_status,
            }
        ]
    if snapshot_status in _TERMINAL_STATUSES and last_summary:
        return [
            {
                "item_id": f"tl_terminal_{snapshot_status}_{event_at}",
                "business_event_key": f"terminal/{snapshot_status}/{event_at}",
                "item_type": "terminal_summary",
                "created_at": event_at,
                "status": snapshot_status,
                "text": last_summary,
                "question_set_id": None,
                "question_ids": [],
                "paused_from_status": None,
            }
        ]
    return []


def _session_projection(
    *,
    session_state: Any,
    thread_state: Any,
    emitted_at: str,
    question_state: dict[str, Any] | None = None,
    timeline_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    session_status = _opt_text(_read(session_state, "status"))
    if not session_status:
        session_status = _session_status_from_thread(
            _text(_read(thread_state, "status"), "thread_state.status"),
            queued_task_id=_opt_text(_read(session_state, "queued_task_id")) or _opt_text(_read(thread_state, "queued_task_id")),
        )
    thread_status = _opt_text(_read(thread_state, "status")) or "running"
    snapshot_status = _wire_snapshot_status(
        session_status,
        thread_status,
        queued_task_id=_opt_text(_read(session_state, "queued_task_id")) or _opt_text(_read(thread_state, "queued_task_id")),
    )
    projected_question_state = question_state if question_state is not None else _question_state(session_state, thread_state, None)
    projected_timeline_items = (
        timeline_items
        if timeline_items is not None
        else _timeline_items(
            session_state=session_state,
            thread_state=thread_state,
            snapshot_status=snapshot_status,
            question_state=projected_question_state,
            emitted_at=emitted_at,
        )
    )
    return {
        "session_name": _opt_text(_read(session_state, "session_name"))
        or _opt_text(_read(thread_state, "session_name"))
        or _opt_text(_read(thread_state, "subject_norm")),
        "backend": _text(_read(session_state, "backend") or _read(thread_state, "backend"), "backend"),
        "repo_path": _text(_read(session_state, "repo_path") or _read(thread_state, "repo_path"), "repo_path"),
        "workdir": _opt_text(_read(session_state, "workdir")) or _opt_text(_read(thread_state, "workdir")),
        "status": snapshot_status,
        "lifecycle": _opt_text(_read(session_state, "lifecycle")) or _opt_text(_read(thread_state, "lifecycle")) or "active",
        "last_summary": _opt_text(_read(session_state, "last_summary"))
        or _opt_text(_read(thread_state, "last_summary"))
        or snapshot_status.replace("_", " ").title() + ".",
        "last_active_at": _opt_text(_read(session_state, "last_active_at"))
        or _opt_text(_read(thread_state, "last_active_at"))
        or emitted_at,
        "last_progress_at": _opt_text(_read(session_state, "last_progress_at"))
        or _opt_text(_read(thread_state, "last_progress_at"))
        or emitted_at,
        "paused_from_status": _opt_text(_read(thread_state, "paused_from_status")),
        "question_state": projected_question_state,
        "timeline_items": projected_timeline_items,
    }


def _attachment_payload(attachment: Any) -> dict[str, Any]:
    content_type = _opt_text(_read(attachment, "content_type")) or "application/octet-stream"
    return {
        "attachment_id": _text(_read(attachment, "attachment_id"), "attachment_id"),
        "display_name": _text(_read(attachment, "display_name") or _read(attachment, "name") or _read(attachment, "filename"), "display_name"),
        "content_type": content_type,
        "size_bytes": _opt_int(_read(attachment, "size_bytes") or _read(attachment, "size"), "size_bytes", minimum=0) or 0,
        "is_image": _opt_bool(_read(attachment, "is_image"), "is_image")
        if _read(attachment, "is_image") is not None
        else content_type.startswith("image/"),
    }


def _artifact_ref_payload(ref: Any) -> dict[str, Any]:
    content_type = _opt_text(_read(ref, "content_type")) or "application/octet-stream"
    return {
        "artifact_id": _text(_read(ref, "artifact_id"), "artifact_id"),
        "display_name": _text(_read(ref, "display_name") or _read(ref, "name"), "display_name"),
        "content_type": content_type,
        "size_bytes": _opt_int(_read(ref, "size_bytes") or _read(ref, "size"), "size_bytes", minimum=0),
        "is_image": _opt_bool(_read(ref, "is_image"), "is_image")
        if _read(ref, "is_image") is not None
        else content_type.startswith("image/"),
        "file_id": _opt_text(_read(ref, "file_id")),
        "download_ref": normalize_download_ref(_read(ref, "download_ref"), field_name="artifact_ref.download_ref"),
        "download_ref_source": _opt_text(_read(ref, "download_ref_source")),
        "provider": _opt_text(_read(ref, "provider")),
        "expires_at": _opt_text(_read(ref, "expires_at")),
    }


def _target_session_identity_payload(identity: Any) -> dict[str, Any] | None:
    if identity is None:
        return None
    payload = {
        "workspace_id": _opt_text(_read(identity, "workspace_id")),
        "session_id": _opt_text(_read(identity, "session_id")),
        "thread_id": _opt_text(_read(identity, "thread_id")),
    }
    payload = {key: value for key, value in payload.items() if value}
    return payload or None


def build_session_projection_upsert(
    *,
    pc_id: str,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    projection_version: int,
    session_name: str,
    backend: str,
    backend_transport: str | None,
    profile: str | None,
    permission: str | None,
    repo_path: str,
    workdir: str | None,
    list_status: str,
    snapshot_status: str,
    lifecycle: str,
    current_task_id: str | None,
    queued_task_id: str | None,
    pending_task_count: int,
    last_summary: str | None,
    last_active_at: str | None,
    last_progress_at: str | None,
    paused_from_status: str | None,
    backend_session_id: str | None,
    backend_session_resumable: bool,
    question_state: dict[str, Any] | None,
    timeline_items: list[dict[str, Any]] | None,
    created_at: str,
    updated_at: str,
    source_updated_at: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "type": "session_projection_upsert",
        "pc_id": _text(pc_id, "pc_id"),
        "workspace_id": _text(workspace_id, "workspace_id"),
        "session_id": _text(session_id, "session_id"),
        "thread_id": _text(thread_id, "thread_id"),
        "projection_version": _opt_int(projection_version, "projection_version", minimum=1),
        "session_name": _text(session_name, "session_name"),
        "backend": _text(backend, "backend"),
        "backend_transport": _opt_text(backend_transport),
        "profile": _opt_text(profile),
        "permission": _opt_text(permission),
        "repo_path": _text(repo_path, "repo_path"),
        "workdir": _opt_text(workdir),
        "list_status": _text(list_status, "list_status"),
        "snapshot_status": _text(snapshot_status, "snapshot_status"),
        "lifecycle": _text(lifecycle, "lifecycle"),
        "current_task_id": _opt_text(current_task_id),
        "queued_task_id": _opt_text(queued_task_id),
        "pending_task_count": _opt_int(pending_task_count, "pending_task_count", minimum=0),
        "last_summary": _opt_text(last_summary),
        "last_active_at": _opt_text(last_active_at),
        "last_progress_at": _opt_text(last_progress_at),
        "paused_from_status": _opt_text(paused_from_status),
        "backend_session_id": _opt_text(backend_session_id),
        "backend_session_resumable": bool(backend_session_resumable),
        "question_state": question_state,
        "timeline_items": list(timeline_items or []),
        "created_at": _text(created_at, "created_at"),
        "updated_at": _text(updated_at, "updated_at"),
        "source_updated_at": _opt_text(source_updated_at),
    }
    payload["idempotency_key"] = idempotency_key or _digest({key: value for key, value in payload.items() if key != "idempotency_key"}, prefix="sess_head")
    return payload


def build_session_round_upsert(
    *,
    pc_id: str,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    projection_version: int,
    task_id: str,
    round_sort_at: str,
    status: str,
    speaker_label: str,
    input_text: str | None = None,
    input_attachments: Sequence[Any] = (),
    process_items: Sequence[Any] = (),
    result_text: str | None = None,
    result_attachments: Sequence[Any] = (),
    artifact_refs: Sequence[Any] = (),
    source_updated_at: str | None = None,
    round_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "type": "session_round_upsert",
        "pc_id": _text(pc_id, "pc_id"),
        "workspace_id": _text(workspace_id, "workspace_id"),
        "session_id": _text(session_id, "session_id"),
        "thread_id": _text(thread_id, "thread_id"),
        "projection_version": _opt_int(projection_version, "projection_version", minimum=1),
        "task_id": _text(task_id, "task_id"),
        "round_id": _opt_text(round_id) or f"hist_round_{_text(task_id, 'task_id')}",
        "round_sort_at": _text(round_sort_at, "round_sort_at"),
        "status": _text(status, "status"),
        "speaker_label": _text(speaker_label, "speaker_label"),
        "input_text": _opt_text(input_text),
        "input_attachments": [_attachment_payload(item) for item in list(input_attachments)],
        "process_items": [dict(item) for item in list(process_items)],
        "result_text": _opt_text(result_text),
        "result_attachments": [_attachment_payload(item) for item in list(result_attachments)],
        "artifact_refs": [_artifact_ref_payload(item) for item in list(artifact_refs)],
        "source_updated_at": _opt_text(source_updated_at),
    }
    payload["idempotency_key"] = idempotency_key or _digest({key: value for key, value in payload.items() if key != "idempotency_key"}, prefix="sess_round")
    return payload


def build_session_closeout_upsert(
    *,
    pc_id: str,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    projection_version: int,
    closeout_key: str,
    task_id: str | None = None,
    request_id: str | None = None,
    packet_id: str | None = None,
    receipt_id: str | None = None,
    action_type: str | None = None,
    target_session_identity: Any | None = None,
    last_summary: str | None = None,
    terminal_mail_message_id: str | None = None,
    terminal_mail_subject: str | None = None,
    generated_at: str | None = None,
    source_updated_at: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    generated_at_text = _opt_text(generated_at) or _timestamp()
    payload = {
        "type": "session_closeout_upsert",
        "pc_id": _text(pc_id, "pc_id"),
        "workspace_id": _text(workspace_id, "workspace_id"),
        "session_id": _text(session_id, "session_id"),
        "thread_id": _text(thread_id, "thread_id"),
        "projection_version": _opt_int(projection_version, "projection_version", minimum=1),
        "closeout_key": _text(closeout_key, "closeout_key"),
        "task_id": _opt_text(task_id),
        "request_id": _opt_text(request_id),
        "packet_id": _opt_text(packet_id),
        "receipt_id": _opt_text(receipt_id),
        "action_type": _opt_text(action_type),
        "target_session_identity": _target_session_identity_payload(target_session_identity),
        "last_summary": _opt_text(last_summary),
        "terminal_mail_message_id": _opt_text(terminal_mail_message_id),
        "terminal_mail_subject": _opt_text(terminal_mail_subject),
        "generated_at": generated_at_text,
        "source_updated_at": _opt_text(source_updated_at),
    }
    payload["idempotency_key"] = idempotency_key or _digest({key: value for key, value in payload.items() if key != "idempotency_key"}, prefix="sess_closeout")
    return payload


def build_transport_probe_observation_upsert(
    *,
    pc_id: str,
    connection_epoch: int,
    probe_id: str,
    request_id: str | None,
    packet_id: str | None,
    receipt_id: str | None = None,
    mailbox_message_id: str | None = None,
    summary_text: str,
    observation_status: str,
    observed_at: str,
    payload: Mapping[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload_dict = {
        "type": "transport_probe_observation_upsert",
        "pc_id": _text(pc_id, "pc_id"),
        "connection_epoch": _opt_int(connection_epoch, "connection_epoch", minimum=1),
        "probe_id": _text(probe_id, "probe_id"),
        "request_id": _opt_text(request_id),
        "packet_id": _opt_text(packet_id),
        "receipt_id": _opt_text(receipt_id),
        "mailbox_message_id": _opt_text(mailbox_message_id),
        "summary_text": _text(summary_text, "summary_text"),
        "observation_status": _text(observation_status, "observation_status"),
        "observed_at": _text(observed_at, "observed_at"),
        "payload": dict(payload),
    }
    payload_dict["idempotency_key"] = idempotency_key or _digest(
        {key: value for key, value in payload_dict.items() if key != "idempotency_key"},
        prefix="probe_obs",
    )
    return payload_dict


def build_projection_batch(
    *,
    scope: str,
    pc_id: str,
    connection_epoch: int,
    items: Sequence[Mapping[str, Any]],
    sent_at: str | None = None,
    workspace_id: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    projection_version: int | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    normalized_scope = _text(scope, "scope")
    if normalized_scope not in {_SESSION_SCOPE, _PROBE_SCOPE}:
        raise ValueError("scope must be either 'session' or 'probe'")
    sent_at_text = _opt_text(sent_at) or _timestamp()
    envelope: dict[str, Any] = {
        "message_type": PROJECTION_BATCH_MESSAGE_TYPE,
        "schema_version": PROJECTION_BATCH_SCHEMA_VERSION,
        "pc_id": _text(pc_id, "pc_id"),
        "connection_epoch": _opt_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": sent_at_text,
        "scope": normalized_scope,
        "items": [dict(item) for item in list(items)],
    }
    if normalized_scope == _SESSION_SCOPE:
        envelope["workspace_id"] = _text(workspace_id, "workspace_id")
        envelope["session_id"] = _text(session_id, "session_id")
        envelope["thread_id"] = _text(thread_id, "thread_id")
        envelope["projection_version"] = _opt_int(projection_version, "projection_version", minimum=1)
    envelope["batch_id"] = batch_id or _digest({key: value for key, value in envelope.items() if key not in {"batch_id", "sent_at"}}, prefix="projection_batch")
    return envelope


def build_session_projection_batch(
    *,
    pc_id: str,
    connection_epoch: int,
    session_state: Any,
    thread_state: Any | None = None,
    projection_version: int,
    current_round: Mapping[str, Any] | Any | None = None,
    input_attachments: Sequence[Any] = (),
    result_attachments: Sequence[Any] = (),
    artifact_refs: Sequence[Any] = (),
    closeouts: Sequence[Mapping[str, Any] | Any] = (),
    include_session_projection: bool = True,
    include_round: bool = True,
    question_state: dict[str, Any] | None = None,
    timeline_items: list[dict[str, Any]] | None = None,
    sent_at: str | None = None,
    source_updated_at: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    session_source = session_state
    thread_source = thread_state if thread_state is not None else session_state
    session_pc_id = _opt_text(_read(session_source, "pc_id")) or _opt_text(pc_id)
    if not session_pc_id:
        raise ValueError("pc_id must be provided")
    workspace_id = _opt_text(_read(session_source, "workspace_id")) or _opt_text(_read(thread_source, "workspace_id"))
    session_id = _opt_text(_read(session_source, "session_id")) or _opt_text(_read(thread_source, "session_id"))
    thread_id = _opt_text(_read(session_source, "thread_id")) or _opt_text(_read(thread_source, "thread_id"))
    if not workspace_id or not session_id or not thread_id:
        raise ValueError("session_state or thread_state must provide workspace_id, session_id, and thread_id")

    sent_at_text = _opt_text(sent_at) or _timestamp()
    items: list[dict[str, Any]] = []
    projected_snapshot = None
    if include_session_projection:
        projected_snapshot = _session_projection(
            session_state=session_source,
            thread_state=thread_source,
            emitted_at=sent_at_text,
            question_state=question_state,
            timeline_items=timeline_items,
        )
        items.append(
            build_session_projection_upsert(
                pc_id=session_pc_id,
                workspace_id=workspace_id,
                session_id=session_id,
                thread_id=thread_id,
                projection_version=projection_version,
                session_name=projected_snapshot["session_name"],
                backend=projected_snapshot["backend"],
                backend_transport=_opt_text(_read(session_source, "backend_transport")) or _opt_text(_read(thread_source, "backend_transport")),
                profile=_opt_text(_read(session_source, "profile")) or _opt_text(_read(thread_source, "profile")),
                permission=_opt_text(_read(session_source, "permission")) or _opt_text(_read(thread_source, "permission")),
                repo_path=projected_snapshot["repo_path"],
                workdir=projected_snapshot["workdir"],
                list_status=_session_status_from_thread(
                    _text(_read(session_source, "status") or _read(thread_source, "status"), "status"),
                    queued_task_id=_opt_text(_read(session_source, "queued_task_id")) or _opt_text(_read(thread_source, "queued_task_id")),
                ),
                snapshot_status=projected_snapshot["status"],
                lifecycle=projected_snapshot["lifecycle"],
                current_task_id=_opt_text(_read(session_source, "current_task_id")) or _opt_text(_read(thread_source, "current_task_id")),
                queued_task_id=_opt_text(_read(session_source, "queued_task_id")) or _opt_text(_read(thread_source, "queued_task_id")),
                pending_task_count=_opt_int(_read(session_source, "pending_task_count"), "pending_task_count", minimum=0)
                if _read(session_source, "pending_task_count") is not None
                else (1 if (_opt_text(_read(session_source, "queued_task_id")) or _opt_text(_read(thread_source, "queued_task_id"))) else 0),
                last_summary=projected_snapshot["last_summary"],
                last_active_at=projected_snapshot["last_active_at"],
                last_progress_at=projected_snapshot["last_progress_at"],
                paused_from_status=projected_snapshot["paused_from_status"],
                backend_session_id=_opt_text(_read(session_source, "backend_session_id")) or _opt_text(_read(thread_source, "backend_session_id")),
                backend_session_resumable=bool(_read(session_source, "backend_session_resumable", _read(thread_source, "backend_session_resumable", False))),
                question_state=projected_snapshot["question_state"],
                timeline_items=projected_snapshot["timeline_items"],
                created_at=_opt_text(_read(session_source, "created_at")) or _opt_text(_read(thread_source, "created_at")) or sent_at_text,
                updated_at=_opt_text(_read(session_source, "updated_at")) or _opt_text(_read(thread_source, "updated_at")) or sent_at_text,
                source_updated_at=source_updated_at
                or _opt_text(_read(session_source, "updated_at"))
                or _opt_text(_read(thread_source, "updated_at"))
                or sent_at_text,
            )
        )

    if include_round and current_round is not None:
        task_id = _opt_text(_read(current_round, "task_id"))
        if not task_id:
            raise ValueError("current_round must provide task_id when include_round is true")
        round_status = _opt_text(_read(current_round, "status"))
        if not round_status:
            raise ValueError("current_round must provide status when include_round is true")
        round_sort_at = (
            _opt_text(_read(current_round, "round_sort_at"))
            or _opt_text(_read(current_round, "finished_at"))
            or _opt_text(_read(current_round, "started_at"))
            or _opt_text(_read(current_round, "created_at"))
            or sent_at_text
        )
        speaker_label = _opt_text(_read(current_round, "speaker_label")) or _backend_label(projected_snapshot["backend"] if projected_snapshot else _read(session_source, "backend"))
        items.append(
            build_session_round_upsert(
                pc_id=session_pc_id,
                workspace_id=workspace_id,
                session_id=session_id,
                thread_id=thread_id,
                projection_version=projection_version,
                task_id=task_id,
                round_sort_at=round_sort_at,
                status=round_status,
                speaker_label=speaker_label,
                input_text=_opt_text(_read(current_round, "input_text")),
                input_attachments=_read(current_round, "input_attachments", input_attachments) or input_attachments,
                process_items=_read(current_round, "process_items", []) or [],
                result_text=_opt_text(_read(current_round, "result_text")),
                result_attachments=_read(current_round, "result_attachments", result_attachments) or result_attachments,
                artifact_refs=_read(current_round, "artifact_refs", artifact_refs) or artifact_refs,
                source_updated_at=_opt_text(_read(current_round, "source_updated_at")) or source_updated_at,
                round_id=_opt_text(_read(current_round, "round_id")),
            )
        )

    for closeout in closeouts:
        items.append(
            build_session_closeout_upsert(
                pc_id=session_pc_id,
                workspace_id=workspace_id,
                session_id=session_id,
                thread_id=thread_id,
                projection_version=projection_version,
                closeout_key=_text(_read(closeout, "closeout_key"), "closeout_key"),
                task_id=_opt_text(_read(closeout, "task_id")),
                request_id=_opt_text(_read(closeout, "request_id")),
                packet_id=_opt_text(_read(closeout, "packet_id")),
                receipt_id=_opt_text(_read(closeout, "receipt_id")),
                action_type=_opt_text(_read(closeout, "action_type")),
                target_session_identity=_read(closeout, "target_session_identity"),
                last_summary=_opt_text(_read(closeout, "last_summary")),
                terminal_mail_message_id=_opt_text(_read(closeout, "terminal_mail_message_id")),
                terminal_mail_subject=_opt_text(_read(closeout, "terminal_mail_subject")),
                generated_at=_opt_text(_read(closeout, "generated_at")) or sent_at_text,
                source_updated_at=_opt_text(_read(closeout, "source_updated_at")) or source_updated_at,
            )
        )

    return build_projection_batch(
        scope=_SESSION_SCOPE,
        pc_id=session_pc_id,
        connection_epoch=connection_epoch,
        sent_at=sent_at_text,
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        projection_version=projection_version,
        items=items,
        batch_id=batch_id,
    )


def build_metadata_only_closeout_batch(
    *,
    pc_id: str,
    connection_epoch: int,
    session_state: Any,
    thread_state: Any | None = None,
    projection_version: int,
    closeouts: Sequence[Mapping[str, Any] | Any],
    sent_at: str | None = None,
    source_updated_at: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    return build_session_projection_batch(
        pc_id=pc_id,
        connection_epoch=connection_epoch,
        session_state=session_state,
        thread_state=thread_state,
        projection_version=projection_version,
        closeouts=closeouts,
        include_session_projection=False,
        include_round=False,
        sent_at=sent_at,
        source_updated_at=source_updated_at,
        batch_id=batch_id,
    )


def build_transport_probe_batch(
    *,
    pc_id: str,
    connection_epoch: int,
    observation: Mapping[str, Any] | Any,
    sent_at: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    sent_at_text = _opt_text(sent_at) or _timestamp()
    payload = build_transport_probe_observation_upsert(
        pc_id=pc_id,
        connection_epoch=connection_epoch,
        probe_id=_text(_read(observation, "probe_id"), "probe_id"),
        request_id=_opt_text(_read(observation, "request_id")),
        packet_id=_opt_text(_read(observation, "packet_id")),
        receipt_id=_opt_text(_read(observation, "receipt_id")),
        mailbox_message_id=_opt_text(_read(observation, "mailbox_message_id")),
        summary_text=_text(_read(observation, "summary_text"), "summary_text"),
        observation_status=_text(_read(observation, "observation_status"), "observation_status"),
        observed_at=_opt_text(_read(observation, "observed_at")) or sent_at_text,
        payload=dict(_read(observation, "payload", {}) or {}),
    )
    return build_projection_batch(
        scope=_PROBE_SCOPE,
        pc_id=pc_id,
        connection_epoch=connection_epoch,
        sent_at=sent_at_text,
        items=[payload],
        batch_id=batch_id,
    )


__all__ = [
    "PROJECTION_BATCH_MESSAGE_TYPE",
    "PROJECTION_BATCH_SCHEMA_VERSION",
    "build_metadata_only_closeout_batch",
    "build_projection_batch",
    "build_session_closeout_upsert",
    "build_session_projection_batch",
    "build_session_projection_upsert",
    "build_session_round_upsert",
    "build_transport_probe_batch",
    "build_transport_probe_observation_upsert",
]
