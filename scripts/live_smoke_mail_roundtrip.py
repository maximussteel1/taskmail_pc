"""Live end-to-end smoke test through the real mailbox and real backend."""

from __future__ import annotations

import argparse
import imaplib
import json
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.config import PROJECT_ROOT, load_config
from mail_runner.mail_io import (
    SYSTEM_MESSAGE_HEADER,
    SYSTEM_MESSAGE_HEADER_VALUE,
    MailClient,
    message_bytes_to_envelope,
)
from mail_runner.workspace import WorkspaceManager

BACKEND_SUBJECT_PREFIX = {
    "opencode": "OC",
    "codex": "CX",
}
TERMINAL_STATUS_LABELS = {"DONE", "FAILED", "KILLED", "QUESTION", "PAUSED"}


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _envelope_to_dict(envelope) -> dict[str, Any]:
    return {
        "message_id": envelope.message_id,
        "subject": envelope.subject,
        "from_addr": envelope.from_addr,
        "to_addr": envelope.to_addr,
        "date": str(envelope.date),
        "in_reply_to": envelope.in_reply_to,
        "references": list(envelope.references),
        "body_text": envelope.body_text,
        "raw_headers": dict(envelope.raw_headers),
    }


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
        for label in ("Status", "Session ID", "Thread ID", "Task ID", "Backend", "Repo", "Workdir", "Exit Code", "Error"):
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
        "exit_code": fields.get("Exit Code", ""),
        "error": fields.get("Error", ""),
        "reply_text": "\n".join(reply_lines).strip(),
    }


