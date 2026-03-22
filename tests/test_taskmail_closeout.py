from __future__ import annotations

import json

from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.taskmail_closeout import build_taskmail_daily_closeout_bundle, write_taskmail_daily_closeout_bundle
from mail_runner.thread_store import create_thread, save_raw_mail
from mail_runner.workspace import WorkspaceManager
from scripts.build_taskmail_closeout_bundle import main


def _create_thread_with_canonical_run(
    tmp_path,
    *,
    terminal_mail_message_id: str | None = "<done@example.com>",
    terminal_mail_subject: str | None = "[DONE][S:thread_001] Demo task",
) -> tuple:
    task_root = tmp_path / "tasks"
    state = create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo task",
        backend="codex",
        repo_path="E:\\projects\\android_task_manager",
        workdir="feature/taskmail/internal",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        task_root=task_root,
        status="done",
        history_files=["runs/task_001/result.json"],
        last_summary="PHASE5_BIND_001",
        created_at="2026-03-22T12:00:00",
        updated_at="2026-03-22T12:05:00",
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<ingress@example.com>",
            "subject": "[CX] Demo task",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2026-03-22T12:00:01",
            "raw_headers": {
                "Subject": "[CX] Demo task",
                "X-TaskMail-Direct": "1",
                "X-TaskMail-Relay-Request-Id": "req_001",
            },
        },
        task_root,
    )
    workspace = WorkspaceManager(task_root)
    workspace.save_json(
        workspace.run_file_path("thread_001", "task_001", "canonical_summary.json"),
        {
            "version": 1,
            "thread_id": "thread_001",
            "task_id": "task_001",
            "run_status": "success",
            "ingress_type": "direct_bridge",
            "ingress_message_id": "<ingress@example.com>",
            "request_id": "req_001",
            "packet_id": "android-taskmail:new-task:req_001",
            "last_summary": "PHASE5_BIND_001",
            "terminal_mail_message_id": terminal_mail_message_id,
            "terminal_mail_subject": terminal_mail_subject,
            "generated_at": "2026-03-22T12:05:00",
        },
    )
    return task_root, state


def _save_system_mail(task_root, *, message_id: str, subject: str) -> None:
    save_raw_mail(
        "thread_001",
        {
            "message_id": message_id,
            "subject": subject,
            "from_addr": "runner@example.com",
            "to_addr": "user@example.com",
            "date": "2026-03-22T12:00:02",
            "raw_headers": {
                "Subject": subject,
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
            },
        },
        task_root,
    )


def _write_android_send_records(tmp_path, records: list[dict]) -> str:
    path = tmp_path / "taskmail_new_task_send_records.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [json.dumps(record) for record in records],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return str(path)


