from __future__ import annotations

import json
from pathlib import Path
import threading

from mail_runner.file_surface import (
    ARTIFACT_FILE_BINDING_INDEX_FILENAME,
    build_file_surface_upload_metadata,
    derive_file_surface_url,
    FileSurfaceUploadError,
    SINGLE_FILE_UPLOAD_LIMIT_BYTES,
    upload_artifact_to_file_surface,
    write_artifact_upload_failure_binding,
    write_artifact_upload_success_binding,
)
from mail_runner.models import RunArtifact, RunResult
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
        started_at="2026-03-23T18:00:00",
        finished_at="2026-03-23T18:01:00",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir="runs/task_001/artifacts",
        changed_files=[],
        tests_passed=True,
        error_message=None,
    )


def _artifact(tmp_path: Path) -> RunArtifact:
    artifacts_dir = tmp_path / "tasks" / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / "chart.png"
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nchart")
    return RunArtifact(
        artifact_id="artifact-chart",
        path=str(artifact_path),
        name="chart.png",
        kind="image",
        content_type="image/png",
        source="manifest",
        attach=True,
        inline_preview=True,
        caption="Chart",
    )


def test_derive_file_surface_url_from_plaintext_relay_url() -> None:
    assert derive_file_surface_url("ws://127.0.0.1:8787/relay") == "http://127.0.0.1:8787/v1/files"


def test_build_file_surface_upload_metadata_uses_artifact_truth(tmp_path: Path) -> None:
    metadata = build_file_surface_upload_metadata(_artifact(tmp_path), role="attachment", trace_id="trace_001")

    assert metadata["artifact_id"] == "artifact-chart"
    assert metadata["name"] == "chart.png"
    assert metadata["kind"] == "image"
    assert metadata["role"] == "attachment"
    assert metadata["mime_type"] == "image/png"
    assert metadata["byte_size"] > 0
    assert len(metadata["sha256"]) == 64
    assert metadata["trace"] == {"trace_id": "trace_001"}


def test_file_surface_upload_error_builds_machine_readable_payload() -> None:
    error = FileSurfaceUploadError(
        status_code=413,
        error_code="payload_too_large",
        error_message="single_file_upload_limit_bytes exceeded",
        retryable=False,
        trace_id="trace_001",
        artifact_id="artifact-chart",
        max_bytes=SINGLE_FILE_UPLOAD_LIMIT_BYTES,
        observed_bytes=SINGLE_FILE_UPLOAD_LIMIT_BYTES + 9,
    )

    payload = error.to_response_payload()

    assert payload["status"] == "error"
    assert payload["error_code"] == "payload_too_large"
    assert payload["retryable"] is False
    assert payload["artifact_id"] == "artifact-chart"
    assert payload["max_bytes"] == SINGLE_FILE_UPLOAD_LIMIT_BYTES
    assert payload["observed_bytes"] == SINGLE_FILE_UPLOAD_LIMIT_BYTES + 9


