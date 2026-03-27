"""OpenCode SDK adapter tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mail_runner.adapters.opencode_sdk_adapter import OpenCodeSdkAdapter, _OpenCodeIncrementalStreamRecorder
from mail_runner.config import AppConfig
from mail_runner.models import TaskSnapshot
from mail_runner.opencode_sdk_common import ServerHandle
from mail_runner.run_result_capsule import render_run_result_capsule
from mail_runner.stream_events import StreamEvent, load_stream_events, write_stream_events
from mail_runner.status import RUN_STATUS_SUCCESS


class _FakePart:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = dict(payload)
        for key, value in payload.items():
            setattr(self, key, value)

    def model_dump(self, by_alias: bool = True, exclude_none: bool = True) -> dict[str, object]:
        _ = by_alias
        if exclude_none:
            return {key: value for key, value in self._payload.items() if value is not None}
        return dict(self._payload)


class _FakeSessionApi:
    assistant_parts: list[_FakePart] = []
    created_titles: list[dict[str, object]] = []
    last_chat: dict[str, object] | None = None

    def create(self, extra_body: dict[str, object]) -> object:
        self.created_titles.append(dict(extra_body))
        return SimpleNamespace(id="ses_fake_001")

    def chat(self, session_id: str, **kwargs) -> None:
        self.last_chat = {"session_id": session_id, **kwargs}

    def messages(self, session_id: str) -> list[object]:
        _ = session_id
        return [
            SimpleNamespace(info=SimpleNamespace(role="user"), parts=[]),
            SimpleNamespace(info=SimpleNamespace(role="assistant"), parts=list(self.assistant_parts)),
        ]


class _FakeAppApi:
    def providers(self) -> object:
        return SimpleNamespace()


class _FakeOpencode:
    session_api = _FakeSessionApi()

    def __init__(self, base_url: str, timeout: float, max_retries: int) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.app = _FakeAppApi()
        self.session = self.session_api

    def __enter__(self) -> "_FakeOpencode":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _ = exc_type
        _ = exc
        _ = tb
        return False


class _NoopIncrementalStreamCapture:
    def start(self) -> None:
        return None

    def finish(self, *, assistant_parts, visible_reply: str, finished_at: str):
        _ = visible_reply
        _ = finished_at
        return SimpleNamespace(
            stream_mode="event_stream_message_parts_incremental",
            assistant_parts_records=[part.model_dump(by_alias=True, exclude_none=True) for part in assistant_parts],
            stream_event_count=0,
            saw_incremental_evidence=False,
        )

    def abort(self) -> None:
        return None


def _snapshot(tmp_path: Path) -> TaskSnapshot:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="opencode",
        repo_path=str(repo_dir),
        workdir=None,
        task_text="Inspect the project.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-25T10:00:00",
        updated_at="2026-03-25T10:00:00",
        backend_transport="sdk",
    )


def _install_fake_server(monkeypatch, *, stderr_text: str = "") -> None:
    def _fake_start_server(*, output_dir: Path, workspace: Path, **kwargs) -> ServerHandle:
        _ = kwargs
        output_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = output_dir / "serve.stdout.log"
        stderr_log = output_dir / "serve.stderr.log"
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text(stderr_text, encoding="utf-8")
        return ServerHandle(
            process=SimpleNamespace(pid=4242),
            port=8787,
            base_url="http://127.0.0.1:8787",
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            workspace=workspace,
        )

    monkeypatch.setattr("mail_runner.adapters.opencode_sdk_adapter.start_server", _fake_start_server)
    monkeypatch.setattr("mail_runner.adapters.opencode_sdk_adapter.wait_for_server", lambda server, timeout_seconds: None)
    monkeypatch.setattr("mail_runner.adapters.opencode_sdk_adapter.stop_server", lambda server: None)
    monkeypatch.setattr(
        "mail_runner.adapters.opencode_sdk_adapter.resolve_profile_provider_model",
        lambda providers_payload, configured_model: ("provider-test", "model-test"),
    )
    monkeypatch.setattr("mail_runner.adapters.opencode_sdk_adapter.Opencode", _FakeOpencode)
    monkeypatch.setattr(
        "mail_runner.adapters.opencode_sdk_adapter._build_incremental_stream_capture",
        lambda **kwargs: _NoopIncrementalStreamCapture(),
    )


def test_opencode_sdk_adapter_persists_minimal_stream_evidence(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeSdkAdapter(AppConfig(opencode_command="opencode"))
    _FakeOpencode.session_api.assistant_parts = [
        _FakePart({"id": "prt_start", "type": "step-start"}),
        _FakePart(
            {
                "id": "prt_text",
                "type": "text",
                "text": "\n".join(
                    [
                        "STATUS: OK",
                        "FILE: smoke_note.txt",
                        render_run_result_capsule({"changed_files": ["smoke_note.txt"], "tests_passed": True}),
                    ]
                ),
                "time": {"start": 1774422031554.0, "end": 1774422032554.0},
            }
        ),
        _FakePart(
            {
                "id": "prt_finish",
                "type": "step-finish",
                "reason": "stop",
                "time": {"start": 1774422032554.0, "end": 1774422033554.0},
            }
        ),
    ]
    _FakeOpencode.session_api.created_titles = []
    _FakeOpencode.session_api.last_chat = None
    _install_fake_server(monkeypatch, stderr_text="server stderr\n")

    result = adapter.run(snapshot, str(run_dir))

    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    sdk_turn_payload = json.loads((run_dir / "sdk_turn.json").read_text(encoding="utf-8"))
    stream_events = load_stream_events(run_dir / "stream.events.jsonl")

    assert result.status == "success"
    assert result.backend_session_id == "ses_fake_001"
    assert _FakeOpencode.session_api.created_titles == [{"title": "mail-runner:thread_001:task_001"}]
    assert _FakeOpencode.session_api.last_chat is not None
    assert _FakeOpencode.session_api.last_chat["provider_id"] == "provider-test"
    assert _FakeOpencode.session_api.last_chat["model_id"] == "model-test"
    assert [event.seq for event in stream_events] == [1, 2, 3, 4]
    assert [event.kind for event in stream_events] == [
        "turn.started",
        "assistant.delta",
        "assistant.completed",
        "turn.completed",
    ]
    assert stream_events[1].delta == "STATUS: OK\nFILE: smoke_note.txt"
    assert stream_events[1].payload["stream_mode"] == "posthoc_assistant_parts_projection"
    assert stream_events[1].payload["text_part_count"] == 1
    assert stream_events[2].text == "STATUS: OK\nFILE: smoke_note.txt"
    assert stream_events[3].payload["sdk_session_id"] == "ses_fake_001"
    assert "---TASK-RUN-RESULT-BEGIN---" not in stdout_text
    assert stdout_text.strip() == "STATUS: OK\nFILE: smoke_note.txt"
    assert sdk_turn_payload["stream_event_count"] == 4
    assert sdk_turn_payload["stream_mode"] == "posthoc_assistant_parts_projection"
    assert sdk_turn_payload["stream_path"] == str(run_dir / "stream.events.jsonl")


def test_opencode_incremental_stream_recorder_flushes_pending_assistant_parts(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    run_dir.mkdir(parents=True)
    stream_path = run_dir / "stream.events.jsonl"
    recorder = _OpenCodeIncrementalStreamRecorder(
        task=snapshot,
        stream_events_path=stream_path,
        session_id="ses_fake_001",
        provider_id="provider-test",
        model_id="model-test",
        started_at="2026-03-25T10:00:00",
    )
    part_first = _FakePart(
        {
            "id": "prt_text",
            "type": "text",
            "message_id": "msg_assistant_001",
            "session_id": "ses_fake_001",
            "text": "STATUS",
            "time": {"start": 1774422031554.0, "end": 1774422032554.0},
        }
    )
    part_final = _FakePart(
        {
            "id": "prt_text",
            "type": "text",
            "message_id": "msg_assistant_001",
            "session_id": "ses_fake_001",
            "text": "STATUS: OK\nFILE: smoke_note.txt",
            "time": {"start": 1774422031554.0, "end": 1774422033554.0},
        }
    )

    recorder.handle_event(
        SimpleNamespace(
            type="message.part.updated",
            properties=SimpleNamespace(part=part_first),
        )
    )
    recorder.handle_event(
        SimpleNamespace(
            type="message.updated",
            properties=SimpleNamespace(
                info=SimpleNamespace(id="msg_assistant_001", role="assistant", session_id="ses_fake_001")
            ),
        )
    )
    recorder.handle_event(
        SimpleNamespace(
            type="message.part.updated",
            properties=SimpleNamespace(part=part_final),
        )
    )
    recorder.handle_event(
        SimpleNamespace(
            type="session.idle",
            properties=SimpleNamespace(session_id="ses_fake_001"),
        )
    )

    result = recorder.finalize(
        assistant_parts=[part_final],
        visible_reply="STATUS: OK\nFILE: smoke_note.txt",
        finished_at="2026-03-25T10:00:05",
    )
    events = load_stream_events(stream_path)

    assert result.saw_incremental_evidence is True
    assert result.stream_mode == "event_stream_message_parts_incremental"
    assert [event.seq for event in events] == [1, 2, 3, 4, 5]
    assert [event.kind for event in events] == [
        "turn.started",
        "assistant.delta",
        "assistant.delta",
        "assistant.completed",
        "turn.completed",
    ]
    assert events[1].delta == "STATUS"
    assert events[2].delta == ": OK\nFILE: smoke_note.txt"
    assert events[3].text == "STATUS: OK\nFILE: smoke_note.txt"


def test_opencode_sdk_adapter_uses_incremental_stream_mode_when_capture_succeeds(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeSdkAdapter(AppConfig(opencode_command="opencode"))
    _FakeOpencode.session_api.assistant_parts = [
        _FakePart(
            {
                "id": "prt_text",
                "type": "text",
                "text": "STATUS: OK\nFILE: smoke_note.txt",
                "message_id": "msg_assistant_001",
                "session_id": "ses_fake_001",
                "time": {"start": 1774422031554.0, "end": 1774422032554.0},
            }
        ),
    ]
    _install_fake_server(monkeypatch)

    class _IncrementalCaptureStub:
        def __init__(self, stream_path: Path, task: TaskSnapshot) -> None:
            self._stream_path = stream_path
            self._task = task

        def start(self) -> None:
            return None

        def finish(self, *, assistant_parts, visible_reply: str, finished_at: str):
            _ = finished_at
            write_stream_events(
                self._stream_path,
                [
                    StreamEvent(
                        ts="2026-03-25T11:11:49.870000Z",
                        seq=1,
                        thread_id=self._task.thread_id,
                        task_id=self._task.task_id,
                        backend=self._task.backend,
                        backend_transport=self._task.backend_transport or "sdk",
                        kind="turn.started",
                        text="OpenCode SDK turn started.",
                        status="started",
                        payload={"event_type": "turn.started", "stream_mode": "event_stream_message_parts_incremental"},
                    ),
                    StreamEvent(
                        ts="2026-03-25T11:11:50.000000Z",
                        seq=2,
                        thread_id=self._task.thread_id,
                        task_id=self._task.task_id,
                        backend=self._task.backend,
                        backend_transport=self._task.backend_transport or "sdk",
                        kind="assistant.delta",
                        delta=visible_reply,
                        item_type="agent_message",
                        status="streaming",
                        payload={
                            "event_type": "assistant.delta",
                            "stream_mode": "event_stream_message_parts_incremental",
                        },
                    ),
                    StreamEvent(
                        ts="2026-03-25T11:11:50.100000Z",
                        seq=3,
                        thread_id=self._task.thread_id,
                        task_id=self._task.task_id,
                        backend=self._task.backend,
                        backend_transport=self._task.backend_transport or "sdk",
                        kind="assistant.completed",
                        text=visible_reply,
                        item_type="agent_message",
                        status="completed",
                        payload={
                            "event_type": "assistant.completed",
                            "stream_mode": "event_stream_message_parts_incremental",
                        },
                    ),
                    StreamEvent(
                        ts="2026-03-25T11:11:50.200000Z",
                        seq=4,
                        thread_id=self._task.thread_id,
                        task_id=self._task.task_id,
                        backend=self._task.backend,
                        backend_transport=self._task.backend_transport or "sdk",
                        kind="turn.completed",
                        text="Turn completed",
                        status="completed",
                        payload={
                            "event_type": "turn.completed",
                            "stream_mode": "event_stream_message_parts_incremental",
                        },
                    ),
                ],
            )
            return SimpleNamespace(
                stream_mode="event_stream_message_parts_incremental",
                assistant_parts_records=[part.model_dump(by_alias=True, exclude_none=True) for part in assistant_parts],
                stream_event_count=4,
                saw_incremental_evidence=True,
            )

        def abort(self) -> None:
            return None

    monkeypatch.setattr(
        "mail_runner.adapters.opencode_sdk_adapter._build_incremental_stream_capture",
        lambda **kwargs: _IncrementalCaptureStub(kwargs["stream_events_path"], kwargs["task"]),
    )

    adapter.run(snapshot, str(run_dir))

    sdk_turn_payload = json.loads((run_dir / "sdk_turn.json").read_text(encoding="utf-8"))
    stream_events = load_stream_events(run_dir / "stream.events.jsonl")

    assert sdk_turn_payload["stream_mode"] == "event_stream_message_parts_incremental"
    assert sdk_turn_payload["stream_event_count"] == 4
    assert [event.kind for event in stream_events] == [
        "turn.started",
        "assistant.delta",
        "assistant.completed",
        "turn.completed",
    ]


def test_opencode_sdk_adapter_keeps_question_capsule_in_stream_text(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeSdkAdapter(AppConfig(opencode_command="opencode"))
    _FakeOpencode.session_api.assistant_parts = [
        _FakePart({"id": "prt_start", "type": "step-start"}),
        _FakePart(
            {
                "id": "prt_text",
                "type": "text",
                "text": "\n".join(
                    [
                        "I need one decision before I continue.",
                        "",
                        "---TASK-QUESTION-BEGIN---",
                        "question_set_id: qset_001",
                        "question_id: q_001",
                        "question_type: short_text",
                        "required: true",
                        "question_text: What directory should I use?",
                        "---TASK-QUESTION-END---",
                    ]
                ),
                "time": {"start": 1774422031554.0, "end": 1774422032554.0},
            }
        ),
        _FakePart({"id": "prt_finish", "type": "step-finish", "time": {"end": 1774422033554.0}}),
    ]
    _FakeOpencode.session_api.created_titles = []
    _FakeOpencode.session_api.last_chat = None
    _install_fake_server(monkeypatch)

    result = adapter.run(snapshot, str(run_dir))

    stream_events = load_stream_events(run_dir / "stream.events.jsonl")

    assert result.status == "awaiting_user_input"
    assert result.question_id == "q_001"
    assert len(stream_events) == 4
    assert stream_events[1].kind == "assistant.delta"
    assert "What directory should I use?" in (stream_events[1].delta or "")
    assert stream_events[2].kind == "assistant.completed"
    assert "What directory should I use?" in (stream_events[2].text or "")
    assert "---TASK-QUESTION-BEGIN---" in (stream_events[2].text or "")


def test_opencode_sdk_adapter_demo_falls_back_to_cli_demo_path(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="opencode",
        repo_path=str(repo_dir),
        workdir="src",
        task_text="Inspect the project.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-25T10:00:00",
        updated_at="2026-03-25T10:00:00",
        backend_transport="sdk",
    )
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeSdkAdapter(AppConfig(opencode_command="demo", auto_create_workdir=True, mock_sleep_seconds=0.0))

    result = adapter.run(snapshot, str(run_dir))

    assert result.status == RUN_STATUS_SUCCESS
    assert result.backend_transport == "sdk"
    assert result.backend_session_id == "demo-session-opencode-thread_001"
    assert (repo_dir / "src").is_dir()
