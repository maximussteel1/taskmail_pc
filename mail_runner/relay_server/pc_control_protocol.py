"""Protocol helpers for the VPS-first PC control plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..download_ref import normalize_download_ref


PC_CONTROL_SCHEMA_VERSION = "v1"
_SERVER_MESSAGE_TYPES = {
    "hello_ack",
    "error",
    "command_dispatch",
    "output_resume_request",
    "delivery_ack",
    "mailbox_lease_ack",
    "ingress_decision",
    "thread_binding_ack",
    "terminal_outcome_ack",
}
_CLIENT_MESSAGE_TYPES = {
    "pc_hello",
    "heartbeat",
    "workspace_snapshot",
    "projection_batch",
    "command_ack",
    "event",
    "result",
    "output_chunk",
    "artifact_manifest",
    "mailbox_lease",
    "ingress_candidate",
    "thread_binding",
    "terminal_outcome",
}
_ACK_STATUSES = {"accepted", "accepted_but_queued", "rejected"}
_COMMAND_TYPES = {
    "new_task",
    "reply",
    "status",
    "pause",
    "resume",
    "kill",
    "end",
    "answers",
    "attachment_continuation",
    "sync_project_folders",
}
_EVENT_TYPES = {"queued", "accepted", "running", "awaiting_user_input", "paused", "done", "failed", "killed"}
_RESULT_FINAL_STATUSES = {"awaiting_user_input", "paused", "done", "failed", "killed"}
_ARTIFACT_KINDS = {"image", "file"}
_LEASE_OPERATIONS = {"acquire", "renew", "release"}
_LEASE_STATUSES = {"active", "released", "denied"}
_INGRESS_DECISIONS = {"accepted", "duplicate", "stale", "invalid", "ignored", "lease_denied"}
_INGRESS_CANDIDATE_STATUSES = {"ready", "stale", "invalid", "ignored"}
_INGRESS_CLASSIFICATIONS = {"new_task", "reply", "sync", "direct_kill", "system_mail", "unsupported"}
_THREAD_BINDING_STATUSES = {"committed", "duplicate", "denied"}
_TERMINAL_OUTCOME_STATUSES = {"committed", "denied"}
_PROJECTION_SCOPES = {"session", "probe"}
_DELIVERY_ACK_MESSAGE_TYPES = {"projection_batch", "result"}
_DELIVERY_ACK_STATUSES = {"committed"}


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


def _require_optional_chunk_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PcControlProtocolError(f"{field_name} must be a string")
    return value if value else None


def _validate_optional_download_ref(value: Any, field_name: str) -> dict[str, Any] | None:
    try:
        return normalize_download_ref(value, field_name=field_name)
    except ValueError as exc:
        raise PcControlProtocolError(str(exc)) from exc


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
                "download_ref": _validate_optional_download_ref(data.get("download_ref"), f"{item_field}.download_ref"),
                "download_ref_source": _require_optional_text(
                    data.get("download_ref_source"),
                    f"{item_field}.download_ref_source",
                ),
            }
        )
    return normalized


def _validate_lease_operation(value: Any, field_name: str) -> str:
    operation = _require_text(value, field_name)
    if operation not in _LEASE_OPERATIONS:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_LEASE_OPERATIONS))}")
    return operation


def _validate_lease_status(value: Any, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in _LEASE_STATUSES:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_LEASE_STATUSES))}")
    return status


def _validate_ingress_decision(value: Any, field_name: str) -> str:
    decision = _require_text(value, field_name)
    if decision not in _INGRESS_DECISIONS:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_INGRESS_DECISIONS))}")
    return decision


def _validate_ingress_candidate_status(value: Any, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in _INGRESS_CANDIDATE_STATUSES:
        raise PcControlProtocolError(
            f"{field_name} must be one of: {', '.join(sorted(_INGRESS_CANDIDATE_STATUSES))}"
        )
    return status


def _validate_ingress_classification(value: Any, field_name: str) -> str:
    classification = _require_text(value, field_name)
    if classification not in _INGRESS_CLASSIFICATIONS:
        raise PcControlProtocolError(
            f"{field_name} must be one of: {', '.join(sorted(_INGRESS_CLASSIFICATIONS))}"
        )
    return classification


def _validate_thread_binding_status(value: Any, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in _THREAD_BINDING_STATUSES:
        raise PcControlProtocolError(
            f"{field_name} must be one of: {', '.join(sorted(_THREAD_BINDING_STATUSES))}"
        )
    return status


def _validate_terminal_outcome_status(value: Any, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in _TERMINAL_OUTCOME_STATUSES:
        raise PcControlProtocolError(
            f"{field_name} must be one of: {', '.join(sorted(_TERMINAL_OUTCOME_STATUSES))}"
        )
    return status


def _validate_projection_scope(value: Any, field_name: str) -> str:
    scope = _require_text(value, field_name)
    if scope not in _PROJECTION_SCOPES:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_PROJECTION_SCOPES))}")
    return scope


def _validate_delivery_ack_message_type(value: Any, field_name: str) -> str:
    message_type = _require_text(value, field_name)
    if message_type not in _DELIVERY_ACK_MESSAGE_TYPES:
        raise PcControlProtocolError(
            f"{field_name} must be one of: {', '.join(sorted(_DELIVERY_ACK_MESSAGE_TYPES))}"
        )
    return message_type


def _validate_delivery_ack_status(value: Any, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in _DELIVERY_ACK_STATUSES:
        raise PcControlProtocolError(f"{field_name} must be one of: {', '.join(sorted(_DELIVERY_ACK_STATUSES))}")
    return status


def _validate_projection_items(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise PcControlProtocolError(f"{field_name} must be a non-empty list[dict]")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_field = f"{field_name}[{index}]"
        data = _require_mapping(item, item_field)
        normalized_item = dict(data)
        normalized_item["family"] = _require_text(data.get("family"), f"{item_field}.family")
        normalized_item["idempotency_key"] = _require_text(
            data.get("idempotency_key"),
            f"{item_field}.idempotency_key",
        )
        normalized.append(normalized_item)
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
        text = _require_optional_chunk_text(payload.get("text"), "payload.text")
        delta = _require_optional_chunk_text(payload.get("delta"), "payload.delta")
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


@dataclass(slots=True)
class PcMailboxLeaseMessage:
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
            expected_type="mailbox_lease",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "operation": _validate_lease_operation(payload.get("operation"), "payload.operation"),
            "mailbox_key": _require_text(payload.get("mailbox_key"), "payload.mailbox_key"),
            "lease_holder_id": _require_text(payload.get("lease_holder_id"), "payload.lease_holder_id"),
            "lease_ttl_seconds": _require_int(payload.get("lease_ttl_seconds"), "payload.lease_ttl_seconds", minimum=5),
            "lease_epoch": _require_optional_int(payload.get("lease_epoch"), "payload.lease_epoch", minimum=1),
            "config_fingerprint": _require_optional_text(payload.get("config_fingerprint"), "payload.config_fingerprint"),
            "host_fingerprint": _require_optional_text(payload.get("host_fingerprint"), "payload.host_fingerprint"),
            "runtime_fingerprint": _require_optional_text(payload.get("runtime_fingerprint"), "payload.runtime_fingerprint"),
            "last_seen_thread_id": _require_optional_text(payload.get("last_seen_thread_id"), "payload.last_seen_thread_id"),
            "last_seen_ingress_id": _require_optional_text(payload.get("last_seen_ingress_id"), "payload.last_seen_ingress_id"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcIngressCandidateMessage:
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
            expected_type="ingress_candidate",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "mailbox_key": _require_text(payload.get("mailbox_key"), "payload.mailbox_key"),
            "lease_holder_id": _require_text(payload.get("lease_holder_id"), "payload.lease_holder_id"),
            "lease_epoch": _require_int(payload.get("lease_epoch"), "payload.lease_epoch", minimum=1),
            "folder": _require_text(payload.get("folder"), "payload.folder"),
            "uid_validity": _require_optional_int(payload.get("uid_validity"), "payload.uid_validity", minimum=1),
            "uid": _require_optional_int(payload.get("uid"), "payload.uid", minimum=1),
            "message_id": _require_text(payload.get("message_id"), "payload.message_id"),
            "in_reply_to": _require_optional_text(payload.get("in_reply_to"), "payload.in_reply_to"),
            "references_hash": _require_optional_text(payload.get("references_hash"), "payload.references_hash"),
            "from_addr": _require_text(payload.get("from_addr"), "payload.from_addr"),
            "subject": _require_text(payload.get("subject"), "payload.subject"),
            "subject_norm": _require_text(payload.get("subject_norm"), "payload.subject_norm"),
            "raw_date": _require_optional_text(payload.get("raw_date"), "payload.raw_date"),
            "classification": _validate_ingress_classification(payload.get("classification"), "payload.classification"),
            "candidate_status": _validate_ingress_candidate_status(
                payload.get("candidate_status"),
                "payload.candidate_status",
            ),
            "candidate_reason": _require_optional_text(payload.get("candidate_reason"), "payload.candidate_reason"),
            "taskmail_request_id": _require_optional_text(payload.get("taskmail_request_id"), "payload.taskmail_request_id"),
            "packet_id": _require_optional_text(payload.get("packet_id"), "payload.packet_id"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcThreadBindingMessage:
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
            expected_type="thread_binding",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "mailbox_key": _require_text(payload.get("mailbox_key"), "payload.mailbox_key"),
            "lease_holder_id": _require_text(payload.get("lease_holder_id"), "payload.lease_holder_id"),
            "lease_epoch": _require_int(payload.get("lease_epoch"), "payload.lease_epoch", minimum=1),
            "ingress_id": _require_text(payload.get("ingress_id"), "payload.ingress_id"),
            "root_message_id": _require_text(payload.get("root_message_id"), "payload.root_message_id"),
            "thread_id": _require_text(payload.get("thread_id"), "payload.thread_id"),
            "session_id": _require_text(payload.get("session_id"), "payload.session_id"),
            "repo_path": _require_text(payload.get("repo_path"), "payload.repo_path"),
            "workdir": _require_optional_text(payload.get("workdir"), "payload.workdir"),
            "subject_norm": _require_text(payload.get("subject_norm"), "payload.subject_norm"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcTerminalOutcomeMessage:
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
            expected_type="terminal_outcome",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "mailbox_key": _require_text(payload.get("mailbox_key"), "payload.mailbox_key"),
            "lease_holder_id": _require_text(payload.get("lease_holder_id"), "payload.lease_holder_id"),
            "lease_epoch": _require_int(payload.get("lease_epoch"), "payload.lease_epoch", minimum=1),
            "thread_id": _require_text(payload.get("thread_id"), "payload.thread_id"),
            "task_id": _require_text(payload.get("task_id"), "payload.task_id"),
            "run_status": _require_text(payload.get("run_status"), "payload.run_status"),
            "generated_at": _require_text(payload.get("generated_at"), "payload.generated_at"),
            "last_summary": _require_optional_text(payload.get("last_summary"), "payload.last_summary"),
            "terminal_mail_message_id": _require_optional_text(
                payload.get("terminal_mail_message_id"),
                "payload.terminal_mail_message_id",
            ),
            "terminal_mail_subject": _require_optional_text(
                payload.get("terminal_mail_subject"),
                "payload.terminal_mail_subject",
            ),
            "taskmail_request_id": _require_optional_text(payload.get("taskmail_request_id"), "payload.taskmail_request_id"),
            "packet_id": _require_optional_text(payload.get("packet_id"), "payload.packet_id"),
            "source_ingress_id": _require_optional_text(payload.get("source_ingress_id"), "payload.source_ingress_id"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcProjectionBatchMessage:
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
            expected_type="projection_batch",
            minimum_epoch=1,
        )
        payload = envelope["payload"]
        scope = _validate_projection_scope(payload.get("scope"), "payload.scope")
        self.schema_version = envelope["schema_version"]
        self.type = envelope["type"]
        self.message_id = envelope["message_id"]
        self.trace_id = envelope["trace_id"]
        self.pc_id = envelope["pc_id"]
        self.connection_epoch = envelope["connection_epoch"]
        self.sent_at = envelope["sent_at"]
        self.payload = {
            "batch_id": _require_text(payload.get("batch_id"), "payload.batch_id"),
            "scope": scope,
            "workspace_id": _require_optional_text(payload.get("workspace_id"), "payload.workspace_id"),
            "session_id": _require_optional_text(payload.get("session_id"), "payload.session_id"),
            "thread_id": _require_optional_text(payload.get("thread_id"), "payload.thread_id"),
            "projection_version": _require_optional_int(
                payload.get("projection_version"),
                "payload.projection_version",
                minimum=1,
            ),
            "items": _validate_projection_items(payload.get("items"), "payload.items"),
        }
        if scope == "session":
            if self.payload["workspace_id"] is None:
                raise PcControlProtocolError("payload.workspace_id is required for session projection batches")
            if self.payload["session_id"] is None:
                raise PcControlProtocolError("payload.session_id is required for session projection batches")
            if self.payload["thread_id"] is None:
                raise PcControlProtocolError("payload.thread_id is required for session projection batches")
            if self.payload["projection_version"] is None:
                raise PcControlProtocolError("payload.projection_version is required for session projection batches")
        else:
            self.payload["workspace_id"] = None
            self.payload["session_id"] = None
            self.payload["thread_id"] = None
            self.payload["projection_version"] = None


PcControlClientMessage = (
    PcHelloMessage
    | PcHeartbeatMessage
    | PcWorkspaceSnapshotMessage
    | PcProjectionBatchMessage
    | PcCommandAckMessage
    | PcCommandEventMessage
    | PcCommandResultMessage
    | PcOutputChunkMessage
    | PcArtifactManifestMessage
    | PcMailboxLeaseMessage
    | PcIngressCandidateMessage
    | PcThreadBindingMessage
    | PcTerminalOutcomeMessage
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


@dataclass(slots=True)
class PcMailboxLeaseAckMessage:
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
            expected_type="mailbox_lease_ack",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "operation": _validate_lease_operation(payload.get("operation"), "payload.operation"),
            "mailbox_key": _require_text(payload.get("mailbox_key"), "payload.mailbox_key"),
            "lease_status": _validate_lease_status(payload.get("lease_status"), "payload.lease_status"),
            "lease_holder_id": _require_optional_text(payload.get("lease_holder_id"), "payload.lease_holder_id"),
            "lease_pc_id": _require_optional_text(payload.get("lease_pc_id"), "payload.lease_pc_id"),
            "lease_epoch": _require_optional_int(payload.get("lease_epoch"), "payload.lease_epoch", minimum=1),
            "expires_at": _require_optional_text(payload.get("expires_at"), "payload.expires_at"),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcDeliveryAckMessage:
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
            expected_type="delivery_ack",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "message_type": _validate_delivery_ack_message_type(payload.get("message_type"), "payload.message_type"),
            "delivery_status": _validate_delivery_ack_status(payload.get("delivery_status"), "payload.delivery_status"),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
        }


@dataclass(slots=True)
class PcIngressDecisionMessage:
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
            expected_type="ingress_decision",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "ingress_id": _require_text(payload.get("ingress_id"), "payload.ingress_id"),
            "mailbox_key": _require_text(payload.get("mailbox_key"), "payload.mailbox_key"),
            "decision": _validate_ingress_decision(payload.get("decision"), "payload.decision"),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
            "classification": _validate_ingress_classification(payload.get("classification"), "payload.classification"),
            "lease_holder_id": _require_optional_text(payload.get("lease_holder_id"), "payload.lease_holder_id"),
            "lease_epoch": _require_optional_int(payload.get("lease_epoch"), "payload.lease_epoch", minimum=1),
            "thread_id": _require_optional_text(payload.get("thread_id"), "payload.thread_id"),
            "session_id": _require_optional_text(payload.get("session_id"), "payload.session_id"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcThreadBindingAckMessage:
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
            expected_type="thread_binding_ack",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "ingress_id": _require_text(payload.get("ingress_id"), "payload.ingress_id"),
            "binding_status": _validate_thread_binding_status(payload.get("binding_status"), "payload.binding_status"),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
            "thread_id": _require_optional_text(payload.get("thread_id"), "payload.thread_id"),
            "session_id": _require_optional_text(payload.get("session_id"), "payload.session_id"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


@dataclass(slots=True)
class PcTerminalOutcomeAckMessage:
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
            expected_type="terminal_outcome_ack",
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
            "request_id": _require_text(payload.get("request_id"), "payload.request_id"),
            "thread_id": _require_text(payload.get("thread_id"), "payload.thread_id"),
            "task_id": _require_text(payload.get("task_id"), "payload.task_id"),
            "outcome_status": _validate_terminal_outcome_status(payload.get("outcome_status"), "payload.outcome_status"),
            "reason": _require_optional_text(payload.get("reason"), "payload.reason"),
            "source_ingress_id": _require_optional_text(payload.get("source_ingress_id"), "payload.source_ingress_id"),
            "degraded_mode": _require_bool(payload.get("degraded_mode", False), "payload.degraded_mode"),
        }


PcControlServerMessage = (
    PcHelloAckMessage
    | PcErrorMessage
    | PcCommandDispatchMessage
    | PcOutputResumeRequestMessage
    | PcDeliveryAckMessage
    | PcMailboxLeaseAckMessage
    | PcIngressDecisionMessage
    | PcThreadBindingAckMessage
    | PcTerminalOutcomeAckMessage
)


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
    if message_type == "projection_batch":
        return PcProjectionBatchMessage(**data)
    if message_type == "command_ack":
        return PcCommandAckMessage(**data)
    if message_type == "event":
        return PcCommandEventMessage(**data)
    if message_type == "result":
        return PcCommandResultMessage(**data)
    if message_type == "output_chunk":
        return PcOutputChunkMessage(**data)
    if message_type == "artifact_manifest":
        return PcArtifactManifestMessage(**data)
    if message_type == "mailbox_lease":
        return PcMailboxLeaseMessage(**data)
    if message_type == "ingress_candidate":
        return PcIngressCandidateMessage(**data)
    if message_type == "thread_binding":
        return PcThreadBindingMessage(**data)
    return PcTerminalOutcomeMessage(**data)


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
    if message_type == "output_resume_request":
        return PcOutputResumeRequestMessage(**data)
    if message_type == "delivery_ack":
        return PcDeliveryAckMessage(**data)
    if message_type == "mailbox_lease_ack":
        return PcMailboxLeaseAckMessage(**data)
    if message_type == "ingress_decision":
        return PcIngressDecisionMessage(**data)
    if message_type == "thread_binding_ack":
        return PcThreadBindingAckMessage(**data)
    return PcTerminalOutcomeAckMessage(**data)


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
            "text": _require_optional_chunk_text(text, "payload.text"),
            "delta": _require_optional_chunk_text(delta, "payload.delta"),
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


def build_projection_batch(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    batch_id: str,
    scope: str,
    items: list[dict[str, Any]],
    workspace_id: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    projection_version: int | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "projection_batch",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "batch_id": _require_text(batch_id, "payload.batch_id"),
            "scope": _validate_projection_scope(scope, "payload.scope"),
            "workspace_id": _require_optional_text(workspace_id, "payload.workspace_id"),
            "session_id": _require_optional_text(session_id, "payload.session_id"),
            "thread_id": _require_optional_text(thread_id, "payload.thread_id"),
            "projection_version": _require_optional_int(
                projection_version,
                "payload.projection_version",
                minimum=1,
            ),
            "items": _validate_projection_items(items, "payload.items"),
        },
    }
    PcProjectionBatchMessage(**payload)
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


def build_delivery_ack(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    message_type: str,
    delivery_status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "delivery_ack",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "message_type": _validate_delivery_ack_message_type(message_type, "payload.message_type"),
            "delivery_status": _validate_delivery_ack_status(delivery_status, "payload.delivery_status"),
            "reason": _require_optional_text(reason, "payload.reason"),
        },
    }
    PcDeliveryAckMessage(**payload)
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


def build_mailbox_lease(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    operation: str,
    mailbox_key: str,
    lease_holder_id: str,
    lease_ttl_seconds: int,
    lease_epoch: int | None = None,
    config_fingerprint: str | None = None,
    host_fingerprint: str | None = None,
    runtime_fingerprint: str | None = None,
    last_seen_thread_id: str | None = None,
    last_seen_ingress_id: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "mailbox_lease",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "operation": _validate_lease_operation(operation, "payload.operation"),
            "mailbox_key": _require_text(mailbox_key, "payload.mailbox_key"),
            "lease_holder_id": _require_text(lease_holder_id, "payload.lease_holder_id"),
            "lease_ttl_seconds": _require_int(lease_ttl_seconds, "payload.lease_ttl_seconds", minimum=5),
            "lease_epoch": _require_optional_int(lease_epoch, "payload.lease_epoch", minimum=1),
            "config_fingerprint": _require_optional_text(config_fingerprint, "payload.config_fingerprint"),
            "host_fingerprint": _require_optional_text(host_fingerprint, "payload.host_fingerprint"),
            "runtime_fingerprint": _require_optional_text(runtime_fingerprint, "payload.runtime_fingerprint"),
            "last_seen_thread_id": _require_optional_text(last_seen_thread_id, "payload.last_seen_thread_id"),
            "last_seen_ingress_id": _require_optional_text(last_seen_ingress_id, "payload.last_seen_ingress_id"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcMailboxLeaseMessage(**payload)
    return payload


def build_ingress_candidate(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    mailbox_key: str,
    lease_holder_id: str,
    lease_epoch: int,
    folder: str,
    uid_validity: int | None,
    uid: int | None,
    ingress_message_id: str,
    in_reply_to: str | None,
    references_hash: str | None,
    from_addr: str,
    subject: str,
    subject_norm: str,
    raw_date: str | None,
    classification: str,
    candidate_status: str,
    candidate_reason: str | None = None,
    taskmail_request_id: str | None = None,
    packet_id: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "ingress_candidate",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "mailbox_key": _require_text(mailbox_key, "payload.mailbox_key"),
            "lease_holder_id": _require_text(lease_holder_id, "payload.lease_holder_id"),
            "lease_epoch": _require_int(lease_epoch, "payload.lease_epoch", minimum=1),
            "folder": _require_text(folder, "payload.folder"),
            "uid_validity": _require_optional_int(uid_validity, "payload.uid_validity", minimum=1),
            "uid": _require_optional_int(uid, "payload.uid", minimum=1),
            "message_id": _require_text(ingress_message_id, "payload.message_id"),
            "in_reply_to": _require_optional_text(in_reply_to, "payload.in_reply_to"),
            "references_hash": _require_optional_text(references_hash, "payload.references_hash"),
            "from_addr": _require_text(from_addr, "payload.from_addr"),
            "subject": _require_text(subject, "payload.subject"),
            "subject_norm": _require_text(subject_norm, "payload.subject_norm"),
            "raw_date": _require_optional_text(raw_date, "payload.raw_date"),
            "classification": _validate_ingress_classification(classification, "payload.classification"),
            "candidate_status": _validate_ingress_candidate_status(candidate_status, "payload.candidate_status"),
            "candidate_reason": _require_optional_text(candidate_reason, "payload.candidate_reason"),
            "taskmail_request_id": _require_optional_text(taskmail_request_id, "payload.taskmail_request_id"),
            "packet_id": _require_optional_text(packet_id, "payload.packet_id"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcIngressCandidateMessage(**payload)
    return payload


def build_thread_binding(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    mailbox_key: str,
    lease_holder_id: str,
    lease_epoch: int,
    ingress_id: str,
    root_message_id: str,
    thread_id: str,
    session_id: str,
    repo_path: str,
    workdir: str | None,
    subject_norm: str,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "thread_binding",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "mailbox_key": _require_text(mailbox_key, "payload.mailbox_key"),
            "lease_holder_id": _require_text(lease_holder_id, "payload.lease_holder_id"),
            "lease_epoch": _require_int(lease_epoch, "payload.lease_epoch", minimum=1),
            "ingress_id": _require_text(ingress_id, "payload.ingress_id"),
            "root_message_id": _require_text(root_message_id, "payload.root_message_id"),
            "thread_id": _require_text(thread_id, "payload.thread_id"),
            "session_id": _require_text(session_id, "payload.session_id"),
            "repo_path": _require_text(repo_path, "payload.repo_path"),
            "workdir": _require_optional_text(workdir, "payload.workdir"),
            "subject_norm": _require_text(subject_norm, "payload.subject_norm"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcThreadBindingMessage(**payload)
    return payload


def build_terminal_outcome(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    mailbox_key: str,
    lease_holder_id: str,
    lease_epoch: int,
    thread_id: str,
    task_id: str,
    run_status: str,
    generated_at: str,
    last_summary: str | None = None,
    terminal_mail_message_id: str | None = None,
    terminal_mail_subject: str | None = None,
    taskmail_request_id: str | None = None,
    packet_id: str | None = None,
    source_ingress_id: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "terminal_outcome",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "mailbox_key": _require_text(mailbox_key, "payload.mailbox_key"),
            "lease_holder_id": _require_text(lease_holder_id, "payload.lease_holder_id"),
            "lease_epoch": _require_int(lease_epoch, "payload.lease_epoch", minimum=1),
            "thread_id": _require_text(thread_id, "payload.thread_id"),
            "task_id": _require_text(task_id, "payload.task_id"),
            "run_status": _require_text(run_status, "payload.run_status"),
            "generated_at": _require_text(generated_at, "payload.generated_at"),
            "last_summary": _require_optional_text(last_summary, "payload.last_summary"),
            "terminal_mail_message_id": _require_optional_text(
                terminal_mail_message_id,
                "payload.terminal_mail_message_id",
            ),
            "terminal_mail_subject": _require_optional_text(
                terminal_mail_subject,
                "payload.terminal_mail_subject",
            ),
            "taskmail_request_id": _require_optional_text(taskmail_request_id, "payload.taskmail_request_id"),
            "packet_id": _require_optional_text(packet_id, "payload.packet_id"),
            "source_ingress_id": _require_optional_text(source_ingress_id, "payload.source_ingress_id"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcTerminalOutcomeMessage(**payload)
    return payload


def build_mailbox_lease_ack(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    operation: str,
    mailbox_key: str,
    lease_status: str,
    lease_holder_id: str | None,
    lease_pc_id: str | None,
    lease_epoch: int | None,
    expires_at: str | None,
    reason: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "mailbox_lease_ack",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "operation": _validate_lease_operation(operation, "payload.operation"),
            "mailbox_key": _require_text(mailbox_key, "payload.mailbox_key"),
            "lease_status": _validate_lease_status(lease_status, "payload.lease_status"),
            "lease_holder_id": _require_optional_text(lease_holder_id, "payload.lease_holder_id"),
            "lease_pc_id": _require_optional_text(lease_pc_id, "payload.lease_pc_id"),
            "lease_epoch": _require_optional_int(lease_epoch, "payload.lease_epoch", minimum=1),
            "expires_at": _require_optional_text(expires_at, "payload.expires_at"),
            "reason": _require_optional_text(reason, "payload.reason"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcMailboxLeaseAckMessage(**payload)
    return payload


def build_ingress_decision(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    ingress_id: str,
    mailbox_key: str,
    decision: str,
    classification: str,
    lease_holder_id: str | None,
    lease_epoch: int | None,
    reason: str | None = None,
    thread_id: str | None = None,
    session_id: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "ingress_decision",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "ingress_id": _require_text(ingress_id, "payload.ingress_id"),
            "mailbox_key": _require_text(mailbox_key, "payload.mailbox_key"),
            "decision": _validate_ingress_decision(decision, "payload.decision"),
            "reason": _require_optional_text(reason, "payload.reason"),
            "classification": _validate_ingress_classification(classification, "payload.classification"),
            "lease_holder_id": _require_optional_text(lease_holder_id, "payload.lease_holder_id"),
            "lease_epoch": _require_optional_int(lease_epoch, "payload.lease_epoch", minimum=1),
            "thread_id": _require_optional_text(thread_id, "payload.thread_id"),
            "session_id": _require_optional_text(session_id, "payload.session_id"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcIngressDecisionMessage(**payload)
    return payload


def build_thread_binding_ack(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    ingress_id: str,
    binding_status: str,
    reason: str | None = None,
    thread_id: str | None = None,
    session_id: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "thread_binding_ack",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "ingress_id": _require_text(ingress_id, "payload.ingress_id"),
            "binding_status": _validate_thread_binding_status(binding_status, "payload.binding_status"),
            "reason": _require_optional_text(reason, "payload.reason"),
            "thread_id": _require_optional_text(thread_id, "payload.thread_id"),
            "session_id": _require_optional_text(session_id, "payload.session_id"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcThreadBindingAckMessage(**payload)
    return payload


def build_terminal_outcome_ack(
    *,
    message_id: str,
    trace_id: str,
    pc_id: str,
    connection_epoch: int,
    sent_at: str,
    request_id: str,
    thread_id: str,
    task_id: str,
    outcome_status: str,
    reason: str | None = None,
    source_ingress_id: str | None = None,
    degraded_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_version": PC_CONTROL_SCHEMA_VERSION,
        "type": "terminal_outcome_ack",
        "message_id": _require_text(message_id, "message_id"),
        "trace_id": _require_text(trace_id, "trace_id"),
        "pc_id": _require_text(pc_id, "pc_id"),
        "connection_epoch": _require_int(connection_epoch, "connection_epoch", minimum=1),
        "sent_at": _require_text(sent_at, "sent_at"),
        "payload": {
            "request_id": _require_text(request_id, "payload.request_id"),
            "thread_id": _require_text(thread_id, "payload.thread_id"),
            "task_id": _require_text(task_id, "payload.task_id"),
            "outcome_status": _validate_terminal_outcome_status(outcome_status, "payload.outcome_status"),
            "reason": _require_optional_text(reason, "payload.reason"),
            "source_ingress_id": _require_optional_text(source_ingress_id, "payload.source_ingress_id"),
            "degraded_mode": _require_bool(degraded_mode, "payload.degraded_mode"),
        },
    }
    PcTerminalOutcomeAckMessage(**payload)
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