def _write_outbound_attempts(task_root, attempts: list[dict]) -> None:
    path = task_root / "thread_001" / "outbound" / "delivery_attempts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in attempts:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def test_build_taskmail_daily_closeout_bundle_prefers_request_id_bind(tmp_path) -> None:
    task_root, _ = _create_thread_with_canonical_run(tmp_path)
    relay_state_dir = tmp_path / "relay_state"
    _save_system_mail(task_root, message_id="<accepted@example.com>", subject="[ACCEPTED][S:thread_001] Demo task")
    _save_system_mail(task_root, message_id="<running@example.com>", subject="[RUNNING][S:thread_001] Demo task")
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")
    _write_outbound_attempts(
        task_root,
        [
            {
                "packet_id": "packet:task_001:accepted",
                "thread_id": "thread_001",
                "task_id": "task_001",
                "transport_name": "email",
                "sent_at": "2026-03-22T12:00:02",
                "success": True,
                "to_addr": "user@example.com",
                "subject": "[DONE][S:thread_001] Demo task",
                "transport_message_id": "<done@example.com>",
                "error_message": None,
                "client_trace_id": "task_001",
            }
        ],
    )
    packet_store = PersistentAcceptedPacketStore(relay_state_dir)
    packet_store.accept_packet(
        packet_id="android-taskmail:new-task:req_001",
        receipt_id="relay-receipt:req_001",
        connection_id="conn_001",
        client_id="android",
        client_trace_id="trace_001",
        received_at="2026-03-22T12:00:01",
        task_run_packet={"action": "new_task"},
        dispatch_metadata={"action": "new_task"},
    )
    packet_store.mark_delivery_result(
        "android-taskmail:new-task:req_001",
        attempted_at="2026-03-22T12:00:01",
        transport_name="mail_bridge",
        success=True,
        transport_message_id="<ingress@example.com>",
    )
    android_records_path = _write_android_send_records(
        tmp_path,
        [
            {
                "recordedAt": 1711111111000,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "workdir": "feature/taskmail/internal",
                "evidence": {
                    "bootstrapStatus": "hello_ack",
                    "outcome": "DirectAccepted",
                    "switchGate": "KeepDirectDefault",
                    "requestId": "req_001",
                    "receiptId": "relay-receipt:req_001",
                    "transportMessageId": "<ingress@example.com>",
                },
            }
        ],
    )

    bundle = build_taskmail_daily_closeout_bundle(
        "thread_001",
        task_root,
        android_send_records_path=android_records_path,
        sender_account_id="acc-001",
        android_last_summary="PHASE5_BIND_001",
        relay_state_dir=relay_state_dir,
    )

    assert bundle["bundle_presence"]["pc_terminal_mail"] is True
    assert bundle["bundle_presence"]["android_latest_send_evidence"] is True
    assert bundle["bundle_presence"]["pc_outbound_delivery_attempts"] is True
    assert bundle["bundle_presence"]["pc_relay_packet_store"] is True
    assert bundle["pc_terminal_mail"]["resolution"] == "terminal_mail_message_id"
    assert bundle["pc_terminal_mail"]["thread_relative_path"] == "mail/raw_004.json"
    assert bundle["pc_supporting_evidence"]["pc_ingress_mail"]["thread_relative_path"] == "mail/raw_001.json"
    assert bundle["pc_supporting_evidence"]["pc_ingress_mail"]["request_id"] == "req_001"
    assert bundle["pc_supporting_evidence"]["outbound_delivery_attempts"]["matched_attempt_count"] == 1
    assert bundle["pc_supporting_evidence"]["relay_packet_store"]["packet"]["receipt_id"] == "relay-receipt:req_001"
    assert bundle["same_run_bind"]["effective_bind_level"] == "request_id"
    assert bundle["same_run_bind"]["matched_fields"] == [
        "request_id",
        "transport_message_id",
        "last_summary",
    ]
    assert bundle["same_run_bind"]["strong_bind"] is True
    assert bundle["same_run_bind"]["can_promote_to_mismatch_candidate"] is True


def test_build_taskmail_daily_closeout_bundle_falls_back_to_terminal_subject_without_fixed_raw_number(tmp_path) -> None:
    task_root, _ = _create_thread_with_canonical_run(
        tmp_path,
        terminal_mail_message_id=None,
        terminal_mail_subject="[DONE][S:thread_001] Demo task",
    )
    _save_system_mail(task_root, message_id="<accepted@example.com>", subject="[ACCEPTED][S:thread_001] Demo task")
    _save_system_mail(task_root, message_id="<status@example.com>", subject="[STATUS][S:thread_001] Demo task")
    _save_system_mail(task_root, message_id="<question@example.com>", subject="[QUESTION][S:thread_001] Demo task")
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")

    bundle = build_taskmail_daily_closeout_bundle("thread_001", task_root)

    assert bundle["pc_terminal_mail"]["resolution"] == "terminal_mail_subject"
    assert bundle["pc_terminal_mail"]["message_id"] == "<done@example.com>"
    assert bundle["pc_terminal_mail"]["thread_relative_path"] == "mail/raw_005.json"


