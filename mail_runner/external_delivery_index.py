"""Helpers for persisted per-run external delivery evidence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ExternalDelivery, RunArtifact, RunResult

EXTERNAL_DELIVERY_INDEX_FILENAME = "external_delivery_index.json"
EXTERNAL_DELIVERY_INDEX_SCHEMA = "taskmail-external-delivery-index-v1"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(rendered, encoding="utf-8")
    temp_path.replace(path)


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


def _load_external_delivery_index(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"external delivery index {path} must be a JSON object")
    if payload.get("schemaVersion") != EXTERNAL_DELIVERY_INDEX_SCHEMA:
        raise ValueError(f"external delivery index {path} has unsupported schemaVersion")
    if not isinstance(payload.get("items"), list):
        raise ValueError(f"external delivery index {path} must contain items as a list")
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
        "content_type": artifact.content_type,
        "byte_size": path.stat().st_size,
        "deliveries": [],
    }


def _ensure_artifact_item(index_payload: dict[str, Any], artifact: RunArtifact) -> dict[str, Any]:
    items = index_payload.setdefault("items", [])
    for item in items:
        if isinstance(item, dict) and item.get("artifact_id") == artifact.artifact_id:
            refreshed = _artifact_item(artifact)
            item.update({key: value for key, value in refreshed.items() if key != "deliveries"})
            item.setdefault("deliveries", [])
            if not isinstance(item["deliveries"], list):
                raise ValueError("external delivery item deliveries must be a list")
            return item
    new_item = _artifact_item(artifact)
    items.append(new_item)
    return new_item


def _index_payload(
    task_root: str | Path,
    result: RunResult,
    *,
    generated_at: str,
) -> tuple[Path, dict[str, Any]]:
    artifacts_root = _resolve_artifacts_root(task_root, result)
    target = artifacts_root / EXTERNAL_DELIVERY_INDEX_FILENAME
    existing = _load_external_delivery_index(target)
    if existing is not None:
        existing["generated_at"] = generated_at
        return target, existing
    return target, {
        "schemaVersion": EXTERNAL_DELIVERY_INDEX_SCHEMA,
        "task_id": result.task_id,
        "thread_id": result.thread_id,
        "artifacts_root": _artifacts_root_label(task_root, result, artifacts_root),
        "generated_at": generated_at,
        "items": [],
    }


def write_external_delivery_index(
    task_root: str | Path,
    result: RunResult,
    *,
    artifacts: list[RunArtifact],
    deliveries: list[ExternalDelivery],
    recorded_at: str | None = None,
) -> Path | None:
    if not deliveries:
        return None
    artifact_by_id = {
        _require_text(artifact.artifact_id, "artifact.artifact_id"): artifact
        for artifact in artifacts
    }
    now = recorded_at or _timestamp()
    target, payload = _index_payload(task_root, result, generated_at=now)
    for delivery in deliveries:
        artifact = artifact_by_id.get(delivery.artifact_id)
        if artifact is None:
            continue
        item = _ensure_artifact_item(payload, artifact)
        deliveries_payload = item["deliveries"]
        for existing in deliveries_payload:
            if isinstance(existing, dict) and existing.get("status") == "delivered":
                existing["status"] = "superseded"
        deliveries_payload.append(
            {
                "status": "delivered",
                "recorded_at": now,
                "provider": delivery.provider,
                "url": delivery.url,
                "expires_at": delivery.expires_at,
                "object_key": delivery.object_key,
                "bucket": delivery.bucket,
                "content_type": delivery.content_type,
                "size_bytes": delivery.size_bytes,
            }
        )
    _atomic_write_json(target, payload)
    return target
