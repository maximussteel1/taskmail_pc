"""Standalone fixture smoke for current artifact truth and manifest projection."""

from __future__ import annotations

import argparse
import json
import threading
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .artifact_resolver import project_run_artifacts_to_outgoing_attachments, resolve_run_artifacts, write_artifact_index
from .config import AppConfig, load_config
from .external_delivery import prepare_external_deliveries
from .external_delivery_index import EXTERNAL_DELIVERY_INDEX_FILENAME
from .file_surface import ARTIFACT_FILE_BINDING_INDEX_FILENAME, derive_file_surface_url
from .models import RunResult, ThreadState
from .pc_control_plane_projection import project_artifact_manifest
from .relay_server.app import build_http_server
from .relay_server.config import RelayServerConfig
from .relay_server.session_store import InMemorySessionStore
from .status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS, THREAD_STATUS_DONE

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_artifact_contract_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _build_direct_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def _relay_verify_arg(config: AppConfig) -> bool | str:
    ca_file = str(config.relay_ca_file or "").strip()
    if ca_file:
        return ca_file
    return bool(config.relay_verify_tls)


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _live_relay_config(config_path: Path) -> AppConfig:
    config = load_config(str(config_path))
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("live artifact contract smoke requires outbound_transport=relay")
    if not str(config.relay_url or "").strip():
        raise ValueError("live artifact contract smoke requires relay_url")
    if not str(config.relay_transport_token or "").strip():
        raise ValueError("live artifact contract smoke requires relay_transport_token")
    return replace(
        config,
        external_delivery_backend_preference="file_surface",
        external_delivery_threshold_mb=0,
    )


def _fetch_authenticated_response(
    url: str,
    *,
    transport_token: str,
    verify: bool | str,
    timeout_seconds: int,
) -> requests.Response:
    session = _build_direct_requests_session()
    try:
        return session.get(
            url,
            headers={"Authorization": f"Bearer {transport_token}"},
            timeout=max(1, int(timeout_seconds)),
            verify=verify,
        )
    finally:
        session.close()


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


