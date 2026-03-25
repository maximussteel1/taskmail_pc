import json
from pathlib import Path

from mail_runner.external_delivery_index import EXTERNAL_DELIVERY_INDEX_SCHEMA
from mail_runner.external_delivery_window import build_external_delivery_window_report
from mail_runner.file_surface import SINGLE_FILE_UPLOAD_LIMIT_BYTES


def _write_run(
    task_root: Path,
    *,
    thread_id: str,
    task_id: str,
    backend: str,
    finished_at: str,
    artifact_name: str,
    provider: str,
    size_bytes: int,
    include_delivery_size: bool = True,
) -> None:
    run_dir = task_root / thread_id / "runs" / task_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "thread_id": thread_id,
                "task_id": task_id,
                "backend": backend,
                "status": "success",
                "finished_at": finished_at,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    delivery_payload = {
        "status": "delivered",
        "recorded_at": finished_at,
        "provider": provider,
        "url": f"https://example.test/{task_id}/{artifact_name}",
    }
    if include_delivery_size:
        delivery_payload["size_bytes"] = size_bytes
    (artifacts_dir / "external_delivery_index.json").write_text(
        json.dumps(
            {
                "schemaVersion": EXTERNAL_DELIVERY_INDEX_SCHEMA,
                "task_id": task_id,
                "thread_id": thread_id,
                "artifacts_root": f"runs/{task_id}/artifacts",
                "generated_at": finished_at,
                "items": [
                    {
                        "artifact_id": f"artifact-{artifact_name}",
                        "local_path": str(artifacts_dir / artifact_name),
                        "name": artifact_name,
                        "kind": "file",
                        "content_type": "application/octet-stream",
                        "byte_size": size_bytes,
                        "deliveries": [delivery_payload],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_build_external_delivery_window_report_flags_owner_lane_mismatches(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _write_run(
        task_root,
        thread_id="thread_001",
        task_id="task_001",
        backend="opencode",
        finished_at="2026-03-25T21:01:00",
        artifact_name="small.bin",
        provider="file_surface",
        size_bytes=1024,
    )
    _write_run(
        task_root,
        thread_id="thread_002",
        task_id="task_002",
        backend="codex",
        finished_at="2026-03-25T21:02:00",
        artifact_name="large.bin",
        provider="cos",
        size_bytes=SINGLE_FILE_UPLOAD_LIMIT_BYTES + 1,
    )
    _write_run(
        task_root,
        thread_id="thread_003",
        task_id="task_003",
        backend="opencode",
        finished_at="2026-03-25T21:03:00",
        artifact_name="unexpected.bin",
        provider="cos",
        size_bytes=2048,
    )

    report = build_external_delivery_window_report(
        task_root,
        owner_preference="file_surface",
    )

    assert report["scanned_runs"] == 3
    assert report["reported_runs"] == 3
    assert report["provider_counts"] == {"cos": 2, "file_surface": 1}
    assert report["expectation_mismatch_count"] == 1
    mismatch = report["expectation_mismatches"][0]
    assert mismatch["thread_id"] == "thread_003"
    assert mismatch["expected_provider"] == "file_surface"
    assert [run["thread_id"] for run in report["runs"]] == ["thread_003", "thread_002", "thread_001"]
    newest_delivery = report["runs"][0]["deliveries"][0]
    assert newest_delivery["matches_expectation"] is False
    assert newest_delivery["expected_provider"] == "file_surface"
    oversize_delivery = report["runs"][1]["deliveries"][0]
    assert oversize_delivery["oversize_for_file_surface"] is True
    assert oversize_delivery["matches_expectation"] is True


def test_build_external_delivery_window_report_applies_limit_and_falls_back_to_item_size(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _write_run(
        task_root,
        thread_id="thread_010",
        task_id="task_010",
        backend="codex",
        finished_at="2026-03-25T21:10:00",
        artifact_name="a.bin",
        provider="file_surface",
        size_bytes=4096,
        include_delivery_size=False,
    )
    _write_run(
        task_root,
        thread_id="thread_011",
        task_id="task_011",
        backend="codex",
        finished_at="2026-03-25T21:11:00",
        artifact_name="b.bin",
        provider="file_surface",
        size_bytes=8192,
    )

    report = build_external_delivery_window_report(
        task_root,
        limit_runs=1,
        owner_preference="auto",
    )

    assert report["scanned_runs"] == 2
    assert report["reported_runs"] == 1
    assert report["expectation_mismatch_count"] == 0
    assert report["runs"][0]["thread_id"] == "thread_011"
    assert report["runs"][0]["deliveries"][0]["size_bytes"] == 8192
