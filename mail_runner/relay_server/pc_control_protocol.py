"""Protocol helpers for the VPS-first PC control plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PC_CONTROL_SCHEMA_VERSION = "v1"
_SERVER_MESSAGE_TYPES = {"hello_ack", "error", "command_dispatch", "output_resume_request"}
_CLIENT_MESSAGE_TYPES = {
    "pc_hello",
    "heartbeat",
    "workspace_snapshot",
    "command_ack",
    "event",
    "result",
    "output_chunk",
    "artifact_manifest",
}
_ACK_STATUSES = {"accepted", "accepted_but_queued", "rejected"}
_COMMAND_TYPES = {"new_task", "reply", "status", "pause", "resume", "kill", "sync_project_folders"}
_EVENT_TYPES = {"queued", "accepted", "running", "awaiting_user_input", "paused", "done", "failed", "killed"}
_RESULT_FINAL_STATUSES = {"awaiting_user_input", "paused", "done", "failed", "killed"}
_ARTIFACT_KINDS = {"image", "file"}


class PcControlProtocolError(ValueError):
    """Raised when a pc-control message does not match the frozen shape."""


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PcControlProtocolError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PcControlProtocolError(f"{field_name} must be a dict")
    return dict(value)


def _require_optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _require_mapping(value, field_name)


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PcControlProtocolError(f"{field_name} must be a bool")
    return value


def _require_int(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int):
        raise PcControlProtocolError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise PcControlProtocolError(f"{field_name} must be >= {minimum}")
    return value


def _require_optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name, minimum=minimum)


def _require_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise PcControlProtocolError(f"{field_name} must be a list[str]")
    normalized: list[str] = []
    for index, item in enumerate(value):
        normalized.append(_require_text(item, f"{field_name}[{index}]"))
    return normalized


def _require_string_list_mapping(value: Any, field_name: str) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise PcControlProtocolError(f"{field_name} must be a dict[str, list[str]]")
    normalized: dict[str, list[str]] = {}
    for key, item in value.items():
        normalized[_require_text(key, f"{field_name}.key")] = _require_string_list(item, f"{field_name}[{key}]")
    return normalized


def _require_schema_version(value: Any) -> str:
    schema_version = _require_text(value, "schema_version")
    if schema_version != PC_CONTROL_SCHEMA_VERSION:
        raise PcControlProtocolError(f"schema_version must be {PC_CONTROL_SCHEMA_VERSION}")
    return schema_version


def _validate_capabilities(value: Any, field_name: str) -> dict[str, Any]:
    data = _require_mapping(value, field_name)
    return {
        "streaming": _require_bool(data.get("streaming", False), f"{field_name}.streaming"),
        "artifact_manifest": _require_bool(data.get("artifact_manifest", False), f"{field_name}.artifact_manifest"),
        "workspace_snapshot": _require_bool(data.get("workspace_snapshot", False), f"{field_name}.workspace_snapshot"),
        "supported_backends": _require_string_list(data.get("supported_backends"), f"{field_name}.supported_backends"),
        "profile_catalogs": _require_string_list_mapping(data.get("profile_catalogs"), f"{field_name}.profile_catalogs"),
        "permission_modes": _require_string_list(data.get("permission_modes"), f"{field_name}.permission_modes"),
        "backend_transport_modes": _require_string_list_mapping(
            data.get("backend_transport_modes"),
            f"{field_name}.backend_transport_modes",
        ),
    }


def _validate_execution_policy(value: Any, field_name: str) -> dict[str, Any]:
    data = _require_mapping(value, field_name)
    return {
        "backend": _require_optional_text(data.get("backend"), f"{field_name}.backend"),
        "profile": _require_optional_text(data.get("profile"), f"{field_name}.profile"),
        "permission": _require_optional_text(data.get("permission"), f"{field_name}.permission"),
        "backend_transport": _require_optional_text(data.get("backend_transport"), f"{field_name}.backend_transport"),
    }


def _validate_workspace_entries(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PcControlProtocolError(f"{field_name} must be a list[dict]")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        workspace = _require_mapping(item, f"{field_name}[{index}]")
        normalized.append(
            {
                "workspace_id": _require_text(workspace.get("workspace_id"), f"{field_name}[{index}].workspace_id"),
                "workspace_norm": _require_optional_text(
                    workspace.get("workspace_norm"),
                    f"{field_name}[{index}].workspace_norm",
                ),
                "repo_path": _require_text(workspace.get("repo_path"), f"{field_name}[{index}].repo_path"),
                "workdir": _require_optional_text(workspace.get("workdir"), f"{field_name}[{index}].workdir"),
                "display_name": _require_text(workspace.get("display_name"), f"{field_name}[{index}].display_name"),
                "source": _require_optional_text(workspace.get("source"), f"{field_name}[{index}].source"),
                "capabilities": _validate_capabilities(
                    workspace.get("capabilities"),
                    f"{field_name}[{index}].capabilities",
                ),
            }
        )
    return normalized


def _validate_command_type(value: Any, field_name: str) -> str:
    command_type = _require_text(value, field_name)
    if command_type not in _COMMAND_TYPES:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_COMMAND_TYPES))}")
    return command_type


def _validate_ack_status(value: Any, field_name: str) -> str:
    ack_status = _require_text(value, field_name)
    if ack_status not in _ACK_STATUSES:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_ACK_STATUSES))}")
    return ack_status


def _validate_event_type(value: Any, field_name: str) -> str:
    event_type = _require_text(value, field_name)
    if event_type not in _EVENT_TYPES:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_EVENT_TYPES))}")
    return event_type


def _validate_result_final_status(value: Any, field_name: str) -> str:
    final_status = _require_text(value, field_name)
    if final_status not in _RESULT_FINAL_STATUSES:
        raise PcControlProtocolError(
            f"{field_name} must be one of: {', '.join(sorted(_RESULT_FINAL_STATUSES))}"
        )
    return final_status


def _validate_effective_execution(value: Any, field_name: str) -> dict[str, Any]:
    data = _require_mapping(value, field_name)
    return {
        "backend": _require_optional_text(data.get("backend"), f"{field_name}.backend"),
        "profile": _require_optional_text(data.get("profile"), f"{field_name}.profile"),
        "permission": _require_optional_text(data.get("permission"), f"{field_name}.permission"),
        "backend_transport": _require_optional_text(data.get("backend_transport"), f"{field_name}.backend_transport"),
        "resolved_model": _require_optional_text(data.get("resolved_model"), f"{field_name}.resolved_model"),
    }


def _validate_artifact_manifest_items(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PcControlProtocolError(f"{field_name} must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_field = f"{field_name}[{index}]"
        data = _require_mapping(item, item_field)
        kind = _require_text(data.get("kind"), f"{item_field}.kind")
        if kind not in _ARTIFACT_KINDS:
            raise PcControlProtocolError(f"{item_field}.kind must be one of: {', '.join(sorted(_ARTIFACT_KINDS))}")
        normalized.append(
            {
                "artifact_id": _require_text(data.get("artifact_id"), f"{item_field}.artifact_id"),
                "kind": kind,
                "name": _require_text(data.get("name"), f"{item_field}.name"),
                "content_type": _require_text(data.get("content_type"), f"{item_field}.content_type"),
                "size": _require_int(data.get("size"), f"{item_field}.size", minimum=0),
                "download_ref": _require_optional_text(data.get("download_ref"), f"{item_field}.download_ref"),
                "download_ref_source": _require_optional_text(
                    data.get("download_ref_source"),
                    f"{item_field}.download_ref_source",
                ),
            }
        )
    return normalized


def _validate_envelope(data: dict[str, Any], *, expected_type: str, minimum_epoch: int) -> dict[str, Any]:
    schema_version = _require_schema_version(data.get("schema_version"))
    message_type = _require_text(data.get("type"), "type")
    if message_type != expected_type:
        raise PcControlProtocolError(f"type must be {expected_type}")
    return {
        "schema_version": schema_version,
        "type": message_type,
        "message_id": _require_text(data.get("message_id"), "message_id"),
        "trace_id": _require_text(data.get("trace_id"), "trace_id"),
        "pc_id": _require_text(data.get("pc_id"), "pc_id"),
        "connection_epoch": _require_int(data.get("connection_epoch"), "connection_epoch", minimum=minimum_epoch),
        "sent_at": _require_text(data.get("sent_at"), "sent_at"),
        "payload": _require_mapping(data.get("payload"), "payload"),
    }


@dataclass(slots=True)
class PcHelloMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="pc_hello",
            minimum_epoch=0,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "display_name": _require_text(payload.get("display_name"), "payload.display_name"),
            "client_version": _require_text(payload.get("client_version"), "payload.client_version"),
            "host_fingerprint": _require_optional_text(payload.get("host_fingerprint"), "payload.host_fingerprint"),
            "runtime_fingerprint": _require_optional_text(
                payload.get("runtime_fingerprint"),
                "payload.runtime_fingerprint",
            ),
            "capabilities": _validate_capabilities(payload.get("capabilities"), "payload.capabilities"),
        }


@dataclass(slots=True)
class PcHeartbeatMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="heartbeat",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "active_run_count": _require_int(payload.get("active_run_count"), "payload.active_run_count", minimum=0),
            "workspace_count": _require_int(payload.get("workspace_count"), "payload.workspace_count", minimum=0),
            "load_hint": _require_text(payload.get("load_hint"), "payload.load_hint"),
        }


@dataclass(slots=True)
class PcWorkspaceSnapshotMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="workspace_snapshot",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "snapshot_id": _require_text(payload.get("snapshot_id"), "payload.snapshot_id"),
            "workspaces": _validate_workspace_entries(payload.get("workspaces"), "payload.workspaces"),
        }


@dataclass(slots=True)
class PcCommandAckMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="command_ack",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "ack_status": _validate_ack_status(payload.get("ack_status"), "payload.ack_status"),
            "queue_position": _require_optional_int(payload.get("queue_position"), "payload.queue_position", minimum=1),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
            "error_code": _require_optional_text(payload.get("error_code"), "payload.error_code"),
        }


@dataclass(slots=True)
class PcCommandEventMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="event",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "event_id": _require_text(payload.get("event_id"), "payload.event_id"),
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "event_type": _validate_event_type(payload.get("event_type"), "payload.event_type"),
            "summary": _require_optional_text(payload.get("summary"), "payload.summary"),
            "effective_execution": (
                _validate_effective_execution(payload.get("effective_execution"), "payload.effective_execution")
                if payload.get("effective_execution") is not None
                else None
            ),
            "payload": _require_mapping(payload.get("payload", {}), "payload.payload"),
        }


@dataclass(slots=True)
class PcCommandResultMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="result",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "result_id": _require_text(payload.get("result_id"), "payload.result_id"),
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "final_status": _validate_result_final_status(payload.get("final_status"), "payload.final_status"),
            "summary": _require_text(payload.get("summary"), "payload.summary"),
            "structured_payload": _require_mapping(payload.get("structured_payload"), "payload.structured_payload"),
            "effective_execution": _validate_effective_execution(
                payload.get("effective_execution"),
                "payload.effective_execution",
            ),
            "error_code": _require_optional_text(payload.get("error_code"), "payload.error_code"),
            "error_message": _require_optional_text(payload.get("error_message"), "payload.error_message"),
        }


@dataclass(slots=True)
class PcOutputChunkMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="output_chunk",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        text = _require_optional_text(payload.get("text"), "payload.text")
        delta = _require_optional_text(payload.get("delta"), "payload.delta")
        if text is None and delta is None:
            raise PcControlProtocolError("payload.text or payload.delta is required")
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "output_chunk_id": _require_text(payload.get("output_chunk_id"), "payload.output_chunk_id"),
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "stream_id": _require_text(payload.get("stream_id"), "payload.stream_id"),
            "stream_id_source": _require_optional_text(payload.get("stream_id_source"), "payload.stream_id_source"),
            "seq": _require_int(payload.get("seq"), "payload.seq", minimum=1),
            "kind": _require_text(payload.get("kind"), "payload.kind"),
            "text": text,
            "delta": delta,
            "item_type": _require_optional_text(payload.get("item_type"), "payload.item_type"),
            "status": _require_optional_text(payload.get("status"), "payload.status"),
        }


@dataclass(slots=True)
class PcArtifactManifestMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="artifact_manifest",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "manifest_id": _require_text(payload.get("manifest_id"), "payload.manifest_id"),
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "artifacts_root": _require_optional_text(payload.get("artifacts_root"), "payload.artifacts_root"),
            "source": _require_optional_text(payload.get("source"), "payload.source"),
            "artifacts": _validate_artifact_manifest_items(payload.get("artifacts"), "payload.artifacts"),
        }


PcControlClientMessage = (
    PcHelloMessage
    | PcHeartbeatMessage
    | PcWorkspaceSnapshotMessage
    | PcCommandAckMessage
    | PcCommandEventMessage
    | PcCommandResultMessage
    | PcOutputChunkMessage
    | PcArtifactManifestMessage
)


@dataclass(slots=True)
class PcHelloAckMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="hello_ack",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "accepted": _require_bool(payload.get("accepted"), "payload.accepted"),
            "keepalive_seconds": _require_int(payload.get("keepalive_seconds"), "payload.keepalive_seconds", minimum=1),
        }


@dataclass(slots=True)
class PcErrorMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str | None
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        self.schema_version = _require_schema_version(self.schema_version)
        if self.type != "error":
            raise PcControlProtocolError("type must be error")
        self.message_id = _require_text(self.message_id, "message_id")
        self.trace_id = _require_text(self.trace_id, "trace_id")
        if self.pc_id is not None:
            self.pc_id = _require_text(self.pc_id, "pc_id")
        self.connection_epoch = _require_int(self.connection_epoch, "connection_epoch", minimum=0)
        self.sent_at = _require_text(self.sent_at, "sent_at")
        payload = _require_mapping(self.payload, "payload")
        self.payload = {
            "code": _require_text(payload.get("code"), "payload.code"),
            "message": _require_text(payload.get("message"), "payload.message"),
        }


@dataclass(slots=True)
class PcCommandDispatchMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="command_dispatch",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "command_type": _validate_command_type(payload.get("command_type"), "payload.command_type"),
            "workspace_id": _require_text(payload.get("workspace_id"), "payload.workspace_id"),
            "session_id": _require_optional_text(payload.get("session_id"), "payload.session_id"),
            "execution_policy": _validate_execution_policy(payload.get("execution_policy"), "payload.execution_policy"),
            "payload": _require_mapping(payload.get("payload"), "payload.payload"),
        }


@dataclass(slots=True)
class PcOutputResumeRequestMessage:
    schema_version: str
    type: str
    message_id: str
    trace_id: str
    pc_id: str
    connection_epoch: int
    sent_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        envelope = _validate_envelope(
            {
                "schema_version": self.schema_version,
                "type": self.type,
                "message_id": self.message_id,
                "trace_id": self.trace_id,
                "pc_id": self.pc_id,
                "connection_epoch": self.connection_epoch,
                "sent_at": self.sent_at,
                "payload": self.payload,
            },
            expected_type="output_resume_request",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        stream_id = _require_optional_text(payload.get("stream_id"), "payload.stream_id")
        stream_id_source = _require_optional_text(payload.get("stream_id_source"), "payload.stream_id_source")
        if stream_id is None and stream_id_source is not None:
            raise PcControlProtocolError("payload.stream_id_source requires payload.stream_id")
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "command_id": _require_text(payload.get("command_id"), "payload.command_id"),
            "stream_id": stream_id,
            "stream_id_source": stream_id_source,
            "after_seq": _require_int(payload.get("after_seq"), "payload.after_seq", minimum=0),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
        }


PcControlServerMessage = PcHelloAckMessage | PcErrorMessage | PcCommandDispatchMessage | PcOutputResumeRequestMessage


def parse_pc_control_client_message(payload: dict[str, Any]) -> PcControlClientMessage:
    data = _require_mapping(payload, "payload")
    message_type = _require_text(data.get("type"), "type")
    if message_type not in _CLIENT_MESSAGE_TYPES:
        raise PcControlProtocolError(f"unsupported type: {message_type}")
    if message_type == "pc_hello":
        return PcHelloMessage(**data)
    if message_type == "heartbeat":
        return PcHeartbeatMessage(**data)
    if message_type == "workspace_snapshot":
        return PcWorkspaceSnapshotMessage(**data)
    if message_type == "command_ack":
        return PcCommandAckMessage(**data)
    if message_type == "event":
        return PcCommandEventMessage(**data)
    if message_type == "result":
        return PcCommandResultMessage(**data)
    if message_type == "output_chunk":
        return PcOutputChunkMessage(**data)
    return PcArtifactManifestMessage(**data)


def parse_pc_control_server_message(payload: dict[str, Any]) -> PcControlServerMessage:
    data = _require_mapping(payload, "payload")
    message_type = _require_text(data.get("type"), "type")
    if message_type not in _SERVER_MESSAGE_TYPES:
        raise PcControlProtocolError(f"unsupported type: {message_type}")
    if message_type == "hello_ack":
        return PcHelloAckMessage(**data)
    if message_type == "error":
        return PcErrorMessage(**data)
    if message_type == "command_dispatch":
        return PcCommandDispatchMessage(**data)
    return PcOutputResumeRequestMessage(**data)


def build_pc_hello(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    sent_at: str,
    display_name: str,
    client_version: str,
    host_fingerprint: str | None,
    runtime_fingerprint: str | None,
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "pc_hello",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": 0,
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "display_name": _require_text(display_name, "payload.display_name"),
            "client_version": _require_text(client_version, "payload.client_version"),
            "host_fingerprint": _require_optional_text(host_fingerprint, "payload.host_fingerprint"),
            "runtime_fingerprint": _require_optional_text(runtime_fingerprint, "payload.runtime_fingerprint"),
            "capabilities": _validate_capabilities(capabilities, "payload.capabilities"),
        },
    }
    PcHelloMessage(**payload)
    return payload


def build_heartbeat(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    active_run_count: int,
    workspace_count: int,
    load_hint: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "heartbeat",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "active_run_count": _require_int(active_run_count, "payload.active_run_count", minimum=0),
            "workspace_count": _require_int(workspace_count, "payload.workspace_count", minimum=0),
            "load_hint": _require_text(load_hint, "payload.load_hint"),
        },
    }
    PcHeartbeatMessage(**payload)
    return payload


def build_workspace_snapshot(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    snapshot_id: str,
    workspaces: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "workspace_snapshot",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "snapshot_id": _require_text(snapshot_id, "payload.snapshot_id"),
            "workspaces": _validate_workspace_entries(workspaces, "payload.workspaces"),
        },
    }
    PcWorkspaceSnapshotMessage(**payload)
    return payload


def build_command_ack(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    command_id: str,
    ack_status: str,
    queue_position: int | None = None,
    reason: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "command_ack",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "command_id": _require_text(command_id, "payload.command_id"),
            "ack_status": _validate_ack_status(ack_status, "payload.ack_status"),
            "queue_position": _require_optional_int(queue_position, "payload.queue_position", minimum=1),
            "reason": _require_optional_text(reason, "payload.reason"),
            "error_code": _require_optional_text(error_code, "payload.error_code"),
        },
    }
    PcCommandAckMessage(**payload)
    return payload


def build_command_event(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    event_id: str,
    command_id: str,
    event_type: str,
    summary: str | None = None,
    effective_execution: dict[str, Any] | None = None,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "event",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "event_id": _require_text(event_id, "payload.event_id"),
            "command_id": _require_text(command_id, "payload.command_id"),
            "event_type": _validate_event_type(event_type, "payload.event_type"),
            "summary": _require_optional_text(summary, "payload.summary"),
            "effective_execution": (
                _validate_effective_execution(effective_execution, "payload.effective_execution")
                if effective_execution is not None
                else None
            ),
            "payload": _require_mapping(event_payload or {}, "payload.payload"),
        },
    }
    PcCommandEventMessage(**payload)
    return payload


def build_command_result(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    result_id: str,
    command_id: str,
    final_status: str,
    summary: str,
    structured_payload: dict[str, Any],
    effective_execution: dict[str, Any],
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "result",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "result_id": _require_text(result_id, "payload.result_id"),
            "command_id": _require_text(command_id, "payload.command_id"),
            "final_status": _validate_result_final_status(final_status, "payload.final_status"),
            "summary": _require_text(summary, "payload.summary"),
            "structured_payload": _require_mapping(structured_payload, "payload.structured_payload"),
            "effective_execution": _validate_effective_execution(
                effective_execution,
                "payload.effective_execution",
            ),
            "error_code": _require_optional_text(error_code, "payload.error_code"),
            "error_message": _require_optional_text(error_message, "payload.error_message"),
        },
    }
    PcCommandResultMessage(**payload)
    return payload


def build_output_chunk(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    output_chunk_id: str,
    command_id: str,
    stream_id: str,
    seq: int,
    kind: str,
    text: str | None = None,
    delta: str | None = None,
    item_type: str | None = None,
    status: str | None = None,
    stream_id_source: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "output_chunk",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "output_chunk_id": _require_text(output_chunk_id, "payload.output_chunk_id"),
            "command_id": _require_text(command_id, "payload.command_id"),
            "stream_id": _require_text(stream_id, "payload.stream_id"),
            "stream_id_source": _require_optional_text(stream_id_source, "payload.stream_id_source"),
            "seq": _require_int(seq, "payload.seq", minimum=1),
            "kind": _require_text(kind, "payload.kind"),
            "text": _require_optional_text(text, "payload.text"),
            "delta": _require_optional_text(delta, "payload.delta"),
            "item_type": _require_optional_text(item_type, "payload.item_type"),
            "status": _require_optional_text(status, "payload.status"),
        },
    }
    PcOutputChunkMessage(**payload)
    return payload


def build_artifact_manifest(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    manifest_id: str,
    command_id: str,
    artifacts: list[dict[str, Any]],
    artifacts_root: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "artifact_manifest",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "manifest_id": _require_text(manifest_id, "payload.manifest_id"),
            "command_id": _require_text(command_id, "payload.command_id"),
            "artifacts_root": _require_optional_text(artifacts_root, "payload.artifacts_root"),
            "source": _require_optional_text(source, "payload.source"),
            "artifacts": _validate_artifact_manifest_items(artifacts, "payload.artifacts"),
        },
    }
    PcArtifactManifestMessage(**payload)
    return payload


def build_pc_hello_ack(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    keepalive_seconds: int,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "hello_ack",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "accepted": True,
            "keepalive_seconds": _require_int(keepalive_seconds, "payload.keepalive_seconds", minimum=1),
        },
    }
    PcHelloAckMessage(**payload)
    return payload


def build_command_dispatch(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    command_id: str,
    command_type: str,
    workspace_id: str,
    execution_policy: dict[str, Any],
    command_payload: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "command_dispatch",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "command_id": _require_text(command_id, "payload.command_id"),
            "command_type": _validate_command_type(command_type, "payload.command_type"),
            "workspace_id": _require_text(workspace_id, "payload.workspace_id"),
            "session_id": _require_optional_text(session_id, "payload.session_id"),
            "execution_policy": _validate_execution_policy(execution_policy, "payload.execution_policy"),
            "payload": _require_mapping(command_payload, "payload.payload"),
        },
    }
    PcCommandDispatchMessage(**payload)
    return payload


def build_output_resume_request(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    command_id: str,
    after_seq: int,
    stream_id: str | None = None,
    stream_id_source: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "output_resume_request",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "command_id": _require_text(command_id, "payload.command_id"),
            "stream_id": _require_optional_text(stream_id, "payload.stream_id"),
            "stream_id_source": _require_optional_text(stream_id_source, "payload.stream_id_source"),
            "after_seq": _require_int(after_seq, "payload.after_seq", minimum=0),
            "reason": _require_optional_text(reason, "payload.reason"),
        },
    }
    PcOutputResumeRequestMessage(**payload)
    return payload


def build_pc_error(
    *,
    message_id: str,
    trace_id: str,
    sent_at: str,
    code: str,
    message: str,
    pc_id: str | None = None,
    connection_epoch: int = 0,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "error",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_optional_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=0),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "code": _require_text(code, "payload.code"),
            "message": _require_text(message, "payload.message"),
        },
    }
    PcErrorMessage(**payload)
    return payload
