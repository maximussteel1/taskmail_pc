"""Live mailbox smoke test for the real-backend running-task kill flow."""

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
    TERMINAL_STATUS_LABELS,
    _dedupe_references,
    _envelope_to_dict,
    _extract_backend_session_id,
    _load_local_thread_snapshot,
    _parse_status_mail_body,
    _scan_recent_messages,
    _send_and_record_mail,
    _subject_matches,
    _timestamp_slug,
    _write_json,
)
from mail_runner.config import PROJECT_ROOT, load_config
from mail_runner.mail_io import MailClient, SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE


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


def _build_kill_prompt() -> str:
    return "\n".join(
        [
            "This is a live mailbox smoke test for kill handling.",
            "Do not modify files and do not run tests.",
            "Run exactly this command and wait for it to finish:",
            'powershell -NoProfile -Command "Start-Sleep -Seconds 180"',
            "If the command completes naturally before the task is killed, reply with exactly one line:",
            "KILL_SMOKE_UNEXPECTEDLY_COMPLETED",
        ]
    )


def _wait_for_status(
    *,
    config,
    subject_text: str,
    thread_id: str | None,
    known_status_message_ids: set[str],
    target_labels: set[str],
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

            status_label = str(parsed.get("status") or "").upper()
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
                f"[{step_name}] observed status={status_label or '?'} "
                f"thread={parsed.get('thread_id') or current_thread_id or '?'} "
                f"task={parsed.get('task_id') or '?'}"
            )

            if status_label in target_labels or status_label in TERMINAL_STATUS_LABELS:
                return record, current_thread_id, observed

        time.sleep(interval_seconds)

    raise TimeoutError(
        f"Timed out after {timeout_seconds}s waiting for status {sorted(target_labels)} "
        f"for subject '{subject_text}'."
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live mailbox smoke test for the real-backend running-task kill flow."
    )
    parser.add_argument("--config", "-c", help="Path to the mail runner config file")
    parser.add_argument(
        "--sender-config",
        help="Optional sender mailbox config. Defaults to --config for single-mailbox smoke tests.",
    )
    parser.add_argument(
        "--backend",
        choices=sorted(BACKEND_SUBJECT_PREFIX),
        default="codex",
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
        default="modify",
        help="Task mode for the kill smoke task",
    )
    parser.add_argument("--profile", help="Optional backend profile")
    parser.add_argument(
        "--permission",
        choices=("default", "highest"),
        default="highest",
        help="Permission used on the initial task mail.",
    )
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
        help="How long to wait for running and killed status mails",
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
        default=str(PROJECT_ROOT / "_tmp_live_mail_kill_smoke"),
        help="Directory where run artifacts are written",
    )
    parser.add_argument(
        "--run-name",
        help="Optional fixed subject suffix. Default is backend + timestamp + random token.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    bot_config = load_config(args.config)
    sender_config = load_config(args.sender_config) if args.sender_config else bot_config
    to_addr = args.to_addr or bot_config.imap_user or bot_config.from_addr or bot_config.smtp_user
    if not to_addr:
        raise SystemExit("Unable to determine destination address. Pass --to-addr or set mail config credentials.")

    config_base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT
    run_token = args.run_name or f"{args.backend}-kill-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir) / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    client = MailClient(sender_config)
    task_root = bot_config.resolve_task_root(config_base_dir)
    subject_text = f"live-kill-{run_token}"
    subject = f"[{BACKEND_SUBJECT_PREFIX[args.backend]}] {subject_text}"
    known_status_message_ids: set[str] = set()

    summary: dict[str, Any] = {
        "run_token": run_token,
        "backend": args.backend,
        "subject_text": subject_text,
        "subject": subject,
        "repo_path": args.repo,
        "workdir": args.workdir,
        "mode": args.mode,
        "profile": args.profile,
        "permission": args.permission,
        "bot_mailbox": bot_config.from_addr or bot_config.smtp_user or bot_config.imap_user,
        "sender_mailbox": sender_config.from_addr or sender_config.smtp_user or sender_config.imap_user,
        "output_dir": str(run_dir),
        "steps": {},
        "passed": False,
    }

    try:
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
                permission=args.permission,
                task_text=_build_kill_prompt(),
            ),
            output_dir=run_dir,
            step_name="initial",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "initial",
            },
        )
        running_record, thread_id, running_observed = _wait_for_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=None,
            known_status_message_ids=known_status_message_ids,
            target_labels={"RUNNING"},
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="running",
            output_dir=run_dir,
        )
        if not thread_id:
            raise RuntimeError("Running step did not produce a thread_id.")

        running_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "running_local_state.json", running_local_state)
        running_thread_state = running_local_state.get("thread_state") or {}
        running_checks = {
            "status_running": (running_record["parsed"].get("status") or "").upper() == "RUNNING",
            "thread_state_running": (running_thread_state.get("status") or "") == "running",
        }
        running_checks["ok"] = all(running_checks.values())
        summary["thread_id"] = thread_id
        summary["steps"]["running"] = {
            "sent_message_id": initial_message_id,
            "status_mail": running_record,
            "observed_statuses": running_observed,
            "backend_session_id": _extract_backend_session_id(running_local_state),
            "checks": running_checks,
        }
        if not running_checks["ok"]:
            raise RuntimeError(f"Running step failed checks: {json.dumps(running_checks, ensure_ascii=False)}")

        last_status_mail = running_record["mail"]
        kill_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=f"Re: {last_status_mail['subject']}",
            body="/kill\n",
            output_dir=run_dir,
            step_name="kill",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "kill",
            },
            in_reply_to=last_status_mail["message_id"],
            references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
        )
        killed_record, thread_id, killed_observed = _wait_for_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            target_labels={"KILLED"},
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="killed",
            output_dir=run_dir,
        )
        killed_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "killed_local_state.json", killed_local_state)
        killed_thread_state = killed_local_state.get("thread_state") or {}
        killed_latest_result = killed_local_state.get("latest_result") or {}
        killed_checks = {
            "status_killed": (killed_record["parsed"].get("status") or "").upper() == "KILLED",
            "thread_state_killed": (killed_thread_state.get("status") or "") == "killed",
            "latest_result_killed": (killed_latest_result.get("status") or "") == "killed",
            "latest_result_error_mentions_kill": "kill" in str(killed_latest_result.get("error_message") or "").lower(),
        }
        killed_checks["ok"] = all(killed_checks.values())
        summary["steps"]["killed"] = {
            "sent_message_id": kill_message_id,
            "status_mail": killed_record,
            "observed_statuses": killed_observed,
            "backend_session_id": _extract_backend_session_id(killed_local_state),
            "checks": killed_checks,
        }
        if not killed_checks["ok"]:
            raise RuntimeError(f"Killed step failed checks: {json.dumps(killed_checks, ensure_ascii=False)}")

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
