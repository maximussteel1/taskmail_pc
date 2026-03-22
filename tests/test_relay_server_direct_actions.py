from __future__ import annotations

import json

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.outbound.relay_bootstrap import build_hello_payload
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.direct_actions import (
    DIRECT_NEW_TASK_OUTCOME_ACCEPTED,
    DIRECT_NEW_TASK_OUTCOME_FALLBACK_CLASSIFIED_REJECTION,
    DIRECT_NEW_TASK_OUTCOME_HARD_REJECTION,
    RelayTaskMailDirectNewTaskHandler,
    RelayTaskMailDirectNewTaskMailBridge,
    classify_direct_new_task_server_outcome,
)
from mail_runner.relay_server.loopback import LoopbackRelayServer
from mail_runner.relay_server.protocol import RelayErrorMessage, RelayHelloAckMessage, RelayPacketAckMessage, parse_server_message
from mail_runner.runner import SerialTaskRunner
from mail_runner.thread_store import load_thread_state


class FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return f"<sent-{len(self.sent_messages)}@example.com>"


def test_direct_new_task_packet_is_accepted_and_reuses_mail_task_start_path(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    relay_state = tmp_path / "relay_state"
    mail_client = FakeMailClient()
    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(0), MockAdapter(0)))
    handler = RelayTaskMailDirectNewTaskHandler(
        config=AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root=str(task_root)),
        task_root=task_root,
        mail_client=mail_client,
        runner=runner,
        recipient_addr="user@example.com",
        background=True,
    )
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(relay_state),
        ),
        direct_packet_handler=handler,
        clock=lambda: "2026-03-21T12:30:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_packet(),
        connection_id=connection_id,
    )
    runner.wait_until_idle()

    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayPacketAckMessage)
    assert parsed.accepted is True
    assert parsed.packet_id == "android-taskmail:new-task:req_001"
    assert classify_direct_new_task_server_outcome(parsed) == DIRECT_NEW_TASK_OUTCOME_ACCEPTED

    packet = server.packet_store.get_packet("android-taskmail:new-task:req_001")
    state = load_thread_state("thread_001", task_root)
    raw_mail = json.loads((task_root / "thread_001" / "mail" / "raw_001.json").read_text(encoding="utf-8"))
    canonical_summary = json.loads(
        (task_root / "thread_001" / "runs" / state.current_task_id / "canonical_summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert state.status == "done"
    assert state.session_name == "Audit the direct-send handoff path"
    assert raw_mail["from_addr"] == "user@example.com"
    assert raw_mail["raw_headers"]["X-TaskMail-Direct"] == "1"
    assert [item["subject"] for item in mail_client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Audit the direct-send handoff path",
        "[RUNNING][S:thread_001] Audit the direct-send handoff path",
        "[DONE][S:thread_001] Audit the direct-send handoff path",
    ]
    assert all(item["to_addr"] == "user@example.com" for item in mail_client.sent_messages)
    assert canonical_summary["ingress_type"] == "direct_bridge"
    assert canonical_summary["ingress_message_id"] == raw_mail["message_id"]
    assert canonical_summary["request_id"] == "req_001"
    assert canonical_summary["packet_id"] == "android-taskmail:new-task:req_001"
    assert canonical_summary["last_summary"] == state.last_summary
    assert canonical_summary["terminal_mail_message_id"] == "<sent-3@example.com>"
    assert canonical_summary["terminal_mail_subject"] == "[DONE][S:thread_001] Audit the direct-send handoff path"


def test_direct_packet_returns_unsupported_action_for_non_new_task_phase2_payload(tmp_path) -> None:
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
        ),
        direct_packet_handler=_build_direct_handler(tmp_path),
        clock=lambda: "2026-03-21T12:30:00",
    )
    connection_id = _connect(server)
    packet = _canonical_direct_packet()
    packet["task_run_packet"]["action"] = "pause"
    packet["dispatch_metadata"]["action"] = "pause"

    response = server.handle_client_message(packet, connection_id=connection_id)

    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "unsupported_action"
    assert classify_direct_new_task_server_outcome(parsed) == DIRECT_NEW_TASK_OUTCOME_FALLBACK_CLASSIFIED_REJECTION
    assert server.packet_store.get_packet("android-taskmail:new-task:req_001") is None


def test_direct_packet_returns_invalid_payload_for_missing_task_text(tmp_path) -> None:
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
        ),
        direct_packet_handler=_build_direct_handler(tmp_path),
        clock=lambda: "2026-03-21T12:30:00",
    )
    connection_id = _connect(server)
    packet = _canonical_direct_packet()
    packet["task_run_packet"]["new_task"]["task_text"] = ""

    response = server.handle_client_message(packet, connection_id=connection_id)

    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "invalid_payload"
    assert classify_direct_new_task_server_outcome(parsed) == DIRECT_NEW_TASK_OUTCOME_HARD_REJECTION
    assert server.packet_store.get_packet("android-taskmail:new-task:req_001") is None


