"""Phase 3 subscribe_session_detail helpers and thread-store-backed provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..config import load_config
from ..models import SessionState, ThreadState
from ..status import THREAD_STATUS_ACCEPTED, THREAD_STATUS_AWAITING_USER_INPUT, THREAD_STATUS_RUNNING
from ..thread_store import (
    build_workspace_id,
    load_session_state,
    load_thread_state,
    normalize_workspace_value,
)
from .phase3_emitter import build_phase3_session_snapshot_update
from .protocol import RelayPacketMessage

_PHASE3_SCHEMA_VERSION = "phase3-direct-inbound-wire-v1"
_DIRECT_CHANNEL = "taskmail_android_direct"
_ORIGIN_CLIENT = "android_taskmail"
_SUBSCRIBE_ACTION = "subscribe_session_detail"
_ALLOWED_REASONS = {"detail_open", "detail_refresh", "detail_reconnect"}


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class Phase3SubscriptionError(Exception):
    """Raised when a subscribe_session_detail packet is invalid or rejected."""

    def __init__(self, code: str, message: str, *, reject: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reject = reject


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase3SubscriptionError("invalid_payload", f"{field_name} must be a non-empty string", reject=False)
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Phase3SubscriptionError("invalid_payload", f"{field_name} must be a string when present", reject=False)
    text = value.strip()
    return text or None


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Phase3SubscriptionError("invalid_payload", f"{field_name} must be a dict", reject=False)
    return dict(value)


def _optional_non_negative_int(value: Any, field_name: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or value < 0:
        raise Phase3SubscriptionError("invalid_payload", f"{field_name} must be a non-negative integer", reject=False)
    return value


def _session_status_from_thread(thread_status: str, *, queued_task_id: str | None) -> str:
    if thread_status == THREAD_STATUS_ACCEPTED:
        return "queued"
    if thread_status == THREAD_STATUS_AWAITING_USER_INPUT:
        return "waiting_user"
    if queued_task_id and thread_status not in {THREAD_STATUS_RUNNING, THREAD_STATUS_AWAITING_USER_INPUT}:
        return "queued"
    return thread_status


def _build_session_state_from_thread(thread_state: ThreadState) -> SessionState:
    workspace_id = thread_state.workspace_id or build_workspace_id(thread_state.repo_path, thread_state.workdir)
    session_id = thread_state.session_id or thread_state.thread_id
    session_name = thread_state.session_name or thread_state.subject_norm
    session_norm = thread_state.session_norm or thread_state.subject_norm
    pending_task_count = 1 if thread_state.queued_task_id else 0
    return SessionState(
        session_id=session_id,
        workspace_id=workspace_id,
        thread_id=thread_state.thread_id,
        session_name=session_name,
        session_norm=session_norm,
        backend=thread_state.backend,
        profile=thread_state.profile,
        permission=thread_state.permission,
        repo_path=thread_state.repo_path,
        workdir=thread_state.workdir,
        status=_session_status_from_thread(thread_state.status, queued_task_id=thread_state.queued_task_id),
        current_task_id=thread_state.current_task_id,
        last_task_snapshot_file=thread_state.last_task_snapshot_file,
        queued_task_id=thread_state.queued_task_id,
        queued_snapshot_file=thread_state.queued_snapshot_file,
        pending_task_count=pending_task_count,
        history_files=list(thread_state.history_files),
        last_summary=thread_state.last_summary,
        lifecycle=thread_state.lifecycle,
        last_active_at=thread_state.last_active_at,
        last_progress_at=thread_state.last_progress_at,
        backend_session_id=thread_state.backend_session_id,
        backend_session_resumable=thread_state.backend_session_resumable,
        backend_transport=thread_state.backend_transport,
        created_at=thread_state.created_at,
        updated_at=thread_state.updated_at,
    )


def _canonical_workspace_id(session_state: SessionState, thread_state: ThreadState) -> str:
    if session_state.workspace_id:
        return session_state.workspace_id
    if thread_state.workspace_id:
        return thread_state.workspace_id
    return build_workspace_id(session_state.repo_path, session_state.workdir)


def _matches_workspace_locator(
    *,
    workspace_id: str,
    repo_path: str | None,
    workdir: str | None,
    session_state: SessionState,
    thread_state: ThreadState,
) -> None:
    canonical_workspace_id = _canonical_workspace_id(session_state, thread_state)
    if workspace_id and canonical_workspace_id != workspace_id:
        raise Phase3SubscriptionError(
            "workspace_identity_mismatch",
            "workspace_id does not match the canonical workspace for this session",
            reject=True,
        )
    if repo_path is None:
        return
    if normalize_workspace_value(repo_path) != normalize_workspace_value(session_state.repo_path):
        raise Phase3SubscriptionError(
            "workspace_identity_mismatch",
            "repo_path does not match the canonical workspace for this session",
            reject=True,
        )
    if workdir is not None and normalize_workspace_value(workdir) != normalize_workspace_value(session_state.workdir):
        raise Phase3SubscriptionError(
            "workspace_identity_mismatch",
            "workdir does not match the canonical workspace for this session",
            reject=True,
        )


@dataclass(slots=True)
class Phase3SubscribeSessionDetailRequest:
    request_id: str
    workspace_id: str | None
    repo_path: str | None
    workdir: str | None
    session_id: str | None
    thread_id: str | None
    last_known_sequence: int
    reason: str | None


@dataclass(slots=True)
class Phase3SubscribeSessionDetailResult:
    subscription_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    sequence: int
    session_update: dict[str, Any]


class Phase3SessionDetailProvider(Protocol):
    def resolve_session_detail(
        self,
        request: Phase3SubscribeSessionDetailRequest,
    ) -> tuple[SessionState, ThreadState]: ...

    def build_initial_snapshot(
        self,
        request: Phase3SubscribeSessionDetailRequest,
        *,
        subscription_id: str,
        sequence: int,
        sent_at: str,
    ) -> Phase3SubscribeSessionDetailResult: ...


def parse_phase3_subscribe_request(message: RelayPacketMessage) -> Phase3SubscribeSessionDetailRequest | None:
    task_run_packet = _require_mapping(message.task_run_packet, "task_run_packet")
    schema_version = _optional_text(task_run_packet.get("schema_version"), "task_run_packet.schema_version")
    if schema_version != _PHASE3_SCHEMA_VERSION:
        return None
    action = _require_text(task_run_packet.get("action"), "task_run_packet.action")
    if action != _SUBSCRIBE_ACTION:
        raise Phase3SubscriptionError("unsupported_action", "Phase 3 direct inbound action is not available", reject=False)

    request_id = _require_text(task_run_packet.get("request_id"), "task_run_packet.request_id")
    origin = _require_mapping(task_run_packet.get("origin"), "task_run_packet.origin")
    if _require_text(origin.get("client"), "task_run_packet.origin.client") != _ORIGIN_CLIENT:
        raise Phase3SubscriptionError(
            "invalid_payload",
            "task_run_packet.origin.client must equal android_taskmail",
            reject=False,
        )
    subscription = _require_mapping(task_run_packet.get("subscription"), "task_run_packet.subscription")
    workspace_id = _optional_text(subscription.get("workspace_id"), "subscription.workspace_id")
    repo_path = _optional_text(subscription.get("repo_path"), "subscription.repo_path")
    workdir = _optional_text(subscription.get("workdir"), "subscription.workdir")
    if workdir is not None and repo_path is None and workspace_id is None:
        raise Phase3SubscriptionError("invalid_payload", "subscription.workdir requires repo_path or workspace_id", reject=False)
    session_id = _optional_text(subscription.get("session_id"), "subscription.session_id")
    thread_id = _optional_text(subscription.get("thread_id"), "subscription.thread_id")
    if workspace_id is None and repo_path is None:
        raise Phase3SubscriptionError(
            "invalid_payload",
            "subscription must include workspace_id or repo_path",
            reject=False,
        )
    if session_id is None and thread_id is None:
        raise Phase3SubscriptionError(
            "invalid_payload",
            "subscription must include session_id or thread_id",
            reject=False,
        )
    last_known_sequence = _optional_non_negative_int(
        subscription.get("last_known_sequence"),
        "subscription.last_known_sequence",
    )
    reason = _optional_text(subscription.get("reason"), "subscription.reason")
    if reason is not None and reason not in _ALLOWED_REASONS:
        allowed = ", ".join(sorted(_ALLOWED_REASONS))
        raise Phase3SubscriptionError(
            "invalid_payload",
            f"subscription.reason must be one of: {allowed}",
            reject=False,
        )
    dispatch_metadata = _require_mapping(message.dispatch_metadata, "dispatch_metadata")
    channel = _optional_text(dispatch_metadata.get("channel"), "dispatch_metadata.channel")
    if channel is not None and channel != _DIRECT_CHANNEL:
        raise Phase3SubscriptionError(
            "invalid_payload",
            "dispatch_metadata.channel must equal taskmail_android_direct",
            reject=False,
        )
    metadata_schema = _optional_text(dispatch_metadata.get("schema_version"), "dispatch_metadata.schema_version")
    if metadata_schema is not None and metadata_schema != _PHASE3_SCHEMA_VERSION:
        raise Phase3SubscriptionError(
            "invalid_payload",
            "dispatch_metadata.schema_version must equal phase3-direct-inbound-wire-v1",
            reject=False,
        )
    metadata_action = _optional_text(dispatch_metadata.get("action"), "dispatch_metadata.action")
    if metadata_action is not None and metadata_action != _SUBSCRIBE_ACTION:
        raise Phase3SubscriptionError(
            "invalid_payload",
            "dispatch_metadata.action must equal subscribe_session_detail",
            reject=False,
        )
    return Phase3SubscribeSessionDetailRequest(
        request_id=request_id,
        workspace_id=workspace_id,
        repo_path=repo_path,
        workdir=workdir,
        session_id=session_id,
        thread_id=thread_id,
        last_known_sequence=last_known_sequence,
        reason=reason,
    )


class ThreadStorePhase3SessionDetailProvider:
    def __init__(self, *, task_root: str | Path | None = None) -> None:
        self._task_root = Path(task_root) if task_root is not None else None

    def resolve_session_detail(
        self,
        request: Phase3SubscribeSessionDetailRequest,
    ) -> tuple[SessionState, ThreadState]:
        return self._resolve_states(request)

    def build_initial_snapshot(
        self,
        request: Phase3SubscribeSessionDetailRequest,
        *,
        subscription_id: str,
        sequence: int,
        sent_at: str,
    ) -> Phase3SubscribeSessionDetailResult:
        session_state, thread_state = self.resolve_session_detail(request)
        update = build_phase3_session_snapshot_update(
            subscription_id=subscription_id,
            session_state=session_state,
            thread_state=thread_state,
            update_id=f"sessupd:{session_state.session_id}:{sequence}",
            sequence=sequence,
            sent_at=sent_at,
        )
        return Phase3SubscribeSessionDetailResult(
            subscription_id=subscription_id,
            workspace_id=session_state.workspace_id,
            session_id=session_state.session_id,
            thread_id=session_state.thread_id,
            sequence=sequence,
            session_update=update,
        )

    def _resolve_states(self, request: Phase3SubscribeSessionDetailRequest) -> tuple[SessionState, ThreadState]:
        resolved_task_root = self._resolved_task_root()
        canonical_workspace_id = self._resolve_workspace_id(request)

        session_state: SessionState | None = None
        thread_state: ThreadState | None = None

        if request.session_id is not None and canonical_workspace_id is not None:
            try:
                session_state = load_session_state(canonical_workspace_id, request.session_id, resolved_task_root)
            except FileNotFoundError:
                session_state = None

        if request.thread_id is not None:
            try:
                thread_state = load_thread_state(request.thread_id, resolved_task_root)
            except FileNotFoundError:
                thread_state = None

        if session_state is None and thread_state is None:
            raise Phase3SubscriptionError(
                "session_not_found",
                "could not resolve a session for the requested workspace/session locator",
                reject=True,
            )

        if thread_state is None and session_state is not None:
            try:
                thread_state = load_thread_state(session_state.thread_id, resolved_task_root)
            except FileNotFoundError as exc:
                raise Phase3SubscriptionError(
                    "session_not_found",
                    "session_state exists but backing thread_state is missing",
                    reject=True,
                ) from exc

        if session_state is None and thread_state is not None:
            canonical_workspace_id = canonical_workspace_id or thread_state.workspace_id or build_workspace_id(
                thread_state.repo_path,
                thread_state.workdir,
            )
            session_id = thread_state.session_id or thread_state.thread_id
            try:
                session_state = load_session_state(canonical_workspace_id, session_id, resolved_task_root)
            except FileNotFoundError:
                session_state = _build_session_state_from_thread(thread_state)

        if session_state is None or thread_state is None:
            raise Phase3SubscriptionError("session_not_found", "failed to resolve session detail state", reject=True)

        if request.session_id is not None and session_state.session_id != request.session_id:
            raise Phase3SubscriptionError(
                "session_identity_unresolved",
                "session_id does not match the resolved canonical session",
                reject=True,
            )
        if request.thread_id is not None and thread_state.thread_id != request.thread_id:
            raise Phase3SubscriptionError(
                "session_identity_unresolved",
                "thread_id does not match the resolved canonical thread",
                reject=True,
            )
        if session_state.thread_id != thread_state.thread_id:
            raise Phase3SubscriptionError(
                "session_identity_unresolved",
                "session_state and thread_state do not resolve to the same canonical thread",
                reject=True,
            )

        _matches_workspace_locator(
            workspace_id=request.workspace_id or "",
            repo_path=request.repo_path,
            workdir=request.workdir,
            session_state=session_state,
            thread_state=thread_state,
        )
        return session_state, thread_state

    def _resolve_workspace_id(self, request: Phase3SubscribeSessionDetailRequest) -> str | None:
        if request.workspace_id is not None and request.repo_path is not None and request.workdir is not None:
            fallback_workspace_id = build_workspace_id(request.repo_path, request.workdir)
            if fallback_workspace_id != request.workspace_id:
                raise Phase3SubscriptionError(
                    "workspace_identity_mismatch",
                    "workspace_id does not match repo_path/workdir canonical identity",
                    reject=True,
                )
        if request.workspace_id is not None:
            return request.workspace_id
        if request.repo_path is None:
            return None
        if request.workdir is None:
            raise Phase3SubscriptionError(
                "workspace_identity_unresolved",
                "repo_path alone cannot resolve a unique workspace",
                reject=True,
            )
        return build_workspace_id(request.repo_path, request.workdir)

    def _resolved_task_root(self) -> Path:
        if self._task_root is not None:
            return self._task_root
        return load_config().resolve_task_root()


def default_subscription_id_factory(request_id: str) -> str:
    return f"sub:{request_id}:{_timestamp()}"
