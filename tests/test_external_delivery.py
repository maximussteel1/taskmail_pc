"""External delivery tests for oversized artifacts."""

from __future__ import annotations

import json
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

from mail_runner.config import AppConfig
from mail_runner.external_delivery_index import EXTERNAL_DELIVERY_INDEX_FILENAME
from mail_runner.file_surface import ARTIFACT_FILE_BINDING_INDEX_FILENAME
from mail_runner.external_delivery import _build_cos_client, prepare_external_deliveries
from mail_runner.models import OutgoingAttachment, RunArtifact, RunResult
from mail_runner.relay_server.app import build_http_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.session_store import InMemorySessionStore
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


def test_build_cos_client_disables_environment_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCosConfig:
        def __init__(self, **kwargs):
            captured["config_kwargs"] = kwargs

    class FakeCosS3Client:
        def __init__(self, config, session=None):
            captured["config"] = config
            captured["session"] = session

    monkeypatch.setitem(
        sys.modules,
        "qcloud_cos",
        SimpleNamespace(CosConfig=FakeCosConfig, CosS3Client=FakeCosS3Client),
    )

    _build_cos_client(
        {
            "region": "ap-shanghai",
            "bucket": "mailbot-1412015279",
            "secret_id": "secret-id",
            "secret_key": "secret-key",
            "object_prefix": "mail-runner",
        }
    )

    session = captured["session"]
    assert session is not None
    assert session.trust_env is False
    assert captured["config_kwargs"]["Proxies"] == {}
    assert "http://" in session.adapters
    assert "https://" in session.adapters


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
        task_root=tmp_path,
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
    sidecar_path = tmp_path / "thread_001" / "runs" / "task_001" / "artifacts" / EXTERNAL_DELIVERY_INDEX_FILENAME
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    delivery = payload["items"][0]["deliveries"][0]
    assert delivery["status"] == "delivered"
    assert delivery["provider"] == "cos"
    assert delivery["url"] == "https://cos.example/mail-runner/thread_001/task_001/preview.png"


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


