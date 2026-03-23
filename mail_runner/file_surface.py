"""Helpers for transport-facing file-surface payloads and bindings."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .models import RunArtifact, RunResult

ARTIFACT_FILE_BINDING_INDEX_FILENAME = "artifact_file_binding_index.json"
ARTIFACT_FILE_BINDING_INDEX_SCHEMA = "taskmail-artifact-file-binding-index-v1"
SINGLE_FILE_UPLOAD_LIMIT_BYTES = 32 * 1024 * 1024
FILE_SURFACE_SCHEMA_VERSION = "taskmail-control-artifact-contract-v1"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_optional_text(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _require_text(value, field_name)


def _require_optional_non_negative_int(value: int | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _resolve_artifacts_root(task_root: str | Path, result: RunResult) -> Path:
    if result.artifacts_dir:
        candidate = Path(result.artifacts_dir)
        if candidate.is_absolute():
            return candidate
        return Path(task_root) / result.thread_id / candidate
    return Path(task_root) / result.thread_id / "runs" / result.task_id / "artifacts"


def _artifacts_root_label(task_root: str | Path, result: RunResult, artifacts_root: Path) -> str:
    if result.artifacts_dir:
        candidate = Path(result.artifacts_dir)
        if candidate.is_absolute():
            return str(candidate)
        return candidate.as_posix()
    thread_root = Path(task_root) / result.thread_id
    try:
        return artifacts_root.relative_to(thread_root).as_posix()
    except ValueError:
        return str(artifacts_root)


def _sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _build_direct_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(rendered, encoding="utf-8")
    temp_path.replace(path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_bytes(payload)
    temp_path.replace(path)


def _load_binding_index(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"binding index {path} must be a JSON object")
    if payload.get("schemaVersion") != ARTIFACT_FILE_BINDING_INDEX_SCHEMA:
        raise ValueError(f"binding index {path} has unsupported schemaVersion")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError(f"binding index {path} must contain items as a list")
    return payload


def _artifact_item(artifact: RunArtifact) -> dict[str, Any]:
    path = Path(artifact.path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"artifact file does not exist: {path}")
    return {
        "artifact_id": artifact.artifact_id,
        "local_path": str(path.resolve()),
        "name": artifact.name,
        "kind": artifact.kind,
        "mime_type": artifact.content_type,
        "byte_size": path.stat().st_size,
        "sha256": _sha256_for_file(path),
        "bindings": [],
    }


def _ensure_artifact_item(index_payload: dict[str, Any], artifact: RunArtifact) -> dict[str, Any]:
    artifact_id = artifact.artifact_id
    items = index_payload.setdefault("items", [])
    for item in items:
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id:
            refreshed = _artifact_item(artifact)
            item.update({key: value for key, value in refreshed.items() if key != "bindings"})
            item.setdefault("bindings", [])
            if not isinstance(item["bindings"], list):
                raise ValueError("binding item bindings must be a list")
            return item
    new_item = _artifact_item(artifact)
    items.append(new_item)
    return new_item


def _binding_index_payload(task_root: str | Path, result: RunResult, *, generated_at: str) -> tuple[Path, dict[str, Any]]:
    artifacts_root = _resolve_artifacts_root(task_root, result)
    target = artifacts_root / ARTIFACT_FILE_BINDING_INDEX_FILENAME
    existing = _load_binding_index(target)
    if existing is not None:
        existing["generated_at"] = generated_at
        return target, existing
    return target, {
        "schemaVersion": ARTIFACT_FILE_BINDING_INDEX_SCHEMA,
        "task_id": result.task_id,
        "thread_id": result.thread_id,
        "artifacts_root": _artifacts_root_label(task_root, result, artifacts_root),
        "generated_at": generated_at,
        "surface": "v1_files",
        "items": [],
    }


def _optional_text_fields(**values: str | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text
    return normalized


def _validate_artifact_kind(value: str) -> None:
    if value not in {"image", "file"}:
        raise ValueError("kind must be one of: image, file")


@dataclass(slots=True)
class FileSurfaceUploadError(Exception):
    status_code: int
    error_code: str
    error_message: str
    retryable: bool
    trace_id: str | None = None
    artifact_id: str | None = None
    max_bytes: int | None = None
    observed_bytes: int | None = None
    expected_sha256: str | None = None
    received_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int) or not (100 <= self.status_code <= 599):
            raise ValueError("status_code must be an integer between 100 and 599")
        _require_text(self.error_code, "error_code")
        _require_text(self.error_message, "error_message")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a bool")
        _require_optional_text(self.trace_id, "trace_id")
        _require_optional_text(self.artifact_id, "artifact_id")
        _require_optional_text(self.expected_sha256, "expected_sha256")
        _require_optional_text(self.received_sha256, "received_sha256")
        _require_optional_non_negative_int(self.max_bytes, "max_bytes")
        _require_optional_non_negative_int(self.observed_bytes, "observed_bytes")

    def to_response_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retryable": self.retryable,
        }
        payload.update(
            _optional_text_fields(
                trace_id=self.trace_id,
                artifact_id=self.artifact_id,
                expected_sha256=self.expected_sha256,
                received_sha256=self.received_sha256,
            )
        )
        if self.max_bytes is not None:
            payload["max_bytes"] = self.max_bytes
        if self.observed_bytes is not None:
            payload["observed_bytes"] = self.observed_bytes
        return payload


@dataclass(slots=True)
class FileSurfaceClientUploadResult:
    file_surface_url: str
    success: bool
    status_code: int | None = None
    descriptor: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    sidecar_path: Path | None = None


def derive_file_surface_url(relay_url: str) -> str:
    normalized = str(relay_url or "").strip()
    if not normalized:
        raise ValueError("relay_url must be a non-empty string")
    parsed = urlsplit(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError("relay_url must use ws:// or wss://")
    http_scheme = "https" if scheme == "wss" else "http"
    return urlunsplit((http_scheme, parsed.netloc, "/v1/files", "", ""))


def build_file_surface_upload_metadata(
    artifact: RunArtifact,
    *,
    role: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    _require_text(role, "role")
    item = _artifact_item(artifact)
    payload = {
        "artifact_id": item["artifact_id"],
        "name": item["name"],
        "kind": item["kind"],
        "role": role,
        "mime_type": item["mime_type"],
        "byte_size": item["byte_size"],
        "sha256": item["sha256"],
    }
    if trace_id:
        payload["trace"] = {"trace_id": str(trace_id).strip()}
    return payload


def upload_artifact_to_file_surface(
    task_root: str | Path,
    result: RunResult,
    artifact: RunArtifact,
    *,
    file_surface_url: str,
    transport_token: str,
    role: str,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
    ca_file: str | None = None,
    trace_id: str | None = None,
    probe_id: str | None = None,
    request_id: str | None = None,
    packet_id: str | None = None,
    session_factory: Any | None = None,
) -> FileSurfaceClientUploadResult:
    normalized_url = str(file_surface_url or "").strip()
    normalized_token = str(transport_token or "").strip()
    _require_text(normalized_url, "file_surface_url")
    _require_text(normalized_token, "transport_token")
    metadata = build_file_surface_upload_metadata(artifact, role=role, trace_id=trace_id)
    artifact_path = Path(artifact.path)
    file_bytes = artifact_path.read_bytes()
    session = (session_factory or _build_direct_requests_session)()
    try:
        response = session.post(
            normalized_url,
            headers={"Authorization": f"Bearer {normalized_token}"},
            files={
                "metadata": (None, json.dumps(metadata, ensure_ascii=False), "application/json"),
                "file": (artifact.name, file_bytes, artifact.content_type),
            },
            timeout=max(1, int(timeout_seconds)),
            verify=(ca_file if ca_file else verify_tls),
        )
    except Exception as exc:
        error = FileSurfaceUploadError(
            status_code=599,
            error_code="request_failed",
            error_message=f"{type(exc).__name__}: {exc}",
            retryable=True,
            trace_id=trace_id,
            artifact_id=artifact.artifact_id,
        )
        sidecar_path = write_artifact_upload_failure_binding(
            task_root,
            result,
            artifact,
            role=role,
            error=error,
            trace_id=trace_id,
            probe_id=probe_id,
            request_id=request_id,
            packet_id=packet_id,
        )
        return FileSurfaceClientUploadResult(
            file_surface_url=normalized_url,
            success=False,
            status_code=None,
            error_code=error.error_code,
            error_message=error.error_message,
            sidecar_path=sidecar_path,
        )
    finally:
        try:
            session.close()
        except Exception:
            pass

    if response.ok:
        descriptor = response.json()
        artifact_descriptor = descriptor.get("artifact") if isinstance(descriptor, dict) else None
        if not isinstance(artifact_descriptor, dict):
            error = FileSurfaceUploadError(
                status_code=int(response.status_code),
                error_code="invalid_descriptor",
                error_message="successful upload response missing artifact descriptor",
                retryable=False,
                trace_id=trace_id,
                artifact_id=artifact.artifact_id,
            )
            sidecar_path = write_artifact_upload_failure_binding(
                task_root,
                result,
                artifact,
                role=role,
                error=error,
                trace_id=trace_id,
                probe_id=probe_id,
                request_id=request_id,
                packet_id=packet_id,
            )
            return FileSurfaceClientUploadResult(
                file_surface_url=normalized_url,
                success=False,
                status_code=int(response.status_code),
                error_code=error.error_code,
                error_message=error.error_message,
                sidecar_path=sidecar_path,
            )
        sidecar_path = write_artifact_upload_success_binding(
            task_root,
            result,
            artifact,
            role=role,
            file_id=str(artifact_descriptor.get("file_id") or "").strip(),
            metadata_url=str(artifact_descriptor.get("metadata_url") or "").strip(),
            download_url=str(artifact_descriptor.get("download_url") or "").strip(),
            trace_id=trace_id,
            probe_id=probe_id,
            request_id=request_id,
            packet_id=packet_id,
        )
        return FileSurfaceClientUploadResult(
            file_surface_url=normalized_url,
            success=True,
            status_code=int(response.status_code),
            descriptor=descriptor,
            sidecar_path=sidecar_path,
        )

    error_payload: dict[str, Any] = {}
    try:
        parsed_payload = response.json()
        if isinstance(parsed_payload, dict):
            error_payload = parsed_payload
    except Exception:
        error_payload = {}
    error = FileSurfaceUploadError(
        status_code=int(response.status_code),
        error_code=str(error_payload.get("error_code") or "upload_failed").strip() or "upload_failed",
        error_message=str(error_payload.get("error_message") or f"HTTP {response.status_code}").strip()
        or f"HTTP {response.status_code}",
        retryable=bool(error_payload.get("retryable", False)),
        trace_id=str(error_payload.get("trace_id") or trace_id or "").strip() or None,
        artifact_id=str(error_payload.get("artifact_id") or artifact.artifact_id or "").strip() or None,
        max_bytes=(
            int(error_payload["max_bytes"])
            if isinstance(error_payload.get("max_bytes"), int)
            else None
        ),
        observed_bytes=(
            int(error_payload["observed_bytes"])
            if isinstance(error_payload.get("observed_bytes"), int)
            else None
        ),
        expected_sha256=str(error_payload.get("expected_sha256") or "").strip() or None,
        received_sha256=str(error_payload.get("received_sha256") or "").strip() or None,
    )
    sidecar_path = write_artifact_upload_failure_binding(
        task_root,
        result,
        artifact,
        role=role,
        error=error,
        trace_id=trace_id,
        probe_id=probe_id,
        request_id=request_id,
        packet_id=packet_id,
    )
    return FileSurfaceClientUploadResult(
        file_surface_url=normalized_url,
        success=False,
        status_code=int(response.status_code),
        error_code=error.error_code,
        error_message=error.error_message,
        sidecar_path=sidecar_path,
    )


class FileSurfaceStore:
    def __init__(
        self,
        state_dir: str | Path,
        *,
        upload_limit_bytes: int = SINGLE_FILE_UPLOAD_LIMIT_BYTES,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._files_root = self._state_dir / "files"
        if not isinstance(upload_limit_bytes, int) or upload_limit_bytes <= 0:
            raise ValueError("upload_limit_bytes must be a positive integer")
        self._upload_limit_bytes = upload_limit_bytes

    def store_upload(
        self,
        metadata: dict[str, Any],
        file_bytes: bytes,
        *,
        stored_at: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise FileSurfaceUploadError(
                status_code=400,
                error_code="invalid_metadata",
                error_message="metadata must be a JSON object",
                retryable=False,
            )
        if not isinstance(file_bytes, (bytes, bytearray)):
            raise FileSurfaceUploadError(
                status_code=400,
                error_code="invalid_metadata",
                error_message="file payload must be bytes",
                retryable=False,
            )
        raw_bytes = bytes(file_bytes)
        observed_size = len(raw_bytes)
        if observed_size > self._upload_limit_bytes:
            raise FileSurfaceUploadError(
                status_code=413,
                error_code="payload_too_large",
                error_message="single_file_upload_limit_bytes exceeded",
                retryable=False,
                artifact_id=str(metadata.get("artifact_id") or "").strip() or None,
                max_bytes=self._upload_limit_bytes,
                observed_bytes=observed_size,
            )

        try:
            name = str(metadata.get("name") or "").strip()
            kind = str(metadata.get("kind") or "").strip()
            role = str(metadata.get("role") or "").strip()
            mime_type = str(metadata.get("mime_type") or "").strip()
            declared_sha256 = str(metadata.get("sha256") or "").strip().lower()
            declared_size = metadata.get("byte_size")
            artifact_id = str(metadata.get("artifact_id") or "").strip()

            _require_text(name, "name")
            _validate_artifact_kind(kind)
            _require_text(role, "role")
            _require_text(mime_type, "mime_type")
            _require_text(declared_sha256, "sha256")
            if not isinstance(declared_size, int) or declared_size < 0:
                raise ValueError("byte_size must be a non-negative integer")
        except ValueError as exc:
            raise FileSurfaceUploadError(
                status_code=400,
                error_code="invalid_metadata",
                error_message=str(exc),
                retryable=False,
                artifact_id=artifact_id or None,
            ) from exc

        if declared_size != observed_size:
            raise FileSurfaceUploadError(
                status_code=409,
                error_code="byte_size_mismatch",
                error_message="declared byte_size does not match uploaded content",
                retryable=False,
                artifact_id=artifact_id or None,
                observed_bytes=observed_size,
            )

        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha256 != declared_sha256:
            raise FileSurfaceUploadError(
                status_code=409,
                error_code="hash_mismatch",
                error_message="declared sha256 does not match uploaded content",
                retryable=False,
                artifact_id=artifact_id or None,
                observed_bytes=observed_size,
                expected_sha256=declared_sha256,
                received_sha256=actual_sha256,
            )

        now = stored_at or _timestamp()
        file_id, target_dir = self._allocate_target_dir()
        artifact_descriptor = {
            "artifact_id": artifact_id or file_id,
            "file_id": file_id,
            "name": name,
            "kind": kind,
            "role": role,
            "mime_type": mime_type,
            "byte_size": observed_size,
            "sha256": actual_sha256,
            "metadata_url": f"/v1/files/{file_id}",
            "download_url": f"/v1/files/{file_id}/content",
        }
        payload = {
            "schema_version": FILE_SURFACE_SCHEMA_VERSION,
            "file_id": file_id,
            "stored_at": now,
            "artifact": artifact_descriptor,
        }
        target_dir.mkdir(parents=True, exist_ok=False)
        try:
            _atomic_write_bytes(target_dir / "content.bin", raw_bytes)
            _atomic_write_json(target_dir / "metadata.json", payload)
        except Exception:
            self._cleanup_dir(target_dir)
            raise
        return payload

    def get_metadata(self, file_id: str) -> dict[str, Any] | None:
        _require_text(file_id, "file_id")
        metadata_path = self._files_root / file_id / "metadata.json"
        if not metadata_path.exists():
            return None
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"metadata for {file_id} must be a JSON object")
        return payload

    def get_content(self, file_id: str) -> tuple[dict[str, Any], bytes] | None:
        metadata = self.get_metadata(file_id)
        if metadata is None:
            return None
        content_path = self._files_root / file_id / "content.bin"
        if not content_path.exists():
            return None
        return metadata, content_path.read_bytes()

    def _allocate_target_dir(self) -> tuple[str, Path]:
        self._files_root.mkdir(parents=True, exist_ok=True)
        while True:
            file_id = f"file_{secrets.token_hex(8)}"
            target_dir = self._files_root / file_id
            if not target_dir.exists():
                return file_id, target_dir

    @staticmethod
    def _cleanup_dir(path: Path) -> None:
        if not path.exists():
            return
        for child in path.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
        path.rmdir()


def write_artifact_upload_success_binding(
    task_root: str | Path,
    result: RunResult,
    artifact: RunArtifact,
    *,
    role: str,
    file_id: str,
    metadata_url: str,
    download_url: str,
    uploaded_at: str | None = None,
    trace_id: str | None = None,
    probe_id: str | None = None,
    request_id: str | None = None,
    packet_id: str | None = None,
) -> Path:
    _require_text(role, "role")
    _require_text(file_id, "file_id")
    _require_text(metadata_url, "metadata_url")
    _require_text(download_url, "download_url")
    now = uploaded_at or _timestamp()
    target, payload = _binding_index_payload(task_root, result, generated_at=now)
    item = _ensure_artifact_item(payload, artifact)
    bindings = item["bindings"]
    for binding in bindings:
        if isinstance(binding, dict) and binding.get("status") == "uploaded":
            binding["status"] = "superseded"
    binding_payload: dict[str, Any] = {
        "status": "uploaded",
        "uploaded_at": now,
        "role": role,
        "file_id": file_id,
        "metadata_url": metadata_url,
        "download_url": download_url,
    }
    binding_payload.update(
        _optional_text_fields(
            trace_id=trace_id,
            probe_id=probe_id,
            request_id=request_id,
            packet_id=packet_id,
        )
    )
    bindings.append(binding_payload)
    _atomic_write_json(target, payload)
    return target


def write_artifact_upload_failure_binding(
    task_root: str | Path,
    result: RunResult,
    artifact: RunArtifact,
    *,
    role: str,
    error: FileSurfaceUploadError,
    uploaded_at: str | None = None,
    trace_id: str | None = None,
    probe_id: str | None = None,
    request_id: str | None = None,
    packet_id: str | None = None,
) -> Path:
    _require_text(role, "role")
    now = uploaded_at or _timestamp()
    target, payload = _binding_index_payload(task_root, result, generated_at=now)
    item = _ensure_artifact_item(payload, artifact)
    binding_payload: dict[str, Any] = {
        "status": "failed",
        "uploaded_at": now,
        "role": role,
        "status_code": error.status_code,
        "error_code": error.error_code,
        "error_message": error.error_message,
        "retryable": error.retryable,
    }
    binding_payload.update(
        _optional_text_fields(
            trace_id=trace_id or error.trace_id,
            probe_id=probe_id,
            request_id=request_id,
            packet_id=packet_id,
        )
    )
    if error.max_bytes is not None:
        binding_payload["max_bytes"] = error.max_bytes
    if error.observed_bytes is not None:
        binding_payload["observed_bytes"] = error.observed_bytes
    if error.expected_sha256:
        binding_payload["expected_sha256"] = error.expected_sha256
    if error.received_sha256:
        binding_payload["received_sha256"] = error.received_sha256
    bindings = item["bindings"]
    bindings.append(binding_payload)
    _atomic_write_json(target, payload)
    return target
