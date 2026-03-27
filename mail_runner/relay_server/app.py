"""Runnable relay server with health and WebSocket transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
import threading
from datetime import datetime
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets

from ..config import AppConfig, load_config
from ..file_surface import FileSurfaceStore, FileSurfaceUploadError
from .android_create_session_facade import (
    ANDROID_CREATE_SESSION_PATH,
    AndroidCreateSessionContractError,
    AndroidCreateSessionRequestError,
    AndroidCreateSessionSubmitTimeout,
    submit_android_create_session_command,
)
from .android_environment_inventory_facade import (
    ANDROID_ENVIRONMENT_INVENTORY_PATH,
    build_android_environment_inventory_snapshot,
)
from .android_sessions_facade import (
    ANDROID_SESSIONS_PATH,
    build_android_sessions_snapshot,
)
from .android_session_snapshot_facade import (
    ANDROID_SESSION_SNAPSHOT_PATH,
    AndroidSessionSnapshotFacadeError,
    build_android_session_snapshot,
)
from .auth import token_fingerprint, validate_bearer_token, validate_transport_token
from .config import RelayServerConfig, load_relay_server_config
from .control_protocol import (
    ControlBridgeError,
    ControlCommandMessage,
    ControlHelloMessage,
    ControlPingMessage,
    build_control_hello_ack,
    build_control_pong,
    build_relay_packet_from_control_command,
    negotiate_control_payload_schemas,
    parse_control_client_message,
    translate_relay_response_to_control,
)
from .delivery import RelayPacketDeliverer
from .direct_actions import (
    RelayTaskMailDirectNewTaskMailBridge,
    RelayTaskMailDirectProjectSyncHandler,
    RelayTaskMailDirectProjectSyncMailBridge,
)
from .loopback import LoopbackRelayServer
from .packet_store import InMemoryAcceptedPacketStore, PersistentAcceptedPacketStore
from .pc_control_protocol import (
    PcArtifactManifestMessage,
    PcCommandAckMessage,
    PcCommandEventMessage,
    PcCommandResultMessage,
    PcControlProtocolError,
    PcIngressCandidateMessage,
    PcMailboxLeaseMessage,
    PcOutputChunkMessage,
    PcTerminalOutcomeMessage,
    PcThreadBindingMessage,
    PcHeartbeatMessage,
    PcHelloMessage,
    PcWorkspaceSnapshotMessage,
    build_command_dispatch,
    build_pc_error,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from .pc_control_runtime import PcCommandDispatchValidationError, PcControlRuntime, build_pc_control_runtime
from .post_creation_actions import (
    RelayTaskMailDirectCurrentSessionReplyMailBridge,
    RelayTaskMailDirectCurrentSessionStatusMailBridge,
)
from .session_store import InMemorySessionStore, PersistentSessionStore
from .transport_probe import RelayTaskMailTransportProbeHandler

LOGGER = logging.getLogger(__name__)
_HTTP_RESPONSE_HEADERS = [("Content-Type", "application/json; charset=utf-8")]
_HTTP_HEADER_LIMIT_BYTES = 64 * 1024
_RELAY_WEBSOCKET_PATH = "/relay"
_CONTROL_WEBSOCKET_PATH = "/control"
_PC_CONTROL_WEBSOCKET_PATH = "/pc-control"
_PC_CONTROL_OPERATOR_DISPATCH_PATH = "/debug/pc-control/dispatch"
_PC_CONTROL_OPERATOR_NODES_PATH = "/debug/pc-control/nodes"
_PC_CONTROL_OPERATOR_WORKSPACES_PATH = "/debug/pc-control/workspaces"
_PC_CONTROL_OPERATOR_COMMANDS_PATH = "/debug/pc-control/commands"
_PC_CONTROL_OPERATOR_LEASE_PATH = "/debug/pc-control/lease"
_PC_CONTROL_OPERATOR_INGRESS_PATH = "/debug/pc-control/ingress"
_PC_CONTROL_OPERATOR_OUTCOME_PATH = "/debug/pc-control/terminal-outcome"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _task_root_diagnostics(task_root: str | None) -> dict[str, Any]:
    configured_path = str(task_root or "").strip() or None
    if configured_path is None:
        return {
            "configured_path": None,
            "exists": False,
            "is_dir": False,
            "scheduler_present": False,
            "thread_count": 0,
        }

    path = Path(configured_path)
    exists = path.exists()
    is_dir = path.is_dir() if exists else False
    scheduler_present = (path / "_scheduler").is_dir() if is_dir else False
    thread_count = 0
    if is_dir:
        try:
            thread_count = sum(1 for item in path.iterdir() if item.is_dir() and item.name.startswith("thread_"))
        except OSError:
            thread_count = 0
    return {
        "configured_path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "scheduler_present": scheduler_present,
        "thread_count": thread_count,
    }


def _taskmail_direct_ingress_enabled(
    config: RelayServerConfig,
    *,
    runner_config: AppConfig | None = None,
) -> bool:
    if not config.taskmail_direct_ingress_enabled:
        return False
    if runner_config is None:
        return True
    return runner_config.mail_ingress_enabled


def build_health_payload(
    config: RelayServerConfig,
    session_store,
    *,
    packet_store=None,
    pc_control_runtime: PcControlRuntime | None = None,
    runner_config: AppConfig | None = None,
    listening_host: str | None = None,
    listening_port: int | None = None,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": config.server_name,
        "listen": {
            "host": listening_host or config.host,
            "port": listening_port if listening_port is not None else config.port,
        },
        "session_count": session_store.count(),
        "packet_count": packet_store.count() if packet_store is not None else 0,
        "state_dir": config.state_dir,
        "tls_enabled": bool(config.tls_certfile),
        "taskmail_direct_ingress_enabled": _taskmail_direct_ingress_enabled(
            config,
            runner_config=runner_config,
        ),
        "task_root": _task_root_diagnostics(config.task_root or None),
        "auth": {
            "transport_token_id": token_fingerprint(config.transport_token),
        },
        "pc_control": {
            "node_count": pc_control_runtime.node_store.count() if pc_control_runtime is not None else 0,
            "workspace_count": pc_control_runtime.workspace_store.count() if pc_control_runtime is not None else 0,
            "command_count": pc_control_runtime.command_store.count() if pc_control_runtime is not None else 0,
            "lease_count": pc_control_runtime.ingress_store.count_leases() if pc_control_runtime is not None else 0,
            "ingress_count": pc_control_runtime.ingress_store.count_ingress() if pc_control_runtime is not None else 0,
            "binding_count": pc_control_runtime.ingress_store.count_bindings() if pc_control_runtime is not None else 0,
            "terminal_outcome_count": (
                pc_control_runtime.ingress_store.count_terminal_outcomes() if pc_control_runtime is not None else 0
            ),
        },
    }


def build_runtime_relay(
    config: RelayServerConfig,
    *,
    session_store,
    packet_store,
) -> LoopbackRelayServer:
    runner_config = load_config()
    deliverer = RelayPacketDeliverer(config)
    direct_packet_handlers: list[Any] = []
    if config.taskmail_direct_ingress_enabled:
        if runner_config.mail_ingress_enabled:
            direct_packet_handlers.extend(
                [
                    RelayTaskMailDirectNewTaskMailBridge(config),
                    RelayTaskMailDirectCurrentSessionStatusMailBridge(config, task_root=config.task_root or None),
                    RelayTaskMailDirectCurrentSessionReplyMailBridge(config, task_root=config.task_root or None),
                ]
            )
        if str(config.task_root or "").strip():
            direct_packet_handlers.append(RelayTaskMailDirectProjectSyncHandler(config=runner_config))
        if runner_config.mail_ingress_enabled:
            direct_packet_handlers.append(RelayTaskMailDirectProjectSyncMailBridge(config))
            direct_packet_handlers.append(RelayTaskMailTransportProbeHandler(config))
    return LoopbackRelayServer(
        config,
        session_store=session_store,
        packet_store=packet_store,
        delivery_callback=deliverer.deliver,
        direct_packet_handlers=direct_packet_handlers,
    )


def _resolve_control_payload_schemas(relay: LoopbackRelayServer) -> list[str]:
    accepted: list[str] = []
    for handler in getattr(relay, "_direct_packet_handlers", ()):
        for schema in getattr(handler, "control_payload_schemas", ()):
            normalized = str(schema or "").strip()
            if normalized and normalized not in accepted:
                accepted.append(normalized)
    return accepted


def build_http_server(
    config: RelayServerConfig,
    *,
    session_store: InMemorySessionStore | None = None,
    packet_store: InMemoryAcceptedPacketStore | None = None,
    pc_control_runtime: PcControlRuntime | None = None,
    runner_config: AppConfig | None = None,
    file_upload_limit_bytes: int | None = None,
) -> ThreadingHTTPServer:
    store = session_store or InMemorySessionStore()
    packets = packet_store or InMemoryAcceptedPacketStore()
    effective_runner_config = runner_config or load_config()
    file_store = FileSurfaceStore(
        config.state_dir,
        upload_limit_bytes=file_upload_limit_bytes if file_upload_limit_bytes is not None else 32 * 1024 * 1024,
    )

    class RelayRequestHandler(BaseHTTPRequestHandler):
        def _discard_request_body(self) -> None:
            try:
                raw_length = str(self.headers.get("Content-Length", "") or "").strip()
                if not raw_length:
                    return
                remaining = int(raw_length)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 64 * 1024))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except Exception:
                return

        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_bytes(self, status_code: int, payload: bytes, *, content_type: str, etag: str | None = None) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "private, max-age=3600")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(payload)

        def _write_upload_error(self, error: FileSurfaceUploadError) -> None:
            self._write_json(error.status_code, error.to_response_payload())

        def _require_transport_token(self) -> bool:
            provided_token = _extract_bearer_token(self.headers)
            if validate_transport_token(provided_token, config.transport_token):
                return True
            self._write_upload_error(
                FileSurfaceUploadError(
                    status_code=401,
                    error_code="unauthorized",
                    error_message="transport token mismatch",
                    retryable=False,
                )
            )
            return False

        def _require_android_app_token(self) -> bool:
            expected_token = str(config.android_app_token or "").strip()
            if not expected_token:
                self._write_json(
                    503,
                    {
                        "status": "error",
                        "error_code": "android_app_auth_unavailable",
                        "error_message": "android app auth is not configured for this relay",
                        "retryable": True,
                    },
                )
                return False
            provided_token = _extract_bearer_token(self.headers)
            if validate_bearer_token(provided_token, expected_token):
                return True
            self._write_json(
                401,
                {
                    "status": "error",
                    "error_code": "unauthorized",
                    "error_message": "android app token mismatch",
                    "retryable": False,
                },
            )
            return False

        def _normalized_path(self) -> str:
            return urlparse(self.path).path

        def do_GET(self) -> None:  # noqa: N802
            path = self._normalized_path()
            if path in {"/healthz", "/readyz"}:
                host, port = self.server.server_address[:2]
                self._write_json(
                    200,
                    build_health_payload(
                        config,
                        store,
                        packet_store=packets,
                        pc_control_runtime=pc_control_runtime,
                        runner_config=effective_runner_config,
                        listening_host=str(host),
                        listening_port=int(port),
                    ),
                )
                return
            if path == ANDROID_ENVIRONMENT_INVENTORY_PATH:
                if not self._require_android_app_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                include_offline = str((query.get("include_offline") or ["true"])[0]).strip().lower() != "false"
                include_missing_workspaces = (
                    str((query.get("include_missing_workspaces") or ["true"])[0]).strip().lower() != "false"
                )
                pc_ids = [item.strip() for item in query.get("pc_id", []) if str(item).strip()]
                workspace_ids = [item.strip() for item in query.get("workspace_id", []) if str(item).strip()]
                self._write_json(
                    200,
                    build_android_environment_inventory_snapshot(
                        pc_control_runtime=pc_control_runtime,
                        include_offline=include_offline,
                        include_missing_workspaces=include_missing_workspaces,
                        pc_ids=pc_ids,
                        workspace_ids=workspace_ids,
                    ),
                )
                return
            if path == ANDROID_SESSIONS_PATH:
                if not self._require_android_app_token():
                    return
                task_root = str(config.task_root or "").strip()
                if not task_root:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "task_root_unavailable",
                            "error_message": "relay task_root is not configured for android session reads",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                include_ended = str((query.get("include_ended") or ["false"])[0]).strip().lower() == "true"
                pc_ids = [item.strip() for item in query.get("pc_id", []) if str(item).strip()]
                workspace_ids = [item.strip() for item in query.get("workspace_id", []) if str(item).strip()]
                session_ids = [item.strip() for item in query.get("session_id", []) if str(item).strip()]
                thread_ids = [item.strip() for item in query.get("thread_id", []) if str(item).strip()]
                self._write_json(
                    200,
                    build_android_sessions_snapshot(
                        task_root=task_root,
                        pc_control_runtime=pc_control_runtime,
                        include_ended=include_ended,
                        pc_ids=pc_ids,
                        workspace_ids=workspace_ids,
                        session_ids=session_ids,
                        thread_ids=thread_ids,
                    ),
                )
                return
            if path == ANDROID_SESSION_SNAPSHOT_PATH:
                if not self._require_android_app_token():
                    return
                task_root = str(config.task_root or "").strip()
                if not task_root:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "task_root_unavailable",
                            "error_message": "relay task_root is not configured for android session reads",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                try:
                    response_payload = build_android_session_snapshot(
                        query=query,
                        task_root=task_root,
                        pc_control_runtime=pc_control_runtime,
                    )
                except AndroidSessionSnapshotFacadeError as exc:
                    self._write_json(exc.status_code, exc.to_response_payload())
                    return
                self._write_json(200, response_payload)
                return
            if path == _PC_CONTROL_OPERATOR_NODES_PATH:
                if not self._require_transport_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                pc_id = str((query.get("pc_id") or [""])[0]).strip()
                self._write_json(
                    200,
                    {
                        "status": "ok",
                        "pc_id": pc_id or None,
                        "node": (pc_control_runtime.get_node(pc_id=pc_id) if pc_id else None),
                        "nodes": ([] if pc_id else pc_control_runtime.list_nodes()),
                    },
                )
                return
            if path == _PC_CONTROL_OPERATOR_WORKSPACES_PATH:
                if not self._require_transport_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                pc_id = str((query.get("pc_id") or [""])[0]).strip() or None
                self._write_json(
                    200,
                    {
                        "status": "ok",
                        "pc_id": pc_id,
                        "workspaces": pc_control_runtime.list_workspaces(pc_id=pc_id),
                    },
                )
                return
            if path == _PC_CONTROL_OPERATOR_COMMANDS_PATH:
                if not self._require_transport_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                pc_id = str((query.get("pc_id") or [""])[0]).strip() or None
                command_id = str((query.get("command_id") or [""])[0]).strip() or None
                if command_id and not pc_id:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "missing_pc_id",
                            "error_message": "pc_id is required when command_id is provided",
                            "retryable": False,
                        },
                    )
                    return
                self._write_json(
                    200,
                    {
                        "status": "ok",
                        "pc_id": pc_id,
                        "command_id": command_id,
                        "command": (
                            pc_control_runtime.get_command(pc_id=pc_id, command_id=command_id)
                            if pc_id is not None and command_id is not None
                            else None
                        ),
                        "commands": (
                            []
                            if command_id is not None
                            else pc_control_runtime.list_commands(pc_id=pc_id)
                        ),
                    },
                )
                return
            if path == _PC_CONTROL_OPERATOR_LEASE_PATH:
                if not self._require_transport_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                mailbox_key = str((query.get("mailbox_key") or [""])[0]).strip()
                if not mailbox_key:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "missing_mailbox_key",
                            "error_message": "mailbox_key is required",
                            "retryable": False,
                        },
                    )
                    return
                self._write_json(
                    200,
                    {
                        "status": "ok",
                        "mailbox_key": mailbox_key,
                        "lease": pc_control_runtime.get_mailbox_lease(mailbox_key=mailbox_key),
                        "lease_events": pc_control_runtime.list_mailbox_lease_events(mailbox_key=mailbox_key, limit=20),
                    },
                )
                return
            if path == _PC_CONTROL_OPERATOR_INGRESS_PATH:
                if not self._require_transport_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                mailbox_key = str((query.get("mailbox_key") or [""])[0]).strip() or None
                message_id = str((query.get("message_id") or [""])[0]).strip() or None
                ingress_id = str((query.get("ingress_id") or [""])[0]).strip() or None
                uid_text = str((query.get("uid") or [""])[0]).strip()
                uid = int(uid_text) if uid_text.isdigit() else None
                uid_validity_text = str((query.get("uid_validity") or [""])[0]).strip()
                uid_validity = int(uid_validity_text) if uid_validity_text.isdigit() else None
                folder = str((query.get("folder") or ["INBOX"])[0]).strip() or "INBOX"
                record = pc_control_runtime.find_ingress(
                    ingress_id=ingress_id,
                    mailbox_key=mailbox_key,
                    message_id=message_id,
                    uid=uid,
                    folder=folder,
                    uid_validity=uid_validity,
                )
                self._write_json(
                    200,
                    {
                        "status": "ok",
                        "ingress": record,
                    },
                )
                return
            if path == _PC_CONTROL_OPERATOR_OUTCOME_PATH:
                if not self._require_transport_token():
                    return
                if pc_control_runtime is None:
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                query = parse_qs(urlparse(self.path).query)
                thread_id = str((query.get("thread_id") or [""])[0]).strip()
                if not thread_id:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "missing_thread_id",
                            "error_message": "thread_id is required",
                            "retryable": False,
                        },
                    )
                    return
                self._write_json(
                    200,
                    {
                        "status": "ok",
                        "thread_id": thread_id,
                        "terminal_outcome": pc_control_runtime.find_terminal_outcome(thread_id=thread_id),
                    },
                )
                return
            if path.startswith("/v1/files/"):
                if not self._require_transport_token():
                    return
                remainder = path[len("/v1/files/") :]
                if remainder.endswith("/content"):
                    file_id = remainder[: -len("/content")].strip("/")
                    record = file_store.get_content(file_id)
                    if record is None:
                        self._write_json(
                            404,
                            {
                                "status": "error",
                                "error_code": "not_found",
                                "error_message": f"file_id not found: {file_id}",
                                "retryable": False,
                            },
                        )
                        return
                    metadata, content = record
                    artifact = metadata.get("artifact") or {}
                    self._write_bytes(
                        200,
                        content,
                        content_type=str(artifact.get("mime_type") or "application/octet-stream"),
                        etag=str(artifact.get("sha256") or "").strip() or None,
                    )
                    return
                file_id = remainder.strip("/")
                metadata = file_store.get_metadata(file_id)
                if metadata is None:
                    self._write_json(
                        404,
                        {
                            "status": "error",
                            "error_code": "not_found",
                            "error_message": f"file_id not found: {file_id}",
                            "retryable": False,
                        },
                    )
                    return
                self._write_json(200, metadata)
                return
            self._write_json(
                404,
                {
                    "status": "not_found",
                    "path": self.path,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            path = self._normalized_path()
            if path == ANDROID_CREATE_SESSION_PATH:
                if not self._require_android_app_token():
                    self._discard_request_body()
                    return
                if pc_control_runtime is None:
                    self._discard_request_body()
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                try:
                    raw_length = str(self.headers.get("Content-Length", "") or "").strip()
                    content_length = int(raw_length)
                    if content_length < 0:
                        raise ValueError("Content-Length must be non-negative")
                    body = self.rfile.read(content_length)
                    payload = json.loads(body.decode("utf-8"))
                    response_payload = submit_android_create_session_command(
                        payload,
                        pc_control_runtime=pc_control_runtime,
                    )
                except json.JSONDecodeError as exc:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "invalid_json",
                            "error_message": f"request body is not valid JSON: {exc}",
                            "retryable": False,
                        },
                    )
                    return
                except AndroidCreateSessionRequestError as exc:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "invalid_payload",
                            "error_message": str(exc),
                            "retryable": False,
                        },
                    )
                    return
                except AndroidCreateSessionContractError as exc:
                    self._write_json(
                        502,
                        {
                            "status": "error",
                            "error_code": "android_create_session_contract_violation",
                            "error_message": str(exc),
                            "retryable": True,
                        },
                    )
                    return
                except AndroidCreateSessionSubmitTimeout as exc:
                    self._write_json(
                        504,
                        {
                            "status": "error",
                            "error_code": "submit_ack_timeout",
                            "error_message": str(exc),
                            "retryable": True,
                            "command_id": exc.command_id,
                        },
                    )
                    return
                self._write_json(200, response_payload)
                return
            if path == _PC_CONTROL_OPERATOR_DISPATCH_PATH:
                if not self._require_transport_token():
                    self._discard_request_body()
                    return
                if pc_control_runtime is None:
                    self._discard_request_body()
                    self._write_json(
                        503,
                        {
                            "status": "error",
                            "error_code": "pc_control_unavailable",
                            "error_message": "pc_control_runtime is not configured",
                            "retryable": True,
                        },
                    )
                    return
                try:
                    raw_length = str(self.headers.get("Content-Length", "") or "").strip()
                    content_length = int(raw_length)
                    if content_length < 0:
                        raise ValueError("Content-Length must be non-negative")
                    body = self.rfile.read(content_length)
                    payload = json.loads(body.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    dispatch_payload, response_command = _build_operator_pc_dispatch(
                        payload,
                        pc_control_runtime=pc_control_runtime,
                    )
                    record = pc_control_runtime.enqueue_command(parse_pc_control_server_message(dispatch_payload))
                except json.JSONDecodeError as exc:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "invalid_json",
                            "error_message": f"request body is not valid JSON: {exc}",
                            "retryable": False,
                        },
                    )
                    return
                except (ValueError, PcControlProtocolError) as exc:
                    self._write_json(
                        400,
                        {
                            "status": "error",
                            "error_code": "invalid_payload",
                            "error_message": str(exc),
                            "retryable": False,
                        },
                    )
                    return
                except PcCommandDispatchValidationError as exc:
                    self._write_json(
                        409,
                        {
                            "status": "error",
                            "error_code": exc.code,
                            "error_message": exc.message,
                            "retryable": False,
                        },
                    )
                    return
                self._write_json(
                    200,
                    {
                        "status": "accepted",
                        "command": response_command,
                        "record": {
                            "pc_id": record.pc_id,
                            "workspace_id": record.workspace_id,
                            "command_id": record.command_id,
                            "command_type": record.command_type,
                            "status": record.status,
                            "trace_id": record.trace_id,
                            "dispatch_message_id": record.dispatch_message_id,
                            "created_at": record.created_at,
                        },
                    },
                )
                return
            if path != "/v1/files":
                self._write_json(
                    404,
                    {
                        "status": "not_found",
                        "path": self.path,
                    },
                )
                return
            if not self._require_transport_token():
                self._discard_request_body()
                return
            try:
                raw_length = str(self.headers.get("Content-Length", "") or "").strip()
                content_length = int(raw_length)
                if content_length < 0:
                    raise ValueError("Content-Length must be non-negative")
                body = self.rfile.read(content_length)
                form_fields = _parse_multipart_form_data(self.headers, body)
                metadata_part = form_fields.get("metadata")
                file_part = form_fields.get("file")
                if metadata_part is None or file_part is None:
                    raise FileSurfaceUploadError(
                        status_code=400,
                        error_code="invalid_metadata",
                        error_message="multipart/form-data must contain metadata and file parts",
                        retryable=False,
                    )
                metadata = json.loads(bytes(metadata_part["payload"]).decode("utf-8"))
                file_bytes = bytes(file_part["payload"])
                descriptor = file_store.store_upload(metadata, file_bytes)
            except FileSurfaceUploadError as exc:
                self._write_upload_error(exc)
                return
            except json.JSONDecodeError as exc:
                self._write_upload_error(
                    FileSurfaceUploadError(
                        status_code=400,
                        error_code="invalid_metadata",
                        error_message=f"metadata is not valid JSON: {exc}",
                        retryable=False,
                    )
                )
                return
            except ValueError as exc:
                self._write_upload_error(
                    FileSurfaceUploadError(
                        status_code=400,
                        error_code="invalid_metadata",
                        error_message=str(exc),
                        retryable=False,
                    )
                )
                return
            except Exception as exc:
                self._write_upload_error(
                    FileSurfaceUploadError(
                        status_code=500,
                        error_code="store_write_failed",
                        error_message=str(exc),
                        retryable=True,
                    )
                )
                return
            self._write_json(200, descriptor)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            LOGGER.debug("relay_server http: " + format, *args)

    return ThreadingHTTPServer((config.host, config.port), RelayRequestHandler)


def _extract_bearer_token(request_headers: Any) -> str:
    raw_auth = ""
    if hasattr(request_headers, "get"):
        raw_auth = str(request_headers.get("Authorization", "") or "")
    if not raw_auth.lower().startswith("bearer "):
        return ""
    return raw_auth[7:].strip()


def _require_text_field(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text_field(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string when provided")
    return value.strip()


def _mapping_field(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = payload.get(field_name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _build_operator_pc_dispatch(
    payload: dict[str, Any],
    *,
    pc_control_runtime: PcControlRuntime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pc_id = _require_text_field(payload, "pc_id")
    workspace_id = _require_text_field(payload, "workspace_id")
    command_type = _require_text_field(payload, "command_type")
    session_id = _optional_text_field(payload, "session_id")
    execution_policy = _mapping_field(payload, "execution_policy")
    command_payload = _mapping_field(payload, "payload")

    node = pc_control_runtime.node_store.get_node(pc_id)
    if node is None or node.status != "online":
        raise PcCommandDispatchValidationError("pc_not_online", f"pc_id is not online: {pc_id}")

    command_id = _optional_text_field(payload, "command_id") or f"cmd:{pc_id}:{_timestamp()}"
    trace_id = _optional_text_field(payload, "trace_id") or f"trace:pc-control:operator-dispatch:{pc_id}:{command_id}"
    sent_at = _optional_text_field(payload, "sent_at") or _timestamp()
    message_id = _optional_text_field(payload, "message_id") or f"operator-dispatch:{pc_id}:{command_id}"
    dispatch = build_command_dispatch(
        message_id=message_id,
        trace_id=trace_id,
        pc_id=pc_id,
        connection_epoch=node.current_connection_epoch,
        sent_at=sent_at,
        command_id=command_id,
        command_type=command_type,
        workspace_id=workspace_id,
        session_id=session_id,
        execution_policy=execution_policy,
        command_payload=command_payload,
    )
    return dispatch, {
        "pc_id": pc_id,
        "workspace_id": workspace_id,
        "command_id": command_id,
        "command_type": command_type,
        "session_id": session_id,
        "trace_id": trace_id,
        "message_id": message_id,
        "connection_epoch": node.current_connection_epoch,
        "sent_at": sent_at,
    }


def _build_ssl_context(config: RelayServerConfig) -> ssl.SSLContext | None:
    if not config.tls_certfile or not config.tls_keyfile:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.tls_certfile, keyfile=config.tls_keyfile)
    return context


def _json_response(status: HTTPStatus, payload: dict[str, Any]) -> tuple[HTTPStatus, list[tuple[str, str]], bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [*_HTTP_RESPONSE_HEADERS, ("Content-Length", str(len(body)))]
    return status, headers, body


def _render_http_response(
    status: HTTPStatus | int,
    headers: list[tuple[str, str]],
    body: bytes,
) -> bytes:
    normalized_status = HTTPStatus(int(status))
    lines = [f"HTTP/1.1 {normalized_status.value} {normalized_status.phrase}\r\n"]
    seen_connection = False
    for name, value in headers:
        if name.lower() == "connection":
            seen_connection = True
        lines.append(f"{name}: {value}\r\n")
    if not seen_connection:
        lines.append("Connection: close\r\n")
    lines.append("\r\n")
    return "".join(lines).encode("iso-8859-1") + body


class _UnifiedRelayServer:
    def __init__(
        self,
        public_server: asyncio.AbstractServer,
        websocket_server,
        http_server: ThreadingHTTPServer,
        http_thread: threading.Thread,
    ) -> None:
        self._public_server = public_server
        self._websocket_server = websocket_server
        self._http_server = http_server
        self._http_thread = http_thread
        self._closed = False

    @property
    def sockets(self):
        return self._public_server.sockets

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._public_server.close()
        self._websocket_server.close()
        self._http_server.shutdown()
        self._http_server.server_close()

    async def wait_closed(self) -> None:
        await self._public_server.wait_closed()
        await self._websocket_server.wait_closed()
        await asyncio.to_thread(self._http_thread.join, 5)


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[bytes, str, str, dict[str, str]] | None:
    try:
        first_byte = await reader.read(1)
        if not first_byte:
            return None
        if not (65 <= first_byte[0] <= 90):
            raise ValueError("invalid HTTP preface")
        header_bytes = first_byte + await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        return None
    except asyncio.LimitOverrunError as exc:
        raise ValueError(f"HTTP header exceeded limit: {exc}") from exc
    if len(header_bytes) > _HTTP_HEADER_LIMIT_BYTES:
        raise ValueError("HTTP header exceeded limit")

    header_text = header_bytes.decode("iso-8859-1")
    header_lines = header_text.split("\r\n")
    request_line = header_lines[0].strip()
    if not request_line:
        return None
    try:
        method, path, _http_version = request_line.split(" ", 2)
    except ValueError as exc:
        raise ValueError("invalid HTTP request line") from exc

    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ValueError("invalid HTTP header line")
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if transfer_encoding and transfer_encoding != "identity":
        raise ValueError("transfer-encoding is not supported")

    content_length = 0
    if headers.get("content-length"):
        try:
            content_length = int(headers["content-length"])
        except ValueError as exc:
            raise ValueError("invalid Content-Length header") from exc
        if content_length < 0:
            raise ValueError("invalid Content-Length header")

    body = await reader.readexactly(content_length) if content_length else b""
    return header_bytes + body, method.upper(), path, headers


async def _pipe_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        raise
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
                await writer.drain()
        except Exception:
            pass


async def _proxy_raw_http_request(
    request_bytes: bytes,
    writer: asyncio.StreamWriter,
    *,
    target_host: str,
    target_port: int,
) -> None:
    target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    try:
        target_writer.write(request_bytes)
        await target_writer.drain()
        response_head = await target_reader.readuntil(b"\r\n\r\n")
        writer.write(response_head)
        await writer.drain()

        header_text = response_head.decode("iso-8859-1")
        response_headers: dict[str, str] = {}
        for line in header_text.split("\r\n")[1:]:
            if not line:
                continue
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            response_headers[name.strip().lower()] = value.strip()

        if response_headers.get("transfer-encoding", "").lower() == "chunked":
            while True:
                chunk_header = await target_reader.readuntil(b"\r\n")
                writer.write(chunk_header)
                await writer.drain()
                chunk_size_text = chunk_header.decode("ascii").split(";", 1)[0].strip()
                chunk_size = int(chunk_size_text, 16)
                if chunk_size == 0:
                    trailer = await target_reader.readuntil(b"\r\n")
                    writer.write(trailer)
                    await writer.drain()
                    break
                chunk_payload = await target_reader.readexactly(chunk_size + 2)
                writer.write(chunk_payload)
                await writer.drain()
            return

        content_length = response_headers.get("content-length")
        if content_length:
            remaining = int(content_length)
            while remaining > 0:
                chunk = await target_reader.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                writer.write(chunk)
                await writer.drain()
            return

        while True:
            chunk = await target_reader.read(64 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        target_writer.close()
        await target_writer.wait_closed()


async def _proxy_websocket_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    request_bytes: bytes,
    *,
    target_host: str,
    target_port: int,
) -> None:
    target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    try:
        target_writer.write(request_bytes)
        await target_writer.drain()

        upstream_task = asyncio.create_task(_pipe_stream(client_reader, target_writer))
        downstream_task = asyncio.create_task(_pipe_stream(target_reader, client_writer))
        done, pending = await asyncio.wait(
            {upstream_task, downstream_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, return_exceptions=True)
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        target_writer.close()
        await target_writer.wait_closed()


async def _handle_unified_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    config: RelayServerConfig,
    session_store,
    packet_store,
    pc_control_runtime: PcControlRuntime,
    listen_ref: dict[str, Any],
    websocket_target: tuple[str, int],
    http_target: tuple[str, int],
) -> None:
    try:
        request = await _read_http_request(reader)
        if request is None:
            return
        request_bytes, _method, raw_path, headers = request
        normalized_path = urlparse(raw_path).path
        is_websocket_upgrade = (
            normalized_path in {_RELAY_WEBSOCKET_PATH, _CONTROL_WEBSOCKET_PATH, _PC_CONTROL_WEBSOCKET_PATH}
            and headers.get("upgrade", "").lower() == "websocket"
            and "upgrade" in headers.get("connection", "").lower()
        )
        if normalized_path in {"/healthz", "/readyz"} and not is_websocket_upgrade:
            response = _json_response(
                HTTPStatus.OK,
                build_health_payload(
                    config,
                    session_store,
                    packet_store=packet_store,
                    pc_control_runtime=pc_control_runtime,
                    runner_config=load_config(),
                    listening_host=str(listen_ref.get("host") or config.host),
                    listening_port=int(listen_ref.get("port") or config.port),
                ),
            )
            writer.write(_render_http_response(*response))
            await writer.drain()
            return
        if is_websocket_upgrade:
            await _proxy_websocket_connection(
                reader,
                writer,
                request_bytes,
                target_host=websocket_target[0],
                target_port=websocket_target[1],
            )
            return
        await _proxy_raw_http_request(
            request_bytes,
            writer,
            target_host=http_target[0],
            target_port=http_target[1],
        )
    except ValueError as exc:
        response = _json_response(
            HTTPStatus.BAD_REQUEST,
            {
                "status": "error",
                "error_code": "bad_request",
                "error_message": str(exc),
                "retryable": False,
            },
        )
        writer.write(_render_http_response(*response))
        await writer.drain()
    except Exception:
        LOGGER.exception("unified relay listener failed")
        response = _json_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {
                "status": "error",
                "error_code": "server_error",
                "error_message": "internal server error",
                "retryable": True,
            },
        )
        writer.write(_render_http_response(*response))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _parse_multipart_form_data(request_headers: Any, body: bytes) -> dict[str, dict[str, Any]]:
    content_type = str(getattr(request_headers, "get", lambda *_: "")("Content-Type", "") or "")
    if "multipart/form-data" not in content_type:
        raise FileSurfaceUploadError(
            status_code=400,
            error_code="invalid_metadata",
            error_message="Content-Type must be multipart/form-data",
            retryable=False,
        )
    envelope = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    if not message.is_multipart():
        raise FileSurfaceUploadError(
            status_code=400,
            error_code="invalid_metadata",
            error_message="multipart/form-data body could not be parsed",
            retryable=False,
        )
    fields: dict[str, dict[str, Any]] = {}
    for part in message.iter_parts():
        field_name = str(part.get_param("name", header="content-disposition") or "").strip()
        if not field_name:
            continue
        fields[field_name] = {
            "filename": part.get_filename(),
            "content_type": part.get_content_type(),
            "payload": part.get_payload(decode=True) or b"",
        }
    return fields


async def _process_request(path: str, _request_headers: Any, *, config: RelayServerConfig, session_store, packet_store, pc_control_runtime):
    if path not in {"/healthz", "/readyz"}:
        return None
    return _json_response(
        HTTPStatus.OK,
        build_health_payload(
            config,
            session_store,
            packet_store=packet_store,
            pc_control_runtime=pc_control_runtime,
            runner_config=load_config(),
        ),
    )


async def _websocket_handler(websocket, path: str, *, relay: LoopbackRelayServer, pc_control_runtime: PcControlRuntime) -> None:
    normalized_path = str(path or "").strip() or "/"
    if normalized_path == _RELAY_WEBSOCKET_PATH:
        await _relay_websocket_handler(websocket, relay=relay)
        return
    if normalized_path == _CONTROL_WEBSOCKET_PATH:
        await _control_websocket_handler(websocket, relay=relay)
        return
    if normalized_path == _PC_CONTROL_WEBSOCKET_PATH:
        await _pc_control_websocket_handler(websocket, pc_control_runtime=pc_control_runtime)
        return
    await websocket.close(code=1008, reason="unsupported_path")


async def _relay_websocket_handler(websocket, *, relay: LoopbackRelayServer) -> None:
    provided_token = _extract_bearer_token(getattr(websocket, "request_headers", {}))
    connection_id: str | None = None
    send_lock = asyncio.Lock()
    subscription_push_task: asyncio.Task | None = None

    async def _send_payload(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    try:
        async for raw_message in websocket:
            now = _timestamp()
            try:
                payload = json.loads(raw_message)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
            except Exception as exc:
                responses = [
                    {
                        "message_type": "error",
                        "code": "invalid_json",
                        "message": str(exc),
                        "sent_at": now,
                    }
                ]
            else:
                responses = relay.handle_client_message_batch(
                    payload,
                    provided_token=provided_token if connection_id is None else None,
                    connection_id=connection_id,
                )
            if not responses:
                responses = [
                    {
                        "message_type": "error",
                        "code": "server_error",
                        "message": "relay produced no response",
                        "sent_at": now,
                    }
                ]
            first_response = responses[0]
            for response in responses:
                if response.get("message_type") == "hello_ack":
                    connection_id = str(response.get("connection_id") or "").strip() or None
                    if connection_id and subscription_push_task is None:
                        subscription_push_task = asyncio.create_task(
                            _subscription_push_loop(
                                websocket,
                                relay=relay,
                                connection_id=connection_id,
                                send_payload=_send_payload,
                            )
                        )
                await _send_payload(response)
            if first_response.get("message_type") == "error" and connection_id is None:
                await websocket.close(code=1008, reason=str(first_response.get("code") or "error"))
                return
    finally:
        if subscription_push_task is not None:
            subscription_push_task.cancel()
            try:
                await subscription_push_task
            except asyncio.CancelledError:
                pass
        if connection_id:
            relay.clear_subscription_runtime_state(connection_id)
            relay.session_store.close_session(connection_id, closed_at=_timestamp())


def _handle_control_message_batch(
    payload: dict[str, Any],
    *,
    relay: LoopbackRelayServer,
    provided_token: str | None,
    connection_id: str | None,
    now: str,
) -> list[dict[str, Any]]:
    try:
        message = parse_control_client_message(payload)
    except Exception as exc:
        return [
            {
                "message_type": "error",
                "code": "invalid_payload",
                "message": str(exc),
                "sent_at": now,
            }
        ]

    if isinstance(message, ControlHelloMessage):
        relay_responses = relay.handle_client_message_batch(
            {
                "message_type": "hello",
                "client_id": message.client_id,
                "client_version": message.client_version,
                "transport_token_id": message.transport_token_id,
                "sent_at": message.sent_at,
            },
            provided_token=provided_token,
            connection_id=connection_id,
        )
        translated: list[dict[str, Any]] = []
        accepted_payload_schemas = negotiate_control_payload_schemas(
            message.supported_payload_schemas,
            supported_payload_schemas=_resolve_control_payload_schemas(relay),
        )
        for response in relay_responses:
            if response.get("message_type") == "hello_ack":
                translated.append(
                    build_control_hello_ack(
                        connection_id=str(response.get("connection_id") or ""),
                        server_time=str(response.get("server_time") or ""),
                        heartbeat_seconds=int(response.get("heartbeat_seconds") or 0),
                        transport_token_id=token_fingerprint(relay._config.transport_token),
                        accepted_payload_schemas=accepted_payload_schemas,
                    )
                )
            else:
                translated.append(dict(response))
        return translated

    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return [
            {
                "message_type": "error",
                "code": "missing_connection",
                "message": "connection_id is required before command or ping",
                "sent_at": now,
            }
        ]

    if isinstance(message, ControlPingMessage):
        relay.session_store.touch_session(normalized_connection_id, last_seen_at=message.sent_at)
        return [build_control_pong(sent_at=now)]

    if not isinstance(message, ControlCommandMessage):
        return [
            {
                "message_type": "error",
                "code": "unsupported_message_type",
                "message": "unsupported control client message",
                "sent_at": now,
            }
        ]

    try:
        relay_packet = build_relay_packet_from_control_command(message)
    except ControlBridgeError as exc:
        return [
            {
                "message_type": "error",
                "code": exc.code,
                "message": exc.message,
                "sent_at": now,
            }
        ]

    relay_responses = relay.handle_client_message_batch(
        relay_packet,
        connection_id=normalized_connection_id,
    )
    translated_responses: list[dict[str, Any]] = []
    for response in relay_responses:
        try:
            translated_responses.append(
                translate_relay_response_to_control(
                    dict(response),
                    message=message,
                )
            )
        except ControlBridgeError as exc:
            return [
                {
                    "message_type": "error",
                    "code": exc.code,
                    "message": exc.message,
                    "sent_at": now,
                }
            ]
    return translated_responses


async def _control_websocket_handler(websocket, *, relay: LoopbackRelayServer) -> None:
    provided_token = _extract_bearer_token(getattr(websocket, "request_headers", {}))
    connection_id: str | None = None
    send_lock = asyncio.Lock()

    async def _send_payload(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    try:
        async for raw_message in websocket:
            now = _timestamp()
            try:
                payload = json.loads(raw_message)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
            except Exception as exc:
                responses = [
                    {
                        "message_type": "error",
                        "code": "invalid_json",
                        "message": str(exc),
                        "sent_at": now,
                    }
                ]
            else:
                responses = _handle_control_message_batch(
                    payload,
                    relay=relay,
                    provided_token=provided_token if connection_id is None else None,
                    connection_id=connection_id,
                    now=now,
                )
            if not responses:
                responses = [
                    {
                        "message_type": "error",
                        "code": "server_error",
                        "message": "control plane produced no response",
                        "sent_at": now,
                    }
                ]
            first_response = responses[0]
            for response in responses:
                if response.get("message_type") == "hello_ack":
                    connection_id = str(response.get("connection_id") or "").strip() or None
                await _send_payload(response)
            if first_response.get("message_type") == "error" and connection_id is None:
                await websocket.close(code=1008, reason=str(first_response.get("code") or "error"))
                return
    finally:
        if connection_id:
            relay.clear_subscription_runtime_state(connection_id)
            relay.session_store.close_session(connection_id, closed_at=_timestamp())


async def _pc_control_websocket_handler(websocket, *, pc_control_runtime: PcControlRuntime) -> None:
    provided_token = _extract_bearer_token(getattr(websocket, "request_headers", {}))
    server_connection_id: str | None = None
    pc_id: str | None = None
    connection_epoch: int | None = None
    send_lock = asyncio.Lock()

    async def _send_payload(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    def _protocol_error(*, code: str, message: str, trace_id: str, payload_pc_id: str | None = None, payload_epoch: int = 0) -> dict[str, Any]:
        return build_pc_error(
            message_id=f"pc-control-error:{_timestamp()}",
            trace_id=trace_id,
            pc_id=payload_pc_id,
            connection_epoch=payload_epoch,
            sent_at=_timestamp(),
            code=code,
            message=message,
        )

    try:
        async for raw_message in websocket:
            allow_pending_dispatches = False
            allow_output_resume_requests = False
            try:
                payload = json.loads(raw_message)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
            except Exception as exc:
                responses = [
                    _protocol_error(
                        code="invalid_json",
                        message=str(exc),
                        trace_id="trace:pc-control:invalid-json",
                        payload_pc_id=pc_id,
                        payload_epoch=connection_epoch or 0,
                    )
                ]
            else:
                try:
                    message = parse_pc_control_client_message(payload)
                except Exception as exc:
                    responses = [
                        _protocol_error(
                            code="invalid_payload",
                            message=str(exc),
                            trace_id=str(payload.get("trace_id") or "trace:pc-control:invalid-payload"),
                            payload_pc_id=str(payload.get("pc_id") or "").strip() or pc_id,
                            payload_epoch=int(payload.get("connection_epoch") or 0) if isinstance(payload.get("connection_epoch"), int) else (connection_epoch or 0),
                        )
                    ]
                else:
                    if isinstance(message, PcHelloMessage):
                        response, next_connection_id, next_epoch = pc_control_runtime.handle_hello(
                            message,
                            provided_token=provided_token,
                        )
                        responses = [response]
                        if response.get("type") == "hello_ack":
                            server_connection_id = next_connection_id
                            pc_id = message.pc_id
                            connection_epoch = next_epoch
                            allow_pending_dispatches = True
                            allow_output_resume_requests = True
                    else:
                        if server_connection_id is None or pc_id is None or connection_epoch is None:
                            responses = [
                                _protocol_error(
                                    code="missing_connection",
                                    message="pc_hello is required before other pc-control client messages",
                                    trace_id=message.trace_id,
                                    payload_pc_id=message.pc_id,
                                    payload_epoch=message.connection_epoch,
                                )
                            ]
                        elif message.pc_id != pc_id:
                            responses = [
                                _protocol_error(
                                    code="pc_id_mismatch",
                                    message="message pc_id does not match the current connection",
                                    trace_id=message.trace_id,
                                    payload_pc_id=message.pc_id,
                                    payload_epoch=message.connection_epoch,
                                )
                            ]
                        elif isinstance(message, PcHeartbeatMessage):
                            response = pc_control_runtime.handle_heartbeat(message, connection_id=server_connection_id)
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcWorkspaceSnapshotMessage):
                            response = pc_control_runtime.handle_workspace_snapshot(message, connection_id=server_connection_id)
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcCommandAckMessage):
                            response = pc_control_runtime.handle_command_ack(message, connection_id=server_connection_id)
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcCommandEventMessage):
                            response = pc_control_runtime.handle_event(message, connection_id=server_connection_id)
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcCommandResultMessage):
                            response = pc_control_runtime.handle_result(message, connection_id=server_connection_id)
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcOutputChunkMessage):
                            response = pc_control_runtime.handle_output_chunk(message, connection_id=server_connection_id)
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcArtifactManifestMessage):
                            response = pc_control_runtime.handle_artifact_manifest(
                                message,
                                connection_id=server_connection_id,
                            )
                            responses = [] if response is None else [response]
                            allow_pending_dispatches = response is None
                        elif isinstance(message, PcMailboxLeaseMessage):
                            responses = [pc_control_runtime.handle_mailbox_lease(message, connection_id=server_connection_id)]
                            allow_pending_dispatches = False
                        elif isinstance(message, PcIngressCandidateMessage):
                            responses = [pc_control_runtime.handle_ingress_candidate(message, connection_id=server_connection_id)]
                            allow_pending_dispatches = False
                        elif isinstance(message, PcThreadBindingMessage):
                            responses = [pc_control_runtime.handle_thread_binding(message, connection_id=server_connection_id)]
                            allow_pending_dispatches = False
                        elif isinstance(message, PcTerminalOutcomeMessage):
                            responses = [pc_control_runtime.handle_terminal_outcome(message, connection_id=server_connection_id)]
                            allow_pending_dispatches = False
                        else:
                            responses = [
                                _protocol_error(
                                    code="unsupported_message_type",
                                    message="unsupported pc-control client message",
                                    trace_id=message.trace_id,
                                    payload_pc_id=message.pc_id,
                                    payload_epoch=message.connection_epoch,
                                )
                            ]

            if allow_pending_dispatches and server_connection_id is not None and pc_id is not None and connection_epoch is not None:
                try:
                    responses.extend(
                        pc_control_runtime.collect_pending_dispatches(
                            pc_id=pc_id,
                            connection_id=server_connection_id,
                            connection_epoch=connection_epoch,
                        )
                    )
                except PcCommandDispatchValidationError as exc:
                    responses = [
                        _protocol_error(
                            code=exc.code,
                            message=exc.message,
                            trace_id=f"trace:pc-control:dispatch:{pc_id}",
                            payload_pc_id=pc_id,
                            payload_epoch=connection_epoch,
                        )
                    ]

            if allow_output_resume_requests and server_connection_id is not None and pc_id is not None and connection_epoch is not None:
                try:
                    responses.extend(
                        pc_control_runtime.collect_output_resume_requests(
                            pc_id=pc_id,
                            connection_id=server_connection_id,
                            connection_epoch=connection_epoch,
                        )
                    )
                except PcCommandDispatchValidationError as exc:
                    responses = [
                        _protocol_error(
                            code=exc.code,
                            message=exc.message,
                            trace_id=f"trace:pc-control:resume:{pc_id}",
                            payload_pc_id=pc_id,
                            payload_epoch=connection_epoch,
                        )
                    ]

            first_response = responses[0] if responses else None
            for response in responses:
                await _send_payload(response)
            if first_response is not None and first_response.get("type") == "error" and server_connection_id is None:
                await websocket.close(code=1008, reason=str(first_response.get("payload", {}).get("code") or "error"))
                return
    finally:
        if server_connection_id is not None and pc_id is not None and connection_epoch is not None:
            pc_control_runtime.close_connection(
                pc_id=pc_id,
                connection_id=server_connection_id,
                connection_epoch=connection_epoch,
            )


async def _subscription_push_loop(
    websocket,
    *,
    relay: LoopbackRelayServer,
    connection_id: str,
    send_payload,
) -> None:
    try:
        while True:
            await asyncio.sleep(relay.phase3_broadcast_interval_seconds)
            responses = relay.collect_subscription_updates(connection_id)
            for response in responses:
                await send_payload(response)
    except asyncio.CancelledError:
        raise
    except websockets.ConnectionClosed:
        return
    except Exception:
        LOGGER.exception("relay subscription push loop crashed connection_id=%s", connection_id)


async def run_relay_server(
    config: RelayServerConfig,
    *,
    session_store: PersistentSessionStore | None = None,
    packet_store: PersistentAcceptedPacketStore | None = None,
    pc_control_runtime: PcControlRuntime | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    sessions = session_store or PersistentSessionStore(config.state_dir)
    packets = packet_store or PersistentAcceptedPacketStore(config.state_dir)
    pc_runtime = pc_control_runtime or build_pc_control_runtime(config)
    relay = build_runtime_relay(
        config,
        session_store=sessions,
        packet_store=packets,
    )
    ssl_context = _build_ssl_context(config)
    server = await start_relay_server(
        config,
        relay=relay,
        session_store=sessions,
        packet_store=packets,
        pc_control_runtime=pc_runtime,
        ssl_context=ssl_context,
    )
    try:
        LOGGER.info(
            "Relay server listening on %s:%s transport=%s",
            config.host,
            config.port,
            "wss" if ssl_context else "ws",
        )
        if shutdown_event is None:
            await asyncio.Future()
        else:
            await shutdown_event.wait()
    finally:
        server.close()
        await server.wait_closed()


async def start_relay_server(
    config: RelayServerConfig,
    *,
    relay: LoopbackRelayServer,
    session_store,
    packet_store,
    pc_control_runtime: PcControlRuntime | None = None,
    ssl_context: ssl.SSLContext | None = None,
    file_upload_limit_bytes: int | None = None,
):
    internal_config = replace(
        config,
        host="127.0.0.1",
        port=0,
        tls_certfile=None,
        tls_keyfile=None,
    )
    websocket_server = None
    http_server = None
    http_thread = None
    pc_runtime = pc_control_runtime or build_pc_control_runtime(config)
    try:
        websocket_server = await _start_internal_websocket_server(
            internal_config,
            relay=relay,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=pc_runtime,
        )
        websocket_host, websocket_port = websocket_server.sockets[0].getsockname()[:2]

        http_server = build_http_server(
            internal_config,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=pc_runtime,
            file_upload_limit_bytes=file_upload_limit_bytes,
        )
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()
        http_host, http_port = http_server.server_address[:2]

        listen_ref: dict[str, Any] = {"host": config.host, "port": config.port}
        public_server = await asyncio.start_server(
            lambda reader, writer: _handle_unified_connection(
                reader,
                writer,
                config=config,
                session_store=session_store,
                packet_store=packet_store,
                pc_control_runtime=pc_runtime,
                listen_ref=listen_ref,
                websocket_target=(str(websocket_host), int(websocket_port)),
                http_target=(str(http_host), int(http_port)),
            ),
            config.host,
            config.port,
            ssl=ssl_context,
        )
        public_host, public_port = public_server.sockets[0].getsockname()[:2]
        listen_ref["host"] = str(public_host)
        listen_ref["port"] = int(public_port)

        return _UnifiedRelayServer(
            public_server=public_server,
            websocket_server=websocket_server,
            http_server=http_server,
            http_thread=http_thread,
        )
    except Exception:
        if websocket_server is not None:
            websocket_server.close()
            await websocket_server.wait_closed()
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if http_thread is not None:
            await asyncio.to_thread(http_thread.join, 5)
        raise


async def _start_internal_websocket_server(
    config: RelayServerConfig,
    *,
    relay: LoopbackRelayServer,
    session_store,
    packet_store,
    pc_control_runtime: PcControlRuntime,
):
    return await websockets.serve(
        lambda websocket, path: _websocket_handler(
            websocket,
            path,
            relay=relay,
            pc_control_runtime=pc_control_runtime,
        ),
        config.host,
        config.port,
        process_request=lambda path, request_headers: _process_request(
            path,
            request_headers,
            config=config,
            session_store=session_store,
            packet_store=packet_store,
            pc_control_runtime=pc_control_runtime,
        ),
        ping_interval=30,
        ping_timeout=30,
        max_size=32 * 1024 * 1024,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the mail-runner relay server.")
    parser.add_argument("--host", default=None, help="Bind host; defaults to MAIL_RELAY_HOST or 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help="Bind port; defaults to MAIL_RELAY_PORT or 8787.")
    parser.add_argument("--transport-token", default=None, help="Required relay transport token.")
    parser.add_argument(
        "--android-app-token",
        default=None,
        help="Optional bearer token for the Android-facing create-session facade.",
    )
    parser.add_argument("--state-dir", default=None, help="Persistent state directory for sessions and packets.")
    parser.add_argument("--smtp-host", default=None, help="SMTP host used by the relay for user-facing delivery.")
    parser.add_argument("--smtp-port", type=int, default=None, help="SMTP port used by the relay.")
    parser.add_argument("--smtp-user", default=None, help="SMTP username used by the relay.")
    parser.add_argument("--smtp-password", default=None, help="SMTP password used by the relay.")
    parser.add_argument("--from-name", default=None, help="From display name for relay-sent mail.")
    parser.add_argument("--from-addr", default=None, help="From email address for relay-sent mail.")
    parser.add_argument(
        "--taskmail-bot-mailbox-addr",
        default=None,
        help="Bot mailbox address used for TaskMail direct new_task bridge ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-from-name",
        default=None,
        help="From display name used when the relay bridges direct TaskMail packets into bot mailbox mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-from-addr",
        default=None,
        help="From email address used when the relay bridges direct TaskMail packets into bot mailbox mail ingress.",
    )
    parser.add_argument("--tls-certfile", default=None, help="TLS certificate path for WSS/HTTPS.")
    parser.add_argument("--tls-keyfile", default=None, help="TLS private key path for WSS/HTTPS.")
    parser.add_argument("--log-level", default=None, help="Log level; defaults to MAIL_RELAY_LOG_LEVEL or INFO.")
    parser.add_argument("--server-name", default=None, help="Server display name; defaults to mail-runner-relay.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_relay_server_config(
        host=args.host,
        port=args.port,
        transport_token=args.transport_token,
        android_app_token=args.android_app_token,
        state_dir=args.state_dir,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_user=args.smtp_user,
        smtp_password=args.smtp_password,
        from_name=args.from_name,
        from_addr=args.from_addr,
        taskmail_bot_mailbox_addr=args.taskmail_bot_mailbox_addr,
        taskmail_direct_from_name=args.taskmail_direct_from_name,
        taskmail_direct_from_addr=args.taskmail_direct_from_addr,
        tls_certfile=args.tls_certfile,
        tls_keyfile=args.tls_keyfile,
        log_level=args.log_level,
        server_name=args.server_name,
    )
    configure_logging(config.log_level)
    try:
        asyncio.run(run_relay_server(config))
    except KeyboardInterrupt:
        LOGGER.info("Relay server interrupted by operator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
