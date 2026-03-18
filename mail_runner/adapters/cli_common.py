"""Shared subprocess helpers for real CLI-backed adapters."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

from ..config import AppConfig
from ..models import QuestionItem, RunResult, TaskSnapshot
from ..run_result_capsule import parse_run_result_capsule, strip_run_result_capsules
from ..state_capsule import parse_question_capsules
from ..status import BACKEND_TRANSPORT_CLI
from ..status import RUN_STATUS_AWAITING_USER_INPUT, RUN_STATUS_FAILED, RUN_STATUS_KILLED, RUN_STATUS_PAUSED, RUN_STATUS_SUCCESS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DEMO_COMMAND = "demo"
WINDOWS = os.name == "nt"
SUMMARY_TAIL_LINES = 8
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TIMESTAMP_LOG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T.*\b(?:ERROR|WARN|INFO)\b", re.IGNORECASE)
_LS_LINE_RE = re.compile(r"^[dl-][rwx-]{9}\b")
_NUMBER_ONLY_RE = re.compile(r"^[\d,\s]+$")
_SEPARATOR_LINE_RE = re.compile(r"^[=\-_*]{3,}$")
_SESSION_ID_RE = re.compile(r"(?im)^\s*session id:\s*([0-9a-z_-]{8,})\s*$")
DEMO_SCRIPT = (
    "import os,sys,time\n"
    "backend=os.environ.get('MAIL_RUNNER_DEMO_BACKEND','demo')\n"
    "sleep_seconds=float(os.environ.get('MAIL_RUNNER_DEMO_SLEEP','0'))\n"
    "prompt_path=os.environ.get('MAIL_RUNNER_PROMPT_PATH','')\n"
    "question_text=os.environ.get('MAIL_RUNNER_DEMO_QUESTION_TEXT','').strip()\n"
    "question_choices=os.environ.get('MAIL_RUNNER_DEMO_QUESTION_CHOICES','').strip()\n"
    "question_id=os.environ.get('MAIL_RUNNER_DEMO_QUESTION_ID','').strip()\n"
    "session_id=os.environ.get('MAIL_RUNNER_DEMO_SESSION_ID','').strip()\n"
    "result_changed_files=os.environ.get('MAIL_RUNNER_DEMO_CHANGED_FILES','').strip()\n"
    "result_tests_passed=os.environ.get('MAIL_RUNNER_DEMO_TESTS_PASSED','').strip()\n"
    "result_error_type=os.environ.get('MAIL_RUNNER_DEMO_ERROR_TYPE','').strip()\n"
    "result_error_message=os.environ.get('MAIL_RUNNER_DEMO_ERROR_MESSAGE','').strip()\n"
    "exit_code=int(os.environ.get('MAIL_RUNNER_DEMO_EXIT_CODE','0') or '0')\n"
    "stdin_text=sys.stdin.read() if not sys.stdin.closed else ''\n"
    "print(f'Demo backend {backend} starting')\n"
    "if prompt_path:\n"
    "    print(f'Prompt file: {prompt_path}')\n"
    "if stdin_text:\n"
    "    print(f'Stdin chars: {len(stdin_text)}')\n"
    "if session_id:\n"
    "    print(f'session id: {session_id}')\n"
    "print('Demo backend is running')\n"
    "time.sleep(max(0.0, sleep_seconds))\n"
    "if question_text:\n"
    "    print('---TASK-QUESTION-BEGIN---')\n"
    "    print(f'question_id: {question_id}')\n"
    "    print(f'question_text: {question_text}')\n"
    "    print(f'choices: {question_choices}')\n"
    "    print('---TASK-QUESTION-END---')\n"
    "    sys.exit(0)\n"
    "print(f'Demo backend {backend} finished')\n"
    "if any((result_changed_files, result_tests_passed, result_error_type, result_error_message)):\n"
    "    print('---TASK-RUN-RESULT-BEGIN---')\n"
    "    print(f'changed_files: {result_changed_files}')\n"
    "    print(f'tests_passed: {result_tests_passed}')\n"
    "    print(f'error_type: {result_error_type}')\n"
    "    print(f'error_message: {result_error_message}')\n"
    "    print('---TASK-RUN-RESULT-END---')\n"
    "sys.exit(exit_code)\n"
)
_NOISE_PREFIXES = (
    "OpenAI Codex",
    "workdir:",
    "model:",
    "provider:",
    "approval:",
    "sandbox:",
    "reasoning effort:",
    "reasoning summaries:",
    "session id:",
    "mcp startup:",
    "Reconnecting...",
    "warning: Falling back from WebSockets to HTTPS transport.",
    "Prompt file:",
    "Stdin chars:",
)
_NOISE_EXACT = {
    "--------",
    "user",
    "codex",
    "tokens used",
}
_SUMMARY_CHATTER_EXACT = {
    "analysis complete.",
    "need anything else?",
}
_SUMMARY_CHATTER_PREFIXES = (
    "continue if you have next steps",
    "the analysis task is complete",
    "the analysis task was completed successfully",
    "no work is pending",
    "let me know if you'd like",
)
_META_SECTION_HEADINGS = {
    "goal",
    "instructions",
    "discoveries",
    "accomplished",
    "relevant files",
    "relevant files / directories",
}


@dataclass(slots=True)
class _ResolvedCommand:
    prefix: list[str]
    display_prefix: str
    is_demo: bool


@dataclass(slots=True)
class _ActiveProcess:
    process: subprocess.Popen[str]
    kill_requested: bool = False


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _format_acceptance(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def _format_attachments(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def _render_prompt(task: TaskSnapshot, backend: str) -> str:
    template_path = TEMPLATES_DIR / f"{backend}_prompt.txt"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        task_id=task.task_id,
        thread_id=task.thread_id,
        profile=task.profile or "",
        permission=task.permission or "default",
        repo_path=task.repo_path,
        workdir=task.workdir or "",
        mode=task.mode,
        timeout_minutes=task.timeout_minutes,
        task_text=task.task_text,
        acceptance=_format_acceptance(task.acceptance),
        attachments=_format_attachments(task.attachments),
    )


def _incoming_attachment_payload(task: TaskSnapshot) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for item in task.attachments:
        path = Path(str(item))
        payload.append({"path": str(path), "name": path.name})
    return payload


def _runtime_prompt_hint(
    *,
    task: TaskSnapshot,
    cwd: Path,
    run_path: Path,
    artifacts_dir: Path,
    incoming_attachments_json: Path,
) -> str:
    lines = [
        "Runtime Mail Paths:",
        f"- MAIL_RUNNER_WORKDIR: {cwd}",
        f"- MAIL_RUNNER_RUN_DIR: {run_path}",
        f"- MAIL_RUNNER_ARTIFACTS_DIR: {artifacts_dir}",
        f"- MAIL_RUNNER_INCOMING_ATTACHMENTS_JSON: {incoming_attachments_json}",
        "",
        "If you want files or images sent back to the user, create them under MAIL_RUNNER_ARTIFACTS_DIR.",
        "If you need to send files outside that directory, write MAIL_RUNNER_ARTIFACTS_DIR/manifest.json and use absolute paths explicitly.",
        "Only describe image contents if you actually inspected the file contents.",
    ]
    if task.attachments:
        lines.extend(["", "Incoming attachment paths already materialized in the workdir:"])
        lines.extend(f"- {item}" for item in task.attachments)
    return "\n".join(lines).strip()


def render_task_input(task: TaskSnapshot, backend: str) -> str:
    if task.run_mode == "resume":
        return (task.turn_text or "").strip() or "Continue the previous task."
    return _render_prompt(task, backend)


def normalize_log_text(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def _find_session_id_value(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).strip().lower()
            if normalized in {"session_id", "sessionid", "session", "conversation_id", "conversationid"}:
                if isinstance(value, str) and value.strip():
                    return value.strip()
            nested = _find_session_id_value(value)
            if nested:
                return nested
        return None
    if isinstance(payload, list):
        for item in payload:
            nested = _find_session_id_value(item)
            if nested:
                return nested
    return None


def extract_backend_session_id(*texts: str) -> str | None:
    for text in texts:
        match = _SESSION_ID_RE.search(normalize_log_text(text))
        if match:
            return match.group(1).strip()
    for text in texts:
        for raw_line in normalize_log_text(text).splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            session_id = _find_session_id_value(payload)
            if session_id:
                return session_id
    return None


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped in _NOISE_EXACT:
        return True
    if any(stripped.startswith(prefix) for prefix in _NOISE_PREFIXES):
        return True
    lowered = stripped.lower()
    if lowered.endswith(" starting") and lowered.startswith("demo backend "):
        return True
    if lowered == "demo backend is running":
        return True
    if lowered.startswith("$ ") or lowered.startswith("→ ") or lowered.startswith("> build"):
        return True
    if lowered.startswith("total ") or lowered.startswith("mode ") or lowered.startswith("count "):
        return True
    if _TIMESTAMP_LOG_RE.match(stripped):
        return True
    if _LS_LINE_RE.match(stripped):
        return True
    if _NUMBER_ONLY_RE.match(stripped):
        return True
    return False


def _extract_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in normalize_log_text(text).splitlines():
        line = raw_line.strip()
        if not line or _is_noise_line(line):
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def extract_output_block(text: str) -> list[str]:
    blocks = _extract_blocks(text)
    for block in reversed(blocks):
        if _summary_candidates(block):
            return block
    return blocks[-1] if blocks else []


def _looks_like_heading(line: str) -> bool:
    simplified = re.sub(r"[*_`#>\-]", "", line).strip()
    if not simplified.endswith(":"):
        return False
    return len(simplified.removesuffix(":").split()) <= 4


def _normalize_summary_line(line: str) -> str:
    return re.sub(r"[*_`#>\-]", "", line).strip()


def _is_meta_section_heading(line: str) -> bool:
    lowered = _normalize_summary_line(line).removesuffix(":").strip().lower()
    return lowered in _META_SECTION_HEADINGS


def _is_summary_chatter_line(line: str) -> bool:
    lowered = _normalize_summary_line(line).lower()
    if lowered in _SUMMARY_CHATTER_EXACT:
        return True
    return any(lowered.startswith(prefix) for prefix in _SUMMARY_CHATTER_PREFIXES)


def _summary_candidates(block: list[str]) -> list[str]:
    candidates: list[str] = []
    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if _SEPARATOR_LINE_RE.match(stripped):
            continue
        if stripped.startswith("- "):
            continue
        if _looks_like_heading(stripped):
            continue
        if _is_meta_section_heading(stripped):
            continue
        if _is_summary_chatter_line(stripped):
            continue
        candidates.append(stripped)
    return candidates


def extract_summary_line(text: str) -> str | None:
    blocks = _extract_blocks(text)
    for block in reversed(blocks):
        candidates = _summary_candidates(block)
        if candidates:
            return candidates[0]
    if not blocks:
        return None
    return blocks[-1][0]


def extract_error_excerpt(stderr_text: str, stdout_text: str = "") -> str | None:
    for source in (stderr_text, stdout_text):
        normalized = normalize_log_text(source)
        for raw_line in reversed(normalized.splitlines()):
            line = raw_line.strip()
            if not line or _is_noise_line(line):
                continue
            return line
    return None


def split_command_text(command: str) -> list[str]:
    return shlex.split(command, posix=not WINDOWS)


def resolve_command_prefix(configured_command: str, executable_name: str) -> _ResolvedCommand:
    normalized = configured_command.strip()
    if normalized.lower() == DEMO_COMMAND:
        return _ResolvedCommand(prefix=[DEMO_COMMAND], display_prefix=DEMO_COMMAND, is_demo=True)

    if normalized:
        return _ResolvedCommand(
            prefix=split_command_text(normalized),
            display_prefix=normalized,
            is_demo=False,
        )

    candidates = [f"{executable_name}.cmd", executable_name] if WINDOWS else [executable_name]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return _ResolvedCommand(prefix=[resolved], display_prefix=resolved, is_demo=False)
    joined = ", ".join(candidates)
    raise FileNotFoundError(f"Unable to locate backend command. Tried: {joined}")


def resolve_task_cwd(task: TaskSnapshot) -> Path:
    repo_path = Path(task.repo_path)
    if not task.workdir:
        return repo_path
    workdir = Path(task.workdir)
    return workdir if workdir.is_absolute() else (repo_path / workdir)


def prepare_task_cwd(task: TaskSnapshot, *, auto_create_workdir: bool = False) -> Path:
    repo_path = Path(task.repo_path)
    if not repo_path.exists():
        raise FileNotFoundError(f"Task repository path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise NotADirectoryError(f"Task repository path is not a directory: {repo_path}")

    cwd = resolve_task_cwd(task)
    if cwd.exists():
        if not cwd.is_dir():
            raise NotADirectoryError(f"Task working directory is not a directory: {cwd}")
        return cwd

    if not task.workdir or not auto_create_workdir:
        raise FileNotFoundError(f"Task working directory does not exist: {cwd}")

    workdir = Path(task.workdir)
    if workdir.is_absolute():
        raise FileNotFoundError(f"Task working directory does not exist: {cwd}")

    repo_root = repo_path.resolve()
    candidate = cwd.resolve(strict=False)
    if not candidate.is_relative_to(repo_root):
        raise ValueError(f"Auto-created workdir must stay within repo_path: {cwd}")

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def build_demo_command(backend: str) -> tuple[list[str], str]:
    display = f"{DEMO_COMMAND} ({backend})"
    return [sys.executable, "-u", "-c", DEMO_SCRIPT], display


def build_run_result(
    *,
    task: TaskSnapshot,
    thread_dir: Path,
    status: str,
    exit_code: int | None,
    started_at: str,
    finished_at: str,
    stdout_path: Path,
    stderr_path: Path,
    summary_path: Path,
    changed_files: list[str] | None,
    tests_passed: bool | None,
    error_type: str | None,
    error_message: str | None,
    question_id: str | None = None,
    question_text: str | None = None,
    pending_choices: list[str] | None = None,
    question_set_id: str | None = None,
    pending_questions: list[QuestionItem] | None = None,
    backend_session_id: str | None = None,
    backend_session_resumable: bool = False,
    backend_transport: str = BACKEND_TRANSPORT_CLI,
) -> RunResult:
    return RunResult(
        task_id=task.task_id,
        thread_id=task.thread_id,
        backend=task.backend,
        status=status,
        exit_code=exit_code,
        started_at=started_at,
        finished_at=finished_at,
        stdout_file=stdout_path.relative_to(thread_dir).as_posix(),
        stderr_file=stderr_path.relative_to(thread_dir).as_posix(),
        summary_file=summary_path.relative_to(thread_dir).as_posix(),
        artifacts_dir=f"runs/{task.task_id}/artifacts",
        changed_files=list(changed_files or []),
        tests_passed=tests_passed,
        error_type=error_type,
        error_message=error_message,
        question_id=question_id,
        question_text=question_text,
        pending_choices=list(pending_choices or []),
        question_set_id=question_set_id,
        pending_questions=list(pending_questions or []),
        backend_session_id=backend_session_id,
        backend_session_resumable=backend_session_resumable,
        backend_transport=backend_transport,
    )


def write_summary(
    *,
    path: Path,
    summary_line: str,
    backend_label: str,
    command_text: str,
    cwd: Path,
    exit_code: int | None,
    started_at: str,
    finished_at: str,
    stdout_path: Path,
    stderr_path: Path,
    error_message: str | None,
    primary_output: list[str] | None = None,
) -> None:
    sections = [
        summary_line,
        "",
        f"Backend: {backend_label}",
        f"Command: {command_text}",
        f"CWD: {cwd}",
        f"Started At: {started_at}",
        f"Finished At: {finished_at}",
        f"Exit Code: {'' if exit_code is None else exit_code}",
    ]
    if error_message:
        sections.extend(["", f"Error: {error_message}"])
    if primary_output:
        sections.extend(["", "Primary Output:"])
        sections.extend(primary_output)
    stdout_tail = read_tail(stdout_path)
    stderr_tail = read_tail(stderr_path)
    sections.extend(["", "Stdout Tail:"])
    sections.extend(stdout_tail or ["<empty>"])
    sections.extend(["", "Stderr Tail:"])
    sections.extend(stderr_tail or ["<empty>"])
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def read_tail(path: Path, max_lines: int = SUMMARY_TAIL_LINES) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


class BaseCliAdapter:
    """Common subprocess lifecycle for real CLI-backed adapters."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or AppConfig()
        self._lock = Lock()
        self._active_processes: dict[str, _ActiveProcess] = {}

    @property
    def backend(self) -> str:
        raise NotImplementedError

    @property
    def backend_label(self) -> str:
        raise NotImplementedError

    def _configured_command(self) -> str:
        raise NotImplementedError

    def _default_executable(self) -> str:
        raise NotImplementedError

    def _profile_model_map(self) -> dict[str, str]:
        raise NotImplementedError

    def _build_backend_command(
        self,
        *,
        task: TaskSnapshot,
        resolved: _ResolvedCommand,
        prompt_path: Path,
        cwd: Path,
    ) -> tuple[list[str], str | None, str]:
        raise NotImplementedError

    def _build_environment_overrides(
        self,
        *,
        task: TaskSnapshot,
        resolved: _ResolvedCommand,
        cwd: Path,
    ) -> dict[str, str]:
        return {}

    def resolve_profile_model(self, profile: str | None) -> str | None:
        if profile is None:
            return None
        profile_name = profile.strip().lower()
        mapping = {key.strip().lower(): value for key, value in self._profile_model_map().items()}
        if profile_name not in mapping:
            raise ValueError(f"{self.backend_label} profile mapping is missing for profile '{profile_name}'")
        return mapping[profile_name]

    def _extract_backend_session_id(
        self,
        *,
        task: TaskSnapshot,
        resolved: _ResolvedCommand,
        cwd: Path,
        stdout_text: str,
        stderr_text: str,
    ) -> str | None:
        return extract_backend_session_id(stdout_text, stderr_text) or task.backend_session_id

    def _build_subprocess_env(
        self,
        *,
        task: TaskSnapshot,
        resolved: _ResolvedCommand,
        cwd: Path,
        run_path: Path | None = None,
        artifacts_dir: Path | None = None,
        incoming_attachments_json: Path | None = None,
        prompt_path: Path | None = None,
        allow_permission_override: bool = True,
    ) -> dict[str, str] | None:
        env_overrides = self._build_environment_overrides(task=task, resolved=resolved, cwd=cwd)
        resolved_run_path = run_path or cwd
        resolved_artifacts_dir = artifacts_dir or (resolved_run_path / "artifacts")
        resolved_incoming_json = incoming_attachments_json or (resolved_run_path / "incoming_attachments.json")
        env = os.environ.copy()
        env.update(env_overrides)
        env["MAIL_RUNNER_WORKDIR"] = str(cwd)
        env["MAIL_RUNNER_RUN_DIR"] = str(resolved_run_path)
        env["MAIL_RUNNER_ARTIFACTS_DIR"] = str(resolved_artifacts_dir)
        env["MAIL_RUNNER_INCOMING_ATTACHMENTS_JSON"] = str(resolved_incoming_json)
        if resolved.is_demo:
            env["MAIL_RUNNER_DEMO_BACKEND"] = self.backend
            env["MAIL_RUNNER_DEMO_SLEEP"] = str(self._config.mock_sleep_seconds)
            if prompt_path is not None:
                env["MAIL_RUNNER_PROMPT_PATH"] = str(prompt_path)
            env["MAIL_RUNNER_DEMO_SESSION_ID"] = task.backend_session_id or f"demo-session-{self.backend}-{task.thread_id}"
        return env

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
            prompt_path.write_text(f"{prompt_text.rstrip()}\n\n{runtime_hint}\n", encoding="utf-8")
            command, stdin_text, display_command = self._build_backend_command(
                task=task,
                resolved=resolved,
                prompt_path=prompt_path,
                cwd=cwd,
            )
            env = self._build_subprocess_env(
                task=task,
                resolved=resolved,
                cwd=cwd,
                run_path=run_path,
                artifacts_dir=artifacts_dir,
                incoming_attachments_json=incoming_attachments_json,
                prompt_path=prompt_path,
            )

            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if WINDOWS else 0
            with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_handle:
                with stderr_path.open("w", encoding="utf-8", errors="replace") as stderr_handle:
                    process = subprocess.Popen(
                        command,
                        cwd=str(cwd),
                        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        creationflags=creationflags,
                        start_new_session=not WINDOWS,
                    )
                    with self._lock:
                        self._active_processes[task.task_id] = _ActiveProcess(process=process)
                    if stdin_text is not None:
                        process.communicate(stdin_text)
                    else:
                        process.wait()
                    with self._lock:
                        active = self._active_processes.pop(task.task_id, None)
                    killed = bool(active and active.kill_requested)
                    returncode = process.returncode

            finished_at = _timestamp()
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            structured_result = parse_run_result_capsule(stdout_text)
            visible_stdout_text = strip_run_result_capsules(stdout_text)
            if visible_stdout_text != stdout_text.strip():
                stdout_path.write_text(f"{visible_stdout_text}\n" if visible_stdout_text else "", encoding="utf-8")
            primary_output = extract_output_block(visible_stdout_text)
            question_blocks = parse_question_capsules(normalize_log_text(stdout_text))
            pending_questions = [
                QuestionItem(
                    question_set_id=str(
                        block.get("question_set_id")
                        or block.get("question_id")
                        or f"question_{task.task_id}"
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
            question_block = question_blocks[-1] if question_blocks else None
            backend_session_id = self._extract_backend_session_id(
                task=task,
                resolved=resolved,
                cwd=cwd,
                stdout_text=stdout_text,
                stderr_text=stderr_text,
            )
            changed_files = list(structured_result.changed_files) if structured_result else []
            tests_passed = structured_result.tests_passed if structured_result else None
            error_type = structured_result.error_type if structured_result else None
            structured_error_message = structured_result.error_message if structured_result else None
            if killed:
                status = RUN_STATUS_KILLED
                exit_code = None
                error_type = "killed"
                error_message = f"{self.backend_label} task was killed."
                summary_line = error_message
                primary_output = None
                question_id = None
                question_text = None
                pending_choices: list[str] = []
                question_set_id = None
                pending_questions = []
            elif returncode == 0 and pending_questions:
                status = RUN_STATUS_AWAITING_USER_INPUT
                exit_code = 0
                question_id = str(question_block.get("question_id") or "").strip() or None
                question_text = str(question_block.get("question_text") or "").strip() or None
                pending_choices = list(question_block.get("choices", []))
                question_set_id = str(question_block.get("question_set_id") or "").strip() or pending_questions[0].question_set_id
                changed_files = []
                tests_passed = None
                error_type = None
                error_message = None
                summary_line = question_text or f"{self.backend_label} is awaiting user input."
                primary_output = None
            elif returncode == 0:
                status = RUN_STATUS_SUCCESS
                exit_code = 0
                error_type = None
                error_message = None
                summary_line = extract_summary_line(visible_stdout_text) or f"{self.backend_label} command completed successfully."
                question_id = None
                question_text = None
                pending_choices = []
                question_set_id = None
                pending_questions = []
            else:
                status = RUN_STATUS_FAILED
                exit_code = returncode
                error_message = structured_error_message or extract_error_excerpt(stderr_text, visible_stdout_text) or (
                    f"{self.backend_label} command exited with code {returncode}."
                )
                summary_line = error_message
                primary_output = None
                question_id = None
                question_text = None
                pending_choices = []
                question_set_id = None
                pending_questions = []
            backend_session_resumable = bool(backend_session_id) and status != RUN_STATUS_KILLED

            write_summary(
                path=summary_path,
                summary_line=summary_line,
                backend_label=self.backend_label,
                command_text=display_command,
                cwd=cwd,
                exit_code=exit_code,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                error_message=error_message,
                primary_output=primary_output,
            )
            return build_run_result(
                task=task,
                thread_dir=thread_dir,
                status=status,
                exit_code=exit_code,
                started_at=started_at,
                finished_at=finished_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                summary_path=summary_path,
                changed_files=changed_files,
                tests_passed=tests_passed,
                error_type=error_type,
                error_message=error_message,
                question_id=question_id,
                question_text=question_text,
                pending_choices=pending_choices,
                question_set_id=question_set_id,
                pending_questions=pending_questions,
                backend_session_id=backend_session_id,
                backend_session_resumable=backend_session_resumable,
                backend_transport=task.backend_transport,
            )
        except Exception as exc:
            with self._lock:
                self._active_processes.pop(task.task_id, None)
            stderr_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            stdout_path.write_text("", encoding="utf-8")
            finished_at = _timestamp()
            error_message = extract_error_excerpt(
                stderr_path.read_text(encoding="utf-8", errors="replace"),
                "",
            ) or f"{type(exc).__name__}: {exc}"
            write_summary(
                path=summary_path,
                summary_line=error_message,
                backend_label=self.backend_label,
                command_text=self._configured_command() or self._default_executable(),
                cwd=resolve_task_cwd(task),
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
                question_id=None,
                question_text=None,
                pending_choices=[],
                question_set_id=None,
                pending_questions=[],
                backend_session_id=None,
                backend_session_resumable=False,
                backend_transport=task.backend_transport,
            )

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
