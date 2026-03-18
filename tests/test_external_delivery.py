"""External delivery tests for oversized artifacts."""

from __future__ import annotations

from pathlib import Path

from mail_runner.config import AppConfig
from mail_runner.external_delivery import prepare_external_deliveries
from mail_runner.models import OutgoingAttachment, RunArtifact, RunResult
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS


def _result() -> RunResult:
    return RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-17T18:00:00",
        finished_at="2026-03-17T18:01:00",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir="runs/task_001/artifacts",
        changed_files=[],
        tests_passed=True,
        error_message=None,
    )


class FakeCosClient:
    def __init__(self) -> None:
        self.upload_calls: list[dict[str, str]] = []
        self.download_calls: list[dict[str, str | int]] = []

    def upload_file(self, **kwargs):
        self.upload_calls.append(kwargs)
        return {"ETag": '"demo"'}

    def get_presigned_download_url(self, **kwargs):
        self.download_calls.append(kwargs)
        return f"https://cos.example/{kwargs['Key']}"


def test_prepare_external_deliveries_externalizes_oversized_artifact(tmp_path: Path) -> None:
    artifact_path = tmp_path / "preview.png"
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    artifact = RunArtifact(
        artifact_id="artifact-preview",
        path=str(artifact_path),
        name="preview.png",
        kind="image",
        content_type="image/png",
        source="directory_fallback",
        attach=True,
        inline_preview=True,
        caption="Preview image",
    )
    attachment = OutgoingAttachment(
        path=str(artifact_path),
        name="preview.png",
        content_type="image/png",
        attach=True,
        inline=True,
        caption="Preview image",
    )
    config = AppConfig(
        cos_region="ap-shanghai",
        cos_bucket="mailbot-1412015279",
        cos_secret_id="secret-id",
        cos_secret_key="secret-key",
        cos_object_prefix="mail-runner",
        external_delivery_threshold_mb=0,
        cos_presign_expire_seconds=600,
    )
    client = FakeCosClient()

    effective_artifacts, remaining_attachments, deliveries, notices = prepare_external_deliveries(
        config,
        artifacts=[artifact],
        attachments=[attachment],
        result=_result(),
        cos_client_factory=lambda settings: client,
    )

    assert remaining_attachments == []
    assert notices == []
    assert len(deliveries) == 1
    assert deliveries[0].provider == "cos"
    assert deliveries[0].bucket == "mailbot-1412015279"
    assert deliveries[0].object_key == "mail-runner/thread_001/task_001/preview.png"
    assert deliveries[0].url == "https://cos.example/mail-runner/thread_001/task_001/preview.png"
    assert effective_artifacts[0].inline_preview is False
    assert client.upload_calls[0]["Bucket"] == "mailbot-1412015279"
    assert client.download_calls[0]["Expired"] == 600


def test_prepare_external_deliveries_reports_upload_failure_without_attaching_file(tmp_path: Path) -> None:
    artifact_path = tmp_path / "app.apk"
    artifact_path.write_bytes(b"apk-payload")
    artifact = RunArtifact(
        artifact_id="artifact-apk",
        path=str(artifact_path),
        name="app.apk",
        kind="file",
        content_type="application/vnd.android.package-archive",
        source="directory_fallback",
        attach=True,
        inline_preview=False,
        caption="Debug APK",
    )
    attachment = OutgoingAttachment(
        path=str(artifact_path),
        name="app.apk",
        content_type="application/vnd.android.package-archive",
        attach=True,
        inline=False,
        caption="Debug APK",
    )
    config = AppConfig(
        cos_region="ap-shanghai",
        cos_bucket="mailbot-1412015279",
        cos_secret_id="secret-id",
        cos_secret_key="secret-key",
        external_delivery_threshold_mb=0,
    )

    class FailingCosClient:
        def upload_file(self, **kwargs):
            raise RuntimeError("upload denied")

    effective_artifacts, remaining_attachments, deliveries, notices = prepare_external_deliveries(
        config,
        artifacts=[artifact],
        attachments=[attachment],
        result=_result(),
        cos_client_factory=lambda settings: FailingCosClient(),
    )

    assert remaining_attachments == []
    assert deliveries == []
    assert len(notices) == 1
    assert "External delivery failed for app.apk: upload denied." in notices[0]
    assert "not attached to avoid mail size limits" in notices[0]
    assert effective_artifacts[0].inline_preview is False


def test_prepare_external_deliveries_renames_apk_object_key_for_cos_default_domain(tmp_path: Path) -> None:
    artifact_path = tmp_path / "app.apk"
    artifact_path.write_bytes(b"apk-payload")
    artifact = RunArtifact(
        artifact_id="artifact-apk",
        path=str(artifact_path),
        name="app.apk",
        kind="file",
        content_type="application/vnd.android.package-archive",
        source="directory_fallback",
        attach=True,
        inline_preview=False,
        caption="Debug APK",
    )
    attachment = OutgoingAttachment(
        path=str(artifact_path),
        name="app.apk",
        content_type="application/vnd.android.package-archive",
        attach=True,
        inline=False,
        caption="Debug APK",
    )
    config = AppConfig(
        cos_region="ap-shanghai",
        cos_bucket="mailbot-1412015279",
        cos_secret_id="secret-id",
        cos_secret_key="secret-key",
        cos_object_prefix="mail-runner",
        external_delivery_threshold_mb=0,
        cos_presign_expire_seconds=600,
    )
    client = FakeCosClient()

    _, remaining_attachments, deliveries, notices = prepare_external_deliveries(
        config,
        artifacts=[artifact],
        attachments=[attachment],
        result=_result(),
        cos_client_factory=lambda settings: client,
    )

    assert remaining_attachments == []
    assert deliveries[0].object_key == "mail-runner/thread_001/task_001/app.apk.bin"
    assert deliveries[0].url == "https://cos.example/mail-runner/thread_001/task_001/app.apk.bin"
    assert len(notices) == 1
    assert "blocks direct APK distribution" in notices[0]
    assert "rename the downloaded file back to app.apk" in notices[0]
