from __future__ import annotations

import json

from mail_runner.models import ThreadState
from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket, TransportReceipt
from mail_runner.outbound.journal import OutboundJournal, delivery_attempts_path
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
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        last_summary="Mock run completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )


def test_outbound_journal_appends_delivery_attempt_jsonl(tmp_path) -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:10:00",
        message_kind="status_update",
        content_format="text/plain+text/html",
        html="<html><body>Done.</body></html>",
        text_fallback="Status: DONE\n",
        state_patch={"thread_id": "thread_001"},
        client_trace_id="task_001",
    )
    request = OutboundDispatchRequest(
        packet=packet,
        to_addr="user@example.com",
        subject="[DONE][S:thread_001] Demo task",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        headers={"X-Mail-Runner": "1"},
    )
    receipt = TransportReceipt(
        success=True,
        transport_name="email",
        sent_at="2026-03-20T11:10:05",
        transport_message_id="<sent@example.com>",
    )

    attempt = OutboundJournal(tmp_path / "tasks").record_attempt(
        state=_state(),
        request=request,
        receipt=receipt,
    )

    assert attempt.packet_id == "packet:task_001:test"
    journal_path = delivery_attempts_path(tmp_path / "tasks", "thread_001")
    payloads = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert payloads == [
        {
            "packet_id": "packet:task_001:test",
            "thread_id": "thread_001",
            "task_id": "task_001",
            "transport_name": "email",
            "sent_at": "2026-03-20T11:10:05",
            "success": True,
            "to_addr": "user@example.com",
            "subject": "[DONE][S:thread_001] Demo task",
            "transport_message_id": "<sent@example.com>",
            "error_message": None,
            "client_trace_id": "task_001",
        }
    ]
