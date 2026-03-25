"""Standalone sdk-first permission smoke for Codex and OpenCode."""

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
from .models import ParsedMailAction, RunResult, TaskSnapshot, ThreadState
from .runner import SerialTaskRunner, _build_dispatcher
from .task_compiler import compile_task

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_sdk_permission_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _permission_task_text(step_name: str, token: str) -> str:
    return "\n".join(
        [
            "This is a sdk-first permission smoke test.",
            "Do not modify any files and do not run tests.",
            "Reply with exactly one line and nothing else before any structured capsule:",
            f"PERMISSION_OK | {step_name} | {token}",
            "Append a structured run-result capsule with:",
            "changed_files: <empty>",
            "tests_passed: unknown",
            "error_type: <empty>",
            "error_message: <empty>",
        ]
    ).strip()


def _seed_payload(*, backend: str, repo_path: Path, permission: str, step_name: str, token: str) -> dict[str, Any]:
    return {
        "task_id": "task_001",
        "thread_id": "thread_001",
        "backend": backend,
        "repo_path": str(repo_path),
        "workdir": None,
        "task_text": _permission_task_text(step_name, token),
        "permission": permission,
        "acceptance": [
            "reply includes the expected permission line",
            "runtime projects the requested permission into backend-specific execution settings",
            "follow-up inherits permission when omitted",
            "explicit permission override resets runtime projection",
        ],
        "timeout_minutes": 20,
        "mode": "analysis_only",
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


def _load_runtime_projection(backend: str, run_dir: Path) -> dict[str, Any]:
    prompt_path = run_dir / "prompt.txt"
    if backend == "codex":
        request_path = run_dir / "sidecar_request.json"
        request_payload = json.loads(request_path.read_text(encoding="utf-8")) if request_path.exists() else None
        return {
            "prompt_path": str(prompt_path),
            "prompt_text": _read_text(prompt_path),
            "sidecar_request_path": str(request_path),
            "sidecar_request": request_payload,
        }
    overlay_path = run_dir / "opencode_permission_overlay.json"
    overlay_payload = json.loads(overlay_path.read_text(encoding="utf-8")) if overlay_path.exists() else None
    sdk_turn_path = run_dir / "sdk_turn.json"
    sdk_turn_payload = json.loads(sdk_turn_path.read_text(encoding="utf-8")) if sdk_turn_path.exists() else None
    return {
        "prompt_path": str(prompt_path),
        "prompt_text": _read_text(prompt_path),
        "overlay_path": str(overlay_path),
        "overlay_exists": overlay_path.exists(),
        "overlay_payload": overlay_payload,
        "sdk_turn_path": str(sdk_turn_path),
        "sdk_turn": sdk_turn_payload,
    }


def _verify_permission_projection(backend: str, expected_permission: str, projection: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if backend == "codex":
        request_payload = projection.get("sidecar_request")
        if not isinstance(request_payload, dict):
            return ["Missing or invalid sidecar_request.json for Codex SDK run."]
        expected_sandbox = "danger-full-access" if expected_permission == "highest" else "workspace-write"
        if request_payload.get("sandbox_mode") != expected_sandbox:
            failures.append(
                f"Codex sandbox_mode mismatch: expected {expected_sandbox}, got {request_payload.get('sandbox_mode')!r}"
            )
        if request_payload.get("approval_policy") != "never":
            failures.append(
                f"Codex approval_policy mismatch: expected 'never', got {request_payload.get('approval_policy')!r}"
            )
        return failures

    overlay_exists = bool(projection.get("overlay_exists"))
    overlay_payload = projection.get("overlay_payload")
    if expected_permission == "highest":
        if not overlay_exists:
            failures.append("OpenCode highest permission run did not create overlay config.")
            return failures
        permission_payload = overlay_payload.get("permission") if isinstance(overlay_payload, dict) else None
        required_allow_keys = ("edit", "bash", "webfetch", "doom_loop", "external_directory")
        if not isinstance(permission_payload, dict):
            failures.append("OpenCode overlay payload is missing permission mapping.")
            return failures
        for key in required_allow_keys:
            if permission_payload.get(key) != "allow":
                failures.append(f"OpenCode overlay permission {key!r} was not set to 'allow'.")
        return failures

    if overlay_exists:
        failures.append("OpenCode default permission run unexpectedly created overlay config.")
    return failures


def _run_step_failures(
    *,
    backend: str,
    expected_permission: str,
    expected_step_name: str,
    expected_token: str,
    result: RunResult,
    state: ThreadState,
    stdout_text: str,
    cleanup: dict[str, Any],
    projection: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    expected_line = f"PERMISSION_OK | {expected_step_name} | {expected_token}"
    if result.status != "success":
        failures.append(f"Unexpected run status: {result.status}")
    if result.backend_transport != "sdk":
        failures.append(f"Unexpected backend transport: {result.backend_transport}")
    if not result.backend_session_id:
        failures.append("backend_session_id was empty.")
    if state.status != "done":
        failures.append(f"Unexpected thread status: {state.status}")
    if state.permission != expected_permission:
        failures.append(f"Thread permission mismatch: expected {expected_permission}, got {state.permission!r}")
    actual_lines = {line.strip() for line in stdout_text.splitlines() if line.strip()}
    if expected_line not in actual_lines:
        failures.append(f"Stdout is missing expected permission line: {expected_line}")
    failures.extend(_verify_permission_projection(backend, expected_permission, projection))
    if not cleanup.get("cleanup_ok", False):
        failures.append("Backend cleanup verification failed.")
    return failures


def _build_followup_snapshot(
    *,
    action: ParsedMailAction,
    thread_state: ThreadState,
    latest_snapshot: TaskSnapshot,
    task_id: str,
    config: AppConfig,
) -> TaskSnapshot:
    compiled = compile_task(
        action,
        thread_state,
        latest_snapshot,
        task_id=task_id,
        now=_timestamp(),
        default_transport_for_backend=config.default_transport_for_backend,
    )
    if compiled is None:
        raise RuntimeError(f"compile_task returned None for action={action.action}")
    return compiled


def _collect_step_record(
    *,
    backend: str,
    task_root: Path,
    result: RunResult,
) -> tuple[dict[str, Any], ThreadState, str, dict[str, Any], dict[str, Any]]:
    paths = _result_paths(task_root, result)
    state_payload = json.loads((task_root / result.thread_id / "thread_state.json").read_text(encoding="utf-8"))
    state = ThreadState(**state_payload)
    stdout_text = _read_text(paths["stdout_path"])
    cleanup = _verify_cleanup(backend, paths["run_dir"])
    projection = _load_runtime_projection(backend, paths["run_dir"])
    step_record = {
        "status": result.status,
        "backend_transport": result.backend_transport,
        "backend_session_id": result.backend_session_id,
        "result_path": str(paths["result_path"]),
        "stdout_path": str(paths["stdout_path"]),
        "stderr_path": str(paths["stderr_path"]),
        "summary_path": str(paths["summary_path"]) if result.summary_file else None,
        "cleanup": cleanup,
        "thread_state": state_payload,
        "stdout_excerpt": stdout_text.strip(),
        "runtime_projection": projection,
    }
    return step_record, state, stdout_text, cleanup, projection


def run_sdk_permission_smoke(
    *,
    backend: str,
    output_dir: Path,
    run_name: str,
    initial_permission: str,
    reset_permission: str,
    opencode_command: str,
    codex_command: str,
) -> dict[str, Any]:
    run_root = output_dir / run_name
    repo_dir = run_root / "repo"
    task_root = run_root / "tasks"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.txt").write_text("sdk permission smoke workspace\n", encoding="utf-8")

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
    initial_token = secrets.token_hex(5).upper()
    inherit_token = secrets.token_hex(5).upper()
    reset_token = secrets.token_hex(5).upper()
    seed_payload = _seed_payload(
        backend=backend,
        repo_path=repo_dir,
        permission=initial_permission,
        step_name="initial",
        token=initial_token,
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
        "initial_permission": initial_permission,
        "reset_permission": reset_permission,
        "steps": {},
        "failures": [],
    }

    try:
        initial_result = runner.run_snapshot_seed(seed_payload)
        initial_record, initial_state, initial_stdout, initial_cleanup, initial_projection = _collect_step_record(
            backend=backend,
            task_root=task_root,
            result=initial_result,
        )
        smoke_result["steps"]["initial"] = initial_record
        initial_failures = _run_step_failures(
            backend=backend,
            expected_permission=initial_permission,
            expected_step_name="initial",
            expected_token=initial_token,
            result=initial_result,
            state=initial_state,
            stdout_text=initial_stdout,
            cleanup=initial_cleanup,
            projection=initial_projection,
        )
        if initial_failures:
            smoke_result["failures"] = initial_failures
            return smoke_result

        latest_snapshot = runner.workspace.load_snapshot(initial_state.thread_id, initial_state.last_task_snapshot_file)
        inherit_snapshot = _build_followup_snapshot(
            action=ParsedMailAction(
                action="CONTINUE_SESSION",
                confidence=1.0,
                raw_user_text=_permission_task_text("inherit", inherit_token),
            ),
            thread_state=initial_state,
            latest_snapshot=latest_snapshot,
            task_id="task_002",
            config=config,
        )
        _write_json(run_root / "compiled_inherit_snapshot.json", asdict(inherit_snapshot))
        inherit_result = runner.run_task_snapshot(inherit_snapshot)
        inherit_record, inherit_state, inherit_stdout, inherit_cleanup, inherit_projection = _collect_step_record(
            backend=backend,
            task_root=task_root,
            result=inherit_result,
        )
        smoke_result["steps"]["inherit"] = inherit_record
        inherit_failures = _run_step_failures(
            backend=backend,
            expected_permission=initial_permission,
            expected_step_name="inherit",
            expected_token=inherit_token,
            result=inherit_result,
            state=inherit_state,
            stdout_text=inherit_stdout,
            cleanup=inherit_cleanup,
            projection=inherit_projection,
        )
        if inherit_snapshot.permission != initial_permission:
            inherit_failures.append(
                f"Inherited snapshot permission mismatch: expected {initial_permission}, got {inherit_snapshot.permission!r}"
            )
        if inherit_result.backend_session_id != initial_result.backend_session_id:
            inherit_failures.append("Inherited run did not keep the previous backend_session_id.")
        if inherit_failures:
            smoke_result["failures"] = inherit_failures
            return smoke_result

        latest_snapshot = runner.workspace.load_snapshot(inherit_state.thread_id, inherit_state.last_task_snapshot_file)
        reset_snapshot = _build_followup_snapshot(
            action=ParsedMailAction(
                action="CONTINUE_SESSION",
                confidence=1.0,
                permission=reset_permission,
                raw_user_text=_permission_task_text("reset", reset_token),
            ),
            thread_state=inherit_state,
            latest_snapshot=latest_snapshot,
            task_id="task_003",
            config=config,
        )
        _write_json(run_root / "compiled_reset_snapshot.json", asdict(reset_snapshot))
        reset_result = runner.run_task_snapshot(reset_snapshot)
        reset_record, reset_state, reset_stdout, reset_cleanup, reset_projection = _collect_step_record(
            backend=backend,
            task_root=task_root,
            result=reset_result,
        )
        smoke_result["steps"]["reset"] = reset_record
        reset_failures = _run_step_failures(
            backend=backend,
            expected_permission=reset_permission,
            expected_step_name="reset",
            expected_token=reset_token,
            result=reset_result,
            state=reset_state,
            stdout_text=reset_stdout,
            cleanup=reset_cleanup,
            projection=reset_projection,
        )
        if reset_snapshot.permission != reset_permission:
            reset_failures.append(
                f"Reset snapshot permission mismatch: expected {reset_permission}, got {reset_snapshot.permission!r}"
            )
        if reset_result.backend_session_id != initial_result.backend_session_id:
            reset_failures.append("Reset run did not keep the previous backend_session_id.")
        smoke_result["failures"] = reset_failures
        smoke_result["success"] = not reset_failures
        return smoke_result
    except Exception as exc:
        smoke_result["failures"] = [f"{type(exc).__name__}: {exc}"]
        return smoke_result
    finally:
        smoke_result_path = run_root / "smoke_result.json"
        smoke_result["smoke_result_path"] = str(smoke_result_path)
        _write_json(smoke_result_path, smoke_result)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a standalone sdk-first permission smoke.")
    parser.add_argument("--backend", choices=["opencode", "codex"], required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    parser.add_argument("--initial-permission", choices=["default", "highest"], default="highest")
    parser.add_argument("--reset-permission", choices=["default", "highest"], default="default")
    parser.add_argument("--opencode-command", default="")
    parser.add_argument("--codex-command", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"{args.backend}-sdk-permission-smoke-{_timestamp_slug()}"
    result = run_sdk_permission_smoke(
        backend=args.backend,
        output_dir=Path(args.output_dir),
        run_name=run_name,
        initial_permission=args.initial_permission,
        reset_permission=args.reset_permission,
        opencode_command=args.opencode_command,
        codex_command=args.codex_command,
    )
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"], "backend": result["backend"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
