from __future__ import annotations

from mail_runner.artifact_resolver import write_artifact_index
from mail_runner.external_delivery_index import write_external_delivery_index
from mail_runner.file_surface import write_artifact_upload_success_binding
from mail_runner.models import ExternalDelivery, RunArtifact, RunResult
from mail_runner.pc_control_plane_projection import project_artifact_manifest
from mail_runner.status import BACKEND_CODEX, RUN_STATUS_SUCCESS


def _result() -> RunResult:
    return RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_CODEX,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-25T12:00:00",
        finished_at="2026-03-25T12:00:01",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir="runs/task_001/artifacts",
        changed_files=[],
        tests_passed=True,
        backend_transport="sdk",
    )


def test_project_artifact_manifest_uses_real_index_and_latest_uploaded_binding(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    preview_path = artifacts_dir / "preview.png"
    report_path = artifacts_dir / "report.md"
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\nprojected-preview")
    report_path.write_text("# projected report\n", encoding="utf-8")
    artifacts = [
        RunArtifact(
            artifact_id="artifact-preview",
            path=str(preview_path),
            name="preview.png",
            kind="image",
            content_type="image/png",
            source="manifest",
            inline_preview=True,
            caption="Preview",
        ),
        RunArtifact(
            artifact_id="artifact-report",
            path=str(report_path),
            name="report.md",
            kind="file",
            content_type="text/markdown",
            source="manifest",
        ),
    ]
    result = _result()

    index_path = write_artifact_index(task_root, result, artifacts, [])
    assert index_path is not None
    write_artifact_upload_success_binding(
        task_root,
        result,
        artifacts[0],
        role="artifact_delivery",
        file_id="file_preview_001",
        metadata_url="/v1/files/file_preview_001",
        download_url="/v1/files/file_preview_001/content",
        uploaded_at="2026-03-25T12:00:02",
        trace_id="trace_artifact_001",
    )
    write_artifact_upload_success_binding(
        task_root,
        result,
        artifacts[0],
        role="artifact_delivery",
        file_id="file_preview_002",
        metadata_url="/v1/files/file_preview_002",
        download_url="/v1/files/file_preview_002/content",
        uploaded_at="2026-03-25T12:00:03",
        trace_id="trace_artifact_001",
    )

    manifest = project_artifact_manifest(task_root, result=result)

    assert manifest is not None
    assert manifest["artifacts_root"] == "runs/task_001/artifacts"
    assert manifest["source"] == "directory_fallback"
    assert manifest["artifacts"] == [
        {
            "artifact_id": "artifact-preview",
            "kind": "image",
            "name": "preview.png",
            "content_type": "image/png",
            "size": preview_path.stat().st_size,
            "download_ref": "/v1/files/file_preview_002/content",
            "download_ref_source": "artifact_file_binding_index",
        },
        {
            "artifact_id": "artifact-report",
            "kind": "file",
            "name": "report.md",
            "content_type": "text/markdown",
            "size": report_path.stat().st_size,
            "download_ref": None,
            "download_ref_source": None,
        },
    ]


def test_project_artifact_manifest_skips_missing_file_entries_from_real_index(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    preview_path = artifacts_dir / "preview.png"
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    artifacts = [
        RunArtifact(
            artifact_id="artifact-preview",
            path=str(preview_path),
            name="preview.png",
            kind="image",
            content_type="image/png",
            source="manifest",
        ),
        RunArtifact(
            artifact_id="artifact-missing",
            path=str(artifacts_dir / "missing.txt"),
            name="missing.txt",
            kind="file",
            content_type="text/plain",
            source="manifest",
        ),
    ]

    index_path = write_artifact_index(task_root, _result(), artifacts, [])
    assert index_path is not None

    manifest = project_artifact_manifest(task_root, result=_result())

    assert manifest is not None
    assert [item["artifact_id"] for item in manifest["artifacts"]] == ["artifact-preview"]
    assert manifest["artifacts"][0]["download_ref"] is None


def test_project_artifact_manifest_prefers_live_external_delivery_index_over_binding(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    preview_path = artifacts_dir / "preview.png"
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\nprojected-preview")
    artifact = RunArtifact(
        artifact_id="artifact-preview",
        path=str(preview_path),
        name="preview.png",
        kind="image",
        content_type="image/png",
        source="manifest",
        inline_preview=True,
    )
    result = _result()

    index_path = write_artifact_index(task_root, result, [artifact], [])
    assert index_path is not None
    write_artifact_upload_success_binding(
        task_root,
        result,
        artifact,
        role="artifact_delivery",
        file_id="file_preview_001",
        metadata_url="/v1/files/file_preview_001",
        download_url="/v1/files/file_preview_001/content",
        uploaded_at="2026-03-25T12:00:02",
        trace_id="trace_artifact_001",
    )
    write_external_delivery_index(
        task_root,
        result,
        artifacts=[artifact],
        deliveries=[
            ExternalDelivery(
                artifact_id="artifact-preview",
                name="preview.png",
                provider="file_surface",
                url="https://relay.example/v1/files/file_preview_001/content",
                expires_at="2026-03-25T12:00:05",
                object_key="file_preview_001",
                size_bytes=preview_path.stat().st_size,
                content_type="image/png",
                bucket="relay-file-surface",
                path=str(preview_path),
            )
        ],
        recorded_at="2026-03-25T12:00:03",
    )

    manifest = project_artifact_manifest(task_root, result=result)

    assert manifest is not None
    assert manifest["artifacts"] == [
        {
            "artifact_id": "artifact-preview",
            "kind": "image",
            "name": "preview.png",
            "content_type": "image/png",
            "size": preview_path.stat().st_size,
            "download_ref": "https://relay.example/v1/files/file_preview_001/content",
            "download_ref_source": "external_delivery_index.file_surface",
        }
    ]


def test_project_artifact_manifest_projects_cos_external_delivery_index(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    report_path = artifacts_dir / "report.md"
    report_path.write_text("# projected report\n", encoding="utf-8")
    artifact = RunArtifact(
        artifact_id="artifact-report",
        path=str(report_path),
        name="report.md",
        kind="file",
        content_type="text/markdown",
        source="manifest",
    )
    result = _result()

    index_path = write_artifact_index(task_root, result, [artifact], [])
    assert index_path is not None
    write_external_delivery_index(
        task_root,
        result,
        artifacts=[artifact],
        deliveries=[
            ExternalDelivery(
                artifact_id="artifact-report",
                name="report.md",
                provider="cos",
                url="https://cos.example/mail-runner/thread_001/task_001/report.md",
                expires_at="2026-03-26T12:00:05",
                object_key="mail-runner/thread_001/task_001/report.md",
                size_bytes=report_path.stat().st_size,
                content_type="text/markdown",
                bucket="mailbot-bucket",
                path=str(report_path),
            )
        ],
        recorded_at="2026-03-25T12:00:03",
    )

    manifest = project_artifact_manifest(task_root, result=result)

    assert manifest is not None
    assert manifest["artifacts"] == [
        {
            "artifact_id": "artifact-report",
            "kind": "file",
            "name": "report.md",
            "content_type": "text/markdown",
            "size": report_path.stat().st_size,
            "download_ref": "https://cos.example/mail-runner/thread_001/task_001/report.md",
            "download_ref_source": "external_delivery_index.cos",
        }
    ]
