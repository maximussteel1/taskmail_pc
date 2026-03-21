"""Local PC-side runtime control helpers."""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime
from pathlib import Path

from .config import load_config
from .status import THREAD_STATUS_RUNNING
from .thread_store import load_thread_state

THREAD_KILL_REQUESTS_DIRNAME = "thread_kill_requests"
RUNNER_RESTART_REQUESTS_DIRNAME = "runner_restart_requests"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def thread_kill_requests_dir(runtime_dir: str | Path) -> Path:
    return Path(runtime_dir).resolve() / THREAD_KILL_REQUESTS_DIRNAME


def runner_restart_requests_dir(runtime_dir: str | Path) -> Path:
    return Path(runtime_dir).resolve() / RUNNER_RESTART_REQUESTS_DIRNAME


def write_thread_kill_request(
    runtime_dir: str | Path,
    *,
    thread_id: str,
    task_id: str,
    source: str = "pc",
    requested_at: str | None = None,
) -> Path:
    request_dir = thread_kill_requests_dir(runtime_dir)
    request_dir.mkdir(parents=True, exist_ok=True)
    normalized_requested_at = requested_at or _timestamp()
    request_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
    payload = {
        "request_id": request_id,
        "thread_id": str(thread_id).strip(),
        "task_id": str(task_id).strip(),
        "source": str(source).strip() or "pc",
        "requested_at": normalized_requested_at,
    }
    path = request_dir / f"{request_id}_{payload['thread_id']}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_runner_restart_request(
    runtime_dir: str | Path,
    *,
    source: str = "pc",
    requested_at: str | None = None,
    thread_id: str | None = None,
    message_id: str | None = None,
) -> Path:
    request_dir = runner_restart_requests_dir(runtime_dir)
    request_dir.mkdir(parents=True, exist_ok=True)
    normalized_requested_at = requested_at or _timestamp()
    request_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
    payload = {
        "request_id": request_id,
        "source": str(source).strip() or "pc",
        "requested_at": normalized_requested_at,
        "thread_id": str(thread_id or "").strip(),
        "message_id": str(message_id or "").strip(),
    }
    path = request_dir / f"{request_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def list_thread_kill_request_paths(runtime_dir: str | Path) -> list[Path]:
    request_dir = thread_kill_requests_dir(runtime_dir)
    if not request_dir.exists():
        return []
    return sorted(path for path in request_dir.glob("*.json") if path.is_file())


def list_runner_restart_request_paths(runtime_dir: str | Path) -> list[Path]:
    request_dir = runner_restart_requests_dir(runtime_dir)
    if not request_dir.exists():
        return []
    return sorted(path for path in request_dir.glob("*.json") if path.is_file())


def read_thread_kill_request(path: str | Path) -> dict[str, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Thread kill request must be a JSON object.")

    normalized: dict[str, str] = {}
    for key in ("request_id", "thread_id", "task_id", "source", "requested_at"):
        normalized[key] = str(payload.get(key) or "").strip()

    if not normalized["thread_id"]:
        raise ValueError("Thread kill request is missing thread_id.")
    if not normalized["task_id"]:
        raise ValueError("Thread kill request is missing task_id.")
    if not normalized["request_id"]:
        normalized["request_id"] = Path(path).stem
    if not normalized["source"]:
        normalized["source"] = "pc"
    if not normalized["requested_at"]:
        normalized["requested_at"] = _timestamp()
    return normalized


def read_runner_restart_request(path: str | Path) -> dict[str, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Runner restart request must be a JSON object.")

    normalized: dict[str, str] = {}
    for key in ("request_id", "source", "requested_at", "thread_id", "message_id"):
        normalized[key] = str(payload.get(key) or "").strip()

    if not normalized["request_id"]:
        normalized["request_id"] = Path(path).stem
    if not normalized["source"]:
        normalized["source"] = "pc"
    if not normalized["requested_at"]:
        normalized["requested_at"] = _timestamp()
    return normalized


def _resolve_task_root(task_root: str | None, config_path: str | None) -> Path:
    if task_root:
        return Path(task_root).resolve()
    config = load_config(config_path)
    config_base_dir = Path(config_path).resolve().parent if config_path else None
    return config.resolve_task_root(config_base_dir).resolve()


def request_thread_kill(
    *,
    runtime_dir: str,
    thread_id: str,
    task_root: str | None = None,
    config_path: str | None = None,
    source: str = "pc",
) -> tuple[int, str]:
    resolved_task_root = _resolve_task_root(task_root, config_path)
    try:
        state = load_thread_state(thread_id, resolved_task_root)
    except FileNotFoundError:
        return 1, f"Thread not found: {thread_id}"

    task_id = str(state.current_task_id or "").strip()
    if state.status != THREAD_STATUS_RUNNING or not task_id:
        return 1, f"Thread {thread_id} is not currently running; status={state.status or 'unknown'}"

    request_path = write_thread_kill_request(
        runtime_dir,
        thread_id=thread_id,
        task_id=task_id,
        source=source,
    )
    return 0, f"Queued local kill request for thread {thread_id} task {task_id}: {request_path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local PC-side runtime control helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    request_kill = subparsers.add_parser(
        "request-thread-kill",
        help="Queue a local kill request for a running thread.",
    )
    request_kill.add_argument("thread_id", help="Thread id, for example thread_048")
    request_kill.add_argument("--runtime-dir", required=True, help="Runtime directory shared with the host loop.")
    request_kill.add_argument("--task-root", help="Optional task root override.")
    request_kill.add_argument("--config", help="Optional config path used to resolve task_root.")
    request_kill.add_argument("--source", default="pc", help="Operator-facing source label for logging.")

    args = parser.parse_args(argv)
    if args.command == "request-thread-kill":
        exit_code, message = request_thread_kill(
            runtime_dir=args.runtime_dir,
            thread_id=args.thread_id,
            task_root=args.task_root,
            config_path=args.config,
            source=args.source,
        )
        print(message)
        return exit_code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
