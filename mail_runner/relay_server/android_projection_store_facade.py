"""Android-facing read adapter backed by a relay projection store."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from ..thread_store import build_workspace_id, normalize_workspace_value
from .android_session_history_facade import ANDROID_SESSION_HISTORY_SCHEMA_VERSION
from .android_session_snapshot_facade import ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION
from .android_sessions_facade import ANDROID_SESSIONS_SCHEMA_VERSION, DEFAULT_REFRESH_AFTER_SECONDS

_INPUT_ATTACHMENT_SUMMARY_MARKER = "New incoming attachments materialized in workdir:"


class AndroidProjectionStore(Protocol):
    def list_sessions(self, pc_id: str | None = None) -> list[Any]: ...

    def list_history_rounds(self, *, session_key: str) -> list[Any]: ...

    def get_live_process(self, *, session_key: str) -> dict[str, Any] | None: ...


@dataclass(slots=True)
class AndroidProjectionStoreFacadeError(RuntimeError):
    status_code: int
    error_code: str
    error_message: str
    retryable: bool = False

    def to_response_payload(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retryable": self.retryable,
        }


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_query_text(query: Mapping[str, list[str]], field_name: str) -> str | None:
    values = query.get(field_name) or []
    if not values:
        return None
    return _normalize_text(values[0])


def _load_store_items(items: Any) -> list[dict[str, Any]]:
    if items is None:
        return []
    if isinstance(items, list):
        return [_record_to_dict(item) for item in items]
    return [_record_to_dict(item) for item in list(items)]


def _record_to_dict(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, dict):
        return dict(record)
    if is_dataclass(record):
        return asdict(record)
    if isinstance(record, Mapping):
        return dict(record)
    if hasattr(record, "__dict__"):
        return dict(vars(record))
    raise TypeError(f"Unsupported projection store record type: {type(record)!r}")


def _coerce_json_payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def _coerce_list_payload(value: Any) -> list[Any]:
    payload = _coerce_json_payload(value)
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, tuple):
        return list(payload)
    if isinstance(payload, dict):
        return [payload]
    return []


def _coerce_bool(value: Any) -> bool:
    return bool(value)


def _max_timestamp_text(first: str | None, second: str | None) -> str | None:
    normalized_first = _normalize_text(first)
    normalized_second = _normalize_text(second)
    if normalized_first is None:
        return normalized_second
    if normalized_second is None:
        return normalized_first
    return normalized_first if normalized_first >= normalized_second else normalized_second


def _session_session_key(record: Mapping[str, Any]) -> str | None:
    session_key = _normalize_text(record.get("session_key"))
    if session_key is not None:
        return session_key
    pc_id = _normalize_text(record.get("pc_id")) or "pc"
    workspace_id = _normalize_text(record.get("workspace_id")) or "workspace"
    session_id = _normalize_text(record.get("session_id")) or _normalize_text(record.get("thread_id")) or "session"
    thread_id = _normalize_text(record.get("thread_id")) or session_id
    return f"{pc_id}::{workspace_id}::{session_id}::{thread_id}"


def _canonical_workspace_id(record: Mapping[str, Any]) -> str | None:
    workspace_id = _normalize_text(record.get("workspace_id"))
    if workspace_id is not None:
        return workspace_id
    repo_path = _normalize_text(record.get("repo_path"))
    workdir = _normalize_text(record.get("workdir"))
    if repo_path is None:
        return None
    try:
        return build_workspace_id(repo_path, workdir)
    except ValueError:
        return None


def _canonical_session_id(record: Mapping[str, Any]) -> str | None:
    return _normalize_text(record.get("session_id")) or _normalize_text(record.get("thread_id"))


def _canonical_locator(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pc_id": _normalize_text(record.get("pc_id")),
        "workspace_id": _canonical_workspace_id(record),
        "session_id": _canonical_session_id(record),
        "thread_id": _normalize_text(record.get("thread_id")),
    }


def _humanize_status(status: str | None) -> str:
    normalized = _normalize_text(status)
    if normalized is None:
        return "Unknown status."
    return normalized.replace("_", " ").strip().capitalize() + "."


def _session_status_for_list(record: Mapping[str, Any]) -> str:
    return (
        _normalize_text(record.get("list_status"))
        or _normalize_text(record.get("status"))
        or _normalize_text(record.get("lifecycle"))
        or "queued"
    )


def _session_status_for_snapshot(record: Mapping[str, Any]) -> str:
    return (
        _normalize_text(record.get("snapshot_status"))
        or _normalize_text(record.get("status"))
        or _session_status_for_list(record)
    )


def _sort_session_records(record: Mapping[str, Any]) -> tuple[str, str, str]:
    last_progress_at = _normalize_text(record.get("last_progress_at")) or _normalize_text(record.get("last_active_at"))
    updated_at = _normalize_text(record.get("updated_at"))
    session_id = _canonical_session_id(record) or ""
    return (
        last_progress_at or updated_at or "",
        updated_at or "",
        session_id,
    )


def _sort_round_records(record: Mapping[str, Any]) -> tuple[str, str]:
    sort_at = (
        _normalize_text(record.get("round_sort_at"))
        or _normalize_text(record.get("created_at"))
        or _normalize_text(record.get("source_updated_at"))
        or ""
    )
    task_id = _normalize_text(record.get("task_id")) or ""
    return sort_at, task_id


def _build_attachment_payload(record: Any, *, prefix: str, ordinal: int) -> dict[str, Any]:
    payload = _record_to_dict(record)
    attachment_id = (
        _normalize_text(payload.get("attachment_id"))
        or _normalize_text(payload.get("artifact_id"))
        or _normalize_text(payload.get("id"))
        or f"{prefix}_{ordinal}"
    )
    content_type = _normalize_text(payload.get("content_type")) or "application/octet-stream"
    display_name = _normalize_text(payload.get("display_name")) or _normalize_text(payload.get("name")) or attachment_id
    size_bytes = payload.get("size_bytes")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        size_bytes = None
    return {
        "attachment_id": attachment_id,
        "display_name": display_name,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "is_image": _coerce_bool(payload.get("is_image")) or content_type.startswith("image/"),
    }


def _build_attachment_list(value: Any, *, prefix: str) -> list[dict[str, Any]]:
    attachments = _coerce_list_payload(value)
    return [
        _build_attachment_payload(attachment, prefix=prefix, ordinal=index)
        for index, attachment in enumerate(attachments, start=1)
    ]


def _input_attachment_identity_key(record: Any) -> str:
    payload = _record_to_dict(record)
    source_path = _normalize_text(payload.get("source_path")) or _normalize_text(payload.get("path"))
    if source_path is not None:
        normalized_path = source_path.replace("\\", "/").lower()
        return f"path:{normalized_path}"
    content_type = _normalize_text(payload.get("content_type")) or "application/octet-stream"
    display_name = (
        _normalize_text(payload.get("display_name"))
        or _normalize_text(payload.get("name"))
        or _normalize_text(payload.get("filename"))
        or ""
    )
    size_bytes = payload.get("size_bytes")
    normalized_size = size_bytes if isinstance(size_bytes, int) and size_bytes >= 0 else None
    is_image = _coerce_bool(payload.get("is_image")) or content_type.startswith("image/")
    return json.dumps(
        {
            "display_name": display_name,
            "content_type": content_type,
            "size_bytes": normalized_size,
            "is_image": is_image,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_input_attachment_delta(
    value: Any,
    *,
    prefix: str,
    previous_keys: set[str],
    input_text: str | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    attachments = _coerce_list_payload(value)
    keyed = [
        (
            _input_attachment_identity_key(attachment),
            _build_attachment_payload(attachment, prefix=prefix, ordinal=index),
        )
        for index, attachment in enumerate(attachments, start=1)
    ]
    current_keys = {key for key, _payload in keyed}
    if previous_keys and previous_keys.issubset(current_keys):
        if current_keys == previous_keys and _INPUT_ATTACHMENT_SUMMARY_MARKER in str(input_text or ""):
            filtered = keyed
        else:
            filtered = [(key, payload) for key, payload in keyed if key not in previous_keys]
    else:
        filtered = keyed
    return [payload for _key, payload in filtered], current_keys


def _build_process_items(value: Any, *, prefix: str) -> list[dict[str, Any]]:
    items = _coerce_list_payload(value)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        payload = _record_to_dict(item)
        item_id = _normalize_text(payload.get("item_id")) or f"{prefix}_{index}"
        kind = _normalize_text(payload.get("kind")) or "assistant"
        created_at = _normalize_text(payload.get("created_at"))
        updated_at = _normalize_text(payload.get("updated_at")) or created_at
        status = _normalize_text(payload.get("status"))
        text = _normalize_text(payload.get("text"))
        normalized.append(
            {
                "item_id": item_id,
                "kind": kind,
                "created_at": created_at,
                "updated_at": updated_at,
                "status": status,
                "text": text,
            }
        )
    return normalized


def _build_session_record(record: Mapping[str, Any]) -> dict[str, Any]:
    pending_task_count = record.get("pending_task_count")
    if not isinstance(pending_task_count, int) or pending_task_count < 0:
        pending_task_count = 1 if _normalize_text(record.get("queued_task_id")) else 0
    backend_session_id = _normalize_text(record.get("backend_session_id"))
    backend_session_resumable = record.get("backend_session_resumable")
    if backend_session_resumable is None:
        backend_session_resumable = bool(backend_session_id)
    return {
        "session_id": _canonical_session_id(record),
        "thread_id": _normalize_text(record.get("thread_id")),
        "pc_id": _normalize_text(record.get("pc_id")),
        "workspace_id": _canonical_workspace_id(record),
        "session_name": _normalize_text(record.get("session_name")),
        "status": _session_status_for_list(record),
        "lifecycle": _normalize_text(record.get("lifecycle")) or "active",
        "backend": _normalize_text(record.get("backend")),
        "backend_transport": _normalize_text(record.get("backend_transport")),
        "profile": _normalize_text(record.get("profile")),
        "permission": _normalize_text(record.get("permission")),
        "repo_path": _normalize_text(record.get("repo_path")),
        "workdir": _normalize_text(record.get("workdir")),
        "current_task_id": _normalize_text(record.get("current_task_id")),
        "queued_task_id": _normalize_text(record.get("queued_task_id")),
        "pending_task_count": pending_task_count,
        "last_summary": _normalize_text(record.get("last_summary")),
        "last_active_at": _normalize_text(record.get("last_active_at")),
        "last_progress_at": _normalize_text(record.get("last_progress_at")),
        "backend_session_id": backend_session_id,
        "backend_session_resumable": _coerce_bool(backend_session_resumable),
        "created_at": _normalize_text(record.get("created_at")),
        "updated_at": _normalize_text(record.get("updated_at")),
    }


def _build_session_snapshot_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    session_snapshot_status = _session_status_for_snapshot(record)
    last_summary = _normalize_text(record.get("last_summary")) or _humanize_status(session_snapshot_status)
    question_state = _coerce_json_payload(record.get("question_state_json")) if record.get("question_state_json") is not None else _coerce_json_payload(record.get("question_state"))
    if not isinstance(question_state, dict):
        question_state = None
    timeline_items = _coerce_json_payload(record.get("timeline_items_json")) if record.get("timeline_items_json") is not None else _coerce_json_payload(record.get("timeline_items"))
    if not isinstance(timeline_items, list):
        timeline_items = []
    live_process = _build_live_process_payload(record.get("live_process"))
    return {
        "session_name": _normalize_text(record.get("session_name")),
        "backend": _normalize_text(record.get("backend")),
        "repo_path": _normalize_text(record.get("repo_path")),
        "workdir": _normalize_text(record.get("workdir")),
        "status": session_snapshot_status,
        "lifecycle": _normalize_text(record.get("lifecycle")) or "active",
        "last_summary": last_summary,
        "last_active_at": _normalize_text(record.get("last_active_at")),
        "last_progress_at": _max_timestamp_text(
            _normalize_text(record.get("last_progress_at")),
            None if live_process is None else _normalize_text(live_process.get("updated_at")),
        ),
        "paused_from_status": _normalize_text(record.get("paused_from_status")),
        "question_state": question_state,
        "timeline_items": timeline_items,
        "live_process": live_process,
    }


def _build_live_process_payload(value: Any) -> dict[str, Any] | None:
    payload = _coerce_json_payload(value)
    if not isinstance(payload, dict):
        return None
    status = _normalize_text(payload.get("status"))
    updated_at = _normalize_text(payload.get("updated_at"))
    items = _build_process_items(payload.get("items"), prefix="live_process")
    if updated_at is None or status is None:
        return None
    return {
        "status": status,
        "updated_at": updated_at,
        "items": items,
    }


def _resolve_session_candidates(
    sessions: list[Mapping[str, Any]],
    *,
    session_id: str | None,
    thread_id: str | None,
) -> list[Mapping[str, Any]]:
    candidates = sessions
    if session_id is not None:
        candidates = [record for record in candidates if _canonical_session_id(record) == session_id]
    if thread_id is not None:
        candidates = [record for record in candidates if _normalize_text(record.get("thread_id")) == thread_id]
    return candidates


def _supporting_locator_mismatch_code(
    candidates: list[Mapping[str, Any]],
    *,
    workspace_id: str | None,
    repo_path: str | None,
    workdir: str | None,
    thread_id: str | None,
) -> str | None:
    if workspace_id is not None and all(_canonical_workspace_id(record) != workspace_id for record in candidates):
        return "workspace_identity_mismatch"
    if repo_path is not None and all(
        normalize_workspace_value(_normalize_text(record.get("repo_path"))) != normalize_workspace_value(repo_path)
        for record in candidates
    ):
        return "workspace_identity_mismatch"
    if workdir is not None and all(
        normalize_workspace_value(_normalize_text(record.get("workdir"))) != normalize_workspace_value(workdir)
        for record in candidates
    ):
        return "workspace_identity_mismatch"
    if thread_id is not None and all(_normalize_text(record.get("thread_id")) != thread_id for record in candidates):
        return "session_identity_mismatch"
    return None


def _resolve_single_session(
    projection_store: AndroidProjectionStore,
    *,
    query: Mapping[str, list[str]],
) -> dict[str, Any]:
    session_id = _optional_query_text(query, "session_id")
    thread_id = _optional_query_text(query, "thread_id")
    workspace_id = _optional_query_text(query, "workspace_id")
    repo_path = _optional_query_text(query, "repo_path")
    workdir = _optional_query_text(query, "workdir")

    if session_id is None and thread_id is None:
        raise AndroidProjectionStoreFacadeError(
            status_code=400,
            error_code="invalid_payload",
            error_message="session_id or thread_id is required",
            retryable=False,
        )

    try:
        raw_sessions = projection_store.list_sessions()
    except AttributeError as exc:
        raise AndroidProjectionStoreFacadeError(
            status_code=503,
            error_code="task_root_unavailable",
            error_message="projection store is not configured",
            retryable=True,
        ) from exc

    sessions = _load_store_items(raw_sessions)
    primary_candidates = _resolve_session_candidates(sessions, session_id=session_id, thread_id=thread_id)
    if not primary_candidates:
        if session_id is not None and any(_canonical_session_id(record) == session_id for record in sessions):
            mismatch_code = _supporting_locator_mismatch_code(
                [record for record in sessions if _canonical_session_id(record) == session_id],
                workspace_id=workspace_id,
                repo_path=repo_path,
                workdir=workdir,
                thread_id=thread_id,
            )
            if mismatch_code is not None:
                raise AndroidProjectionStoreFacadeError(
                    status_code=409,
                    error_code=mismatch_code,
                    error_message="supporting locators do not match the canonical session",
                    retryable=False,
                )
            raise AndroidProjectionStoreFacadeError(
                status_code=409,
                error_code="session_binding_unresolved",
                error_message="supporting locators did not resolve a unique canonical session",
                retryable=False,
            )
        if thread_id is not None and any(_normalize_text(record.get("thread_id")) == thread_id for record in sessions):
            mismatch_code = _supporting_locator_mismatch_code(
                [record for record in sessions if _normalize_text(record.get("thread_id")) == thread_id],
                workspace_id=workspace_id,
                repo_path=repo_path,
                workdir=workdir,
                thread_id=thread_id,
            )
            if mismatch_code is not None:
                raise AndroidProjectionStoreFacadeError(
                    status_code=409,
                    error_code=mismatch_code,
                    error_message="supporting locators do not match the canonical session",
                    retryable=False,
                )
        raise AndroidProjectionStoreFacadeError(
            status_code=404,
            error_code="session_not_found",
            error_message="could not resolve a session for the requested locator",
            retryable=False,
        )

    filtered_candidates = primary_candidates
    if workspace_id is not None:
        filtered_candidates = [record for record in filtered_candidates if _canonical_workspace_id(record) == workspace_id]
    if repo_path is not None:
        filtered_candidates = [
            record
            for record in filtered_candidates
            if normalize_workspace_value(_normalize_text(record.get("repo_path"))) == normalize_workspace_value(repo_path)
        ]
    if workdir is not None:
        filtered_candidates = [
            record
            for record in filtered_candidates
            if normalize_workspace_value(_normalize_text(record.get("workdir"))) == normalize_workspace_value(workdir)
        ]

    if not filtered_candidates:
        mismatch_code = _supporting_locator_mismatch_code(
            primary_candidates,
            workspace_id=workspace_id,
            repo_path=repo_path,
            workdir=workdir,
            thread_id=thread_id,
        )
        if mismatch_code is not None:
            raise AndroidProjectionStoreFacadeError(
                status_code=409,
                error_code=mismatch_code,
                error_message="supporting locators do not match the canonical session",
                retryable=False,
            )
        raise AndroidProjectionStoreFacadeError(
            status_code=409,
            error_code="session_binding_unresolved",
            error_message="supporting locators did not resolve a unique canonical session",
            retryable=False,
        )

    if len(filtered_candidates) > 1:
        raise AndroidProjectionStoreFacadeError(
            status_code=409,
            error_code="session_binding_unresolved",
            error_message="multiple sessions matched the requested locator",
            retryable=False,
        )

    return filtered_candidates[0]


def _build_history_round_payload(
    round_record: Mapping[str, Any],
    *,
    round_number: int,
    previous_input_attachment_keys: set[str],
) -> tuple[dict[str, Any], set[str]]:
    task_id = _normalize_text(round_record.get("task_id")) or "task"
    status = _normalize_text(round_record.get("status")) or "done"
    round_sort_at = (
        _normalize_text(round_record.get("round_sort_at"))
        or _normalize_text(round_record.get("created_at"))
        or _normalize_text(round_record.get("source_updated_at"))
        or _timestamp()
    )
    input_payload = _record_to_dict(round_record.get("input")) if round_record.get("input") is not None else {}
    process_payload = _record_to_dict(round_record.get("process")) if round_record.get("process") is not None else {}
    result_payload = _record_to_dict(round_record.get("result")) if round_record.get("result") is not None else {}
    input_text = _normalize_text(round_record.get("input_text")) or _normalize_text(input_payload.get("text"))
    result_text = (
        _normalize_text(round_record.get("result_text"))
        or _normalize_text(result_payload.get("text"))
        or _humanize_status(status)
    )
    input_attachments = (
        round_record.get("input_attachments")
        or round_record.get("input_attachments_json")
        or input_payload.get("attachments")
    )
    process_items = (
        round_record.get("process_items")
        or round_record.get("process_items_json")
        or process_payload.get("items")
    )
    result_attachments = (
        round_record.get("result_attachments")
        or round_record.get("result_attachments_json")
        or result_payload.get("attachments")
    )
    input_attachment_payloads, current_input_attachment_keys = _build_input_attachment_delta(
        input_attachments,
        prefix=f"hist_input_{task_id}",
        previous_keys=previous_input_attachment_keys,
        input_text=input_text,
    )
    return {
        "round_id": _normalize_text(round_record.get("round_id")) or f"hist_round_{task_id}",
        "round_number": round_number,
        "created_at": round_sort_at,
        "status": status,
        "speaker_label": _normalize_text(round_record.get("speaker_label")) or "TaskMail",
        "input": {
            "text": input_text,
            "attachments": input_attachment_payloads,
        },
        "process": {
            "items": _build_process_items(
                process_items,
                prefix=f"hist_process_{task_id}",
            ),
        },
        "result": {
            "text": result_text,
            "attachments": _build_attachment_list(
                result_attachments,
                prefix=f"hist_result_{task_id}",
            ),
        },
    }, current_input_attachment_keys


def _load_history_rounds(
    projection_store: AndroidProjectionStore,
    *,
    session_record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    session_key = _session_session_key(session_record)
    if session_key is None:
        return []

    history_loader = getattr(projection_store, "list_history_rounds", None)
    if history_loader is None:
        raise AndroidProjectionStoreFacadeError(
            status_code=503,
            error_code="task_root_unavailable",
            error_message="projection store is not configured",
            retryable=True,
        )

    probe_kwargs = [
        {"session_key": session_key},
        {
            "pc_id": _normalize_text(session_record.get("pc_id")),
            "workspace_id": _canonical_workspace_id(session_record),
            "session_id": _canonical_session_id(session_record),
            "thread_id": _normalize_text(session_record.get("thread_id")),
        },
        {
            "session_id": _canonical_session_id(session_record),
            "thread_id": _normalize_text(session_record.get("thread_id")),
        },
        {
            "session_id": _canonical_session_id(session_record),
        },
    ]
    raw_rounds: list[Any] = []
    for kwargs in probe_kwargs:
        compact_kwargs = {key: value for key, value in kwargs.items() if value is not None}
        try:
            raw_rounds = history_loader(**compact_kwargs)
            break
        except TypeError:
            continue
    else:
        raise AndroidProjectionStoreFacadeError(
            status_code=503,
            error_code="task_root_unavailable",
            error_message="projection store history interface is not available",
            retryable=True,
        )

    rounds = _load_store_items(raw_rounds)
    rounds.sort(key=_sort_round_records)
    payload: list[dict[str, Any]] = []
    previous_input_attachment_keys: set[str] = set()
    for index, round_record in enumerate(rounds, start=1):
        round_payload, previous_input_attachment_keys = _build_history_round_payload(
            round_record,
            round_number=index,
            previous_input_attachment_keys=previous_input_attachment_keys,
        )
        payload.append(round_payload)
    payload.reverse()
    return payload


def _build_latest_session_action(
    *,
    session_record: Mapping[str, Any],
    latest_session_action_resolver: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    if latest_session_action_resolver is None:
        return None
    context = {
        "pc_id": _normalize_text(session_record.get("pc_id")),
        "workspace_id": _canonical_workspace_id(session_record),
        "session_id": _canonical_session_id(session_record),
        "thread_id": _normalize_text(session_record.get("thread_id")),
        "session_key": _session_session_key(session_record),
        "repo_path": _normalize_text(session_record.get("repo_path")),
        "workdir": _normalize_text(session_record.get("workdir")),
    }
    latest_action = latest_session_action_resolver(dict(context))
    if latest_action is None:
        return None
    if isinstance(latest_action, Mapping):
        return dict(latest_action)
    raise TypeError("latest_session_action_resolver must return a mapping or None")


def build_android_sessions_snapshot_from_projection_store(
    *,
    projection_store: AndroidProjectionStore,
    include_ended: bool = False,
    pc_ids: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    session_ids: list[str] | None = None,
    thread_ids: list[str] | None = None,
    refresh_after_seconds: int = DEFAULT_REFRESH_AFTER_SECONDS,
) -> dict[str, Any]:
    generated_at = _timestamp()
    pc_filter = {item for item in (_normalize_text(value) for value in pc_ids or []) if item is not None}
    workspace_filter = {item for item in (_normalize_text(value) for value in workspace_ids or []) if item is not None}
    session_filter = {item for item in (_normalize_text(value) for value in session_ids or []) if item is not None}
    thread_filter = {item for item in (_normalize_text(value) for value in thread_ids or []) if item is not None}

    try:
        raw_sessions = projection_store.list_sessions()
    except AttributeError as exc:
        raise AndroidProjectionStoreFacadeError(
            status_code=503,
            error_code="task_root_unavailable",
            error_message="projection store is not configured",
            retryable=True,
        ) from exc

    sessions = _load_store_items(raw_sessions)
    sessions.sort(key=_sort_session_records, reverse=True)
    sessions.sort(key=lambda record: _normalize_text(record.get("lifecycle")) != "active")

    records: list[dict[str, Any]] = []
    for session_record in sessions:
        if not include_ended and _normalize_text(session_record.get("lifecycle")) == "ended":
            continue
        resolved_pc_id = _normalize_text(session_record.get("pc_id"))
        if pc_filter and resolved_pc_id not in pc_filter:
            continue
        workspace_id = _canonical_workspace_id(session_record)
        if workspace_filter and workspace_id not in workspace_filter:
            continue
        canonical_session_id = _canonical_session_id(session_record)
        if session_filter and canonical_session_id not in session_filter:
            continue
        thread_id = _normalize_text(session_record.get("thread_id"))
        if thread_filter and thread_id not in thread_filter:
            continue
        records.append(_build_session_record(session_record))

    return {
        "schema_version": ANDROID_SESSIONS_SCHEMA_VERSION,
        "snapshot_id": f"sess_snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": generated_at,
        "refresh_after_seconds": max(1, int(refresh_after_seconds)),
        "session_count": len(records),
        "sessions": records,
    }


def build_android_session_snapshot_from_projection_store(
    *,
    projection_store: AndroidProjectionStore,
    query: Mapping[str, list[str]],
    latest_session_action_resolver: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    generated_at = _timestamp()
    session_record = _resolve_single_session(projection_store, query=query)
    session_key = _session_session_key(session_record)
    live_process_loader = getattr(projection_store, "get_live_process", None)
    if session_key is not None and live_process_loader is not None:
        try:
            live_process = live_process_loader(session_key=session_key)
        except TypeError:
            live_process = None
        if isinstance(live_process, Mapping):
            session_record = dict(session_record)
            session_record["live_process"] = dict(live_process)
    session_payload = _build_session_record(session_record)
    history_rounds = _load_history_rounds(projection_store, session_record=session_record)
    snapshot_payload = _build_session_snapshot_projection(session_record)
    snapshot_payload["latest_session_action"] = _build_latest_session_action(
        session_record=session_record,
        latest_session_action_resolver=latest_session_action_resolver,
    )
    snapshot_payload["history_rounds"] = history_rounds
    return {
        "schema_version": ANDROID_SESSION_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": f"sess_detail_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": generated_at,
        "locator": _canonical_locator(session_record),
        "session": session_payload,
        "session_snapshot": snapshot_payload,
    }


def build_android_session_history_from_projection_store(
    *,
    projection_store: AndroidProjectionStore,
    query: Mapping[str, list[str]],
    latest_session_action_resolver: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    snapshot_payload = build_android_session_snapshot_from_projection_store(
        projection_store=projection_store,
        query=query,
        latest_session_action_resolver=latest_session_action_resolver,
    )
    return {
        "schema_version": ANDROID_SESSION_HISTORY_SCHEMA_VERSION,
        "history_id": f"sess_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}",
        "generated_at": snapshot_payload["generated_at"],
        "locator": dict(snapshot_payload["locator"]),
        "session": dict(snapshot_payload["session"]),
        "history_rounds": list(snapshot_payload["session_snapshot"]["history_rounds"]),
    }


__all__ = [
    "AndroidProjectionStore",
    "AndroidProjectionStoreFacadeError",
    "build_android_session_history_from_projection_store",
    "build_android_session_snapshot_from_projection_store",
    "build_android_sessions_snapshot_from_projection_store",
]
