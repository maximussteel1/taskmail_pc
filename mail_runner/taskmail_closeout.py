"""TaskMail daily closeout bundle helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from .session_action_closeout import (
    ACTION_TYPE_HEADER,
    RECEIPT_ID_HEADER,
    SESSION_ACTION_CLOSEOUT_FILENAME,
    target_session_identity_from_headers,
)
from .thread_store import load_thread_state
from .workspace import WorkspaceManager

_RAW_MAIL_RE = re.compile(r"^raw_(?P<index>\d+)\.json$")
_STATUS_LABEL_RE = re.compile(r"^\[(?P<status>[A-Z]+)\]")
_DEFAULT_BUNDLE_FILENAME = "taskmail_daily_closeout_bundle.json"
_DIRECT_HEADER = "X-TaskMail-Direct"
_REQUEST_ID_HEADER = "X-TaskMail-Relay-Request-Id"
_PACKET_ID_HEADER = "X-TaskMail-Relay-Packet-Id"
_RESULT_FILENAME = "result.json"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _raw_index(raw_path: Path) -> int:
    match = _RAW_MAIL_RE.match(raw_path.name)
    if match is None:
        raise ValueError(f"Unsupported raw mail filename: {raw_path.name}")
    return int(match.group("index"))


def _normalized_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_action_type(value: Any) -> str | None:
    text = _normalized_text(value)
    if text is None:
        return None
    return text.lower()


def _raw_mail_items(task_root: str | Path, thread_id: str) -> list[tuple[Path, dict[str, Any]]]:
    workspace = WorkspaceManager(task_root)
    mail_dir = workspace.mail_dir(thread_id)
    if not mail_dir.exists():
        return []

    items: list[tuple[Path, dict[str, Any]]] = []
    for raw_path in sorted(mail_dir.glob("raw_*.json"), key=_raw_index):
        payload = workspace.load_json(raw_path)
        if isinstance(payload, dict):
            items.append((raw_path, payload))
    return items


def _payload_subject(payload: dict[str, Any]) -> str | None:
    raw_headers = payload.get("raw_headers") or {}
    if not isinstance(raw_headers, dict):
        raw_headers = {}
    return _normalized_text(payload.get("subject") or raw_headers.get("Subject"))


def _payload_message_id(payload: dict[str, Any]) -> str | None:
    return _normalized_text(payload.get("message_id"))


def _is_system_mail(payload: dict[str, Any]) -> bool:
    raw_headers = payload.get("raw_headers") or {}
    if not isinstance(raw_headers, dict):
        return False
    return _normalized_text(raw_headers.get(SYSTEM_MESSAGE_HEADER)) == SYSTEM_MESSAGE_HEADER_VALUE


def _status_label(subject: str | None) -> str | None:
    text = _normalized_text(subject)
    if text is None:
        return None
    match = _STATUS_LABEL_RE.match(text)
    if match is None:
        return None
    return match.group("status")


def _payload_headers(payload: dict[str, Any]) -> dict[str, Any]:
    raw_headers = payload.get("raw_headers") or {}
    if not isinstance(raw_headers, dict):
        return {}
    return raw_headers


def _payload_header(payload: dict[str, Any], name: str) -> str | None:
    return _normalized_text(_payload_headers(payload).get(name))


def _payload_direct_header(payload: dict[str, Any]) -> str | None:
    return _payload_header(payload, _DIRECT_HEADER)


def _payload_request_id(payload: dict[str, Any]) -> str | None:
    return _payload_header(payload, _REQUEST_ID_HEADER)


def _payload_packet_id(payload: dict[str, Any]) -> str | None:
    return _payload_header(payload, _PACKET_ID_HEADER)


def _payload_receipt_id(payload: dict[str, Any]) -> str | None:
    return _payload_header(payload, RECEIPT_ID_HEADER)


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _session_action_closeout_items(task_root: str | Path, thread_id: str) -> list[tuple[Path, dict[str, Any]]]:
    workspace = WorkspaceManager(task_root)
    root = workspace.session_actions_dir(thread_id)
    if not root.exists():
        return []

    items: list[tuple[Path, dict[str, Any]]] = []
    for payload_path in root.glob(f"*/{SESSION_ACTION_CLOSEOUT_FILENAME}"):
        payload = workspace.load_json(payload_path)
        if isinstance(payload, dict):
            items.append((payload_path, payload))
    return sorted(
        items,
        key=lambda item: (
            _normalized_text(item[1].get("generated_at")) or "",
            str(item[0]),
        ),
        reverse=True,
    )


def _select_session_action_closeout_item(
    task_root: str | Path,
    thread_id: str,
    *,
    request_id: str | None,
) -> tuple[Path | None, dict[str, Any] | None, str]:
    items = _session_action_closeout_items(task_root, thread_id)
    if not items:
        return None, None, "not_found"

    normalized_request_id = _normalized_text(request_id)
    if normalized_request_id is not None:
        for payload_path, payload in items:
            if _normalized_text(payload.get("request_id")) == normalized_request_id:
                return payload_path, payload, "request_id"

    return items[0][0], items[0][1], "latest_generated_at"


def _bundle_anchor_from_thread_state(state: Any) -> dict[str, Any]:
    return {
        "ingress_type": None,
        "request_id": None,
        "ingress_message_id": _normalized_text(state.root_message_id),
        "packet_id": None,
        "receipt_id": None,
        "action_type": None,
        "target_session_identity": None,
        "last_summary": _normalized_text(state.last_summary),
        "terminal_mail_message_id": _normalized_text(state.latest_message_id),
        "terminal_mail_subject": None,
    }


def _normalized_target_session_identity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    payload = {
        "workspace_id": _normalized_text(value.get("workspace_id")),
        "session_id": _normalized_text(value.get("session_id")),
        "thread_id": _normalized_text(value.get("thread_id")),
    }
    if all(item is None for item in payload.values()):
        return None
    return {key: item for key, item in payload.items() if item is not None}


def _bundle_anchor_from_session_action_closeout(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ingress_type": _normalized_text(payload.get("ingress_type")),
        "request_id": _normalized_text(payload.get("request_id")),
        "ingress_message_id": _normalized_text(payload.get("ingress_message_id")),
        "packet_id": _normalized_text(payload.get("packet_id")),
        "receipt_id": _normalized_text(payload.get("receipt_id")),
        "action_type": _normalized_text(payload.get("action_type")),
        "target_session_identity": _normalized_target_session_identity(payload.get("target_session_identity")),
        "last_summary": _normalized_text(payload.get("last_summary")),
        "terminal_mail_message_id": _normalized_text(payload.get("terminal_mail_message_id")),
        "terminal_mail_subject": _normalized_text(payload.get("terminal_mail_subject")),
    }


def _bundle_anchor_from_canonical_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ingress_type": _normalized_text(payload.get("ingress_type")),
        "request_id": _normalized_text(payload.get("request_id")),
        "ingress_message_id": _normalized_text(payload.get("ingress_message_id")),
        "packet_id": _normalized_text(payload.get("packet_id")),
        "receipt_id": _normalized_text(payload.get("receipt_id")),
        "action_type": _normalized_text(payload.get("action_type")),
        "target_session_identity": _normalized_target_session_identity(payload.get("target_session_identity")),
        "last_summary": _normalized_text(payload.get("last_summary")),
        "terminal_mail_message_id": _normalized_text(payload.get("terminal_mail_message_id")),
        "terminal_mail_subject": _normalized_text(payload.get("terminal_mail_subject")),
    }


def _merge_bundle_anchors(
    primary: dict[str, Any],
    secondary: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(primary)
    if secondary is None:
        return merged
    for key, value in secondary.items():
        if key == "target_session_identity":
            if _normalized_target_session_identity(merged.get(key)) is None:
                merged[key] = _normalized_target_session_identity(value)
            continue
        if _normalized_text(merged.get(key)) is None and _normalized_text(value) is not None:
            merged[key] = _normalized_text(value)
    return merged


def _select_ingress_mail_item(
    task_root: str | Path,
    thread_id: str,
    *,
    ingress_message_id: str | None,
) -> tuple[Path | None, dict[str, Any] | None, str]:
    items = _raw_mail_items(task_root, thread_id)
    if not items:
        return None, None, "not_found"

    normalized_ingress_message_id = _normalized_text(ingress_message_id)
    if normalized_ingress_message_id is not None:
        for raw_path, payload in items:
            if _payload_message_id(payload) == normalized_ingress_message_id:
                return raw_path, payload, "ingress_message_id"

    for raw_path, payload in items:
        if not _is_system_mail(payload):
            return raw_path, payload, "first_non_system_mail"

    return None, None, "not_found"


def _select_terminal_mail_item(
    task_root: str | Path,
    thread_id: str,
    *,
    terminal_message_id: str | None,
    terminal_subject: str | None,
    message_id_source: str,
) -> tuple[Path | None, dict[str, Any] | None, str]:
    items = _raw_mail_items(task_root, thread_id)
    if not items:
        return None, None, "not_found"

    normalized_terminal_message_id = _normalized_text(terminal_message_id)
    if normalized_terminal_message_id is not None:
        for raw_path, payload in items:
            if _payload_message_id(payload) == normalized_terminal_message_id:
                return raw_path, payload, message_id_source

    normalized_terminal_subject = _normalized_text(terminal_subject)
    system_items = [(raw_path, payload) for raw_path, payload in items if _is_system_mail(payload)]

    if normalized_terminal_subject is not None:
        for raw_path, payload in system_items:
            if _payload_subject(payload) == normalized_terminal_subject:
                return raw_path, payload, "terminal_mail_subject"
        for raw_path, payload in items:
            if _payload_subject(payload) == normalized_terminal_subject:
                return raw_path, payload, "terminal_mail_subject"

    terminal_status_label = _status_label(normalized_terminal_subject)
    if terminal_status_label is not None:
        for raw_path, payload in reversed(system_items):
            if _status_label(_payload_subject(payload)) == terminal_status_label:
                return raw_path, payload, "terminal_status_label"

    return None, None, "not_found"


def load_android_taskmail_send_records(path: str | Path) -> list[dict[str, Any]]:
    root = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(root, dict):
        return []

    raw_records = root.get("records")
    if not isinstance(raw_records, list):
        return []

    records: list[dict[str, Any]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, str):
            continue
        try:
            payload = json.loads(raw_record)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        evidence = payload.get("evidence") or {}
        if not isinstance(evidence, dict):
            evidence = {}
        target = payload.get("target") or {}
        if not isinstance(target, dict):
            target = {}
        recorded_at = payload.get("recordedAt")
        if not isinstance(recorded_at, int):
            recorded_at = 0
        records.append(
            {
                "recorded_at": recorded_at,
                "action_type": _normalized_action_type(payload.get("actionType")),
                "target_session_identity": _normalized_target_session_identity(
                    {
                        "workspace_id": target.get("workspaceId"),
                        "session_id": target.get("sessionId"),
                        "thread_id": target.get("threadId"),
                    }
                ),
                "sender_account_id": _normalized_text(payload.get("senderAccountId")),
                "repo_path": _normalized_text(payload.get("repoPath")),
                "workdir": _normalized_text(payload.get("workdir")),
                "backend": _normalized_text(payload.get("backend")),
                "bootstrap_status": _normalized_text(evidence.get("bootstrapStatus")),
                "outcome": _normalized_text(evidence.get("outcome")),
                "switch_gate": _normalized_text(evidence.get("switchGate")),
                "request_id": _normalized_text(evidence.get("requestId")),
                "receipt_id": _normalized_text(evidence.get("receiptId")),
                "transport_message_id": _normalized_text(evidence.get("transportMessageId")),
                "fallback_reason": _normalized_text(evidence.get("fallbackReason")),
                "error_message": _normalized_text(evidence.get("errorMessage")),
            }
        )
    return sorted(records, key=lambda item: item["recorded_at"], reverse=True)


def _workspace_matches(
    record: dict[str, Any],
    *,
    repo_path: str | None,
    workdir: str | None,
) -> bool:
    if repo_path is None:
        return True
    if record.get("repo_path") != repo_path:
        return False
    return (record.get("workdir") or None) == (workdir or None)


def _parse_iso_timestamp_to_epoch_millis(value: Any) -> int | None:
    text = _normalized_text(value)
    if text is None:
        return None
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def _android_send_outcome_family(record: dict[str, Any]) -> str | None:
    outcome = _normalized_text(record.get("outcome"))
    switch_gate = _normalized_text(record.get("switch_gate"))
    if outcome == "DirectAccepted" or switch_gate == "KeepDirectDefault":
        return "direct"
    if outcome in {"MailFallbackSucceeded", "MailFallbackFailed"} or switch_gate == "FallbackRequired":
        return "fallback"
    if outcome == "DirectRejected" or switch_gate == "SwitchBlocker":
        return "hard_rejection"
    return None


def _target_session_identity_matches(
    record: dict[str, Any],
    target_session_identity: dict[str, str] | None,
) -> bool:
    normalized_target = _normalized_target_session_identity(target_session_identity)
    record_target = _normalized_target_session_identity(record.get("target_session_identity"))
    if normalized_target is None or record_target is None:
        return False

    for key, expected_value in normalized_target.items():
        if record_target.get(key) != expected_value:
            return False

    return True


def _expected_android_send_outcome_family(ingress_type: str | None) -> str | None:
    normalized_ingress_type = _normalized_text(ingress_type)
    if normalized_ingress_type == "direct_bridge":
        return "direct"
    if normalized_ingress_type == "mail":
        return "fallback"
    return None


def _candidate_time_distance(record: dict[str, Any], anchor_recorded_at: int | None) -> int | None:
    recorded_at = record.get("recorded_at")
    if not isinstance(recorded_at, int) or recorded_at <= 0 or anchor_recorded_at is None:
        return None
    return abs(recorded_at - anchor_recorded_at)


def _select_best_android_record(
    records: list[dict[str, Any]],
    *,
    repo_path: str | None,
    workdir: str | None,
    anchor_recorded_at: int | None,
) -> dict[str, Any] | None:
    if not records:
        return None

    def _sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        distance = _candidate_time_distance(item, anchor_recorded_at)
        recorded_at = item.get("recorded_at")
        normalized_recorded_at = recorded_at if isinstance(recorded_at, int) else 0
        workspace_rank = 0 if _workspace_matches(item, repo_path=repo_path, workdir=workdir) else 1
        distance_rank = distance if distance is not None else 2**62
        return (workspace_rank, distance_rank, -normalized_recorded_at)

    return sorted(records, key=_sort_key)[0]


def select_android_taskmail_send_record(
    records: list[dict[str, Any]],
    *,
    request_id: str | None,
    ingress_message_id: str | None,
    action_type: str | None,
    target_session_identity: dict[str, str] | None,
    ingress_type: str | None,
    sender_account_id: str | None,
    repo_path: str | None,
    workdir: str | None,
    anchor_timestamp: str | None,
) -> tuple[dict[str, Any] | None, str]:
    candidates = list(records)
    if sender_account_id is not None:
        sender_matches = [item for item in candidates if item.get("sender_account_id") == sender_account_id]
        if sender_matches:
            candidates = sender_matches
    if not candidates:
        return None, "not_found"

    if request_id is not None:
        request_matches = [item for item in candidates if item.get("request_id") == request_id]
        if request_matches:
            best_request_match = _select_best_android_record(
                request_matches,
                repo_path=repo_path,
                workdir=workdir,
                anchor_recorded_at=_parse_iso_timestamp_to_epoch_millis(anchor_timestamp),
            )
            return best_request_match, "request_id"

    if ingress_message_id is not None:
        transport_matches = [
            item
            for item in candidates
            if item.get("transport_message_id") == ingress_message_id
        ]
        if transport_matches:
            best_transport_match = _select_best_android_record(
                transport_matches,
                repo_path=repo_path,
                workdir=workdir,
                anchor_recorded_at=_parse_iso_timestamp_to_epoch_millis(anchor_timestamp),
            )
            return best_transport_match, "transport_message_id"

    workspace_matches = [
        item
        for item in candidates
        if _workspace_matches(item, repo_path=repo_path, workdir=workdir)
    ]
    anchor_recorded_at = _parse_iso_timestamp_to_epoch_millis(anchor_timestamp)
    expected_outcome_family = _expected_android_send_outcome_family(ingress_type)
    normalized_action_type = _normalized_action_type(action_type)
    normalized_target_session_identity = _normalized_target_session_identity(target_session_identity)

    if normalized_target_session_identity is not None:
        target_matches = [
            item
            for item in candidates
            if _target_session_identity_matches(item, normalized_target_session_identity)
        ]
        if normalized_action_type is not None:
            target_matches = [
                item
                for item in target_matches
                if _normalized_action_type(item.get("action_type")) == normalized_action_type
            ]
        if expected_outcome_family is not None:
            target_outcome_matches = [
                item
                for item in target_matches
                if _android_send_outcome_family(item) == expected_outcome_family
            ]
            best_target_outcome_match = _select_best_android_record(
                target_outcome_matches,
                repo_path=repo_path,
                workdir=workdir,
                anchor_recorded_at=anchor_recorded_at,
            )
            if best_target_outcome_match is not None:
                return best_target_outcome_match, "target_outcome_time"

        if target_matches:
            best_target_match = _select_best_android_record(
                target_matches,
                repo_path=repo_path,
                workdir=workdir,
                anchor_recorded_at=anchor_recorded_at,
            )
            return best_target_match, "target_time"

    if expected_outcome_family is not None:
        workspace_outcome_matches = [
            item
            for item in workspace_matches
            if _android_send_outcome_family(item) == expected_outcome_family
        ]
        best_workspace_outcome_match = _select_best_android_record(
            workspace_outcome_matches,
            repo_path=repo_path,
            workdir=workdir,
            anchor_recorded_at=anchor_recorded_at,
        )
        if best_workspace_outcome_match is not None:
            return best_workspace_outcome_match, "workspace_outcome_time"

    if workspace_matches:
        best_workspace_match = _select_best_android_record(
            workspace_matches,
            repo_path=repo_path,
            workdir=workdir,
            anchor_recorded_at=anchor_recorded_at,
        )
        return best_workspace_match, "workspace_time"

    if sender_account_id is not None:
        if expected_outcome_family is not None:
            sender_outcome_matches = [
                item
                for item in candidates
                if _android_send_outcome_family(item) == expected_outcome_family
            ]
            best_sender_outcome_match = _select_best_android_record(
                sender_outcome_matches,
                repo_path=repo_path,
                workdir=workdir,
                anchor_recorded_at=anchor_recorded_at,
            )
            if best_sender_outcome_match is not None:
                return best_sender_outcome_match, "sender_outcome_time"

        best_sender_match = _select_best_android_record(
            candidates,
            repo_path=repo_path,
            workdir=workdir,
            anchor_recorded_at=anchor_recorded_at,
        )
        return best_sender_match, "sender_time"

    return None, "not_found"


def _evaluate_same_run_bind(
    canonical_summary: dict[str, Any],
    android_record: dict[str, Any] | None,
    *,
    android_last_summary: str | None,
) -> dict[str, Any]:
    canonical_request_id = _normalized_text(canonical_summary.get("request_id"))
    canonical_ingress_message_id = _normalized_text(canonical_summary.get("ingress_message_id"))
    canonical_last_summary = _normalized_text(canonical_summary.get("last_summary"))

    matched_fields: list[str] = []
    mismatched_fields: list[str] = []
    notes: list[str] = []

    if android_record is None:
        notes.append("android_latest_send_evidence_missing")
    else:
        android_request_id = _normalized_text(android_record.get("request_id"))
        android_transport_message_id = _normalized_text(android_record.get("transport_message_id"))
        if canonical_request_id is not None and android_request_id is not None:
            if canonical_request_id == android_request_id:
                matched_fields.append("request_id")
            else:
                mismatched_fields.append("request_id")
        elif canonical_request_id is not None:
            notes.append("android_request_id_missing")

        if canonical_ingress_message_id is not None and android_transport_message_id is not None:
            if canonical_ingress_message_id == android_transport_message_id:
                matched_fields.append("transport_message_id")
            else:
                mismatched_fields.append("transport_message_id")
        elif canonical_ingress_message_id is not None:
            notes.append("android_transport_message_id_missing")

    normalized_android_last_summary = _normalized_text(android_last_summary)
    if normalized_android_last_summary is not None and canonical_last_summary is not None:
        if normalized_android_last_summary == canonical_last_summary:
            matched_fields.append("last_summary")
        else:
            mismatched_fields.append("last_summary")
    elif canonical_last_summary is not None:
        notes.append("android_last_summary_missing")

    if "request_id" in matched_fields:
        effective_bind_level = "request_id"
    elif "transport_message_id" in matched_fields:
        effective_bind_level = "transport_message_id"
    elif "last_summary" in matched_fields:
        effective_bind_level = "last_summary"
    else:
        effective_bind_level = "none"

    strong_bind = effective_bind_level in {"request_id", "transport_message_id"}

    return {
        "effective_bind_level": effective_bind_level,
        "matched_fields": matched_fields,
        "mismatched_fields": mismatched_fields,
        "strong_bind": strong_bind,
        "weak_bind_only": effective_bind_level == "last_summary",
        "can_promote_to_mismatch_candidate": strong_bind,
        "notes": notes,
    }


def _relative_path(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _mail_item_evidence(
    thread_root: Path,
    raw_path: Path | None,
    payload: dict[str, Any] | None,
    *,
    resolution: str,
) -> dict[str, Any]:
    return {
        "resolution": resolution,
        "path": str(raw_path) if raw_path is not None else None,
        "thread_relative_path": _relative_path(thread_root, raw_path),
        "message_id": _payload_message_id(payload) if payload is not None else None,
        "subject": _payload_subject(payload) if payload is not None else None,
        "status_label": _status_label(_payload_subject(payload)) if payload is not None else None,
        "request_id": _payload_request_id(payload) if payload is not None else None,
        "packet_id": _payload_packet_id(payload) if payload is not None else None,
        "receipt_id": _payload_receipt_id(payload) if payload is not None else None,
        "direct_header": _payload_direct_header(payload) if payload is not None else None,
    }


def _run_result_evidence(workspace: WorkspaceManager, thread_id: str, task_id: str) -> dict[str, Any] | None:
    result_path = workspace.run_file_path(thread_id, task_id, _RESULT_FILENAME)
    if not result_path.exists():
        return None
    payload = workspace.load_json(result_path)
    return {
        "path": str(result_path),
        "thread_relative_path": _relative_path(workspace.thread_dir(thread_id), result_path),
        "status": _normalized_text(payload.get("status")),
        "exit_code": payload.get("exit_code"),
        "started_at": _normalized_text(payload.get("started_at")),
        "finished_at": _normalized_text(payload.get("finished_at")),
        "summary_file": _normalized_text(payload.get("summary_file")),
        "error_type": _normalized_text(payload.get("error_type")),
        "error_message": _normalized_text(payload.get("error_message")),
    }


def _outbound_delivery_attempts_evidence(
    workspace: WorkspaceManager,
    thread_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    path = workspace.thread_dir(thread_id) / "outbound" / "delivery_attempts.jsonl"
    if not path.exists():
        return None
    matched_attempts = [
        item
        for item in _jsonl_rows(path)
        if _normalized_text(item.get("thread_id")) == thread_id
        and _normalized_text(item.get("task_id")) == task_id
    ]
    return {
        "path": str(path),
        "thread_relative_path": _relative_path(workspace.thread_dir(thread_id), path),
        "matched_attempt_count": len(matched_attempts),
        "matched_attempts": matched_attempts,
    }


def _discover_relay_state_dir(task_root: str | Path, relay_state_dir: str | Path | None) -> Path | None:
    if relay_state_dir is not None:
        candidate = Path(relay_state_dir)
        return candidate if candidate.exists() else None
    candidate = Path(task_root).resolve().parent / "relay_state"
    return candidate if candidate.exists() else None


def _relay_packet_store_evidence(
    task_root: str | Path,
    *,
    packet_id: str | None,
    relay_state_dir: str | Path | None,
) -> dict[str, Any] | None:
    state_dir = _discover_relay_state_dir(task_root, relay_state_dir)
    if state_dir is None:
        return None

    packets_path = state_dir / "packets.json"
    delivery_attempts_path = state_dir / "delivery_attempts.jsonl"
    normalized_packet_id = _normalized_text(packet_id)

    packet = None
    if packets_path.exists() and normalized_packet_id is not None:
        payload = json.loads(packets_path.read_text(encoding="utf-8"))
        packets = payload.get("packets") if isinstance(payload, dict) else []
        if isinstance(packets, list):
            packet = next(
                (
                    item
                    for item in packets
                    if isinstance(item, dict) and _normalized_text(item.get("packet_id")) == normalized_packet_id
                ),
                None,
            )

    matched_delivery_attempts = []
    if delivery_attempts_path.exists() and normalized_packet_id is not None:
        matched_delivery_attempts = [
            item
            for item in _jsonl_rows(delivery_attempts_path)
            if _normalized_text(item.get("packet_id")) == normalized_packet_id
        ]

    return {
        "state_dir": str(state_dir),
        "packets_path": str(packets_path) if packets_path.exists() else None,
        "delivery_attempts_path": str(delivery_attempts_path) if delivery_attempts_path.exists() else None,
        "packet": packet,
        "matched_delivery_attempt_count": len(matched_delivery_attempts),
        "matched_delivery_attempts": matched_delivery_attempts,
    }


def _thread_state_target_session_identity(state: Any) -> dict[str, str] | None:
    return _normalized_target_session_identity(
        {
            "workspace_id": getattr(state, "workspace_id", None),
            "session_id": getattr(state, "session_id", None) or getattr(state, "thread_id", None),
            "thread_id": getattr(state, "thread_id", None),
        }
    )


def _session_action_closeout_evidence(
    thread_root: Path,
    payload_path: Path | None,
    payload: dict[str, Any] | None,
    *,
    resolution: str,
) -> dict[str, Any] | None:
    if payload_path is None and payload is None:
        return None
    return {
        "resolution": resolution,
        "path": str(payload_path) if payload_path is not None else None,
        "thread_relative_path": _relative_path(thread_root, payload_path),
        "action_type": _normalized_text(payload.get("action_type")) if payload is not None else None,
        "request_id": _normalized_text(payload.get("request_id")) if payload is not None else None,
        "packet_id": _normalized_text(payload.get("packet_id")) if payload is not None else None,
        "receipt_id": _normalized_text(payload.get("receipt_id")) if payload is not None else None,
        "ingress_message_id": _normalized_text(payload.get("ingress_message_id")) if payload is not None else None,
        "terminal_mail_subject": _normalized_text(payload.get("terminal_mail_subject")) if payload is not None else None,
        "last_summary": _normalized_text(payload.get("last_summary")) if payload is not None else None,
        "target_session_identity": (
            _normalized_target_session_identity(payload.get("target_session_identity"))
            if payload is not None
            else None
        ),
    }


def default_taskmail_daily_closeout_bundle_path(
    task_root: str | Path,
    thread_id: str,
    task_id: str,
) -> Path:
    workspace = WorkspaceManager(task_root)
    return workspace.run_file_path(thread_id, task_id, _DEFAULT_BUNDLE_FILENAME)


def build_taskmail_daily_closeout_bundle(
    thread_id: str,
    task_root: str | Path,
    *,
    task_id: str | None = None,
    android_send_records_path: str | Path | None = None,
    sender_account_id: str | None = None,
    android_last_summary: str | None = None,
    relay_state_dir: str | Path | None = None,
) -> dict[str, Any]:
    workspace = WorkspaceManager(task_root)
    state = load_thread_state(thread_id, workspace.task_root)
    resolved_task_id = _normalized_text(task_id) or state.current_task_id
    run_dir = workspace.run_dir(thread_id, resolved_task_id)
    canonical_summary_path = workspace.run_file_path(thread_id, resolved_task_id, "canonical_summary.json")
    canonical_summary_payload = workspace.load_json(canonical_summary_path) if canonical_summary_path.exists() else None
    canonical_summary = (
        _bundle_anchor_from_canonical_summary(canonical_summary_payload)
        if isinstance(canonical_summary_payload, dict)
        else None
    )
    session_action_closeout_path, session_action_closeout_payload, session_action_closeout_resolution = (
        _select_session_action_closeout_item(
            workspace.task_root,
            thread_id,
            request_id=(
                _normalized_text(canonical_summary.get("request_id"))
                if canonical_summary is not None
                else None
            ),
        )
    )
    prefer_session_action_closeout = (
        canonical_summary is not None
        and session_action_closeout_payload is not None
        and _normalized_text(canonical_summary.get("ingress_type")) == "direct_bridge"
        and _normalized_action_type(canonical_summary.get("action_type")) in {"status", "reply"}
    )
    if prefer_session_action_closeout:
        canonical_summary = _merge_bundle_anchors(
            _bundle_anchor_from_session_action_closeout(session_action_closeout_payload),
            canonical_summary,
        )
        canonical_outcome_source = "session_action_closeout"
        canonical_outcome_path = session_action_closeout_path
        canonical_outcome_resolution = session_action_closeout_resolution
    elif canonical_summary is not None:
        canonical_outcome_source = "canonical_summary"
        canonical_outcome_path = canonical_summary_path
        canonical_outcome_resolution = "canonical_summary"
    elif session_action_closeout_payload is not None:
        canonical_summary = _bundle_anchor_from_session_action_closeout(session_action_closeout_payload)
        canonical_outcome_source = "session_action_closeout"
        canonical_outcome_path = session_action_closeout_path
        canonical_outcome_resolution = session_action_closeout_resolution
    else:
        canonical_summary = _bundle_anchor_from_thread_state(state)
        canonical_outcome_source = "thread_state_fallback"
        canonical_outcome_path = None
        canonical_outcome_resolution = "thread_state_fallback"

    ingress_mail_path, ingress_mail_payload, ingress_mail_resolution = _select_ingress_mail_item(
        workspace.task_root,
        thread_id,
        ingress_message_id=_normalized_text(canonical_summary.get("ingress_message_id")),
    )
    if ingress_mail_payload is not None:
        if _normalized_text(canonical_summary.get("ingress_message_id")) is None:
            canonical_summary["ingress_message_id"] = _payload_message_id(ingress_mail_payload)
        if _normalized_text(canonical_summary.get("request_id")) is None:
            canonical_summary["request_id"] = _payload_request_id(ingress_mail_payload)
        if _normalized_text(canonical_summary.get("packet_id")) is None:
            canonical_summary["packet_id"] = _payload_packet_id(ingress_mail_payload)
        if _normalized_text(canonical_summary.get("receipt_id")) is None:
            canonical_summary["receipt_id"] = _payload_receipt_id(ingress_mail_payload)
        if _normalized_text(canonical_summary.get("action_type")) is None:
            canonical_summary["action_type"] = _normalized_text(
                _payload_headers(ingress_mail_payload).get(ACTION_TYPE_HEADER)
            )
        if _normalized_target_session_identity(canonical_summary.get("target_session_identity")) is None:
            canonical_summary["target_session_identity"] = target_session_identity_from_headers(
                _payload_headers(ingress_mail_payload)
            )
        if _normalized_text(canonical_summary.get("ingress_type")) is None:
            canonical_summary["ingress_type"] = (
                "direct_bridge" if _payload_direct_header(ingress_mail_payload) == "1" else "mail"
            )

    terminal_mail_path, terminal_mail_payload, terminal_mail_resolution = _select_terminal_mail_item(
        workspace.task_root,
        thread_id,
        terminal_message_id=_normalized_text(canonical_summary.get("terminal_mail_message_id")),
        terminal_subject=_normalized_text(canonical_summary.get("terminal_mail_subject")),
        message_id_source=(
            "terminal_mail_message_id"
            if canonical_outcome_source in {"canonical_summary", "session_action_closeout"}
            else "thread_state.latest_message_id"
        ),
    )
    if terminal_mail_payload is not None and _normalized_text(canonical_summary.get("terminal_mail_subject")) is None:
        canonical_summary["terminal_mail_subject"] = _payload_subject(terminal_mail_payload)

    run_result = _run_result_evidence(workspace, thread_id, resolved_task_id)

    android_record = None
    android_record_selection = "not_requested"
    if android_send_records_path is not None:
        android_records = load_android_taskmail_send_records(android_send_records_path)
        android_record, android_record_selection = select_android_taskmail_send_record(
            android_records,
            request_id=_normalized_text(canonical_summary.get("request_id")),
            ingress_message_id=_normalized_text(canonical_summary.get("ingress_message_id")),
            action_type=_normalized_text(canonical_summary.get("action_type")),
            target_session_identity=(
                _normalized_target_session_identity(canonical_summary.get("target_session_identity"))
                or _thread_state_target_session_identity(state)
            ),
            ingress_type=_normalized_text(canonical_summary.get("ingress_type")),
            sender_account_id=_normalized_text(sender_account_id),
            repo_path=_normalized_text(state.repo_path),
            workdir=_normalized_text(state.workdir),
            anchor_timestamp=_normalized_text(state.created_at) or (
                _normalized_text(run_result.get("started_at")) if run_result is not None else None
            ),
        )

    thread_root = workspace.thread_dir(thread_id)
    outbound_delivery_attempts = _outbound_delivery_attempts_evidence(workspace, thread_id, resolved_task_id)
    relay_packet_store = _relay_packet_store_evidence(
        workspace.task_root,
        packet_id=_normalized_text(canonical_summary.get("packet_id")),
        relay_state_dir=relay_state_dir,
    )

    bundle = {
        "version": 1,
        "generated_at": _timestamp(),
        "thread_id": thread_id,
        "task_id": resolved_task_id,
        "run_dir": {
            "path": str(run_dir),
            "thread_relative_path": _relative_path(workspace.thread_dir(thread_id), run_dir),
        },
        "bundle_presence": {
            "pc_canonical_outcome": canonical_outcome_source != "thread_state_fallback",
            "pc_terminal_mail": terminal_mail_payload is not None,
            "android_latest_send_evidence": android_record is not None,
            "android_terminal_summary": _normalized_text(android_last_summary) is not None,
            "pc_outbound_delivery_attempts": outbound_delivery_attempts is not None,
            "pc_relay_packet_store": relay_packet_store is not None,
            "pc_session_action_closeout": session_action_closeout_payload is not None,
        },
        "pc_canonical_outcome": {
            "source": canonical_outcome_source,
            "resolution": canonical_outcome_resolution,
            "path": str(canonical_outcome_path) if canonical_outcome_path is not None else None,
            "thread_relative_path": _relative_path(workspace.thread_dir(thread_id), canonical_outcome_path),
            "ingress_type": _normalized_text(canonical_summary.get("ingress_type")),
            "request_id": _normalized_text(canonical_summary.get("request_id")),
            "ingress_message_id": _normalized_text(canonical_summary.get("ingress_message_id")),
            "packet_id": _normalized_text(canonical_summary.get("packet_id")),
            "receipt_id": _normalized_text(canonical_summary.get("receipt_id")),
            "action_type": _normalized_text(canonical_summary.get("action_type")),
            "target_session_identity": _normalized_target_session_identity(
                canonical_summary.get("target_session_identity")
            ),
            "last_summary": _normalized_text(canonical_summary.get("last_summary")),
            "terminal_mail_message_id": _normalized_text(canonical_summary.get("terminal_mail_message_id")),
            "terminal_mail_subject": _normalized_text(canonical_summary.get("terminal_mail_subject")),
        },
        "pc_terminal_mail": {
            "resolution": terminal_mail_resolution,
            "path": str(terminal_mail_path) if terminal_mail_path is not None else None,
            "thread_relative_path": _relative_path(workspace.thread_dir(thread_id), terminal_mail_path),
            "message_id": _payload_message_id(terminal_mail_payload) if terminal_mail_payload is not None else None,
            "subject": _payload_subject(terminal_mail_payload) if terminal_mail_payload is not None else None,
            "status_label": _status_label(_payload_subject(terminal_mail_payload))
            if terminal_mail_payload is not None
            else None,
        },
        "android_latest_send_evidence": {
            "source_path": str(android_send_records_path) if android_send_records_path is not None else None,
            "selection": android_record_selection,
            "action_type": _normalized_action_type(android_record.get("action_type")) if android_record is not None else None,
            "target_session_identity": (
                _normalized_target_session_identity(android_record.get("target_session_identity"))
                if android_record is not None
                else None
            ),
            "sender_account_id": android_record.get("sender_account_id") if android_record is not None else None,
            "recorded_at": android_record.get("recorded_at") if android_record is not None else None,
            "repo_path": android_record.get("repo_path") if android_record is not None else None,
            "workdir": android_record.get("workdir") if android_record is not None else None,
            "bootstrap_status": android_record.get("bootstrap_status") if android_record is not None else None,
            "outcome": android_record.get("outcome") if android_record is not None else None,
            "switch_gate": android_record.get("switch_gate") if android_record is not None else None,
            "request_id": android_record.get("request_id") if android_record is not None else None,
            "receipt_id": android_record.get("receipt_id") if android_record is not None else None,
            "transport_message_id": android_record.get("transport_message_id") if android_record is not None else None,
            "fallback_reason": android_record.get("fallback_reason") if android_record is not None else None,
            "error_message": android_record.get("error_message") if android_record is not None else None,
        },
        "android_terminal_summary": {
            "last_summary": _normalized_text(android_last_summary),
        },
        "pc_supporting_evidence": {
            "thread_state": {
                "path": str(workspace.thread_state_path(thread_id)),
                "thread_relative_path": _relative_path(thread_root, workspace.thread_state_path(thread_id)),
                "status": _normalized_text(state.status),
                "lifecycle": _normalized_text(state.lifecycle),
                "root_message_id": _normalized_text(state.root_message_id),
                "latest_message_id": _normalized_text(state.latest_message_id),
                "current_task_id": _normalized_text(state.current_task_id),
                "last_summary": _normalized_text(state.last_summary),
            },
            "run_result": run_result,
            "pc_ingress_mail": _mail_item_evidence(
                thread_root,
                ingress_mail_path,
                ingress_mail_payload,
                resolution=ingress_mail_resolution,
            ),
            "session_action_closeout": _session_action_closeout_evidence(
                thread_root,
                session_action_closeout_path,
                session_action_closeout_payload,
                resolution=session_action_closeout_resolution,
            ),
            "outbound_delivery_attempts": outbound_delivery_attempts,
            "relay_packet_store": relay_packet_store,
        },
    }
    bundle["same_run_bind"] = _evaluate_same_run_bind(
        canonical_summary,
        android_record,
        android_last_summary=android_last_summary,
    )
    return bundle


def write_taskmail_daily_closeout_bundle(
    thread_id: str,
    task_root: str | Path,
    *,
    task_id: str | None = None,
    android_send_records_path: str | Path | None = None,
    sender_account_id: str | None = None,
    android_last_summary: str | None = None,
    relay_state_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    bundle = build_taskmail_daily_closeout_bundle(
        thread_id,
        task_root,
        task_id=task_id,
        android_send_records_path=android_send_records_path,
        sender_account_id=sender_account_id,
        android_last_summary=android_last_summary,
        relay_state_dir=relay_state_dir,
    )
    resolved_output_path = (
        Path(output_path)
        if output_path is not None
        else default_taskmail_daily_closeout_bundle_path(
            task_root,
            thread_id,
            bundle["task_id"],
        )
    )
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return resolved_output_path
