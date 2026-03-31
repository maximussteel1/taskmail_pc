"""SDK stream smoke contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from mail_runner.sdk_stream_smoke import _opencode_stream_contract
from mail_runner.stream_events import StreamEvent, load_stream_events, write_stream_events


def _write_sdk_turn(path: Path, *, stream_mode: str = "event_stream_message_parts_incremental") -> None:
    path.write_text(
        json.dumps(
            {
                "session_id": "ses_001",
                "stream_mode": stream_mode,
                "assistant_parts": [
                    {"type": "step-start"},
                    {"type": "text", "text": "STATUS: OK\nFILE: stream_smoke_note.txt"},
                    {"type": "step-finish"},
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_opencode_stream_contract_accepts_incremental_stream_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "task_001"
    run_dir.mkdir(parents=True)
    write_stream_events(
        run_dir / "stream.events.jsonl",
        [
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=1,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="turn.started",
                text="OpenCode SDK turn started.",
                status="started",
                payload={"event_type": "turn.started"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=2,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="assistant.delta",
                delta="STATUS: OK\nFILE: stream_smoke_note.txt",
                item_type="agent_message",
                status="streaming",
                payload={"event_type": "assistant.delta"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=3,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="assistant.completed",
                text="STATUS: OK\nFILE: stream_smoke_note.txt",
                item_type="agent_message",
                status="completed",
                payload={"event_type": "assistant.completed"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=4,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="turn.completed",
                text="Turn completed",
                status="completed",
                payload={"event_type": "turn.completed"},
            ),
        ],
    )
    _write_sdk_turn(run_dir / "sdk_turn.json")
    failures: list[str] = []

    record = _opencode_stream_contract(run_dir, failures)

    assert failures == []
    assert record["stream_exists"] is True
    assert record["supports_persisted_stream"] is True
    assert record["supports_incremental_stream"] is True
    assert record["stream_mode"] == "event_stream_message_parts_incremental"
    assert record["seqs"] == [1, 2, 3, 4]
    assert record["kinds"] == ["turn.started", "assistant.delta", "assistant.completed", "turn.completed"]
    assert len(record["candidate_output_chunks"]) == 4
    assert "residual_gap" not in record


def test_opencode_stream_contract_keeps_residual_gap_for_posthoc_mode(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "task_001"
    run_dir.mkdir(parents=True)
    write_stream_events(
        run_dir / "stream.events.jsonl",
        [
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=1,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="turn.started",
                text="OpenCode SDK turn started.",
                status="started",
                payload={"event_type": "turn.started"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=2,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="assistant.delta",
                delta="STATUS: OK\nFILE: stream_smoke_note.txt",
                item_type="agent_message",
                status="streaming",
                payload={"event_type": "assistant.delta"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=3,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="assistant.completed",
                text="STATUS: OK\nFILE: stream_smoke_note.txt",
                item_type="agent_message",
                status="completed",
                payload={"event_type": "assistant.completed"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=4,
                thread_id="thread_001",
                task_id="task_001",
                backend="opencode",
                backend_transport="sdk",
                kind="turn.completed",
                text="Turn completed",
                status="completed",
                payload={"event_type": "turn.completed"},
            ),
        ],
    )
    _write_sdk_turn(run_dir / "sdk_turn.json", stream_mode="posthoc_assistant_parts_projection")
    failures: list[str] = []

    record = _opencode_stream_contract(run_dir, failures)

    assert failures == []
    assert record["supports_incremental_stream"] is False
    assert record["stream_mode"] == "posthoc_assistant_parts_projection"
    assert record["residual_gap"]["kind"] == "incremental_stream_not_proven"


def test_opencode_stream_contract_records_missing_stream_gap(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "task_001"
    run_dir.mkdir(parents=True)
    _write_sdk_turn(run_dir / "sdk_turn.json")
    failures: list[str] = []

    record = _opencode_stream_contract(run_dir, failures)

    assert "OpenCode stream smoke did not produce stream.events.jsonl." in failures
    assert record["stream_exists"] is False
    assert record["supports_persisted_stream"] is False
    assert record["residual_gap"]["kind"] == "missing_same_layer_stream_evidence"


def test_stream_events_preserve_multiline_and_newline_only_chunks(tmp_path: Path) -> None:
    path = tmp_path / "stream.events.jsonl"
    write_stream_events(
        path,
        [
            StreamEvent(
                ts="2026-03-30T10:00:00Z",
                seq=1,
                thread_id="thread_001",
                task_id="task_001",
                backend="codex",
                backend_transport="sdk",
                kind="assistant.delta",
                delta="Line 1\n",
                item_type="agent_message",
                status="streaming",
            ),
            StreamEvent(
                ts="2026-03-30T10:00:01Z",
                seq=2,
                thread_id="thread_001",
                task_id="task_001",
                backend="codex",
                backend_transport="sdk",
                kind="assistant.delta",
                delta="\nLine 2",
                item_type="agent_message",
                status="streaming",
            ),
            StreamEvent(
                ts="2026-03-30T10:00:02Z",
                seq=3,
                thread_id="thread_001",
                task_id="task_001",
                backend="codex",
                backend_transport="sdk",
                kind="assistant.completed",
                text="Line 1\n\nLine 2",
                item_type="agent_message",
                status="completed",
            ),
        ],
    )

    events = load_stream_events(path)

    assert [event.delta for event in events[:2]] == ["Line 1\n", "\nLine 2"]
    assert events[2].text == "Line 1\n\nLine 2"
