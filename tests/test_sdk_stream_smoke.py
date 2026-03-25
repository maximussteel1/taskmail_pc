"""SDK stream smoke contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from mail_runner.sdk_stream_smoke import _opencode_stream_contract
from mail_runner.stream_events import StreamEvent, write_stream_events


def _write_sdk_turn(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "session_id": "ses_001",
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


def test_opencode_stream_contract_accepts_persisted_stream_evidence(tmp_path: Path) -> None:
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
                kind="assistant.completed",
                text="STATUS: OK\nFILE: stream_smoke_note.txt",
                item_type="agent_message",
                status="completed",
                payload={"event_type": "assistant.completed"},
            ),
            StreamEvent(
                ts="2026-03-25T11:11:49.870000Z",
                seq=3,
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
    assert record["supports_incremental_stream"] is False
    assert record["seqs"] == [1, 2, 3]
    assert record["kinds"] == ["turn.started", "assistant.completed", "turn.completed"]
    assert len(record["candidate_output_chunks"]) == 3
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
