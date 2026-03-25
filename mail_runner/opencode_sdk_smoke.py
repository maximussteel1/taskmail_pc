"""Minimal local OpenCode SDK smoke test."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from opencode_ai import Opencode

from .opencode_sdk_common import (
    extract_reply_text,
    latest_assistant_message,
    part_to_record,
    read_stderr_tail,
    resolve_profile_provider_model,
    start_server,
    stop_server,
    wait_for_server,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_opencode_sdk_smoke"
DEFAULT_FILENAME = "smoke_note.txt"
DEFAULT_FILE_TEXT = "hello from opencode sdk smoke"
def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_default_prompt(filename: str, file_text: str) -> str:
    return "\n".join(
        [
            f"Create a UTF-8 text file named {filename} in the current workspace.",
            f"Write exactly this one line into the file: {file_text}",
            "Do not modify any other files.",
            "Then reply with exactly these two lines and nothing else:",
            "STATUS: OK",
            f"FILE: {filename}",
        ]
    ).strip()


def evaluate_smoke_result(
    *,
    filename: str,
    expected_text: str,
    reply_text: str,
    file_exists: bool,
    file_content: str | None,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if not file_exists:
        failures.append(f"Expected file was not created: {filename}")
    elif file_content != expected_text:
        failures.append("Created file content did not match the expected text.")

    reply_lines = {line.strip() for line in reply_text.splitlines() if line.strip()}
    expected_lines = {"STATUS: OK", f"FILE: {filename}"}
    missing_lines = [line for line in expected_lines if line not in reply_lines]
    if missing_lines:
        failures.append("Assistant reply is missing expected lines: " + ", ".join(missing_lines))

    return not failures, failures


def run_smoke(
    *,
    base_url: str,
    workspace: Path,
    filename: str,
    expected_text: str,
    provider_id: str | None,
    model_id: str | None,
    prompt: str,
    session_title: str,
) -> dict[str, Any]:
    with Opencode(base_url=base_url, timeout=180.0, max_retries=0) as client:
        providers_payload = client.app.providers()
        configured_model = f"{provider_id}/{model_id}" if provider_id and model_id else (model_id or None)
        resolved_provider_id, resolved_model_id = resolve_profile_provider_model(providers_payload, configured_model)
        session = client.session.create(extra_body={"title": session_title})
        client.session.chat(
            session.id,
            provider_id=resolved_provider_id,
            model_id=resolved_model_id,
            parts=[{"type": "text", "text": prompt}],
        )
        messages = client.session.messages(session.id)

    assistant_message = latest_assistant_message(list(messages))
    assistant_parts = list(getattr(assistant_message, "parts", []) or [])
    reply_text = extract_reply_text(assistant_parts)
    target_file = workspace / filename
    file_exists = target_file.exists()
    file_content = target_file.read_text(encoding="utf-8") if file_exists else None
    success, failures = evaluate_smoke_result(
        filename=filename,
        expected_text=expected_text,
        reply_text=reply_text,
        file_exists=file_exists,
        file_content=file_content,
    )
    return {
        "success": success,
        "failures": failures,
        "base_url": base_url,
        "workspace": str(workspace),
        "provider_id": resolved_provider_id,
        "model_id": resolved_model_id,
        "session_id": session.id,
        "filename": filename,
        "expected_text": expected_text,
        "prompt": prompt,
        "file_exists": file_exists,
        "file_path": str(target_file),
        "file_content": file_content,
        "assistant_reply": reply_text,
        "message_count": len(messages),
        "assistant_parts": [part_to_record(part) for part in assistant_parts],
        "provider_defaults": dict(getattr(providers_payload, "default", {}) or {}),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start a temporary opencode server, talk to it through the Python SDK, and verify a simple file edit."
    )
    parser.add_argument("--opencode-command", default="", help="Optional explicit opencode command prefix.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Directory where smoke artifacts are written.",
    )
    parser.add_argument("--run-name", help="Optional fixed run name.")
    parser.add_argument("--workspace", help="Workspace used as the temporary opencode server cwd.")
    parser.add_argument("--provider-id", help="Optional provider id override.")
    parser.add_argument("--model-id", help="Optional model id override.")
    parser.add_argument("--filename", default=DEFAULT_FILENAME, help="Text file created by the smoke task.")
    parser.add_argument(
        "--file-text",
        default=DEFAULT_FILE_TEXT,
        help="Exact one-line UTF-8 text expected inside the created file.",
    )
    parser.add_argument("--prompt", help="Optional full prompt override.")
    parser.add_argument("--port", type=int, help="Optional fixed local port for the temporary opencode server.")
    parser.add_argument(
        "--startup-timeout-seconds",
        type=int,
        default=30,
        help="How long to wait for opencode serve to become ready.",
    )
    parser.add_argument(
        "--leave-server-running",
        action="store_true",
        help="Do not stop the temporary opencode server after the smoke test.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"opencode-sdk-smoke-{_timestamp_slug()}"
    run_dir = Path(args.output_dir) / run_name
    workspace = Path(args.workspace) if args.workspace else (run_dir / "workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = args.prompt or build_default_prompt(args.filename, args.file_text)

    server = start_server(
        opencode_command=args.opencode_command,
        workspace=workspace,
        output_dir=run_dir,
        port=args.port,
    )
    result_path = run_dir / "result.json"
    result: dict[str, Any] = {
        "success": False,
        "run_name": run_name,
        "workspace": str(workspace),
        "base_url": server.base_url,
    }

    try:
        wait_for_server(server, timeout_seconds=args.startup_timeout_seconds)
        result = run_smoke(
            base_url=server.base_url,
            workspace=workspace,
            filename=args.filename,
            expected_text=args.file_text,
            provider_id=args.provider_id,
            model_id=args.model_id,
            prompt=prompt,
            session_title=run_name,
        )
        result["run_name"] = run_name
        result["result_path"] = str(result_path)
        _write_json(result_path, result)
        print(f"result: {result_path}")
        print(json.dumps({"success": result["success"], "file_path": result["file_path"]}, ensure_ascii=False))
        return 0 if result["success"] else 1
    except Exception as exc:
        result.update(
            {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stderr_tail": read_stderr_tail(server.stderr_log),
                "stdout_log": str(server.stdout_log),
                "stderr_log": str(server.stderr_log),
                "result_path": str(result_path),
            }
        )
        _write_json(result_path, result)
        print(f"result: {result_path}")
        print(result["error"])
        return 1
    finally:
        if not args.leave_server_running:
            stop_server(server)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