def test_write_artifact_upload_success_binding_creates_sidecar(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    artifact = _artifact(tmp_path)

    sidecar_path = write_artifact_upload_success_binding(
        task_root,
        _result(),
        artifact,
        role="attachment",
        file_id="file_001",
        metadata_url="/v1/files/file_001",
        download_url="/v1/files/file_001/content",
        uploaded_at="2026-03-23T18:09:55",
        trace_id="trace_001",
        request_id="req_001",
        packet_id="pkt_001",
    )

    assert sidecar_path.name == ARTIFACT_FILE_BINDING_INDEX_FILENAME
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "taskmail-artifact-file-binding-index-v1"
    assert payload["task_id"] == "task_001"
    assert payload["thread_id"] == "thread_001"
    assert payload["artifacts_root"] == "runs/task_001/artifacts"
    assert payload["surface"] == "v1_files"
    assert payload["items"][0]["artifact_id"] == "artifact-chart"
    assert payload["items"][0]["name"] == "chart.png"
    assert payload["items"][0]["mime_type"] == "image/png"
    assert payload["items"][0]["byte_size"] > 0
    assert len(payload["items"][0]["sha256"]) == 64
    assert payload["items"][0]["bindings"] == [
        {
            "status": "uploaded",
            "uploaded_at": "2026-03-23T18:09:55",
            "role": "attachment",
            "file_id": "file_001",
            "metadata_url": "/v1/files/file_001",
            "download_url": "/v1/files/file_001/content",
            "trace_id": "trace_001",
            "request_id": "req_001",
            "packet_id": "pkt_001",
        }
    ]


def test_write_artifact_upload_failure_binding_appends_failure_without_overwriting_success(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    artifact = _artifact(tmp_path)
    write_artifact_upload_success_binding(
        task_root,
        _result(),
        artifact,
        role="attachment",
        file_id="file_001",
        metadata_url="/v1/files/file_001",
        download_url="/v1/files/file_001/content",
        uploaded_at="2026-03-23T18:09:55",
    )

    error = FileSurfaceUploadError(
        status_code=503,
        error_code="storage_unavailable",
        error_message="file store unavailable",
        retryable=True,
        trace_id="trace_002",
    )
    sidecar_path = write_artifact_upload_failure_binding(
        task_root,
        _result(),
        artifact,
        role="attachment",
        error=error,
        uploaded_at="2026-03-23T18:10:10",
        request_id="req_002",
    )

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    bindings = payload["items"][0]["bindings"]
    assert bindings[0]["status"] == "uploaded"
    assert bindings[1] == {
        "status": "failed",
        "uploaded_at": "2026-03-23T18:10:10",
        "role": "attachment",
        "status_code": 503,
        "error_code": "storage_unavailable",
        "error_message": "file store unavailable",
        "retryable": True,
        "trace_id": "trace_002",
        "request_id": "req_002",
    }


def test_write_artifact_upload_success_binding_supersedes_previous_uploaded_binding(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    artifact = _artifact(tmp_path)
    write_artifact_upload_success_binding(
        task_root,
        _result(),
        artifact,
        role="attachment",
        file_id="file_001",
        metadata_url="/v1/files/file_001",
        download_url="/v1/files/file_001/content",
        uploaded_at="2026-03-23T18:09:55",
    )

    sidecar_path = write_artifact_upload_success_binding(
        task_root,
        _result(),
        artifact,
        role="attachment",
        file_id="file_002",
        metadata_url="/v1/files/file_002",
        download_url="/v1/files/file_002/content",
        uploaded_at="2026-03-23T18:11:00",
    )

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    bindings = payload["items"][0]["bindings"]
    assert bindings[0]["status"] == "superseded"
    assert bindings[0]["file_id"] == "file_001"
    assert bindings[1]["status"] == "uploaded"
    assert bindings[1]["file_id"] == "file_002"


def test_upload_artifact_to_file_surface_roundtrip_writes_success_binding(tmp_path: Path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        task_root = tmp_path / "tasks"
        artifact = _artifact(tmp_path)
        result = upload_artifact_to_file_surface(
            task_root,
            _result(),
            artifact,
            file_surface_url=f"http://{host}:{port}/v1/files",
            transport_token="secret-token",
            role="attachment",
            trace_id="trace_001",
            request_id="req_001",
            packet_id="pkt_001",
        )

        assert result.success is True
        assert result.status_code == 200
        assert result.descriptor is not None
        assert result.descriptor["artifact"]["artifact_id"] == "artifact-chart"
        assert result.sidecar_path is not None
        payload = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
        binding = payload["items"][0]["bindings"][0]
        assert binding["status"] == "uploaded"
        assert binding["file_id"] == result.descriptor["artifact"]["file_id"]
        assert binding["trace_id"] == "trace_001"
        assert binding["request_id"] == "req_001"
        assert binding["packet_id"] == "pkt_001"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_upload_artifact_to_file_surface_writes_failed_binding_for_oversized_upload(tmp_path: Path) -> None:
    config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="secret-token",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(
        config,
        session_store=InMemorySessionStore(),
        file_upload_limit_bytes=4,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        task_root = tmp_path / "tasks"
        artifact = _artifact(tmp_path)
        result = upload_artifact_to_file_surface(
            task_root,
            _result(),
            artifact,
            file_surface_url=f"http://{host}:{port}/v1/files",
            transport_token="secret-token",
            role="attachment",
            trace_id="trace_002",
        )

        assert result.success is False
        assert result.status_code == 413
        assert result.error_code == "payload_too_large"
        assert result.sidecar_path is not None
        payload = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
        bindings = payload["items"][0]["bindings"]
        assert len(bindings) == 1
        assert bindings[0]["status"] == "failed"
        assert bindings[0]["error_code"] == "payload_too_large"
        assert bindings[0]["trace_id"] == "trace_002"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
