"""Standalone sdk-first runtime smoke for Codex and OpenCode."""

from __future__ import annotations

import argparse
import json
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex_process_registry import PROCESS_RECORD_FILENAME
from .config import AppConfig
from .runner import SerialTaskRunner, _build_dispatcher

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_sdk_runtime_smoke"
DEFAULT_FILENAME = "smoke_note.txt"
DEFAULT_FILE_TEXT = "hello from sdk runtime smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _normalize_single_line_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.rstrip("\r\n")


def build_task_text(filename: str, file_text: str) -> str:
    return "\n".join(
        [
            f"Create a UTF-8 text file named {filename} in the repo root.",
            f"Write this single line into the file: {file_text}",
            "Do not modify any other files.",
            "Then output exactly these two human-readable lines and nothing else before the structured capsule:",
            "STATUS: OK",
            f"FILE: {filename}",
            "After those two lines, append exactly one run-result capsule with:",
            f"changed_files: {filename}",
            "tests_passed: unknown",
            "error_type: <empty>",
            "error_message: <empty>",
        ]
    ).strip()


def _seed_payload(*, backend: str, repo_path: Path, filename: str, file_text: str) -> dict[str, Any]:
    return {
        "task_id": "task_001",
        "thread_id": "thread_001",
        "backend": backend,
        "repo_path": str(repo_path),
        "workdir": None,
        "task_text": build_task_text(filename, file_text),
        "acceptance": [
            f"{filename} exists",
            f"{filename} contains the requested single-line text: {file_text}",
            "reply includes STATUS: OK and FILE line",
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


def run_runtime_smoke(
    *,
    backend: str,
    output_dir: Path,
    run_name: str,
    filename: str,
    file_text: str,
    opencode_command: str,
    codex_command: str,
) -> dict[str, Any]:
    run_root = output_dir / run_name
    repo_dir = run_root / "repo"
    task_root = run_root / "tasks"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.txt").write_text("sdk runtime smoke workspace\n", encoding="utf-8")

    config = AppConfig(
        opencode_command=opencode_command,
        codex_command=codex_command,
        opencode_transport_default="sdk",
        codex_transport_default="sdk",
    )
    seed_payload = _seed_payload(backend=backend, repo_path=repo_dir, filename=filename, file_text=file_text)
    seed_path = run_root / "seed.json"
    _write_json(seed_path, seed_payload)

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
    result = runner.run_snapshot_seed(seed_payload)
    thread_dir = task_root / result.thread_id
    run_dir = thread_dir / "runs" / result.task_id
    result_path = run_dir / "result.json"
    stdout_path = thread_dir / result.stdout_file
    summary_path = thread_dir / result.summary_file if result.summary_file else None
    target_file = repo_dir / filename

    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    file_exists = target_file.exists()
    file_content = target_file.read_text(encoding="utf-8") if file_exists else None
    cleanup = _verify_cleanup(backend, run_dir)

    failures: list[str] = []
    if result.status != "success":
        failures.append(f"Unexpected run status: {result.status}")
    if result.backend_transport != "sdk":
        failures.append(f"Unexpected backend transport: {result.backend_transport}")
    if not result.backend_session_id:
        failures.append("backend_session_id was empty.")
    if not file_exists:
        failures.append(f"Expected file was not created: {filename}")
    elif _normalize_single_line_text(file_content) != file_text:
        failures.append("Created file content did not match the expected text.")
    expected_lines = {"STATUS: OK", f"FILE: {filename}"}
    actual_lines = {line.strip() for line in stdout_text.splitlines() if line.strip()}
    missing_lines = sorted(expected_lines - actual_lines)
    if missing_lines:
        failures.append("Stdout is missing expected lines: " + ", ".join(missing_lines))
    if filename not in result.changed_files:
        failures.append(f"RunResult.changed_files does not include {filename}.")
    if not cleanup.get("cleanup_ok", False):
        failures.append("Backend cleanup verification failed.")

    smoke_result = {
        "success": not failures,
        "backend": backend,
        "run_name": run_name,
        "seed_path": str(seed_path),
        "task_root": str(task_root),
        "result_path": str(result_path),
        "stdout_path": str(stdout_path),
        "summary_path": str(summary_path) if summary_path is not None else None,
        "file_path": str(target_file),
        "file_exists": file_exists,
        "file_content": file_content,
        "status": result.status,
        "backend_transport": result.backend_transport,
        "backend_session_id": result.backend_session_id,
        "changed_files": list(result.changed_files),
        "cleanup": cleanup,
        "failures": failures,
    }
    smoke_result_path = run_root / "smoke_result.json"
    smoke_result["smoke_result_path"] = str(smoke_result_path)
    _write_json(smoke_result_path, smoke_result)
    return smoke_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a standalone sdk-first runtime smoke for OpenCode or Codex.")
    parser.add_argument("--backend", choices=["opencode", "codex"], required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    parser.add_argument("--filename", default=DEFAULT_FILENAME)
    parser.add_argument("--file-text", default=DEFAULT_FILE_TEXT)
    parser.add_argument("--opencode-command", default="")
    parser.add_argument("--codex-command", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"{args.backend}-sdk-runtime-smoke-{_timestamp_slug()}"
    result = run_runtime_smoke(
        backend=args.backend,
        output_dir=Path(args.output_dir),
        run_name=run_name,
        filename=args.filename,
        file_text=args.file_text,
        opencode_command=args.opencode_command,
        codex_command=args.codex_command,
    )
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"], "backend": result["backend"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
