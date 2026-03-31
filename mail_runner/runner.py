"""Serial local runner for snapshot-driven execution."""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .adapters.codex_adapter import CodexAdapter
from .adapters.codex_routing_adapter import CodexRoutingAdapter
from .adapters.codex_sdk_adapter import CodexSdkAdapter
from .adapters.opencode_adapter import OpenCodeAdapter
from .adapters.opencode_routing_adapter import OpenCodeRoutingAdapter
from .adapters.opencode_sdk_adapter import OpenCodeSdkAdapter
from .artifact_resolver import resolve_run_artifacts, write_artifact_index
from .config import AppConfig, load_config
from .dispatcher import Dispatcher
from .models import RunResult, TaskSnapshot, ThreadState
from .monitor_windows import ActiveSessionWindowManager
from .status import (
    BACKEND_TRANSPORT_CLI,
    FINAL_THREAD_STATUS_BY_RUN_STATUS,
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_FAILED,
    RUN_STATUS_KILLED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_AWAITING_USER_INPUT,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_PAUSED,
    THREAD_STATUS_RUNNING,
)
from .thread_store import build_workspace_id, build_workspace_norm, create_thread, load_thread_state, save_thread_state
from .workspace import WorkspaceManager

LOGGER = logging.getLogger(__name__)
StateCallback = Callable[[ThreadState], None]
FinishedCallback = Callable[[ThreadState, RunResult], None]
RecoveryCallbackFactory = Callable[[ThreadState, TaskSnapshot], tuple[StateCallback | None, FinishedCallback | None]]


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _generate_task_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(2)


def _generate_question_id(task_id: str) -> str:
    return f"question_{task_id}"


def _empty_to_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return str(value)