def run_artifact_contract_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
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
    attachments = project_run_artifacts_to_outgoing_attachments(artifacts)
    for attachment in attachments:
        if attachment.name == "report.md":
            attachment.attach = False
            attachment.inline = False

    live_mode = config_path is not None
    relay_url = ""
    file_surface_url = ""
    host = ""
    port: int | None = None
    cleanup_required = False
    cleanup_ok = True
    cleanup_reason = "no local relay fixture was started"
    metadata_url = ""
    metadata_status: int | None = None
    metadata_payload: dict[str, Any] | None = None
    download_status: int | None = None
    download_verified = False
    effective_config: AppConfig | None = None

    if live_mode:
        resolved_config_path = Path(config_path).resolve()
        effective_config = _live_relay_config(resolved_config_path)
        relay_url = str(effective_config.relay_url or "").strip()
        file_surface_url = derive_file_surface_url(relay_url)
        parsed_surface = urlsplit(file_surface_url)
        host = parsed_surface.hostname or ""
        port = parsed_surface.port or _default_port_for_scheme(parsed_surface.scheme)
        _, remaining_attachments, deliveries, notices = prepare_external_deliveries(
            effective_config,
            artifacts=artifacts,
            attachments=attachments,
            result=result,
            task_root=task_root,
        )
    else:
        relay_config = RelayServerConfig(
            host="127.0.0.1",
            port=0,
            transport_token="relay-secret",
            state_dir=str(run_root / "relay_state"),
        )
        server = build_http_server(relay_config, session_store=InMemorySessionStore())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        relay_url = f"ws://{host}:{port}/relay"
        file_surface_url = derive_file_surface_url(relay_url)
        cleanup_required = True
        cleanup_reason = "local relay file-surface server was started and shut down within the smoke"
        try:
            _, remaining_attachments, deliveries, notices = prepare_external_deliveries(
                AppConfig(
                    outbound_transport="relay",
                    relay_url=relay_url,
                    relay_transport_token="relay-secret",
                    relay_timeout_seconds=5,
                    external_delivery_threshold_mb=0,
                    external_delivery_backend_preference="file_surface",
                ),
                artifacts=artifacts,
                attachments=attachments,
                result=result,
                task_root=task_root,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    binding_path = artifacts_dir / ARTIFACT_FILE_BINDING_INDEX_FILENAME
    external_delivery_index_path = artifacts_dir / EXTERNAL_DELIVERY_INDEX_FILENAME
    candidate_manifest = project_artifact_manifest(task_root, result=result)

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
    if not external_delivery_index_path.exists():
        failures.append("external_delivery_index.json was not written.")
    if len(deliveries) != 1 or deliveries[0].provider != "file_surface":
        failures.append("Expected exactly one live relay file-surface external delivery.")
    if notices:
        failures.append("Live relay file-surface smoke unexpectedly produced notices.")
    if len(remaining_attachments) != 1 or remaining_attachments[0].name != "report.md":
        failures.append("Expected report.md to remain as the only direct attachment candidate.")
    if candidate_manifest is None:
        failures.append("project_artifact_manifest did not produce a manifest.")
        candidate_items: list[dict[str, Any]] = []
    else:
        candidate_items = list(candidate_manifest.get("artifacts") or [])
    preview_item = next((item for item in candidate_items if item.get("artifact_id") == "artifact-preview"), None)
    report_item = next((item for item in candidate_items if item.get("artifact_id") == "artifact-report"), None)
    expected_preview_url = deliveries[0].url if deliveries else ""

    if deliveries:
        delivery = deliveries[0]
        metadata_url = f"{file_surface_url.rstrip('/')}/{delivery.object_key}"
        verify_arg = _relay_verify_arg(effective_config) if effective_config is not None else True
        transport_token = (
            str(effective_config.relay_transport_token or "").strip() if effective_config is not None else "relay-secret"
        )
        timeout_seconds = int(effective_config.relay_timeout_seconds) if effective_config is not None else 5
        metadata_response = _fetch_authenticated_response(
            metadata_url,
            transport_token=transport_token,
            verify=verify_arg,
            timeout_seconds=timeout_seconds,
        )
        metadata_status = metadata_response.status_code
        try:
            metadata_payload = metadata_response.json()
        except Exception:
            metadata_payload = None
        download_response = _fetch_authenticated_response(
            delivery.url,
            transport_token=transport_token,
            verify=verify_arg,
            timeout_seconds=timeout_seconds,
        )
        download_status = download_response.status_code
        download_verified = download_response.content == preview_path.read_bytes()

    if deliveries:
        if metadata_status != 200:
            failures.append(f"Expected metadata GET to return 200, got {metadata_status}.")
        elif not isinstance(metadata_payload, dict):
            failures.append("Metadata GET did not return a JSON object.")
        elif str((metadata_payload.get("artifact") or {}).get("file_id") or "").strip() != str(deliveries[0].object_key):
            failures.append("Metadata GET did not return the expected file_id.")
        if download_status != 200:
            failures.append(f"Expected content GET to return 200, got {download_status}.")
        elif not download_verified:
            failures.append("Downloaded file-surface content did not match the local artifact bytes.")

    if preview_item is None:
        failures.append("artifact-preview did not appear in projected artifact_manifest.")
    elif preview_item.get("download_ref") != expected_preview_url:
        failures.append("artifact-preview download_ref did not match the live relay file-surface URL.")
    elif preview_item.get("download_ref_source") != "external_delivery_index.file_surface":
        failures.append("artifact-preview download_ref_source did not reflect live external_delivery_index evidence.")
    if report_item is None:
        failures.append("artifact-report did not appear in projected artifact_manifest.")
    elif report_item.get("download_ref") is not None:
        failures.append("Non-uploaded artifact unexpectedly has a download_ref.")

    smoke_result = {
        "success": not failures,
        "run_name": run_name,
        "smoke_mode": "live_relay_file_surface" if live_mode else "local_fixture",
        "config_path": str(Path(config_path).resolve()) if config_path is not None else None,
        "task_root": str(task_root),
        "manifest_path": str(manifest_path),
        "artifact_index_path": str(index_path) if index_path is not None else None,
        "binding_index_path": str(binding_path) if binding_path.exists() else None,
        "external_delivery_index_path": str(external_delivery_index_path) if external_delivery_index_path.exists() else None,
        "live_relay_file_surface": {
            "mode": "live_relay_host" if live_mode else "local_fixture_server",
            "relay_url": relay_url,
            "file_surface_url": file_surface_url,
            "host": host,
            "port": port,
            "delivery_count": len(deliveries),
            "delivery_urls": [delivery.url for delivery in deliveries],
            "metadata_url": metadata_url or None,
            "metadata_status": metadata_status,
            "download_status": download_status,
            "download_verified": download_verified,
        },
        "resolved_artifacts": [asdict(item) for item in artifacts],
        "skipped": skipped,
        "remaining_attachments": [asdict(item) for item in remaining_attachments],
        "external_deliveries": [asdict(item) for item in deliveries],
        "delivery_notices": notices,
        "candidate_artifact_manifest": candidate_items,
        "gaps": [
            {
                "kind": "cos_live_delivery_not_covered",
                "summary": (
                    "This smoke covers a live relay /v1/files upload/download roundtrip, "
                    "but it still does not cover a live COS upload roundtrip."
                ),
                "recorded": True,
            }
        ],
        "cleanup": {
            "required": cleanup_required,
            "cleanup_ok": cleanup_ok,
            "reason": cleanup_reason,
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
    parser.add_argument(
        "--config",
        help="Optional relay-enabled config path. When provided, the smoke uploads to the real relay host's /v1/files lane instead of a local fixture server.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"artifact-contract-smoke-{_timestamp_slug()}"
    result = run_artifact_contract_smoke(
        output_dir=Path(args.output_dir),
        run_name=run_name,
        config_path=args.config,
    )
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