def test_direct_packet_records_post_accept_fallback_classified_failure_on_packet_store(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path / "relay_state"),
        ),
        direct_packet_handler=RelayTaskMailDirectNewTaskHandler(
            config=AppConfig(from_name="Mail Runner", task_root=str(task_root)),
            task_root=task_root,
            mail_client=FakeMailClient(),
            runner=SerialTaskRunner(task_root, Dispatcher(MockAdapter(0), MockAdapter(0))),
            recipient_addr="user@example.com",
            background=True,
        ),
        clock=lambda: "2026-03-21T12:30:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_packet(),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:new-task:req_001")

    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "direct_temporarily_unavailable"
    assert classify_direct_new_task_server_outcome(parsed) == DIRECT_NEW_TASK_OUTCOME_FALLBACK_CLASSIFIED_REJECTION
    assert packet is not None
    assert packet.delivery_status == "failed"
    assert packet.attempt_count == 1
    assert packet.last_error_code == "direct_temporarily_unavailable"
    assert packet.last_error_message == "bot mailbox address is not configured for direct TaskMail acceptance"


def test_direct_new_task_bridge_sends_canonical_first_mail_without_system_header(tmp_path) -> None:
    mail_client = FakeMailClient()
    config = RelayServerConfig(
        host="127.0.0.1",
        port=8787,
        transport_token="relay-secret",
        state_dir=str(tmp_path / "relay_state"),
        smtp_host="smtp.example.com",
        smtp_user="relay@example.com",
        smtp_password="secret",
        from_addr="relay@example.com",
        taskmail_bot_mailbox_addr="bot@example.com",
        taskmail_direct_from_addr="taskmail-user@example.com",
    )
    server = LoopbackRelayServer(
        config,
        direct_packet_handler=RelayTaskMailDirectNewTaskMailBridge(
            config,
            mail_client=mail_client,
        ),
        clock=lambda: "2026-03-21T12:30:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_packet(),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:new-task:req_001")

    assert isinstance(parsed, RelayPacketAckMessage)
    assert parsed.accepted is True
    assert classify_direct_new_task_server_outcome(parsed) == DIRECT_NEW_TASK_OUTCOME_ACCEPTED
    assert parsed.transport_message_id == "<sent-1@example.com>"
    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert packet.attempt_count == 1
    assert mail_client.sent_messages[0]["to_addr"] == "bot@example.com"
    assert mail_client.sent_messages[0]["subject"] == "[CX] Audit the direct-send handoff path"
    assert "Repo: E:\\projects\\android_task_manager" in mail_client.sent_messages[0]["body"]
    assert "Task:\nAudit the direct-send handoff path.\n" in mail_client.sent_messages[0]["body"]
    assert "X-Mail-Runner" not in mail_client.sent_messages[0]["headers"]
    assert mail_client.sent_messages[0]["headers"]["X-TaskMail-Direct"] == "1"


def _build_direct_handler(tmp_path) -> RelayTaskMailDirectNewTaskHandler:
    task_root = tmp_path / "tasks"
    runner = SerialTaskRunner(task_root, Dispatcher(MockAdapter(0), MockAdapter(0)))
    return RelayTaskMailDirectNewTaskHandler(
        config=AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root=str(task_root)),
        task_root=task_root,
        mail_client=FakeMailClient(),
        runner=runner,
        recipient_addr="user@example.com",
        background=True,
    )


def _connect(server: LoopbackRelayServer) -> str:
    response = server.handle_client_message(
        build_hello_payload(
            client_id="android-taskmail",
            client_version="0.1.0",
            transport_token="relay-secret",
        ),
        provided_token="relay-secret",
    )
    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayHelloAckMessage)
    return parsed.connection_id


def _canonical_direct_packet() -> dict[str, object]:
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:new-task:req_001",
        "client_trace_id": "req_001",
        "task_run_packet": {
            "schema_version": "phase2-direct-outbound-contract-v1",
            "action": "new_task",
            "request_id": "req_001",
            "origin": {
                "client": "android_taskmail",
                "sender_account_uuid": "acc-001",
            },
            "new_task": {
                "backend": "codex",
                "repo_path": "E:\\projects\\android_task_manager",
                "workdir": "feature/taskmail/internal",
                "task_text": "Audit the direct-send handoff path.",
                "subject_title": "Audit the direct-send handoff path",
                "timeout_minutes": 120,
                "mode": "analysis_only",
                "profile": "android",
                "permission": "highest",
                "acceptance": [
                    "List any contract mismatches.",
                    "Do not change user-facing reply semantics.",
                ],
            },
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "phase2-direct-outbound-contract-v1",
            "action": "new_task",
            "fallback_policy": "mail",
        },
        "sent_at": "2026-03-21T12:30:00",
    }
