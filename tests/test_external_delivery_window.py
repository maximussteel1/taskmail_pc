import json
from dataclasses import asdict
from pathlib import Path

from mail_runner.artifact_resolver import write_artifact_index
from mail_runner.external_delivery_index import EXTERNAL_DELIVERY_INDEX_SCHEMA, write_external_delivery_index
from mail_runner.models import ExternalDelivery, RunArtifact, RunResult
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
    artifact_kind: str = "file",
    include_delivery_size: bool = True,
    include_artifact_projection: bool = True,
) -> None:
    run_dir = task_root / thread_id / "runs" / task_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / artifact_name
    artifact_path.write_bytes(f"{task_id}:{artifact_name}".encode("utf-8"))
    result = RunResult(
        task_id=task_id,
        thread_id=thread_id,
        backend=backend,
        status="success",
        exit_code=0,
        started_at=finished_at,
        finished_at=finished_at,
        stdout_file=f"runs/{task_id}/stdout.log",
        stderr_file=f"runs/{task_id}/stderr.log",
        summary_file=f"runs/{task_id}/summary.md",
        artifacts_dir=f"runs/{task_id}/artifacts",
        changed_files=[],
        tests_passed=True,
    )
    (run_dir / "result.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact = RunArtifact(
        artifact_id=f"artifact-{artifact_name}",
        path=str(artifact_path),
        name=artifact_name,
        kind=artifact_kind,  # type: ignore[arg-type]
        content_type="application/octet-stream",
        source="directory_fallback",
    )
    if include_artifact_projection:
        index_path = write_artifact_index(task_root, result, [artifact], [])
        assert index_path is not None
    delivery_payload = {
        "status": "delivered",
        "recorded_at": finished_at,
        "provider": provider,
        "url": f"https://example.test/{task_id}/{artifact_name}",
    }
    if include_delivery_size:
        delivery_payload["size_bytes"] = size_bytes
    index_path = write_external_delivery_index(
        task_root,
        result,
        artifacts=[artifact],
        deliveries=[
            ExternalDelivery(
                artifact_id=artifact.artifact_id,
                name=artifact_name,
                provider=provider,  # type: ignore[arg-type]
                url=delivery_payload["url"],
                expires_at="2026-03-30T00:00:00",
                object_key=f"{task_id}/{artifact_name}",
                size_bytes=size_bytes,
                content_type="application/octet-stream",
                bucket="relay-file-surface" if provider == "file_surface" else "mailbot-bucket",
                path=str(artifact_path),
            )
        ],
        recorded_at=finished_at,
    )
    assert index_path is not None
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == EXTERNAL_DELIVERY_INDEX_SCHEMA
    if not include_delivery_size:
        payload["items"][0]["deliveries"][0].pop("size_bytes", None)
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    assert report["artifact_kind_counts"] == {"file": 3}
    assert report["expectation_mismatch_count"] == 1
    assert report["artifact_manifest_missing_count"] == 0
    assert report["legacy_auto_requested"] is False
    assert report["cos_delivery_count"] == 2
    assert report["artifact_manifest_download_ref_source_counts"] == {
        "external_delivery_index.cos": 2,
        "external_delivery_index.file_surface": 1,
    }
    assert report["artifact_manifest_download_ref_source_mismatch_count"] == 0
    assert report["window_ready"] is False
    assert report["cos_decommission_candidate"] is False
    assert report["cos_decommission_checks"]["window_has_no_cos_deliveries"] is False
    assert report["cos_decommission_checks"]["cos_deliveries_are_oversize_only"] is False
    assert "COS deliveries are still present in the requested observation window" in report["cos_decommission_failures"]
    mismatch = report["expectation_mismatches"][0]
    assert mismatch["thread_id"] == "thread_003"
    assert mismatch["expected_provider"] == "file_surface"
    assert [run["thread_id"] for run in report["runs"]] == ["thread_003", "thread_002", "thread_001"]
    newest_delivery = report["runs"][0]["deliveries"][0]
    assert newest_delivery["matches_expectation"] is False
    assert newest_delivery["expected_provider"] == "file_surface"
    assert newest_delivery["projected_download_ref_source"] == "external_delivery_index.cos"
    oversize_delivery = report["runs"][1]["deliveries"][0]
    assert oversize_delivery["oversize_for_file_surface"] is True
    assert oversize_delivery["matches_expectation"] is True
    assert oversize_delivery["projected_download_ref_source_matches_expectation"] is True


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
    assert report["owner_preference_input"] == "auto"
    assert report["legacy_auto_requested"] is True
    assert report["window_ready"] is None
    assert report["cos_decommission_candidate"] is False


