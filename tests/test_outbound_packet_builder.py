from __future__ import annotations

from mail_runner.models import OutgoingAttachment, RunResult, TaskSnapshot, ThreadState
from mail_runner.outbound.packet_builder import build_outbound_dispatch_request, build_task_run_packet
from mail_runner.outbound.renderer import RenderedStatusMail
from mail_runner.reporter import MAIL_STATUS_DONE
from mail_runner.status import THREAD_STATUS_DONE


def _state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend="opencode",
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        session_id="session_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        last_summary="Mock run completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )


def _snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="opencode",
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect the module.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )


def _result() -> RunResult:
    return RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend="opencode",
        status="success",
        exit_code=0,
        started_at="2026-03-12T12:01:00",
        finished_at="2026-03-12T12:02:00",
        stdout_file="runs/task_001/stdout.txt",
        stderr_file="runs/task_001/stderr.txt",
        tests_passed=True,
    )


def test_build_task_run_packet_and_dispatch_request() -> None:
    rendered_mail = RenderedStatusMail(
        subject="[DONE][S:session_001] Demo task",
        plain_body="Status: DONE\nSummary: Completed.\n",
        html_body="<html><body>Completed.</body></html>",
    )
    attachments = [
        OutgoingAttachment(
            path="D:\\repo\\runs\\task_001\\report.txt",
            name="report.txt",
            content_type="text/plain",
        )
    ]

    packet = build_task_run_packet(
        rendered_mail=rendered_mail,
        state=_state(),
        task_snapshot=_snapshot(),
        status_label=MAIL_STATUS_DONE,
        attachments=attachments,
        result=_result(),
        created_at="2026-03-12T12:02:30",
        packet_id_factory=lambda task_id: f"packet:{task_id}:fixed",
    )

    assert packet.packet_id == "packet:task_001:fixed"
    assert packet.task_id == "task_001"
    assert packet.created_at == "2026-03-12T12:02:30"
    assert packet.message_kind == "status_update"
    assert packet.content_format == "text/plain+text/html"
    assert packet.text_fallback == rendered_mail.plain_body
    assert packet.html == rendered_mail.html_body
    assert packet.attachments == attachments
    assert packet.state_patch == {
        "thread_id": "thread_001",
        "session_id": "session_001",
        "thread_status": THREAD_STATUS_DONE,
        "status_label": MAIL_STATUS_DONE,
        "backend": "opencode",
        "run_status": "success",
    }
    assert packet.client_trace_id == "task_001"

    dispatch_request = build_outbound_dispatch_request(
        packet=packet,
        to_addr="user@example.com",
        subject=rendered_mail.subject,
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<root@example.com>", "<done@example.com>"],
        headers={"X-Task-Mail": "yes"},
    )

    assert dispatch_request.packet is packet
    assert dispatch_request.to_addr == "user@example.com"
    assert dispatch_request.subject == rendered_mail.subject
    assert dispatch_request.in_reply_to == "<done@example.com>"
    assert dispatch_request.references == ["<root@example.com>", "<done@example.com>"]
    assert dispatch_request.headers == {"X-Task-Mail": "yes"}
