from __future__ import annotations

import json

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import TaskSnapshot
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
from mail_runner.relay_server.post_creation_actions import (
    DIRECT_POST_CREATION_OUTCOME_ACCEPTED,
    DIRECT_POST_CREATION_OUTCOME_FALLBACK_REQUIRED,
    DIRECT_POST_CREATION_OUTCOME_HARD_STOP,
    RelayTaskMailDirectCurrentSessionReplyHandler,
    RelayTaskMailDirectCurrentSessionReplyMailBridge,
    RelayTaskMailDirectCurrentSessionStatusHandler,
    RelayTaskMailDirectCurrentSessionStatusMailBridge,
    classify_direct_post_creation_server_outcome,
)
from mail_runner.relay_server.protocol import RelayErrorMessage, RelayHelloAckMessage, RelayPacketAckMessage, parse_server_message
from mail_runner.runner import SerialTaskRunner
from mail_runner.status import BACKEND_OPENCODE, THREAD_STATUS_AWAITING_USER_INPUT, THREAD_STATUS_PAUSED
from mail_runner.thread_store import load_thread_state, save_thread_state


class FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return f"<sent-{len(self.sent_messages)}@example.com>"


class RecordingMockAdapter(MockAdapter):
    def __init__(self) -> None:
        super().__init__(sleep_seconds=0)
        self.snapshots: list[TaskSnapshot] = []

    def run(self, task: TaskSnapshot, run_dir: str):
        self.snapshots.append(task)
        return super().run(task, run_dir)


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


