"""Runtime for the VPS-first PC control plane."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import asdict
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
    PcProjectionBatchMessage,
    PcCommandResultMessage,
    PcIngressCandidateMessage,
    PcMailboxLeaseMessage,
    PcTerminalOutcomeMessage,
    PcThreadBindingMessage,
    PcOutputChunkMessage,
    PcHeartbeatMessage,
    PcHelloMessage,
    PcWorkspaceSnapshotMessage,
    build_command_dispatch,
    build_delivery_ack,
    build_ingress_decision,
    build_mailbox_lease_ack,
    build_output_resume_request,
    build_pc_error,
    build_pc_hello_ack,
    build_terminal_outcome_ack,
    build_thread_binding_ack,
)
from .pc_credential_registry import InMemoryPcCredentialRegistry, PersistentPcCredentialRegistry
from .pc_execution_policy import compute_effective_capabilities, validate_execution_policy
from .pc_ingress_store import InMemoryPcIngressStore, PersistentPcIngressStore
from .pc_node_store import InMemoryPcNodeStore, PcNodeFenceError, PcNodeRecord, PersistentPcNodeStore
from .projection_store import (
    ProjectionCloseoutUpsert,
    ProjectionProbeObservationUpsert,
    ProjectionRoundUpsert,
    ProjectionSessionBatch,
    ProjectionSessionUpsert,
    ProjectionStoreConflictError,
    RelayProjectionStore,
)
from .workspace_inventory_store import (
    InMemoryWorkspaceInventoryStore,
    PcWorkspaceRecord,
    PersistentWorkspaceInventoryStore,
    WorkspaceInventoryConflictError,
)

LOGGER = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class PcCommandDispatchValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "").strip()
        self.message = str(message or "").strip()
        super().__init__(self.message)


class PcControlRuntime:
    def __init__(
        self,
        *,
        credential_registry: InMemoryPcCredentialRegistry,
        node_store: InMemoryPcNodeStore,
        workspace_store: InMemoryWorkspaceInventoryStore,
        command_store: InMemoryPcCommandStore,
        ingress_store: InMemoryPcIngressStore | None = None,
        projection_store: RelayProjectionStore | None = None,
        keepalive_seconds: int = 15,
        connection_id_factory=None,
        message_id_factory=None,
        clock=None,
        command_enqueue_callback: Callable[[PcCommandRecord], None] | None = None,
    ) -> None:
        if not isinstance(keepalive_seconds, int) or keepalive_seconds <= 0:
            raise ValueError("keepalive_seconds must be a positive integer")
        self._credential_registry = credential_registry
        self._node_store = node_store
        self._workspace_store = workspace_store
        self._command_store = command_store
        self._ingress_store = ingress_store or InMemoryPcIngressStore()
        self._projection_store = projection_store
        self._keepalive_seconds = keepalive_seconds
        self._connection_id_factory = connection_id_factory or (lambda pc_id: f"pc-ctrl:{pc_id}:{secrets.token_hex(4)}")
        self._message_id_factory = message_id_factory or (lambda prefix: f"{prefix}:{secrets.token_hex(4)}")
        self._clock = clock or _timestamp
        self._command_enqueue_callback = command_enqueue_callback

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

    @property
    def ingress_store(self) -> InMemoryPcIngressStore:
        return self._ingress_store

    @property
    def projection_store(self) -> RelayProjectionStore | None:
        return self._projection_store

    @property
    def keepalive_seconds(self) -> int:
        return self._keepalive_seconds

    def set_command_enqueue_callback(self, callback: Callable[[PcCommandRecord], None] | None) -> None:
        self._command_enqueue_callback = callback

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
            self._freeze_live_process_for_terminal_result(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
            )
            return self._build_delivery_ack(
                message,
                request_id=message.payload["result_id"],
                message_type="result",
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (PcCommandConflictError, PcCommandUnknownError) as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)

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
            self._project_live_process_from_command(
                pc_id=message.pc_id,
                command_id=message.payload["command_id"],
                stream_id=message.payload["stream_id"],
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

    def handle_projection_batch(
        self,
        message: PcProjectionBatchMessage,
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
            if self._projection_store is None:
                raise PcCommandDispatchValidationError(
                    "projection_store_unavailable",
                    "projection store is not configured",
                )
            if message.payload["scope"] == "probe":
                self._apply_probe_projection_batch(message)
            else:
                self._apply_session_projection_batch(message)
            return self._build_delivery_ack(
                message,
                request_id=message.payload["batch_id"],
                message_type="projection_batch",
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)
        except (ProjectionStoreConflictError, PcCommandDispatchValidationError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_projection_batch")
            return self._build_error(message, code=code, error_message=str(exc))

    def handle_mailbox_lease(
        self,
        message: PcMailboxLeaseMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any]:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            operation = message.payload["operation"]
            if operation == "acquire":
                lease_status, record, reason = self._ingress_store.acquire_mailbox_lease(
                    mailbox_key=message.payload["mailbox_key"],
                    lease_holder_id=message.payload["lease_holder_id"],
                    pc_id=message.pc_id,
                    acquired_at=message.sent_at,
                    lease_ttl_seconds=message.payload["lease_ttl_seconds"],
                    config_fingerprint=message.payload["config_fingerprint"],
                    host_fingerprint=message.payload["host_fingerprint"],
                    runtime_fingerprint=message.payload["runtime_fingerprint"],
                    last_seen_thread_id=message.payload["last_seen_thread_id"],
                    last_seen_ingress_id=message.payload["last_seen_ingress_id"],
                )
            elif operation == "renew":
                lease_status, record, reason = self._ingress_store.renew_mailbox_lease(
                    mailbox_key=message.payload["mailbox_key"],
                    lease_holder_id=message.payload["lease_holder_id"],
                    pc_id=message.pc_id,
                    lease_epoch=int(message.payload["lease_epoch"] or 0),
                    renewed_at=message.sent_at,
                    lease_ttl_seconds=message.payload["lease_ttl_seconds"],
                    last_seen_thread_id=message.payload["last_seen_thread_id"],
                    last_seen_ingress_id=message.payload["last_seen_ingress_id"],
                )
            else:
                lease_status, record, reason = self._ingress_store.release_mailbox_lease(
                    mailbox_key=message.payload["mailbox_key"],
                    lease_holder_id=message.payload["lease_holder_id"],
                    pc_id=message.pc_id,
                    lease_epoch=int(message.payload["lease_epoch"] or 0),
                    released_at=message.sent_at,
                )
            return build_mailbox_lease_ack(
                message_id=self._message_id_factory("mailbox_lease_ack"),
                trace_id=message.trace_id,
                pc_id=message.pc_id,
                connection_epoch=message.connection_epoch,
                sent_at=self._clock(),
                request_id=message.payload["request_id"],
                operation=operation,
                mailbox_key=message.payload["mailbox_key"],
                lease_status=lease_status,
                lease_holder_id=(None if record is None else record.lease_holder_id),
                lease_pc_id=(None if record is None else record.pc_id),
                lease_epoch=(None if record is None else record.lease_epoch),
                expires_at=(None if record is None else record.expires_at),
                reason=reason,
                degraded_mode=message.payload["degraded_mode"],
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)

    def handle_ingress_candidate(
        self,
        message: PcIngressCandidateMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any]:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            record = self._ingress_store.register_ingress_candidate(
                mailbox_key=message.payload["mailbox_key"],
                lease_holder_id=message.payload["lease_holder_id"],
                pc_id=message.pc_id,
                lease_epoch=message.payload["lease_epoch"],
                folder=message.payload["folder"],
                uid_validity=message.payload["uid_validity"],
                uid=message.payload["uid"],
                message_id=message.payload["message_id"],
                in_reply_to=message.payload["in_reply_to"],
                references_hash=message.payload["references_hash"],
                from_addr=message.payload["from_addr"],
                subject=message.payload["subject"],
                subject_norm=message.payload["subject_norm"],
                raw_date=message.payload["raw_date"],
                observed_at=message.sent_at,
                classification=message.payload["classification"],
                candidate_status=message.payload["candidate_status"],
                candidate_reason=message.payload["candidate_reason"],
                taskmail_request_id=message.payload["taskmail_request_id"],
                packet_id=message.payload["packet_id"],
                degraded_mode=message.payload["degraded_mode"],
            )
            return build_ingress_decision(
                message_id=self._message_id_factory("ingress_decision"),
                trace_id=message.trace_id,
                pc_id=message.pc_id,
                connection_epoch=message.connection_epoch,
                sent_at=self._clock(),
                request_id=message.payload["request_id"],
                ingress_id=record.ingress_id,
                mailbox_key=record.mailbox_key,
                decision=record.decision,
                reason=record.decision_reason,
                classification=record.classification,
                lease_holder_id=record.lease_holder_id,
                lease_epoch=record.lease_epoch,
                thread_id=record.thread_id,
                session_id=record.session_id,
                degraded_mode=record.degraded_mode,
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)

    def handle_thread_binding(
        self,
        message: PcThreadBindingMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any]:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            binding_status, record, reason = self._ingress_store.commit_thread_binding(
                mailbox_key=message.payload["mailbox_key"],
                lease_holder_id=message.payload["lease_holder_id"],
                pc_id=message.pc_id,
                lease_epoch=message.payload["lease_epoch"],
                ingress_id=message.payload["ingress_id"],
                root_message_id=message.payload["root_message_id"],
                thread_id=message.payload["thread_id"],
                session_id=message.payload["session_id"],
                repo_path=message.payload["repo_path"],
                workdir=message.payload["workdir"],
                subject_norm=message.payload["subject_norm"],
                binding_created_at=message.sent_at,
                degraded_mode=message.payload["degraded_mode"],
            )
            return build_thread_binding_ack(
                message_id=self._message_id_factory("thread_binding_ack"),
                trace_id=message.trace_id,
                pc_id=message.pc_id,
                connection_epoch=message.connection_epoch,
                sent_at=self._clock(),
                request_id=message.payload["request_id"],
                ingress_id=message.payload["ingress_id"],
                binding_status=binding_status,
                reason=reason,
                thread_id=(None if record is None else record.thread_id),
                session_id=(None if record is None else record.session_id),
                degraded_mode=message.payload["degraded_mode"],
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)

    def handle_terminal_outcome(
        self,
        message: PcTerminalOutcomeMessage,
        *,
        connection_id: str,
    ) -> dict[str, Any]:
        try:
            self._node_store.touch_connection(
                pc_id=message.pc_id,
                connection_id=connection_id,
                connection_epoch=message.connection_epoch,
                last_seen_at=message.sent_at,
            )
            outcome_status, record, reason = self._ingress_store.commit_terminal_outcome(
                mailbox_key=message.payload["mailbox_key"],
                lease_holder_id=message.payload["lease_holder_id"],
                pc_id=message.pc_id,
                lease_epoch=message.payload["lease_epoch"],
                thread_id=message.payload["thread_id"],
                task_id=message.payload["task_id"],
                run_status=message.payload["run_status"],
                generated_at=message.payload["generated_at"],
                last_summary=message.payload["last_summary"],
                terminal_mail_message_id=message.payload["terminal_mail_message_id"],
                terminal_mail_subject=message.payload["terminal_mail_subject"],
                taskmail_request_id=message.payload["taskmail_request_id"],
                packet_id=message.payload["packet_id"],
                source_ingress_id=message.payload["source_ingress_id"],
                degraded_mode=message.payload["degraded_mode"],
            )
            return build_terminal_outcome_ack(
                message_id=self._message_id_factory("terminal_outcome_ack"),
                trace_id=message.trace_id,
                pc_id=message.pc_id,
                connection_epoch=message.connection_epoch,
                sent_at=self._clock(),
                request_id=message.payload["request_id"],
                thread_id=message.payload["thread_id"],
                task_id=message.payload["task_id"],
                outcome_status=outcome_status,
                reason=reason,
                source_ingress_id=(None if record is None else record.source_ingress_id),
                degraded_mode=message.payload["degraded_mode"],
            )
        except PcNodeFenceError as exc:
            return self._build_error(message, code=exc.code, error_message=exc.message)

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
        self._notify_command_enqueued(record)
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

    def get_mailbox_lease(self, *, mailbox_key: str) -> dict[str, Any] | None:
        record = self._ingress_store.get_mailbox_lease(mailbox_key, now=self._clock())
        return None if record is None else asdict(record)

    def get_node(self, *, pc_id: str) -> dict[str, Any] | None:
        record = self._node_store.get_node(pc_id, now=self._clock())
        return None if record is None else asdict(record)

    def list_nodes(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._node_store.list_nodes(now=self._clock())]

    def list_workspaces(self, *, pc_id: str | None = None) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._workspace_store.list_workspaces(pc_id=pc_id)]

    def get_command(self, *, pc_id: str, command_id: str) -> dict[str, Any] | None:
        record = self._command_store.get_command(pc_id, command_id)
        return None if record is None else asdict(record)

    def list_commands(self, *, pc_id: str | None = None) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._command_store.list_commands(pc_id=pc_id)]

    def list_thread_bindings(self, *, pc_id: str | None = None) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._ingress_store.list_thread_bindings(pc_id=pc_id)]

    def list_mailbox_lease_events(self, *, mailbox_key: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._ingress_store.list_lease_events(mailbox_key=mailbox_key, limit=limit)]

    def find_ingress(
        self,
        *,
        ingress_id: str | None = None,
        mailbox_key: str | None = None,
        message_id: str | None = None,
        uid: int | None = None,
        folder: str = "INBOX",
        uid_validity: int | None = None,
    ) -> dict[str, Any] | None:
        record = self._ingress_store.find_ingress(
            ingress_id=ingress_id,
            mailbox_key=mailbox_key,
            message_id=message_id,
            uid=uid,
            folder=folder,
            uid_validity=uid_validity,
        )
        return None if record is None else asdict(record)

    def find_terminal_outcome(self, *, thread_id: str) -> dict[str, Any] | None:
        record = self._ingress_store.find_terminal_outcome(thread_id=thread_id)
        return None if record is None else asdict(record)

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
        validation_error = validate_execution_policy(
            command_type=message.payload["command_type"],
            execution_policy=message.payload["execution_policy"],
            effective_capabilities=effective_capabilities,
        )
        if validation_error is not None:
            code, error_message = validation_error
            raise PcCommandDispatchValidationError(code, error_message)

    @staticmethod
    def _effective_capabilities(*, node: PcNodeRecord, workspace: PcWorkspaceRecord) -> dict[str, Any]:
        return compute_effective_capabilities(
            pc_capabilities=node.capabilities,
            workspace_capabilities=workspace.capabilities,
        )

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

    def _build_delivery_ack(self, message, *, request_id: str, message_type: str) -> dict[str, Any]:
        return build_delivery_ack(
            message_id=self._message_id_factory("delivery_ack"),
            trace_id=message.trace_id,
            pc_id=getattr(message, "pc_id", None),
            connection_epoch=getattr(message, "connection_epoch", 0),
            sent_at=self._clock(),
            request_id=request_id,
            message_type=message_type,
            delivery_status="committed",
        )

    def _notify_command_enqueued(self, record: PcCommandRecord) -> None:
        callback = self._command_enqueue_callback
        if callback is None:
            return
        try:
            callback(record)
        except Exception:
            LOGGER.exception(
                "pc-control enqueue callback failed pc_id=%s command_id=%s",
                record.pc_id,
                record.command_id,
            )

    def _project_live_process_from_command(
        self,
        *,
        pc_id: str,
        command_id: str,
        stream_id: str,
    ) -> None:
        if self._projection_store is None:
            return
        record = self._command_store.get_command(pc_id, command_id)
        if record is None or not record.session_id:
            return
        aggregate = self._aggregate_live_process(record=record, stream_id=stream_id)
        if aggregate is None or not aggregate["items"]:
            return
        self._projection_store.upsert_session_live_process(
            pc_id=record.pc_id,
            workspace_id=record.workspace_id,
            session_id=record.session_id,
            command_id=record.command_id,
            stream_id=stream_id,
            task_id=aggregate["task_id"],
            last_seq=aggregate["last_seq"],
            items=aggregate["items"],
            updated_at=aggregate["updated_at"],
            status=aggregate["status"],
        )

    def _freeze_live_process_for_terminal_result(
        self,
        *,
        pc_id: str,
        command_id: str,
    ) -> None:
        if self._projection_store is None:
            return
        record = self._command_store.get_command(pc_id, command_id)
        if record is None or not record.session_id or not record.output_chunks:
            return
        stream_id = self._latest_live_process_stream_id(record)
        if stream_id is None:
            return
        aggregate = self._aggregate_live_process(record=record, stream_id=stream_id)
        if aggregate is None or not aggregate["items"]:
            return
        self._projection_store.upsert_session_live_process(
            pc_id=record.pc_id,
            workspace_id=record.workspace_id,
            session_id=record.session_id,
            command_id=record.command_id,
            stream_id=stream_id,
            task_id=aggregate["task_id"],
            last_seq=aggregate["last_seq"],
            items=[
                {
                    **item,
                    "status": "completed",
                }
                for item in aggregate["items"]
            ],
            updated_at=aggregate["updated_at"],
            status="completed",
        )

    def _aggregate_live_process(
        self,
        *,
        record: PcCommandRecord,
        stream_id: str,
    ) -> dict[str, Any] | None:
        chunks = sorted(
            [chunk for chunk in record.output_chunks if chunk.stream_id == stream_id],
            key=lambda item: item.seq,
        )
        visible_chunks = (
            chunks
            if self._is_gap_free_live_process_window(chunks)
            else self._visible_live_process_chunks(chunks)
        )
        if not visible_chunks:
            return None
        items: list[dict[str, Any]] = []
        for chunk in visible_chunks:
            process_kind = self._live_process_kind(chunk)
            if items and items[-1]["kind"] == process_kind:
                current = dict(items[-1])
            else:
                current = {
                    "item_id": f"process:{stream_id}:{chunk.seq}",
                    "kind": process_kind,
                    "created_at": chunk.sent_at,
                    "updated_at": chunk.sent_at,
                    "status": chunk.status or "streaming",
                    "text": "",
                }
                items.append(current)
            current["text"] = chunk.text if chunk.text is not None else f"{current['text']}{chunk.delta or ''}"
            current["updated_at"] = chunk.sent_at
            current["status"] = chunk.status or current["status"] or "streaming"
            items[-1] = current
        normalized_items = [
            item
            for item in items
            if str(item.get("text") or "").strip()
        ]
        if not normalized_items:
            return None
        consumed_chunk = visible_chunks[-1]
        return {
            "task_id": self._command_task_id(record=record, stream_id=stream_id),
            "items": normalized_items,
            "last_seq": consumed_chunk.seq,
            "updated_at": consumed_chunk.sent_at,
            "status": "streaming",
        }

    @staticmethod
    def _is_gap_free_live_process_window(chunks: list[PcOutputChunkRecord]) -> bool:
        if not chunks:
            return False
        expected_seq = chunks[0].seq
        for chunk in chunks:
            if chunk.seq != expected_seq:
                return False
            expected_seq = chunk.seq + 1
        return chunks[0].seq == 1 or chunks[0].text is not None

    @staticmethod
    def _visible_live_process_chunks(chunks: list[PcOutputChunkRecord]) -> list[PcOutputChunkRecord]:
        if not chunks:
            return []
        latest_full_text_index = max(
            (index for index, chunk in enumerate(chunks) if chunk.text is not None),
            default=None,
        )
        start_index = latest_full_text_index if latest_full_text_index is not None else 0
        expected_seq = chunks[start_index].seq
        if latest_full_text_index is None and expected_seq != 1:
            return []
        visible: list[PcOutputChunkRecord] = []
        for chunk in chunks[start_index:]:
            if chunk.seq != expected_seq:
                break
            visible.append(chunk)
            expected_seq = chunk.seq + 1
        return visible

    @staticmethod
    def _latest_live_process_stream_id(record: PcCommandRecord) -> str | None:
        latest_chunk_by_stream: dict[str, tuple[str, int]] = {}
        for chunk in record.output_chunks:
            existing = latest_chunk_by_stream.get(chunk.stream_id)
            candidate = (chunk.sent_at, chunk.seq)
            if existing is None or candidate > existing:
                latest_chunk_by_stream[chunk.stream_id] = candidate
        if not latest_chunk_by_stream:
            return None
        return max(
            latest_chunk_by_stream.items(),
            key=lambda item: (item[1][0], item[1][1], item[0]),
        )[0]

    @staticmethod
    def _live_process_kind(chunk: PcOutputChunkRecord) -> str:
        kind = str(chunk.kind or "").strip().lower()
        item_type = str(chunk.item_type or "").strip().lower()
        if kind.startswith("assistant."):
            return "assistant"
        if kind.startswith("tool.") or "tool" in item_type:
            return "tool"
        if item_type == "reasoning" or kind.startswith("turn."):
            return "system"
        return "system"

    @staticmethod
    def _command_task_id(record: PcCommandRecord, *, stream_id: str) -> str | None:
        if record.result is not None:
            task_id = str(record.result.structured_payload.get("task_id") or "").strip()
            if task_id:
                return task_id
        for event in reversed(record.events):
            task_id = str(event.payload.get("task_id") or "").strip()
            if task_id:
                return task_id
        stream_parts = [part.strip() for part in str(stream_id or "").split(":") if part.strip()]
        return stream_parts[-1] if stream_parts else None

    def _apply_session_projection_batch(self, message: PcProjectionBatchMessage) -> None:
        if self._projection_store is None:
            raise PcCommandDispatchValidationError("projection_store_unavailable", "projection store is not configured")
        session_payload: dict[str, Any] | None = None
        round_payloads: list[dict[str, Any]] = []
        closeout_payloads: list[dict[str, Any]] = []
        for item in message.payload["items"]:
            item_type = str(item.get("type") or "").strip()
            if item_type == "session_projection_upsert":
                if session_payload is not None:
                    raise PcCommandDispatchValidationError(
                        "invalid_projection_batch",
                        "session projection batch may contain only one session_projection_upsert",
                    )
                session_payload = dict(item)
                continue
            if item_type == "session_round_upsert":
                round_payloads.append(dict(item))
                continue
            if item_type == "session_closeout_upsert":
                closeout_payloads.append(dict(item))
                continue
            raise PcCommandDispatchValidationError(
                "invalid_projection_batch",
                f"unsupported session projection item type: {item_type}",
            )
        if session_payload is None:
            raise PcCommandDispatchValidationError(
                "invalid_projection_batch",
                "session projection batch requires one session_projection_upsert item",
            )
        self._validate_session_projection_identity(message, session_payload)
        session_upsert = ProjectionSessionUpsert(**self._projection_session_upsert_payload(session_payload))
        rounds = [
            ProjectionRoundUpsert(**self._projection_round_upsert_payload(message, item))
            for item in round_payloads
        ]
        closeouts = [
            ProjectionCloseoutUpsert(**self._projection_closeout_upsert_payload(message, item))
            for item in closeout_payloads
        ]
        self._projection_store.apply_session_batch(
            ProjectionSessionBatch(
                batch_id=message.payload["batch_id"],
                connection_epoch=message.connection_epoch,
                sent_at=message.sent_at,
                session=session_upsert,
                rounds=rounds,
                closeouts=closeouts,
            )
        )

    def _apply_probe_projection_batch(self, message: PcProjectionBatchMessage) -> None:
        if self._projection_store is None:
            raise PcCommandDispatchValidationError("projection_store_unavailable", "projection store is not configured")
        if len(message.payload["items"]) != 1:
            raise PcCommandDispatchValidationError(
                "invalid_projection_batch",
                "probe projection batch requires exactly one item",
            )
        item = dict(message.payload["items"][0])
        item_type = str(item.get("type") or "").strip()
        if item_type != "transport_probe_observation_upsert":
            raise PcCommandDispatchValidationError(
                "invalid_projection_batch",
                f"unsupported probe projection item type: {item_type}",
            )
        self._projection_store.upsert_probe_observation(
            ProjectionProbeObservationUpsert(
                **{
                    key: value
                    for key, value in item.items()
                    if key
                    in {
                        "idempotency_key",
                        "probe_id",
                        "summary_text",
                        "observation_status",
                        "observed_at",
                        "payload",
                        "pc_id",
                        "request_id",
                        "packet_id",
                        "receipt_id",
                        "mailbox_message_id",
                    }
                }
            ),
            batch_id=message.payload["batch_id"],
        )

    def _validate_session_projection_identity(
        self,
        message: PcProjectionBatchMessage,
        session_payload: dict[str, Any],
    ) -> None:
        required_identity = {
            "pc_id": message.pc_id,
            "workspace_id": message.payload["workspace_id"],
            "session_id": message.payload["session_id"],
            "thread_id": message.payload["thread_id"],
            "projection_version": message.payload["projection_version"],
        }
        for field_name, expected in required_identity.items():
            actual = session_payload.get(field_name)
            if actual != expected:
                raise PcCommandDispatchValidationError(
                    "invalid_projection_batch",
                    f"{field_name} does not match the batch envelope",
                )

    @staticmethod
    def _projection_session_upsert_payload(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in item.items()
            if key
            in {
                "idempotency_key",
                "projection_version",
                "pc_id",
                "workspace_id",
                "session_id",
                "thread_id",
                "session_name",
                "backend",
                "backend_transport",
                "profile",
                "permission",
                "repo_path",
                "workdir",
                "list_status",
                "snapshot_status",
                "lifecycle",
                "current_task_id",
                "queued_task_id",
                "pending_task_count",
                "last_summary",
                "last_active_at",
                "last_progress_at",
                "paused_from_status",
                "backend_session_id",
                "backend_session_resumable",
                "question_state",
                "timeline_items",
                "created_at",
                "updated_at",
                "source_updated_at",
            }
        }

    def _projection_round_upsert_payload(
        self,
        message: PcProjectionBatchMessage,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        for field_name in ("pc_id", "workspace_id", "session_id", "thread_id", "projection_version"):
            expected = message.pc_id if field_name == "pc_id" else message.payload[field_name]
            if item.get(field_name) != expected:
                raise PcCommandDispatchValidationError(
                    "invalid_projection_batch",
                    f"{field_name} does not match the batch envelope",
                )
        payload = {
            key: value
            for key, value in item.items()
            if key
            in {
                "idempotency_key",
                "round_id",
                "task_id",
                "round_sort_at",
                "status",
                "speaker_label",
                "input_text",
                "process_items",
                "result_text",
                "input_attachments",
                "result_attachments",
                "source_updated_at",
                "projection_version",
            }
        }
        payload["created_at"] = item.get("round_sort_at") or message.sent_at
        return payload

    def _projection_closeout_upsert_payload(
        self,
        message: PcProjectionBatchMessage,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        for field_name in ("pc_id", "workspace_id", "session_id", "thread_id"):
            expected = message.pc_id if field_name == "pc_id" else message.payload[field_name]
            if item.get(field_name) != expected:
                raise PcCommandDispatchValidationError(
                    "invalid_projection_batch",
                    f"{field_name} does not match the batch envelope",
                )
        payload = {
            key: value
            for key, value in item.items()
            if key
            in {
                "idempotency_key",
                "closeout_key",
                "task_id",
                "request_id",
                "packet_id",
                "receipt_id",
                "action_type",
                "target_session_identity",
                "last_summary",
                "terminal_mail_message_id",
                "terminal_mail_subject",
                "generated_at",
                "source_updated_at",
                "projection_version",
            }
        }
        if payload.get("projection_version") is None:
            payload["projection_version"] = message.payload["projection_version"]
        return payload


def build_pc_control_runtime(
    config: RelayServerConfig,
    *,
    credential_registry: InMemoryPcCredentialRegistry | None = None,
    node_store: InMemoryPcNodeStore | None = None,
    workspace_store: InMemoryWorkspaceInventoryStore | None = None,
    command_store: InMemoryPcCommandStore | None = None,
    ingress_store: InMemoryPcIngressStore | None = None,
    keepalive_seconds: int = 15,
    clock=None,
    command_enqueue_callback: Callable[[PcCommandRecord], None] | None = None,
) -> PcControlRuntime:
    state_root = Path(config.state_dir) / "pc_control"
    state_root.mkdir(parents=True, exist_ok=True)
    resolved_credential_registry = credential_registry or PersistentPcCredentialRegistry(
        Path(config.pc_control_credentials_path).expanduser()
        if config.pc_control_credentials_path
        else state_root / "pc_credentials.json",
        default_transport_token=config.transport_token,
    )
    resolved_node_store = node_store or PersistentPcNodeStore(state_root / "pc_nodes.json")
    resolved_workspace_store = workspace_store or PersistentWorkspaceInventoryStore(state_root / "workspaces.json")
    resolved_command_store = command_store or PersistentPcCommandStore(state_root / "commands.json")
    resolved_ingress_store = ingress_store or PersistentPcIngressStore(state_root / "ingress_truth.json")
    projection_store_path = Path(config.resolved_android_projection_store_path)
    projection_store_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_projection_store = RelayProjectionStore(projection_store_path)
    return PcControlRuntime(
        credential_registry=resolved_credential_registry,
        node_store=resolved_node_store,
        workspace_store=resolved_workspace_store,
        command_store=resolved_command_store,
        ingress_store=resolved_ingress_store,
        projection_store=resolved_projection_store,
        keepalive_seconds=keepalive_seconds,
        clock=clock,
        command_enqueue_callback=command_enqueue_callback,
    )
