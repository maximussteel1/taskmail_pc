"""PC-side control-plane sidecar for the VPS-first protocol."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
import json
import logging
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import websockets

from .config import AppConfig
from .mail_io import MailClient
from .mail_attachments import materialize_incoming_attachments
from .models import MailAttachment, MailEnvelope, RunResult, TaskSnapshot, ThreadState
from .parser import parse_subject
from .pc_control_plane_projection import project_artifact_manifest, project_output_chunks
from .pc_workspace_inventory import build_execution_capabilities, collect_workspace_inventory
from .question_utils import effective_pending_questions
from .relay_server.auth import token_fingerprint
from .relay_server.control_protocol import (
    CONTROL_CHANNEL,
    CONTROL_FALLBACK_POLICY,
    CONTROL_POST_CREATION_PAYLOAD_SCHEMA,
    CONTROL_SESSION_ACTION_RESULT_TYPE,
)
from .relay_server.direct_actions import RelayDirectActionError, RelayDirectActionResult
from .relay_server.packet_store import AcceptedRelayPacket
from .relay_server.post_creation_actions import (
    RelayTaskMailDirectCurrentSessionReplyHandler,
    RelayTaskMailDirectCurrentSessionStatusHandler,
    _build_direct_message_id,
    _build_post_creation_headers,
    _build_thread_reply_chain,
    _resolve_current_session_thread_state,
    _subject_text_for_target_state,
    _validate_plain_reply_target_state,
)
from .relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcControlProtocolError,
    PcErrorMessage,
    PcHelloAckMessage,
    PcIngressDecisionMessage,
    PcMailboxLeaseAckMessage,
    PcOutputResumeRequestMessage,
    PcTerminalOutcomeAckMessage,
    PcThreadBindingAckMessage,
    build_artifact_manifest,
    build_command_ack,
    build_command_event,
    build_command_result,
    build_ingress_candidate,
    build_mailbox_lease,
    build_output_chunk,
    build_heartbeat,
    build_pc_hello,
    build_terminal_outcome,
    build_thread_binding,
    build_workspace_snapshot,
    parse_pc_control_server_message,
)
from .status import (
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_FAILED,
    RUN_STATUS_KILLED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_SUCCESS,
)
from .thread_store import load_thread_state, save_thread_state
from .session_action_closeout import build_target_session_identity, load_session_action_closeout

LOGGER = logging.getLogger(__name__)
_WEBSOCKETS_CONNECT_SUPPORTS_PROXY = "proxy" in inspect.signature(websockets.connect).parameters
_CONTROL_PLANE_ATTACHMENT_PREFIX = "_ctrlin_"
_SESSION_ACTION_COMMAND_TYPES = frozenset(
    {"reply", "status", "pause", "resume", "kill", "end", "answers", "attachment_continuation"}
)
_EMPTY_BODY_SESSION_ACTION_TYPES = frozenset({"pause", "resume", "kill", "end"})


@dataclass(slots=True)
class _SessionActionCommand:
    action_type: str
    request_id: str
    packet_id: str
    receipt_id: str
    workspace_id: str
    session_id: str
    thread_id: str | None
    task_run_packet: dict[str, Any]
    dispatch_metadata: dict[str, Any]


def _normalize_session_action_question_answers(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("payload.answers.question_answers must be a non-empty list")
    normalized: list[dict[str, str]] = []
    seen_question_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"payload.answers.question_answers[{index}] must be a dict")
        question_id = str(item.get("question_id") or "").strip()
        if not question_id:
            raise ValueError(f"payload.answers.question_answers[{index}].question_id must be a non-empty string")
        if question_id in seen_question_ids:
            raise ValueError(
                f"payload.answers.question_answers[{index}].question_id duplicates an earlier answer entry"
            )
        answer_value = item.get("value")
        if not isinstance(answer_value, str) or not answer_value.strip():
            raise ValueError(f"payload.answers.question_answers[{index}].value must be a non-empty string")
        seen_question_ids.add(question_id)
        normalized.append(
            {
                "question_id": question_id,
                "value": answer_value.strip(),
            }
        )
    return sorted(normalized, key=lambda item: item["question_id"])


def _normalize_attachment_payload_items(
    value: Any,
    *,
    field_name: str,
    allow_string_paths: bool,
    require_non_empty: bool = False,
) -> list[str | dict[str, Any]]:
    if value is None:
        normalized: list[str | dict[str, Any]] = []
    elif not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list when provided")
    else:
        normalized = []
        for index, item in enumerate(value):
            item_field_name = f"{field_name}[{index}]"
            if isinstance(item, str):
                normalized_text = item.strip()
                if not allow_string_paths:
                    raise ValueError(f"{item_field_name} must be a JSON object")
                if normalized_text:
                    normalized.append(normalized_text)
                continue
            if not isinstance(item, dict):
                if allow_string_paths:
                    raise ValueError(f"{item_field_name} must be a string or JSON object")
                raise ValueError(f"{item_field_name} must be a JSON object")
            name = str(item.get("name") or "").strip()
            if not name:
                raise ValueError(f"{item_field_name}.name must be a non-empty string")
            content_type = str(item.get("content_type") or "").strip()
            if not content_type:
                raise ValueError(f"{item_field_name}.content_type must be a non-empty string")
            content_bytes_b64 = str(item.get("content_bytes_b64") or "").strip()
            if not content_bytes_b64:
                raise ValueError(f"{item_field_name}.content_bytes_b64 must be a non-empty string")
            size_bytes = item.get("size_bytes")
            if size_bytes is not None and (not isinstance(size_bytes, int) or size_bytes < 0):
                raise ValueError(f"{item_field_name}.size_bytes must be a non-negative integer")
            normalized.append(
                {
                    "name": name,
                    "content_type": content_type,
                    "content_bytes_b64": content_bytes_b64,
                    "size_bytes": size_bytes,
                }
            )
    if require_non_empty and not normalized:
        raise ValueError(f"{field_name} must contain at least one attachment entry")
    return normalized


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def derive_pc_control_url(relay_url: str) -> str:
    normalized = str(relay_url or "").strip()
    if not normalized:
        return ""
    parsed = urllib.parse.urlsplit(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError("relay_url must use ws:// or wss://")
    path = parsed.path or ""
    if path.endswith("/relay"):
        target_path = f"{path[:-6]}/pc-control" if path != "/relay" else "/pc-control"
    elif path.endswith("/control"):
        target_path = f"{path[:-8]}/pc-control" if path != "/control" else "/pc-control"
    elif not path or path == "/":
        target_path = "/pc-control"
    else:
        target_path = f"{path.rstrip('/')}/pc-control"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, target_path, parsed.query, parsed.fragment))


def derive_mailbox_key(config: AppConfig) -> str:
    imap_host = str(config.imap_host or "").strip().lower()
    imap_user = str(config.imap_user or "").strip().lower()
    if not imap_host or not imap_user:
        return ""
    return f"imap://{imap_user}@{imap_host}/INBOX"


def hash_references(references: list[str]) -> str | None:
    normalized = [str(item).strip() for item in references if str(item).strip()]
    if not normalized:
        return None
    digest = hashlib.sha256(" ".join(normalized).encode("utf-8")).hexdigest()
    return digest


class PcControlPlaneClient:
    def __init__(
        self,
        *,
        relay_url: str,
        transport_token: str,
        pc_id: str,
        client_version: str,
        display_name: str | None = None,
        config: AppConfig,
        runner=None,
        heartbeat_interval_seconds: int = 15,
        snapshot_interval_seconds: int = 60,
        verify_tls: bool = True,
        ca_file: str | None = None,
        clock: Callable[[], str] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        workspace_provider: Callable[[], list[dict[str, Any]]] | None = None,
        mail_client: Any | None = None,
    ) -> None:
        self._pc_control_url = derive_pc_control_url(relay_url)
        self._transport_token = str(transport_token or "").strip()
        self._pc_id = str(pc_id or "").strip()
        self._client_version = str(client_version or "").strip()
        self._display_name = str(display_name or socket.gethostname() or self._pc_id).strip() or self._pc_id
        self._config = config
        self._runner = runner
        self._heartbeat_interval_seconds = max(1, int(heartbeat_interval_seconds))
        self._snapshot_interval_seconds = max(self._heartbeat_interval_seconds, int(snapshot_interval_seconds))
        self._verify_tls = bool(verify_tls)
        self._ca_file = str(ca_file or "").strip() or None
        self._clock = clock or _timestamp
        self._monotonic = monotonic_fn or time.monotonic
        self._workspace_provider = workspace_provider or (lambda: collect_workspace_inventory(self._config))
        self._mail_client = mail_client or MailClient(self._config)
        self._runner_id = f"runner:{self._pc_id}:{secrets.token_hex(6)}"
        self._mailbox_key = derive_mailbox_key(self._config)
        self._lease_mode = str(self._config.relay_mailbox_lease_mode or "disabled").strip().lower() or "disabled"
        self._lease_ttl_seconds = max(5, int(self._config.relay_mailbox_lease_ttl_seconds or 45))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._websocket = None
        self._send_lock: asyncio.Lock | None = None
        self._current_connection_epoch: int | None = None
        self._control_lock = threading.Lock()
        self._command_ack_cache: dict[str, dict[str, Any]] = {}
        self._pending_client_messages: list[dict[str, Any]] = []
        self._launched_command_ids: set[str] = set()
        self._command_contexts: dict[str, dict[str, Any]] = {}
        self._output_chunk_replay_contexts: dict[str, dict[str, Any]] = {}
        self._pending_rpc_requests: dict[str, asyncio.Future] = {}
        self._lease_state: dict[str, Any] = {
            "mode": self._lease_mode,
            "mailbox_key": self._mailbox_key,
            "runner_id": self._runner_id,
            "status": "disabled" if self._lease_mode == "disabled" else "inactive",
            "lease_epoch": None,
            "expires_at": None,
            "lease_holder_id": None,
            "lease_pc_id": None,
            "reason": None,
            "degraded_mode": False,
            "connected": False,
        }

    @property
    def is_configured(self) -> bool:
        return bool(self._pc_control_url and self._transport_token and self._pc_id and self._client_version)

    @property
    def mailbox_lease_enabled(self) -> bool:
        return self._lease_mode != "disabled"

    def mailbox_lease_state(self) -> dict[str, Any]:
        with self._control_lock:
            return dict(self._lease_state)

    def can_consume_mailbox(self) -> bool:
        if not self.mailbox_lease_enabled:
            return True
        state = self.mailbox_lease_state()
        if state.get("status") == "active" and state.get("lease_holder_id") == self._runner_id:
            return True
        if state.get("connected"):
            return False
        return self._lease_mode == "degraded"

    def register_ingress_candidate(
        self,
        *,
        envelope,
        classification: str,
        subject_norm: str,
        candidate_status: str,
        candidate_reason: str | None = None,
        taskmail_request_id: str | None = None,
        packet_id: str | None = None,
        folder: str = "INBOX",
    ) -> dict[str, Any]:
        if not self.mailbox_lease_enabled:
            return self._synthetic_ingress_decision(
                classification=classification,
                candidate_status=candidate_status,
                candidate_reason=candidate_reason,
                degraded_mode=False,
            )
        if self._should_use_local_degraded_path():
            return self._synthetic_ingress_decision(
                classification=classification,
                candidate_status=candidate_status,
                candidate_reason=candidate_reason,
                degraded_mode=True,
            )
        state = self.mailbox_lease_state()
        return self._rpc_call(
            request_id=self._next_request_id("ingress"),
            trace_id=self._next_trace_id("ingress"),
            payload_builder=lambda request_id, trace_id, connection_epoch: build_ingress_candidate(
                message_id=self._next_message_id("ingress_candidate"),
                trace_id=trace_id,
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                request_id=request_id,
                mailbox_key=self._mailbox_key,
                lease_holder_id=self._runner_id,
                lease_epoch=int(state["lease_epoch"]),
                folder=folder,
                uid_validity=getattr(envelope, "imap_uid_validity", None),
                uid=getattr(envelope, "imap_uid", None),
                ingress_message_id=envelope.message_id,
                in_reply_to=envelope.in_reply_to,
                references_hash=hash_references(getattr(envelope, "references", []) or []),
                from_addr=envelope.from_addr,
                subject=envelope.subject,
                subject_norm=subject_norm,
                raw_date=(str(envelope.date) if getattr(envelope, "date", None) is not None else None),
                classification=classification,
                candidate_status=candidate_status,
                candidate_reason=candidate_reason,
                taskmail_request_id=taskmail_request_id,
                packet_id=packet_id,
                degraded_mode=False,
            ),
        )

    def commit_thread_binding(
        self,
        *,
        ingress_id: str | None,
        root_message_id: str,
        thread_id: str,
        session_id: str,
        repo_path: str,
        workdir: str | None,
        subject_norm: str,
        degraded_mode: bool = False,
    ) -> dict[str, Any]:
        if not ingress_id or not self.mailbox_lease_enabled:
            return {
                "binding_status": "committed",
                "ingress_id": ingress_id,
                "thread_id": thread_id,
                "session_id": session_id,
                "degraded_mode": degraded_mode,
            }
        if self._should_use_local_degraded_path():
            return {
                "binding_status": "committed",
                "ingress_id": ingress_id,
                "thread_id": thread_id,
                "session_id": session_id,
                "degraded_mode": True,
            }
        state = self.mailbox_lease_state()
        return self._rpc_call(
            request_id=self._next_request_id("thread_binding"),
            trace_id=self._next_trace_id("thread_binding"),
            payload_builder=lambda request_id, trace_id, connection_epoch: build_thread_binding(
                message_id=self._next_message_id("thread_binding"),
                trace_id=trace_id,
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                request_id=request_id,
                mailbox_key=self._mailbox_key,
                lease_holder_id=self._runner_id,
                lease_epoch=int(state["lease_epoch"]),
                ingress_id=ingress_id,
                root_message_id=root_message_id,
                thread_id=thread_id,
                session_id=session_id,
                repo_path=repo_path,
                workdir=workdir,
                subject_norm=subject_norm,
                degraded_mode=False,
            ),
        )

    def commit_terminal_outcome(
        self,
        *,
        thread_id: str,
        task_id: str,
        run_status: str,
        generated_at: str,
        last_summary: str | None,
        terminal_mail_message_id: str | None,
        terminal_mail_subject: str | None,
        taskmail_request_id: str | None,
        packet_id: str | None,
        source_ingress_id: str | None,
        degraded_mode: bool = False,
    ) -> dict[str, Any]:
        if not self.mailbox_lease_enabled:
            return {
                "outcome_status": "committed",
                "thread_id": thread_id,
                "task_id": task_id,
                "source_ingress_id": source_ingress_id,
                "degraded_mode": degraded_mode,
            }
        if self._should_use_local_degraded_path():
            return {
                "outcome_status": "committed",
                "thread_id": thread_id,
                "task_id": task_id,
                "source_ingress_id": source_ingress_id,
                "degraded_mode": True,
            }
        state = self.mailbox_lease_state()
        return self._rpc_call(
            request_id=self._next_request_id("terminal_outcome"),
            trace_id=self._next_trace_id("terminal_outcome"),
            payload_builder=lambda request_id, trace_id, connection_epoch: build_terminal_outcome(
                message_id=self._next_message_id("terminal_outcome"),
                trace_id=trace_id,
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                request_id=request_id,
                mailbox_key=self._mailbox_key,
                lease_holder_id=self._runner_id,
                lease_epoch=int(state["lease_epoch"]),
                thread_id=thread_id,
                task_id=task_id,
                run_status=run_status,
                generated_at=generated_at,
                last_summary=last_summary,
                terminal_mail_message_id=terminal_mail_message_id,
                terminal_mail_subject=terminal_mail_subject,
                taskmail_request_id=taskmail_request_id,
                packet_id=packet_id,
                source_ingress_id=source_ingress_id,
                degraded_mode=False,
            ),
        )

    def start(self) -> None:
        if not self.is_configured:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_thread, daemon=True, name=f"pc-control-{self._pc_id}")
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        if self.mailbox_lease_enabled and self._loop is not None and self._websocket is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._release_mailbox_lease(), self._loop).result(timeout=timeout_seconds)
            except Exception:
                LOGGER.debug("pc-control mailbox lease release skipped during shutdown", exc_info=True)
        if self._loop is not None and self._websocket is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._websocket.close(), self._loop).result(timeout=timeout_seconds)
            except Exception:
                LOGGER.debug("pc-control websocket close skipped during shutdown", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, timeout_seconds))

    def _set_connection_state(self, *, connected: bool) -> None:
        with self._control_lock:
            self._lease_state["connected"] = connected
            if connected:
                if self.mailbox_lease_enabled and self._lease_state["status"] == "disabled":
                    self._lease_state["status"] = "inactive"
                return
            if self.mailbox_lease_enabled:
                self._lease_state["status"] = "inactive"
                self._lease_state["reason"] = "pc-control disconnected"
                self._lease_state["lease_holder_id"] = None
                self._lease_state["lease_pc_id"] = None
                self._lease_state["expires_at"] = None

    def _update_lease_state_from_ack(self, payload: dict[str, Any]) -> None:
        with self._control_lock:
            self._lease_state["status"] = str(payload.get("lease_status") or "inactive")
            self._lease_state["lease_epoch"] = payload.get("lease_epoch")
            self._lease_state["expires_at"] = payload.get("expires_at")
            self._lease_state["lease_holder_id"] = payload.get("lease_holder_id")
            self._lease_state["lease_pc_id"] = payload.get("lease_pc_id")
            self._lease_state["reason"] = payload.get("reason")
            self._lease_state["degraded_mode"] = bool(payload.get("degraded_mode"))
            self._lease_state["connected"] = True

    def _synthetic_ingress_decision(
        self,
        *,
        classification: str,
        candidate_status: str,
        candidate_reason: str | None,
        degraded_mode: bool,
    ) -> dict[str, Any]:
        if candidate_status == "stale":
            decision = "stale"
        elif candidate_status == "invalid":
            decision = "invalid"
        elif candidate_status == "ignored":
            decision = "ignored"
        else:
            decision = "accepted"
        return {
            "type": "ingress_decision",
            "ingress_id": None,
            "mailbox_key": self._mailbox_key or None,
            "decision": decision,
            "reason": candidate_reason,
            "classification": classification,
            "lease_holder_id": self._runner_id if not degraded_mode else None,
            "lease_epoch": self.mailbox_lease_state().get("lease_epoch"),
            "thread_id": None,
            "session_id": None,
            "degraded_mode": degraded_mode,
        }

    def _should_use_local_degraded_path(self) -> bool:
        if self._lease_mode != "degraded":
            return False
        if not self._mailbox_key:
            return True
        state = self.mailbox_lease_state()
        if state.get("connected"):
            return False
        return True

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run_forever())
        except Exception:
            LOGGER.exception("pc-control sidecar crashed")

    async def _run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            try:
                await self._connect_once()
            except Exception:
                LOGGER.exception("pc-control sidecar reconnect loop failed")
            if self._stop_event.is_set():
                break
            await asyncio.sleep(2)

    async def _rpc_async(
        self,
        *,
        request_id: str,
        trace_id: str,
        payload_builder,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        websocket = self._websocket
        send_lock = self._send_lock
        connection_epoch = self._current_connection_epoch
        if websocket is None or send_lock is None or connection_epoch is None:
            raise RuntimeError("pc-control sidecar is not connected")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._control_lock:
            self._pending_rpc_requests[request_id] = future
        try:
            payload = payload_builder(request_id, trace_id, connection_epoch)
            await self._send_payload(websocket, payload, send_lock)
            resolved = await asyncio.wait_for(future, timeout=timeout_seconds or max(5.0, self._heartbeat_interval_seconds))
            return dict(resolved)
        finally:
            with self._control_lock:
                self._pending_rpc_requests.pop(request_id, None)

    def _rpc_call(self, *, request_id: str, trace_id: str, payload_builder, timeout_seconds: float | None = None) -> dict[str, Any]:
        if self._loop is None:
            raise RuntimeError("pc-control sidecar loop is not running")
        future = asyncio.run_coroutine_threadsafe(
            self._rpc_async(
                request_id=request_id,
                trace_id=trace_id,
                payload_builder=payload_builder,
                timeout_seconds=timeout_seconds,
            ),
            self._loop,
        )
        return future.result(timeout=(timeout_seconds or max(5.0, self._heartbeat_interval_seconds)) + 1.0)

    def _resolve_rpc_response(self, parsed) -> bool:
        if not isinstance(
            parsed,
            (PcMailboxLeaseAckMessage, PcIngressDecisionMessage, PcThreadBindingAckMessage, PcTerminalOutcomeAckMessage),
        ):
            return False
        request_id = str(parsed.payload.get("request_id") or "").strip()
        if not request_id:
            return False
        with self._control_lock:
            future = self._pending_rpc_requests.get(request_id)
        if future is None or future.done():
            return False
        payload = {"type": parsed.type, **dict(parsed.payload)}
        future.set_result(payload)
        return True

    def _fail_pending_rpc_requests(self, exc: Exception) -> None:
        with self._control_lock:
            pending = list(self._pending_rpc_requests.values())
            self._pending_rpc_requests = {}
        for future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _maintain_mailbox_lease(self) -> None:
        if not self.mailbox_lease_enabled or not self._mailbox_key:
            return
        state = self.mailbox_lease_state()
        operation = "acquire"
        lease_epoch = None
        if state.get("status") == "active" and state.get("lease_holder_id") == self._runner_id:
            expires_at = state.get("expires_at")
            if expires_at:
                try:
                    expires_dt = datetime.fromisoformat(str(expires_at))
                    remaining = (expires_dt - datetime.fromisoformat(self._clock())).total_seconds()
                except Exception:
                    remaining = 0.0
                if remaining > max(5.0, self._lease_ttl_seconds / 2):
                    return
            operation = "renew"
            lease_epoch = state.get("lease_epoch")
        try:
            response = await self._rpc_async(
                request_id=self._next_request_id("mailbox_lease"),
                trace_id=self._next_trace_id("mailbox_lease"),
                payload_builder=lambda request_id, trace_id, connection_epoch: build_mailbox_lease(
                    message_id=self._next_message_id("mailbox_lease"),
                    trace_id=trace_id,
                    pc_id=self._pc_id,
                    connection_epoch=connection_epoch,
                    sent_at=self._clock(),
                    request_id=request_id,
                    operation=operation,
                    mailbox_key=self._mailbox_key,
                    lease_holder_id=self._runner_id,
                    lease_ttl_seconds=self._lease_ttl_seconds,
                    lease_epoch=(None if lease_epoch is None else int(lease_epoch)),
                    config_fingerprint=token_fingerprint(
                        f"{self._config.imap_host}|{self._config.imap_user}|{self._config.new_task_max_age_minutes}"
                    ),
                    host_fingerprint=token_fingerprint(socket.gethostname()),
                    runtime_fingerprint=token_fingerprint(self._runner_id),
                    degraded_mode=False,
                ),
            )
            self._update_lease_state_from_ack(response)
        except Exception:
            LOGGER.warning("pc-control mailbox lease maintenance failed", exc_info=True)
            self._set_connection_state(connected=False)

    async def _release_mailbox_lease(self) -> None:
        if not self.mailbox_lease_enabled or not self._mailbox_key:
            return
        state = self.mailbox_lease_state()
        if state.get("status") != "active" or state.get("lease_holder_id") != self._runner_id:
            return
        await self._rpc_async(
            request_id=self._next_request_id("mailbox_lease_release"),
            trace_id=self._next_trace_id("mailbox_lease_release"),
            payload_builder=lambda request_id, trace_id, connection_epoch: build_mailbox_lease(
                message_id=self._next_message_id("mailbox_lease"),
                trace_id=trace_id,
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                request_id=request_id,
                operation="release",
                mailbox_key=self._mailbox_key,
                lease_holder_id=self._runner_id,
                lease_ttl_seconds=self._lease_ttl_seconds,
                lease_epoch=int(state["lease_epoch"]),
                degraded_mode=False,
            ),
            timeout_seconds=3.0,
        )

    async def _connect_once(self) -> None:
        ssl_context = self._build_ssl_context()
        async with websockets.connect(
            self._pc_control_url,
            ssl=ssl_context,
            open_timeout=max(1, self._heartbeat_interval_seconds),
            close_timeout=max(1, self._heartbeat_interval_seconds),
            extra_headers={"Authorization": f"Bearer {self._transport_token}"},
            max_size=4 * 1024 * 1024,
            **_direct_websocket_connect_kwargs(),
        ) as websocket:
            self._websocket = websocket
            send_lock = asyncio.Lock()
            self._send_lock = send_lock
            connection_epoch = await self._perform_hello(websocket, send_lock)
            self._current_connection_epoch = connection_epoch
            self._set_connection_state(connected=True)
            await self._send_workspace_snapshot(websocket, connection_epoch, send_lock)
            await self._flush_pending_client_messages(websocket, send_lock)
            await self._replay_output_chunks_after_reconnect(websocket, send_lock)
            receiver_task = asyncio.create_task(
                self._receive_loop(
                    websocket,
                    connection_epoch=connection_epoch,
                    send_lock=send_lock,
                )
            )
            if self.mailbox_lease_enabled:
                await self._maintain_mailbox_lease()
            last_snapshot_at = self._monotonic()
            try:
                while not self._stop_event.is_set() and not receiver_task.done():
                    stop_requested = await asyncio.to_thread(self._stop_event.wait, self._heartbeat_interval_seconds)
                    if stop_requested:
                        break
                    await self._send_heartbeat(websocket, connection_epoch, send_lock)
                    if self.mailbox_lease_enabled:
                        await self._maintain_mailbox_lease()
                    if self._monotonic() - last_snapshot_at >= self._snapshot_interval_seconds:
                        await self._send_workspace_snapshot(websocket, connection_epoch, send_lock)
                        last_snapshot_at = self._monotonic()
                if receiver_task.done():
                    exc = receiver_task.exception()
                    if exc is not None:
                        raise exc
            finally:
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass
                self._set_connection_state(connected=False)
                self._fail_pending_rpc_requests(RuntimeError("pc-control sidecar disconnected"))
                self._mark_output_chunk_replay_needed()
                self._websocket = None
                self._send_lock = None
                self._current_connection_epoch = None

    async def _perform_hello(self, websocket, send_lock: asyncio.Lock) -> int:
        capabilities = build_execution_capabilities(self._config).to_payload()
        trace_id = self._next_trace_id("pc_hello")
        await self._send_payload(
            websocket,
            build_pc_hello(
                message_id=self._next_message_id("pc_hello"),
                trace_id=trace_id,
                pc_id=self._pc_id,
                sent_at=self._clock(),
                display_name=self._display_name,
                client_version=self._client_version,
                host_fingerprint=token_fingerprint(socket.gethostname()),
                runtime_fingerprint=token_fingerprint(f"{self._runner_id}|{self._pc_control_url}"),
                capabilities=capabilities,
            ),
            send_lock,
        )
        while True:
            parsed = self._parse_server_frame(json.loads(await websocket.recv()))
            if isinstance(parsed, PcErrorMessage):
                raise RuntimeError(f"{parsed.payload['code']}: {parsed.payload['message']}")
            if isinstance(parsed, PcHelloAckMessage):
                return parsed.connection_epoch
            LOGGER.debug("Ignoring unexpected pc-control server frame before hello_ack: %s", parsed.type)

    async def _send_heartbeat(self, websocket, connection_epoch: int, send_lock: asyncio.Lock) -> None:
        workspaces = self._workspace_provider()
        active_run_count = self._runner_count("active_count")
        await self._send_payload(
            websocket,
            build_heartbeat(
                message_id=self._next_message_id("heartbeat"),
                trace_id=self._next_trace_id("heartbeat"),
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                active_run_count=active_run_count,
                workspace_count=len(workspaces),
                load_hint="busy" if active_run_count > 0 else "normal",
            ),
            send_lock,
        )

    async def _send_workspace_snapshot(self, websocket, connection_epoch: int, send_lock: asyncio.Lock) -> None:
        await self._send_payload(
            websocket,
            build_workspace_snapshot(
                message_id=self._next_message_id("workspace_snapshot"),
                trace_id=self._next_trace_id("workspace_snapshot"),
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                snapshot_id=self._next_snapshot_id(),
                workspaces=self._workspace_provider(),
            ),
            send_lock,
        )

    async def _receive_loop(self, websocket, *, connection_epoch: int, send_lock: asyncio.Lock) -> None:
        async for raw_message in websocket:
            parsed = self._parse_server_frame(json.loads(raw_message))
            if isinstance(parsed, PcErrorMessage):
                LOGGER.warning(
                    "pc-control server error code=%s message=%s",
                    parsed.payload["code"],
                    parsed.payload["message"],
                )
                continue
            if self._resolve_rpc_response(parsed):
                if isinstance(parsed, PcMailboxLeaseAckMessage):
                    self._update_lease_state_from_ack(parsed.payload)
                continue
            if isinstance(parsed, PcCommandDispatchMessage):
                await self._handle_command_dispatch(
                    websocket,
                    message=parsed,
                    connection_epoch=connection_epoch,
                    send_lock=send_lock,
                )
                continue
            if isinstance(parsed, PcOutputResumeRequestMessage):
                await self._handle_output_resume_request(
                    websocket,
                    message=parsed,
                    connection_epoch=connection_epoch,
                    send_lock=send_lock,
                )
                continue
            LOGGER.debug("Ignoring unexpected pc-control server frame: %s", parsed.type)

    async def _handle_command_dispatch(
        self,
        websocket,
        *,
        message: PcCommandDispatchMessage,
        connection_epoch: int,
        send_lock: asyncio.Lock,
    ) -> None:
        if message.pc_id != self._pc_id or message.connection_epoch != connection_epoch:
            LOGGER.warning(
                "Ignoring command_dispatch for mismatched routing pc_id=%s epoch=%s",
                message.pc_id,
                message.connection_epoch,
            )
            return
        command_id = message.payload["command_id"]
        ack_payload = self._command_ack_cache.get(command_id)
        admission: dict[str, Any] | None = None
        if ack_payload is None:
            admission = self._admit_command(message)
            ack_payload = build_command_ack(
                message_id=self._next_message_id("command_ack"),
                trace_id=message.trace_id,
                pc_id=self._pc_id,
                connection_epoch=connection_epoch,
                sent_at=self._clock(),
                command_id=command_id,
                ack_status=admission["ack_status"],
                queue_position=admission["queue_position"],
                reason=admission["reason"],
                error_code=admission["error_code"],
            )
            self._command_ack_cache[command_id] = ack_payload
        await self._send_payload(websocket, ack_payload, send_lock)
        if admission is not None and admission["ack_status"] in {"accepted", "accepted_but_queued"}:
            self._start_command_execution(
                message,
                admission=admission,
                connection_epoch=connection_epoch,
            )

    def _admit_command(self, message: PcCommandDispatchMessage) -> dict[str, Any]:
        error_code, reason = self._validate_command_dispatch(message)
        if error_code is not None:
            return {
                "ack_status": "rejected",
                "queue_position": None,
                "reason": reason,
                "error_code": error_code,
            }
        command_type = str(message.payload["command_type"] or "").strip().lower()
        if command_type == "status":
            return {
                "ack_status": "accepted",
                "queue_position": None,
                "reason": None,
                "error_code": None,
            }
        active_count = self._runner_count("active_count")
        queued_count = self._runner_count("queued_count")
        if active_count > 0 or queued_count > 0:
            return {
                "ack_status": "accepted_but_queued",
                "queue_position": max(1, queued_count + (1 if active_count > 0 else 0)),
                "reason": "command accepted into the local runner queue",
                "error_code": None,
            }
        return {
            "ack_status": "accepted",
            "queue_position": None,
            "reason": None,
            "error_code": None,
        }

    def _validate_command_dispatch(self, message: PcCommandDispatchMessage) -> tuple[str | None, str | None]:
        workspace_inventory = {
            str(item.get("workspace_id") or "").strip(): item
            for item in self._workspace_provider()
            if str(item.get("workspace_id") or "").strip()
        }
        workspace = workspace_inventory.get(message.payload["workspace_id"])
        if workspace is None:
            return "unknown_workspace", "workspace_id is not currently available on this PC"

        capabilities = dict(workspace.get("capabilities") or build_execution_capabilities(self._config).to_payload())
        policy = dict(message.payload["execution_policy"])
        command_type = str(message.payload["command_type"] or "").strip().lower()

        if command_type in _SESSION_ACTION_COMMAND_TYPES:
            return self._validate_session_action_dispatch(message)
        if command_type != "new_task":
            return "unsupported_command_type", f"command_type is not implemented on this PC client: {command_type}"

        backend = str(policy.get("backend") or "").strip().lower()
        if not backend:
            if command_type == "new_task":
                return "unsupported_backend", "new_task requires execution_policy.backend"
            return None, None

        supported_backends = {
            str(item).strip().lower() for item in capabilities.get("supported_backends", []) if str(item).strip()
        }
        if backend not in supported_backends:
            return "unsupported_backend", f"backend is not supported on this PC/workspace: {backend}"

        normalized_profile_catalogs = {
            str(key).strip().lower(): {
                str(item).strip().lower() for item in value if str(item).strip()
            }
            for key, value in dict(capabilities.get("profile_catalogs") or {}).items()
            if str(key).strip()
        }
        profile = str(policy.get("profile") or "").strip().lower()
        if profile:
            if profile not in normalized_profile_catalogs.get(backend, set()):
                return "unsupported_profile", f"profile is not supported on this PC/workspace: {backend}/{profile}"
            if profile != "default":
                resolved_model = self._resolve_profile_model(backend, profile)
                if resolved_model is None:
                    return "profile_model_unresolved", f"profile could not be resolved to a local model: {backend}/{profile}"

        permission = str(policy.get("permission") or "").strip().lower()
        if permission:
            supported_permissions = {
                str(item).strip().lower() for item in capabilities.get("permission_modes", []) if str(item).strip()
            }
            if permission not in supported_permissions:
                return "unsupported_permission", f"permission is not supported on this PC/workspace: {permission}"

        backend_transport = str(policy.get("backend_transport") or "").strip().lower()
        if backend_transport:
            normalized_transport_modes = {
                str(key).strip().lower(): {
                    str(item).strip().lower() for item in value if str(item).strip()
                }
                for key, value in dict(capabilities.get("backend_transport_modes") or {}).items()
                if str(key).strip()
            }
            if backend_transport not in normalized_transport_modes.get(backend, set()):
                return "unsupported_backend_transport", (
                    f"backend_transport is not supported on this PC/workspace: {backend}/{backend_transport}"
                )

        task_text = str(message.payload["payload"].get("task_text") or "").strip()
        if not task_text:
            return "invalid_command_payload", "new_task requires payload.task_text"
        attachments_raw = message.payload["payload"].get("attachments")
        try:
            _normalize_attachment_payload_items(
                attachments_raw,
                field_name="payload.attachments",
                allow_string_paths=True,
            )
        except ValueError as exc:
            return "invalid_command_payload", str(exc)

        return None, None

    def _validate_session_action_dispatch(self, message: PcCommandDispatchMessage) -> tuple[str | None, str | None]:
        task_root = self._runner_task_root()
        if task_root is None:
            return (
                "direct_temporarily_unavailable",
                "runner task_root is unavailable for current-session action handling",
            )
        if self._session_action_bot_mailbox_addr() is None:
            return (
                "direct_temporarily_unavailable",
                "bot mailbox address is not configured for current-session action handling",
            )
        try:
            command = self._build_session_action_command(message)
            target_state = self._resolve_session_action_target_state(command, task_root=task_root)
            if self._resolve_session_action_recipient(task_root=task_root, state=target_state) is None:
                return (
                    "session_recipient_unresolved",
                    "could not resolve a durable canonical reply recipient for the requested session action",
                )
        except RelayDirectActionError as exc:
            return exc.code, exc.message
        except ValueError as exc:
            return "invalid_command_payload", str(exc).strip() or "invalid current-session action payload"
        return None, None

    def _build_session_action_command(self, message: PcCommandDispatchMessage) -> _SessionActionCommand:
        command_type = str(message.payload["command_type"] or "").strip().lower()
        if command_type not in _SESSION_ACTION_COMMAND_TYPES:
            raise ValueError(f"unsupported session-action command_type: {command_type}")

        command_payload = dict(message.payload["payload"] or {})
        target_raw = command_payload.get("target")
        if target_raw is not None and not isinstance(target_raw, dict):
            raise ValueError("payload.target must be a dict when present")
        target_payload = dict(target_raw or {})

        target_scope = str(target_payload.get("scope") or "").strip() or "current_session"
        if target_scope != "current_session":
            raise ValueError("payload.target.scope must be current_session")

        workspace_id = str(target_payload.get("workspace_id") or message.payload["workspace_id"] or "").strip()
        if workspace_id != str(message.payload["workspace_id"] or "").strip():
            raise ValueError("payload.target.workspace_id must match dispatch workspace_id")

        dispatch_session_id = str(message.payload.get("session_id") or "").strip()
        session_id = str(target_payload.get("session_id") or dispatch_session_id or "").strip()
        if not session_id:
            raise ValueError("current-session action requires session_id or payload.target.session_id")
        if dispatch_session_id and dispatch_session_id != session_id:
            raise ValueError("dispatch session_id must match payload.target.session_id")

        thread_id = str(target_payload.get("thread_id") or command_payload.get("thread_id") or "").strip() or None
        normalized_target = {
            "scope": "current_session",
            "workspace_id": workspace_id,
            "session_id": session_id,
        }
        if thread_id is not None:
            normalized_target["thread_id"] = thread_id

        request_id = str(message.payload["command_id"] or "").strip()
        if not request_id:
            raise ValueError("command_id is required for current-session action dispatch")
        task_run_packet: dict[str, Any] = {
            "schema_version": CONTROL_POST_CREATION_PAYLOAD_SCHEMA,
            "action": command_type,
            "request_id": request_id,
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "pc_control",
            },
            "target": normalized_target,
        }
        if command_type == "reply":
            reply_payload = command_payload.get("reply")
            if not isinstance(reply_payload, dict):
                raise ValueError("payload.reply must be a dict")
            task_run_packet["reply"] = dict(reply_payload)
        elif command_type == "status":
            status_payload = command_payload.get("status")
            if status_payload is None:
                status_payload = {}
            if not isinstance(status_payload, dict):
                raise ValueError("payload.status must be a dict when present")
            task_run_packet["status"] = dict(status_payload)
        elif command_type == "answers":
            answers_payload = command_payload.get("answers")
            if not isinstance(answers_payload, dict):
                raise ValueError("payload.answers must be a dict")
            task_run_packet["answers"] = {
                "question_answers": _normalize_session_action_question_answers(
                    answers_payload.get("question_answers")
                )
            }
        elif command_type == "attachment_continuation":
            attachment_payload = command_payload.get("attachment_continuation")
            if not isinstance(attachment_payload, dict):
                raise ValueError("payload.attachment_continuation must be a dict")
            attachments = _normalize_attachment_payload_items(
                attachment_payload.get("attachments"),
                field_name="payload.attachment_continuation.attachments",
                allow_string_paths=False,
                require_non_empty=True,
            )
            reply_text = attachment_payload.get("reply_text")
            if reply_text is not None and not isinstance(reply_text, str):
                raise ValueError("payload.attachment_continuation.reply_text must be a string when present")
            normalized_attachment_payload: dict[str, Any] = {
                "attachments": [dict(item) for item in attachments if isinstance(item, dict)],
            }
            normalized_reply_text = str(reply_text or "").strip()
            if normalized_reply_text:
                normalized_attachment_payload["reply_text"] = normalized_reply_text
            task_run_packet["attachment_continuation"] = normalized_attachment_payload
        else:
            action_payload = command_payload.get(command_type)
            if action_payload is None:
                action_payload = {}
            if not isinstance(action_payload, dict):
                raise ValueError(f"payload.{command_type} must be a dict when present")
            if action_payload:
                raise ValueError(f"payload.{command_type} must be an empty dict in the current first slice")
            task_run_packet[command_type] = {}

        return _SessionActionCommand(
            action_type=command_type,
            request_id=request_id,
            packet_id=f"pc-control:session-action:{request_id}",
            receipt_id=f"pc-control:session-action-receipt:{request_id}",
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            task_run_packet=task_run_packet,
            dispatch_metadata={
                "schema_version": CONTROL_POST_CREATION_PAYLOAD_SCHEMA,
                "channel": CONTROL_CHANNEL,
                "action": command_type,
                "fallback_policy": CONTROL_FALLBACK_POLICY,
                "control_trace": {
                    "trace_id": message.trace_id,
                },
                "control_related": {
                    "pc_control_command_id": request_id,
                },
            },
        )

    def _resolve_session_action_target_state(self, command: _SessionActionCommand, *, task_root: str) -> ThreadState:
        task_root_path = Path(task_root)
        target_state = _resolve_current_session_thread_state(command, task_root_path)
        if command.action_type == "reply":
            _validate_plain_reply_target_state(target_state)
        if command.action_type == "answers":
            self._validate_answers_target_state(command, target_state=target_state)
        return target_state

    def _resolve_session_action_recipient(self, *, task_root: str, state: ThreadState) -> str | None:
        from .app import _resolve_recovery_recipient

        normalized = str(state.canonical_reply_recipient or "").strip()
        if normalized:
            return normalized
        recipient = _resolve_recovery_recipient(Path(task_root), state)
        normalized = str(recipient or "").strip()
        if not normalized:
            return None
        state.canonical_reply_recipient = normalized
        state.updated_at = self._clock()
        save_thread_state(state, task_root)
        return normalized

    def _session_action_bot_mailbox_addr(self) -> str | None:
        for candidate in (self._config.from_addr, self._config.smtp_user, self._config.imap_user):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        return None

    def _resolve_profile_model(self, backend: str, profile: str) -> str | None:
        if not profile or profile == "default":
            return None
        if backend == "codex":
            mapping = self._config.codex_profile_models
        elif backend == "opencode":
            mapping = self._config.opencode_profile_models
        else:
            return None
        normalized_mapping = {
            str(key).strip().lower(): str(value).strip()
            for key, value in mapping.items()
            if str(key).strip()
        }
        resolved = normalized_mapping.get(profile)
        if resolved is None:
            return None
        return resolved or None

    def _start_command_execution(
        self,
        message: PcCommandDispatchMessage,
        *,
        admission: dict[str, Any],
        connection_epoch: int,
    ) -> None:
        command_type = str(message.payload["command_type"] or "").strip().lower()
        if command_type == "new_task":
            self._start_new_task_execution(
                message,
                admission=admission,
                connection_epoch=connection_epoch,
            )
            return
        self._start_session_action_execution(
            message,
            connection_epoch=connection_epoch,
        )

    def _start_new_task_execution(
        self,
        message: PcCommandDispatchMessage,
        *,
        admission: dict[str, Any],
        connection_epoch: int,
    ) -> None:
        runner_start = getattr(self._runner, "start_background_task", None)
        if not callable(runner_start):
            LOGGER.info("pc-control command accepted but no runner.start_background_task() is available")
            return
        command_id = message.payload["command_id"]
        base_context = {
            "trace_id": message.trace_id,
            "connection_epoch": connection_epoch,
            "execution_policy": dict(message.payload["execution_policy"]),
            "snapshot": None,
        }
        with self._control_lock:
            if command_id in self._launched_command_ids:
                return
            self._command_contexts[command_id] = dict(base_context)
        try:
            snapshot = self._build_task_snapshot(message)
            with self._control_lock:
                self._launched_command_ids.add(command_id)
                self._command_contexts[command_id] = {**base_context, "snapshot": snapshot}
            self._remember_output_chunk_replay_context(
                command_id,
                trace_id=message.trace_id,
                thread_id=snapshot.thread_id,
                task_id=snapshot.task_id,
            )

            runner_start(
                snapshot,
                root_message_id=f"<pc-control-{self._sanitize_identifier(command_id, prefix='root')}@local>",
                latest_message_id=f"<pc-control-{self._sanitize_identifier(command_id, prefix='latest')}@local>",
                subject_norm=f"pc-control:{snapshot.thread_id}",
                session_name=str(message.payload.get("session_id") or snapshot.thread_id),
                on_accepted=lambda _state: self._emit_command_event(
                    command_id,
                    event_type="accepted",
                    summary="command accepted by the local runner",
                    effective_execution=self._effective_execution(command_id),
                ),
                on_running=lambda state: self._on_runner_running(command_id, state),
                on_finished=lambda state, result: self._on_runner_finished(command_id, state, result),
            )
            if admission["ack_status"] == "accepted_but_queued":
                self._emit_command_event(
                    command_id,
                    event_type="queued",
                    summary="command accepted into the local runner queue",
                    event_payload={"queue_position": admission["queue_position"]},
                    effective_execution=self._effective_execution(command_id),
                )
        except Exception as exc:
            LOGGER.exception("pc-control command execution bootstrap failed command_id=%s", command_id)
            error_text = f"{type(exc).__name__}: {exc}"
            self._emit_command_event(
                command_id,
                event_type="failed",
                summary=error_text,
            )
            self._emit_command_result(
                command_id,
                final_status="failed",
                summary=error_text,
                structured_payload={
                    "kind": "command_bootstrap_error",
                    "command_id": command_id,
                    "message": error_text,
                },
                error_code="command_bootstrap_failed",
                error_message=error_text,
            )

    def _start_session_action_execution(
        self,
        message: PcCommandDispatchMessage,
        *,
        connection_epoch: int,
    ) -> None:
        command_id = message.payload["command_id"]
        base_context = {
            "trace_id": message.trace_id,
            "connection_epoch": connection_epoch,
            "execution_policy": dict(message.payload["execution_policy"]),
            "snapshot": None,
        }
        with self._control_lock:
            if command_id in self._launched_command_ids:
                return
            self._launched_command_ids.add(command_id)
            self._command_contexts[command_id] = dict(base_context)
        try:
            command = self._build_session_action_command(message)
            structured_payload = self._dispatch_session_action_command(command)
            self._emit_command_result(
                command_id,
                final_status="done",
                summary=f"{command.action_type} mail ingress submitted",
                structured_payload=structured_payload,
                effective_execution=self._effective_execution(command_id),
            )
        except RelayDirectActionError as exc:
            LOGGER.info(
                "pc-control session-action command failed command_id=%s code=%s message=%s",
                command_id,
                exc.code,
                exc.message,
            )
            self._emit_command_result(
                command_id,
                final_status="failed",
                summary=exc.message,
                structured_payload={
                    "kind": "session_action_error",
                    "command_id": command_id,
                    "message": exc.message,
                },
                effective_execution=self._effective_execution(command_id),
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception as exc:
            LOGGER.exception("pc-control session-action execution failed command_id=%s", command_id)
            error_text = f"{type(exc).__name__}: {exc}"
            self._emit_command_result(
                command_id,
                final_status="failed",
                summary=error_text,
                structured_payload={
                    "kind": "session_action_error",
                    "command_id": command_id,
                    "message": error_text,
                },
                effective_execution=self._effective_execution(command_id),
                error_code="session_action_execution_failed",
                error_message=error_text,
            )
        finally:
            with self._control_lock:
                self._launched_command_ids.discard(command_id)
                self._command_contexts.pop(command_id, None)

    def _dispatch_session_action_command(self, command: _SessionActionCommand) -> dict[str, Any]:
        task_root = self._runner_task_root()
        if task_root is None:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "runner task_root is unavailable for current-session action handling",
            )
        target_state = self._resolve_session_action_target_state(command, task_root=task_root)
        recipient_addr = self._resolve_session_action_recipient(task_root=task_root, state=target_state)
        if recipient_addr is None:
            raise RelayDirectActionError(
                "session_recipient_unresolved",
                "could not resolve a durable canonical reply recipient for the requested session action",
            )
        if command.action_type == "status":
            handler = RelayTaskMailDirectCurrentSessionStatusHandler(
                config=self._config,
                task_root=task_root,
                mail_client=self._mail_client,
                runner=self._runner,
                recipient_addr=recipient_addr,
                background=True,
            )
            result = handler.handle_accepted_packet(
                AcceptedRelayPacket(
                    packet_id=command.packet_id,
                    receipt_id=command.receipt_id,
                    connection_id=self._runner_id,
                    client_id="pc_control",
                    client_trace_id=command.request_id,
                    received_at=self._clock(),
                    task_run_packet=command.task_run_packet,
                    dispatch_metadata=command.dispatch_metadata,
                )
            )
            server_messages = result.server_messages if isinstance(result, RelayDirectActionResult) else []
        elif command.action_type == "reply":
            handler = RelayTaskMailDirectCurrentSessionReplyHandler(
                config=self._config,
                task_root=task_root,
                mail_client=self._mail_client,
                runner=self._runner,
                recipient_addr=recipient_addr,
                background=True,
            )
            result = handler.handle_accepted_packet(
                AcceptedRelayPacket(
                    packet_id=command.packet_id,
                    receipt_id=command.receipt_id,
                    connection_id=self._runner_id,
                    client_id="pc_control",
                    client_trace_id=command.request_id,
                    received_at=self._clock(),
                    task_run_packet=command.task_run_packet,
                    dispatch_metadata=command.dispatch_metadata,
                )
            )
            server_messages = result.server_messages if isinstance(result, RelayDirectActionResult) else []
        else:
            self._dispatch_session_action_via_existing_thread_mail(
                command,
                task_root=task_root,
                target_state=target_state,
                recipient_addr=recipient_addr,
            )
            server_messages = []
        return self._structured_session_action_payload(
            command,
            task_root=task_root,
            target_state=target_state,
            server_messages=server_messages,
        )

    def _dispatch_session_action_via_existing_thread_mail(
        self,
        command: _SessionActionCommand,
        *,
        task_root: str,
        target_state: ThreadState,
        recipient_addr: str,
    ) -> None:
        from .app import _process_existing_thread_mail

        bot_addr = self._session_action_bot_mailbox_addr()
        if bot_addr is None:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                "bot mailbox address is not configured for current-session action handling",
            )
        if command.action_type not in {"pause", "resume", "kill"}:
            if command.action_type not in {"end", "answers", "attachment_continuation"}:
                raise ValueError(f"unsupported local current-session action: {command.action_type}")

        target_session_identity = build_target_session_identity(
            workspace_id=target_state.workspace_id,
            session_id=target_state.session_id or target_state.thread_id,
            thread_id=target_state.thread_id,
        )
        subject_text = _subject_text_for_target_state(target_state)
        reply_to, references = _build_thread_reply_chain(target_state)
        subject = f"Re: [S:{command.session_id}] {subject_text}".strip()
        envelope = MailEnvelope(
            message_id=_build_direct_message_id(command.packet_id),
            subject=subject,
            from_addr=recipient_addr,
            to_addr=bot_addr,
            date=self._clock(),
            in_reply_to=reply_to,
            references=references,
            body_text=self._build_session_action_mail_body(command, target_state=target_state),
            attachments=self._build_session_action_mail_attachments(command),
            raw_headers={
                "Subject": subject,
                **_build_post_creation_headers(
                    packet_id=command.packet_id,
                    receipt_id=command.receipt_id,
                    request_id=command.request_id,
                    action_type=command.action_type,
                    target_session_identity=target_session_identity,
                ),
            },
        )
        subject_info = parse_subject(envelope.subject)
        handled = _process_existing_thread_mail(
            envelope,
            subject_info,
            {
                "workspace_id": target_state.workspace_id,
                "session_id": target_state.session_id,
                "thread_id": target_state.thread_id,
            },
            self._config,
            Path(task_root),
            self._mail_client,
            self._runner,
            background=True,
        )
        if not handled:
            raise RelayDirectActionError(
                "direct_temporarily_unavailable",
                f"direct TaskMail current-session {command.action_type} could not be processed",
            )

    def _build_session_action_mail_attachments(self, command: _SessionActionCommand) -> list[MailAttachment]:
        if command.action_type != "attachment_continuation":
            return []
        attachment_payload = dict(command.task_run_packet.get("attachment_continuation") or {})
        attachments = attachment_payload.get("attachments") or []
        return [
            self._build_command_mail_attachment(item=dict(item), index=index)
            for index, item in enumerate(attachments, start=1)
            if isinstance(item, dict)
        ]

    def _validate_answers_target_state(
        self,
        command: _SessionActionCommand,
        *,
        target_state: ThreadState,
    ) -> None:
        pending_questions = effective_pending_questions(target_state, fallback_task_id=target_state.current_task_id)
        if not pending_questions:
            raise RelayDirectActionError(
                "validation_failed",
                "direct answers action requires the target session to have a pending question set",
            )
        if target_state.status not in {RUN_STATUS_AWAITING_USER_INPUT, RUN_STATUS_PAUSED}:
            raise RelayDirectActionError(
                "validation_failed",
                "direct answers action is only available while the session is awaiting user input or paused",
            )
        answer_items = list(dict(command.task_run_packet.get("answers") or {}).get("question_answers") or [])
        pending_questions_by_id = {item.question_id: item for item in pending_questions}
        matched = 0
        for item in answer_items:
            question_id = str(item.get("question_id") or "").strip()
            answer_value = str(item.get("value") or "").strip()
            question = pending_questions_by_id.get(question_id)
            if question is None:
                raise RelayDirectActionError(
                    "validation_failed",
                    f"direct answers action includes an unknown pending question id: {question_id}",
                )
            if question.choices and answer_value not in question.choices:
                raise RelayDirectActionError(
                    "validation_failed",
                    f"direct answers action must use the canonical choice value for question_id={question_id}",
                )
            matched += 1
        if matched == 0:
            raise RelayDirectActionError(
                "validation_failed",
                "direct answers action must include at least one matching question answer",
            )

    def _build_session_action_mail_body(
        self,
        command: _SessionActionCommand,
        *,
        target_state: ThreadState,
    ) -> str:
        if command.action_type in _EMPTY_BODY_SESSION_ACTION_TYPES:
            return f"/{command.action_type}\n"
        if command.action_type == "attachment_continuation":
            attachment_payload = dict(command.task_run_packet.get("attachment_continuation") or {})
            reply_text = str(attachment_payload.get("reply_text") or "").strip()
            return f"{reply_text.rstrip()}\n" if reply_text else ""
        if command.action_type != "answers":
            raise ValueError(f"unsupported local current-session body action: {command.action_type}")

        pending_questions = effective_pending_questions(target_state, fallback_task_id=target_state.current_task_id)
        pending_questions_by_id = {item.question_id: item for item in pending_questions}
        answer_map = {
            str(item.get("question_id") or "").strip(): str(item.get("value") or "").strip()
            for item in list(dict(command.task_run_packet.get("answers") or {}).get("question_answers") or [])
        }
        rendered_answers: list[str] = []
        for question in pending_questions:
            answer_value = answer_map.get(question.question_id)
            if answer_value is None:
                continue
            if len(pending_questions) > 1 and ("\n" in answer_value or "\r" in answer_value):
                raise RelayDirectActionError(
                    "validation_failed",
                    f"direct answers action must keep multi-question answer values single-line: {question.question_id}",
                )
            rendered_answers.append(f"{question.question_id}: {answer_value}")
        if not rendered_answers:
            raise RelayDirectActionError(
                "validation_failed",
                "direct answers action must include at least one matching question answer",
            )
        if len(pending_questions) == 1:
            single_answer_value = answer_map.get(pending_questions[0].question_id)
            if single_answer_value is None:
                raise RelayDirectActionError(
                    "validation_failed",
                    "direct answers action must include the current pending question answer",
                )
            body = single_answer_value
        else:
            body = "Answers:\n" + "\n".join(rendered_answers)
        if target_state.status == RUN_STATUS_PAUSED:
            body = f"/resume\n{body}"
        return body.rstrip() + "\n"

    def _structured_session_action_payload(
        self,
        command: _SessionActionCommand,
        *,
        task_root: str,
        target_state: ThreadState,
        server_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if server_messages:
            payload = server_messages[0].get("payload")
            if isinstance(payload, dict) and payload:
                return {
                    "kind": CONTROL_SESSION_ACTION_RESULT_TYPE,
                    **dict(payload),
                }

        updated_state = load_thread_state(target_state.thread_id, task_root)
        closeout_payload = load_session_action_closeout(
            task_root,
            thread_id=updated_state.thread_id,
            request_id=command.request_id,
        ) or {
            "action_type": command.action_type,
            "target_session_identity": build_target_session_identity(
                workspace_id=updated_state.workspace_id,
                session_id=updated_state.session_id or updated_state.thread_id,
                thread_id=updated_state.thread_id,
            ),
            "ingress_type": "direct_bridge",
            "request_id": command.request_id,
            "ingress_message_id": None,
            "packet_id": command.packet_id,
            "receipt_id": command.receipt_id,
            "last_summary": updated_state.last_summary,
            "terminal_mail_message_id": None,
            "terminal_mail_subject": None,
        }
        return {
            "kind": CONTROL_SESSION_ACTION_RESULT_TYPE,
            "session_action_result": {
                "action_type": command.action_type,
                "result_scope": "mail_ingress_submission",
                "canonical_outcome_via": "mail",
                "delivery_status": "submitted",
                "submitted_at": self._clock(),
                "transport_message_id": None,
                "session_action_closeout": closeout_payload,
            },
        }

    def _build_task_snapshot(self, message: PcCommandDispatchMessage) -> TaskSnapshot:
        workspace = self._workspace_by_id(message.payload["workspace_id"])
        if workspace is None:
            raise ValueError("workspace_id is not currently available on this PC")
        if str(message.payload["command_type"] or "").strip().lower() != "new_task":
            raise ValueError(f"unsupported command_type: {message.payload['command_type']}")
        policy = dict(message.payload["execution_policy"])
        command_payload = dict(message.payload["payload"])
        backend = str(policy.get("backend") or "").strip().lower()
        task_text = str(command_payload.get("task_text") or "").strip()
        if not task_text:
            raise ValueError("new_task requires payload.task_text")
        acceptance_raw = command_payload.get("acceptance") or []
        if not isinstance(acceptance_raw, list):
            raise ValueError("payload.acceptance must be a list[str] when provided")
        acceptance = [str(item).strip() for item in acceptance_raw if str(item).strip()]
        attachments = self._materialize_command_attachments(
            command_id=str(message.payload["command_id"]),
            repo_path=str(workspace.get("repo_path") or "").strip(),
            workdir=(str(workspace.get("workdir") or "").strip() or None),
            attachments_raw=command_payload.get("attachments") or [],
        )
        timeout_minutes = int(command_payload.get("timeout_minutes") or self._config.default_timeout_minutes)
        mode = str(command_payload.get("mode") or "modify").strip() or "modify"
        now = self._clock()
        command_id = message.payload["command_id"]
        session_id = str(message.payload.get("session_id") or "").strip() or command_id
        canonical_reply_recipient_raw = command_payload.get("canonical_reply_recipient")
        if canonical_reply_recipient_raw is None:
            canonical_reply_recipient = None
        elif not isinstance(canonical_reply_recipient_raw, str) or not canonical_reply_recipient_raw.strip():
            raise ValueError("payload.canonical_reply_recipient must be a non-empty string when provided")
        else:
            canonical_reply_recipient = canonical_reply_recipient_raw.strip()
        return TaskSnapshot(
            task_id=self._sanitize_identifier(command_id, prefix="task"),
            thread_id=self._sanitize_identifier(session_id, prefix="thread"),
            backend=backend,
            profile=(str(policy.get("profile") or "").strip() or None),
            permission=(str(policy.get("permission") or "").strip() or None),
            repo_path=str(workspace.get("repo_path") or "").strip(),
            workdir=(str(workspace.get("workdir") or "").strip() or None),
            task_text=task_text,
            acceptance=acceptance,
            timeout_minutes=timeout_minutes,
            mode=mode,
            attachments=attachments,
            created_at=now,
            updated_at=now,
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport=(
                str(policy.get("backend_transport") or "").strip()
                or self._config.default_transport_for_backend(backend)
            ),
            canonical_reply_recipient=canonical_reply_recipient,
        )

    def _materialize_command_attachments(
        self,
        *,
        command_id: str,
        repo_path: str,
        workdir: str | None,
        attachments_raw: Any,
    ) -> list[str]:
        if not isinstance(attachments_raw, list):
            raise ValueError("payload.attachments must be a list when provided")
        normalized_paths = [str(item).strip() for item in attachments_raw if isinstance(item, str) and str(item).strip()]
        inline_attachments = [
            self._build_command_mail_attachment(item=item, index=index)
            for index, item in enumerate(attachments_raw, start=1)
            if isinstance(item, dict)
        ]
        if not inline_attachments:
            return normalized_paths

        envelope = MailEnvelope(
            message_id=f"<pc-control-{self._sanitize_identifier(command_id, prefix='attachment')}@local>",
            subject=f"pc-control attachments for {command_id}",
            from_addr="pc-control@local",
            to_addr="runner@local",
            date=self._clock(),
            body_text="pc-control inline attachments",
            attachments=inline_attachments,
        )
        materialized = materialize_incoming_attachments(
            envelope,
            repo_path=repo_path,
            workdir=workdir,
            auto_create_workdir=False,
            filename_prefix=_CONTROL_PLANE_ATTACHMENT_PREFIX,
        )
        materialized_paths = [
            str(attachment.saved_path).strip()
            for attachment in materialized.attachments
            if str(attachment.saved_path or "").strip()
        ]
        if len(materialized_paths) != len(inline_attachments):
            raise ValueError("failed to materialize all control-plane attachments")
        return normalized_paths + materialized_paths

    def _build_command_mail_attachment(
        self,
        *,
        item: dict[str, Any],
        index: int,
    ) -> MailAttachment:
        name = str(item.get("name") or "").strip()
        content_type = str(item.get("content_type") or "").strip()
        encoded_bytes = str(item.get("content_bytes_b64") or "").strip()
        try:
            content_bytes = base64.b64decode(encoded_bytes, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"payload.attachments[{index - 1}].content_bytes_b64 is invalid base64") from exc

        declared_size = item.get("size_bytes")
        size_bytes = declared_size if isinstance(declared_size, int) and declared_size >= 0 else len(content_bytes)
        return MailAttachment(
            filename=name,
            content_type=content_type,
            size_bytes=size_bytes,
            content_bytes=content_bytes,
        )

    def _workspace_by_id(self, workspace_id: str) -> dict[str, Any] | None:
        normalized_workspace_id = str(workspace_id or "").strip()
        for item in self._workspace_provider():
            if str(item.get("workspace_id") or "").strip() == normalized_workspace_id:
                return dict(item)
        return None

    def _sanitize_identifier(self, value: str, *, prefix: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(value or "").strip())
        cleaned = cleaned.strip("._-")
        if not cleaned:
            cleaned = secrets.token_hex(4)
        return cleaned if cleaned.startswith(prefix) else f"{prefix}_{cleaned}"

    async def _handle_output_resume_request(
        self,
        websocket,
        *,
        message: PcOutputResumeRequestMessage,
        connection_epoch: int,
        send_lock: asyncio.Lock,
    ) -> None:
        if message.pc_id != self._pc_id or message.connection_epoch != connection_epoch:
            LOGGER.warning(
                "Ignoring output_resume_request for mismatched routing pc_id=%s epoch=%s",
                message.pc_id,
                message.connection_epoch,
            )
            return
        replayed_count = await self._replay_output_chunks_for_request(
            websocket,
            send_lock=send_lock,
            command_id=message.payload["command_id"],
            stream_id=message.payload["stream_id"],
            after_seq=message.payload["after_seq"],
        )
        LOGGER.debug(
            "Processed output_resume_request command_id=%s after_seq=%s replayed=%s",
            message.payload["command_id"],
            message.payload["after_seq"],
            replayed_count,
        )

    def _on_runner_running(self, command_id: str, state: ThreadState) -> None:
        self._emit_command_event(
            command_id,
            event_type="running",
            summary="command is running on the local runner",
            event_payload={
                "thread_id": state.thread_id,
                "task_id": state.current_task_id,
                "workspace_id": state.workspace_id,
            },
            effective_execution=self._effective_execution(command_id),
        )

    def _on_runner_finished(self, command_id: str, state: ThreadState, result: RunResult) -> None:
        final_status = self._canonical_final_status(result)
        summary = str(state.last_summary or result.error_message or final_status).strip() or final_status
        structured_payload = self._structured_result_payload(state, result)
        try:
            self._emit_output_chunks(command_id, result=result)
        except Exception:
            LOGGER.warning("Unable to emit pc-control output chunks for command_id=%s", command_id, exc_info=True)
        try:
            self._emit_artifact_manifest(command_id, result=result)
        except Exception:
            LOGGER.warning("Unable to emit pc-control artifact manifest for command_id=%s", command_id, exc_info=True)
        self._emit_command_event(
            command_id,
            event_type=final_status,
            summary=summary,
            event_payload={
                "thread_id": state.thread_id,
                "task_id": result.task_id,
            },
            effective_execution=self._effective_execution(command_id, result=result),
        )
        self._emit_command_result(
            command_id,
            final_status=final_status,
            summary=summary,
            structured_payload=structured_payload,
            effective_execution=self._effective_execution(command_id, result=result),
            error_code=(str(result.error_type or "").strip() or None),
            error_message=result.error_message,
        )
        with self._control_lock:
            self._launched_command_ids.discard(command_id)
            self._command_contexts.pop(command_id, None)

    def _emit_output_chunks(self, command_id: str, *, result: RunResult) -> None:
        context = self._command_context(command_id)
        if context is None:
            return
        task_root = self._runner_task_root()
        if task_root is None:
            return
        output_chunks = project_output_chunks(task_root, thread_id=result.thread_id, task_id=result.task_id)
        if not output_chunks:
            return
        self._remember_output_chunk_replay_context(
            command_id,
            trace_id=str(context["trace_id"]),
            thread_id=result.thread_id,
            task_id=result.task_id,
        )
        for chunk in output_chunks:
            payload = self._build_output_chunk_payload(
                command_id,
                trace_id=str(context["trace_id"]),
                connection_epoch=(self._current_connection_epoch or context["connection_epoch"]),
                chunk=chunk,
            )
            self._queue_client_payload(payload)

    def _emit_artifact_manifest(self, command_id: str, *, result: RunResult) -> None:
        context = self._command_context(command_id)
        if context is None:
            return
        task_root = self._runner_task_root()
        if task_root is None:
            return
        manifest = project_artifact_manifest(task_root, result=result)
        if manifest is None:
            return
        payload = build_artifact_manifest(
            message_id=self._next_message_id("artifact_manifest"),
            trace_id=context["trace_id"],
            pc_id=self._pc_id,
            connection_epoch=self._current_connection_epoch or context["connection_epoch"],
            sent_at=self._clock(),
            manifest_id=f"artifact_manifest:{command_id}",
            command_id=command_id,
            artifacts=list(manifest["artifacts"]),
            artifacts_root=(str(manifest["artifacts_root"]) if manifest.get("artifacts_root") else None),
            source=(str(manifest["source"]) if manifest.get("source") else None),
        )
        self._queue_client_payload(payload)

    def _emit_command_event(
        self,
        command_id: str,
        *,
        event_type: str,
        summary: str | None,
        event_payload: dict[str, Any] | None = None,
        effective_execution: dict[str, Any] | None = None,
    ) -> None:
        context = self._command_context(command_id)
        if context is None:
            return
        payload = build_command_event(
            message_id=self._next_message_id("event"),
            trace_id=context["trace_id"],
            pc_id=self._pc_id,
            connection_epoch=self._current_connection_epoch or context["connection_epoch"],
            sent_at=self._clock(),
            event_id=f"event:{command_id}:{event_type}",
            command_id=command_id,
            event_type=event_type,
            summary=summary,
            effective_execution=effective_execution,
            event_payload=event_payload,
        )
        self._queue_client_payload(payload)

    def _emit_command_result(
        self,
        command_id: str,
        *,
        final_status: str,
        summary: str,
        structured_payload: dict[str, Any],
        effective_execution: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        context = self._command_context(command_id)
        if context is None:
            return
        payload = build_command_result(
            message_id=self._next_message_id("result"),
            trace_id=context["trace_id"],
            pc_id=self._pc_id,
            connection_epoch=self._current_connection_epoch or context["connection_epoch"],
            sent_at=self._clock(),
            result_id=f"result:{command_id}",
            command_id=command_id,
            final_status=final_status,
            summary=summary,
            structured_payload=structured_payload,
            effective_execution=effective_execution or self._effective_execution(command_id),
            error_code=error_code,
            error_message=error_message,
        )
        self._queue_client_payload(payload)

    def _command_context(self, command_id: str) -> dict[str, Any] | None:
        with self._control_lock:
            context = self._command_contexts.get(command_id)
        return None if context is None else dict(context)

    def _effective_execution(self, command_id: str, *, result: RunResult | None = None) -> dict[str, Any]:
        context = self._command_context(command_id) or {}
        snapshot: TaskSnapshot | None = context.get("snapshot")
        if snapshot is None:
            return {
                "backend": None,
                "profile": None,
                "permission": None,
                "backend_transport": None,
                "resolved_model": None,
            }
        backend = snapshot.backend
        profile = snapshot.profile
        backend_transport = result.backend_transport if result is not None else snapshot.backend_transport
        return {
            "backend": backend,
            "profile": profile,
            "permission": snapshot.permission,
            "backend_transport": backend_transport,
            "resolved_model": self._resolve_profile_model(backend, profile or ""),
        }

    def _canonical_final_status(self, result: RunResult) -> str:
        if result.status == RUN_STATUS_SUCCESS:
            return "done"
        if result.status == RUN_STATUS_AWAITING_USER_INPUT:
            return "awaiting_user_input"
        if result.status == RUN_STATUS_PAUSED:
            return "paused"
        if result.status == RUN_STATUS_KILLED:
            return "killed"
        if result.status == RUN_STATUS_FAILED:
            return "failed"
        return "failed"

    def _structured_result_payload(self, state: ThreadState, result: RunResult) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": "run_result",
            "task_id": result.task_id,
            "thread_id": result.thread_id,
            "run_status": result.status,
            "stdout_file": result.stdout_file,
            "stderr_file": result.stderr_file,
            "summary_file": result.summary_file,
            "artifacts_dir": result.artifacts_dir,
            "changed_files": list(result.changed_files),
            "tests_passed": result.tests_passed,
            "backend_session_id": result.backend_session_id,
            "backend_session_resumable": result.backend_session_resumable,
            "thread_status": state.status,
        }
        if result.error_type:
            payload["error_type"] = result.error_type
        if result.error_message:
            payload["error_message"] = result.error_message
        if result.question_id:
            payload["question_id"] = result.question_id
        if result.question_text:
            payload["question_text"] = result.question_text
        if result.question_set_id:
            payload["question_set_id"] = result.question_set_id
        if result.pending_questions:
            payload["pending_question_ids"] = [item.question_id for item in result.pending_questions]
        runner_workspace = getattr(self._runner, "workspace", None)
        if runner_workspace is not None:
            try:
                payload["result_file"] = runner_workspace.to_thread_relative(
                    result.thread_id,
                    runner_workspace.run_file_path(result.thread_id, result.task_id, "result.json"),
                )
            except Exception:
                LOGGER.debug("Unable to derive result_file for pc-control result", exc_info=True)
        return payload

    def _runner_task_root(self) -> str | None:
        runner_workspace = getattr(self._runner, "workspace", None)
        task_root = getattr(runner_workspace, "task_root", None)
        return str(task_root) if task_root is not None else None

    def _remember_output_chunk_replay_context(
        self,
        command_id: str,
        *,
        trace_id: str,
        thread_id: str,
        task_id: str,
    ) -> None:
        with self._control_lock:
            self._output_chunk_replay_contexts.pop(command_id, None)
            self._output_chunk_replay_contexts[command_id] = {
                "trace_id": str(trace_id),
                "thread_id": str(thread_id),
                "task_id": str(task_id),
                "needs_replay": False,
            }
            while len(self._output_chunk_replay_contexts) > 32:
                oldest_command_id = next(iter(self._output_chunk_replay_contexts))
                self._output_chunk_replay_contexts.pop(oldest_command_id, None)

    def _mark_output_chunk_replay_needed(self) -> None:
        with self._control_lock:
            for context in self._output_chunk_replay_contexts.values():
                context["needs_replay"] = True

    async def _replay_output_chunks_after_reconnect(self, websocket, send_lock: asyncio.Lock) -> None:
        task_root = self._runner_task_root()
        if task_root is None:
            return
        with self._control_lock:
            replay_items = [
                (command_id, dict(context))
                for command_id, context in self._output_chunk_replay_contexts.items()
                if bool(context.get("needs_replay"))
            ]
        if not replay_items:
            return
        for command_id, context in replay_items:
            replayed = False
            for chunk in project_output_chunks(
                task_root,
                thread_id=str(context["thread_id"]),
                task_id=str(context["task_id"]),
            ):
                replayed = True
                await self._send_payload_with_requeue(
                    websocket,
                    self._build_output_chunk_payload(
                        command_id,
                        trace_id=str(context["trace_id"]),
                        connection_epoch=(self._current_connection_epoch or 1),
                        chunk=chunk,
                    ),
                    send_lock,
                )
            if replayed:
                with self._control_lock:
                    stored = self._output_chunk_replay_contexts.get(command_id)
                    if stored is not None:
                        stored["needs_replay"] = False

    async def _replay_output_chunks_for_request(
        self,
        websocket,
        *,
        send_lock: asyncio.Lock,
        command_id: str,
        stream_id: str | None,
        after_seq: int,
    ) -> int:
        task_root = self._runner_task_root()
        if task_root is None:
            return 0
        context = self._output_chunk_replay_context(command_id)
        if context is None:
            return 0
        replayed_count = 0
        for chunk in project_output_chunks(
            task_root,
            thread_id=str(context["thread_id"]),
            task_id=str(context["task_id"]),
        ):
            if stream_id is not None and str(chunk["stream_id"]) != stream_id:
                continue
            if int(chunk["seq"]) <= after_seq:
                continue
            replayed_count += 1
            await self._send_payload_with_requeue(
                websocket,
                self._build_output_chunk_payload(
                    command_id,
                    trace_id=str(context["trace_id"]),
                    connection_epoch=(self._current_connection_epoch or 1),
                    chunk=chunk,
                ),
                send_lock,
            )
        return replayed_count

    def _output_chunk_replay_context(self, command_id: str) -> dict[str, Any] | None:
        with self._control_lock:
            context = self._output_chunk_replay_contexts.get(command_id)
            if context is not None:
                return dict(context)
            command_context = self._command_contexts.get(command_id)
            snapshot = None if command_context is None else command_context.get("snapshot")
            if snapshot is None:
                return None
            derived = {
                "trace_id": str(command_context["trace_id"]),
                "thread_id": str(snapshot.thread_id),
                "task_id": str(snapshot.task_id),
                "needs_replay": False,
            }
            self._output_chunk_replay_contexts[command_id] = dict(derived)
            return derived

    def _build_output_chunk_payload(
        self,
        command_id: str,
        *,
        trace_id: str,
        connection_epoch: int,
        chunk: dict[str, Any],
    ) -> dict[str, Any]:
        return build_output_chunk(
            message_id=self._next_message_id("output_chunk"),
            trace_id=trace_id,
            pc_id=self._pc_id,
            connection_epoch=connection_epoch,
            sent_at=self._clock(),
            output_chunk_id=f"output:{command_id}:{chunk['stream_id']}:{chunk['seq']}",
            command_id=command_id,
            stream_id=str(chunk["stream_id"]),
            stream_id_source=(str(chunk["stream_id_source"]) if chunk.get("stream_id_source") else None),
            seq=int(chunk["seq"]),
            kind=str(chunk["kind"]),
            text=(str(chunk["text"]) if chunk.get("text") else None),
            delta=(str(chunk["delta"]) if chunk.get("delta") else None),
            item_type=(str(chunk["item_type"]) if chunk.get("item_type") else None),
            status=(str(chunk["status"]) if chunk.get("status") else None),
        )

    def _queue_client_payload(self, payload: dict[str, Any]) -> None:
        with self._control_lock:
            websocket = self._websocket
            loop = self._loop
            send_lock = self._send_lock
            if websocket is None or loop is None or send_lock is None:
                self._pending_client_messages.append(dict(payload))
                return
        self._schedule_payload_send(dict(payload), websocket=websocket, send_lock=send_lock, loop=loop)

    async def _flush_pending_client_messages(self, websocket, send_lock: asyncio.Lock) -> None:
        with self._control_lock:
            pending = [dict(item) for item in self._pending_client_messages]
            self._pending_client_messages = []
        for payload in pending:
            await self._send_payload_with_requeue(websocket, payload, send_lock)

    def _schedule_payload_send(self, payload: dict[str, Any], *, websocket, send_lock: asyncio.Lock, loop) -> None:
        coroutine = self._send_payload_with_requeue(websocket, payload, send_lock)
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            asyncio.create_task(coroutine)
            return
        asyncio.run_coroutine_threadsafe(coroutine, loop)

    async def _send_payload_with_requeue(self, websocket, payload: dict[str, Any], send_lock: asyncio.Lock) -> None:
        normalized_payload = self._rewrite_payload_for_current_connection(payload)
        try:
            await self._send_payload(websocket, normalized_payload, send_lock)
        except Exception:
            LOGGER.warning("pc-control payload send failed; queueing for retry", exc_info=True)
            with self._control_lock:
                self._pending_client_messages.append(dict(payload))

    def _rewrite_payload_for_current_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        with self._control_lock:
            current_epoch = self._current_connection_epoch
        if current_epoch is not None:
            normalized["connection_epoch"] = current_epoch
            normalized["sent_at"] = self._clock()
        normalized["pc_id"] = self._pc_id
        return normalized

    def _runner_count(self, method_name: str) -> int:
        if self._runner is None:
            return 0
        method = getattr(self._runner, method_name, None)
        if callable(method):
            try:
                value = method()
            except Exception:
                LOGGER.debug("runner.%s() failed during pc-control admission", method_name, exc_info=True)
                return 0
            if isinstance(value, int) and value >= 0:
                return value
        return 0

    async def _send_payload(self, websocket, payload: dict[str, Any], send_lock: asyncio.Lock) -> None:
        async with send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        if self._pc_control_url.startswith("ws://"):
            return None
        context = ssl.create_default_context()
        if not self._verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif self._ca_file:
            context.load_verify_locations(self._ca_file)
        return context

    @staticmethod
    def _parse_server_frame(
        payload: dict[str, Any],
    ) -> (
        PcHelloAckMessage
        | PcErrorMessage
        | PcCommandDispatchMessage
        | PcOutputResumeRequestMessage
        | PcMailboxLeaseAckMessage
        | PcIngressDecisionMessage
        | PcThreadBindingAckMessage
        | PcTerminalOutcomeAckMessage
    ):
        try:
            parsed = parse_pc_control_server_message(payload)
        except PcControlProtocolError as exc:
            raise RuntimeError(str(exc)) from exc
        if isinstance(
            parsed,
            (
                PcHelloAckMessage,
                PcErrorMessage,
                PcCommandDispatchMessage,
                PcOutputResumeRequestMessage,
                PcMailboxLeaseAckMessage,
                PcIngressDecisionMessage,
                PcThreadBindingAckMessage,
                PcTerminalOutcomeAckMessage,
            ),
        ):
            return parsed
        raise RuntimeError("unsupported pc-control server frame")

    def _next_message_id(self, prefix: str) -> str:
        return f"{prefix}:{secrets.token_hex(4)}"

    def _next_trace_id(self, prefix: str) -> str:
        return f"trace:{prefix}:{self._pc_id}:{secrets.token_hex(4)}"

    def _next_request_id(self, prefix: str) -> str:
        return f"request:{prefix}:{self._pc_id}:{secrets.token_hex(4)}"

    def _next_snapshot_id(self) -> str:
        return f"snapshot:{self._pc_id}:{secrets.token_hex(4)}"


def build_pc_control_plane_client(
    config: AppConfig,
    *,
    runner=None,
    heartbeat_interval_seconds: int = 15,
    snapshot_interval_seconds: int = 60,
) -> PcControlPlaneClient | None:
    if not config.pc_control_sidecar_enabled:
        return None
    relay_url = str(config.relay_url or "").strip()
    transport_token = str(config.relay_transport_token or "").strip()
    pc_id = str(config.relay_client_id or "").strip()
    client_version = str(config.relay_client_version or "").strip()
    if not relay_url or not transport_token or not pc_id or not client_version:
        return None
    return PcControlPlaneClient(
        relay_url=relay_url,
        transport_token=transport_token,
        pc_id=pc_id,
        client_version=client_version,
        display_name=pc_id,
        config=config,
        runner=runner,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        snapshot_interval_seconds=snapshot_interval_seconds,
        verify_tls=config.relay_verify_tls,
        ca_file=config.relay_ca_file or None,
    )


def _direct_websocket_connect_kwargs() -> dict[str, object]:
    if _WEBSOCKETS_CONNECT_SUPPORTS_PROXY:
        return {"proxy": None}
    return {}
