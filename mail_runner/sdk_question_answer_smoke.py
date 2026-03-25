"""Standalone sdk-first question-answer smoke for Codex and OpenCode."""

from __future__ import annotations

import argparse
import json
import secrets
import socket
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex_process_registry import PROCESS_RECORD_FILENAME
from .config import AppConfig
from .models import ParsedMailAction, RunResult, ThreadState
from .runner import SerialTaskRunner, _build_dispatcher
from .task_compiler import compile_task

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_sdk_question_smoke"
DEFAULT_FILENAME = "question_smoke_note.txt"
DEFAULT_FILE_TEXT = "hello from sdk question smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _build_question_task_text(answer_token: str, filename: str, file_text: str) -> str:
    return "\n".join(
        [
            "This is a sdk-first question/answer smoke test.",
            "Do not modify any files until the user answers the pending question.",
            "First turn rule: if the user has not answered yet, output exactly one question capsule and nothing else:",
            "---TASK-QUESTION-BEGIN---",
            "question_set_id: sdk_question_smoke",
            "question_id: sdk_question_answer_token",
            "question_type: short_text",
            "required: true",
            f"question_text: Reply with the exact token {answer_token}",
            "---TASK-QUESTION-END---",
            "",
            f"Resume rule: when the user later replies with exactly {answer_token}, create a UTF-8 text file named {filename} in the repo root.",
            f"Write exactly this one line into the file: {file_text}",
            "Do not modify any other files.",
            "Then output exactly these two human-readable lines and nothing else before the structured capsule:",
            f"QUESTION_FLOW_OK | {answer_token}",
            f"FILE: {filename}",
            "After those two lines, append exactly one run-result capsule with:",
            f"changed_files: {filename}",
            "tests_passed: unknown",
            "error_type: <empty>",
            "error_message: <empty>",
        ]
    ).strip()


def _seed_payload(*, backend: str, repo_path: Path, answer_token: str, filename: str, file_text: str) -> dict[str, Any]:
    return {
        "task_id": "task_001",
        "thread_id": "thread_001",
        "backend": backend,
        "repo_path": str(repo_path),
        "workdir": None,
        "task_text": _build_question_task_text(answer_token, filename, file_text),
        "acceptance": [
            "first run enters awaiting_user_input",
            f"second run creates {filename}",
            f"{filename} contains exactly: {file_text}",
            f"second run reply includes QUESTION_FLOW_OK | {answer_token}",
        ],
        "timeout_minutes": 20,
        "mode": "modify",
        "created_at": "2026-03-25T00:00:00",
        "updated_at": "2026-03-25T00:00:00",
    }


