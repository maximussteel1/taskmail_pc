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
from .pc_workspace_inventory import build_execution_capabilities, collect_workspace_inventory
from .relay_server.auth import token_fingerprint
from .relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcControlProtocolError,
    PcErrorMessage,
    PcHelloAckMessage,
    build_command_ack,
    build_heartbeat,
    build_pc_hello,
    build_workspace_snapshot,
    parse_pc_control_server_message,
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
        self._command_ack_cache: dict[str, dict[str, Any]] = {}

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
            connection_epoch = await self._perform_hello(websocket, send_lock)
            await self._send_workspace_snapshot(websocket, connection_epoch, send_lock)
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
                self._websocket = None

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
    def _parse_server_frame(payload: dict[str, Any]) -> PcHelloAckMessage | PcErrorMessage | PcCommandDispatchMessage:
        try:
            parsed = parse_pc_control_server_message(payload)
        except PcControlProtocolError as exc:
            raise RuntimeError(str(exc)) from exc
        if isinstance(parsed, (PcHelloAckMessage, PcErrorMessage, PcCommandDispatchMessage)):
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