def test_build_taskmail_daily_closeout_bundle_falls_back_when_canonical_summary_is_missing(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend="codex",
        repo_path="E:\\projects\\android_task_manager",
        workdir="feature/taskmail/internal",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        task_root=task_root,
        status="done",
        history_files=["runs/task_001/result.json"],
        last_summary="PHASE5_BIND_001",
        created_at="2026-03-22T12:00:00",
        updated_at="2026-03-22T12:05:00",
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<root@example.com>",
            "subject": "[CX] Demo task",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2026-03-22T12:00:01",
            "raw_headers": {
                "Subject": "[CX] Demo task",
                "X-TaskMail-Direct": "1",
                "X-TaskMail-Relay-Request-Id": "req_001",
                "X-TaskMail-Relay-Packet-Id": "android-taskmail:new-task:req_001",
            },
        },
        task_root,
    )
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")

    bundle = build_taskmail_daily_closeout_bundle("thread_001", task_root)

    assert bundle["bundle_presence"]["pc_canonical_outcome"] is False
    assert bundle["pc_canonical_outcome"]["source"] == "thread_state_fallback"
    assert bundle["pc_canonical_outcome"]["request_id"] == "req_001"
    assert bundle["pc_canonical_outcome"]["packet_id"] == "android-taskmail:new-task:req_001"
    assert bundle["pc_canonical_outcome"]["ingress_type"] == "direct_bridge"
    assert bundle["pc_canonical_outcome"]["last_summary"] == "PHASE5_BIND_001"
    assert bundle["pc_terminal_mail"]["resolution"] == "thread_state.latest_message_id"
    assert bundle["pc_supporting_evidence"]["pc_ingress_mail"]["resolution"] == "ingress_message_id"
    assert bundle["pc_supporting_evidence"]["pc_ingress_mail"]["message_id"] == "<root@example.com>"


def test_build_taskmail_daily_closeout_bundle_keeps_weak_summary_bind_separate_from_strong_bind(tmp_path) -> None:
    task_root, _ = _create_thread_with_canonical_run(tmp_path)
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")
    android_records_path = _write_android_send_records(
        tmp_path,
        [
            {
                "recordedAt": 1711111111001,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "workdir": "feature/taskmail/internal",
                "evidence": {
                    "bootstrapStatus": "hello_ack",
                    "outcome": "DirectAccepted",
                    "switchGate": "KeepDirectDefault",
                    "requestId": "req_999",
                    "receiptId": "relay-receipt:req_999",
                    "transportMessageId": "<other@example.com>",
                },
            }
        ],
    )

    bundle = build_taskmail_daily_closeout_bundle(
        "thread_001",
        task_root,
        android_send_records_path=android_records_path,
        sender_account_id="acc-001",
        android_last_summary="PHASE5_BIND_001",
    )

    assert bundle["android_latest_send_evidence"]["selection"] == "workspace_outcome_time"
    assert bundle["same_run_bind"]["effective_bind_level"] == "last_summary"
    assert bundle["same_run_bind"]["matched_fields"] == ["last_summary"]
    assert bundle["same_run_bind"]["mismatched_fields"] == ["request_id", "transport_message_id"]
    assert bundle["same_run_bind"]["strong_bind"] is False
    assert bundle["same_run_bind"]["weak_bind_only"] is True
    assert bundle["same_run_bind"]["can_promote_to_mismatch_candidate"] is False


def test_build_taskmail_daily_closeout_bundle_matches_direct_record_by_transport_message_id(tmp_path) -> None:
    task_root, _ = _create_thread_with_canonical_run(tmp_path)
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")
    android_records_path = _write_android_send_records(
        tmp_path,
        [
            {
                "recordedAt": 1774176099208,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "evidence": {
                    "bootstrapStatus": "hello_ack",
                    "outcome": "DirectAccepted",
                    "switchGate": "KeepDirectDefault",
                    "receiptId": "relay-receipt:req_999",
                    "transportMessageId": "<other@example.com>",
                },
            },
            {
                "recordedAt": 1774168646837,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "workdir": "feature/taskmail/internal",
                "evidence": {
                    "bootstrapStatus": "hello_ack",
                    "outcome": "DirectAccepted",
                    "switchGate": "KeepDirectDefault",
                    "receiptId": "relay-receipt:req_001",
                    "transportMessageId": "<ingress@example.com>",
                },
            },
        ],
    )

    bundle = build_taskmail_daily_closeout_bundle(
        "thread_001",
        task_root,
        android_send_records_path=android_records_path,
        sender_account_id="acc-001",
        android_last_summary="PHASE5_BIND_001",
    )

    assert bundle["android_latest_send_evidence"]["selection"] == "transport_message_id"
    assert bundle["android_latest_send_evidence"]["transport_message_id"] == "<ingress@example.com>"
    assert bundle["same_run_bind"]["effective_bind_level"] == "transport_message_id"
    assert bundle["same_run_bind"]["matched_fields"] == ["transport_message_id", "last_summary"]
    assert bundle["same_run_bind"]["mismatched_fields"] == []
    assert bundle["same_run_bind"]["notes"] == ["android_request_id_missing"]
    assert bundle["same_run_bind"]["strong_bind"] is True


