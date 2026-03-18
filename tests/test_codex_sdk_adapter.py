"""Codex SDK adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

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
        _FakePopen.last_command = self.command
        _FakePopen.last_env = kwargs.get("env")

    def communicate(self, stdin_text: str):
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
        return self.returncode


class _StructuredResultPopen(_FakePopen):
    def communicate(self, stdin_text: str):
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
