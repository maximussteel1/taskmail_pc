from __future__ import annotations

import json
import os
from pathlib import Path

from mail_runner.host_state import HOST_STATUS_RUNNING, HostStateStore
from mail_runner.models import RunResult, ThreadState
from mail_runner.runtime_health_report import build_runtime_health_report, render_runtime_health_report
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, save_thread_state
from mail_runner.workspace import WorkspaceManager


def _write_runtime_config(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "mail_config.loop_30s.yaml"
    config_path.write_text("task_root: tasks\n", encoding="utf-8")
    return config_path


def _thread_state(
    *,
    thread_id: str = "thread_001",
    status: str,
    current_task_id: str,
    history_files: list[str],
    updated_at: str,
    backend_transport: str = "sdk",
) -> ThreadState:
    repo_path = "E:\\repo"
    workdir = "."
    workspace_id = build_workspace_id(repo_path, workdir)
    return ThreadState(
        thread_id=thread_id,
        root_message_id=f"<{thread_id}-root@example.com>",
        latest_message_id=f"<{thread_id}-latest@example.com>",
        subject_norm=f"subject-{thread_id}",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=current_task_id,
        last_task_snapshot_file=f"snapshots/{current_task_id}.json",
        status=status,
        history_files=history_files,
        lifecycle="active",
        workspace_id=workspace_id,
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id=thread_id,
        session_name=f"Session {thread_id}",
        session_norm=f"session-{thread_id}",
        backend_transport=backend_transport,
        created_at="2026-03-18T21:00:00",
        updated_at=updated_at,
    )


def _write_run(
    workspace: WorkspaceManager,
    *,
    thread_id: str,
    task_id: str,
    status: str,
    started_at: str,
    finished_at: str | None,
    summary_text: str = "",
    stderr_text: str = "",
    stream_lines: list[dict[str, object]] | None = None,
) -> None:
    run_dir = workspace.create_run_dir(thread_id, task_id, exist_ok=True)
    workspace.save_run_result(
        thread_id,
        task_id,
        RunResult(
            task_id=task_id,
            thread_id=thread_id,
            backend="codex",
            status=status,
            exit_code=0 if status == "success" else 1,
            started_at=started_at,
            finished_at=finished_at,
            stdout_file=f"runs/{task_id}/stdout.log",
            stderr_file=f"runs/{task_id}/stderr.log",
            summary_file=f"runs/{task_id}/summary.md",
            artifacts_dir=f"runs/{task_id}/artifacts",
            backend_transport="sdk",
        ),
    )
    (run_dir / "summary.md").write_text(summary_text, encoding="utf-8")
    (run_dir / "stderr.log").write_text(stderr_text, encoding="utf-8")
    (run_dir / "stdout.log").write_text("", encoding="utf-8")
    if stream_lines is not None:
        rendered = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in stream_lines)
        (run_dir / "stream.events.jsonl").write_text(rendered, encoding="utf-8")


