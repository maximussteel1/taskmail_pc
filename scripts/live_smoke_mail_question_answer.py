"""Live mailbox smoke test for the real-backend [QUESTION] -> ANSWER -> DONE flow."""

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
    _build_initial_task_mail,
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


def _build_question_prompt(answer_token: str) -> str:
    return "\n".join(
        [
            "This is a live mailbox smoke test for the explicit question/answer flow.",
            "Do not modify files and do not run tests.",
            "If the task text already contains an 'Answer to pending question' block with an 'Answer:' line,",
            "reply with exactly one line and nothing else:",
            f"QUESTION_FLOW_OK | {answer_token}",
            "Otherwise output exactly one question capsule and nothing else:",
            "---TASK-QUESTION-BEGIN---",
            "question_set_id: live_mailbox_question_flow",
            "question_id: live_mailbox_answer",
            "question_type: short_text",
            "required: true",
            f"question_text: Reply with the exact token {answer_token}",
            "---TASK-QUESTION-END---",
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


def _require_supported_backend(*, backend: str, config) -> None:
    if backend == "codex" and str(config.codex_transport_default or "").strip().lower() != "cli":
        raise SystemExit(
            "Question-answer live smoke on Codex currently requires `codex_transport_default: cli`. "
            "The default SDK transport does not yet project question capsules into awaiting-user-input state."
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live mailbox smoke test for the real-backend [QUESTION] -> ANSWER -> DONE flow."
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
        help="Backend to test. Codex currently requires the live host to use `codex_transport_default: cli`.",
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
        help="How long to wait for each question/done status mail",
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
        default=str(PROJECT_ROOT / "_tmp_live_mail_question_smoke"),
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
    _require_supported_backend(backend=args.backend, config=bot_config)

    config_base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT
    to_addr = args.to_addr or bot_config.imap_user or bot_config.from_addr or bot_config.smtp_user
    if not to_addr:
        raise SystemExit("Unable to determine destination address. Pass --to-addr or set mail config credentials.")

    run_token = args.run_name or f"{args.backend}-question-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir) / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    client = MailClient(sender_config)
    task_root = bot_config.resolve_task_root(config_base_dir)
    subject_text = f"live-question-{run_token}"
    subject = f"[{BACKEND_SUBJECT_PREFIX[args.backend]}] {subject_text}"
    answer_token = secrets.token_hex(5).upper()
    known_status_message_ids: set[str] = set()

    summary: dict[str, Any] = {
        "run_token": run_token,
        "backend": args.backend,
        "subject_text": subject_text,
        "subject": subject,
        "answer_token": answer_token,
        "repo_path": args.repo,
        "workdir": args.workdir,
        "mode": args.mode,
        "profile": args.profile,
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
                task_text=_build_question_prompt(answer_token),
            ),
            output_dir=run_dir,
            step_name="initial",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "initial",
            },
        )
        question_record, thread_id, question_observed = _wait_for_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=None,
            known_status_message_ids=known_status_message_ids,
            target_labels={"QUESTION"},
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="question",
            output_dir=run_dir,
        )
        if not thread_id:
            raise RuntimeError("Question step did not produce a thread_id.")

        question_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "question_local_state.json", question_local_state)
        thread_state = question_local_state.get("thread_state") or {}
        latest_result = question_local_state.get("latest_result") or {}
        pending_questions = list(thread_state.get("pending_questions") or [])
        question_checks = {
            "status_question": (question_record["parsed"].get("status") or "").upper() == "QUESTION",
            "thread_state_awaiting": (thread_state.get("status") or "") == "awaiting_user_input",
            "latest_result_awaiting": (latest_result.get("status") or "") == "awaiting_user_input",
            "pending_question_count_ok": len(pending_questions) == 1,
            "pending_question_id_ok": (thread_state.get("pending_question_id") or "") == "live_mailbox_answer",
            "question_text_contains_token": answer_token in str(thread_state.get("pending_question_text") or ""),
            "question_mail_contains_token": answer_token in str(question_record["mail"].get("body_text") or ""),
        }
        question_checks["ok"] = all(question_checks.values())
        summary["thread_id"] = thread_id
        summary["steps"]["question"] = {
            "sent_message_id": initial_message_id,
            "status_mail": question_record,
            "observed_statuses": question_observed,
            "backend_session_id": _extract_backend_session_id(question_local_state),
            "checks": question_checks,
        }
        if not question_checks["ok"]:
            raise RuntimeError(f"Question step failed checks: {json.dumps(question_checks, ensure_ascii=False)}")

        last_status_mail = question_record["mail"]
        answer_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=f"Re: {last_status_mail['subject']}",
            body=answer_token + "\n",
            output_dir=run_dir,
            step_name="answer",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "answer",
            },
            in_reply_to=last_status_mail["message_id"],
            references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
        )
        done_record, thread_id, done_observed = _wait_for_status(
            config=sender_config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            target_labels={"DONE"},
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="done",
            output_dir=run_dir,
        )
        done_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "done_local_state.json", done_local_state)
        done_thread_state = done_local_state.get("thread_state") or {}
        done_latest_result = done_local_state.get("latest_result") or {}
        done_checks = {
            "status_done": (done_record["parsed"].get("status") or "").upper() == "DONE",
            "reply_line_ok": str(done_record["parsed"].get("reply_text") or "").strip() == f"QUESTION_FLOW_OK | {answer_token}",
            "thread_state_done": (done_thread_state.get("status") or "") == "done",
            "latest_result_success": (done_latest_result.get("status") or "") == "success",
            "pending_question_cleared": not bool(done_thread_state.get("pending_question_id") or done_thread_state.get("pending_questions")),
        }
        done_checks["ok"] = all(done_checks.values())
        summary["steps"]["done"] = {
            "sent_message_id": answer_message_id,
            "status_mail": done_record,
            "observed_statuses": done_observed,
            "backend_session_id": _extract_backend_session_id(done_local_state),
            "checks": done_checks,
        }
        if not done_checks["ok"]:
            raise RuntimeError(f"Done step failed checks: {json.dumps(done_checks, ensure_ascii=False)}")

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