def test_prepare_external_deliveries_externalizes_oversized_artifact_to_relay_file_surface(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "preview.png"
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nrelay-surface")
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
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr("mail_runner.external_delivery._load_local_cos_config", lambda: {})
        host, port = server.server_address[:2]
        effective_artifacts, remaining_attachments, deliveries, notices = prepare_external_deliveries(
            AppConfig(
                outbound_transport="relay",
                relay_url=f"ws://{host}:{port}/relay",
                relay_transport_token="relay-secret",
                relay_timeout_seconds=5,
                external_delivery_threshold_mb=0,
            ),
            artifacts=[artifact],
            attachments=[attachment],
            result=_result(),
            task_root=tmp_path / "tasks",
        )

        assert remaining_attachments == []
        assert notices == []
        assert len(deliveries) == 1
        assert deliveries[0].provider == "file_surface"
        assert deliveries[0].url.startswith(f"http://{host}:{port}/v1/files/")
        assert deliveries[0].object_key.startswith("file_")
        assert effective_artifacts[0].inline_preview is False

        sidecar_path = tmp_path / "tasks" / "thread_001" / "runs" / "task_001" / "artifacts" / ARTIFACT_FILE_BINDING_INDEX_FILENAME
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        binding = payload["items"][0]["bindings"][0]
        assert binding["status"] == "uploaded"
        assert binding["file_id"] == deliveries[0].object_key
        delivery_sidecar_path = (
            tmp_path / "tasks" / "thread_001" / "runs" / "task_001" / "artifacts" / EXTERNAL_DELIVERY_INDEX_FILENAME
        )
        delivery_payload = json.loads(delivery_sidecar_path.read_text(encoding="utf-8"))
        delivery = delivery_payload["items"][0]["deliveries"][0]
        assert delivery["status"] == "delivered"
        assert delivery["provider"] == "file_surface"
        assert delivery["url"] == deliveries[0].url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_external_deliveries_prefers_file_surface_when_configured_for_cutover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "preview.png"
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nprefer-file-surface")
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
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = FakeCosClient()
    try:
        monkeypatch.setattr("mail_runner.external_delivery._load_local_cos_config", lambda: {})
        host, port = server.server_address[:2]
        effective_artifacts, remaining_attachments, deliveries, notices = prepare_external_deliveries(
            AppConfig(
                outbound_transport="relay",
                relay_url=f"ws://{host}:{port}/relay",
                relay_transport_token="relay-secret",
                relay_timeout_seconds=5,
                external_delivery_threshold_mb=0,
                external_delivery_backend_preference="file_surface",
                cos_region="ap-shanghai",
                cos_bucket="mailbot-1412015279",
                cos_secret_id="secret-id",
                cos_secret_key="secret-key",
                cos_object_prefix="mail-runner",
            ),
            artifacts=[artifact],
            attachments=[attachment],
            result=_result(),
            task_root=tmp_path / "tasks",
            cos_client_factory=lambda settings: client,
        )

        assert remaining_attachments == []
        assert notices == []
        assert len(deliveries) == 1
        assert deliveries[0].provider == "file_surface"
        assert deliveries[0].url.startswith(f"http://{host}:{port}/v1/files/")
        assert effective_artifacts[0].inline_preview is False
        assert client.upload_calls == []
        assert client.download_calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_external_deliveries_keeps_cos_for_oversized_artifact_during_file_surface_cutover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "oversized.apk"
    artifact_path.write_bytes(b"oversized-file-surface-cutover")
    artifact = RunArtifact(
        artifact_id="artifact-apk",
        path=str(artifact_path),
        name="oversized.apk",
        kind="file",
        content_type="application/octet-stream",
        source="directory_fallback",
        attach=True,
        inline_preview=False,
        caption="Debug APK",
    )
    attachment = OutgoingAttachment(
        path=str(artifact_path),
        name="oversized.apk",
        content_type="application/octet-stream",
        attach=True,
        inline=False,
        caption="Debug APK",
    )
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = FakeCosClient()
    try:
        monkeypatch.setattr("mail_runner.external_delivery._load_local_cos_config", lambda: {})
        monkeypatch.setattr("mail_runner.external_delivery.SINGLE_FILE_UPLOAD_LIMIT_BYTES", 4)
        host, port = server.server_address[:2]
        effective_artifacts, remaining_attachments, deliveries, notices = prepare_external_deliveries(
            AppConfig(
                outbound_transport="relay",
                relay_url=f"ws://{host}:{port}/relay",
                relay_transport_token="relay-secret",
                relay_timeout_seconds=5,
                external_delivery_threshold_mb=0,
                external_delivery_backend_preference="file_surface",
                cos_region="ap-shanghai",
                cos_bucket="mailbot-1412015279",
                cos_secret_id="secret-id",
                cos_secret_key="secret-key",
                cos_object_prefix="mail-runner",
            ),
            artifacts=[artifact],
            attachments=[attachment],
            result=_result(),
            task_root=tmp_path / "tasks",
            cos_client_factory=lambda settings: client,
        )

        assert remaining_attachments == []
        assert notices
        assert "rename the downloaded file back to oversized.apk" in notices[0]
        assert len(deliveries) == 1
        assert deliveries[0].provider == "cos"
        assert deliveries[0].object_key.endswith("oversized.apk.bin")
        assert effective_artifacts[0].inline_preview is False
        assert client.upload_calls != []
        assert client.download_calls != []

        binding_path = tmp_path / "tasks" / "thread_001" / "runs" / "task_001" / "artifacts" / ARTIFACT_FILE_BINDING_INDEX_FILENAME
        assert binding_path.exists() is False
        delivery_sidecar_path = (
            tmp_path / "tasks" / "thread_001" / "runs" / "task_001" / "artifacts" / EXTERNAL_DELIVERY_INDEX_FILENAME
        )
        delivery_payload = json.loads(delivery_sidecar_path.read_text(encoding="utf-8"))
        delivery = delivery_payload["items"][0]["deliveries"][0]
        assert delivery["provider"] == "cos"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepare_external_deliveries_records_relay_file_surface_failure_without_attaching_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "report.txt"
    artifact_path.write_text("relay-surface failure", encoding="utf-8")
    artifact = RunArtifact(
        artifact_id="artifact-report",
        path=str(artifact_path),
        name="report.txt",
        kind="file",
        content_type="text/plain",
        source="directory_fallback",
        attach=True,
        inline_preview=False,
        caption="Run report",
    )
    attachment = OutgoingAttachment(
        path=str(artifact_path),
        name="report.txt",
        content_type="text/plain",
        attach=True,
        inline=False,
        caption="Run report",
    )
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr("mail_runner.external_delivery._load_local_cos_config", lambda: {})
        host, port = server.server_address[:2]
        effective_artifacts, remaining_attachments, deliveries, notices = prepare_external_deliveries(
            AppConfig(
                outbound_transport="relay",
                relay_url=f"ws://{host}:{port}/relay",
                relay_transport_token="wrong-secret",
                relay_timeout_seconds=5,
                external_delivery_threshold_mb=0,
            ),
            artifacts=[artifact],
            attachments=[attachment],
            result=_result(),
            task_root=tmp_path / "tasks",
        )

        assert remaining_attachments == []
        assert deliveries == []
        assert len(notices) == 1
        assert "transport token mismatch" in notices[0]
        assert effective_artifacts[0].inline_preview is False

        sidecar_path = tmp_path / "tasks" / "thread_001" / "runs" / "task_001" / "artifacts" / ARTIFACT_FILE_BINDING_INDEX_FILENAME
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        binding = payload["items"][0]["bindings"][0]
        assert binding["status"] == "failed"
        assert binding["error_code"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
