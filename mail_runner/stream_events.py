"""Helpers for persisted per-run stream events."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .workspace import WorkspaceManager

STREAM_EVENTS_FILENAME = "stream.events.jsonl"


@dataclass(slots=True)
class StreamEvent:
    ts: str
    seq: int
    thread_id: str
    task_id: str
    backend: str
    backend_transport: str
    kind: str
    text: str | None = None
    delta: str | None = None
    item_type: str | None = None
    status: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


def stream_events_path(task_root: str | Path, thread_id: str, task_id: str) -> Path:
    workspace = WorkspaceManager(task_root)
    return workspace.run_file_path(thread_id, task_id, STREAM_EVENTS_FILENAME)


def load_stream_events(path: str | Path) -> list[StreamEvent]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    events: list[StreamEvent] = []
    for line_no, raw_line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Stream event line {line_no} must be a JSON object")
        events.append(_coerce_stream_event(payload, line_no))
    return events


def write_stream_events(path: str | Path, events: Iterable[StreamEvent]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = [_render_stream_event(event) for event in events]
    payload = ("\n".join(rendered) + "\n") if rendered else ""
    file_path.write_text(payload, encoding="utf-8")


def append_stream_event(path: str | Path, event: StreamEvent) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(_render_stream_event(event))
        handle.write("\n")


def _coerce_stream_event(payload: dict[str, object], line_no: int) -> StreamEvent:
    raw_payload = payload.get("payload")
    normalized_payload = raw_payload if isinstance(raw_payload, dict) else {}
    return StreamEvent(
        ts=_required_text(payload.get("ts"), "ts", line_no),
        seq=_required_int(payload.get("seq"), "seq", line_no),
        thread_id=_required_text(payload.get("thread_id"), "thread_id", line_no),
        task_id=_required_text(payload.get("task_id"), "task_id", line_no),
        backend=_required_text(payload.get("backend"), "backend", line_no),
        backend_transport=_required_text(payload.get("backend_transport"), "backend_transport", line_no),
        kind=_required_text(payload.get("kind"), "kind", line_no),
        text=_optional_text(payload.get("text")),
        delta=_optional_text(payload.get("delta")),
        item_type=_optional_text(payload.get("item_type")),
        status=_optional_text(payload.get("status")),
        payload={str(key): value for key, value in normalized_payload.items()},
    )


def _render_stream_event(event: StreamEvent) -> str:
    return json.dumps(asdict(event), ensure_ascii=False)


def _required_text(value: object, field_name: str, line_no: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Stream event line {line_no} field '{field_name}' must be a non-empty string")
    return value.strip()


def _required_int(value: object, field_name: str, line_no: int) -> int:
    if not isinstance(value, int):
        raise ValueError(f"Stream event line {line_no} field '{field_name}' must be an integer")
    return value


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