def _load_snapshot_seed(path: str | Path) -> dict[str, Any]:
    seed_path = Path(path)
    payload = json.loads(seed_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Snapshot seed file must contain a JSON object.")
    return payload


def _extract_summary(thread_dir: Path, result: RunResult) -> str | None:
    if not result.summary_file:
        return result.error_message
    summary_path = thread_dir / result.summary_file
    if not summary_path.exists():
        return result.error_message
    for line in summary_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.strip()
    return result.error_message


@dataclass(slots=True)
class _ActiveRun:
    snapshot: TaskSnapshot
    state: ThreadState
    run_path: Path
    dispatch_started_at: str
    on_finished: FinishedCallback | None
    done: threading.Event
    thread: threading.Thread
    result: RunResult | None = None


@dataclass(slots=True)
class _QueuedRun:
    snapshot: TaskSnapshot
    snapshot_rel: str
    root_message_id: str | None
    latest_message_id: str | None
    subject_norm: str | None
    session_name: str | None
    on_running: StateCallback | None
    on_finished: FinishedCallback | None


class SerialTaskRunner:
    """Coordinates local execution with separate active-session and running-session caps."""

    def __init__(
        self,
        task_root: str | Path,
        dispatcher: Dispatcher,
        max_active_sessions: int = 4,
        max_active_sessions_per_workspace: int | None = None,
        max_running_sessions: int | None = None,
        max_running_sessions_per_workspace: int | None = 2,
        opencode_transport_default: str = "sdk",
        codex_transport_default: str = "sdk",
        recovery_callback_factory: RecoveryCallbackFactory | None = None,
        monitor_window_manager: ActiveSessionWindowManager | None = None,
    ) -> None:
        self.workspace = WorkspaceManager(task_root)
        self.dispatcher = dispatcher
        self.max_active_sessions = max(1, int(max_active_sessions))
        if max_active_sessions_per_workspace is None:
            self.max_active_sessions_per_workspace = self.max_active_sessions
        else:
            self.max_active_sessions_per_workspace = max(1, int(max_active_sessions_per_workspace))
        if max_running_sessions is None:
            self.max_running_sessions = self.max_active_sessions
        else:
            self.max_running_sessions = max(1, int(max_running_sessions))
        if max_running_sessions_per_workspace is None:
            self.max_running_sessions_per_workspace = self.max_running_sessions
        else:
            self.max_running_sessions_per_workspace = max(1, int(max_running_sessions_per_workspace))
        self.opencode_transport_default = opencode_transport_default
        self.codex_transport_default = codex_transport_default
        self._recovery_callback_factory = recovery_callback_factory
        self._monitor_window_manager = monitor_window_manager
        self._lock = threading.Lock()
        self._active_runs: dict[str, _ActiveRun] = {}
        self._queued_runs: list[_QueuedRun] = []
        self.workspace.ensure_layout()
        self._recover_persisted_queue()

    def start(self, snapshot_path: str | Path) -> RunResult:
        return self.run_snapshot_seed(_load_snapshot_seed(snapshot_path))

    def run_snapshot_seed(self, snapshot_seed: dict[str, Any]) -> RunResult:
        self.workspace.ensure_layout()
        thread_id = _empty_to_none(snapshot_seed.get("thread_id")) or self._next_thread_id()
        task_id = _empty_to_none(snapshot_seed.get("task_id")) or _generate_task_id()
        created_at = _empty_to_none(snapshot_seed.get("created_at")) or _timestamp()
        updated_at = _empty_to_none(snapshot_seed.get("updated_at")) or created_at

        required_fields = ("backend", "repo_path", "task_text")
        missing = [field_name for field_name in required_fields if not _empty_to_none(snapshot_seed.get(field_name))]
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"Snapshot seed is missing required fields: {missing_text}")

        acceptance = snapshot_seed.get("acceptance") or []
        attachments = snapshot_seed.get("attachments") or []
        snapshot = TaskSnapshot(
            task_id=task_id,
            thread_id=thread_id,
            backend=str(snapshot_seed["backend"]),
            profile=_empty_to_none(snapshot_seed.get("profile")),
            permission=_empty_to_none(snapshot_seed.get("permission")),
            repo_path=str(snapshot_seed["repo_path"]),
            workdir=_empty_to_none(snapshot_seed.get("workdir")),
            task_text=str(snapshot_seed["task_text"]),
            acceptance=list(acceptance),
            timeout_minutes=int(snapshot_seed.get("timeout_minutes", 60)),
            mode=_empty_to_none(snapshot_seed.get("mode")) or "modify",
            attachments=list(attachments),
            created_at=created_at,
            updated_at=updated_at,
            run_mode=_empty_to_none(snapshot_seed.get("run_mode")) or "new",
            backend_session_id=_empty_to_none(snapshot_seed.get("backend_session_id")),
            turn_text=_empty_to_none(snapshot_seed.get("turn_text")),
            backend_transport=(
                _empty_to_none(snapshot_seed.get("backend_transport"))
                or self._default_transport_for_backend(str(snapshot_seed["backend"]))
            ),
        )
        return self.run_task_snapshot(snapshot)

    def run_task_snapshot(
        self,
        snapshot: TaskSnapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        session_name: str | None = None,
        on_accepted: StateCallback | None = None,
        on_running: StateCallback | None = None,
        on_finished: FinishedCallback | None = None,
    ) -> RunResult:
        self.start_background_task(
            snapshot,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
            session_name=session_name,
            on_accepted=on_accepted,
            on_running=on_running,
            on_finished=on_finished,
        )
        self.wait_until_idle()
        result_path = self.workspace.run_file_path(snapshot.thread_id, snapshot.task_id, "result.json")
        if not result_path.exists():
            raise RuntimeError(f"Task result was not written for {snapshot.task_id}.")
        return self.workspace.load_run_result(snapshot.thread_id, f"runs/{snapshot.task_id}/result.json")

    def start_background_task(
        self,
        snapshot: TaskSnapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        session_name: str | None = None,
        on_accepted: StateCallback | None = None,
        on_running: StateCallback | None = None,
        on_finished: FinishedCallback | None = None,
    ) -> ThreadState:
        self.workspace.ensure_layout()
        snapshot_path = self.workspace.snapshot_path(snapshot.thread_id, snapshot.task_id)
        run_dir = self.workspace.run_dir(snapshot.thread_id, snapshot.task_id)
        if snapshot_path.exists() or run_dir.exists():
            raise FileExistsError(
                f"Task already exists for thread '{snapshot.thread_id}' and task '{snapshot.task_id}'."
            )

        saved_snapshot_path = self.workspace.save_snapshot(snapshot)
        snapshot_rel = self.workspace.to_thread_relative(snapshot.thread_id, saved_snapshot_path)
        with self._lock:
            active_thread_ids = {active.snapshot.thread_id for active in self._active_runs.values()}
        state = self._prepare_thread_state(
            snapshot,
            snapshot_rel,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
            session_name=session_name,
            active_thread_ids=active_thread_ids,
        )
        if on_accepted is not None:
            on_accepted(state)

        queued = _QueuedRun(
            snapshot=snapshot,
            snapshot_rel=snapshot_rel,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
            session_name=session_name,
            on_running=on_running,
            on_finished=on_finished,
        )
        self._upsert_queued_run(queued)
        self.dispatch_ready()
        return state

    def collect_finished(self) -> list[tuple[ThreadState, RunResult]]:
        with self._lock:
            finished_runs = [active for active in self._active_runs.values() if active.done.is_set()]
        if not finished_runs:
            self.dispatch_ready()
            return []
        finalized: list[tuple[ThreadState, RunResult]] = []
        for active in finished_runs:
            active.thread.join(timeout=0)
            if active.result is None:
                active.result = self._build_failure_result(
                    active.snapshot,
                    active.run_path,
                    active.dispatch_started_at,
                    RuntimeError("Background dispatch finished without producing a result."),
                )

            result_path = self.workspace.save_run_result(active.snapshot.thread_id, active.snapshot.task_id, active.result)
            final_state = self._finalize_thread_state(active.state, active.result, result_path)
            self._persist_artifact_index(final_state, active.result)
            with self._lock:
                self._active_runs.pop(active.snapshot.task_id, None)
            if active.result.status == RUN_STATUS_AWAITING_USER_INPUT:
                final_state = self._drop_queued_for_thread(active.snapshot.thread_id, clear_state=True) or final_state
            if active.on_finished is not None:
                active.on_finished(final_state, active.result)
            self._notify_monitor_run_finished(final_state, active.result)
            finalized.append((final_state, active.result))
        self.dispatch_ready()
        return finalized

    def wait_for_active(self, poll_seconds: float = 0.05) -> RunResult | None:
        while True:
            with self._lock:
                has_active = bool(self._active_runs)
            if not has_active:
                return None
            time.sleep(poll_seconds)
            finished = self.collect_finished()
            if finished:
                return finished[0][1]

    def wait_until_idle(self, poll_seconds: float = 0.05) -> None:
        while True:
            self.collect_finished()
            self.dispatch_ready()
            with self._lock:
                if not self._active_runs and not self._queued_runs:
                    return
            time.sleep(poll_seconds)

    def kill(self, task_id: str) -> bool:
        with self._lock:
            active = self._active_runs.get(task_id)
        if active is None:
            return False
        return self.dispatcher.kill(active.snapshot.backend, task_id)

    def kill_thread(self, thread_id: str, *, expected_task_id: str | None = None) -> bool:
        with self._lock:
            active = next(
                (
                    item
                    for item in self._active_runs.values()
                    if item.snapshot.thread_id == thread_id
                    and (expected_task_id is None or item.snapshot.task_id == expected_task_id)
                ),
                None,
            )
        if active is None:
            return False
        return self.dispatcher.kill(active.snapshot.backend, active.snapshot.task_id)

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._active_runs) or bool(self._queued_runs)

    def active_task_id(self) -> str | None:
        with self._lock:
            active = next(iter(self._active_runs.values()), None)
        return None if active is None else active.snapshot.task_id

    def active_thread_id(self) -> str | None:
        with self._lock:
            active = next(iter(self._active_runs.values()), None)
        return None if active is None else active.snapshot.thread_id

    def active_snapshot(self) -> TaskSnapshot | None:
        with self._lock:
            active = next(iter(self._active_runs.values()), None)
        return None if active is None else active.snapshot

    def queued_count(self) -> int:
        with self._lock:
            return len(self._queued_runs)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active_runs)

    def dispatch_ready(self) -> bool:
        started = False
        while True:
            with self._lock:
                if len(self._active_runs) >= self.max_running_sessions or not self._queued_runs:
                    return started
                active_workspace_counts: dict[str, int] = {}
                active_thread_ids: set[str] = set()
                for active in self._active_runs.values():
                    active_thread_ids.add(active.snapshot.thread_id)
                    workspace_id = active.state.workspace_id or build_workspace_id(
                        active.snapshot.repo_path,
                        active.snapshot.workdir,
                    )
                    active_workspace_counts[workspace_id] = active_workspace_counts.get(workspace_id, 0) + 1
                candidate_index = None
                for index, queued in enumerate(self._queued_runs):
                    if queued.snapshot.thread_id in active_thread_ids:
                        continue
                    workspace_id = build_workspace_id(queued.snapshot.repo_path, queued.snapshot.workdir)
                    if active_workspace_counts.get(workspace_id, 0) < self.max_running_sessions_per_workspace:
                        candidate_index = index
                        break
                if candidate_index is None:
                    return started
                queued = self._queued_runs.pop(candidate_index)
            self._start_queued_run(queued)
            started = True

    def _persist_artifact_index(self, state: ThreadState, result: RunResult) -> None:
        try:
            artifacts, skipped = resolve_run_artifacts(self.workspace.task_root, state, result)
            write_artifact_index(self.workspace.task_root, result, artifacts, skipped)
        except Exception:
            LOGGER.exception(
                "Unable to persist artifact_index.json for thread=%s task_id=%s",
                state.thread_id,
                result.task_id,
            )

    def next_thread_id(self) -> str:
        return self._next_thread_id()

    def _dispatch_active_run(
        self,
        snapshot: TaskSnapshot,
        run_path: Path,
        dispatch_started_at: str,
        done: threading.Event,
    ) -> None:
        try:
            try:
                result = self.dispatcher.dispatch(snapshot, str(run_path))
            except Exception as exc:
                result = self._build_failure_result(snapshot, run_path, dispatch_started_at, exc)
            with self._lock:
                active = self._active_runs.get(snapshot.task_id)
                if active is not None:
                    active.result = result
        finally:
            done.set()

    def _next_thread_id(self) -> str:
        existing: list[int] = []
        for thread_dir in self.workspace.task_root.glob("thread_*"):
            if thread_dir.is_dir():
                suffix = thread_dir.name.removeprefix("thread_")
                if suffix.isdigit():
                    existing.append(int(suffix))
        return f"thread_{max(existing, default=0) + 1:03d}"

    def _default_transport_for_backend(self, backend: str) -> str:
        if backend == "opencode":
            return self.opencode_transport_default
        if backend == "codex":
            return self.codex_transport_default
        return BACKEND_TRANSPORT_CLI

    def _prepare_thread_state(
        self,
        snapshot: TaskSnapshot,
        snapshot_rel: str,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        session_name: str | None = None,
        active_thread_ids: set[str] | None = None,
    ) -> ThreadState:
        state_path = self.workspace.thread_state_path(snapshot.thread_id)
        if state_path.exists():
            state = load_thread_state(snapshot.thread_id, self.workspace.task_root)
            state.backend = snapshot.backend
            state.profile = snapshot.profile
            state.permission = snapshot.permission
            state.repo_path = snapshot.repo_path
            state.workdir = snapshot.workdir
            state.lifecycle = "active"
            state.last_active_at = snapshot.updated_at
            state.last_progress_at = snapshot.updated_at
            state.backend_transport = snapshot.backend_transport
            state.workspace_id = state.workspace_id or build_workspace_id(snapshot.repo_path, snapshot.workdir)
            state.workspace_norm = state.workspace_norm or build_workspace_norm(snapshot.repo_path, snapshot.workdir)
            state.session_id = state.session_id or snapshot.thread_id
            state.session_name = state.session_name or session_name or subject_norm or snapshot.thread_id
            state.session_norm = state.session_norm or subject_norm or state.subject_norm
            if snapshot.canonical_reply_recipient is not None:
                state.canonical_reply_recipient = snapshot.canonical_reply_recipient
            if snapshot.thread_id in (active_thread_ids or set()) and state.status == THREAD_STATUS_RUNNING:
                state.queued_task_id = snapshot.task_id
                state.queued_snapshot_file = snapshot_rel
            else:
                state.current_task_id = snapshot.task_id
                state.last_task_snapshot_file = snapshot_rel
                state.status = THREAD_STATUS_ACCEPTED
                state.last_summary = None
                state.pending_question_id = None
                state.pending_question_text = None
                state.pending_choices = []
                state.pending_question_set_id = None
                state.pending_questions = []
                state.collected_answers = []
                state.awaiting_since = None
                state.paused_from_status = None
                state.queued_task_id = None
                state.queued_snapshot_file = None
                if snapshot.run_mode == "new":
                    state.backend_session_id = None
                    state.backend_session_resumable = False
            state.updated_at = snapshot.updated_at
            save_thread_state(state, self.workspace.task_root)
            return state

        return create_thread(
            thread_id=snapshot.thread_id,
            root_message_id=root_message_id or f"local-root:{snapshot.thread_id}",
            latest_message_id=latest_message_id or f"local-latest:{snapshot.thread_id}",
            subject_norm=subject_norm or f"local-demo:{snapshot.thread_id}",
            session_name=session_name or subject_norm or f"local-demo:{snapshot.thread_id}",
            backend=snapshot.backend,
            profile=snapshot.profile,
            permission=snapshot.permission,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file=snapshot_rel,
            task_root=self.workspace.task_root,
            status=THREAD_STATUS_ACCEPTED,
            history_files=[],
            last_summary=None,
            lifecycle="active",
            last_active_at=snapshot.updated_at,
            pending_question_id=None,
            pending_question_text=None,
            pending_choices=[],
            pending_question_set_id=None,
            pending_questions=[],
            collected_answers=[],
            awaiting_since=None,
            paused_from_status=None,
            canonical_reply_recipient=snapshot.canonical_reply_recipient,
            backend_session_id=None,
            backend_session_resumable=False,
            backend_transport=snapshot.backend_transport,
            queued_task_id=None,
            queued_snapshot_file=None,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
            last_progress_at=snapshot.updated_at,
        )

    def _upsert_queued_run(self, queued: _QueuedRun) -> None:
        with self._lock:
            for index, existing in enumerate(self._queued_runs):
                if existing.snapshot.thread_id == queued.snapshot.thread_id:
                    self._queued_runs[index] = queued
                    return
            self._queued_runs.append(queued)

    def _start_queued_run(self, queued: _QueuedRun) -> None:
        state = self._activate_thread_state(queued.snapshot, queued.snapshot_rel)
        run_path = self.workspace.create_run_dir(queued.snapshot.thread_id, queued.snapshot.task_id)
        if queued.on_running is not None:
            queued.on_running(state)

        done = threading.Event()
        active = _ActiveRun(
            snapshot=queued.snapshot,
            state=state,
            run_path=run_path,
            dispatch_started_at=_timestamp(),
            on_finished=queued.on_finished,
            done=done,
            thread=threading.Thread(
                target=self._dispatch_active_run,
                args=(queued.snapshot, run_path, _timestamp(), done),
                daemon=True,
                name=f"mail-runner-{queued.snapshot.task_id}",
            ),
        )
        with self._lock:
            self._active_runs[queued.snapshot.task_id] = active
        active.thread.start()
        self._notify_monitor_run_started(state, queued.snapshot)

    def _activate_thread_state(self, snapshot: TaskSnapshot, snapshot_rel: str) -> ThreadState:
        state = load_thread_state(snapshot.thread_id, self.workspace.task_root)
        state.backend = snapshot.backend
        state.profile = snapshot.profile
        state.permission = snapshot.permission
        state.repo_path = snapshot.repo_path
        state.workdir = snapshot.workdir
        state.lifecycle = "active"
        state.last_active_at = _timestamp()
        state.last_progress_at = state.last_active_at
        state.backend_transport = snapshot.backend_transport
        if snapshot.canonical_reply_recipient is not None:
            state.canonical_reply_recipient = snapshot.canonical_reply_recipient
        if state.queued_task_id == snapshot.task_id:
            state.current_task_id = snapshot.task_id
            state.last_task_snapshot_file = state.queued_snapshot_file or snapshot_rel
            state.queued_task_id = None
            state.queued_snapshot_file = None
        else:
            state.current_task_id = snapshot.task_id
            state.last_task_snapshot_file = snapshot_rel
        state.status = THREAD_STATUS_RUNNING
        state.paused_from_status = None
        state.updated_at = _timestamp()
        save_thread_state(state, self.workspace.task_root)
        return state

    def _drop_queued_for_thread(self, thread_id: str, *, clear_state: bool) -> ThreadState | None:
        removed = False
        with self._lock:
            kept: list[_QueuedRun] = []
            for queued in self._queued_runs:
                if queued.snapshot.thread_id == thread_id:
                    removed = True
                    continue
                kept.append(queued)
            self._queued_runs = kept
        if not clear_state or not removed:
            return None
        state = load_thread_state(thread_id, self.workspace.task_root)
        state.queued_task_id = None
        state.queued_snapshot_file = None
        state.updated_at = _timestamp()
        save_thread_state(state, self.workspace.task_root)
        return state

    def _recover_persisted_queue(self) -> None:
        for thread_dir in sorted(self.workspace.task_root.glob("thread_*")):
            if not thread_dir.is_dir():
                continue
            state_path = self.workspace.thread_state_path(thread_dir.name)
            if not state_path.exists():
                continue
            state = load_thread_state(thread_dir.name, self.workspace.task_root)
            if state.status == THREAD_STATUS_ACCEPTED:
                self._recover_snapshot_for_state(state, state.current_task_id, state.last_task_snapshot_file)
                continue
            if state.status == THREAD_STATUS_RUNNING:
                self._recover_running_state(state)
                continue
            if state.queued_task_id and state.queued_snapshot_file:
                self._promote_queued_snapshot(state, note_prefix="Recovered queued follow-up after runner restart.")

    def _recover_running_state(self, state: ThreadState) -> None:
        if state.queued_task_id and state.queued_snapshot_file:
            self._promote_queued_snapshot(state, note_prefix="Runner restarted while the previous run was still executing.")
            return
        state.status = THREAD_STATUS_FAILED
        state.last_summary = "Runner restarted while task was running."
        state.pending_question_id = None
        state.pending_question_text = None
        state.pending_choices = []
        state.pending_question_set_id = None
        state.pending_questions = []
        state.collected_answers = []
        state.awaiting_since = None
        state.paused_from_status = None
        state.updated_at = _timestamp()
        state.last_progress_at = state.updated_at
        save_thread_state(state, self.workspace.task_root)

    def _promote_queued_snapshot(self, state: ThreadState, *, note_prefix: str) -> None:
        queued_task_id = state.queued_task_id
        queued_snapshot_file = state.queued_snapshot_file
        if not queued_task_id or not queued_snapshot_file:
            return
        state.current_task_id = queued_task_id
        state.last_task_snapshot_file = queued_snapshot_file
        state.status = THREAD_STATUS_ACCEPTED
        state.queued_task_id = None
        state.queued_snapshot_file = None
        state.last_summary = note_prefix
        state.pending_question_id = None
        state.pending_question_text = None
        state.pending_choices = []
        state.pending_question_set_id = None
        state.pending_questions = []
        state.collected_answers = []
        state.awaiting_since = None
        state.paused_from_status = None
        state.updated_at = _timestamp()
        state.last_progress_at = state.updated_at
        save_thread_state(state, self.workspace.task_root)
        self._recover_snapshot_for_state(state, state.current_task_id, state.last_task_snapshot_file)

    def _recover_snapshot_for_state(self, state: ThreadState, task_id: str, snapshot_rel: str) -> None:
        try:
            snapshot = self.workspace.load_snapshot(state.thread_id, snapshot_rel)
        except FileNotFoundError:
            LOGGER.warning("Unable to recover queued snapshot for %s: missing %s", state.thread_id, snapshot_rel)
            return
        on_running = None
        on_finished = None
        if self._recovery_callback_factory is not None:
            on_running, on_finished = self._recovery_callback_factory(state, snapshot)
        self._upsert_queued_run(
            _QueuedRun(
                snapshot=snapshot,
                snapshot_rel=snapshot_rel,
                root_message_id=state.root_message_id,
                latest_message_id=state.latest_message_id,
                subject_norm=state.subject_norm,
                session_name=state.session_name,
                on_running=on_running,
                on_finished=on_finished,
            )
        )

    def _build_failure_result(
        self,
        snapshot: TaskSnapshot,
        run_path: Path,
        started_at: str,
        exc: Exception,
    ) -> RunResult:
        error_text = f"{type(exc).__name__}: {exc}"
        stdout_path = self.workspace.write_text(run_path / "stdout.log", "")
        stderr_path = self.workspace.write_text(run_path / "stderr.log", error_text + "\n")
        return RunResult(
            task_id=snapshot.task_id,
            thread_id=snapshot.thread_id,
            backend=snapshot.backend,
            status=RUN_STATUS_FAILED,
            exit_code=1,
            started_at=started_at,
            finished_at=_timestamp(),
            stdout_file=self.workspace.to_thread_relative(snapshot.thread_id, stdout_path),
            stderr_file=self.workspace.to_thread_relative(snapshot.thread_id, stderr_path),
            summary_file=None,
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=error_text,
            backend_transport=snapshot.backend_transport,
        )

    def _finalize_thread_state(self, state: ThreadState, result: RunResult, result_path: Path) -> ThreadState:
        state.current_task_id = result.task_id
        state.history_files.append(self.workspace.to_thread_relative(state.thread_id, result_path))
        state.last_summary = _extract_summary(self.workspace.thread_dir(state.thread_id), result)
        state.status = FINAL_THREAD_STATUS_BY_RUN_STATUS[result.status]
        state.lifecycle = "active"
        state.last_active_at = result.finished_at or _timestamp()
        state.last_progress_at = state.last_active_at
        state.backend_transport = result.backend_transport
        state.backend_session_id = result.backend_session_id or state.backend_session_id
        state.backend_session_resumable = bool(state.backend_session_id) and bool(result.backend_session_resumable)
        state.paused_from_status = THREAD_STATUS_RUNNING if result.status == RUN_STATUS_PAUSED else None
        if result.status == RUN_STATUS_KILLED and state.backend_session_id:
            # A killed run may still have a usable native session id. We keep it resumable
            # and let the mail layer mark the next continuation as a risk recovery.
            state.backend_session_resumable = True
        if result.status == RUN_STATUS_AWAITING_USER_INPUT:
            state.pending_question_id = result.question_id or _generate_question_id(result.task_id)
            state.pending_question_text = result.question_text
            state.pending_choices = list(result.pending_choices)
            state.pending_question_set_id = result.question_set_id or state.pending_question_id
            state.pending_questions = list(result.pending_questions)
            state.collected_answers = []
            state.awaiting_since = result.finished_at or _timestamp()
        else:
            state.pending_question_id = None
            state.pending_question_text = None
            state.pending_choices = []
            state.pending_question_set_id = None
            state.pending_questions = []
            state.collected_answers = []
            state.awaiting_since = None
        state.updated_at = _timestamp()
        save_thread_state(state, self.workspace.task_root)
        return state

    def _notify_monitor_run_started(self, state: ThreadState, snapshot: TaskSnapshot) -> None:
        if self._monitor_window_manager is None:
            return
        try:
            self._monitor_window_manager.on_run_started(state, snapshot)
        except Exception:
            LOGGER.exception("Monitor window startup hook failed for thread %s", state.thread_id)

    def _notify_monitor_run_finished(self, state: ThreadState, result: RunResult) -> None:
        if self._monitor_window_manager is None:
            return
        try:
            self._monitor_window_manager.on_run_finished(state, result)
        except Exception:
            LOGGER.exception("Monitor window cleanup hook failed for thread %s", state.thread_id)


