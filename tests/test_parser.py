"""Parser tests for Phase 2."""

from __future__ import annotations

import pytest

from mail_runner.parser import extract_session_tag, normalize_subject, parse_initial_task, parse_subject
from mail_runner.status import BACKEND_CODEX, BACKEND_OPENCODE


def test_parse_subject_recognizes_supported_prefixes() -> None:
    parsed_oc = parse_subject("[OC] Refactor task")
    parsed_cx = parse_subject("[CX] Analyze task")

    assert parsed_oc["is_new_task"] is True
    assert parsed_oc["backend"] == BACKEND_OPENCODE
    assert parsed_oc["subject_text"] == "Refactor task"
    assert parsed_oc["subject_norm"] == "refactor task"

    assert parsed_cx["is_new_task"] is True
    assert parsed_cx["backend"] == BACKEND_CODEX


def test_parse_subject_handles_unknown_and_kill_prefixes() -> None:
    parsed_unknown = parse_subject("Hello world")
    parsed_kill = parse_subject("[KILL] task_001")

    assert parsed_unknown["is_new_task"] is False
    assert parsed_unknown["backend"] is None
    assert parsed_kill["is_new_task"] is False
    assert parsed_kill["action"] == "KILL"


def test_normalize_subject_strips_reply_and_status_prefixes() -> None:
    assert normalize_subject("Re: [DONE] Demo task") == "demo task"
    assert normalize_subject("FW: [STATUS] Demo task") == "demo task"
    assert normalize_subject("Re: [QUESTION] Demo task") == "demo task"
    assert normalize_subject("Re: [DONE][S:thread_001] Demo task") == "demo task"
    assert normalize_subject("回复：[DONE] Demo task") == "demo task"
    assert normalize_subject("回复: [DONE] Demo task") == "demo task"
    assert normalize_subject("答复：[DONE] Demo task") == "demo task"
    assert normalize_subject("答复: [DONE] Demo task") == "demo task"
    assert extract_session_tag("Re: [DONE][S:thread_001] Demo task") == "thread_001"


def test_parse_initial_task_reads_sections_and_defaults() -> None:
    body = "\n".join(
        [
            "Repo: D:\\repo",
            "",
            "Task:",
            "Refactor the module.",
            "Keep the public API stable.",
            "",
            "Acceptance:",
            "1. pytest passes",
            "- brief summary",
        ]
    )

    parsed = parse_initial_task(body, default_timeout_minutes=45)

    assert parsed["repo_path"] == "D:\\repo"
    assert parsed["workdir"] is None
    assert parsed["timeout_minutes"] == 45
    assert parsed["mode"] == "modify"
    assert parsed["profile"] is None
    assert parsed["task_text"] == "Refactor the module.\nKeep the public API stable."
    assert parsed["acceptance"] == ["pytest passes", "brief summary"]


def test_parse_initial_task_reads_optional_profile() -> None:
    body = "\n".join(
        [
            "Repo: D:\\repo",
            "Profile: strong",
            "",
            "Task:",
            "Analyze the issue.",
        ]
    )

    parsed = parse_initial_task(body)

    assert parsed["profile"] == "strong"


def test_parse_initial_task_requires_repo_and_task() -> None:
    with pytest.raises(ValueError):
        parse_initial_task("Task:\nOnly task")
    with pytest.raises(ValueError):
        parse_initial_task("Repo: D:\\repo")
