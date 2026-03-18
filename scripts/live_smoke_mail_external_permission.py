"""Live mailbox smoke test for external-directory permission gating."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_smoke_mail_permission import (  # type: ignore[import-not-found]
    _build_initial_task_mail,
    _load_latest_run_context,
    _parse_status_mail_body,
    _verify_backend_projection,
    _wait_for_terminal_status,
)
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
from mail_runner.mail_io import MailClient, SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE


def _build_create_prompt(step_name: str, target_path: Path) -> str:
    target = str(target_path)
    return "\n".join(
        [
            "This is a live smoke test for external directory permissions.",
            f"Attempt to create exactly this directory outside the repo: {target}",
            "Do not create any other files or directories.",
            "Use the real backend permissions of this run. Do not ask for approval.",
            "After the attempt, check whether that exact directory exists.",
            "If it exists, reply with exactly one line and nothing else:",
            f"EXTERNAL_DIR_CREATED | {step_name} | {target}",
            "If it does not exist, reply with exactly one line and nothing else:",
            f"EXTERNAL_DIR_BLOCKED | {step_name} | {target}",
        ]
    )


def _build_remove_prompt(step_name: str, target_path: Path) -> str:
    target = str(target_path)
    return "\n".join(
        [
            "This is a live smoke test for external directory permissions.",
            f"Attempt to remove exactly this directory outside the repo: {target}",
            "Do not remove any sibling directories or parent directories.",
            "Use the real backend permissions of this run. Do not ask for approval.",
            "After the attempt, check whether that exact directory still exists.",
            "If it no longer exists, reply with exactly one line and nothing else:",
            f"EXTERNAL_DIR_REMOVED | {step_name} | {target}",
            "If it still exists, reply with exactly one line and nothing else:",
            f"EXTERNAL_DIR_REMOVE_BLOCKED | {step_name} | {target}",
        ]
    )


def _step_summary(
    *,
    backend: str,
    expected_permission: str,
    expected_reply_line: str,
    expect_dir_exists: bool,
    status_record: dict[str, Any],
    local_state: dict[str, Any],
    run_context: dict[str, Any],
    target_path: Path,
) -> dict[str, Any]:
    parsed = status_record["parsed"]
    reply_text = str(parsed.get("reply_text") or "")
    thread_state = local_state.get("thread_state") or {}
    backend_checks = _verify_backend_projection(
        backend=backend,
        expected_permission=expected_permission,
        run_context=run_context,
    )
    dir_exists = target_path.exists()
    checks: dict[str, Any] = {
        "status_done": (parsed.get("status") or "").upper() == "DONE",
        "status_permission_ok": (parsed.get("permission") or "") == expected_permission,
        "thread_state_permission_ok": (thread_state.get("permission") or "") == expected_permission,
        "exact_reply_ok": _reply_has_exact_line(reply_text, expected_reply_line),
        "filesystem_state_ok": dir_exists == expect_dir_exists,
        **backend_checks,
    }
    checks["ok"] = all(bool(value) for key, value in checks.items() if key.endswith("_ok") or key == "status_done")
    return {
        "expected_permission": expected_permission,
        "expected_reply_line": expected_reply_line,
        "expected_dir_exists": expect_dir_exists,
        "actual_dir_exists": dir_exists,
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
        description="Run a live mailbox smoke test for external-directory permission gating."
    )
    parser.add_argument("--config", "-c", help="Path to the mail runner config file")
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
        default=1200,
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
        default=str(PROJECT_ROOT / "_tmp_live_mail_external_permission_smoke"),
        help="Directory where run artifacts are written",
    )
    parser.add_argument(
        "--run-name",
        help="Optional fixed subject suffix. Default is backend + timestamp + random token.",
    )
    parser.add_argument(
        "--target-dir",
        default=r"D:\projects\test_dir",
        help="Absolute external directory path used for the permission gate test.",
    )
    parser.add_argument(
        "--keep-target",
        action="store_true",
        help="Do not remove the target directory locally if the smoke test exits early.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    config = load_config(args.config)
    config_base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT
    to_addr = args.to_addr or config.imap_user or config.from_addr or config.smtp_user
    if not to_addr:
        raise SystemExit("Unable to determine destination address. Pass --to-addr or set mail config credentials.")

    target_path = Path(args.target_dir)
    if target_path.exists():
        raise SystemExit(f"Target already exists, refusing to reuse it: {target_path}")
    if not target_path.parent.exists():
        raise SystemExit(f"Target parent directory does not exist: {target_path.parent}")

    run_token = args.run_name or f"{args.backend}-external-permission-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir) / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    client = MailClient(config)
    task_root = config.resolve_task_root(config_base_dir)
    subject_text = f"live-external-permission-{run_token}"
    subject = f"[{BACKEND_SUBJECT_PREFIX[args.backend]}] {subject_text}"
    known_status_message_ids: set[str] = set()

    summary: dict[str, Any] = {
        "run_token": run_token,
        "backend": args.backend,
        "subject_text": subject_text,
        "subject": subject,
        "repo_path": args.repo,
        "workdir": args.workdir,
        "profile": args.profile,
        "target_dir": str(target_path),
        "output_dir": str(run_dir),
        "steps": {},
        "passed": False,
        "host_cleanup_performed": False,
    }

    try:
        default_expected_line = f"EXTERNAL_DIR_BLOCKED | default_create | {target_path}"
        initial_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=subject,
            body=_build_initial_task_mail(
                repo_path=args.repo,
                workdir=args.workdir,
                timeout_minutes=args.timeout_minutes,
                mode="modify",
                profile=args.profile,
                permission="default",
                task_text=_build_create_prompt("default_create", target_path),
            ),
            output_dir=run_dir,
            step_name="default_create",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "default_create",
            },
        )
        default_status_record, thread_id, default_observed = _wait_for_terminal_status(
            config=config,
            subject_text=subject_text,
            thread_id=None,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="default_create",
            output_dir=run_dir,
        )
        if not thread_id:
            raise RuntimeError("Default permission step did not produce a thread_id.")
        default_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "default_create_local_state.json", default_local_state)
        default_run_context = _load_latest_run_context(task_root, thread_id, default_local_state)
        default_step = _step_summary(
            backend=args.backend,
            expected_permission="default",
            expected_reply_line=default_expected_line,
            expect_dir_exists=False,
            status_record=default_status_record,
            local_state=default_local_state,
            run_context=default_run_context,
            target_path=target_path,
        )
        summary["thread_id"] = thread_id
        summary["steps"]["default_create"] = {
            "sent_message_id": initial_message_id,
            "final_status": default_status_record,
            "observed_statuses": default_observed,
            "step_summary": default_step,
        }
        if not default_step["checks"]["ok"]:
            raise RuntimeError(
                f"Default external-directory gate failed checks: {json.dumps(default_step['checks'], ensure_ascii=False)}"
            )

        last_status_mail = default_status_record["mail"]
        highest_create_expected_line = f"EXTERNAL_DIR_CREATED | highest_create | {target_path}"
        highest_create_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=f"Re: {last_status_mail['subject']}",
            body="\n".join(
                [
                    "/resume",
                    "Permission: highest",
                    _build_create_prompt("highest_create", target_path),
                ]
            ),
            output_dir=run_dir,
            step_name="highest_create",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "highest_create",
            },
            in_reply_to=last_status_mail["message_id"],
            references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
        )
        highest_create_status_record, thread_id, highest_create_observed = _wait_for_terminal_status(
            config=config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="highest_create",
            output_dir=run_dir,
        )
        highest_create_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "highest_create_local_state.json", highest_create_local_state)
        highest_create_run_context = _load_latest_run_context(task_root, thread_id, highest_create_local_state)
        highest_create_step = _step_summary(
            backend=args.backend,
            expected_permission="highest",
            expected_reply_line=highest_create_expected_line,
            expect_dir_exists=True,
            status_record=highest_create_status_record,
            local_state=highest_create_local_state,
            run_context=highest_create_run_context,
            target_path=target_path,
        )
        summary["steps"]["highest_create"] = {
            "sent_message_id": highest_create_message_id,
            "final_status": highest_create_status_record,
            "observed_statuses": highest_create_observed,
            "step_summary": highest_create_step,
        }
        if not highest_create_step["checks"]["ok"]:
            raise RuntimeError(
                f"Highest permission create step failed checks: {json.dumps(highest_create_step['checks'], ensure_ascii=False)}"
            )

        last_status_mail = highest_create_status_record["mail"]
        highest_cleanup_expected_line = f"EXTERNAL_DIR_REMOVED | highest_cleanup | {target_path}"
        highest_cleanup_message_id = _send_and_record_mail(
            client=client,
            to_addr=to_addr,
            subject=f"Re: {last_status_mail['subject']}",
            body="\n".join(
                [
                    "/resume",
                    "Permission: highest",
                    _build_remove_prompt("highest_cleanup", target_path),
                ]
            ),
            output_dir=run_dir,
            step_name="highest_cleanup",
            headers={
                "X-Live-Smoke-Run": run_token,
                "X-Live-Smoke-Step": "highest_cleanup",
            },
            in_reply_to=last_status_mail["message_id"],
            references=_dedupe_references([*last_status_mail.get("references", []), last_status_mail["message_id"]]),
        )
        highest_cleanup_status_record, thread_id, highest_cleanup_observed = _wait_for_terminal_status(
            config=config,
            subject_text=subject_text,
            thread_id=thread_id,
            known_status_message_ids=known_status_message_ids,
            timeout_seconds=args.poll_timeout_seconds,
            interval_seconds=args.poll_interval_seconds,
            scan_limit=args.scan_limit,
            step_name="highest_cleanup",
            output_dir=run_dir,
        )
        highest_cleanup_local_state = _load_local_thread_snapshot(task_root, thread_id)
        _write_json(run_dir / "highest_cleanup_local_state.json", highest_cleanup_local_state)
        highest_cleanup_run_context = _load_latest_run_context(task_root, thread_id, highest_cleanup_local_state)
        highest_cleanup_step = _step_summary(
            backend=args.backend,
            expected_permission="highest",
            expected_reply_line=highest_cleanup_expected_line,
            expect_dir_exists=False,
            status_record=highest_cleanup_status_record,
            local_state=highest_cleanup_local_state,
            run_context=highest_cleanup_run_context,
            target_path=target_path,
        )
        summary["steps"]["highest_cleanup"] = {
            "sent_message_id": highest_cleanup_message_id,
            "final_status": highest_cleanup_status_record,
            "observed_statuses": highest_cleanup_observed,
            "step_summary": highest_cleanup_step,
        }
        summary["backend_cleanup_ok"] = bool(highest_cleanup_step["checks"]["ok"])

        summary["passed"] = True
        return 0
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        if target_path.exists() and not args.keep_target:
            shutil.rmtree(target_path, ignore_errors=True)
            summary["host_cleanup_performed"] = True
        summary["target_exists_after_exit"] = target_path.exists()
        _write_json(run_dir / "result.json", summary)
        print(f"result: {run_dir / 'result.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
