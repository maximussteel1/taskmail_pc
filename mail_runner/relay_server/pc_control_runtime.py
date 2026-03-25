"""Runtime for the VPS-first PC control plane."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import RelayServerConfig
from .pc_command_store import (
    InMemoryPcCommandStore,
    PcArtifactManifestRecord,
    PcCommandConflictError,
    PcCommandEventRecord,
    PcOutputChunkRecord,
    PcCommandRecord,
    PcCommandResultRecord,
    PcCommandUnknownError,
    PersistentPcCommandStore,
)
from .pc_control_protocol import (
    PcArtifactManifestMessage,
    PcCommandAckMessage,
    PcCommandDispatchMessage,
    PcCommandEventMessage,
    PcCommandResultMessage,
    PcOutputChunkMessage,
    PcHeartbeatMessage,
    PcHelloMessage,
    PcWorkspaceSnapshotMessage,
    build_command_dispatch,
    build_output_resume_request,
    build_pc_error,
    build_pc_hello_ack,
)
from .pc_credential_registry import InMemoryPcCredentialRegistry, PersistentPcCredentialRegistry
from .pc_node_store import InMemoryPcNodeStore, PcNodeFenceError, PcNodeRecord, PersistentPcNodeStore
from .workspace_inventory_store import (
    InMemoryWorkspaceInventoryStore,
    PcWorkspaceRecord,
    PersistentWorkspaceInventoryStore,
    WorkspaceInventoryConflictError,
)


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class PcCommandDispatchValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "").strip()
        self.message = str(message or "").strip()
        super().__init__(self.message)


def _normalized_capabilities(capabilities: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(capabilities or {})
    return {
        "supported_backends": [str(item).strip().lower() for item in data.get("supported_backends", []) if str(item).strip()],
        "profile_catalogs": {
            str(key).strip().lower(): [str(item).strip().lower() for item in value if str(item).strip()]
            for key, value in dict(data.get("profile_catalogs") or {}).items()
            if str(key).strip()
        },
        "permission_modes": [str(item).strip().lower() for item in data.get("permission_modes", []) if str(item).strip()],
        "backend_transport_modes": {
            str(key).strip().lower(): [str(item).strip().lower() for item in value if str(item).strip()]
            for key, value in dict(data.get("backend_transport_modes") or {}).items()
            if str(key).strip()
        },
    }


def _intersect_lists(left: list[str], right: list[str]) -> list[str]:
    if not left:
        return list(right)
    if not right:
        return list(left)
    right_set = set(right)
    return [item for item in left if item in right_set]


class PcControlRuntime:
    def __init__(
        self,
        *,
        credential_registry: InMemoryPcCredentialRegistry,
        node_store: InMemoryPcNodeStore,
        workspace_store: InMemoryWorkspaceInventoryStore,
        command_store: InMemoryPcCommandStore,
        keepalive_seconds: int = 15,
        connection_id_factory=None,
        message_id_factory=None,
        clock=None,
    ) -> None:
        if not isinstance(keepalive_seconds, int) or keepalive_seconds <= 0:
            raise ValueError("keepalive_seconds must be a positive integer")
        self._credential_registry = credential_registry
        self._node_store = node_store
        self._workspace_store = workspace_store
        self._command_store = command_store
        self._keepalive_seconds = keepalive_seconds
        self._connection_id_factory = connection_id_factory or (lambda pc_id: f"pc-ctrl:{pc_id}:{secrets.token_hex(4)}")
        self._message_id_factory = message_id_factory or (lambda prefix: f"{prefix}:{secrets.token_hex(4)}")
        self._clock = clock or _timestamp

    @property
    def credential_registry(self) -> InMemoryPcCredentialRegistry:
        return self._credential_registry

    @property
    def node_store(self) -> InMemoryPcNodeStore:
        return self._node_store

    @property
    def workspace_store(self) -> InMemoryWorkspaceInventoryStore:
        return self._workspace_store

    @property
    def command_store(self) -> InMemoryPcCommandStore:
        return self._command_store

    def handle_hello(
        self,
        message: PcHelloMessage,
        *,
        provided_token: str | None,
    ) -> tuple[dict[str, Any], str, int]:
        now = self._clock()
        credential = self._credential_registry.resolve_token(str(provided_token or ""))
        if credential is None:
            return self._build_error(message, code="unauthorized", error_message="transport token mismatch"), "", 0
        if credential.pc_id is not None and credential.pc_id != message.pc_id:
            return self._build_error(message, code="pc_id_mismatch", error_message="pc_id does not match credential"), "", 0
        connection_id = self._connection_id_factory(message.pc_id)
        node = self._node_store.register_connection(
            pc_id=message.pc_id,
            display_name=message.payload["display_name"],
            auth_credential_id=credential.auth_credential_id,
            connection_id=connection_id,
            connected_at=now,
            last_seen_at=message.sent_at,
            client_version=message.payload["client_version"],
            host_fingerprint=message.payload["host_fingerprint"],
            runtime_fingerprint=message.payload["runtime_fingerprint"],
            capabilities=message.payload["capabilities"],
        )
        return (
            build_pc_hello_ack(
                message_id=self._message_id_factory("hello_ack"),
                trace_id=message.trace_id,
                pc_id=message.pc_id,
                connection_epoch=node.current_connection_epoch,
                sent_at=now,
                keepalive_seconds=self._keepalive_seconds,
            ),
            connection_id,
            node.current_connection_epoch,
        )

    def handle_heartbeat(
        self,
        message: PcHeartbeatMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
                active_run_count=message.payload["active_run_count"],
                workspace_count=message.payload["workspace_count"],
                load_hint=message.payload["load_hint"],
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def handle_workspace_snapshot(
        self,
        message: PcWorkspaceSnapshotMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
                workspace_count=len(message.payload["workspaces"]),
            )
            self._workspace_store.replace_snapshot(
                pc_id=message.pc_id,
                snapshot_id=message.payload["snapshot_id"],
                workspaces=message.payload["workspaces"],
                updated_at=message.sent_at,
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except WorkspaceInventoryConflictError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def handle_command_ack(
        self,
        message: PcCommandAckMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            self._command_store.record_ack(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
                ack_status=message.payload["ack_status"],
                ack_message_id=message.message_id,
                acked_at=message.sent_at,
                queue_position=message.payload["queue_position"],
                reason=message.payload["reason"],
                error_code=message.payload["error_code"],
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (PcCommandConflictError, PcCommandUnknownError) as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def handle_event(
        self,
        message: PcCommandEventMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            self._command_store.record_event(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
                event=PcCommandEventRecord(
                    event_id=message.payload["event_id"],
                    event_type=message.payload["event_type"],
                    event_message_id=message.message_id,
                    trace_id=message.trace_id,
                    connection_epoch=message.connection_epoch,
                    sent_at=message.sent_at,
                    summary=message.payload["summary"],
                    effective_execution=message.payload["effective_execution"],
                    payload=message.payload["payload"],
                ),
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (PcCommandConflictError, PcCommandUnknownError) as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def handle_result(
        self,
        message: PcCommandResultMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            self._command_store.record_result(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
                result=PcCommandResultRecord(
                    result_id=message.payload["result_id"],
                    result_message_id=message.message_id,
                    trace_id=message.trace_id,
                    connection_epoch=message.connection_epoch,
                    sent_at=message.sent_at,
                    final_status=message.payload["final_status"],
                    summary=message.payload["summary"],
                    structured_payload=message.payload["structured_payload"],
                    effective_execution=message.payload["effective_execution"],
                    error_code=message.payload["error_code"],
                    error_message=message.payload["error_message"],
                ),
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (PcCommandConflictError, PcCommandUnknownError) as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def handle_output_chunk(
        self,
        message: PcOutputChunkMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            self._command_store.record_output_chunk(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
                chunk=PcOutputChunkRecord(
                    output_chunk_id=message.payload["output_chunk_id"],
                    output_message_id=message.message_id,
                    trace_id=message.trace_id,
                    connection_epoch=message.connection_epoch,
                    sent_at=message.sent_at,
                    stream_id=message.payload["stream_id"],
                    stream_id_source=message.payload["stream_id_source"],
                    seq=message.payload["seq"],
                    kind=message.payload["kind"],
                    text=message.payload["text"],
                    delta=message.payload["delta"],
                    item_type=message.payload["item_type"],
                    status=message.payload["status"],
                ),
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (PcCommandConflictError, PcCommandUnknownError) as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def handle_artifact_manifest(
        self,
        message: PcArtifactManifestMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any] | None:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            self._command_store.record_artifact_manifest(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
                manifest=PcArtifactManifestRecord(
                    manifest_id=message.payload["manifest_id"],
                    manifest_message_id=message.message_id,
                    trace_id=message.trace_id,
                    connection_epoch=message.connection_epoch,
                    sent_at=message.sent_at,
                    artifacts_root=message.payload["artifacts_root"],
                    source=message.payload["source"],
                    artifacts=message.payload["artifacts"],
                ),
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (PcCommandConflictError, PcCommandUnknownError) as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        return None

    def enqueue_command(self, message: PcCommandDispatchMessage) -> PcCommandRecord:
        node = self._require_online_node(message.pc_id)
        workspace = self._workspace_store.get_workspace(message.pc_id, message.payload["workspace_id"])
        if workspace is None:
            raise PcCommandDispatchValidationError(
                "unknown_workspace",
                f"workspace_id not found on target pc: {message.payload['workspace_id']}",
            )
        self._validate_dispatch_policy(message, node=node, workspace=workspace)
        record, _created = self._command_store.upsert_dispatch(
            PcCommandRecord(
                pc_id=message.pc_id,
                workspace_id=message.payload["workspace_id"],
                command_id=message.payload["command_id"],
                command_type=message.payload["command_type"],
                session_id=message.payload["session_id"],
                trace_id=message.trace_id,
                dispatch_message_id=message.message_id,
                created_at=message.sent_at,
                execution_policy=message.payload["execution_policy"],
                command_payload=message.payload["payload"],
            )
        )
        return record

    def collect_pending_dispatches(
        self,
        *,
        pc_id: str,
        connection_id: str,
        connection_epoch: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        node = self._require_connection(pc_id=pc_id, connection_id=connection_id, connection_epoch=connection_epoch)
        if node.status != "online":
            return []
        records = self._command_store.collect_pending_dispatches(
            pc_id=pc_id,
            connection_epoch=connection_epoch,
            dispatched_at=self._clock(),
            limit=limit,
        )
        return [
            build_command_dispatch(
                message_id=record.dispatch_message_id,
                trace_id=record.trace_id,
                pc_id=record.pc_id,
                connection_epoch=connection_epoch,
                sent_at=record.dispatched_at or self._clock(),
                command_id=record.command_id,
                command_type=record.command_type,
                workspace_id=record.workspace_id,
                session_id=record.session_id,
                execution_policy=record.execution_policy,
                command_payload=record.command_payload,
            )
            for record in records
        ]

    def collect_output_resume_requests(
        self,
        *,
        pc_id: str,
        connection_id: str,
        connection_epoch: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        node = self._require_connection(pc_id=pc_id, connection_id=connection_id, connection_epoch=connection_epoch)
        if node.status != "online":
            return []
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")

        in_progress_event_types = {"running", "awaiting_user_input", "paused"}
        requests: list[dict[str, Any]] = []
        for record in self._command_store.list_commands(pc_id=pc_id):
            if record.result is not None:
                continue
            if record.output_chunks:
                stream_cursors: dict[str, tuple[int, str | None]] = {}
                for chunk in record.output_chunks:
                    existing = stream_cursors.get(chunk.stream_id)
                    if existing is None or chunk.seq > existing[0]:
                        stream_cursors[chunk.stream_id] = (chunk.seq, chunk.stream_id_source)
                for stream_id, (after_seq, stream_id_source) in sorted(stream_cursors.items()):
                    requests.append(
                        build_output_resume_request(
                            message_id=self._message_id_factory("output_resume_request"),
                            trace_id=record.trace_id,
                            pc_id=record.pc_id,
                            connection_epoch=connection_epoch,
                            sent_at=self._clock(),
                            request_id=f"output_resume_request:{record.command_id}:{stream_id}:{after_seq}",
                            command_id=record.command_id,
                            stream_id=stream_id,
                            stream_id_source=stream_id_source,
                            after_seq=after_seq,
                            reason="reconnect_resume",
                        )
                    )
                    if len(requests) >= limit:
                        return requests
                continue
            if record.latest_event_type not in in_progress_event_types:
                continue
            requests.append(
                build_output_resume_request(
                    message_id=self._message_id_factory("output_resume_request"),
                    trace_id=record.trace_id,
                    pc_id=record.pc_id,
                    connection_epoch=connection_epoch,
                    sent_at=self._clock(),
                    request_id=f"output_resume_request:{record.command_id}:all:0",
                    command_id=record.command_id,
                    after_seq=0,
                    reason="reconnect_resume",
                )
            )
            if len(requests) >= limit:
                return requests
        return requests

    def close_connection(self, *, pc_id: str, connection_id: str, connection_epoch: int) -> None:
        self._node_store.close_connection(
            pc_id=pc_id,
            connection_id=connection_id,
            connection_epoch=connection_epoch,
            closed_at=self._clock(),
        )

    def _require_online_node(self, pc_id: str) -> PcNodeRecord:
        node = self._node_store.get_node(pc_id, now=self._clock())
        if node is None:
            raise PcCommandDispatchValidationError("unknown_pc", f"pc_id not found: {pc_id}")
        if node.status != "online":
            raise PcCommandDispatchValidationError("pc_offline", f"pc_id is not online: {pc_id}")
        return node

    def _require_connection(self, *, pc_id: str, connection_id: str, connection_epoch: int) -> PcNodeRecord:
        node = self._node_store.get_node(pc_id, now=self._clock())
        if node is None:
            raise PcCommandDispatchValidationError("unknown_pc", f"pc_id not found: {pc_id}")
        if node.current_connection_epoch != connection_epoch:
            raise PcCommandDispatchValidationError("stale_connection_epoch", f"stale connection_epoch for {pc_id}")
        if node.current_connection_id != connection_id:
            raise PcCommandDispatchValidationError("connection_id_mismatch", f"connection_id mismatch for {pc_id}")
        return node

    def _validate_dispatch_policy(
        self,
        message: PcCommandDispatchMessage,
        *,
        node: PcNodeRecord,
        workspace: PcWorkspaceRecord,
    ) -> None:
        effective_capabilities = self._effective_capabilities(node=node, workspace=workspace)
        policy = dict(message.payload["execution_policy"])
        command_type = str(message.payload["command_type"]).strip().lower()
        backend = str(policy.get("backend") or "").strip().lower()
        if not backend:
            if command_type == "new_task":
                raise PcCommandDispatchValidationError(
                    "unsupported_backend",
                    "new_task requires execution_policy.backend",
                )
            return
        if backend not in effective_capabilities["supported_backends"]:
            raise PcCommandDispatchValidationError(
                "unsupported_backend",
                f"backend is not supported by target pc/workspace: {backend}",
            )

        profile = str(policy.get("profile") or "").strip().lower()
        if profile:
            profile_catalog = effective_capabilities["profile_catalogs"].get(backend, [])
            if profile not in profile_catalog:
                raise PcCommandDispatchValidationError(
                    "unsupported_profile",
                    f"profile is not supported by target pc/workspace: {backend}/{profile}",
                )

        permission = str(policy.get("permission") or "").strip().lower()
        if permission and permission not in effective_capabilities["permission_modes"]:
            raise PcCommandDispatchValidationError(
                "unsupported_permission",
                f"permission is not supported by target pc/workspace: {permission}",
            )

        backend_transport = str(policy.get("backend_transport") or "").strip().lower()
        if backend_transport:
            supported_transports = effective_capabilities["backend_transport_modes"].get(backend, [])
            if backend_transport not in supported_transports:
                raise PcCommandDispatchValidationError(
                    "unsupported_backend_transport",
                    f"backend_transport is not supported by target pc/workspace: {backend}/{backend_transport}",
                )

    @staticmethod
    def _effective_capabilities(*, node: PcNodeRecord, workspace: PcWorkspaceRecord) -> dict[str, Any]:
        node_caps = _normalized_capabilities(node.capabilities)
        workspace_caps = _normalized_capabilities(workspace.capabilities)
        supported_backends = _intersect_lists(node_caps["supported_backends"], workspace_caps["supported_backends"])
        profile_catalogs: dict[str, list[str]] = {}
        backend_transport_modes: dict[str, list[str]] = {}
        for backend in supported_backends:
            profile_catalogs[backend] = _intersect_lists(
                node_caps["profile_catalogs"].get(backend, []),
                workspace_caps["profile_catalogs"].get(backend, []),
            )
            backend_transport_modes[backend] = _intersect_lists(
                node_caps["backend_transport_modes"].get(backend, []),
                workspace_caps["backend_transport_modes"].get(backend, []),
            )
        return {
            "supported_backends": supported_backends,
            "profile_catalogs": profile_catalogs,
            "permission_modes": _intersect_lists(
                node_caps["permission_modes"],
                workspace_caps["permission_modes"],
            ),
            "backend_transport_modes": backend_transport_modes,
        }

    def _build_error(self, message, *, code: str, error_message: str) -> dict[str, Any]:
        return build_pc_error(
            message_id=self._message_id_factory("error"),
            trace_id=message.trace_id,
            pc_id=getattr(message, "pc_id", None),
            connection_epoch=getattr(message, "connection_epoch", 0),
            sent_at=self._clock(),
            code=code,
            message=error_message,
        )


def build_pc_control_runtime(
    config: RelayServerConfig,
    *,
    credential_registry: InMemoryPcCredentialRegistry | None = None,
    node_store: InMemoryPcNodeStore | None = None,
    workspace_store: InMemoryWorkspaceInventoryStore | None = None,
    command_store: InMemoryPcCommandStore | None = None,
    keepalive_seconds: int = 15,
    clock=None,
) -> PcControlRuntime:
    state_root = Path(config.state_dir) / "pc_control"
    resolved_credential_registry = credential_registry or PersistentPcCredentialRegistry(
        Path(config.pc_control_credentials_path).expanduser()
        if config.pc_control_credentials_path
        else state_root / "pc_credentials.json",
        default_transport_token=config.transport_token,
    )
    resolved_node_store = node_store or PersistentPcNodeStore(state_root / "pc_nodes.json")
    resolved_workspace_store = workspace_store or PersistentWorkspaceInventoryStore(state_root / "workspaces.json")
    resolved_command_store = command_store or PersistentPcCommandStore(state_root / "commands.json")
    return PcControlRuntime(
        credential_registry=resolved_credential_registry,
        node_store=resolved_node_store,
        workspace_store=resolved_workspace_store,
        command_store=resolved_command_store,
        keepalive_seconds=keepalive_seconds,
        clock=clock,
    )