def _port_is_open(hostname: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((hostname, port)) == 0


def _verify_cleanup(backend: str, run_dir: Path) -> dict[str, Any]:
    if backend == "opencode":
        sdk_turn_path = run_dir / "sdk_turn.json"
        if not sdk_turn_path.exists():
            return {"backend": backend, "cleanup_ok": False, "reason": "missing sdk_turn.json"}
        payload = json.loads(sdk_turn_path.read_text(encoding="utf-8"))
        port = int(payload.get("port") or 0)
        port_open_after_run = _port_is_open("127.0.0.1", port) if port > 0 else None
        return {
            "backend": backend,
            "port": port,
            "port_open_after_run": port_open_after_run,
            "cleanup_ok": port_open_after_run is False,
        }

    process_record_path = run_dir / PROCESS_RECORD_FILENAME
    return {
        "backend": backend,
        "process_record_present_after_run": process_record_path.exists(),
        "cleanup_ok": not process_record_path.exists(),
    }


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _result_paths(task_root: Path, result: RunResult) -> dict[str, Path]:
    thread_dir = task_root / result.thread_id
    run_dir = thread_dir / "runs" / result.task_id
    return {
        "thread_dir": thread_dir,
        "run_dir": run_dir,
        "stdout_path": thread_dir / result.stdout_file,
        "stderr_path": thread_dir / result.stderr_file,
        "summary_path": thread_dir / result.summary_file if result.summary_file else Path(),
        "result_path": run_dir / "result.json",
    }


def _question_step_failures(
    *,
    result: RunResult,
    state: ThreadState,
    stdout_text: str,
    target_file: Path,
    answer_token: str,
    cleanup: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if result.status != "awaiting_user_input":
        failures.append(f"Unexpected first-run status: {result.status}")
    if result.backend_transport != "sdk":
        failures.append(f"Unexpected first-run backend transport: {result.backend_transport}")
    if not result.backend_session_id:
        failures.append("First run did not return backend_session_id.")
    if state.status != "awaiting_user_input":
        failures.append(f"Unexpected thread status after first run: {state.status}")
    if not state.pending_question_text or answer_token not in state.pending_question_text:
        failures.append("Pending question text does not contain the expected answer token.")
    if len(state.pending_questions) != 1:
        failures.append(f"Unexpected pending question count: {len(state.pending_questions)}")
    if answer_token not in stdout_text:
        failures.append("Question stdout did not include the expected answer token.")
    if target_file.exists():
        failures.append("Target file was created before the answer turn.")
    if not cleanup.get("cleanup_ok", False):
        failures.append("First-run cleanup verification failed.")
    return failures


def _answer_step_failures(
    *,
    compiled_backend_session_id: str | None,
    previous_backend_session_id: str | None,
    result: RunResult,
    state: ThreadState,
    stdout_text: str,
    target_file: Path,
    file_text: str,
    answer_token: str,
    filename: str,
    cleanup: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if compiled_backend_session_id != previous_backend_session_id:
        failures.append("Compiled answer snapshot did not preserve the previous backend_session_id.")
    if result.status != "success":
        failures.append(f"Unexpected second-run status: {result.status}")
    if result.backend_transport != "sdk":
        failures.append(f"Unexpected second-run backend transport: {result.backend_transport}")
    if result.backend_session_id != previous_backend_session_id:
        failures.append("Second run did not resume the existing backend session id.")
    if state.status != "done":
        failures.append(f"Unexpected thread status after second run: {state.status}")
    if state.pending_question_id or state.pending_questions:
        failures.append("Pending question state was not cleared after the answer turn.")
    if not target_file.exists():
        failures.append(f"Expected file was not created: {filename}")
    else:
        file_content = target_file.read_text(encoding="utf-8")
        if file_content.rstrip("\r\n") != file_text:
            failures.append("Created file content did not match the expected text.")
    if filename not in result.changed_files:
        failures.append(f"RunResult.changed_files does not include {filename}.")
    if not cleanup.get("cleanup_ok", False):
        failures.append("Second-run cleanup verification failed.")
    return failures


def _answer_step_observations(*, stdout_text: str, answer_token: str, filename: str) -> list[str]:
    expected_lines = {f"QUESTION_FLOW_OK | {answer_token}", f"FILE: {filename}"}
    actual_lines = {line.strip() for line in stdout_text.splitlines() if line.strip()}
    missing_lines = sorted(expected_lines - actual_lines)
    if not missing_lines:
        return []
    return [
        "Second-run stdout did not preserve the requested human-readable reply lines: "
        + ", ".join(missing_lines)
    ]


def run_sdk_question_answer_smoke(
    *,
    backend: str,
    output_dir: Path,
    run_name: str,
    filename: str,
    file_text: str,
    answer_token: str,
    opencode_command: str,
    codex_command: str,
) -> dict[str, Any]:
    run_root = output_dir / run_name
    repo_dir = run_root / "repo"
    task_root = run_root / "tasks"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.txt").write_text("sdk question-answer smoke workspace\n", encoding="utf-8")

    config = AppConfig(
        opencode_command=opencode_command,
        codex_command=codex_command,
        opencode_transport_default="sdk",
        codex_transport_default="sdk",
    )
    runner = SerialTaskRunner(
        task_root,
        _build_dispatcher(config),
        max_active_sessions=config.max_active_sessions,
        max_active_sessions_per_workspace=config.max_active_sessions_per_workspace,
        opencode_transport_default=config.opencode_transport_default,
        codex_transport_default=config.codex_transport_default,
    )
    seed_payload = _seed_payload(
        backend=backend,
        repo_path=repo_dir,
        answer_token=answer_token,
        filename=filename,
        file_text=file_text,
    )
    seed_path = run_root / "seed.json"
    _write_json(seed_path, seed_payload)

    smoke_result: dict[str, Any] = {
        "success": False,
        "backend": backend,
        "run_name": run_name,
        "seed_path": str(seed_path),
        "task_root": str(task_root),
        "repo_path": str(repo_dir),
        "filename": filename,
        "file_text": file_text,
        "answer_token": answer_token,
        "steps": {},
        "failures": [],
        "observations": [],
    }
    target_file = repo_dir / filename

    try:
        question_result = runner.run_snapshot_seed(seed_payload)
        question_paths = _result_paths(task_root, question_result)
        question_state = runner.workspace.load_json(runner.workspace.thread_state_path(question_result.thread_id))
        question_stdout = _read_text(question_paths["stdout_path"])
        question_cleanup = _verify_cleanup(backend, question_paths["run_dir"])
        question_step = {
            "status": question_result.status,
            "backend_transport": question_result.backend_transport,
            "backend_session_id": question_result.backend_session_id,
            "result_path": str(question_paths["result_path"]),
            "stdout_path": str(question_paths["stdout_path"]),
            "stderr_path": str(question_paths["stderr_path"]),
            "summary_path": str(question_paths["summary_path"]) if question_result.summary_file else None,
            "cleanup": question_cleanup,
            "thread_state": question_state,
            "stdout_excerpt": question_stdout.strip(),
        }
        smoke_result["steps"]["question"] = question_step

        loaded_question_state = ThreadState(**question_state)
        question_failures = _question_step_failures(
            result=question_result,
            state=loaded_question_state,
            stdout_text=question_stdout,
            target_file=target_file,
            answer_token=answer_token,
            cleanup=question_cleanup,
        )
        if question_failures:
            smoke_result["failures"] = question_failures
            return smoke_result

        latest_snapshot = runner.workspace.load_snapshot(
            loaded_question_state.thread_id,
            loaded_question_state.last_task_snapshot_file,
        )
        compiled = compile_task(
            ParsedMailAction(
                action="ANSWER_QUESTION",
                confidence=1.0,
                raw_user_text=answer_token,
            ),
            loaded_question_state,
            latest_snapshot,
            task_id="task_002",
            now=_timestamp(),
            default_transport_for_backend=config.default_transport_for_backend,
        )
        if compiled is None:
            smoke_result["failures"] = ["compile_task returned None for ANSWER_QUESTION."]
            return smoke_result
        compiled_snapshot_path = run_root / "compiled_answer_snapshot.json"
        _write_json(compiled_snapshot_path, asdict(compiled))

        answer_result = runner.run_task_snapshot(compiled)
        answer_paths = _result_paths(task_root, answer_result)
        answer_state = runner.workspace.load_json(runner.workspace.thread_state_path(answer_result.thread_id))
        answer_stdout = _read_text(answer_paths["stdout_path"])
        answer_cleanup = _verify_cleanup(backend, answer_paths["run_dir"])
        answer_step = {
            "status": answer_result.status,
            "backend_transport": answer_result.backend_transport,
            "backend_session_id": answer_result.backend_session_id,
            "result_path": str(answer_paths["result_path"]),
            "stdout_path": str(answer_paths["stdout_path"]),
            "stderr_path": str(answer_paths["stderr_path"]),
            "summary_path": str(answer_paths["summary_path"]) if answer_result.summary_file else None,
            "cleanup": answer_cleanup,
            "thread_state": answer_state,
            "stdout_excerpt": answer_stdout.strip(),
            "compiled_snapshot_path": str(compiled_snapshot_path),
            "compiled_snapshot": asdict(compiled),
            "file_exists": target_file.exists(),
            "file_content": target_file.read_text(encoding="utf-8") if target_file.exists() else None,
        }
        smoke_result["steps"]["answer"] = answer_step

        loaded_answer_state = ThreadState(**answer_state)
        answer_failures = _answer_step_failures(
            compiled_backend_session_id=compiled.backend_session_id,
            previous_backend_session_id=question_result.backend_session_id,
            result=answer_result,
            state=loaded_answer_state,
            stdout_text=answer_stdout,
            target_file=target_file,
            file_text=file_text,
            answer_token=answer_token,
            filename=filename,
            cleanup=answer_cleanup,
        )
        smoke_result["observations"] = _answer_step_observations(
            stdout_text=answer_stdout,
            answer_token=answer_token,
            filename=filename,
        )
        smoke_result["failures"] = answer_failures
        smoke_result["success"] = not answer_failures
        return smoke_result
    except Exception as exc:
        smoke_result["failures"] = [f"{type(exc).__name__}: {exc}"]
        return smoke_result
    finally:
        smoke_result_path = run_root / "smoke_result.json"
        smoke_result["smoke_result_path"] = str(smoke_result_path)
        _write_json(smoke_result_path, smoke_result)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a standalone sdk-first question-answer smoke.")
    parser.add_argument("--backend", choices=["opencode", "codex"], required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    parser.add_argument("--filename", default=DEFAULT_FILENAME)
    parser.add_argument("--file-text", default=DEFAULT_FILE_TEXT)
    parser.add_argument("--answer-token", default="", help="Optional fixed answer token.")
    parser.add_argument("--opencode-command", default="")
    parser.add_argument("--codex-command", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"{args.backend}-sdk-question-smoke-{_timestamp_slug()}"
    answer_token = args.answer_token or secrets.token_hex(5).upper()
    result = run_sdk_question_answer_smoke(
        backend=args.backend,
        output_dir=Path(args.output_dir),
        run_name=run_name,
        filename=args.filename,
        file_text=args.file_text,
        answer_token=answer_token,
        opencode_command=args.opencode_command,
        codex_command=args.codex_command,
    )
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"], "backend": result["backend"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
