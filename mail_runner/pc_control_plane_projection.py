"""Helpers for projecting local run evidence into PC control-plane packets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .download_ref import build_vps_file_download_ref
from .external_delivery_index import EXTERNAL_DELIVERY_INDEX_FILENAME
from .file_surface import ARTIFACT_FILE_BINDING_INDEX_FILENAME
from .models import RunResult
from .stream_events import STREAM_EVENTS_FILENAME, load_stream_events
from .workspace import WorkspaceManager

_ARTIFACT_INDEX_FILENAME = "artifact_index.json"


def derive_stream_id(thread_id: str, task_id: str) -> str:
    return f"{str(thread_id).strip()}:{str(task_id).strip()}"


def project_output_chunks(task_root: str | Path, *, thread_id: str, task_id: str) -> list[dict[str, Any]]:
    workspace = WorkspaceManager(task_root)
    stream_path = workspace.run_file_path(thread_id, task_id, STREAM_EVENTS_FILENAME)
    events = load_stream_events(stream_path)
    stream_id = derive_stream_id(thread_id, task_id)
    projected: list[dict[str, Any]] = []
    for event in events:
        if not (event.text or event.delta):
            continue
        projected.append(
            {
                "stream_id": stream_id,
                "stream_id_source": "derived_from_run_identity",
                "seq": event.seq,
                "kind": event.kind,
                "text": event.text,
                "delta": event.delta,
                "status": event.status,
                "item_type": event.item_type,
            }
        )
    return projected


def project_artifact_manifest(task_root: str | Path, *, result: RunResult) -> dict[str, Any] | None:
    artifacts_root = _artifacts_root(task_root, result)
    index_payload = _load_json_if_exists(artifacts_root / _ARTIFACT_INDEX_FILENAME)
    if not isinstance(index_payload, dict):
        return None
    raw_items = index_payload.get("items")
    if not isinstance(raw_items, list):
        return None
    binding_payload = _load_json_if_exists(artifacts_root / ARTIFACT_FILE_BINDING_INDEX_FILENAME)
    binding_items = _binding_items_by_artifact_id(binding_payload)
    external_delivery_payload = _load_json_if_exists(artifacts_root / EXTERNAL_DELIVERY_INDEX_FILENAME)
    external_delivery_items = _external_delivery_items_by_artifact_id(external_delivery_payload)

    projected_items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or "").strip()
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "").strip()
        content_type = str(item.get("content_type") or "").strip()
        path_text = str(item.get("path") or "").strip()
        if not artifact_id or not name or not kind or not content_type or not path_text:
            continue
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            continue
        latest_delivery = _latest_delivered_external_delivery(external_delivery_items.get(artifact_id))
        latest_uploaded = _latest_uploaded_binding(binding_items.get(artifact_id))
        if latest_delivery is not None:
            provider = str(latest_delivery.get("provider") or "").strip()
            download_ref_source = f"external_delivery_index.{provider}" if provider else "external_delivery_index"
            if provider == "file_surface":
                download_ref = build_vps_file_download_ref(
                    file_id=str(latest_delivery.get("object_key") or "").strip() or None,
                    content_url=str(latest_delivery.get("url") or "").strip() or None,
                    content_type=str(latest_delivery.get("content_type") or "").strip() or None,
                )
            else:
                download_ref = None
        else:
            download_ref = (
                build_vps_file_download_ref(
                    file_id=str(latest_uploaded.get("file_id") or "").strip() or None,
                    metadata_url=str(latest_uploaded.get("metadata_url") or "").strip() or None,
                    content_url=str(latest_uploaded.get("download_url") or "").strip() or None,
                )
                if latest_uploaded is not None
                else None
            )
            download_ref_source = "artifact_file_binding_index" if latest_uploaded is not None else None
        projected_items.append(
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "name": name,
                "content_type": content_type,
                "size": path.stat().st_size,
                "download_ref": download_ref,
                "download_ref_source": download_ref_source,
            }
        )
    if not projected_items:
        return None
    return {
        "artifacts_root": str(index_payload.get("artifacts_root") or "").strip() or None,
        "source": str(index_payload.get("source") or "").strip() or None,
        "artifacts": projected_items,
    }


def _artifacts_root(task_root: str | Path, result: RunResult) -> Path:
    workspace = WorkspaceManager(task_root)
    thread_dir = workspace.thread_dir(result.thread_id)
    return thread_dir / result.artifacts_dir if result.artifacts_dir else thread_dir


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


def _binding_items_by_artifact_id(binding_payload: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(binding_payload, dict):
        return items
    raw_items = binding_payload.get("items")
    if not isinstance(raw_items, list):
        return items
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or "").strip()
        bindings = item.get("bindings")
        if not artifact_id or not isinstance(bindings, list):
            continue
        items[artifact_id] = [binding for binding in bindings if isinstance(binding, dict)]
    return items


def _external_delivery_items_by_artifact_id(
    delivery_payload: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(delivery_payload, dict):
        return items
    raw_items = delivery_payload.get("items")
    if not isinstance(raw_items, list):
        return items
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or "").strip()
        deliveries = item.get("deliveries")
        if not artifact_id or not isinstance(deliveries, list):
            continue
        items[artifact_id] = [delivery for delivery in deliveries if isinstance(delivery, dict)]
    return items


def _latest_uploaded_binding(bindings: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not bindings:
        return None
    uploaded = [binding for binding in bindings if str(binding.get("status") or "").strip() == "uploaded"]
    return uploaded[-1] if uploaded else None


def _latest_delivered_external_delivery(deliveries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not deliveries:
        return None
    delivered = [delivery for delivery in deliveries if str(delivery.get("status") or "").strip() == "delivered"]
    return delivered[-1] if delivered else None