def test_direct_current_session_status_packet_is_accepted_and_reuses_mail_status_query_path(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    relay_state = tmp_path / "relay_state"
    mail_client = FakeMailClient()
    runner = _setup_existing_thread(task_root)
    state = load_thread_state("thread_001", task_root)
    handler = RelayTaskMailDirectCurrentSessionStatusHandler(
        config=AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root=str(task_root)),
        task_root=task_root,
        mail_client=mail_client,
        runner=runner,
        recipient_addr="user@example.com",
        background=False,
    )
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(relay_state),
        ),
        direct_packet_handler=handler,
        clock=lambda: "2026-03-22T12:30:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_status_packet(
            workspace_id=state.workspace_id,
            session_id=state.session_id or state.thread_id,
            thread_id=state.thread_id,
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:session-action:req_001")
    raw_mail_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((task_root / "thread_001" / "mail").glob("raw_*.json"))
    ]
    direct_ingress = next(item for item in raw_mail_payloads if item["message_id"].startswith("<relay-direct-"))
    closeout_payload = json.loads(
        (
            task_root
            / "thread_001"
            / "session_actions"
            / "req_001"
            / "session_action_closeout.json"
        ).read_text(encoding="utf-8")
    )

    assert isinstance(parsed, RelayPacketAckMessage)
    assert parsed.accepted is True
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_ACCEPTED
    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert [item["subject"] for item in mail_client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "This session is not currently running." in mail_client.sent_messages[0]["body"]
    assert direct_ingress["raw_headers"]["X-TaskMail-Direct"] == "1"
    assert direct_ingress["raw_headers"]["X-TaskMail-Relay-Request-Id"] == "req_001"
    assert direct_ingress["raw_headers"]["X-TaskMail-Action-Type"] == "status"
    assert direct_ingress["raw_headers"]["X-TaskMail-Target-Workspace-Id"] == state.workspace_id
    assert direct_ingress["raw_headers"]["X-TaskMail-Target-Session-Id"] == state.session_id
    assert direct_ingress["raw_headers"]["X-TaskMail-Target-Thread-Id"] == state.thread_id
    assert closeout_payload["action_type"] == "status"
    assert closeout_payload["request_id"] == "req_001"
    assert closeout_payload["terminal_mail_subject"] == "[STATUS][S:thread_001] demo task"
    assert closeout_payload["target_session_identity"] == {
        "workspace_id": state.workspace_id,
        "session_id": state.session_id,
        "thread_id": state.thread_id,
    }


def test_direct_post_creation_status_bridge_sends_status_query_mail_with_capsule(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectCurrentSessionStatusMailBridge(
            config,
            mail_client=mail_client,
        ),
        clock=lambda: "2026-03-22T12:30:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_status_packet(
            workspace_id="workspace_demo",
            session_id="thread_001",
            thread_id="thread_001",
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:session-action:req_001")

    assert isinstance(parsed, RelayPacketAckMessage)
    assert parsed.accepted is True
    assert parsed.transport_message_id == "<sent-1@example.com>"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_ACCEPTED
    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert mail_client.sent_messages[0]["to_addr"] == "bot@example.com"
    assert mail_client.sent_messages[0]["subject"] == "Re: [S:thread_001] thread_001"
    assert "/status" in mail_client.sent_messages[0]["body"]
    assert "---TASK-STATE-BEGIN---" in mail_client.sent_messages[0]["body"]
    assert "workspace_id: workspace_demo" in mail_client.sent_messages[0]["body"]
    assert mail_client.sent_messages[0]["headers"]["X-TaskMail-Direct"] == "1"


def test_direct_current_session_reply_packet_is_accepted_and_reuses_mail_reply_path(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    relay_state = tmp_path / "relay_state"
    mail_client = FakeMailClient()
    adapter = RecordingMockAdapter()
    runner = _setup_existing_thread(task_root, dispatcher=Dispatcher(adapter, adapter))
    state = load_thread_state("thread_001", task_root)
    handler = RelayTaskMailDirectCurrentSessionReplyHandler(
        config=AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root=str(task_root)),
        task_root=task_root,
        mail_client=mail_client,
        runner=runner,
        recipient_addr="user@example.com",
        background=False,
    )
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(relay_state),
        ),
        direct_packet_handler=handler,
        clock=lambda: "2026-03-23T09:30:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_reply_packet(
            workspace_id=state.workspace_id,
            session_id=state.session_id or state.thread_id,
            thread_id=state.thread_id,
            reply_text="Please continue with the cleanup.",
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:session-action:req_001")
    updated_state = load_thread_state("thread_001", task_root)
    raw_mail_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((task_root / "thread_001" / "mail").glob("raw_*.json"))
    ]
    direct_ingress = next(item for item in raw_mail_payloads if item["message_id"].startswith("<relay-direct-"))

    assert isinstance(parsed, RelayPacketAckMessage)
    assert parsed.accepted is True
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_ACCEPTED
    assert packet is not None
    assert packet.delivery_status == "delivered"
    assert updated_state.status == "done"
    assert len(adapter.snapshots) == 2
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].turn_text == "Please continue with the cleanup."
    assert [item["subject"] for item in mail_client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]
    assert "Please continue with the cleanup." in direct_ingress["body_text"]
    assert direct_ingress["raw_headers"]["X-TaskMail-Relay-Request-Id"] == "req_001"
    assert direct_ingress["raw_headers"]["X-TaskMail-Action-Type"] == "reply"
    assert direct_ingress["raw_headers"]["X-TaskMail-Target-Workspace-Id"] == state.workspace_id
    assert direct_ingress["raw_headers"]["X-TaskMail-Target-Session-Id"] == state.session_id
    assert direct_ingress["raw_headers"]["X-TaskMail-Target-Thread-Id"] == state.thread_id
    canonical_summary = json.loads(
        (task_root / "thread_001" / "runs" / updated_state.current_task_id / "canonical_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert canonical_summary["ingress_message_id"] == direct_ingress["message_id"]
    assert canonical_summary["request_id"] == "req_001"
    assert canonical_summary["action_type"] == "reply"
    assert canonical_summary["target_session_identity"] == {
        "workspace_id": state.workspace_id,
        "session_id": state.session_id,
        "thread_id": state.thread_id,
    }


def test_direct_post_creation_reply_packet_returns_unsupported_action_when_reply_handler_is_unavailable(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectCurrentSessionStatusMailBridge(config, mail_client=FakeMailClient()),
        clock=lambda: "2026-03-22T12:30:00",
    )
    connection_id = _connect(server)
    packet = _canonical_direct_reply_packet(
        workspace_id="workspace_demo",
        session_id="thread_001",
        thread_id="thread_001",
    )

    response = server.handle_client_message(packet, connection_id=connection_id)

    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "unsupported_action"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_FALLBACK_REQUIRED
    assert server.packet_store.get_packet("android-taskmail:session-action:req_001") is None


def test_direct_current_session_reply_handler_rejects_paused_session(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    relay_state = tmp_path / "relay_state"
    mail_client = FakeMailClient()
    adapter = RecordingMockAdapter()
    runner = _setup_existing_thread(task_root, dispatcher=Dispatcher(adapter, adapter))
    state = load_thread_state("thread_001", task_root)
    state.status = THREAD_STATUS_PAUSED
    state.paused_from_status = "done"
    state.updated_at = "2026-03-23T09:31:00"
    state.last_progress_at = state.updated_at
    save_thread_state(state, task_root)
    handler = RelayTaskMailDirectCurrentSessionReplyHandler(
        config=AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root=str(task_root)),
        task_root=task_root,
        mail_client=mail_client,
        runner=runner,
        recipient_addr="user@example.com",
        background=False,
    )
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(relay_state),
        ),
        direct_packet_handler=handler,
        clock=lambda: "2026-03-23T09:31:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_reply_packet(
            workspace_id=state.workspace_id,
            session_id=state.session_id or state.thread_id,
            thread_id=state.thread_id,
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:session-action:req_001")

    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "validation_failed"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    assert packet is not None
    assert packet.delivery_status == "failed"
    assert mail_client.sent_messages == []
    assert len(adapter.snapshots) == 1


def test_direct_current_session_reply_handler_rejects_awaiting_question_session(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    relay_state = tmp_path / "relay_state"
    mail_client = FakeMailClient()
    adapter = RecordingMockAdapter()
    runner = _setup_existing_thread(task_root, dispatcher=Dispatcher(adapter, adapter))
    state = load_thread_state("thread_001", task_root)
    state.status = THREAD_STATUS_AWAITING_USER_INPUT
    state.pending_question_id = "question_task_001"
    state.pending_question_text = "Should I update both modules?"
    state.updated_at = "2026-03-23T09:32:00"
    state.last_progress_at = state.updated_at
    save_thread_state(state, task_root)
    handler = RelayTaskMailDirectCurrentSessionReplyHandler(
        config=AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root=str(task_root)),
        task_root=task_root,
        mail_client=mail_client,
        runner=runner,
        recipient_addr="user@example.com",
        background=False,
    )
    server = LoopbackRelayServer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(relay_state),
        ),
        direct_packet_handler=handler,
        clock=lambda: "2026-03-23T09:32:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_reply_packet(
            workspace_id=state.workspace_id,
            session_id=state.session_id or state.thread_id,
            thread_id=state.thread_id,
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)
    packet = server.packet_store.get_packet("android-taskmail:session-action:req_001")

    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "validation_failed"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    assert packet is not None
    assert packet.delivery_status == "failed"
    assert mail_client.sent_messages == []
    assert len(adapter.snapshots) == 1


