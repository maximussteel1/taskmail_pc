"""Helpers for summarizing recent external-delivery evidence across task runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .external_delivery_index import EXTERNAL_DELIVERY_INDEX_FILENAME, EXTERNAL_DELIVERY_INDEX_SCHEMA
from .file_surface import SINGLE_FILE_UPLOAD_LIMIT_BYTES


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _normalize_provider(value: object) -> str:
    return str(value or "").strip().lower()


def _sort_runs_descending(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        runs,
        key=lambda item: (
            str(item.get("finished_at") or ""),
            str(item.get("task_id") or ""),
            str(item.get("thread_id") or ""),
        ),
        reverse=True,
    )


def collect_external_delivery_runs(task_root: str | Path) -> list[dict[str, Any]]:
    """Collect per-run external-delivery evidence from a task root."""

    task_root_path = Path(task_root)
    runs: list[dict[str, Any]] = []
    pattern = f"thread_*/runs/*/artifacts/{EXTERNAL_DELIVERY_INDEX_FILENAME}"
    for index_path in task_root_path.glob(pattern):
        payload = _load_json(index_path)
        if payload.get("schemaVersion") != EXTERNAL_DELIVERY_INDEX_SCHEMA:
            raise ValueError(f"unsupported schemaVersion in {index_path}")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError(f"{index_path} must contain items as a list")

        run_dir = index_path.parent.parent
        result_path = run_dir / "result.json"
        result_payload = _load_json(result_path) if result_path.exists() else {}
        deliveries: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{index_path} contains a non-object artifact item")
            item_deliveries = item.get("deliveries")
            if not isinstance(item_deliveries, list):
                raise ValueError(f"{index_path} artifact item deliveries must be a list")
            for delivery in item_deliveries:
                if not isinstance(delivery, dict):
                    raise ValueError(f"{index_path} contains a non-object delivery item")
                size_bytes = delivery.get("size_bytes")
                if not isinstance(size_bytes, int):
                    fallback_size = item.get("byte_size")
                    size_bytes = fallback_size if isinstance(fallback_size, int) else None
                deliveries.append(
                    {
                        "artifact_id": str(item.get("artifact_id") or "").strip(),
                        "artifact_name": str(item.get("name") or "").strip(),
                        "kind": str(item.get("kind") or "").strip(),
                        "content_type": str(item.get("content_type") or "").strip(),
                        "local_path": str(item.get("local_path") or "").strip(),
                        "provider": _normalize_provider(delivery.get("provider")),
                        "status": str(delivery.get("status") or "").strip(),
                        "recorded_at": str(delivery.get("recorded_at") or "").strip(),
                        "size_bytes": size_bytes,
                        "url": str(delivery.get("url") or "").strip(),
                        "expires_at": str(delivery.get("expires_at") or "").strip(),
                        "object_key": str(delivery.get("object_key") or "").strip(),
                        "bucket": str(delivery.get("bucket") or "").strip(),
                    }
                )

        runs.append(
            {
                "thread_id": str(payload.get("thread_id") or result_payload.get("thread_id") or "").strip(),
                "task_id": str(payload.get("task_id") or result_payload.get("task_id") or "").strip(),
                "backend": str(result_payload.get("backend") or "").strip(),
                "status": str(result_payload.get("status") or "").strip(),
                "finished_at": str(result_payload.get("finished_at") or "").strip(),
                "run_dir": str(run_dir),
                "index_path": str(index_path),
                "delivery_count": len(deliveries),
                "deliveries": deliveries,
            }
        )

    return _sort_runs_descending(runs)


def build_external_delivery_window_report(
    task_root: str | Path,
    *,
    limit_runs: int | None = None,
    owner_preference: str | None = None,
    file_surface_limit_bytes: int = SINGLE_FILE_UPLOAD_LIMIT_BYTES,
) -> dict[str, Any]:
    """Build a JSON-friendly report for recent external-delivery evidence."""

    if limit_runs is not None and limit_runs < 0:
        raise ValueError("limit_runs must be >= 0 when provided")

    normalized_preference = str(owner_preference or "").strip().lower() or None
    if normalized_preference not in {None, "auto", "cos", "file_surface"}:
        raise ValueError("owner_preference must be one of None, 'auto', 'cos', or 'file_surface'")
    if normalized_preference == "auto":
        normalized_preference = None

    runs = collect_external_delivery_runs(task_root)
    reported_runs = runs[:limit_runs] if limit_runs is not None else runs

    provider_counts: dict[str, int] = {}
    expectation_mismatches: list[dict[str, Any]] = []
    oversize_delivery_count = 0

    for run in reported_runs:
        for delivery in run["deliveries"]:
            provider = str(delivery.get("provider") or "").strip() or "unknown"
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            size_bytes = delivery.get("size_bytes")
            oversize_for_file_surface = isinstance(size_bytes, int) and size_bytes > file_surface_limit_bytes
            if oversize_for_file_surface:
                oversize_delivery_count += 1
            delivery["oversize_for_file_surface"] = oversize_for_file_surface if isinstance(size_bytes, int) else None

            expected_provider: str | None = None
            if normalized_preference == "file_surface" and isinstance(size_bytes, int):
                expected_provider = "cos" if oversize_for_file_surface else "file_surface"
            elif normalized_preference == "cos":
                expected_provider = "cos"

            delivery["expected_provider"] = expected_provider
            if expected_provider is None:
                delivery["matches_expectation"] = None
                continue

            matches_expectation = provider == expected_provider
            delivery["matches_expectation"] = matches_expectation
            if not matches_expectation:
                expectation_mismatches.append(
                    {
                        "thread_id": run["thread_id"],
                        "task_id": run["task_id"],
                        "artifact_id": delivery["artifact_id"],
                        "artifact_name": delivery["artifact_name"],
                        "provider": provider,
                        "expected_provider": expected_provider,
                        "size_bytes": size_bytes,
                        "run_dir": run["run_dir"],
                        "index_path": run["index_path"],
                    }
                )

    return {
        "task_root": str(Path(task_root).resolve()),
        "owner_preference": normalized_preference,
        "file_surface_limit_bytes": file_surface_limit_bytes,
        "scanned_runs": len(runs),
        "reported_runs": len(reported_runs),
        "delivery_count": sum(int(run["delivery_count"]) for run in reported_runs),
        "oversize_delivery_count": oversize_delivery_count,
        "provider_counts": provider_counts,
        "expectation_mismatch_count": len(expectation_mismatches),
        "expectation_mismatches": expectation_mismatches,
        "runs": reported_runs,
    }
