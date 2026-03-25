"""Standalone sdk-first stream/output-chunk smoke for Codex and OpenCode."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .sdk_runtime_smoke import run_runtime_smoke
from .stream_events import STREAM_EVENTS_FILENAME, load_stream_events

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_sdk_stream_smoke"
DEFAULT_FILENAME = "stream_smoke_note.txt"
DEFAULT_FILE_TEXT = "hello from sdk stream smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_dir_from_runtime_result(runtime_result: dict[str, Any]) -> Path:
    return Path(str(runtime_result["result_path"])).parent


def _codex_stream_contract(run_dir: Path, failures: list[str]) -> dict[str, Any]:
    stream_path = run_dir / STREAM_EVENTS_FILENAME
    if not stream_path.exists():
        failures.append("Codex stream smoke did not produce stream.events.jsonl.")
        return {
            "stream_path": str(stream_path),
            "stream_exists": False,
            "supports_persisted_stream": False,
            "candidate_output_chunks": [],
        }

    events = load_stream_events(stream_path)
    seqs = [event.seq for event in events]
    expected_seqs = list(range(1, len(seqs) + 1))
    if seqs != expected_seqs:
        failures.append(f"Codex stream seq contract mismatch: observed {seqs}, expected {expected_seqs}.")
    if not any(event.kind == "assistant.delta" for event in events):
        failures.append("Codex stream is missing assistant.delta events.")
    if not any(event.kind == "turn.completed" for event in events):
        failures.append("Codex stream is missing terminal turn.completed event.")

    stream_id = f"{events[0].thread_id}:{events[0].task_id}" if events else None
    candidate_output_chunks = [
        {
            "stream_id": stream_id,
            "stream_id_source": "derived_from_run_identity",
            "seq": event.seq,
            "kind": event.kind,
            "text": event.text,
            "delta": event.delta,
            "status": event.status,
            "item_type": event.item_type,
        }
        for event in events
        if event.text or event.delta
    ]
    if not candidate_output_chunks:
        failures.append("Codex stream did not produce any candidate output_chunk payloads.")

    return {
        "stream_path": str(stream_path),
        "stream_exists": True,
        "supports_persisted_stream": True,
        "event_count": len(events),
        "seqs": seqs,
        "kinds": [event.kind for event in events],
        "candidate_output_chunks": candidate_output_chunks,
    }


def _opencode_stream_gap(run_dir: Path, failures: list[str]) -> dict[str, Any]:
    stream_path = run_dir / STREAM_EVENTS_FILENAME
    sdk_turn_path = run_dir / "sdk_turn.json"
    sdk_turn_payload = json.loads(sdk_turn_path.read_text(encoding="utf-8")) if sdk_turn_path.exists() else None
    if stream_path.exists():
        failures.append("OpenCode stream smoke unexpectedly produced stream.events.jsonl; update the gap assumption.")
    if not sdk_turn_payload:
        failures.append("OpenCode stream smoke is missing sdk_turn.json.")
    assistant_parts = []
    if isinstance(sdk_turn_payload, dict):
        assistant_parts = list(sdk_turn_payload.get("assistant_parts") or [])
    return {
        "stream_path": str(stream_path),
        "stream_exists": stream_path.exists(),
        "supports_persisted_stream": False,
        "sdk_turn_path": str(sdk_turn_path),
        "assistant_part_count": len(assistant_parts),
        "gap": {
            "kind": "missing_same_layer_stream_evidence",
            "summary": "OpenCode SDK current runtime only persists final sdk_turn.json assistant payload, not seq-based stream events.",
            "recorded": True,
        },
    }


def run_sdk_stream_smoke(
    *,
    backend: str,
    output_dir: Path,
    run_name: str,
    filename: str,
    file_text: str,
    opencode_command: str,
    codex_command: str,
) -> dict[str, Any]:
    runtime_result = run_runtime_smoke(
        backend=backend,
        output_dir=output_dir,
        run_name=run_name,
        filename=filename,
        file_text=file_text,
        opencode_command=opencode_command,
        codex_command=codex_command,
    )
    failures = list(runtime_result.get("failures", []))
    run_dir = _run_dir_from_runtime_result(runtime_result)

    if backend == "codex":
        stream_record = _codex_stream_contract(run_dir, failures)
    else:
        stream_record = _opencode_stream_gap(run_dir, failures)

    smoke_result = {
        "success": not failures,
        "backend": backend,
        "run_name": run_name,
        "runtime_result_path": runtime_result["smoke_result_path"],
        "result_path": runtime_result["result_path"],
        "cleanup": runtime_result["cleanup"],
        "stream": stream_record,
        "failures": failures,
    }
    smoke_result_path = output_dir / run_name / "stream_smoke_result.json"
    smoke_result["smoke_result_path"] = str(smoke_result_path)
    _write_json(smoke_result_path, smoke_result)
    return smoke_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a standalone sdk-first stream smoke for OpenCode or Codex.")
    parser.add_argument("--backend", choices=["opencode", "codex"], required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    parser.add_argument("--filename", default=DEFAULT_FILENAME)
    parser.add_argument("--file-text", default=DEFAULT_FILE_TEXT)
    parser.add_argument("--opencode-command", default="")
    parser.add_argument("--codex-command", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"{args.backend}-sdk-stream-smoke-{_timestamp_slug()}"
    result = run_sdk_stream_smoke(
        backend=args.backend,
        output_dir=Path(args.output_dir),
        run_name=run_name,
        filename=args.filename,
        file_text=args.file_text,
        opencode_command=args.opencode_command,
        codex_command=args.codex_command,
    )
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"], "backend": result["backend"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
