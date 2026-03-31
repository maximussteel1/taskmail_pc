"""Project durable Android-facing history rounds from thread snapshots and run results."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifact_resolver import resolve_run_artifacts
from ..models import RunResult, SessionState, TaskSnapshot, ThreadState
from ..pc_control_plane_projection import project_artifact_manifest
from ..workspace import WorkspaceManager

_INPUT_ATTACHMENT_SUMMARY_MARKER = "New incoming attachments materialized in workdir:"


@dataclass(slots=True)
class _RoundSource:
    task_id: str
    snapshot: TaskSnapshot | None = None
    result: RunResult | None = None


def build_android_history_rounds(
    *,
    session_state: SessionState,
    thread_state: ThreadState,
    task_root: str | Path,
) -> list[dict[str, Any]]:
    workspace = WorkspaceManager(task_root)
    round_sources = _load_round_sources(
        session_state=session_state,
        thread_state=thread_state,
        workspace=workspace,
    )
    if not round_sources:
        return []

    resolved_task_root = Path(task_root)
    chronological_sources = sorted(round_sources.values(), key=_round_order_key)
    previous_input_attachment_keys: set[str] = set()
    rounds: list[dict[str, Any]] = []
    for index, round_source in enumerate(chronological_sources, start=1):
        input_text = _snapshot_input_text(round_source.snapshot)
        input_attachments, previous_input_attachment_keys = _snapshot_input_attachments(
            snapshot=round_source.snapshot,
            task_root=resolved_task_root,
            previous_keys=previous_input_attachment_keys,
            input_text=input_text,
        )
        rounds.append(
            _build_round_payload(
                round_source=round_source,
                session_state=session_state,
                thread_state=thread_state,
                task_root=resolved_task_root,
                round_number=index,
                input_text=input_text,
                input_attachments=input_attachments,
            )
        )
    rounds.reverse()
    return rounds


def _load_round_sources(
    *,
    session_state: SessionState,
    thread_state: ThreadState,
    workspace: WorkspaceManager,
) -> dict[str, _RoundSource]:
    round_sources: dict[str, _RoundSource] = {}

    def upsert_snapshot(relative_path: str | None) -> None:
        if not relative_path:
            return
        try:
            snapshot = workspace.load_snapshot(thread_state.thread_id, relative_path)
        except FileNotFoundError:
            return
        round_sources.setdefault(snapshot.task_id, _RoundSource(task_id=snapshot.task_id)).snapshot = snapshot

    def upsert_result(relative_path: str | None) -> None:
        if not relative_path:
            return
        try:
            result = workspace.load_run_result(thread_state.thread_id, relative_path)
        except FileNotFoundError:
            return
        round_sources.setdefault(result.task_id, _RoundSource(task_id=result.task_id)).result = result
        upsert_snapshot(f"snapshots/{result.task_id}.json")

    upsert_snapshot(session_state.last_task_snapshot_file)
    upsert_snapshot(session_state.queued_snapshot_file)
    for history_file in session_state.history_files:
        upsert_result(history_file)
    return round_sources


def _round_order_key(round_source: _RoundSource) -> tuple[str, str]:
    timestamp = (
        round_source.result.finished_at
        or round_source.result.started_at
        if round_source.result is not None
        else None
    ) or (
        round_source.snapshot.updated_at
        or round_source.snapshot.created_at
        if round_source.snapshot is not None
        else None
    ) or ""
    return timestamp, round_source.task_id


def _build_round_payload(
    *,
    round_source: _RoundSource,
    session_state: SessionState,
    thread_state: ThreadState,
    task_root: Path,
    round_number: int,
    input_text: str | None,
    input_attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshot = round_source.snapshot
    result = round_source.result
    created_at = (
        result.finished_at
        or result.started_at
        if result is not None
        else None
    ) or (
        snapshot.updated_at
        or snapshot.created_at
        if snapshot is not None
        else None
    ) or (
        session_state.last_progress_at
        or session_state.updated_at
    )
    status = _round_status_value(
        task_id=round_source.task_id,
        session_state=session_state,
        result=result,
    )

    result_text = _round_result_text(
        task_id=round_source.task_id,
        session_state=session_state,
        thread_state=thread_state,
        result=result,
        task_root=task_root,
    )
    process_items = _build_process_items(
        task_id=round_source.task_id,
        session_state=session_state,
        result=result,
        created_at=created_at,
    )
    result_attachments = _result_attachments(result=result, thread_state=thread_state, task_root=task_root)

    return {
        "round_id": f"hist_round_{round_source.task_id}",
        "round_number": round_number,
        "created_at": created_at,
        "status": status,
        "speaker_label": _backend_label(session_state.backend),
        "input": {
            "text": input_text,
            "attachments": input_attachments,
        },
        "process": {
            "items": process_items,
        },
        "result": {
            "text": result_text,
            "attachments": result_attachments,
        },
    }


def _round_status_value(
    *,
    task_id: str,
    session_state: SessionState,
    result: RunResult | None,
) -> str:
    if result is not None:
        if result.status == "success":
            return "done"
        if result.status == "awaiting_user_input":
            return "waiting_user"
        return result.status
    if session_state.current_task_id == task_id:
        return session_state.status
    if session_state.queued_task_id == task_id:
        return "queued"
    return "done"


def _backend_label(backend: str) -> str:
    normalized = str(backend or "").strip()
    if not normalized:
        return "TaskMail"
    if normalized.lower() == "opencode":
        return "OpenCode"
    return normalized[:1].upper() + normalized[1:]


def _snapshot_input_text(snapshot: TaskSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    normalized_turn_text = (snapshot.turn_text or "").strip()
    if normalized_turn_text:
        return normalized_turn_text
    normalized_task_text = (snapshot.task_text or "").strip()
    return normalized_task_text or None


def _round_result_text(
    *,
    task_id: str,
    session_state: SessionState,
    thread_state: ThreadState,
    result: RunResult | None,
    task_root: Path,
) -> str:
    if result is not None:
        visible_output_text = _load_result_visible_output_text(result=result, task_root=task_root)
        if visible_output_text is not None:
            return visible_output_text
        summary_text = _load_result_summary_text(result=result, task_root=task_root)
        if summary_text:
            return summary_text
        if result.error_message:
            return result.error_message
        return _humanize_status(result.status)
    if session_state.current_task_id == task_id:
        return (
            (session_state.last_summary or "").strip()
            or (thread_state.last_summary or "").strip()
            or _humanize_status(session_state.status)
        )
    return "No stable result was captured for this round."


def _load_result_visible_output_text(*, result: RunResult, task_root: Path) -> str | None:
    output_path = task_root / result.thread_id / result.stdout_file
    if not output_path.exists():
        return None
    text = output_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text if text else None


def _load_result_summary_text(*, result: RunResult, task_root: Path) -> str | None:
    if not result.summary_file:
        return None
    summary_path = task_root / result.thread_id / result.summary_file
    if not summary_path.exists():
        return None
    text = summary_path.read_text(encoding="utf-8").strip()
    return text or None


def _build_process_items(
    *,
    task_id: str,
    session_state: SessionState,
    result: RunResult | None,
    created_at: str,
) -> list[dict[str, Any]]:
    if result is not None and result.status == "awaiting_user_input":
        prompt_text = _pending_question_prompt_text(result)
        if prompt_text:
            return [
                {
                    "item_id": f"hist_process_{task_id}_awaiting",
                    "kind": "system",
                    "created_at": created_at,
                    "updated_at": created_at,
                    "status": "completed",
                    "text": prompt_text,
                }
            ]

    if result is None and session_state.current_task_id == task_id:
        summary = (session_state.last_summary or "").strip()
        if summary:
            return [
                {
                    "item_id": f"hist_process_{task_id}_{session_state.status}",
                    "kind": "system",
                    "created_at": created_at,
                    "updated_at": created_at,
                    "status": "streaming",
                    "text": summary,
                }
            ]

    return []


def _pending_question_prompt_text(result: RunResult) -> str | None:
    if result.pending_questions:
        if len(result.pending_questions) == 1:
            return result.pending_questions[0].question_text
        return f"Need {len(result.pending_questions)} answers before continuing."
    if result.question_text:
        return result.question_text
    if result.question_set_id and result.pending_choices:
        return f"Need one answer for question set {result.question_set_id}."
    return None


def _normalize_attachment_path_key(path: Path) -> str:
    return str(path).replace("\\", "/").lower()


def _snapshot_input_attachment_entries(
    *,
    snapshot: TaskSnapshot,
    task_root: Path,
) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    thread_root = task_root / snapshot.thread_id
    for index, raw_path in enumerate(snapshot.attachments, start=1):
        candidate = Path(raw_path)
        resolved_path = candidate if candidate.is_absolute() else (thread_root / candidate)
        entries.append(
            (
                _normalize_attachment_path_key(resolved_path),
                _build_attachment_payload(
                    attachment_id=f"hist_input_{snapshot.task_id}_{index}",
                    display_name=resolved_path.name if resolved_path.name else candidate.name or f"input-{index}",
                    content_type=_guess_content_type(resolved_path, None),
                    size_bytes=resolved_path.stat().st_size if resolved_path.exists() and resolved_path.is_file() else None,
                ),
            )
        )
    return entries


def _snapshot_input_attachments(
    *,
    snapshot: TaskSnapshot | None,
    task_root: Path,
    previous_keys: set[str],
    input_text: str | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    if snapshot is None:
        return [], previous_keys
    entries = _snapshot_input_attachment_entries(snapshot=snapshot, task_root=task_root)
    current_keys = {key for key, _payload in entries}
    if previous_keys and previous_keys.issubset(current_keys):
        if current_keys == previous_keys and _INPUT_ATTACHMENT_SUMMARY_MARKER in str(input_text or ""):
            filtered = entries
        else:
            filtered = [(key, payload) for key, payload in entries if key not in previous_keys]
    else:
        filtered = entries
    return [payload for _key, payload in filtered], current_keys


def _result_attachments(
    *,
    result: RunResult | None,
    thread_state: ThreadState,
    task_root: Path,
) -> list[dict[str, Any]]:
    if result is None:
        return []
    artifacts, _skipped = resolve_run_artifacts(task_root, thread_state, result)
    projected_manifest = project_artifact_manifest(task_root, result=result) or {}
    projected_items_by_artifact_id = {
        str(item.get("artifact_id") or "").strip(): item
        for item in list(projected_manifest.get("artifacts") or [])
        if isinstance(item, dict)
    }
    attachments: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts, start=1):
        artifact_path = Path(artifact.path)
        size_bytes = artifact_path.stat().st_size if artifact_path.exists() and artifact_path.is_file() else None
        projected_item = projected_items_by_artifact_id.get(artifact.artifact_id)
        attachments.append(
            _build_attachment_payload(
                attachment_id=f"hist_result_{result.task_id}_{index}",
                display_name=artifact.name,
                content_type=artifact.content_type,
                size_bytes=size_bytes,
                download_ref=projected_item.get("download_ref") if isinstance(projected_item, dict) else None,
            )
        )
    return attachments


def _build_attachment_payload(
    *,
    attachment_id: str,
    display_name: str,
    content_type: str | None,
    size_bytes: int | None,
    download_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_content_type = content_type or "application/octet-stream"
    return {
        "attachment_id": attachment_id,
        "display_name": display_name,
        "content_type": normalized_content_type,
        "size_bytes": size_bytes,
        "is_image": normalized_content_type.startswith("image/"),
        "download_ref": download_ref,
    }


def _guess_content_type(path: Path, declared: str | None) -> str:
    if declared and "/" in declared:
        return declared
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _humanize_status(status: str | None) -> str:
    normalized = str(status or "").strip()
    if not normalized:
        return "Unknown status."
    return normalized.replace("_", " ").strip().capitalize() + "."


__all__ = ["build_android_history_rounds"]