def _scan_recent_messages(config, *, scan_limit: int) -> list[dict[str, Any]]:
    client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
    messages: list[dict[str, Any]] = []
    try:
        client.login(config.imap_user, config.imap_password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Unable to select INBOX.")
        status, data = client.search(None, "ALL")
        if status != "OK":
            raise RuntimeError("Unable to search mailbox.")
        mail_ids = data[0].split()
        if scan_limit > 0:
            mail_ids = mail_ids[-scan_limit:]
        for raw_id in mail_ids:
            status, payload = client.fetch(raw_id, "(BODY.PEEK[])")
            if status != "OK" or not payload or not payload[0]:
                continue
            message_bytes = payload[0][1]
            envelope = message_bytes_to_envelope(message_bytes, raw_id.decode("ascii", errors="ignore"))
            messages.append(
                {
                    "imap_id": raw_id.decode("ascii", errors="ignore"),
                    "envelope": envelope,
                }
            )
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass
    return messages


def _dedupe_references(references: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in references:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _build_initial_task_mail(
    *,
    repo_path: str,
    workdir: str | None,
    timeout_minutes: int,
    mode: str,
    profile: str | None,
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
    lines.extend(["Task:", task_text.strip()])
    return "\n".join(lines).strip() + "\n"


def _build_memory_seed_prompt(token: str) -> str:
    return "\n".join(
        [
            "This is a live smoke test.",
            "Do not modify any files and do not run tests.",
            "Reply with exactly one line and nothing else:",
            f"MEMORY_TOKEN: {token}",
            "Remember that token for the next turn.",
        ]
    )


def _build_memory_resume_prompt() -> str:
    return "\n".join(
        [
            "Reply with exactly one line and nothing else.",
            "Fill in the actual token from the previous turn:",
            "MEMORY_RECALL: <actual token>",
        ]
    )


def _build_websearch_prompt() -> str:
    return "\n".join(
        [
            "Search the web for a live source that shows the current UTC date and time.",
            "Reply with exactly one line in this format:",
            "WEBSEARCH_OK | <source-domain> | <UTC date and time>",
            "If web search is unavailable, reply exactly:",
            "NO_WEBSEARCH",
        ]
    )


def _subject_matches(subject: str, subject_text: str, thread_id: str | None) -> bool:
    lowered_subject = subject.lower()
    if thread_id and f"[s:{thread_id.lower()}]" in lowered_subject:
        return True
    return subject_text.lower() in lowered_subject


def _load_local_thread_snapshot(task_root: Path, thread_id: str) -> dict[str, Any]:
    workspace = WorkspaceManager(task_root)
    thread_dir = workspace.thread_dir(thread_id)
    thread_state_path = workspace.thread_state_path(thread_id)
    if not thread_state_path.exists():
        return {}

    thread_state = workspace.load_json(thread_state_path)
    latest_result: dict[str, Any] | None = None
    latest_stdout_tail: list[str] | None = None
    latest_stderr_tail: list[str] | None = None
    history_files = list(thread_state.get("history_files") or [])
    if history_files:
        latest_result_path = thread_dir / history_files[-1]
        if latest_result_path.exists():
            latest_result = workspace.load_json(latest_result_path)
            stdout_rel = str(latest_result.get("stdout_file") or "").strip()
            stderr_rel = str(latest_result.get("stderr_file") or "").strip()
            if stdout_rel:
                stdout_path = thread_dir / stdout_rel
                if stdout_path.exists():
                    latest_stdout_tail = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            if stderr_rel:
                stderr_path = thread_dir / stderr_rel
                if stderr_path.exists():
                    latest_stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]

    return {
        "thread_state": thread_state,
        "latest_result": latest_result,
        "latest_stdout_tail": latest_stdout_tail,
        "latest_stderr_tail": latest_stderr_tail,
    }


def _extract_backend_session_id(local_state: dict[str, Any]) -> str | None:
    thread_state = local_state.get("thread_state") or {}
    latest_result = local_state.get("latest_result") or {}
    for payload in (latest_result, thread_state):
        value = str(payload.get("backend_session_id") or "").strip()
        if value:
            return value
    return None


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


def _reply_has_exact_line(reply_text: str, expected_line: str) -> bool:
    return any(line.strip() == expected_line for line in reply_text.splitlines())


def _first_nonempty_line(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return ""


def _send_and_record_mail(
    *,
    client: MailClient,
    to_addr: str,
    subject: str,
    body: str,
    output_dir: Path,
    step_name: str,
    headers: dict[str, str],
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> str:
    message_id = client.send_mail(
        to_addr=to_addr,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
        headers=headers,
    )
    _write_json(
        output_dir / f"{step_name}_sent.json",
        {
            "message_id": message_id,
            "subject": subject,
            "in_reply_to": in_reply_to,
            "references": list(references or []),
            "headers": headers,
            "body": body,
        },
    )
    print(f"[{step_name}] sent message_id={message_id}")
    return message_id


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live end-to-end smoke test through the real mailbox and backend."
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
        default=str(PROJECT_ROOT / "_tmp_live_mail_smoke"),
        help="Directory where run artifacts are written",
    )
    parser.add_argument(
        "--run-name",
        help="Optional fixed subject suffix. Default is backend + timestamp + random token.",
    )
    parser.add_argument(
        "--skip-websearch",
        action="store_true",
        help="Skip the websearch follow-up step",
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

    run_token = args.run_name or f"{args.backend}-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir) / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    client = MailClient(sender_config)
    task_root = bot_config.resolve_task_root(config_base_dir)
    subject_text = f"live-smoke-{run_token}"
    subject = f"[{BACKEND_SUBJECT_PREFIX[args.backend]}] {subject_text}"
    memory_token = secrets.token_hex(6).upper()

    summary: dict[str, Any] = {
        "run_token": run_token,
        "backend": args.backend,
        "subject_text": subject_text,
        "subject": subject,
        "memory_token": memory_token,
        "repo_path": args.repo,
        "workdir": args.workdir,
        "mode": args.mode,
        "profile": args.profile,
        "bot_mailbox": bot_config.from_addr or bot_config.smtp_user or bot_config.imap_user,
        "sender_mailbox": sender_config.from_addr or sender_config.smtp_user or sender_config.imap_user,
        "output_dir": str(run_dir),
        "steps": {},
        "checks": {},
        "passed": False,
    }
    known_status_message_ids: set[str] = set()

    try:
        initial_body = _build_initial_task_mail(
            repo_path=args.repo,
            workdir=args.workdir,
            timeout_minutes=args.timeout_minutes,
            mode=args.mode,
            profile=args.profile,
            task_text=_build_memory_seed_prompt(memory_token),
        )
        initial_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=subject,
            body=initial_body,
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
        initial_reply_text = str(initial_status_record["parsed"].get("reply_text") or "")
        initial_local_state = _load_local_thread_snapshot(task_root, thread_id) if thread_id else {}
        _write_json(run_dir / "initial_local_state.json", initial_local_state)
        summary["thread_id"] = thread_id
        summary["steps"]["initial"] = {
            "sent_message_id": initial_message_id,
            "final_status": initial_status_record,
            "observed_statuses": initial_observed,
            "local_state": initial_local_state,
        }
        summary["checks"]["backend_session_id_initial"] = _extract_backend_session_id(initial_local_state)

        initial_ok = (
            (initial_status_record["parsed"].get("status") or "").upper() == "DONE"
            and _reply_has_exact_line(initial_reply_text, f"MEMORY_TOKEN: {memory_token}")
        )
        summary["checks"]["initial_done"] = initial_ok
        if not initial_ok:
            raise RuntimeError("Initial live smoke step did not return the expected memory token.")

        last_status_mail = initial_status_record["mail"]
        followup_subject = f"Re: {last_status_mail['subject']}"
        followup_refs = _dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]])
        resume_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=followup_subject,
            body="/resume\n" + _build_memory_resume_prompt(),
            output_dir=run_dir,
            step_name="resume_memory",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "resume_memory",
            },
            in_reply_to=last_status_mail["message_id"],
            references=followup_refs,
        )
        resume_status_record, thread_id, resume_observed = _wait_for_terminal_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="resume_memory",
            output_dir=run_dir,
        )
        resume_reply_text = str(resume_status_record["parsed"].get("reply_text") or "")
        resume_local_state = _load_local_thread_snapshot(task_root, thread_id) if thread_id else {}
        _write_json(run_dir / "resume_memory_local_state.json", resume_local_state)
        summary["steps"]["resume_memory"] = {
            "sent_message_id": resume_message_id,
            "final_status": resume_status_record,
            "observed_statuses": resume_observed,
            "local_state": resume_local_state,
        }
        summary["checks"]["backend_session_id_resume"] = _extract_backend_session_id(resume_local_state)

        memory_ok = (
            (resume_status_record["parsed"].get("status") or "").upper() == "DONE"
            and _reply_has_exact_line(resume_reply_text, f"MEMORY_RECALL: {memory_token}")
        )
        summary["checks"]["memory_resume_ok"] = memory_ok
        if not memory_ok:
            raise RuntimeError("Resume step did not recall the expected memory token.")

        if not args.skip_websearch:
            last_status_mail = resume_status_record["mail"]
            websearch_message_id = _send_and_record_mail(
                client=client,
                to_addr=to_addr,
                subject=f"Re: {last_status_mail['subject']}",
                body="/resume\n" + _build_websearch_prompt(),
                output_dir=run_dir,
                step_name="websearch",
                headers={
                    "X-Live-Smoke-Run": run_token,
                    "X-Live-Smoke-Step": "websearch",
                },
                in_reply_to=last_status_mail["message_id"],
                references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
            )
            websearch_status_record, thread_id, websearch_observed = _wait_for_terminal_status(
                config=sender_config,
                subject_text=subject_text,
                thread_id=thread_id,
                known_status_message_ids=known_status_message_ids,
                timeout_seconds=args.poll_timeout_seconds,
                interval_seconds=args.poll_interval_seconds,
                scan_limit=args.scan_limit,
                step_name="websearch",
                output_dir=run_dir,
            )
            websearch_reply_text = str(websearch_status_record["parsed"].get("reply_text") or "")
            websearch_local_state = _load_local_thread_snapshot(task_root, thread_id) if thread_id else {}
            _write_json(run_dir / "websearch_local_state.json", websearch_local_state)
            summary["steps"]["websearch"] = {
                "sent_message_id": websearch_message_id,
                "final_status": websearch_status_record,
                "observed_statuses": websearch_observed,
                "local_state": websearch_local_state,
            }
            summary["checks"]["backend_session_id_websearch"] = _extract_backend_session_id(websearch_local_state)

            websearch_line = _first_nonempty_line(websearch_reply_text)
            websearch_ok = (
                (websearch_status_record["parsed"].get("status") or "").upper() == "DONE"
                and websearch_line.startswith("WEBSEARCH_OK | ")
            )
            summary["checks"]["websearch_ok"] = websearch_ok
            summary["checks"]["websearch_line"] = websearch_line
            if not websearch_ok:
                raise RuntimeError("Websearch step did not return the expected WEBSEARCH_OK response.")

        summary["passed"] = True
        return 0
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        _write_json(run_dir / "result.json", summary)
        print(f"result: {run_dir / 'result.json'}")
        if summary.get("checks"):
            print(json.dumps(summary["checks"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
