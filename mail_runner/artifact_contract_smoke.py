"""Standalone fixture smoke for current artifact truth and manifest projection."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifact_resolver import resolve_run_artifacts, write_artifact_index
from .file_surface import ARTIFACT_FILE_BINDING_INDEX_FILENAME, write_artifact_upload_success_binding
from .models import RunArtifact, RunResult, ThreadState
from .status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS, THREAD_STATUS_DONE

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_artifact_contract_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="artifact smoke",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        created_at="2026-03-25T00:00:00",
        updated_at="2026-03-25T00:00:01",
    )


def _result() -> RunResult:
    return RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-25T00:00:00",
        finished_at="2026-03-25T00:00:01",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir="runs/task_001/artifacts",
        changed_files=[],
        tests_passed=True,
        error_message=None,
    )


def _artifact_manifest_candidate(
    *,
    artifacts: list[RunArtifact],
    binding_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    binding_items = {}
    if isinstance(binding_payload, dict):
        for item in binding_payload.get("items", []):
            if isinstance(item, dict):
                binding_items[str(item.get("artifact_id") or "")] = item

    projected: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = Path(artifact.path)
        binding_item = binding_items.get(artifact.artifact_id)
        latest_uploaded = None
        if isinstance(binding_item, dict):
            bindings = binding_item.get("bindings", [])
            if isinstance(bindings, list):
                uploaded = [item for item in bindings if isinstance(item, dict) and item.get("status") == "uploaded"]
                latest_uploaded = uploaded[-1] if uploaded else None
        projected.append(
            {
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind,
                "name": artifact.name,
                "content_type": artifact.content_type,
                "size": path.stat().st_size,
                "download_ref": latest_uploaded.get("download_url") if isinstance(latest_uploaded, dict) else None,
                "download_ref_source": "artifact_file_binding_index" if latest_uploaded else None,
            }
        )
    return projected


def run_artifact_contract_smoke(*, output_dir: Path, run_name: str) -> dict[str, Any]:
    run_root = output_dir / run_name
    task_root = run_root / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    preview_path = run_root / "preview.png"
    report_path = run_root / "report.md"
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    report_path.write_text("# artifact smoke\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "items": [
            {
                "path": str(preview_path),
                "name": "preview.png",
                "mime": "image/png",
                "attach": True,
                "inline": True,
                "caption": "Preview",
            },
            {
                "path": str(report_path),
                "name": "report.md",
                "mime": "text/markdown",
                "attach": True,
                "inline": False,
            },
            {
                "path": str(run_root / "missing.txt"),
                "name": "missing.txt",
                "mime": "text/plain",
                "attach": True,
                "inline": False,
            },
        ],
    }
    manifest_path = artifacts_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    state = _state()
    result = _result()
    artifacts, skipped = resolve_run_artifacts(task_root, state, result)
    index_path = write_artifact_index(task_root, result, artifacts, skipped)
    binding_path = write_artifact_upload_success_binding(
        task_root,
        result,
        artifacts[0],
        role="artifact_delivery",
        file_id="file_preview_001",
        metadata_url="/v1/files/file_preview_001",
        download_url="/v1/files/file_preview_001/content",
        uploaded_at="2026-03-25T00:00:02",
        trace_id="trace-artifact-smoke",
    )
    binding_payload = json.loads(binding_path.read_text(encoding="utf-8"))
    candidate_manifest = _artifact_manifest_candidate(artifacts=artifacts, binding_payload=binding_payload)

    failures: list[str] = []
    if index_path is None or not index_path.exists():
        failures.append("artifact_index.json was not written.")
    if len(artifacts) != 2:
        failures.append(f"Expected 2 resolved artifacts, got {len(artifacts)}.")
    if [item.artifact_id for item in artifacts] != ["artifact-preview", "artifact-report"]:
        failures.append("Artifact IDs did not match the expected canonical IDs.")
    if [item.kind for item in artifacts] != ["image", "file"]:
        failures.append("Artifact kinds did not match the expected projection.")
    if len(skipped) != 1 or "does not exist" not in skipped[0]:
        failures.append("Missing manifest item did not surface as a skipped artifact message.")
    if not binding_path.exists():
        failures.append("artifact_file_binding_index.json was not written.")
    if candidate_manifest[0]["download_ref"] != "/v1/files/file_preview_001/content":
        failures.append("download_ref projection for uploaded artifact is missing.")
    if candidate_manifest[1]["download_ref"] is not None:
        failures.append("Non-uploaded artifact unexpectedly has a download_ref.")

    smoke_result = {
        "success": not failures,
        "run_name": run_name,
        "task_root": str(task_root),
        "manifest_path": str(manifest_path),
        "artifact_index_path": str(index_path) if index_path is not None else None,
        "binding_index_path": str(binding_path),
        "resolved_artifacts": [asdict(item) for item in artifacts],
        "skipped": skipped,
        "candidate_artifact_manifest": candidate_manifest,
        "gaps": [
            {
                "kind": "download_ref_not_universal",
                "summary": "Current local artifact truth only gets download_ref after a file-surface binding exists; local-only artifacts remain unresolved.",
                "recorded": True,
            }
        ],
        "cleanup": {
            "required": False,
            "cleanup_ok": True,
            "reason": "fixture smoke; no external process or listener is started",
        },
        "failures": failures,
    }
    smoke_result_path = run_root / "smoke_result.json"
    smoke_result["smoke_result_path"] = str(smoke_result_path)
    _write_json(smoke_result_path, smoke_result)
    return smoke_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixture smoke for current artifact truth and manifest projection.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"artifact-contract-smoke-{_timestamp_slug()}"
    result = run_artifact_contract_smoke(output_dir=Path(args.output_dir), run_name=run_name)
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
