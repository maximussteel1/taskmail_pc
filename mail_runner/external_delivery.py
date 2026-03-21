"""External delivery helpers for oversized outgoing artifacts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests
import yaml

from .config import AppConfig, PROJECT_ROOT
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


def prepare_external_deliveries(
    config: AppConfig,
    *,
    artifacts: list[RunArtifact],
    attachments: list[OutgoingAttachment],
    result: RunResult | None,
    cos_client_factory: CosClientFactory | None = None,
) -> tuple[list[RunArtifact], list[OutgoingAttachment], list[ExternalDelivery], list[str]]:
    if result is None or not artifacts or not attachments:
        return list(artifacts), list(attachments), [], []

    settings = _resolve_cos_settings(config)
    threshold_bytes = max(int(config.external_delivery_threshold_mb), 0) * 1024 * 1024
    if settings is None:
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

    remaining_attachments = [
        item
        for item in attachments
        if _artifact_attachment_key(item.path, item.name or Path(item.path).name) not in externalized_keys
    ]
    return effective_artifacts, remaining_attachments, deliveries, notices
