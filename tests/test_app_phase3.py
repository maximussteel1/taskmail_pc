"""Phase 3 app integration tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.app import (
    _build_recovery_callback_factory,
    _handle_existing_action,
    _maybe_schedule_requested_runner_restart,
    _process_batch,
    process_once,
)
from mail_runner.config import AppConfig
from mail_runner.context_layer import build_context
from mail_runner.dispatcher import Dispatcher
from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from mail_runner.models import MailEnvelope, ParsedMailAction, RunResult, TaskSnapshot
from mail_runner.runtime_control import list_runner_restart_request_paths, write_runner_restart_request
from mail_runner.runner import SerialTaskRunner
from mail_runner.state_capsule import render_state_capsule
from mail_runner.status import BACKEND_CODEX, BACKEND_OPENCODE, RUN_STATUS_KILLED, THREAD_STATUS_FAILED
from mail_runner.thread_store import create_thread, load_session_state, load_thread_state, save_raw_mail, save_thread_state
from mail_runner.workspace import WorkspaceManager


class FakeMailClient:
    def __init__(self, envelopes):
        self._envelopes = list(envelopes)
        self.sent_messages: list[dict] = []
        self._sent_count = 0

    def set_envelopes(self, envelopes) -> None:
        self._envelopes = list(envelopes)

    def fetch_unseen_messages(self):
        return list(self._envelopes)

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        self._sent_count += 1
        return f"<sent-{self._sent_count}@example.com>"


class RecordingAdapter(MockAdapter):
    def __init__(self) -> None:
        super().__init__(sleep_seconds=0)
        self.snapshots: list[TaskSnapshot] = []

    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        self.snapshots.append(task)
        return super().run(task, run_dir)


def _setup_existing_thread(task_root, dispatcher: Dispatcher) -> None:
    _setup_existing_thread_with_id(
        task_root,
        dispatcher,
        thread_id="thread_001",
        task_id="task_001",
        subject_norm="demo task",
        session_name="demo task",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
    )


def _setup_existing_thread_with_id(
    task_root,
    dispatcher: Dispatcher,
    *,
    thread_id: str,
    task_id: str,
    subject_norm: str,
    session_name: str,
    root_message_id: str,
    latest_message_id: str,
) -> None:
    runner = SerialTaskRunner(task_root, dispatcher)
    snapshot = TaskSnapshot(
        task_id=task_id,
        thread_id=thread_id,
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
        root_message_id=root_message_id,
        latest_message_id=latest_message_id,
        subject_norm=subject_norm,
        session_name=session_name,
    )


def _create_finished_thread(task_root, thread_id: str, *, last_active_at: str, updated_at: str) -> None:
    create_thread(
        thread_id=thread_id,
        root_message_id=f"<{thread_id}-root@example.com>",
        latest_message_id=f"<{thread_id}-latest@example.com>",
        subject_norm=f"demo task {thread_id}",
        session_name=f"Demo task {thread_id}",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id=f"task_{thread_id}",
        last_task_snapshot_file=f"snapshots/task_{thread_id}.json",
        task_root=task_root,
        status="done",
        history_files=[],
        last_summary="Completed",
        created_at="2026-03-12T12:00:00",
        updated_at=updated_at,
    )
    state = load_thread_state(thread_id, task_root)
    state.last_active_at = last_active_at
    save_thread_state(state, task_root)


def _setup_running_thread_with_live_assistant_output(task_root) -> None:
    workspace = WorkspaceManager(task_root)
    snapshot = TaskSnapshot(
        task_id="task_run",
        thread_id="thread_001",
        backend=BACKEND_CODEX,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect the live state.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
        backend_transport="sdk",
    )
    snapshot_path = workspace.save_snapshot(snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<running@example.com>",
        subject_norm="demo task",
        session_name="demo task",
        backend=BACKEND_CODEX,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_run",
        last_task_snapshot_file=snapshot_path.relative_to(task_root / "thread_001").as_posix(),
        task_root=task_root,
        status="running",
        history_files=[],
        last_summary="Mock run completed successfully.",
        backend_transport="sdk",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    stream_path = workspace.run_file_path("thread_001", "task_run", "stream.events.jsonl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-03-12T12:10:00",
                        "seq": 1,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "status",
                        "text": "Need to inspect the parser before patching.",
                        "item_type": "reasoning",
                        "status": "running",
                        "payload": {"message": "Need to inspect the parser before patching."},
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-03-12T12:10:05",
                        "seq": 2,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.delta",
                        "text": "I am applying the patch now.",
                        "delta": "I am applying the patch now.",
                        "item_type": "agent_message",
                        "status": "streaming",
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _setup_running_thread_with_tool_only_events(task_root) -> None:
    workspace = WorkspaceManager(task_root)
    snapshot = TaskSnapshot(
        task_id="task_run",
        thread_id="thread_001",
        backend=BACKEND_CODEX,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect the live state.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
        backend_transport="sdk",
    )
    snapshot_path = workspace.save_snapshot(snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<running@example.com>",
        subject_norm="demo task",
        session_name="demo task",
        backend=BACKEND_CODEX,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_run",
        last_task_snapshot_file=snapshot_path.relative_to(task_root / "thread_001").as_posix(),
        task_root=task_root,
        status="running",
        history_files=[],
        last_summary="Mock run completed successfully.",
        backend_transport="sdk",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    stream_path = workspace.run_file_path("thread_001", "task_run", "stream.events.jsonl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-03-12T12:10:00",
                        "seq": 1,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "tool.started",
                        "text": "pytest -q",
                        "item_type": "command_execution",
                        "status": "running",
                        "payload": {"command": "pytest -q"},
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-03-12T12:10:01",
                        "seq": 2,
                        "thread_id": "thread_001",
                        "task_id": "task_run",
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "tool.completed",
                        "text": "pytest -q",
                        "item_type": "command_execution",
                        "status": "completed",
                        "payload": {"command": "pytest -q", "exit_code": 0},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _direct_post_creation_headers(
    *,
    state,
    action_type: str,
    request_id: str,
    receipt_id: str,
) -> dict[str, str]:
    return {
        "Subject": f"Re: [S:{state.session_id or state.thread_id}] {state.session_name or state.subject_norm or state.thread_id}",
        "X-TaskMail-Direct": "1",
        "X-TaskMail-Relay-Request-Id": request_id,
        "X-TaskMail-Relay-Packet-Id": f"android-taskmail:session-action:{request_id}",
        "X-TaskMail-Relay-Receipt-Id": receipt_id,
        "X-TaskMail-Action-Type": action_type,
        "X-TaskMail-Target-Workspace-Id": state.workspace_id,
        "X-TaskMail-Target-Session-Id": state.session_id or state.thread_id,
        "X-TaskMail-Target-Thread-Id": state.thread_id,
    }


def _direct_status_envelope(state, *, request_id: str, receipt_id: str) -> MailEnvelope:
    subject = f"Re: [S:{state.session_id or state.thread_id}] {state.session_name or state.subject_norm or state.thread_id}"
    return MailEnvelope(
        message_id=f"<direct-status-{request_id}@example.com>",
        subject=subject,
        from_addr="user@example.com",
        to_addr="bot@example.com",
        date="2026-03-23T10:20:00",
        in_reply_to=state.latest_message_id,
        references=[state.root_message_id, state.latest_message_id],
        body_text=f"/status\n\n{render_state_capsule(state)}\n",
        raw_headers=_direct_post_creation_headers(
            state=state,
            action_type="status",
            request_id=request_id,
            receipt_id=receipt_id,
        ),
    )


def _direct_reply_envelope(state, *, request_id: str, receipt_id: str, reply_text: str) -> MailEnvelope:
    subject = f"Re: [S:{state.session_id or state.thread_id}] {state.session_name or state.subject_norm or state.thread_id}"
    return MailEnvelope(
        message_id=f"<direct-reply-{request_id}@example.com>",
        subject=subject,
        from_addr="user@example.com",
        to_addr="bot@example.com",
        date="2026-03-23T10:21:00",
        in_reply_to=state.latest_message_id,
        references=[state.root_message_id, state.latest_message_id],
        body_text=f"{reply_text}\n\n{render_state_capsule(state)}\n",
        raw_headers=_direct_post_creation_headers(
            state=state,
            action_type="reply",
            request_id=request_id,
            receipt_id=receipt_id,
        ),
    )


def test_process_once_handles_status_query_reply(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-status@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/status",
        raw_headers={"Subject": "Re: [DONE] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "This session is not currently running." in client.sent_messages[0]["body"]
    assert "\nSummary: Mock run completed successfully.\n" not in client.sent_messages[0]["body"]
    snapshots = list((tmp_path / "tasks" / "thread_001" / "snapshots").glob("*.json"))
    assert len(snapshots) == 1


def test_process_once_writes_direct_post_creation_status_closeout_on_local_task_root(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    state = load_thread_state("thread_001", task_root)
    envelope = _direct_status_envelope(
        state,
        request_id="req_status_001",
        receipt_id="relay-receipt:req_status_001",
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    closeout_payload = json.loads(
        (
            task_root
            / "thread_001"
            / "session_actions"
            / "req_status_001"
            / "session_action_closeout.json"
        ).read_text(encoding="utf-8")
    )

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert closeout_payload["action_type"] == "status"
    assert closeout_payload["request_id"] == "req_status_001"
    assert closeout_payload["receipt_id"] == "relay-receipt:req_status_001"
    assert closeout_payload["ingress_message_id"] == "<direct-status-req_status_001@example.com>"
    assert closeout_payload["terminal_mail_message_id"] == "<sent-1@example.com>"
    assert closeout_payload["terminal_mail_subject"] == "[STATUS][S:thread_001] demo task"
    assert closeout_payload["target_session_identity"] == {
        "workspace_id": state.workspace_id,
        "session_id": state.session_id,
        "thread_id": state.thread_id,
    }


def test_process_once_writes_direct_post_creation_reply_closeout_on_local_task_root(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    state = load_thread_state("thread_001", task_root)
    envelope = _direct_reply_envelope(
        state,
        request_id="req_reply_001",
        receipt_id="relay-receipt:req_reply_001",
        reply_text="Please continue with the cleanup.",
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    updated_state = load_thread_state("thread_001", task_root)
    closeout_payload = json.loads(
        (
            task_root
            / "thread_001"
            / "session_actions"
            / "req_reply_001"
            / "session_action_closeout.json"
        ).read_text(encoding="utf-8")
    )
    canonical_summary = json.loads(
        (task_root / "thread_001" / "runs" / updated_state.current_task_id / "canonical_summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]
    assert adapter.snapshots[-1].run_mode == "resume"
    assert closeout_payload["action_type"] == "reply"
    assert closeout_payload["request_id"] == "req_reply_001"
    assert closeout_payload["receipt_id"] == "relay-receipt:req_reply_001"
    assert closeout_payload["ingress_message_id"] == "<direct-reply-req_reply_001@example.com>"
    assert closeout_payload["terminal_mail_message_id"] == "<sent-3@example.com>"
    assert closeout_payload["terminal_mail_subject"] == "[DONE][S:thread_001] demo task"
    assert canonical_summary["receipt_id"] == "relay-receipt:req_reply_001"


def test_process_once_reply_bypasses_new_task_freshness_guard(tmp_path, monkeypatch) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-status-stale@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00+00:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/status",
        raw_headers={"Subject": "Re: [DONE] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(
        from_addr="user@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        new_task_max_age_minutes=60,
    )
    monkeypatch.setattr(
        "mail_runner.app._current_time_utc",
        lambda: datetime(2026, 3, 13, 12, 10, 0, tzinfo=timezone.utc),
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]


def test_handle_existing_action_status_query_for_running_thread_uses_live_assistant_output(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _setup_running_thread_with_live_assistant_output(task_root)
    envelope = MailEnvelope(
        message_id="<reply-status-running@example.com>",
        subject="Re: [RUNNING][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:30",
        in_reply_to="<running@example.com>",
        references=["<root@example.com>", "<running@example.com>"],
        body_text="/status",
        raw_headers={"Subject": "Re: [RUNNING][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    state = load_thread_state("thread_001", task_root)
    workspace = WorkspaceManager(task_root)
    snapshot = workspace.load_snapshot("thread_001", state.last_task_snapshot_file)

    handled = _handle_existing_action(
        envelope,
        config,
        task_root,
        client,
        None,
        state=state,
        snapshot=snapshot,
        latest_result=None,
        incoming_attachment_paths=[],
        subject_text="demo task",
        action=ParsedMailAction(action="STATUS_QUERY", confidence=1.0, raw_user_text=""),
        background=False,
        target_reply_chain=False,
    )

    assert handled is True
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "\nSummary: Running.\n" in client.sent_messages[0]["body"]
    assert "Reply:\nI am applying the patch now." in client.sent_messages[0]["body"]
    assert "Need to inspect the parser before patching." not in client.sent_messages[0]["body"]
    assert "\nSummary: Mock run completed successfully.\n" not in client.sent_messages[0]["body"]


def test_handle_existing_action_status_query_for_running_thread_hides_tool_only_stream_events(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _setup_running_thread_with_tool_only_events(task_root)
    envelope = MailEnvelope(
        message_id="<reply-status-running@example.com>",
        subject="Re: [RUNNING][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:30",
        in_reply_to="<running@example.com>",
        references=["<root@example.com>", "<running@example.com>"],
        body_text="/status",
        raw_headers={"Subject": "Re: [RUNNING][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    state = load_thread_state("thread_001", task_root)
    workspace = WorkspaceManager(task_root)
    snapshot = workspace.load_snapshot("thread_001", state.last_task_snapshot_file)

    handled = _handle_existing_action(
        envelope,
        config,
        task_root,
        client,
        None,
        state=state,
        snapshot=snapshot,
        latest_result=None,
        incoming_attachment_paths=[],
        subject_text="demo task",
        action=ParsedMailAction(action="STATUS_QUERY", confidence=1.0, raw_user_text=""),
        background=False,
        target_reply_chain=False,
    )

    assert handled is True
    assert "\nSummary: Running.\n" in client.sent_messages[0]["body"]
    assert "Reply:\nNo assistant output yet." in client.sent_messages[0]["body"]
    assert "pytest -q" not in client.sent_messages[0]["body"]


def test_process_once_handles_last_query_reply_without_backend_call(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-last@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/last",
        raw_headers={"Subject": "Re: [DONE] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "Latest local result for this session. This is a local lookup only; no backend call was made." in client.sent_messages[0]["body"]
    assert "Mock run completed successfully." in client.sent_messages[0]["body"]
    snapshots = list((tmp_path / "tasks" / "thread_001" / "snapshots").glob("*.json"))
    assert len(snapshots) == 1


def test_process_once_rejects_restart_runner_in_one_shot_mode(tmp_path, monkeypatch) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("MAIL_RUNNER_RUNTIME_DIR", str(runtime_dir))
    envelope = MailEnvelope(
        message_id="<reply-restart@example.com>",
        subject="Re: [DONE] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/restart-runner",
        raw_headers={"Subject": "Re: [DONE] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "Runner restart is only available while the hosted background mail loop is running." in client.sent_messages[0]["body"]
    assert list_runner_restart_request_paths(runtime_dir) == []


def test_maybe_schedule_requested_runner_restart_uses_detached_launcher(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    request_path = write_runner_restart_request(
        runtime_dir,
        source="mail",
        thread_id="thread_073",
        message_id="<reply@example.com>",
    )
    calls: list[tuple[str, str]] = []

    def fake_schedule(*, config_path: str, runtime_dir):
        calls.append((config_path, str(runtime_dir)))
        return True, "scheduled"

    monkeypatch.setenv("MAIL_RUNNER_CONFIG", str(tmp_path / "mail_config.yaml"))
    monkeypatch.setattr("mail_runner.app._schedule_detached_runner_restart", fake_schedule)

    scheduled = _maybe_schedule_requested_runner_restart(runtime_dir)

    assert scheduled is True
    assert calls == [(str(tmp_path / "mail_config.yaml"), str(runtime_dir))]
    assert not request_path.exists()
    assert list_runner_restart_request_paths(runtime_dir) == []


def test_process_once_targeted_status_query_replies_on_target_session_chain(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    _setup_existing_thread_with_id(
        task_root,
        dispatcher,
        thread_id="thread_002",
        task_id="task_002",
        subject_norm="other task",
        session_name="other task",
        root_message_id="<root-other@example.com>",
        latest_message_id="<done-other@example.com>",
    )
    envelope = MailEnvelope(
        message_id="<reply-target-status@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/status thread_002",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_002] other task"]
    assert client.sent_messages[0]["in_reply_to"] == "<done-other@example.com>"
    assert load_thread_state("thread_001", task_root).latest_message_id == "<done@example.com>"


def test_process_once_targeted_status_query_reports_unknown_session_in_current_thread(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-missing-target@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/status thread_999",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert client.sent_messages[0]["in_reply_to"] == envelope.message_id
    assert "was not found in this workspace" in client.sent_messages[0]["body"]


def test_process_once_sessions_listing_includes_targeted_command_hints(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-sessions@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/sessions",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "/last <session_id>" in client.sent_messages[0]["body"]
    assert "/continue <session_id>" in client.sent_messages[0]["body"]
    assert "/restart-runner" in client.sent_messages[0]["body"]
    assert "Targeted replies continue on the target session's own mail chain." in client.sent_messages[0]["body"]


def test_process_once_long_reply_with_status_words_continues_existing_session(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-status-words@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:30",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="在刚才的工作中，你有重新拉起服务吗？我现在正在使用邮件和你对话，你感觉目前的工作状态正常吗？",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].thread_id == "thread_001"
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].turn_text == (
        "在刚才的工作中，你有重新拉起服务吗？我现在正在使用邮件和你对话，你感觉目前的工作状态正常吗？"
    )
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]


def test_process_once_plain_reply_resumes_existing_session(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-update@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="Timeout: 120\nMode: analysis_only\nTask:\nOnly analyze the issue.",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    snapshot_path = tmp_path / "tasks" / "thread_001" / state["last_task_snapshot_file"]
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]
    assert adapter.snapshots[-1].thread_id == "thread_001"
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].turn_text == "Timeout: 120\nMode: analysis_only\nTask:\nOnly analyze the issue."
    assert snapshot_payload["timeout_minutes"] == 120
    assert snapshot_payload["mode"] == "analysis_only"
    assert snapshot_payload["task_text"] == "Only analyze the issue."
    assert not (tmp_path / "tasks" / "thread_002").exists()


def test_process_once_targeted_continue_runs_against_target_session(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    _setup_existing_thread_with_id(
        task_root,
        dispatcher,
        thread_id="thread_002",
        task_id="task_002",
        subject_norm="other task",
        session_name="other task",
        root_message_id="<root-other@example.com>",
        latest_message_id="<done-other@example.com>",
    )
    envelope = MailEnvelope(
        message_id="<reply-target-continue@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/continue thread_002\nTimeout: 120\nTask:\nOnly analyze the issue.",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].thread_id == "thread_002"
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].turn_text == "Timeout: 120\nTask:\nOnly analyze the issue."
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_002] other task",
        "[RUNNING][S:thread_002] other task",
        "[DONE][S:thread_002] other task",
    ]
    assert client.sent_messages[0]["in_reply_to"] == "<done-other@example.com>"
    assert load_thread_state("thread_001", task_root).latest_message_id == "<done@example.com>"


def test_process_once_targeted_resume_reactivates_paused_target_session(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    _setup_existing_thread_with_id(
        task_root,
        dispatcher,
        thread_id="thread_002",
        task_id="task_002",
        subject_norm="other task",
        session_name="other task",
        root_message_id="<root-other@example.com>",
        latest_message_id="<done-other@example.com>",
    )
    paused_state = load_thread_state("thread_002", task_root)
    paused_state.status = "paused"
    paused_state.paused_from_status = "done"
    save_thread_state(paused_state, task_root)
    envelope = MailEnvelope(
        message_id="<reply-target-resume@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:12:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/resume thread_002\nPlease continue with the cleanup.",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].thread_id == "thread_002"
    assert adapter.snapshots[-1].run_mode == "resume"
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_002] other task",
        "[RUNNING][S:thread_002] other task",
        "[DONE][S:thread_002] other task",
    ]


def test_process_once_new_codex_task_defaults_to_sdk_transport(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), adapter)
    envelope = MailEnvelope(
        message_id="<root-codex@example.com>",
        subject="[CX] Demo task",
        from_addr="user@example.com",
        to_addr="bot@example.com",
        date="2026-03-12T12:09:00",
        body_text="Repo: D:\\repo\nTask:\nInspect the project.\n",
        raw_headers={"Subject": "[CX] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].backend == BACKEND_CODEX
    assert adapter.snapshots[-1].backend_transport == "sdk"


def test_process_once_new_opencode_task_defaults_to_sdk_transport(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, MockAdapter(sleep_seconds=0))
    envelope = MailEnvelope(
        message_id="<root-opencode@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="bot@example.com",
        date="2026-03-25T12:09:00",
        body_text="Repo: D:\\repo\nTask:\nInspect the project.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].backend == BACKEND_OPENCODE
    assert adapter.snapshots[-1].backend_transport == "sdk"


def test_process_once_auto_ends_oldest_active_session_when_creating_fifth_one(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _create_finished_thread(task_root, "thread_001", last_active_at="2026-03-12T12:01:00", updated_at="2026-03-12T12:01:00")
    _create_finished_thread(task_root, "thread_002", last_active_at="2026-03-12T12:02:00", updated_at="2026-03-12T12:02:00")
    _create_finished_thread(task_root, "thread_003", last_active_at="2026-03-12T12:03:00", updated_at="2026-03-12T12:03:00")
    _create_finished_thread(task_root, "thread_004", last_active_at="2026-03-12T12:04:00", updated_at="2026-03-12T12:04:00")
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    envelope = MailEnvelope(
        message_id="<root-new-cap@example.com>",
        subject="[OC] New active task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:05:00",
        body_text="Repo: D:\\repo\nTask:\nCreate a fifth active thread.\n",
        raw_headers={"Subject": "[OC] New active task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert load_thread_state("thread_001", task_root).lifecycle == "ended"
    assert load_thread_state("thread_005", task_root).lifecycle == "active"
    assert "Auto-ended least recently active session(s) to keep the active working set within 4: thread_001" in (
        client.sent_messages[0]["body"]
    )


def test_process_once_reactivating_ended_thread_auto_ends_oldest_other_active_thread(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(task_root, dispatcher)
    for index, thread_id in enumerate(["thread_002", "thread_003", "thread_004", "thread_005"], start=2):
        _create_finished_thread(
            task_root,
            thread_id,
            last_active_at=f"2026-03-12T12:0{index}:00",
            updated_at=f"2026-03-12T12:0{index}:00",
        )
    ended_state = load_thread_state("thread_001", task_root)
    ended_state.lifecycle = "ended"
    ended_state.last_active_at = "2026-03-12T12:01:00"
    save_thread_state(ended_state, task_root)

    envelope = MailEnvelope(
        message_id="<reply-reactivate@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/resume\nPlease continue with the cleanup.",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert load_thread_state("thread_001", task_root).lifecycle == "active"
    assert load_thread_state("thread_002", task_root).lifecycle == "ended"


def test_process_once_end_command_marks_session_ended_without_changing_last_run_status(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    initial_state = load_thread_state("thread_001", task_root)
    envelope = MailEnvelope(
        message_id="<reply-end@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/end",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)
    ended_state = load_thread_state("thread_001", task_root)
    session_state = load_session_state(ended_state.workspace_id, ended_state.session_id or ended_state.thread_id, task_root)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert ended_state.lifecycle == "ended"
    assert ended_state.status == "done"
    assert ended_state.last_active_at == initial_state.last_active_at
    assert session_state.lifecycle == "ended"
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "now ended and removed from the active working set" in client.sent_messages[0]["body"]
    assert len(adapter.snapshots) == 1


def test_process_once_end_command_is_idempotent_for_already_ended_thread(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    state = load_thread_state("thread_001", task_root)
    state.lifecycle = "ended"
    save_thread_state(state, task_root)
    envelope = MailEnvelope(
        message_id="<reply-end-again@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/end",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert load_thread_state("thread_001", task_root).lifecycle == "ended"
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "already ended" in client.sent_messages[0]["body"]


def test_process_once_end_command_rejects_running_thread(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    task_root = tmp_path / "tasks"
    _setup_existing_thread(task_root, dispatcher)
    state = load_thread_state("thread_001", task_root)
    state.status = "running"
    save_thread_state(state, task_root)
    runner = SerialTaskRunner(task_root, dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-end-running@example.com>",
        subject="Re: [RUNNING][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:12:00",
        in_reply_to=state.latest_message_id,
        references=[state.root_message_id, state.latest_message_id],
        body_text="/end",
        raw_headers={"Subject": "Re: [RUNNING][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    context = build_context(envelope, state, task_root)

    handled = _handle_existing_action(
        envelope,
        config,
        task_root,
        client,
        runner,
        state=state,
        snapshot=context["latest_snapshot"],
        latest_result=context["latest_result"],
        incoming_attachment_paths=context["incoming_attachment_paths"],
        subject_text="demo task",
        action=ParsedMailAction(action="END_SESSION", confidence=1.0, raw_user_text=""),
        background=False,
    )

    assert handled is True
    assert load_thread_state("thread_001", task_root).lifecycle == "active"
    assert [item["subject"] for item in client.sent_messages] == ["[STATUS][S:thread_001] demo task"]
    assert "use /kill before /end" in client.sent_messages[0]["body"]


def test_process_once_new_command_starts_new_session(tmp_path) -> None:
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-new@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/new\nTimeout: 120\nMode: analysis_only\nTask:\nOnly analyze the issue.",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    state = json.loads((tmp_path / "tasks" / "thread_002" / "thread_state.json").read_text(encoding="utf-8"))
    snapshot_path = tmp_path / "tasks" / "thread_002" / state["last_task_snapshot_file"]
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_002] demo task",
        "[RUNNING][S:thread_002] demo task",
        "[DONE][S:thread_002] demo task",
    ]
    assert snapshot_payload["timeout_minutes"] == 120
    assert snapshot_payload["mode"] == "analysis_only"
    assert snapshot_payload["task_text"] == "Only analyze the issue."


def test_process_once_resume_command_continues_existing_native_session(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    envelope = MailEnvelope(
        message_id="<reply-resume@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:12:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="/resume\nPlease continue with the cleanup.",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].thread_id == "thread_001"
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].turn_text == "Please continue with the cleanup."
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]


def test_background_batch_handles_kill_reply(tmp_path) -> None:
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    runner = SerialTaskRunner(tmp_path / "tasks", dispatcher)
    client = FakeMailClient(
        [
            MailEnvelope(
                message_id="<root@example.com>",
                subject="[OC] Demo task",
                from_addr="user@example.com",
                to_addr="user@example.com",
                date="2026-03-12T12:20:00",
                body_text="Repo: D:\\repo\nTask:\nLong running task.\n",
                raw_headers={"Subject": "[OC] Demo task"},
            )
        ]
    )

    first_stats = _process_batch(config, tmp_path / "tasks", client, runner, background=True)
    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    kill_reply = MailEnvelope(
        message_id="<kill@example.com>",
        subject="Re: [RUNNING] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:20:01",
        in_reply_to=state["latest_message_id"],
        references=[state["root_message_id"], state["latest_message_id"]],
        body_text="/kill",
        raw_headers={"Subject": "Re: [RUNNING] Demo task"},
    )
    client.set_envelopes([kill_reply])

    second_stats = _process_batch(config, tmp_path / "tasks", client, runner, background=True)
    result = runner.wait_for_active()

    assert first_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert second_stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert result is not None
    assert result.status == RUN_STATUS_KILLED
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Demo task",
        "[RUNNING][S:thread_001] Demo task",
        "[KILLED][S:thread_001] Demo task",
    ]


def test_process_once_allows_risk_resume_after_kill(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    state_path = tmp_path / "tasks" / "thread_001" / "thread_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "killed"
    state["backend_session_id"] = "native-session-001"
    state["backend_session_resumable"] = False
    state["last_summary"] = "Task was killed."
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    envelope = MailEnvelope(
        message_id="<reply-after-kill@example.com>",
        subject="Re: [KILLED][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:30:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="请继续，并告诉我刚才发生了什么。",
        raw_headers={"Subject": "Re: [KILLED][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].run_mode == "resume"
    assert adapter.snapshots[-1].backend_session_id == "native-session-001"
    assert "Resuming a session after kill. This is a risk recovery" in client.sent_messages[0]["body"]
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]


def test_process_once_replays_failed_thread_as_new_recovery_run_without_native_session(tmp_path) -> None:
    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    _setup_existing_thread(tmp_path / "tasks", dispatcher)
    state_path = tmp_path / "tasks" / "thread_001" / "thread_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = THREAD_STATUS_FAILED
    state["backend_session_id"] = None
    state["backend_session_resumable"] = False
    state["last_summary"] = "Worker failed before a resumable native session was available."
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    envelope = MailEnvelope(
        message_id="<reply-after-failed@example.com>",
        subject="Re: [FAILED][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:31:00",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        body_text="Please continue with the cleanup.",
        raw_headers={"Subject": "Re: [FAILED][S:thread_001] Demo task"},
    )
    client = FakeMailClient([envelope])
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert adapter.snapshots[-1].run_mode == "new"
    assert adapter.snapshots[-1].backend_session_id is None
    assert adapter.snapshots[-1].turn_text is None
    assert "Additional context from reply:" in adapter.snapshots[-1].task_text
    assert "Please continue with the cleanup." in adapter.snapshots[-1].task_text
    assert "Starting a fresh recovery run from the latest saved task snapshot instead." in client.sent_messages[0]["body"]
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] demo task",
        "[RUNNING][S:thread_001] demo task",
        "[DONE][S:thread_001] demo task",
    ]


def test_recovered_accepted_task_sends_running_and_done_mail(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    snapshot = TaskSnapshot(
        task_id="task_queued",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Continue the queued recovery task.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    WorkspaceManager(task_root).save_snapshot(snapshot)
    create_thread(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<accepted@example.com>",
        subject_norm="demo task",
        session_name="Demo task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path=snapshot.repo_path,
        workdir=snapshot.workdir,
        current_task_id=snapshot.task_id,
        last_task_snapshot_file=f"snapshots/{snapshot.task_id}.json",
        task_root=task_root,
        status="accepted",
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<root@example.com>",
            "subject": "[OC] Demo task",
            "from_addr": "user@example.com",
            "to_addr": "bot@example.com",
            "date": "2026-03-12T12:00:00",
            "body_text": "Repo: D:\\repo\nTask:\nRecover me.\n",
            "raw_headers": {"Subject": "[OC] Demo task"},
        },
        task_root,
    )
    save_raw_mail(
        "thread_001",
        {
            "message_id": "<accepted@example.com>",
            "subject": "[ACCEPTED][S:thread_001] Demo task",
            "from_addr": "bot@example.com",
            "to_addr": "user@example.com",
            "date": "2026-03-12T12:00:05",
            "in_reply_to": "<root@example.com>",
            "references": ["<root@example.com>"],
            "body_text": "Accepted",
            "raw_headers": {
                "Subject": "[ACCEPTED][S:thread_001] Demo task",
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
            },
        },
        task_root,
    )

    adapter = RecordingAdapter()
    dispatcher = Dispatcher(adapter, adapter)
    client = FakeMailClient([])
    config = AppConfig(from_addr="bot@example.com", from_name="Mail Runner", task_root="tasks")
    runner = SerialTaskRunner(
        task_root,
        dispatcher,
        recovery_callback_factory=_build_recovery_callback_factory(config, task_root, client),
    )

    runner.dispatch_ready()
    runner.wait_until_idle()

    assert [item["subject"] for item in client.sent_messages] == [
        "[RUNNING][S:thread_001] Demo task",
        "[DONE][S:thread_001] Demo task",
    ]
    assert [item["to_addr"] for item in client.sent_messages] == ["user@example.com", "user@example.com"]
    assert client.sent_messages[0]["in_reply_to"] == "<accepted@example.com>"
    assert client.sent_messages[1]["in_reply_to"] == "<sent-1@example.com>"
