"""Phase 4 CLI adapter tests."""

from __future__ import annotations

import threading
import time

from mail_runner.adapters.cli_common import (
    WINDOWS,
    extract_error_excerpt,
    extract_summary_line,
    resolve_command_prefix,
)
from mail_runner.adapters.codex_adapter import CodexAdapter
from mail_runner.adapters.opencode_adapter import OpenCodeAdapter
from mail_runner.config import AppConfig
from mail_runner.models import TaskSnapshot
from mail_runner.status import BACKEND_CODEX, BACKEND_OPENCODE, RUN_STATUS_AWAITING_USER_INPUT, RUN_STATUS_KILLED, RUN_STATUS_SUCCESS


def _snapshot(tmp_path, backend: str, *, task_id: str = "task_001") -> TaskSnapshot:
    repo_dir = tmp_path / "repo"
    workdir = repo_dir / "src"
    workdir.mkdir(parents=True, exist_ok=True)
    return TaskSnapshot(
        task_id=task_id,
        thread_id="thread_001",
        backend=backend,
        profile=None,
        repo_path=str(repo_dir),
        workdir="src",
        task_text="Refactor the module without changing the API.",
        acceptance=["pytest passes", "brief summary"],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T14:00:00",
        updated_at="2026-03-12T14:00:00",
    )


def test_resolve_command_prefix_prefers_cmd_on_windows(monkeypatch) -> None:
    calls: list[str] = []

    def fake_which(name: str) -> str | None:
        calls.append(name)
        if WINDOWS and name == "opencode.cmd":
            return "C:\\tools\\opencode.cmd"
        if not WINDOWS and name == "opencode":
            return "/usr/local/bin/opencode"
        return None

    monkeypatch.setattr("mail_runner.adapters.cli_common.shutil.which", fake_which)

    resolved = resolve_command_prefix("", "opencode")

    if WINDOWS:
        assert calls == ["opencode.cmd"]
        assert resolved.prefix == ["C:\\tools\\opencode.cmd"]
    else:
        assert calls == ["opencode"]
        assert resolved.prefix == ["/usr/local/bin/opencode"]


def test_extract_summary_line_skips_headings_and_noise() -> None:
    output = "\n".join(
        [
            "**Repository Summary:**",
            "",
            "This is a minimal demo repository with one Python file.",
            "- main.py",
        ]
    )

    assert extract_summary_line(output) == "This is a minimal demo repository with one Python file."


def test_extract_error_excerpt_uses_last_meaningful_line() -> None:
    stderr_text = "\n".join(
        [
            "\u001b[91mError:\u001b[0m Unexpected error",
            "",
            "attempt to write a readonly database",
        ]
    )

    assert extract_error_excerpt(stderr_text) == "attempt to write a readonly database"


def test_opencode_adapter_demo_run_writes_outputs(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_OPENCODE)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeAdapter(AppConfig(opencode_command="demo", mock_sleep_seconds=0.0))

    result = adapter.run(snapshot, str(run_dir))

    prompt_text = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    summary_text = (run_dir / "summary.md").read_text(encoding="utf-8")

    assert result.status == RUN_STATUS_SUCCESS
    assert result.summary_file == f"runs/{snapshot.task_id}/summary.md"
    assert "You are OpenCode." in prompt_text
    assert "Demo backend opencode finished" in stdout_text
    assert summary_text.startswith("Demo backend opencode finished")
    assert "demo (opencode)" in summary_text


def test_opencode_adapter_places_message_before_file_option(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_OPENCODE)
    adapter = OpenCodeAdapter(AppConfig(opencode_command="opencode"))
    resolved = resolve_command_prefix("opencode", "opencode")
    command, stdin_text, _ = adapter._build_backend_command(  # type: ignore[attr-defined]
        task=snapshot,
        resolved=resolved,
        prompt_path=tmp_path / "prompt.txt",
        cwd=tmp_path,
    )

    assert stdin_text is None
    assert command[:3] == ["opencode", "run", "Execute the attached prompt.txt exactly."]
    assert "--file" in command
    assert command.index("--file") > 2


def test_codex_adapter_demo_run_uses_stdin_prompt(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_CODEX)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexAdapter(AppConfig(codex_command="demo", mock_sleep_seconds=0.0))

    result = adapter.run(snapshot, str(run_dir))

    stdout_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    summary_text = (run_dir / "summary.md").read_text(encoding="utf-8")

    assert result.status == RUN_STATUS_SUCCESS
    assert "Stdin chars:" in stdout_text
    assert summary_text.startswith("Demo backend codex finished")
    assert "demo (codex)" in summary_text


def test_codex_adapter_demo_run_detects_question_capsule(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_CODEX)
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = CodexAdapter(AppConfig(codex_command="demo", mock_sleep_seconds=0.0))
    monkeypatch.setenv("MAIL_RUNNER_DEMO_QUESTION_ID", "question_task_001")
    monkeypatch.setenv("MAIL_RUNNER_DEMO_QUESTION_TEXT", "Should I update both files?")
    monkeypatch.setenv("MAIL_RUNNER_DEMO_QUESTION_CHOICES", "yes | no")

    result = adapter.run(snapshot, str(run_dir))

    assert result.status == RUN_STATUS_AWAITING_USER_INPUT
    assert result.question_id == "question_task_001"
    assert result.question_text == "Should I update both files?"
    assert result.pending_choices == ["yes", "no"]


def test_codex_adapter_adds_mapped_model_for_profile(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_CODEX)
    snapshot.profile = "strong"
    adapter = CodexAdapter(
        AppConfig(
            codex_command="codex",
            codex_profile_models={"strong": "gpt-5-codex"},
        )
    )
    resolved = resolve_command_prefix("codex", "codex")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("prompt", encoding="utf-8")
    command, _, _ = adapter._build_backend_command(  # type: ignore[attr-defined]
        task=snapshot,
        resolved=resolved,
        prompt_path=prompt_path,
        cwd=tmp_path,
    )

    assert ["-m", "gpt-5-codex"] == command[5:7]


def test_opencode_adapter_fails_when_profile_mapping_is_missing(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_OPENCODE)
    snapshot.profile = "vision"
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeAdapter(AppConfig(opencode_command="demo", mock_sleep_seconds=0.0))

    result = adapter.run(snapshot, str(run_dir))

    assert result.status != RUN_STATUS_SUCCESS
    assert "profile 'vision'" in (result.error_message or "")


def test_opencode_adapter_demo_kill_marks_result_killed(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, BACKEND_OPENCODE, task_id="task_kill")
    run_dir = tmp_path / snapshot.thread_id / "runs" / snapshot.task_id
    adapter = OpenCodeAdapter(AppConfig(opencode_command="demo", mock_sleep_seconds=3.0))
    result_holder: dict[str, object] = {}

    def _run() -> None:
        result_holder["result"] = adapter.run(snapshot, str(run_dir))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    time.sleep(0.2)

    assert adapter.kill(snapshot.task_id) is True
    worker.join(timeout=5)

    result = result_holder["result"]
    assert result.status == RUN_STATUS_KILLED
    assert "OpenCode task was killed." in (run_dir / "summary.md").read_text(encoding="utf-8")