def test_direct_post_creation_reply_bridge_rejects_leading_slash_command(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectCurrentSessionReplyMailBridge(config, mail_client=FakeMailClient()),
        clock=lambda: "2026-03-23T09:33:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_reply_packet(
            workspace_id="workspace_demo",
            session_id="thread_001",
            thread_id="thread_001",
            reply_text="/resume\nPlease continue.",
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)

    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "validation_failed"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    assert server.packet_store.get_packet("android-taskmail:session-action:req_001") is None


def test_direct_post_creation_reply_bridge_rejects_structured_answers(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectCurrentSessionReplyMailBridge(config, mail_client=FakeMailClient()),
        clock=lambda: "2026-03-23T09:34:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_reply_packet(
            workspace_id="workspace_demo",
            session_id="thread_001",
            thread_id="thread_001",
            reply_text="Answers:\nquestion_task_001: yes",
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)

    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "validation_failed"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    assert server.packet_store.get_packet("android-taskmail:session-action:req_001") is None


def test_direct_post_creation_reply_bridge_rejects_attachments(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectCurrentSessionReplyMailBridge(config, mail_client=FakeMailClient()),
        clock=lambda: "2026-03-23T09:35:00",
    )
    connection_id = _connect(server)

    response = server.handle_client_message(
        _canonical_direct_reply_packet(
            workspace_id="workspace_demo",
            session_id="thread_001",
            thread_id="thread_001",
            attachments=[{"name": "notes.txt"}],
        ),
        connection_id=connection_id,
    )

    parsed = parse_server_message(response)

    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "validation_failed"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    assert server.packet_store.get_packet("android-taskmail:session-action:req_001") is None


