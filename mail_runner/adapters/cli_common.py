"""Shared subprocess helpers for real CLI-backed adapters."""

from __future__ import annotations

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
from ..models import RunResult, TaskSnapshot
from ..state_capsule import parse_question_capsule
from ..status import RUN_STATUS_AWAITING_USER_INPUT, RUN_STATUS_FAILED, RUN_STATUS_KILLED, RUN_STATUS_PAUSED, RUN_STATUS_SUCCESS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DEMO_COMMAND = "demo"
WINDOWS = os.name == "nt"
SUMMARY_TAIL_LINES = 8
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TIMESTAMP_LOG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T.*\b(?:ERROR|WARN|INFO)\b", re.IGNORECASE)
_LS_LINE_RE = re.compile(r"^[dl-][rwx-]{9}\b")
_NUMBER_ONLY_RE = re.compile(r"^[\d,\s]+$")
DEMO_SCRIPT = (
    "import os,sys,time\n"
    "backend=os.environ.get('MAIL_RUNNER_DEMO_BACKEND','demo')\n"
    "sleep_seconds=float(os.environ.get('MAIL_RUNNER_DEMO_SLEEP','0'))\n"
    "prompt_path=os.environ.get('MAIL_RUNNER_PROMPT_PATH','')\n"
    "question_text=os.environ.get('MAIL_RUNNER_DEMO_QUESTION_TEXT','').strip()\n"
    "question_choices=os.environ.get('MAIL_RUNNER_DEMO_QUESTION_CHOICES','').strip()\n"
    "question_id=os.environ.get('MAIL_RUNNER_DEMO_QUESTION_ID','').strip()\n"
    "stdin_text=sys.stdin.read() if not sys.stdin.closed else ''\n"
    "print(f'Demo backend {backend} starting')\n"
    "if prompt_path:\n"
    "    print(f'Prompt file: {prompt_path}')\n"
    "if stdin_text:\n"
    "    print(f'Stdin chars: {len(stdin_text)}')\n"
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


def _render_prompt(task: TaskSnapshot, backend: str) -> str:
    template_path = TEMPLATES_DIR / f"{backend}_prompt.txt"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
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


def normalize_log_text(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


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
    return blocks[-1] if blocks else []


def _looks_like_heading(line: str) -> bool:
    simplified = re.sub(r"[*_`#>\-]", "", line).strip()
    if not simplified.endswith(":"):
        return False
    return len(simplified.removesuffix(":").split()) <= 4


def extract_summary_line(text: str) -> str | None:
    block = extract_output_block(text)
    if not block:
        return None
    for line in block:
        if line.startswith("- "):
            continue
        if _looks_like_heading(line):
            continue
        return line
    return block[0]


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
    return repo_path / task.workdir if task.workdir else repo_path


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
    error_message: str | None,
    question_id: str | None = None,
    question_text: str | None = None,
    pending_choices: list[str] | None = None,
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
        artifacts_dir=None,
        changed_files=[],
        tests_passed=None,
        error_message=error_message,
        question_id=question_id,
        question_text=question_text,
        pending_choices=list(pending_choices or []),
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

    def resolve_profile_model(self, profile: str | None) -> str | None:
        if profile is None:
            return None
        profile_name = profile.strip().lower()
        mapping = {key.strip().lower(): value for key, value in self._profile_model_map().items()}
        if profile_name not in mapping:
            raise ValueError(f"{self.backend_label} profile mapping is missing for profile '{profile_name}'")
        return mapping[profile_name]

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        started_at = _timestamp()
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        thread_dir = run_path.parent.parent
        prompt_path = run_path / "prompt.txt"
        stdout_path = run_path / "stdout.log"
        stderr_path = run_path / "stderr.log"
        summary_path = run_path / "summary.md"
        prompt_path.write_text(_render_prompt(task, self.backend), encoding="utf-8")

        try:
            resolved = resolve_command_prefix(self._configured_command(), self._default_executable())
            cwd = resolve_task_cwd(task)
            if not cwd.exists():
                raise FileNotFoundError(f"Task working directory does not exist: {cwd}")
            command, stdin_text, display_command = self._build_backend_command(
                task=task,
                resolved=resolved,
                prompt_path=prompt_path,
                cwd=cwd,
            )
            env = None
            if resolved.is_demo:
                env = os.environ.copy()
                env["MAIL_RUNNER_DEMO_BACKEND"] = self.backend
                env["MAIL_RUNNER_DEMO_SLEEP"] = str(self._config.mock_sleep_seconds)
                env["MAIL_RUNNER_PROMPT_PATH"] = str(prompt_path)

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
            primary_output = extract_output_block(stdout_text)
            question_block = parse_question_capsule(normalize_log_text(stdout_text))
            if killed:
                status = RUN_STATUS_KILLED
                exit_code = None
                error_message = f"{self.backend_label} task was killed."
                summary_line = error_message
                primary_output = None
                question_id = None
                question_text = None
                pending_choices: list[str] = []
            elif returncode == 0 and question_block and question_block.get("question_text"):
                status = RUN_STATUS_AWAITING_USER_INPUT
                exit_code = 0
                question_id = str(question_block.get("question_id") or "").strip() or None
                question_text = str(question_block.get("question_text") or "").strip() or None
                pending_choices = list(question_block.get("choices", []))
                error_message = None
                summary_line = question_text or f"{self.backend_label} is awaiting user input."
                primary_output = None
            elif returncode == 0:
                status = RUN_STATUS_SUCCESS
                exit_code = 0
                error_message = None
                summary_line = extract_summary_line(stdout_text) or f"{self.backend_label} command completed successfully."
                question_id = None
                question_text = None
                pending_choices = []
            else:
                status = RUN_STATUS_FAILED
                exit_code = returncode
                error_message = extract_error_excerpt(stderr_text, stdout_text) or (
                    f"{self.backend_label} command exited with code {returncode}."
                )
                summary_line = error_message
                primary_output = None
                question_id = None
                question_text = None
                pending_choices = []

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
                error_message=error_message,
                question_id=question_id,
                question_text=question_text,
                pending_choices=pending_choices,
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
                error_message=error_message,
                question_id=None,
                question_text=None,
                pending_choices=[],
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
