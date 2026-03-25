"""PC-side control-plane sidecar for the VPS-first protocol."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from datetime import datetime
from typing import Any, Callable

import websockets

from .config import AppConfig
from .models import RunResult, TaskSnapshot, ThreadState
from .pc_control_plane_projection import project_artifact_manifest, project_output_chunks
from .pc_workspace_inventory import build_execution_capabilities, collect_workspace_inventory
from .relay_server.auth import token_fingerprint
from .relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcControlProtocolError,
    PcErrorMessage,
    PcHelloAckMessage,
    PcOutputResumeRequestMessage,
    build_artifact_manifest,
    build_command_ack,
    build_command_event,
    build_command_result,
    build_output_chunk,
    build_heartbeat,
    build_pc_hello,
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

LOGGER = logging.getLogger(__name__)
_WEBSOCKETS_CONNECT_SUPPORTS_PROXY = "proxy" in inspect.signature(websockets.connect).parameters


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

    @property
    def is_configured(self) -> bool:
        return bool(self._pc_control_url and self._transport_token and self._pc_id and self._client_version)

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
        if self._loop is not None and self._websocket is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._websocket.close(), self._loop).result(timeout=timeout_seconds)
            except Exception:
                LOGGER.debug("pc-control websocket close skipped during shutdown", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, timeout_seconds))

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
            last_snapshot_at = self._monotonic()
            try:
                while not self._stop_event.is_set() and not receiver_task.done():
                    stop_requested = await asyncio.to_thread(self._stop_event.wait, self._heartbeat_interval_seconds)
                    if stop_requested:
                        break
                    await self._send_heartbeat(websocket, connection_epoch, send_lock)
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
                runtime_fingerprint=token_fingerprint(f"{self._pc_id}|{self._pc_control_url}"),
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

        return None, None

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
        attachments_raw = command_payload.get("attachments") or []
        if not isinstance(attachments_raw, list):
            raise ValueError("payload.attachments must be a list[str] when provided")
        attachments = [str(item).strip() for item in attachments_raw if str(item).strip()]
        timeout_minutes = int(command_payload.get("timeout_minutes") or self._config.default_timeout_minutes)
        mode = str(command_payload.get("mode") or "modify").strip() or "modify"
        now = self._clock()
        command_id = message.payload["command_id"]
        session_id = str(message.payload.get("session_id") or "").strip() or command_id
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
    ) -> PcHelloAckMessage | PcErrorMessage | PcCommandDispatchMessage | PcOutputResumeRequestMessage:
        try:
            parsed = parse_pc_control_server_message(payload)
        except PcControlProtocolError as exc:
            raise RuntimeError(str(exc)) from exc
        if isinstance(parsed, (PcHelloAckMessage, PcErrorMessage, PcCommandDispatchMessage, PcOutputResumeRequestMessage)):
            return parsed
        raise RuntimeError("unsupported pc-control server frame")

    def _next_message_id(self, prefix: str) -> str:
        return f"{prefix}:{secrets.token_hex(4)}"

    def _next_trace_id(self, prefix: str) -> str:
        return f"trace:{prefix}:{self._pc_id}:{secrets.token_hex(4)}"

    def _next_snapshot_id(self) -> str:
        return f"snapshot:{self._pc_id}:{secrets.token_hex(4)}"


def build_pc_control_plane_client(
    config: AppConfig,
    *,
    runner=None,
    heartbeat_interval_seconds: int = 15,
    snapshot_interval_seconds: int = 60,
) -> PcControlPlaneClient | None:
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