def test_runtime_health_report_flags_transport_recovery(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    HostStateStore(runtime_dir).write(
        status=HOST_STATUS_RUNNING,
        pid=os.getpid(),
        started_at="2026-03-18T21:00:00",
        config_path=str(config_path),
        runtime_dir=str(runtime_dir),
    )
    (runtime_dir / "loop.stderr.log").write_text(
        "2026-03-18 21:33:45,185 INFO mail_runner.app: Polling cycle complete. fetched=1 processed=1 skipped=0 failed=0 busy=False\n",
        encoding="utf-8",
    )
    save_thread_state(
        _thread_state(
            status="done",
            current_task_id="task_success",
            history_files=["runs/task_failed/result.json", "runs/task_success/result.json"],
            updated_at="2026-03-18T21:33:47",
        ),
        task_root,
    )
    workspace = WorkspaceManager(task_root)
    _write_run(
        workspace,
        thread_id="thread_001",
        task_id="task_failed",
        status="failed",
        started_at="2026-03-18T21:20:23",
        finished_at="2026-03-18T21:22:04",
        summary_text="stream disconnected before completion",
        stderr_text=(
            "failed to connect to websocket: tls handshake eof\n"
            "error sending request for url (https://chatgpt.com/backend-api/codex/responses)\n"
        ),
    )
    _write_run(
        workspace,
        thread_id="thread_001",
        task_id="task_success",
        status="success",
        started_at="2026-03-18T21:28:55",
        finished_at="2026-03-18T21:33:45",
        summary_text="Recovered successfully.",
        stream_lines=[
            {
                "ts": "2026-03-18T13:33:45.487Z",
                "seq": 1,
                "thread_id": "thread_001",
                "task_id": "task_success",
                "backend": "codex",
                "backend_transport": "sdk",
                "kind": "turn.completed",
            }
        ],
    )

    report = build_runtime_health_report(runtime_dir=str(runtime_dir), thread_ids=["thread_001"])

    assert report.threads[0].assessment == "transport_recovered"
    assert "websocket_tls_handshake_eof" in report.threads[0].recovery_issue_kinds
    assert "responses_send_error" in report.threads[0].recovery_issue_kinds
    assert "assessment=transport_recovered" in render_runtime_health_report(report)


def test_runtime_health_report_flags_transport_failure(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    HostStateStore(runtime_dir).write(
        status=HOST_STATUS_RUNNING,
        pid=os.getpid(),
        started_at="2026-03-18T21:00:00",
        config_path=str(config_path),
        runtime_dir=str(runtime_dir),
    )
    save_thread_state(
        _thread_state(
            status="failed",
            current_task_id="task_failed",
            history_files=["runs/task_failed/result.json"],
            updated_at="2026-03-18T21:22:27",
        ),
        task_root,
    )
    workspace = WorkspaceManager(task_root)
    _write_run(
        workspace,
        thread_id="thread_001",
        task_id="task_failed",
        status="failed",
        started_at="2026-03-18T21:20:23",
        finished_at="2026-03-18T21:22:04",
        stderr_text=(
            "failed to connect to websocket: tls handshake eof\n"
            "Falling back from WebSockets to HTTPS transport. stream disconnected before completion: tls handshake eof\n"
            "error sending request for url (https://chatgpt.com/backend-api/codex/responses)\n"
        ),
        stream_lines=[
            {
                "ts": "2026-03-18T13:22:04.380Z",
                "seq": 14,
                "thread_id": "thread_001",
                "task_id": "task_failed",
                "backend": "codex",
                "backend_transport": "sdk",
                "kind": "turn.failed",
                "text": "stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)",
            }
        ],
    )

    report = build_runtime_health_report(runtime_dir=str(runtime_dir), thread_ids=["thread_001"])

    assert report.threads[0].assessment == "transport_failure"
    assert "websocket_tls_handshake_eof" in report.threads[0].issue_kinds
    assert "responses_send_error" in report.threads[0].issue_kinds


def test_runtime_health_report_marks_running_thread_as_progressing(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = _write_runtime_config(runtime_dir)
    task_root = runtime_dir / "tasks"
    HostStateStore(runtime_dir).write(
        status=HOST_STATUS_RUNNING,
        pid=os.getpid(),
        started_at="2026-03-18T21:00:00",
        config_path=str(config_path),
        runtime_dir=str(runtime_dir),
    )
    save_thread_state(
        _thread_state(
            status="running",
            current_task_id="task_live",
            history_files=[],
            updated_at="2099-03-18T21:19:10",
        ),
        task_root,
    )
    workspace = WorkspaceManager(task_root)
    run_dir = workspace.create_run_dir("thread_001", "task_live", exist_ok=True)
    (run_dir / "stream.events.jsonl").write_text(
        json.dumps(
                {
                    "ts": "2099-03-18T13:24:16.030Z",
                    "seq": 47,
                    "thread_id": "thread_001",
                    "task_id": "task_live",
                "backend": "codex",
                "backend_transport": "sdk",
                "kind": "tool.completed",
                "text": "pytest finished",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_runtime_health_report(runtime_dir=str(runtime_dir), thread_ids=["thread_001"])

    assert report.threads[0].assessment == "healthy_progressing"
    assert report.threads[0].latest_stream_kind == "tool.completed"
