"""Command ledger stores for the PC control plane."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from ..download_ref import normalize_download_ref


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_optional_chunk_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value if value else None


def _validate_optional_download_ref(value: Any, field_name: str) -> dict[str, Any] | None:
    return normalize_download_ref(value, field_name=field_name)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict")
    return dict(value)


def _require_optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _require_mapping(value, field_name)


def _require_optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _load_json(path: Path, default):
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _event_semantics(event: "PcCommandEventRecord") -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "trace_id": event.trace_id,
        "summary": event.summary,
        "effective_execution": event.effective_execution,
        "payload": event.payload,
    }


def _result_semantics(result: "PcCommandResultRecord") -> dict[str, Any]:
    return {
        "result_id": result.result_id,
        "trace_id": result.trace_id,
        "final_status": result.final_status,
        "summary": result.summary,
        "structured_payload": result.structured_payload,
        "effective_execution": result.effective_execution,
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


def _output_chunk_semantics(chunk: "PcOutputChunkRecord") -> dict[str, Any]:
    return {
        "output_chunk_id": chunk.output_chunk_id,
        "trace_id": chunk.trace_id,
        "stream_id": chunk.stream_id,
        "stream_id_source": chunk.stream_id_source,
        "seq": chunk.seq,
        "kind": chunk.kind,
        "text": chunk.text,
        "delta": chunk.delta,
        "item_type": chunk.item_type,
        "status": chunk.status,
    }


def _artifact_manifest_semantics(manifest: "PcArtifactManifestRecord") -> dict[str, Any]:
    return {
        "manifest_id": manifest.manifest_id,
        "trace_id": manifest.trace_id,
        "artifacts_root": manifest.artifacts_root,
        "source": manifest.source,
        "artifacts": manifest.artifacts,
    }


@dataclass(slots=True)
class PcCommandRecord:
    pc_id: str
    workspace_id: str
    command_id: str
    command_type: str
    trace_id: str
    dispatch_message_id: str
    created_at: str
    execution_policy: dict[str, Any]
    command_payload: dict[str, Any]
    session_id: str | None = None
    status: str = "queued"
    dispatched_connection_epoch: int | None = None
    dispatched_at: str | None = None
    ack_status: str | None = None
    queue_position: int | None = None
    reason: str | None = None
    error_code: str | None = None
    ack_message_id: str | None = None
    acked_at: str | None = None
    latest_event_type: str | None = None
    latest_event_at: str | None = None
    final_status: str | None = None
    events: list["PcCommandEventRecord"] | list[dict[str, Any]] = field(default_factory=list)
    result: "PcCommandResultRecord | dict[str, Any] | None" = None
    output_chunks: list["PcOutputChunkRecord"] | list[dict[str, Any]] = field(default_factory=list)
    artifact_manifest: "PcArtifactManifestRecord | dict[str, Any] | None" = None

    def __post_init__(self) -> None:
        self.pc_id = _require_text(self.pc_id, "pc_id")
        self.workspace_id = _require_text(self.workspace_id, "workspace_id")
        self.command_id = _require_text(self.command_id, "command_id")
        self.command_type = _require_text(self.command_type, "command_type")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        self.dispatch_message_id = _require_text(self.dispatch_message_id, "dispatch_message_id")
        self.created_at = _require_text(self.created_at, "created_at")
        self.execution_policy = _require_mapping(self.execution_policy, "execution_policy")
        self.command_payload = _require_mapping(self.command_payload, "command_payload")
        self.session_id = _require_optional_text(self.session_id, "session_id")
        self.status = _require_text(self.status, "status")
        if self.status not in {"queued", "dispatched", "acknowledged"}:
            raise ValueError("status must be one of: queued, dispatched, acknowledged")
        self.dispatched_connection_epoch = _require_optional_int(
            self.dispatched_connection_epoch,
            "dispatched_connection_epoch",
            minimum=1,
        )
        self.dispatched_at = _require_optional_text(self.dispatched_at, "dispatched_at")
        self.ack_status = _require_optional_text(self.ack_status, "ack_status")
        self.queue_position = _require_optional_int(self.queue_position, "queue_position", minimum=1)
        self.reason = _require_optional_text(self.reason, "reason")
        self.error_code = _require_optional_text(self.error_code, "error_code")
        self.ack_message_id = _require_optional_text(self.ack_message_id, "ack_message_id")
        self.acked_at = _require_optional_text(self.acked_at, "acked_at")
        self.latest_event_type = _require_optional_text(self.latest_event_type, "latest_event_type")
        self.latest_event_at = _require_optional_text(self.latest_event_at, "latest_event_at")
        self.final_status = _require_optional_text(self.final_status, "final_status")
        normalized_events: list[PcCommandEventRecord] = []
        for item in self.events:
            if isinstance(item, PcCommandEventRecord):
                normalized_events.append(item)
            elif isinstance(item, dict):
                normalized_events.append(PcCommandEventRecord(**item))
            else:
                raise ValueError("events must contain PcCommandEventRecord-compatible entries")
        self.events = normalized_events
        if self.result is not None and not isinstance(self.result, PcCommandResultRecord):
            if isinstance(self.result, dict):
                self.result = PcCommandResultRecord(**self.result)
            else:
                raise ValueError("result must be a PcCommandResultRecord-compatible mapping")
        normalized_output_chunks: list[PcOutputChunkRecord] = []
        for item in self.output_chunks:
            if isinstance(item, PcOutputChunkRecord):
                normalized_output_chunks.append(item)
            elif isinstance(item, dict):
                normalized_output_chunks.append(PcOutputChunkRecord(**item))
            else:
                raise ValueError("output_chunks must contain PcOutputChunkRecord-compatible entries")
        self.output_chunks = sorted(normalized_output_chunks, key=lambda item: (item.stream_id, item.seq))
        if self.artifact_manifest is not None and not isinstance(self.artifact_manifest, PcArtifactManifestRecord):
            if isinstance(self.artifact_manifest, dict):
                self.artifact_manifest = PcArtifactManifestRecord(**self.artifact_manifest)
            else:
                raise ValueError("artifact_manifest must be a PcArtifactManifestRecord-compatible mapping")

    @property
    def command_key(self) -> str:
        return f"{self.pc_id}::{self.command_id}"


class PcCommandConflictError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


class PcCommandUnknownError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        super().__init__(self.message)


@dataclass(slots=True)
class PcCommandEventRecord:
    event_id: str
    event_type: str
    event_message_id: str
    trace_id: str
    connection_epoch: int
    sent_at: str
    summary: str | None = None
    effective_execution: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.event_id = _require_text(self.event_id, "event_id")
        self.event_type = _require_text(self.event_type, "event_type")
        if self.event_type not in {"queued", "accepted", "running", "awaiting_user_input", "paused", "done", "failed", "killed"}:
            raise ValueError("event_type must be a supported canonical pc-control event")
        self.event_message_id = _require_text(self.event_message_id, "event_message_id")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        if not isinstance(self.connection_epoch, int) or self.connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.summary = _require_optional_text(self.summary, "summary")
        self.effective_execution = _require_optional_mapping(self.effective_execution, "effective_execution")
        self.payload = _require_mapping(self.payload, "payload")


@dataclass(slots=True)
class PcCommandResultRecord:
    result_id: str
    result_message_id: str
    trace_id: str
    connection_epoch: int
    sent_at: str
    final_status: str
    summary: str
    structured_payload: dict[str, Any]
    effective_execution: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        self.result_id = _require_text(self.result_id, "result_id")
        self.result_message_id = _require_text(self.result_message_id, "result_message_id")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        if not isinstance(self.connection_epoch, int) or self.connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.final_status = _require_text(self.final_status, "final_status")
        if self.final_status not in {"awaiting_user_input", "paused", "done", "failed", "killed"}:
            raise ValueError("final_status must be a supported canonical pc-control result status")
        self.summary = _require_text(self.summary, "summary")
        self.structured_payload = _require_mapping(self.structured_payload, "structured_payload")
        self.effective_execution = _require_mapping(self.effective_execution, "effective_execution")
        self.error_code = _require_optional_text(self.error_code, "error_code")
        self.error_message = _require_optional_text(self.error_message, "error_message")


@dataclass(slots=True)
class PcOutputChunkRecord:
    output_chunk_id: str
    output_message_id: str
    trace_id: str
    connection_epoch: int
    sent_at: str
    stream_id: str
    seq: int
    kind: str
    text: str | None = None
    delta: str | None = None
    item_type: str | None = None
    status: str | None = None
    stream_id_source: str | None = None

    def __post_init__(self) -> None:
        self.output_chunk_id = _require_text(self.output_chunk_id, "output_chunk_id")
        self.output_message_id = _require_text(self.output_message_id, "output_message_id")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        if not isinstance(self.connection_epoch, int) or self.connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.stream_id = _require_text(self.stream_id, "stream_id")
        self.stream_id_source = _require_optional_text(self.stream_id_source, "stream_id_source")
        if not isinstance(self.seq, int) or self.seq <= 0:
            raise ValueError("seq must be a positive integer")
        self.kind = _require_text(self.kind, "kind")
        self.text = _require_optional_chunk_text(self.text, "text")
        self.delta = _require_optional_chunk_text(self.delta, "delta")
        if self.text is None and self.delta is None:
            raise ValueError("text or delta is required")
        self.item_type = _require_optional_text(self.item_type, "item_type")
        self.status = _require_optional_text(self.status, "status")


@dataclass(slots=True)
class PcArtifactManifestRecord:
    manifest_id: str
    manifest_message_id: str
    trace_id: str
    connection_epoch: int
    sent_at: str
    artifacts: list[dict[str, Any]]
    artifacts_root: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        self.manifest_id = _require_text(self.manifest_id, "manifest_id")
        self.manifest_message_id = _require_text(self.manifest_message_id, "manifest_message_id")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        if not isinstance(self.connection_epoch, int) or self.connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        self.sent_at = _require_text(self.sent_at, "sent_at")
        self.artifacts_root = _require_optional_text(self.artifacts_root, "artifacts_root")
        self.source = _require_optional_text(self.source, "source")
        if not isinstance(self.artifacts, list):
            raise ValueError("artifacts must be a list")
        normalized_artifacts: list[dict[str, Any]] = []
        for index, item in enumerate(self.artifacts):
            if not isinstance(item, dict):
                raise ValueError("artifacts must contain mapping items")
            item_field = f"artifacts[{index}]"
            kind = _require_text(item.get("kind"), f"{item_field}.kind")
            if kind not in {"image", "file"}:
                raise ValueError("artifact kind must be one of: image, file")
            normalized_artifacts.append(
                {
                    "artifact_id": _require_text(item.get("artifact_id"), f"{item_field}.artifact_id"),
                    "kind": kind,
                    "name": _require_text(item.get("name"), f"{item_field}.name"),
                    "content_type": _require_text(item.get("content_type"), f"{item_field}.content_type"),
                    "size": _require_optional_int(item.get("size"), f"{item_field}.size", minimum=0),
                    "download_ref": _validate_optional_download_ref(item.get("download_ref"), f"{item_field}.download_ref"),
                    "download_ref_source": _require_optional_text(
                        item.get("download_ref_source"),
                        f"{item_field}.download_ref_source",
                    ),
                }
            )
        self.artifacts = normalized_artifacts


class InMemoryPcCommandStore:
    def __init__(self) -> None:
        self._records: dict[str, PcCommandRecord] = {}
        self._lock = Lock()

    def upsert_dispatch(self, record: PcCommandRecord) -> tuple[PcCommandRecord, bool]:
        with self._lock:
            existing = self._records.get(record.command_key)
            if existing is None:
                self._records[record.command_key] = record
                return record, True
            immutable_keys = (
                "pc_id",
                "workspace_id",
                "command_id",
                "command_type",
                "session_id",
                "execution_policy",
                "command_payload",
            )
            for key in immutable_keys:
                if getattr(existing, key) != getattr(record, key):
                    raise PcCommandConflictError(
                        "command_id_conflict",
                        f"command_id already exists with different payload: {record.command_key}",
                    )
            return existing, False

    def collect_pending_dispatches(
        self,
        *,
        pc_id: str,
        connection_epoch: int,
        dispatched_at: str,
        limit: int = 50,
    ) -> list[PcCommandRecord]:
        normalized_pc_id = _require_text(pc_id, "pc_id")
        normalized_dispatched_at = _require_text(dispatched_at, "dispatched_at")
        if not isinstance(connection_epoch, int) or connection_epoch <= 0:
            raise ValueError("connection_epoch must be a positive integer")
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            selected: list[PcCommandRecord] = []
            for record in sorted(self._records.values(), key=lambda item: (item.created_at, item.command_id)):
                if record.pc_id != normalized_pc_id or record.ack_status is not None:
                    continue
                if record.dispatched_connection_epoch == connection_epoch:
                    continue
                record.status = "dispatched"
                record.dispatched_connection_epoch = connection_epoch
                record.dispatched_at = normalized_dispatched_at
                selected.append(record)
                if len(selected) >= limit:
                    break
            return [PcCommandRecord(**asdict(item)) for item in selected]

    def record_ack(
        self,
        *,
        pc_id: str,
        command_id: str,
        ack_status: str,
        ack_message_id: str,
        acked_at: str,
        queue_position: int | None = None,
        reason: str | None = None,
        error_code: str | None = None,
    ) -> PcCommandRecord:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        normalized_ack_status = _require_text(ack_status, "ack_status")
        normalized_ack_message_id = _require_text(ack_message_id, "ack_message_id")
        normalized_acked_at = _require_text(acked_at, "acked_at")
        normalized_queue_position = _require_optional_int(queue_position, "queue_position", minimum=1)
        normalized_reason = _require_optional_text(reason, "reason")
        normalized_error_code = _require_optional_text(error_code, "error_code")
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                raise PcCommandUnknownError("unknown_command", f"command_id not found: {command_key}")
            if existing.ack_status is not None:
                if (
                    existing.ack_status != normalized_ack_status
                    or existing.queue_position != normalized_queue_position
                    or existing.reason != normalized_reason
                    or existing.error_code != normalized_error_code
                ):
                    raise PcCommandConflictError(
                        "ack_conflict",
                        f"command_ack does not match the existing ack for {command_key}",
                    )
                return PcCommandRecord(**asdict(existing))
            existing.status = "acknowledged"
            existing.ack_status = normalized_ack_status
            existing.queue_position = normalized_queue_position
            existing.reason = normalized_reason
            existing.error_code = normalized_error_code
            existing.ack_message_id = normalized_ack_message_id
            existing.acked_at = normalized_acked_at
            return PcCommandRecord(**asdict(existing))

    def record_event(
        self,
        *,
        pc_id: str,
        command_id: str,
        event: PcCommandEventRecord,
    ) -> tuple[PcCommandRecord, bool]:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        if not isinstance(event, PcCommandEventRecord):
            raise ValueError("event must be a PcCommandEventRecord")
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                raise PcCommandUnknownError("unknown_command", f"command_id not found: {command_key}")
            for recorded in existing.events:
                if recorded.event_id != event.event_id:
                    continue
                if _event_semantics(recorded) != _event_semantics(event):
                    raise PcCommandConflictError(
                        "event_conflict",
                        f"event_id does not match the existing event for {command_key}: {event.event_id}",
                    )
                return PcCommandRecord(**asdict(existing)), False
            existing.events.append(event)
            existing.latest_event_type = event.event_type
            existing.latest_event_at = event.sent_at
            return PcCommandRecord(**asdict(existing)), True

    def record_result(
        self,
        *,
        pc_id: str,
        command_id: str,
        result: PcCommandResultRecord,
    ) -> tuple[PcCommandRecord, bool]:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        if not isinstance(result, PcCommandResultRecord):
            raise ValueError("result must be a PcCommandResultRecord")
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                raise PcCommandUnknownError("unknown_command", f"command_id not found: {command_key}")
            if existing.result is not None:
                if _result_semantics(existing.result) != _result_semantics(result):
                    raise PcCommandConflictError(
                        "result_conflict",
                        f"result does not match the existing canonical result for {command_key}",
                    )
                return PcCommandRecord(**asdict(existing)), False
            existing.result = result
            existing.final_status = result.final_status
            existing.latest_event_type = result.final_status
            existing.latest_event_at = result.sent_at
            return PcCommandRecord(**asdict(existing)), True

    def record_output_chunk(
        self,
        *,
        pc_id: str,
        command_id: str,
        chunk: PcOutputChunkRecord,
    ) -> tuple[PcCommandRecord, bool]:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        if not isinstance(chunk, PcOutputChunkRecord):
            raise ValueError("chunk must be a PcOutputChunkRecord")
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                raise PcCommandUnknownError("unknown_command", f"command_id not found: {command_key}")
            for recorded in existing.output_chunks:
                if recorded.stream_id != chunk.stream_id or recorded.seq != chunk.seq:
                    continue
                if _output_chunk_semantics(recorded) != _output_chunk_semantics(chunk):
                    raise PcCommandConflictError(
                        "output_chunk_conflict",
                        f"output_chunk does not match the existing stream_id/seq for {command_key}: {chunk.stream_id}/{chunk.seq}",
                    )
                return PcCommandRecord(**asdict(existing)), False
            existing.output_chunks.append(chunk)
            existing.output_chunks = sorted(existing.output_chunks, key=lambda item: (item.stream_id, item.seq))
            return PcCommandRecord(**asdict(existing)), True

    def record_artifact_manifest(
        self,
        *,
        pc_id: str,
        command_id: str,
        manifest: PcArtifactManifestRecord,
    ) -> tuple[PcCommandRecord, bool]:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        if not isinstance(manifest, PcArtifactManifestRecord):
            raise ValueError("manifest must be a PcArtifactManifestRecord")
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                raise PcCommandUnknownError("unknown_command", f"command_id not found: {command_key}")
            if existing.artifact_manifest is not None:
                if _artifact_manifest_semantics(existing.artifact_manifest) != _artifact_manifest_semantics(manifest):
                    raise PcCommandConflictError(
                        "artifact_manifest_conflict",
                        f"artifact_manifest does not match the existing canonical manifest for {command_key}",
                    )
                return PcCommandRecord(**asdict(existing)), False
            existing.artifact_manifest = manifest
            return PcCommandRecord(**asdict(existing)), True

    def get_command(self, pc_id: str, command_id: str) -> PcCommandRecord | None:
        command_key = f"{_require_text(pc_id, 'pc_id')}::{_require_text(command_id, 'command_id')}"
        with self._lock:
            existing = self._records.get(command_key)
            if existing is None:
                return None
            return PcCommandRecord(**asdict(existing))

    def list_commands(self, *, pc_id: str | None = None) -> list[PcCommandRecord]:
        normalized_pc_id = _require_text(pc_id, "pc_id") if pc_id is not None else None
        with self._lock:
            items = [PcCommandRecord(**asdict(item)) for item in self._records.values()]
        if normalized_pc_id is not None:
            items = [item for item in items if item.pc_id == normalized_pc_id]
        return sorted(items, key=lambda item: (item.pc_id, item.created_at, item.command_id))

    def count(self) -> int:
        with self._lock:
            return len(self._records)


class PersistentPcCommandStore(InMemoryPcCommandStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._records: dict[str, PcCommandRecord] = {}
        self._load()

    def _load(self) -> None:
        payload = _load_json(self._path, default={"commands": []})
        commands = payload.get("commands", []) if isinstance(payload, dict) else []
        for item in commands:
            record = PcCommandRecord(**item)
            self._records[record.command_key] = record

    def _save(self) -> None:
        payload = {
            "version": 1,
            "commands": [asdict(item) for item in self.list_commands()],
        }
        _write_json(self._path, payload)

    def upsert_dispatch(self, record: PcCommandRecord) -> tuple[PcCommandRecord, bool]:
        updated = super().upsert_dispatch(record)
        self._save()
        return updated

    def collect_pending_dispatches(self, **kwargs) -> list[PcCommandRecord]:
        items = super().collect_pending_dispatches(**kwargs)
        self._save()
        return items

    def record_ack(self, **kwargs) -> PcCommandRecord:
        record = super().record_ack(**kwargs)
        self._save()
        return record

    def record_event(self, **kwargs) -> tuple[PcCommandRecord, bool]:
        record = super().record_event(**kwargs)
        self._save()
        return record

    def record_result(self, **kwargs) -> tuple[PcCommandRecord, bool]:
        record = super().record_result(**kwargs)
        self._save()
        return record

    def record_output_chunk(self, **kwargs) -> tuple[PcCommandRecord, bool]:
        record = super().record_output_chunk(**kwargs)
        self._save()
        return record

    def record_artifact_manifest(self, **kwargs) -> tuple[PcCommandRecord, bool]:
        record = super().record_artifact_manifest(**kwargs)
        self._save()
        return record