def _build_dispatcher(config: AppConfig | None = None) -> Dispatcher:
    effective_config = config or AppConfig()
    return Dispatcher(
        opencode_adapter=OpenCodeRoutingAdapter(
            cli_adapter=OpenCodeAdapter(effective_config),
            sdk_adapter=OpenCodeSdkAdapter(effective_config),
        ),
        codex_adapter=CodexRoutingAdapter(
            cli_adapter=CodexAdapter(effective_config),
            sdk_adapter=CodexSdkAdapter(effective_config),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local snapshot against the configured backend.")
    parser.add_argument("--snapshot", required=True, help="Path to a TaskSnapshot seed JSON file.")
    parser.add_argument("--task-root", help="Optional task root directory override.")
    parser.add_argument("--config", help="Optional path to config.yaml")
    args = parser.parse_args(argv)

    configure_logging()
    config = load_config(args.config)
    config_base_dir = Path(args.config).resolve().parent if args.config else None
    task_root = Path(args.task_root) if args.task_root else config.resolve_task_root(config_base_dir)
    runner = SerialTaskRunner(
        task_root,
        _build_dispatcher(config),
        max_active_sessions=config.max_active_sessions,
        max_active_sessions_per_workspace=config.max_active_sessions_per_workspace,
        max_running_sessions=config.max_running_sessions,
        max_running_sessions_per_workspace=config.max_running_sessions_per_workspace,
        opencode_transport_default=config.opencode_transport_default,
        codex_transport_default=config.codex_transport_default,
    )
    try:
        result = runner.start(args.snapshot)
    except Exception:
        LOGGER.exception("Snapshot run failed before completion.")
        return 1

    LOGGER.info(
        "Snapshot run completed. task_id=%s thread_id=%s status=%s",
        result.task_id,
        result.thread_id,
        result.status,
    )
    return 0 if result.status in {RUN_STATUS_SUCCESS, RUN_STATUS_AWAITING_USER_INPUT} else 1


if __name__ == "__main__":
    raise SystemExit(main())