def test_build_external_delivery_window_report_marks_window_ready_when_projection_and_provider_align(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _write_run(
        task_root,
        thread_id="thread_101",
        task_id="task_101",
        backend="codex",
        finished_at="2026-03-26T08:00:00",
        artifact_name="small.bin",
        provider="file_surface",
        size_bytes=4096,
    )
    _write_run(
        task_root,
        thread_id="thread_102",
        task_id="task_102",
        backend="opencode",
        finished_at="2026-03-26T08:01:00",
        artifact_name="large.bin",
        provider="cos",
        size_bytes=SINGLE_FILE_UPLOAD_LIMIT_BYTES + 8,
    )

    report = build_external_delivery_window_report(
        task_root,
        owner_preference="file_surface",
    )

    assert report["expectation_mismatch_count"] == 0
    assert report["unsupported_kind_count"] == 0
    assert report["artifact_manifest_missing_count"] == 0
    assert report["artifact_manifest_download_ref_source_mismatch_count"] == 0
    assert report["window_checks"]["owner_lane_matches_expectation"] is True
    assert report["window_ready"] is True
    assert report["cos_decommission_candidate"] is False
    assert report["cos_decommission_checks"]["cos_deliveries_are_oversize_only"] is True
    assert report["cos_decommission_checks"]["window_has_no_cos_deliveries"] is False
    assert "COS deliveries are still present in the requested observation window" in report["cos_decommission_failures"]


def test_build_external_delivery_window_report_flags_missing_artifact_manifest_projection(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    _write_run(
        task_root,
        thread_id="thread_201",
        task_id="task_201",
        backend="codex",
        finished_at="2026-03-26T09:00:00",
        artifact_name="small.bin",
        provider="file_surface",
        size_bytes=1024,
        include_artifact_projection=False,
    )

    report = build_external_delivery_window_report(
        task_root,
        owner_preference="file_surface",
    )

    assert report["artifact_manifest_missing_count"] == 1
    assert report["artifact_manifest_download_ref_source_mismatch_count"] == 1
    assert report["window_ready"] is False
    mismatch = report["artifact_manifest_download_ref_source_mismatches"][0]
    assert mismatch["projected_manifest_item_present"] is False
    assert report["cos_decommission_candidate"] is False


def test_build_external_delivery_window_report_marks_cos_decommission_candidate_when_clean_window_has_no_cos(
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "tasks"
    _write_run(
        task_root,
        thread_id="thread_301",
        task_id="task_301",
        backend="opencode",
        finished_at="2026-03-26T10:00:00",
        artifact_name="small-a.bin",
        provider="file_surface",
        size_bytes=2048,
    )
    _write_run(
        task_root,
        thread_id="thread_302",
        task_id="task_302",
        backend="codex",
        finished_at="2026-03-26T10:01:00",
        artifact_name="small-b.bin",
        provider="file_surface",
        size_bytes=4096,
    )

    report = build_external_delivery_window_report(
        task_root,
        owner_preference="file_surface",
    )

    assert report["window_ready"] is True
    assert report["cos_delivery_count"] == 0
    assert report["cos_decommission_checks"]["owner_preference_is_file_surface"] is True
    assert report["cos_decommission_checks"]["legacy_auto_not_requested"] is True
    assert report["cos_decommission_checks"]["window_has_no_cos_deliveries"] is True
    assert report["cos_decommission_checks"]["cos_deliveries_are_oversize_only"] is True
    assert report["cos_decommission_candidate"] is True
    assert report["cos_decommission_failures"] == []