def test_build_taskmail_daily_closeout_bundle_prefers_nearest_fallback_record_over_workspace_latest(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo fallback task",
        backend="codex",
        repo_path="E:\\projects\\android_task_manager",
        workdir=None,
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        task_root=task_root,
        status="done",
        history_files=["runs/task_001/result.json"],
        last_summary="PHASE4_FALLBACK_PARITY_20260322_B",
        created_at="2026-03-22T15:26:47",
        updated_at="2026-03-22T15:27:02",
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<root@example.com>",
            "subject": "[CX] Demo fallback task",
            "from_addr": "user@example.com",
            "to_addr": "runner@example.com",
            "date": "2026-03-22T15:26:40",
            "raw_headers": {
                "Subject": "[CX] Demo fallback task",
            },
        },
        task_root,
    )
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo fallback task")
    android_records_path = _write_android_send_records(
        tmp_path,
        [
            {
                "recordedAt": 1774176099208,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "evidence": {
                    "bootstrapStatus": "hello_ack",
                    "outcome": "DirectAccepted",
                    "switchGate": "KeepDirectDefault",
                    "receiptId": "relay-receipt:req_999",
                    "transportMessageId": "<other@example.com>",
                },
            },
            {
                "recordedAt": 1774162523754,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "evidence": {
                    "bootstrapStatus": "not_configured",
                    "outcome": "MailFallbackSucceeded",
                    "switchGate": "FallbackRequired",
                    "fallbackReason": "older fallback",
                },
            },
            {
                "recordedAt": 1774164384644,
                "senderAccountId": "acc-001",
                "backend": "codex",
                "repoPath": "E:\\projects\\android_task_manager",
                "evidence": {
                    "bootstrapStatus": "not_configured",
                    "outcome": "MailFallbackSucceeded",
                    "switchGate": "FallbackRequired",
                    "fallbackReason": "closest fallback",
                },
            },
        ],
    )

    bundle = build_taskmail_daily_closeout_bundle(
        "thread_001",
        task_root,
        android_send_records_path=android_records_path,
        sender_account_id="acc-001",
        android_last_summary="PHASE4_FALLBACK_PARITY_20260322_B",
    )

    assert bundle["bundle_presence"]["pc_canonical_outcome"] is False
    assert bundle["android_latest_send_evidence"]["selection"] == "workspace_outcome_time"
    assert bundle["android_latest_send_evidence"]["outcome"] == "MailFallbackSucceeded"
    assert bundle["android_latest_send_evidence"]["recorded_at"] == 1774164384644
    assert bundle["android_latest_send_evidence"]["fallback_reason"] == "closest fallback"
    assert bundle["same_run_bind"]["effective_bind_level"] == "last_summary"
    assert bundle["same_run_bind"]["matched_fields"] == ["last_summary"]
    assert bundle["same_run_bind"]["mismatched_fields"] == []
    assert bundle["same_run_bind"]["notes"] == ["android_transport_message_id_missing"]
    assert bundle["same_run_bind"]["strong_bind"] is False


def test_write_taskmail_daily_closeout_bundle_uses_run_artifact_path_by_default(tmp_path) -> None:
    task_root, _ = _create_thread_with_canonical_run(tmp_path)
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")

    output_path = write_taskmail_daily_closeout_bundle("thread_001", task_root)

    assert output_path.name == "taskmail_daily_closeout_bundle.json"
    assert output_path.parent.name == "task_001"
    assert json.loads(output_path.read_text(encoding="utf-8"))["task_id"] == "task_001"


def test_build_taskmail_closeout_bundle_script_writes_run_artifact(tmp_path, capsys) -> None:
    task_root, _ = _create_thread_with_canonical_run(tmp_path)
    _save_system_mail(task_root, message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo task")

    exit_code = main(["thread_001", "--task-root", str(task_root), "--write-run-artifact"])
    output_path = task_root / "thread_001" / "runs" / "task_001" / "taskmail_daily_closeout_bundle.json"

    assert exit_code == 0
    assert capsys.readouterr().out.strip().endswith("taskmail_daily_closeout_bundle.json")
    assert output_path.exists()
