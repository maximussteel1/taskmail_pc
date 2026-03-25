"""External delivery helpers for oversized outgoing artifacts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

from .config import AppConfig, PROJECT_ROOT
from .external_delivery_index import write_external_delivery_index
from .file_surface import SINGLE_FILE_UPLOAD_LIMIT_BYTES, derive_file_surface_url, upload_artifact_to_file_surface
from .models import ExternalDelivery, OutgoingAttachment, RunArtifact, RunResult

_LOCAL_COS_CONFIG_PATH = PROJECT_ROOT / "mail_config.cos.local.yaml"
_COS_DEFAULT_DOMAIN_BLOCKED_EXTENSIONS = {".apk", ".ipa"}

CosClientFactory = Callable[[dict[str, str]], Any]


def _load_local_cos_config() -> dict[str, str]:
    if not _LOCAL_COS_CONFIG_PATH.exists():
        return {}
    try:
        payload = yaml.safe_load(_LOCAL_COS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in payload.items() if str(value).strip()}


def _effective_config_value(config: AppConfig, field_name: str, overlay: dict[str, str]) -> str:
    configured = str(getattr(config, field_name, "") or "").strip()
    if configured:
        return configured
    return str(overlay.get(field_name) or "").strip()


def _resolve_cos_settings(config: AppConfig) -> dict[str, str] | None:
    overlay = _load_local_cos_config()
    settings = {
        "region": _effective_config_value(config, "cos_region", overlay),
        "bucket": _effective_config_value(config, "cos_bucket", overlay),
        "secret_id": _effective_config_value(config, "cos_secret_id", overlay),
        "secret_key": _effective_config_value(config, "cos_secret_key", overlay),
        "object_prefix": str(config.cos_object_prefix or "").strip() or "mail-runner",
    }
    if all(settings[key] for key in ("region", "bucket", "secret_id", "secret_key")):
        return settings
    return None


def _build_cos_client(settings: dict[str, str]) -> Any:
    from qcloud_cos import CosConfig, CosS3Client

    # COS external delivery should use direct network access and not inherit
    # ambient HTTP(S) proxy settings from the host environment.
    session = requests.session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))

    return CosS3Client(
        CosConfig(
            Region=settings["region"],
            SecretId=settings["secret_id"],
            SecretKey=settings["secret_key"],
            Scheme="https",
            Proxies={},
        ),
        session=session,
    )


def _artifact_attachment_key(path: str, name: str) -> tuple[Path, str]:
    return (Path(path).resolve(), name)


def _external_object_name(file_name: str) -> tuple[str, str | None]:
    suffix = Path(file_name).suffix.lower()
    if suffix in _COS_DEFAULT_DOMAIN_BLOCKED_EXTENSIONS:
        return f"{file_name}.bin", (
            f"COS default domain blocks direct {suffix[1:].upper()} distribution. "
            f"The external download uses a .bin object name; rename the downloaded file back to {file_name} if needed."
        )
    return file_name, None


def _build_object_key(
    *,
    object_prefix: str,
    result: RunResult,
    artifact: RunArtifact,
    used_keys: set[str],
) -> str:
    file_name = Path(artifact.name).name or Path(artifact.path).name
    external_name, _ = _external_object_name(file_name)
    prefix = object_prefix.strip("/")
    base_key = "/".join(
        part
        for part in (
            prefix,
            result.thread_id,
            result.task_id,
            external_name,
        )
        if part
    )
    candidate = base_key
    if candidate in used_keys:
        candidate = "/".join(
            part
            for part in (
                prefix,
                result.thread_id,
                result.task_id,
                f"{artifact.artifact_id}-{external_name}",
            )
            if part
        )
    used_keys.add(candidate)
    return candidate


def _resolve_file_surface_url(config: AppConfig) -> str | None:
    if str(config.outbound_transport or "").strip().lower() != "relay":
        return None
    if not str(config.relay_transport_token or "").strip():
        return None
    relay_url = str(config.relay_url or "").strip()
    if not relay_url:
        return None
    try:
        return derive_file_surface_url(relay_url)
    except ValueError:
        return None


def _select_external_delivery_backend(
    config: AppConfig,
    *,
    cos_settings: dict[str, str] | None,
    file_surface_url: str | None,
    task_root: Path | None,
) -> str | None:
    preference = str(config.external_delivery_backend_preference or "").strip().lower() or "auto"
    file_surface_available = file_surface_url is not None and task_root is not None
    if preference == "file_surface":
        if file_surface_available:
            return "file_surface"
        if cos_settings is not None:
            return "cos"
        return None
    if preference == "cos":
        if cos_settings is not None:
            return "cos"
        if file_surface_available:
            return "file_surface"
        return None
    if cos_settings is not None:
        return "cos"
    if file_surface_available:
        return "file_surface"
    return None


def _absolute_file_surface_url(file_surface_url: str, location: str) -> str:
    parsed_location = urlsplit(str(location or "").strip())
    if parsed_location.scheme and parsed_location.netloc:
        return urlunsplit(parsed_location)
    parsed_surface = urlsplit(file_surface_url)
    normalized_path = str(location or "").strip() or parsed_surface.path
    return urlunsplit((parsed_surface.scheme, parsed_surface.netloc, normalized_path, "", ""))


def _select_backend_for_artifact(
    selected_backend: str,
    *,
    artifact_path: Path,
    cos_settings: dict[str, str] | None,
) -> str:
    if (
        selected_backend == "file_surface"
        and cos_settings is not None
        and artifact_path.stat().st_size > SINGLE_FILE_UPLOAD_LIMIT_BYTES
    ):
        # During file-surface cutover, keep COS as a compatibility lane for
        # artifacts that the live /v1/files surface cannot yet accept.
        return "cos"
    return selected_backend


def prepare_external_deliveries(
    config: AppConfig,
    *,
    artifacts: list[RunArtifact],
    attachments: list[OutgoingAttachment],
    result: RunResult | None,
    task_root: str | Path | None = None,
    cos_client_factory: CosClientFactory | None = None,
) -> tuple[list[RunArtifact], list[OutgoingAttachment], list[ExternalDelivery], list[str]]:
    if result is None or not artifacts or not attachments:
        return list(artifacts), list(attachments), [], []

    settings = _resolve_cos_settings(config)
    file_surface_url = _resolve_file_surface_url(config)
    resolved_task_root = Path(task_root) if task_root is not None else None
    selected_backend = _select_external_delivery_backend(
        config,
        cos_settings=settings,
        file_surface_url=file_surface_url,
        task_root=resolved_task_root,
    )
    threshold_bytes = max(int(config.external_delivery_threshold_mb), 0) * 1024 * 1024
    if selected_backend is None:
        return list(artifacts), list(attachments), [], []

    attachment_map = {
        _artifact_attachment_key(item.path, item.name or Path(item.path).name): item for item in attachments
    }
    effective_artifacts: list[RunArtifact] = []
    externalized_keys: set[tuple[Path, str]] = set()
    deliveries: list[ExternalDelivery] = []
    notices: list[str] = []
    cos_client: Any | None = None
    used_object_keys: set[str] = set()

    for artifact in artifacts:
        attachment_key = _artifact_attachment_key(artifact.path, artifact.name)
        attachment = attachment_map.get(attachment_key)
        artifact_path = Path(artifact.path)
        should_attempt_external = (
            attachment is not None
            and (attachment.attach or attachment.inline)
            and artifact_path.exists()
            and artifact_path.is_file()
            and artifact_path.stat().st_size > threshold_bytes
        )
        if not should_attempt_external:
            effective_artifacts.append(artifact)
            continue

        artifact_backend = _select_backend_for_artifact(
            selected_backend,
            artifact_path=artifact_path,
            cos_settings=settings,
        )

        if artifact_backend == "cos":
            object_key = _build_object_key(
                object_prefix=settings["object_prefix"],
                result=result,
                artifact=artifact,
                used_keys=used_object_keys,
            )
            try:
                if cos_client is None:
                    cos_client = (cos_client_factory or _build_cos_client)(settings)
                cos_client.upload_file(
                    Bucket=settings["bucket"],
                    Key=object_key,
                    LocalFilePath=str(artifact_path),
                    EnableMD5=True,
                )
                download_url = cos_client.get_presigned_download_url(
                    Bucket=settings["bucket"],
                    Key=object_key,
                    Expired=int(config.cos_presign_expire_seconds),
                )
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=int(config.cos_presign_expire_seconds))
                ).replace(microsecond=0)
                deliveries.append(
                    ExternalDelivery(
                        artifact_id=artifact.artifact_id,
                        name=artifact.name,
                        provider="cos",
                        url=str(download_url),
                        expires_at=expires_at.isoformat(),
                        object_key=object_key,
                        size_bytes=artifact_path.stat().st_size,
                        content_type=artifact.content_type,
                        bucket=settings["bucket"],
                        path=str(artifact_path),
                    )
                )
                externalized_keys.add(attachment_key)
                effective_artifacts.append(replace(artifact, inline_preview=False))
                _, object_name_notice = _external_object_name(artifact.name)
                if object_name_notice:
                    notices.append(object_name_notice)
            except Exception as exc:
                externalized_keys.add(attachment_key)
                effective_artifacts.append(replace(artifact, inline_preview=False))
                notices.append(
                    f"External delivery failed for {artifact.name}: {exc}. "
                    "The file was not attached to avoid mail size limits."
                )
            continue

        try:
            upload_result = upload_artifact_to_file_surface(
                resolved_task_root,
                result,
                artifact,
                file_surface_url=file_surface_url,
                transport_token=config.relay_transport_token,
                role="attachment",
                timeout_seconds=config.relay_timeout_seconds,
                verify_tls=config.relay_verify_tls,
                ca_file=config.relay_ca_file or None,
                trace_id=result.task_id,
            )
            artifact_descriptor = (
                upload_result.descriptor.get("artifact")
                if isinstance(upload_result.descriptor, dict)
                else None
            )
            if not upload_result.success or not isinstance(artifact_descriptor, dict):
                raise RuntimeError(upload_result.error_message or upload_result.error_code or "upload failed")
            file_id = str(artifact_descriptor.get("file_id") or "").strip()
            download_url = str(artifact_descriptor.get("download_url") or "").strip()
            if not file_id or not download_url:
                raise RuntimeError("upload response missing file_id or download_url")
            deliveries.append(
                ExternalDelivery(
                    artifact_id=artifact.artifact_id,
                    name=artifact.name,
                    provider="file_surface",
                    url=_absolute_file_surface_url(file_surface_url, download_url),
                    expires_at=str(upload_result.descriptor.get("stored_at") or "not_applicable"),
                    object_key=file_id,
                    size_bytes=artifact_path.stat().st_size,
                    content_type=artifact.content_type,
                    bucket="relay-file-surface",
                    path=str(artifact_path),
                )
            )
            externalized_keys.add(attachment_key)
            effective_artifacts.append(replace(artifact, inline_preview=False))
        except Exception as exc:
            externalized_keys.add(attachment_key)
            effective_artifacts.append(replace(artifact, inline_preview=False))
            notices.append(
                f"External delivery failed for {artifact.name}: {exc}. "
                "The file was not attached to avoid mail size limits."
            )

    remaining_attachments = [
        item
        for item in attachments
        if _artifact_attachment_key(item.path, item.name or Path(item.path).name) not in externalized_keys
    ]
    if resolved_task_root is not None and deliveries:
        write_external_delivery_index(
            resolved_task_root,
            result,
            artifacts=artifacts,
            deliveries=deliveries,
        )
    return effective_artifacts, remaining_attachments, deliveries, notices
