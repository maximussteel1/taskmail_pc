"""Mock adapter for local end-to-end validation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event, Lock
import time

from .base import WorkerAdapter
from ..models import RunResult, TaskSnapshot
from ..status import RUN_STATUS_KILLED, RUN_STATUS_SUCCESS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
SUMMARY_LINE = "Mock run completed successfully."
KILLED_SUMMARY_LINE = "Mock run was killed."


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _format_acceptance(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


class MockAdapter(WorkerAdapter):
    """Local adapter that writes deterministic run outputs."""

    def __init__(self, sleep_seconds: float = 1.0) -> None:
        self._sleep_seconds = max(0.0, float(sleep_seconds))
        self._active_stops: dict[str, Event] = {}
        self._lock = Lock()

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        started_at = _timestamp()
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        stop_event = Event()
        with self._lock:
            self._active_stops[task.task_id] = stop_event

        template_path = TEMPLATES_DIR / f"{task.backend}_prompt.txt"
        prompt_text = template_path.read_text(encoding="utf-8").format(
            task_id=task.task_id,
            thread_id=task.thread_id,
            profile=task.profile or "",
            repo_path=task.repo_path,
            workdir=task.workdir or "",
            mode=task.mode,
            timeout_minutes=task.timeout_minutes,
            task_text=task.task_text,
            acceptance=_format_acceptance(task.acceptance),
        )
        (run_path / "prompt.txt").write_text(prompt_text, encoding="utf-8")

        elapsed = 0.0
        sleep_step = 0.05
        while elapsed < self._sleep_seconds:
            if stop_event.wait(timeout=min(sleep_step, self._sleep_seconds - elapsed)):
                break
            elapsed += sleep_step

        stdout_path = run_path / "stdout.log"
        stderr_path = run_path / "stderr.log"
        summary_path = run_path / "summary.md"
        try:
            if stop_event.is_set():
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text(
                    f"Mock adapter killed task {task.task_id} for backend {task.backend}.\n",
                    encoding="utf-8",
                )
                summary_path.write_text(
                    "\n".join(
                        [
                            KILLED_SUMMARY_LINE,
                            "",
                            f"Backend: {task.backend}",
                            f"Task ID: {task.task_id}",
                            f"Repo: {task.repo_path}",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                status = RUN_STATUS_KILLED
                exit_code = None
                error_message = "Mock task was killed."
            else:
                stdout_path.write_text(
                    f"Mock adapter executed task {task.task_id} for backend {task.backend}.\n",
                    encoding="utf-8",
                )
                stderr_path.write_text("", encoding="utf-8")
                summary_path.write_text(
                    "\n".join(
                        [
                            SUMMARY_LINE,
                            "",
                            f"Backend: {task.backend}",
                            f"Task ID: {task.task_id}",
                            f"Repo: {task.repo_path}",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                status = RUN_STATUS_SUCCESS
                exit_code = 0
                error_message = None

            thread_dir = run_path.parent.parent
            return RunResult(
                task_id=task.task_id,
                thread_id=task.thread_id,
                backend=task.backend,
                status=status,
                exit_code=exit_code,
                started_at=started_at,
                finished_at=_timestamp(),
                stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
                stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
                summary_file=summary_path.relative_to(thread_dir).as_posix(),
                artifacts_dir=None,
                changed_files=[],
                tests_passed=None,
                error_message=error_message,
            )
        finally:
            with self._lock:
                self._active_stops.pop(task.task_id, None)

    def kill(self, task_id: str) -> bool:
        with self._lock:
            stop_event = self._active_stops.get(task_id)
        if stop_event is None:
            return False
        stop_event.set()
        return True
