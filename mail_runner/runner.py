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
from .adapters.opencode_adapter import OpenCodeAdapter
from .config import AppConfig, load_config
from .dispatcher import Dispatcher
from .models import RunResult, TaskSnapshot, ThreadState
from .status import (
    FINAL_THREAD_STATUS_BY_RUN_STATUS,
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_ACCEPTED,
    THREAD_STATUS_RUNNING,
)
from .thread_store import create_thread, load_thread_state, save_thread_state
from .workspace import WorkspaceManager

LOGGER = logging.getLogger(__name__)
StateCallback = Callable[[ThreadState], None]
FinishedCallback = Callable[[ThreadState, RunResult], None]


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


class SerialTaskRunner:
    """Coordinates one local task at a time."""

    def __init__(self, task_root: str | Path, dispatcher: Dispatcher) -> None:
        self.workspace = WorkspaceManager(task_root)
        self.dispatcher = dispatcher
        self._lock = threading.Lock()
        self._active_run: _ActiveRun | None = None

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
            repo_path=str(snapshot_seed["repo_path"]),
            workdir=_empty_to_none(snapshot_seed.get("workdir")),
            task_text=str(snapshot_seed["task_text"]),
            acceptance=list(acceptance),
            timeout_minutes=int(snapshot_seed.get("timeout_minutes", 60)),
            mode=_empty_to_none(snapshot_seed.get("mode")) or "modify",
            attachments=list(attachments),
            created_at=created_at,
            updated_at=updated_at,
        )
        return self.run_task_snapshot(snapshot)

    def run_task_snapshot(
        self,
        snapshot: TaskSnapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        on_accepted: StateCallback | None = None,
        on_running: StateCallback | None = None,
        on_finished: FinishedCallback | None = None,
    ) -> RunResult:
        self.start_background_task(
            snapshot,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
            on_accepted=on_accepted,
            on_running=on_running,
            on_finished=on_finished,
        )
        result = self.wait_for_active()
        if result is None:
            raise RuntimeError("Active task vanished before completion.")
        return result

    def start_background_task(
        self,
        snapshot: TaskSnapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        on_accepted: StateCallback | None = None,
        on_running: StateCallback | None = None,
        on_finished: FinishedCallback | None = None,
    ) -> ThreadState:
        with self._lock:
            if self._active_run is not None:
                raise RuntimeError("Runner is already executing another task.")

        self.workspace.ensure_layout()
        snapshot_path = self.workspace.snapshot_path(snapshot.thread_id, snapshot.task_id)
        run_dir = self.workspace.run_dir(snapshot.thread_id, snapshot.task_id)
        if snapshot_path.exists() or run_dir.exists():
            raise FileExistsError(
                f"Task already exists for thread '{snapshot.thread_id}' and task '{snapshot.task_id}'."
            )

        saved_snapshot_path = self.workspace.save_snapshot(snapshot)
        snapshot_rel = self.workspace.to_thread_relative(snapshot.thread_id, saved_snapshot_path)
        state = self._prepare_thread_state(
            snapshot,
            snapshot_rel,
            root_message_id=root_message_id,
            latest_message_id=latest_message_id,
            subject_norm=subject_norm,
        )
        if on_accepted is not None:
            on_accepted(state)

        run_path = self.workspace.create_run_dir(snapshot.thread_id, snapshot.task_id)
        state.status = THREAD_STATUS_RUNNING
        state.updated_at = _timestamp()
        save_thread_state(state, self.workspace.task_root)
        if on_running is not None:
            on_running(state)

        done = threading.Event()
        active = _ActiveRun(
            snapshot=snapshot,
            state=state,
            run_path=run_path,
            dispatch_started_at=_timestamp(),
            on_finished=on_finished,
            done=done,
            thread=threading.Thread(
                target=self._dispatch_active_run,
                args=(snapshot, run_path, _timestamp(), done),
                daemon=True,
                name=f"mail-runner-{snapshot.task_id}",
            ),
        )
        with self._lock:
            self._active_run = active
        active.thread.start()
        return state

    def collect_finished(self) -> list[tuple[ThreadState, RunResult]]:
        with self._lock:
            active = self._active_run
        if active is None or not active.done.is_set():
            return []

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
        with self._lock:
            if self._active_run is active:
                self._active_run = None
        if active.on_finished is not None:
            active.on_finished(final_state, active.result)
        return [(final_state, active.result)]

    def wait_for_active(self, poll_seconds: float = 0.05) -> RunResult | None:
        while True:
            with self._lock:
                active = self._active_run
            if active is None:
                return None
            if active.done.wait(timeout=poll_seconds):
                finished = self.collect_finished()
                return finished[0][1] if finished else None

    def kill(self, task_id: str) -> bool:
        with self._lock:
            active = self._active_run
        if active is None or active.snapshot.task_id != task_id:
            return False
        return self.dispatcher.kill(active.snapshot.backend, task_id)

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_run is not None

    def active_task_id(self) -> str | None:
        with self._lock:
            return None if self._active_run is None else self._active_run.snapshot.task_id

    def active_thread_id(self) -> str | None:
        with self._lock:
            return None if self._active_run is None else self._active_run.snapshot.thread_id

    def active_snapshot(self) -> TaskSnapshot | None:
        with self._lock:
            return None if self._active_run is None else self._active_run.snapshot

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
                if self._active_run is not None and self._active_run.snapshot.task_id == snapshot.task_id:
                    self._active_run.result = result
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

    def _prepare_thread_state(
        self,
        snapshot: TaskSnapshot,
        snapshot_rel: str,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
    ) -> ThreadState:
        state_path = self.workspace.thread_state_path(snapshot.thread_id)
        if state_path.exists():
            state = load_thread_state(snapshot.thread_id, self.workspace.task_root)
            state.backend = snapshot.backend
            state.profile = snapshot.profile
            state.repo_path = snapshot.repo_path
            state.workdir = snapshot.workdir
            state.current_task_id = snapshot.task_id
            state.last_task_snapshot_file = snapshot_rel
            state.status = THREAD_STATUS_ACCEPTED
            state.last_summary = None
            state.pending_question_id = None
            state.pending_question_text = None
            state.pending_choices = []
            state.awaiting_since = None
            state.updated_at = snapshot.updated_at
            save_thread_state(state, self.workspace.task_root)
            return state

        return create_thread(
            thread_id=snapshot.thread_id,
            root_message_id=root_message_id or f"local-root:{snapshot.thread_id}",
            latest_message_id=latest_message_id or f"local-latest:{snapshot.thread_id}",
            subject_norm=subject_norm or f"local-demo:{snapshot.thread_id}",
            backend=snapshot.backend,
            profile=snapshot.profile,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file=snapshot_rel,
            task_root=self.workspace.task_root,
            status=THREAD_STATUS_ACCEPTED,
            history_files=[],
            last_summary=None,
            pending_question_id=None,
            pending_question_text=None,
            pending_choices=[],
            awaiting_since=None,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
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
        )

    def _finalize_thread_state(self, state: ThreadState, result: RunResult, result_path: Path) -> ThreadState:
        state.current_task_id = result.task_id
        state.history_files.append(self.workspace.to_thread_relative(state.thread_id, result_path))
        state.last_summary = _extract_summary(self.workspace.thread_dir(state.thread_id), result)
        state.status = FINAL_THREAD_STATUS_BY_RUN_STATUS[result.status]
        if result.status == RUN_STATUS_AWAITING_USER_INPUT:
            state.pending_question_id = result.question_id or _generate_question_id(result.task_id)
            state.pending_question_text = result.question_text
            state.pending_choices = list(result.pending_choices)
            state.awaiting_since = result.finished_at or _timestamp()
        else:
            state.pending_question_id = None
            state.pending_question_text = None
            state.pending_choices = []
            state.awaiting_since = None
        state.updated_at = _timestamp()
        save_thread_state(state, self.workspace.task_root)
        return state


def _build_dispatcher(config: AppConfig | None = None) -> Dispatcher:
    effective_config = config or AppConfig()
    return Dispatcher(
        opencode_adapter=OpenCodeAdapter(effective_config),
        codex_adapter=CodexAdapter(effective_config),
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
    runner = SerialTaskRunner(task_root, _build_dispatcher(config))
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
