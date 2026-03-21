"""Codex SDK adapter backed by a thin Node sidecar."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from ..config import AppConfig, PROJECT_ROOT
from ..codex_process_registry import remove_process_record, write_process_record
from ..models import QuestionItem, RunResult, TaskSnapshot
from ..run_result_capsule import parse_run_result_capsule, strip_run_result_capsules
from ..state_capsule import parse_question_capsules
from ..status import (
    RUN_STATUS_AWAITING_USER_INPUT,
    RUN_STATUS_FAILED,
    RUN_STATUS_KILLED,
    RUN_STATUS_SUCCESS,
)
from ..stream_events import STREAM_EVENTS_FILENAME, load_stream_events
from .base import WorkerAdapter
from .cli_common import (
    WINDOWS,
    _incoming_attachment_payload,
    _runtime_prompt_hint,
    extract_error_excerpt,
    extract_output_block,
    extract_summary_line,
    normalize_log_text,
    prepare_task_cwd,
    render_task_input,
    split_command_text,
    write_summary,
)

_DEFAULT_PROXY_ENV = {
    "HTTP_PROXY": "http://127.0.0.1:10809",
    "HTTPS_PROXY": "http://127.0.0.1:10809",
    "ALL_PROXY": "http://127.0.0.1:10809",
    "NO_PROXY": "localhost,127.0.0.1,::1",
}
_COMMUNICATE_POLL_SECONDS = 5.0
_TERMINAL_CLEANUP_GRACE_SECONDS = 15.0
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _ActiveSidecarProcess:
    process: subprocess.Popen[str]
    kill_requested: bool = False


@dataclass(slots=True)
class _TerminalStreamSnapshot:
    kind: str
    event_ts: str
    sdk_thread_id: str | None
    final_response: str
    failure_message: str | None
    usage: object | None
    item_count: int


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_event_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _terminal_snapshot_is_stale(snapshot: _TerminalStreamSnapshot, grace_seconds: float) -> bool:
    event_time = _parse_event_timestamp(snapshot.event_ts)
    if event_time is None:
        return False
    return (datetime.now(timezone.utc) - event_time).total_seconds() >= grace_seconds


def _last_nonempty_assistant_text(events: list[object]) -> str:
    for event in reversed(events):
        if getattr(event, "kind", None) not in {"assistant.completed", "assistant.delta"}:
            continue
        text = str(getattr(event, "text", "") or "").strip()
        if text:
            return text
    return ""


def _pending_questions_from_text(text: str, *, task_id: str) -> tuple[list[QuestionItem], dict[str, object] | None]:
    question_blocks = parse_question_capsules(normalize_log_text(text))
    pending_questions = [
        QuestionItem(
            question_set_id=str(
                block.get("question_set_id")
                or block.get("question_id")
                or f"question_{task_id}"
            ),
            question_id=str(block.get("question_id") or f"question_{index + 1}"),
            question_type=str(block.get("question_type") or ("single_choice" if block.get("choices") else "short_text")),
            question_text=str(block.get("question_text") or ""),
            required=bool(block.get("required", True)),
            choices=list(block.get("choices", [])),
            choice_labels=dict(block.get("choice_labels", {})),
        )
        for index, block in enumerate(question_blocks)
        if str(block.get("question_text") or "").strip()
    ]
    return pending_questions, (question_blocks[-1] if question_blocks else None)


def _load_terminal_snapshot(path: Path) -> _TerminalStreamSnapshot | None:
    try:
        events = load_stream_events(path)
    except Exception:
        return None
    if not events:
        return None

    terminal_event = next((event for event in reversed(events) if event.kind in {"turn.completed", "turn.failed"}), None)
    if terminal_event is None:
        return None

    final_response = _last_nonempty_assistant_text(events)
    sdk_thread_id = str(terminal_event.payload.get("sdk_thread_id") or "").strip() or None
    if sdk_thread_id is None:
        for event in reversed(events):
            candidate = str(event.payload.get("sdk_thread_id") or "").strip()
            if candidate:
                sdk_thread_id = candidate
                break
    usage = terminal_event.payload.get("usage") if terminal_event.kind == "turn.completed" else None
    failure_message = (terminal_event.text or "").strip() or None
    item_count = sum(1 for event in events if event.status == "completed")
    return _TerminalStreamSnapshot(
        kind=terminal_event.kind,
        event_ts=terminal_event.ts,
        sdk_thread_id=sdk_thread_id,
        final_response=final_response,
        failure_message=failure_message,
        usage=usage,
        item_count=item_count,
    )


class CodexSdkAdapter(WorkerAdapter):
    """Runs one Codex SDK turn inside a short-lived Node sidecar process."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or AppConfig()
        self._lock = Lock()
        self._active_processes: dict[str, _ActiveSidecarProcess] = {}

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        started_at = _timestamp()
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        thread_dir = run_path.parent.parent
        prompt_path = run_path / "prompt.txt"
        artifacts_dir = run_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        incoming_attachments_json = run_path / "incoming_attachments.json"
        stdout_path = run_path / "stdout.log"
        stderr_path = run_path / "stderr.log"
        summary_path = run_path / "summary.md"
        sidecar_response_path = run_path / "sdk_turn.json"
        sidecar_request_path = run_path / "sidecar_request.json"
        stream_events_path = run_path / STREAM_EVENTS_FILENAME
        process: subprocess.Popen[str] | None = None

        try:
            cwd = prepare_task_cwd(task, auto_create_workdir=self._config.auto_create_workdir)
            incoming_attachments_json.write_text(
                json.dumps(_incoming_attachment_payload(task), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            prompt_text = render_task_input(task, "codex")
            runtime_hint = _runtime_prompt_hint(
                task=task,
                cwd=cwd,
                run_path=run_path,
                artifacts_dir=artifacts_dir,
                incoming_attachments_json=incoming_attachments_json,
            )
            full_prompt = f"{prompt_text.rstrip()}\n\n{runtime_hint}\n"
            prompt_path.write_text(full_prompt, encoding="utf-8")

            request = {
                "action": "reply" if task.run_mode == "resume" else "start",
                "prompt": full_prompt,
                "thread_id": task.backend_session_id,
                "cwd": str(cwd),
                "model": self._resolve_profile_model(task.profile),
                "sandbox_mode": self._sandbox_mode(task.permission),
                "approval_policy": "never",
                "skip_git_repo_check": True,
                "web_search_mode": "live" if self._config.enable_web_search else "disabled",
                "codex_path_override": self._codex_path_override(),
                "mail_thread_id": task.thread_id,
                "task_id": task.task_id,
                "stream_path": str(stream_events_path),
            }
            sidecar_request_path.write_text(
                json.dumps(request, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            env = self._sidecar_env()
            env["MAIL_RUNNER_WORKDIR"] = str(cwd)
            env["MAIL_RUNNER_RUN_DIR"] = str(run_path)
            env["MAIL_RUNNER_ARTIFACTS_DIR"] = str(artifacts_dir)
            env["MAIL_RUNNER_INCOMING_ATTACHMENTS_JSON"] = str(incoming_attachments_json)
            env["MAIL_RUNNER_PROMPT_PATH"] = str(prompt_path)
            env["MAIL_RUNNER_MAIL_THREAD_ID"] = task.thread_id
            env["MAIL_RUNNER_TASK_ID"] = task.task_id
            command = self._sidecar_command()
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if WINDOWS else 0
            process = subprocess.Popen(
                command,
                cwd=str(self._sidecar_workdir()),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
                start_new_session=not WINDOWS,
            )
            write_process_record(
                run_path,
                pid=process.pid,
                task_id=task.task_id,
                thread_id=task.thread_id,
                started_at=started_at,
                repo_path=task.repo_path,
                workdir=str(cwd),
                command=command,
            )
            with self._lock:
                self._active_processes[task.task_id] = _ActiveSidecarProcess(process=process)
            communicate_input: str | None = json.dumps(request, ensure_ascii=False) + "\n"
            raw_stdout = ""
            raw_stderr = ""
            recovered_payload: dict[str, object] | None = None
            recovered_failure_message: str | None = None
            while True:
                try:
                    raw_stdout, raw_stderr = process.communicate(
                        communicate_input,
                        timeout=_COMMUNICATE_POLL_SECONDS,
                    )
                    break
                except subprocess.TimeoutExpired as exc:
                    communicate_input = None
                    raw_stdout = str(exc.stdout or raw_stdout or "")
                    raw_stderr = str(exc.stderr or raw_stderr or "")
                    terminal_snapshot = _load_terminal_snapshot(stream_events_path)
                    if terminal_snapshot is None:
                        continue
                    if not _terminal_snapshot_is_stale(terminal_snapshot, _TERMINAL_CLEANUP_GRACE_SECONDS):
                        continue
                    LOGGER.warning(
                        "Codex SDK sidecar cleanup timed out after terminal event. "
                        "task_id=%s pid=%s event=%s event_ts=%s",
                        task.task_id,
                        process.pid,
                        terminal_snapshot.kind,
                        terminal_snapshot.event_ts,
                    )
                    self._terminate_process_tree(process)
                    timeout_note = (
                        "Codex SDK sidecar did not exit after terminal event "
                        f"{terminal_snapshot.kind} at {terminal_snapshot.event_ts}; "
                        f"task_id={task.task_id}; pid={process.pid}; forced shutdown.\n"
                    )
                    raw_stderr = timeout_note + raw_stderr
                    if terminal_snapshot.kind == "turn.completed":
                        recovered_payload = {
                            "thread_id": terminal_snapshot.sdk_thread_id,
                            "final_response": terminal_snapshot.final_response,
                            "usage": terminal_snapshot.usage,
                            "item_count": terminal_snapshot.item_count,
                            "recovered_from_terminal_stream": True,
                        }
                    else:
                        recovered_failure_message = (
                            terminal_snapshot.failure_message or "Codex SDK turn failed before sidecar shutdown."
                        )
                    break
            with self._lock:
                active = self._active_processes.pop(task.task_id, None)
            killed = bool(active and active.kill_requested)
            finished_at = _timestamp()

            if killed:
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text(raw_stderr or "Codex SDK sidecar was killed.\n", encoding="utf-8")
                write_summary(
                    path=summary_path,
                    summary_line="Codex SDK task was killed.",
                    backend_label="Codex SDK",
                    command_text=" ".join(command),
                    cwd=cwd,
                    exit_code=None,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    error_message="Codex SDK task was killed.",
                )
                return RunResult(
                    task_id=task.task_id,
                    thread_id=task.thread_id,
                    backend=task.backend,
                    status=RUN_STATUS_KILLED,
                    exit_code=None,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
                    stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
                    summary_file=summary_path.relative_to(thread_dir).as_posix(),
                    artifacts_dir=f"runs/{task.task_id}/artifacts",
                    changed_files=[],
                    tests_passed=None,
                    error_type="killed",
                    error_message="Codex SDK task was killed.",
                    backend_session_id=task.backend_session_id,
                    backend_session_resumable=bool(task.backend_session_id),
                    backend_transport=task.backend_transport,
                )

            if recovered_failure_message is not None:
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text(raw_stderr or (recovered_failure_message + "\n"), encoding="utf-8")
                error_message = recovered_failure_message
                write_summary(
                    path=summary_path,
                    summary_line=error_message,
                    backend_label="Codex SDK",
                    command_text=" ".join(command),
                    cwd=cwd,
                    exit_code=1,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    error_message=error_message,
                )
                return RunResult(
                    task_id=task.task_id,
                    thread_id=task.thread_id,
                    backend=task.backend,
                    status=RUN_STATUS_FAILED,
                    exit_code=1,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
                    stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
                    summary_file=summary_path.relative_to(thread_dir).as_posix(),
                    artifacts_dir=f"runs/{task.task_id}/artifacts",
                    changed_files=[],
                    tests_passed=None,
                    error_type="terminal_cleanup_timeout",
                    error_message=error_message,
                    backend_session_id=task.backend_session_id,
                    backend_session_resumable=bool(task.backend_session_id),
                    backend_transport=task.backend_transport,
                )

            if recovered_payload is None and process.returncode != 0:
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text(raw_stderr, encoding="utf-8")
                error_message = extract_error_excerpt(raw_stderr, raw_stdout) or (
                    f"Codex SDK sidecar exited with code {process.returncode}."
                )
                write_summary(
                    path=summary_path,
                    summary_line=error_message,
                    backend_label="Codex SDK",
                    command_text=" ".join(command),
                    cwd=cwd,
                    exit_code=process.returncode,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    error_message=error_message,
                )
                return RunResult(
                    task_id=task.task_id,
                    thread_id=task.thread_id,
                    backend=task.backend,
                    status=RUN_STATUS_FAILED,
                    exit_code=process.returncode,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
                    stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
                    summary_file=summary_path.relative_to(thread_dir).as_posix(),
                    artifacts_dir=f"runs/{task.task_id}/artifacts",
                    changed_files=[],
                    tests_passed=None,
                    error_type="sidecar_exit",
                    error_message=error_message,
                    backend_session_id=task.backend_session_id,
                    backend_session_resumable=bool(task.backend_session_id),
                    backend_transport=task.backend_transport,
                )

            payload = recovered_payload if recovered_payload is not None else json.loads(raw_stdout or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Codex SDK sidecar returned a non-object payload")
            sidecar_response_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            final_response = str(payload.get("final_response") or "").strip()
            structured_result = parse_run_result_capsule(final_response)
            visible_response = strip_run_result_capsules(final_response)
            pending_questions, question_block = _pending_questions_from_text(final_response, task_id=task.task_id)
            stdout_path.write_text((visible_response + "\n") if visible_response else "", encoding="utf-8")
            stderr_path.write_text(raw_stderr, encoding="utf-8")
            if pending_questions:
                status = RUN_STATUS_AWAITING_USER_INPUT
                question_id = str(question_block.get("question_id") or "").strip() or None
                question_text = str(question_block.get("question_text") or "").strip() or None
                pending_choices = list(question_block.get("choices", []))
                question_set_id = (
                    str(question_block.get("question_set_id") or "").strip()
                    or pending_questions[0].question_set_id
                )
                changed_files: list[str] = []
                tests_passed = None
                summary_line = question_text or "Codex SDK is awaiting user input."
                primary_output = None
            else:
                status = RUN_STATUS_SUCCESS
                question_id = None
                question_text = None
                pending_choices = []
                question_set_id = None
                changed_files = list(structured_result.changed_files) if structured_result else []
                tests_passed = structured_result.tests_passed if structured_result else None
                summary_line = extract_summary_line(visible_response) or "Codex SDK turn completed successfully."
                primary_output = extract_output_block(visible_response)
            write_summary(
                path=summary_path,
                summary_line=summary_line,
                backend_label="Codex SDK",
                command_text=" ".join(command),
                cwd=cwd,
                exit_code=0,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error_message=None,
                primary_output=primary_output,
            )
            thread_id = str(payload.get("thread_id") or task.backend_session_id or "").strip() or None
            return RunResult(
                task_id=task.task_id,
                thread_id=task.thread_id,
                backend=task.backend,
                status=status,
                exit_code=0,
                started_at=started_at,
                finished_at=finished_at,
                stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
                stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
                summary_file=summary_path.relative_to(thread_dir).as_posix(),
                artifacts_dir=f"runs/{task.task_id}/artifacts",
                changed_files=changed_files,
                tests_passed=tests_passed,
                error_type=None,
                error_message=None,
                question_id=question_id,
                question_text=question_text,
                pending_choices=pending_choices,
                question_set_id=question_set_id,
                pending_questions=pending_questions,
                backend_session_id=thread_id,
                backend_session_resumable=bool(thread_id),
                backend_transport=task.backend_transport,
            )
        except Exception as exc:
            with self._lock:
                self._active_processes.pop(task.task_id, None)
            finished_at = _timestamp()
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            error_message = extract_error_excerpt(
                stderr_path.read_text(encoding="utf-8", errors="replace"),
                "",
            ) or f"{type(exc).__name__}: {exc}"
            write_summary(
                path=summary_path,
                summary_line=error_message,
                backend_label="Codex SDK",
                command_text=" ".join(self._sidecar_command()),
                cwd=Path(task.repo_path),
                exit_code=1,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error_message=error_message,
            )
            return RunResult(
                task_id=task.task_id,
                thread_id=task.thread_id,
                backend=task.backend,
                status=RUN_STATUS_FAILED,
                exit_code=1,
                started_at=started_at,
                finished_at=finished_at,
                stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
                stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
                summary_file=summary_path.relative_to(thread_dir).as_posix(),
                artifacts_dir=f"runs/{task.task_id}/artifacts",
                changed_files=[],
                tests_passed=None,
                error_type=type(exc).__name__,
                error_message=error_message,
                backend_transport=task.backend_transport,
            )
        finally:
            with self._lock:
                self._active_processes.pop(task.task_id, None)
            if process is not None and (process.poll() is not None or process.returncode is not None):
                remove_process_record(run_path)

    def kill(self, task_id: str) -> bool:
        with self._lock:
            active = self._active_processes.get(task_id)
            if active is None:
                return False
            active.kill_requested = True
            process = active.process

        if process.poll() is not None:
            return True

        if WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                check=False,
                capture_output=True,
                text=True,
            )
            return True

        try:
            os.killpg(process.pid, 15)
        except ProcessLookupError:
            return True
        except Exception:
            process.kill()
        return True

    def _resolve_profile_model(self, profile: str | None) -> str | None:
        if profile is None:
            return None
        profile_name = profile.strip().lower()
        mapping = {key.strip().lower(): value for key, value in self._config.codex_profile_models.items()}
        if profile_name not in mapping:
            raise ValueError(f"Codex profile mapping is missing for profile '{profile_name}'")
        return mapping[profile_name]

    def _sandbox_mode(self, permission: str | None) -> str:
        if permission == "highest":
            return "danger-full-access"
        return "workspace-write"

    def _codex_path_override(self) -> str | None:
        command_text = (self._config.codex_command or "").strip()
        if not command_text or command_text.lower() == "demo":
            return None
        parts = split_command_text(command_text)
        return parts[0] if parts else None

    def _sidecar_workdir(self) -> Path:
        if self._config.codex_sdk_sidecar_workdir.strip():
            return Path(self._config.codex_sdk_sidecar_workdir)
        return PROJECT_ROOT

    def _sidecar_command(self) -> list[str]:
        command_text = (self._config.codex_sdk_sidecar_command or "").strip()
        if command_text:
            return split_command_text(command_text)
        script_path = PROJECT_ROOT / "scripts" / "codex_sdk_sidecar" / "dist" / "index.js"
        return ["node", str(script_path)]

    def _sidecar_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for name, value in _DEFAULT_PROXY_ENV.items():
            if not env.get(name, "").strip():
                env[name] = value
        return env

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            try:
                process.kill()
            except Exception:
                pass
        try:
            process.wait(timeout=5)
        except Exception:
            pass
