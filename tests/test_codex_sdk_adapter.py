"""Codex SDK adapter tests."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from mail_runner.adapters.codex_sdk_adapter import CodexSdkAdapter
from mail_runner.config import AppConfig
from mail_runner.models import TaskSnapshot
from mail_runner.run_result_capsule import render_run_result_capsule


class _FakePopen:
    last_command: list[str] | None = None
    last_stdin: str | None = None
    last_env: dict[str, str] | None = None

    def __init__(self, command, **kwargs) -> None:
        self.command = list(command)
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode = 0
        self._alive = True
        _FakePopen.last_command = self.command
        _FakePopen.last_env = kwargs.get("env")

    def communicate(self, stdin_text: str | None = None, timeout: float | None = None):
        if stdin_text is not None:
            _FakePopen.last_stdin = stdin_text
        return (
            json.dumps(
                {
                    "thread_id": "sdk-thread-001",
                    "final_response": "SDK adapter completed the task successfully.",
                    "usage": {"input_tokens": 12, "output_tokens": 6},
                    "item_count": 1,
                }
            ),
            "",
        )

    def poll(self):
        return None if self._alive else self.returncode

    def wait(self, timeout: float | None = None):
        self._alive = False
        return self.returncode

    def kill(self):
        self._alive = False


class _StructuredResultPopen(_FakePopen):
    def communicate(self, stdin_text: str | None = None, timeout: float | None = None):
        if stdin_text is not None:
            _FakePopen.last_stdin = stdin_text
        final_response = "\n".join(
            [
                "SDK adapter completed the task successfully.",
                "",
                render_run_result_capsule(
                    {
                        "changed_files": ["src/app.py", "tests/test_app.py"],
                        "tests_passed": True,
                    }
                ),
            ]
        )
        return (
            json.dumps(
                {
                    "thread_id": "sdk-thread-001",
                    "final_response": final_response,
                    "usage": {"input_tokens": 12, "output_tokens": 6},
                    "item_count": 1,
                }
            ),
            "",
        )


class _QuestionPopen(_FakePopen):
    def communicate(self, stdin_text: str | None = None, timeout: float | None = None):
        if stdin_text is not None:
            _FakePopen.last_stdin = stdin_text
        final_response = "\n".join(
            [
                "I need one decision before I continue.",
                "",
                "---TASK-QUESTION-BEGIN---",
                "question_set_id: vps_scope_001",
                "question_id: vps_state_scope",
                "question_type: single_choice",
                "required: true",
                "question_text: Should VPS persist session continuity?",
                "choices: relay_history_session | full_task_authority",
                (
                    "choice_labels: relay_history_session=Keep relay/session history "
                    "| full_task_authority=Make VPS authoritative"
                ),
                "---TASK-QUESTION-END---",
            ]
        )
        return (
            json.dumps(
                {
                    "thread_id": "sdk-thread-001",
                    "final_response": final_response,
                    "usage": {"input_tokens": 12, "output_tokens": 6},
                    "item_count": 1,
                }
            ),
            "",
        )


class _TimeoutAfterCompletedPopen(_FakePopen):
    def __init__(self, command, **kwargs) -> None:
        super().__init__(command, **kwargs)
        self._timed_out = False

    def communicate(self, stdin_text: str | None = None, timeout: float | None = None):
        if stdin_text is not None:
            _FakePopen.last_stdin = stdin_text
        if not self._timed_out:
            self._timed_out = True
            raise subprocess.TimeoutExpired(self.command, timeout or 0, output="", stderr="sidecar still draining\n")
        return super().communicate(stdin_text=stdin_text, timeout=timeout)


def _snapshot(tmp_path: Path) -> TaskSnapshot:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="codex",
        repo_path=str(repo_dir),
        workdir=None,
        task_text="Inspect the project.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-18T10:05:00",
        updated_at="2026-03-18T10:05:00",
        backend_transport="sdk",
    )


def test_codex_sdk_adapter_runs_sidecar_and_records_thread_id(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(
        AppConfig(
            codex_sdk_sidecar_command="node fake-sidecar.js",
            codex_profile_models={"strong": "gpt-5-codex"},
        )
    )
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _FakePopen)

    result = adapter.run(snapshot, str(run_dir))

    request = json.loads(_FakePopen.last_stdin or "{}")
    sidecar_request = json.loads((run_dir / "sidecar_request.json").read_text(encoding="utf-8"))
    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    prompt_text = (run_dir / "prompt.txt").read_text(encoding="utf-8")

    assert _FakePopen.last_command == ["node", "fake-sidecar.js"]
    assert _FakePopen.last_stdin is not None
    assert request["action"] == "start"
    assert request["mail_thread_id"] == "thread_001"
    assert request["task_id"] == "task_001"
    assert request["stream_path"] == str(run_dir / "stream.events.jsonl")
    assert sidecar_request == request
    assert _FakePopen.last_env is not None
    assert _FakePopen.last_env["MAIL_RUNNER_MAIL_THREAD_ID"] == "thread_001"
    assert _FakePopen.last_env["MAIL_RUNNER_TASK_ID"] == "task_001"
    assert request["sandbox_mode"] == "workspace-write"
    assert request["web_search_mode"] == "disabled"
    assert result.status == "success"
    assert result.backend_transport == "sdk"
    assert result.backend_session_id == "sdk-thread-001"
    assert "SDK adapter completed the task successfully." in stdout_text
    assert "Runtime Mail Paths:" in prompt_text
    assert not (run_dir / "codex_sidecar_process.json").exists()


def test_codex_sdk_adapter_reply_uses_existing_thread_id(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    snapshot.run_mode = "resume"
    snapshot.backend_session_id = "sdk-thread-existing"
    snapshot.turn_text = "Please continue."
    snapshot.permission = "highest"
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js", enable_web_search=True))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _FakePopen)

    result = adapter.run(snapshot, str(run_dir))
    request = json.loads(_FakePopen.last_stdin or "{}")

    assert request["action"] == "reply"
    assert request["thread_id"] == "sdk-thread-existing"
    assert request["sandbox_mode"] == "danger-full-access"
    assert request["web_search_mode"] == "live"
    assert result.backend_session_id == "sdk-thread-001"


def test_codex_sdk_adapter_ignores_default_profile_without_mapping(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    snapshot.profile = "default"
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _FakePopen)

    result = adapter.run(snapshot, str(run_dir))
    request = json.loads(_FakePopen.last_stdin or "{}")

    assert result.status == "success"
    assert request["model"] is None


def test_codex_sdk_adapter_injects_default_proxy_env_for_sidecar(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _FakePopen)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    adapter.run(snapshot, str(run_dir))

    assert _FakePopen.last_env is not None
    assert _FakePopen.last_env["HTTP_PROXY"] == "http://127.0.0.1:10809"
    assert _FakePopen.last_env["HTTPS_PROXY"] == "http://127.0.0.1:10809"
    assert _FakePopen.last_env["ALL_PROXY"] == "http://127.0.0.1:10809"
    assert _FakePopen.last_env["NO_PROXY"] == "localhost,127.0.0.1,::1"


def test_codex_sdk_adapter_preserves_existing_proxy_env_for_sidecar(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _FakePopen)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:9000")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:9001")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.example:9002")
    monkeypatch.setenv("NO_PROXY", "localhost,example.com")

    adapter.run(snapshot, str(run_dir))

    assert _FakePopen.last_env is not None
    assert _FakePopen.last_env["HTTP_PROXY"] == "http://proxy.example:9000"
    assert _FakePopen.last_env["HTTPS_PROXY"] == "http://proxy.example:9001"
    assert _FakePopen.last_env["ALL_PROXY"] == "socks5://proxy.example:9002"
    assert _FakePopen.last_env["NO_PROXY"] == "localhost,example.com"


def test_codex_sdk_adapter_parses_structured_result_capsule(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _StructuredResultPopen)

    result = adapter.run(snapshot, str(run_dir))
    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")

    assert result.changed_files == ["src/app.py", "tests/test_app.py"]
    assert result.tests_passed is True
    assert "---TASK-RUN-RESULT-BEGIN---" not in stdout_text


def test_codex_sdk_adapter_returns_awaiting_user_input_for_question_capsules(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _QuestionPopen)

    result = adapter.run(snapshot, str(run_dir))
    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")

    assert result.status == "awaiting_user_input"
    assert result.question_id == "vps_state_scope"
    assert result.question_text == "Should VPS persist session continuity?"
    assert result.pending_choices == ["relay_history_session", "full_task_authority"]
    assert result.question_set_id == "vps_scope_001"
    assert len(result.pending_questions) == 1
    assert result.pending_questions[0].question_text == "Should VPS persist session continuity?"
    assert result.changed_files == []
    assert result.tests_passed is None
    assert "I need one decision before I continue." in stdout_text
    assert "---TASK-QUESTION-BEGIN---" in stdout_text


def test_codex_sdk_adapter_recovers_when_terminal_stream_completed_but_sidecar_hangs(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    run_dir.mkdir(parents=True)
    stream_path = run_dir / "stream.events.jsonl"
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-03-18T10:05:10.000Z",
                        "seq": 1,
                        "thread_id": snapshot.thread_id,
                        "task_id": snapshot.task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.completed",
                        "text": "Recovered final response from stream events.",
                        "item_type": "agent_message",
                        "status": "completed",
                        "payload": {
                            "item_id": "item_1",
                            "event_type": "item.completed",
                            "sdk_thread_id": "sdk-thread-recovered",
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-03-18T10:05:11.000Z",
                        "seq": 2,
                        "thread_id": snapshot.thread_id,
                        "task_id": snapshot.task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "turn.completed",
                        "text": "Turn completed",
                        "status": "completed",
                        "payload": {
                            "usage": {"input_tokens": 3, "output_tokens": 4},
                            "event_type": "turn.completed",
                            "sdk_thread_id": "sdk-thread-recovered",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    taskkill_calls: list[list[str]] = []

    def _fake_run(command, **kwargs):
        taskkill_calls.append(list(command))
        return None

    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.WINDOWS", True)
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _TimeoutAfterCompletedPopen)
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.run", _fake_run)

    result = adapter.run(snapshot, str(run_dir))

    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    stderr_text = (run_dir / "stderr.log").read_text(encoding="utf-8")
    sidecar_payload = json.loads((run_dir / "sdk_turn.json").read_text(encoding="utf-8"))

    assert result.status == "success"
    assert result.backend_session_id == "sdk-thread-recovered"
    assert "Recovered final response from stream events." in stdout_text
    assert "forced shutdown" in stderr_text
    assert sidecar_payload["thread_id"] == "sdk-thread-recovered"
    assert sidecar_payload["recovered_from_terminal_stream"] is True
    assert taskkill_calls == [["taskkill", "/T", "/F", "/PID", "4242"]]


def test_codex_sdk_adapter_recovery_ignores_trailing_empty_assistant_message(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    run_dir.mkdir(parents=True)
    stream_path = run_dir / "stream.events.jsonl"
    stream_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-03-18T10:05:10.000Z",
                        "seq": 1,
                        "thread_id": snapshot.thread_id,
                        "task_id": snapshot.task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.completed",
                        "text": "Recovered final response from stream events.",
                        "item_type": "agent_message",
                        "status": "completed",
                        "payload": {
                            "item_id": "item_1",
                            "event_type": "item.completed",
                            "sdk_thread_id": "sdk-thread-recovered",
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-03-18T10:05:10.500Z",
                        "seq": 2,
                        "thread_id": snapshot.thread_id,
                        "task_id": snapshot.task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "assistant.completed",
                        "text": "",
                        "item_type": "agent_message",
                        "status": "completed",
                        "payload": {
                            "item_id": "item_2",
                            "event_type": "item.completed",
                            "sdk_thread_id": "sdk-thread-recovered",
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-03-18T10:05:11.000Z",
                        "seq": 3,
                        "thread_id": snapshot.thread_id,
                        "task_id": snapshot.task_id,
                        "backend": "codex",
                        "backend_transport": "sdk",
                        "kind": "turn.completed",
                        "text": "Turn completed",
                        "status": "completed",
                        "payload": {
                            "usage": {"input_tokens": 3, "output_tokens": 4},
                            "event_type": "turn.completed",
                            "sdk_thread_id": "sdk-thread-recovered",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    taskkill_calls: list[list[str]] = []

    def _fake_run(command, **kwargs):
        taskkill_calls.append(list(command))
        return None

    adapter = CodexSdkAdapter(AppConfig(codex_sdk_sidecar_command="node fake-sidecar.js"))
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.WINDOWS", True)
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.Popen", _TimeoutAfterCompletedPopen)
    monkeypatch.setattr("mail_runner.adapters.codex_sdk_adapter.subprocess.run", _fake_run)

    result = adapter.run(snapshot, str(run_dir))

    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    sidecar_payload = json.loads((run_dir / "sdk_turn.json").read_text(encoding="utf-8"))

    assert result.status == "success"
    assert "Recovered final response from stream events." in stdout_text
    assert sidecar_payload["final_response"] == "Recovered final response from stream events."
    assert taskkill_calls == [["taskkill", "/T", "/F", "/PID", "4242"]]
