"""Phase 3 app integration tests."""

from __future__ import annotations

import json

from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.app import _build_recovery_callback_factory, _handle_existing_action, _process_batch, process_once
from mail_runner.config import AppConfig
from mail_runner.context_layer import build_context
from mail_runner.dispatcher import Dispatcher
from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE
from mail_runner.models import MailEnvelope, ParsedMailAction, RunResult, TaskSnapshot
from mail_runner.runner import SerialTaskRunner
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
    snapshots = list((tmp_path / "tasks" / "thread_001" / "snapshots").glob("*.json"))
    assert len(snapshots) == 1


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
