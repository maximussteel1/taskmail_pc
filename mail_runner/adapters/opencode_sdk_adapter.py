"""OpenCode SDK adapter backed by a short-lived local opencode serve process."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

from opencode_ai import Opencode

from ..models import QuestionItem, TaskSnapshot
from ..opencode_sdk_common import (
    ServerHandle,
    extract_reply_text,
    latest_assistant_message,
    part_to_record,
    resolve_profile_provider_model,
    start_server,
    stop_server,
    wait_for_server,
)
from ..run_result_capsule import parse_run_result_capsule, strip_run_result_capsules
from ..state_capsule import parse_question_capsules
from ..status import RUN_STATUS_AWAITING_USER_INPUT, RUN_STATUS_FAILED, RUN_STATUS_KILLED, RUN_STATUS_SUCCESS
from .base import WorkerAdapter
from .cli_common import (
    _incoming_attachment_payload,
    _runtime_prompt_hint,
    build_run_result,
    extract_error_excerpt,
    extract_output_block,
    extract_summary_line,
    normalize_log_text,
    prepare_task_cwd,
    render_task_input,
    resolve_command_prefix,
    write_summary,
)
from .opencode_adapter import OpenCodeAdapter

_SERVER_STARTUP_TIMEOUT_SECONDS = 30
_MIN_TURN_TIMEOUT_SECONDS = 180.0


@dataclass(slots=True)
class _ActiveServerRun:
    server: ServerHandle
    session_id: str | None = None
    kill_requested: bool = False


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class OpenCodeSdkAdapter(OpenCodeAdapter, WorkerAdapter):
    """Runs one OpenCode SDK turn through a temporary local opencode server."""

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._sdk_lock = Lock()
        self._active_server_runs: dict[str, _ActiveServerRun] = {}

    def run(self, task: TaskSnapshot, run_dir: str):
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
        sdk_turn_path = run_path / "sdk_turn.json"

        server: ServerHandle | None = None
        session_id: str | None = task.backend_session_id

        try:
            resolved = resolve_command_prefix(self._configured_command(), self._default_executable())
            cwd = prepare_task_cwd(task, auto_create_workdir=self._config.auto_create_workdir)
            incoming_attachments_json.write_text(
                json.dumps(_incoming_attachment_payload(task), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            prompt_text = render_task_input(task, self.backend)
            runtime_hint = _runtime_prompt_hint(
                task=task,
                cwd=cwd,
                run_path=run_path,
                artifacts_dir=artifacts_dir,
                incoming_attachments_json=incoming_attachments_json,
            )
            full_prompt = f"{prompt_text.rstrip()}\n\n{runtime_hint}\n"
            prompt_path.write_text(full_prompt, encoding="utf-8")

            server_env = self._build_subprocess_env(
                task=task,
                resolved=resolved,
                cwd=cwd,
                run_path=run_path,
                artifacts_dir=artifacts_dir,
                incoming_attachments_json=incoming_attachments_json,
                prompt_path=prompt_path,
            )
            server = start_server(
                opencode_command=self._configured_command(),
                workspace=cwd,
                output_dir=run_path,
                port=None,
                env=server_env,
            )
            with self._sdk_lock:
                self._active_server_runs[task.task_id] = _ActiveServerRun(server=server)
            wait_for_server(server, timeout_seconds=_SERVER_STARTUP_TIMEOUT_SECONDS)

            with Opencode(base_url=server.base_url, timeout=self._turn_timeout_seconds(task), max_retries=0) as client:
                providers_payload = client.app.providers()
                configured_model = self.resolve_profile_model(task.profile)
                provider_id, model_id = resolve_profile_provider_model(providers_payload, configured_model)

                if task.run_mode == "resume":
                    if not task.backend_session_id:
                        raise ValueError("OpenCode SDK resume requires backend_session_id.")
                    session_id = task.backend_session_id
                else:
                    session = client.session.create(extra_body={"title": self._session_title(task)})
                    session_id = str(session.id).strip()

                with self._sdk_lock:
                    active = self._active_server_runs.get(task.task_id)
                    if active is not None:
                        active.session_id = session_id

                chat_kwargs = {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "parts": [{"type": "text", "text": full_prompt}],
                }
                sdk_tools = self._sdk_tools()
                if sdk_tools is not None:
                    chat_kwargs["tools"] = sdk_tools
                client.session.chat(session_id, **chat_kwargs)
                messages = list(client.session.messages(session_id))

            assistant_message = latest_assistant_message(messages)
            assistant_parts = list(getattr(assistant_message, "parts", []) or [])
            reply_text = extract_reply_text(assistant_parts)
            server_stderr = self._read_server_stderr(server)
            sdk_turn_path.write_text(
                json.dumps(
                    {
                        "base_url": server.base_url,
                        "port": server.port,
                        "session_id": session_id,
                        "provider_id": provider_id,
                        "model_id": model_id,
                        "message_count": len(messages),
                        "assistant_parts": [part_to_record(part) for part in assistant_parts],
                        "assistant_reply": reply_text,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            structured_result = parse_run_result_capsule(reply_text)
            visible_reply = strip_run_result_capsules(reply_text)
            stdout_path.write_text((visible_reply + "\n") if visible_reply else "", encoding="utf-8")
            stderr_path.write_text(server_stderr, encoding="utf-8")

            question_blocks = parse_question_capsules(normalize_log_text(reply_text))
            pending_questions = [
                QuestionItem(
                    question_set_id=str(
                        block.get("question_set_id")
                        or block.get("question_id")
                        or f"question_{task.task_id}"
                    ),
                    question_id=str(block.get("question_id") or f"question_{index + 1}"),
                    question_type=str(
                        block.get("question_type") or ("single_choice" if block.get("choices") else "short_text")
                    ),
                    question_text=str(block.get("question_text") or ""),
                    required=bool(block.get("required", True)),
                    choices=list(block.get("choices", [])),
                    choice_labels=dict(block.get("choice_labels", {})),
                )
                for index, block in enumerate(question_blocks)
                if str(block.get("question_text") or "").strip()
            ]
            question_block = question_blocks[-1] if question_blocks else None

            active = self._pop_active_run(task.task_id)
            killed = bool(active and active.kill_requested)
            session_id = (active.session_id if active is not None else None) or session_id
            finished_at = _timestamp()
            command_text = f"OpenCode SDK via {server.base_url} ({provider_id}/{model_id})"

            if killed:
                return self._build_killed_result(
                    task=task,
                    thread_dir=thread_dir,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    summary_path=summary_path,
                    cwd=cwd,
                    command_text=command_text,
                    started_at=started_at,
                    finished_at=finished_at,
                    session_id=session_id,
                )

            changed_files = list(structured_result.changed_files) if structured_result else []
            tests_passed = structured_result.tests_passed if structured_result else None
            if pending_questions:
                question_id = str(question_block.get("question_id") or "").strip() or None
                question_text = str(question_block.get("question_text") or "").strip() or None
                pending_choices = list(question_block.get("choices", []))
                question_set_id = (
                    str(question_block.get("question_set_id") or "").strip()
                    or pending_questions[0].question_set_id
                )
                summary_line = question_text or "OpenCode SDK is awaiting user input."
                write_summary(
                    path=summary_path,
                    summary_line=summary_line,
                    backend_label="OpenCode SDK",
                    command_text=command_text,
                    cwd=cwd,
                    exit_code=0,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    error_message=None,
                )
                return build_run_result(
                    task=task,
                    thread_dir=thread_dir,
                    status=RUN_STATUS_AWAITING_USER_INPUT,
                    exit_code=0,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    summary_path=summary_path,
                    changed_files=[],
                    tests_passed=None,
                    error_type=None,
                    error_message=None,
                    question_id=question_id,
                    question_text=question_text,
                    pending_choices=pending_choices,
                    question_set_id=question_set_id,
                    pending_questions=pending_questions,
                    backend_session_id=session_id,
                    backend_session_resumable=bool(session_id),
                    backend_transport=task.backend_transport,
                )

            summary_line = extract_summary_line(visible_reply) or "OpenCode SDK turn completed successfully."
            write_summary(
                path=summary_path,
                summary_line=summary_line,
                backend_label="OpenCode SDK",
                command_text=command_text,
                cwd=cwd,
                exit_code=0,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error_message=None,
                primary_output=extract_output_block(visible_reply),
            )
            return build_run_result(
                task=task,
                thread_dir=thread_dir,
                status=RUN_STATUS_SUCCESS,
                exit_code=0,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                summary_path=summary_path,
                changed_files=changed_files,
                tests_passed=tests_passed,
                error_type=None,
                error_message=None,
                backend_session_id=session_id,
                backend_session_resumable=bool(session_id),
                backend_transport=task.backend_transport,
            )
        except Exception as exc:
            active = self._pop_active_run(task.task_id)
            killed = bool(active and active.kill_requested)
            session_id = (active.session_id if active is not None else None) or session_id or task.backend_session_id
            finished_at = _timestamp()
            stdout_path.write_text("", encoding="utf-8")
            stderr_text = self._read_server_stderr(server) or f"{type(exc).__name__}: {exc}\n"
            stderr_path.write_text(stderr_text, encoding="utf-8")

            if killed:
                return self._build_killed_result(
                    task=task,
                    thread_dir=thread_dir,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    summary_path=summary_path,
                    cwd=Path(task.repo_path),
                    command_text=self._configured_command() or self._default_executable(),
                    started_at=started_at,
                    finished_at=finished_at,
                    session_id=session_id,
                )

            error_message = extract_error_excerpt(stderr_text, "") or f"{type(exc).__name__}: {exc}"
            write_summary(
                path=summary_path,
                summary_line=error_message,
                backend_label="OpenCode SDK",
                command_text=self._configured_command() or self._default_executable(),
                cwd=Path(task.repo_path),
                exit_code=1,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error_message=error_message,
            )
            return build_run_result(
                task=task,
                thread_dir=thread_dir,
                status=RUN_STATUS_FAILED,
                exit_code=1,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                summary_path=summary_path,
                changed_files=[],
                tests_passed=None,
                error_type=type(exc).__name__,
                error_message=error_message,
                backend_session_id=session_id,
                backend_session_resumable=bool(session_id),
                backend_transport=task.backend_transport,
            )
        finally:
            self._pop_active_run(task.task_id)
            if server is not None:
                stop_server(server)

    def kill(self, task_id: str) -> bool:
        with self._sdk_lock:
            active = self._active_server_runs.get(task_id)
            if active is None:
                return False
            active.kill_requested = True
            server = active.server
        stop_server(server)
        return True

    def _build_killed_result(
        self,
        *,
        task: TaskSnapshot,
        thread_dir: Path,
        stdout_path: Path,
        stderr_path: Path,
        summary_path: Path,
        cwd: Path,
        command_text: str,
        started_at: str,
        finished_at: str,
        session_id: str | None,
    ):
        error_message = "OpenCode SDK task was killed."
        write_summary(
            path=summary_path,
            summary_line=error_message,
            backend_label="OpenCode SDK",
            command_text=command_text,
            cwd=cwd,
            exit_code=None,
            started_at=started_at,
            finished_at=finished_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error_message=error_message,
        )
        return build_run_result(
            task=task,
            thread_dir=thread_dir,
            status=RUN_STATUS_KILLED,
            exit_code=None,
            started_at=started_at,
            finished_at=finished_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            summary_path=summary_path,
            changed_files=[],
            tests_passed=None,
            error_type="killed",
            error_message=error_message,
            backend_session_id=session_id,
            backend_session_resumable=bool(session_id),
            backend_transport=task.backend_transport,
        )

    def _pop_active_run(self, task_id: str) -> _ActiveServerRun | None:
        with self._sdk_lock:
            return self._active_server_runs.pop(task_id, None)

    def _read_server_stderr(self, server: ServerHandle | None) -> str:
        if server is None or not server.stderr_log.exists():
            return ""
        return server.stderr_log.read_text(encoding="utf-8", errors="replace")

    def _sdk_tools(self) -> dict[str, bool] | None:
        if not self._config.enable_web_search:
            return None
        return {"websearch": True}

    def _turn_timeout_seconds(self, task: TaskSnapshot) -> float:
        requested = max(float(task.timeout_minutes) * 60.0, 0.0)
        return max(_MIN_TURN_TIMEOUT_SECONDS, requested + 30.0)
