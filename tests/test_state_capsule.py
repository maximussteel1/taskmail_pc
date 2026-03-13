"""State capsule tests for Phase 1."""

from __future__ import annotations

from mail_runner.state_capsule import (
    BEGIN_MARKER,
    END_MARKER,
    QUESTION_BEGIN_MARKER,
    QUESTION_END_MARKER,
    parse_question_capsule,
    parse_state_capsule,
    render_question_capsule,
    render_state_capsule,
)


def test_render_and_parse_state_capsule() -> None:
    rendered = render_state_capsule(
        {
            "thread_id": "thread_001",
            "workspace_id": "workspace_001",
            "session_id": "thread_001",
            "session_name": "Demo task",
            "task_id": "task_001",
            "backend": "opencode",
            "repo_path": "D:\\repo",
            "workdir": "src",
            "mode": "modify",
            "status": "done",
            "last_summary": "Finished successfully",
        }
    )

    parsed = parse_state_capsule(rendered)

    assert rendered.startswith(BEGIN_MARKER)
    assert rendered.endswith(END_MARKER)
    assert parsed is not None
    assert parsed["thread_id"] == "thread_001"
    assert parsed["workspace_id"] == "workspace_001"
    assert parsed["session_name"] == "Demo task"
    assert parsed["status"] == "done"


def test_state_capsule_flattens_multiline_values_and_uses_last_block() -> None:
    first = render_state_capsule({"thread_id": "thread_001", "task_id": "task_001"})
    second = render_state_capsule({"thread_id": "thread_002", "task_id": "task_002", "last_summary": "line1\nline2"})

    parsed = parse_state_capsule(first + "\nhello\n" + second)

    assert parsed is not None
    assert parsed["thread_id"] == "thread_002"
    assert parsed["last_summary"] == "line1 line2"


def test_parse_state_capsule_returns_none_without_complete_block() -> None:
    assert parse_state_capsule("thread_id: thread_001") is None


def test_render_and_parse_question_capsule() -> None:
    rendered = render_question_capsule(
        {
            "question_id": "question_task_001",
            "question_text": "Should I update both files?",
            "choices": ["yes", "no"],
        }
    )

    parsed = parse_question_capsule(rendered)

    assert rendered.startswith(QUESTION_BEGIN_MARKER)
    assert rendered.endswith(QUESTION_END_MARKER)
    assert parsed is not None
    assert parsed["question_id"] == "question_task_001"
    assert parsed["choices"] == ["yes", "no"]