def test_direct_post_creation_status_packet_rejects_non_current_session_scope(tmp_path) -> None:
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
        direct_packet_handler=RelayTaskMailDirectCurrentSessionStatusMailBridge(config, mail_client=FakeMailClient()),
        clock=lambda: "2026-03-22T12:30:00",
    )
    connection_id = _connect(server)
    packet = _canonical_direct_status_packet(
        workspace_id="workspace_demo",
        session_id="thread_001",
        thread_id="thread_001",
    )
    packet["task_run_packet"]["target"]["scope"] = "targeted_session"

    response = server.handle_client_message(packet, connection_id=connection_id)

    parsed = parse_server_message(response)
    assert isinstance(parsed, RelayErrorMessage)
    assert parsed.code == "current_session_only_violation"
    assert classify_direct_post_creation_server_outcome(parsed) == DIRECT_POST_CREATION_OUTCOME_HARD_STOP
    assert server.packet_store.get_packet("android-taskmail:session-action:req_001") is None


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


def _setup_existing_thread(task_root, dispatcher: Dispatcher | None = None) -> SerialTaskRunner:
    dispatcher = dispatcher or Dispatcher(MockAdapter(0), MockAdapter(0))
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor the module.",
        acceptance=["pytest passes"],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    runner.run_task_snapshot(
        snapshot,
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        session_name="demo task",
    )
    return runner


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


def _canonical_direct_status_packet(*, workspace_id: str, session_id: str, thread_id: str | None) -> dict[str, object]:
    target: dict[str, object] = {
        "scope": "current_session",
        "workspace_id": workspace_id,
        "session_id": session_id,
    }
    if thread_id is not None:
        target["thread_id"] = thread_id
    return {
        "message_type": "packet",
        "packet_id": "android-taskmail:session-action:req_001",
        "client_trace_id": "req_001",
        "task_run_packet": {
            "schema_version": "post-creation-session-action-contract-v1",
            "action": "status",
            "request_id": "req_001",
            "origin": {
                "client": "android_taskmail",
            },
            "target": target,
            "status": {},
        },
        "dispatch_metadata": {
            "channel": "taskmail_android_direct",
            "schema_version": "post-creation-session-action-contract-v1",
            "action": "status",
            "fallback_policy": "mail",
        },
        "sent_at": "2026-03-22T12:30:00",
    }


def _canonical_direct_reply_packet(
    *,
    workspace_id: str,
    session_id: str,
    thread_id: str | None,
    reply_text: str = "Please continue.",
    attachments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    packet = _canonical_direct_status_packet(
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    packet["task_run_packet"]["action"] = "reply"
    packet["task_run_packet"]["reply"] = {"reply_text": reply_text}
    packet["task_run_packet"].pop("status", None)
    if attachments is not None:
        packet["task_run_packet"]["attachments"] = attachments
    packet["dispatch_metadata"]["action"] = "reply"
    return packet
