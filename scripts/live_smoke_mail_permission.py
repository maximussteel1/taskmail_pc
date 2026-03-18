"""Live mailbox smoke test for Permission propagation and backend projection."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_smoke_mail_roundtrip import (  # type: ignore[import-not-found]
    BACKEND_SUBJECT_PREFIX,
    _dedupe_references,
    _envelope_to_dict,
    _load_local_thread_snapshot,
    _reply_has_exact_line,
    _scan_recent_messages,
    _send_and_record_mail,
    _subject_matches,
    _timestamp_slug,
    _write_json,
)
from mail_runner.config import PROJECT_ROOT, load_config
from mail_runner.mail_io import (
    SYSTEM_MESSAGE_HEADER,
    SYSTEM_MESSAGE_HEADER_VALUE,
    MailClient,
)
from mail_runner.workspace import WorkspaceManager

TERMINAL_STATUS_LABELS = {"DONE", "FAILED", "KILLED", "QUESTION", "PAUSED"}
STATUS_FIELD_LABELS = (
    "Status",
    "Session ID",
    "Thread ID",
    "Task ID",
    "Backend",
    "Repo",
    "Workdir",
    "Permission",
    "Exit Code",
    "Error",
)


def _build_initial_task_mail(
    *,
    repo_path: str,
    workdir: str | None,
    timeout_minutes: int,
    mode: str,
    profile: str | None,
    permission: str | None,
    task_text: str,
) -> str:
    lines = [f"Repo: {repo_path}"]
    if workdir:
        lines.append(f"Workdir: {workdir}")
    lines.extend(
        [
            f"Timeout: {timeout_minutes}",
            f"Mode: {mode}",
        ]
    )
    if profile:
        lines.append(f"Profile: {profile}")
    if permission:
        lines.append(f"Permission: {permission}")
    lines.extend(["Task:", task_text.strip()])
    return "\n".join(lines).strip() + "\n"


def _build_exact_reply_prompt(step_name: str, token: str) -> str:
    return "\n".join(
        [
            "This is a live smoke test for Permission handling.",
            "Do not modify any files and do not run tests.",
            "Reply with exactly one line and nothing else:",
            f"PERMISSION_OK | {step_name} | {token}",
        ]
    )


def _parse_status_mail_body(body_text: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    reply_lines: list[str] = []
    in_reply = False
    for raw_line in body_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if in_reply:
            if line.strip() == "---TASK-STATE-BEGIN---":
                break
            reply_lines.append(line)
            continue
        if line.strip() == "Reply:":
            in_reply = True
            continue
        for label in STATUS_FIELD_LABELS:
            prefix = f"{label}:"
            if line.startswith(prefix):
                fields[label] = line[len(prefix):].strip()
                break

    while reply_lines and not reply_lines[0].strip():
        reply_lines.pop(0)
    while reply_lines and not reply_lines[-1].strip():
        reply_lines.pop()

    return {
        "status": fields.get("Status", ""),
        "session_id": fields.get("Session ID", ""),
        "thread_id": fields.get("Thread ID", ""),
        "task_id": fields.get("Task ID", ""),
        "backend": fields.get("Backend", ""),
        "repo_path": fields.get("Repo", ""),
        "workdir": fields.get("Workdir", ""),
        "permission": fields.get("Permission", ""),
        "exit_code": fields.get("Exit Code", ""),
        "error": fields.get("Error", ""),
        "reply_text": "\n".join(reply_lines).strip(),
    }


def _wait_for_terminal_status(
    *,
    config,
    subject_text: str,
    thread_id: str | None,
    known_status_message_ids: set[str],
    timeout_seconds: int,
    interval_seconds: int,
    scan_limit: int,
    step_name: str,
    output_dir: Path,
) -> tuple[dict[str, Any], str | None, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    current_thread_id = thread_id
    observed: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        for item in _scan_recent_messages(config, scan_limit=scan_limit):
            envelope = item["envelope"]
            if envelope.message_id in known_status_message_ids:
                continue
            if envelope.raw_headers.get(SYSTEM_MESSAGE_HEADER) != SYSTEM_MESSAGE_HEADER_VALUE:
                continue
            if not _subject_matches(envelope.subject, subject_text, current_thread_id):
                continue

            parsed = _parse_status_mail_body(envelope.body_text)
            parsed_thread_id = parsed.get("thread_id") or ""
            if current_thread_id and parsed_thread_id and parsed_thread_id != current_thread_id:
                continue

            known_status_message_ids.add(envelope.message_id)
            if parsed_thread_id:
                current_thread_id = parsed_thread_id

            record = {
                "imap_id": item["imap_id"],
                "mail": _envelope_to_dict(envelope),
                "parsed": parsed,
            }
            observed.append(record)
            _write_json(output_dir / f"{step_name}_status_{len(observed):02d}.json", record)
            print(
                f"[{step_name}] observed status={parsed.get('status') or '?'} "
                f"permission={parsed.get('permission') or '?'} "
                f"thread={parsed.get('thread_id') or current_thread_id or '?'} "
                f"task={parsed.get('task_id') or '?'}"
            )

            if (parsed.get("status") or "").upper() in TERMINAL_STATUS_LABELS:
                return record, current_thread_id, observed

        time.sleep(interval_seconds)

    raise TimeoutError(
        f"Timed out after {timeout_seconds}s waiting for a terminal status mail "
        f"for subject '{subject_text}'."
    )


def _load_latest_run_context(task_root: Path, thread_id: str, local_state: dict[str, Any]) -> dict[str, Any]:
    workspace = WorkspaceManager(task_root)
    thread_dir = workspace.thread_dir(thread_id)
    latest_result = local_state.get("latest_result") or {}

    def _resolve(rel_path: str | None) -> Path | None:
        value = str(rel_path or "").strip()
        if not value:
            return None
        return thread_dir / value

    summary_path = _resolve(latest_result.get("summary_file"))
    stdout_path = _resolve(latest_result.get("stdout_file"))
    stderr_path = _resolve(latest_result.get("stderr_file"))
    run_dir = None
    for candidate in (summary_path, stdout_path, stderr_path):
        if candidate is not None:
            run_dir = candidate.parent
            break
    prompt_path = run_dir / "prompt.txt" if run_dir is not None else None
    overlay_path = run_dir / "opencode_permission_overlay.json" if run_dir is not None else None
    summary_text = summary_path.read_text(encoding="utf-8", errors="replace") if summary_path and summary_path.exists() else ""
    prompt_text = prompt_path.read_text(encoding="utf-8", errors="replace") if prompt_path and prompt_path.exists() else ""
    overlay_payload = None
    if overlay_path and overlay_path.exists():
        overlay_payload = json.loads(overlay_path.read_text(encoding="utf-8"))

    return {
        "thread_dir": str(thread_dir),
        "run_dir": str(run_dir) if run_dir is not None else None,
        "summary_path": str(summary_path) if summary_path is not None else None,
        "prompt_path": str(prompt_path) if prompt_path is not None else None,
        "overlay_path": str(overlay_path) if overlay_path is not None else None,
        "summary_text": summary_text,
        "prompt_text": prompt_text,
        "overlay_payload": overlay_payload,
    }


def _verify_backend_projection(
    *,
    backend: str,
    expected_permission: str,
    run_context: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "prompt_contains_permission": f"Permission: {expected_permission}" in str(run_context.get("prompt_text") or ""),
    }
    if backend == "codex":
        summary_text = str(run_context.get("summary_text") or "")
        checks["summary_contains_dangerous_flag"] = "--dangerously-bypass-approvals-and-sandbox" in summary_text
        checks["summary_contains_full_auto"] = "--full-auto" in summary_text
        checks["backend_projection_ok"] = (
            checks["summary_contains_dangerous_flag"] and not checks["summary_contains_full_auto"]
            if expected_permission == "highest"
            else checks["summary_contains_full_auto"] and not checks["summary_contains_dangerous_flag"]
        )
        return checks

    overlay_path = run_context.get("overlay_path")
    overlay_payload = run_context.get("overlay_payload")
    checks["overlay_path"] = overlay_path
    checks["overlay_exists"] = bool(overlay_path and Path(str(overlay_path)).exists())
    if expected_permission != "highest":
        checks["backend_projection_ok"] = not checks["overlay_exists"]
        return checks

    permission_payload = overlay_payload.get("permission") if isinstance(overlay_payload, dict) else None
    checks["overlay_permissions"] = permission_payload
    required_allow_keys = ("edit", "bash", "webfetch", "doom_loop", "external_directory")
    checks["backend_projection_ok"] = bool(
        checks["overlay_exists"]
        and isinstance(permission_payload, dict)
        and all(permission_payload.get(key) == "allow" for key in required_allow_keys)
    )
    return checks


def _build_step_summary(
    *,
    backend: str,
    expected_permission: str,
    expected_reply_line: str,
    status_record: dict[str, Any],
    local_state: dict[str, Any],
    run_context: dict[str, Any],
) -> dict[str, Any]:
    parsed = status_record["parsed"]
    reply_text = str(parsed.get("reply_text") or "")
    thread_state = local_state.get("thread_state") or {}
    backend_checks = _verify_backend_projection(
        backend=backend,
        expected_permission=expected_permission,
        run_context=run_context,
    )
    checks = {
        "status_done": (parsed.get("status") or "").upper() == "DONE",
        "status_permission_ok": (parsed.get("permission") or "") == expected_permission,
        "thread_state_permission_ok": (thread_state.get("permission") or "") == expected_permission,
        "exact_reply_ok": _reply_has_exact_line(reply_text, expected_reply_line),
        **backend_checks,
    }
    checks["ok"] = all(bool(value) for key, value in checks.items() if key.endswith("_ok") or key == "status_done")
    return {
        "expected_permission": expected_permission,
        "expected_reply_line": expected_reply_line,
        "parsed_status": parsed,
        "thread_state_permission": thread_state.get("permission"),
        "backend_session_id": thread_state.get("backend_session_id"),
        "checks": checks,
        "run_context": {
            "run_dir": run_context.get("run_dir"),
            "summary_path": run_context.get("summary_path"),
            "prompt_path": run_context.get("prompt_path"),
            "overlay_path": run_context.get("overlay_path"),
        },
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live end-to-end permission smoke test through the real mailbox and backend."
    )
    parser.add_argument("--config", "-c", help="Path to the mail runner config file")
    parser.add_argument(
        "--sender-config",
        help="Optional sender mailbox config. Defaults to --config for single-mailbox smoke tests.",
    )
    parser.add_argument(
        "--backend",
        choices=sorted(BACKEND_SUBJECT_PREFIX),
        default="opencode",
        help="Backend to test",
    )
    parser.add_argument(
        "--repo",
        default=str(PROJECT_ROOT),
        help="Repo path used in the initial task mail",
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Workdir used in the initial task mail",
    )
    parser.add_argument(
        "--mode",
        choices=("analysis_only", "modify"),
        default="analysis_only",
        help="Task mode for the live smoke task",
    )
    parser.add_argument("--profile", help="Optional backend profile")
    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=20,
        help="Task timeout passed to the runner in the task mail",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=int,
        default=900,
        help="How long to wait for each terminal status mail",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=10,
        help="Mailbox poll interval",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=200,
        help="How many recent inbox messages to scan each poll",
    )
    parser.add_argument(
        "--to-addr",
        help="Destination mailbox address. Defaults to imap_user/from_addr/smtp_user.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "_tmp_live_mail_permission_smoke"),
        help="Directory where run artifacts are written",
    )
    parser.add_argument(
        "--run-name",
        help="Optional fixed subject suffix. Default is backend + timestamp + random token.",
    )
    parser.add_argument(
        "--initial-permission",
        choices=("default", "highest"),
        default="highest",
        help="Permission used on the initial task mail.",
    )
    parser.add_argument(
        "--reset-permission",
        choices=("default", "highest"),
        default="default",
        help="Permission used on the final explicit override step.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    bot_config = load_config(args.config)
    sender_config = load_config(args.sender_config) if args.sender_config else bot_config
    config_base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT
    to_addr = args.to_addr or bot_config.imap_user or bot_config.from_addr or bot_config.smtp_user
    if not to_addr:
        raise SystemExit("Unable to determine destination address. Pass --to-addr or set mail config credentials.")

    run_token = args.run_name or f"{args.backend}-permission-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir) / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    client = MailClient(sender_config)
    task_root = bot_config.resolve_task_root(config_base_dir)
    subject_text = f"live-permission-{run_token}"
    subject = f"[{BACKEND_SUBJECT_PREFIX[args.backend]}] {subject_text}"
    known_status_message_ids: set[str] = set()

    initial_token = secrets.token_hex(5).upper()
    inherit_token = secrets.token_hex(5).upper()
    reset_token = secrets.token_hex(5).upper()

    summary: dict[str, Any] = {
        "run_token": run_token,
        "backend": args.backend,
        "subject_text": subject_text,
        "subject": subject,
        "repo_path": args.repo,
        "workdir": args.workdir,
        "mode": args.mode,
        "profile": args.profile,
        "initial_permission": args.initial_permission,
        "reset_permission": args.reset_permission,
        "bot_mailbox": bot_config.from_addr or bot_config.smtp_user or bot_config.imap_user,
        "sender_mailbox": sender_config.from_addr or sender_config.smtp_user or sender_config.imap_user,
        "output_dir": str(run_dir),
        "steps": {},
        "passed": False,
    }

    try:
        initial_expected_line = f"PERMISSION_OK | initial | {initial_token}"
        initial_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=subject,
            body=_build_initial_task_mail(
                repo_path=args.repo,
                workdir=args.workdir,
                timeout_minutes=args.timeout_minutes,
                mode=args.mode,
                profile=args.profile,
                permission=args.initial_permission,
                task_text=_build_exact_reply_prompt("initial", initial_token),
            ),
            output_dir=run_dir,
            step_name="initial",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "initial",
            },
        )
        initial_status_record, thread_id, initial_observed = _wait_for_terminal_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=None,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="initial",
            output_dir=run_dir,
        )
        if not thread_id:
            raise RuntimeError("Initial step did not produce a thread_id.")
        initial_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "initial_local_state.json", initial_local_state)
        initial_run_context = _load_latest_run_context(task_root, thread_id, initial_local_state)
        initial_step = _build_step_summary(
            backend=args.backend,
            expected_permission=args.initial_permission,
            expected_reply_line=initial_expected_line,
            status_record=initial_status_record,
            local_state=initial_local_state,
            run_context=initial_run_context,
        )
        summary["thread_id"] = thread_id
        summary["steps"]["initial"] = {
            "sent_message_id": initial_message_id,
            "final_status": initial_status_record,
            "observed_statuses": initial_observed,
            "step_summary": initial_step,
        }
        if not initial_step["checks"]["ok"]:
            raise RuntimeError(f"Initial permission step failed checks: {json.dumps(initial_step['checks'], ensure_ascii=False)}")

        last_status_mail = initial_status_record["mail"]
        inherit_expected_line = f"PERMISSION_OK | inherit | {inherit_token}"
        inherit_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=f"Re: {last_status_mail['subject']}",
            body="/resume\n" + _build_exact_reply_prompt("inherit", inherit_token),
            output_dir=run_dir,
            step_name="resume_inherit",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "resume_inherit",
            },
            in_reply_to=last_status_mail["message_id"],
            references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
        )
        inherit_status_record, thread_id, inherit_observed = _wait_for_terminal_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="resume_inherit",
            output_dir=run_dir,
        )
        inherit_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "resume_inherit_local_state.json", inherit_local_state)
        inherit_run_context = _load_latest_run_context(task_root, thread_id, inherit_local_state)
        inherit_step = _build_step_summary(
            backend=args.backend,
            expected_permission=args.initial_permission,
            expected_reply_line=inherit_expected_line,
            status_record=inherit_status_record,
            local_state=inherit_local_state,
            run_context=inherit_run_context,
        )
        summary["steps"]["resume_inherit"] = {
            "sent_message_id": inherit_message_id,
            "final_status": inherit_status_record,
            "observed_statuses": inherit_observed,
            "step_summary": inherit_step,
        }
        if not inherit_step["checks"]["ok"]:
            raise RuntimeError(f"Inherited permission step failed checks: {json.dumps(inherit_step['checks'], ensure_ascii=False)}")

        last_status_mail = inherit_status_record["mail"]
        reset_expected_line = f"PERMISSION_OK | reset | {reset_token}"
        reset_body = "\n".join(
            [
                "/resume",
                f"Permission: {args.reset_permission}",
                _build_exact_reply_prompt("reset", reset_token),
            ]
        )
        reset_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=f"Re: {last_status_mail['subject']}",
            body=reset_body,
            output_dir=run_dir,
            step_name="resume_reset",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "resume_reset",
            },
            in_reply_to=last_status_mail["message_id"],
            references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
        )
        reset_status_record, thread_id, reset_observed = _wait_for_terminal_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="resume_reset",
            output_dir=run_dir,
        )
        reset_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "resume_reset_local_state.json", reset_local_state)
        reset_run_context = _load_latest_run_context(task_root, thread_id, reset_local_state)
        reset_step = _build_step_summary(
            backend=args.backend,
            expected_permission=args.reset_permission,
            expected_reply_line=reset_expected_line,
            status_record=reset_status_record,
            local_state=reset_local_state,
            run_context=reset_run_context,
        )
        summary["steps"]["resume_reset"] = {
            "sent_message_id": reset_message_id,
            "final_status": reset_status_record,
            "observed_statuses": reset_observed,
            "step_summary": reset_step,
        }
        if not reset_step["checks"]["ok"]:
            raise RuntimeError(f"Reset permission step failed checks: {json.dumps(reset_step['checks'], ensure_ascii=False)}")

        summary["passed"] = True
        return 0
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        _write_json(run_dir / "result.json", summary)
        print(f"result: {run_dir / 'result.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
