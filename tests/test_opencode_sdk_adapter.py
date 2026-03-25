"""OpenCode SDK adapter tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mail_runner.adapters.opencode_sdk_adapter import OpenCodeSdkAdapter
from mail_runner.config import AppConfig
from mail_runner.models import TaskSnapshot
from mail_runner.opencode_sdk_common import ServerHandle
from mail_runner.run_result_capsule import render_run_result_capsule
from mail_runner.stream_events import load_stream_events


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
    assert [event.seq for event in stream_events] == [1, 2, 3]
    assert [event.kind for event in stream_events] == ["turn.started", "assistant.completed", "turn.completed"]
    assert stream_events[1].text == "STATUS: OK\nFILE: smoke_note.txt"
    assert stream_events[1].payload["stream_mode"] == "posthoc_assistant_parts_projection"
    assert stream_events[1].payload["text_part_count"] == 1
    assert stream_events[2].payload["sdk_session_id"] == "ses_fake_001"
    assert "---TASK-RUN-RESULT-BEGIN---" not in stdout_text
    assert stdout_text.strip() == "STATUS: OK\nFILE: smoke_note.txt"
    assert sdk_turn_payload["stream_event_count"] == 3
    assert sdk_turn_payload["stream_mode"] == "posthoc_assistant_parts_projection"
    assert sdk_turn_payload["stream_path"] == str(run_dir / "stream.events.jsonl")


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
    assert len(stream_events) == 3
    assert stream_events[1].kind == "assistant.completed"
    assert "What directory should I use?" in (stream_events[1].text or "")
    assert "---TASK-QUESTION-BEGIN---" in (stream_events[1].text or "")
