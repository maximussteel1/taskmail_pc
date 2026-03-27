"""Helpers for summarizing recent external-delivery evidence across task runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .external_delivery_index import EXTERNAL_DELIVERY_INDEX_FILENAME, EXTERNAL_DELIVERY_INDEX_SCHEMA
from .file_surface import SINGLE_FILE_UPLOAD_LIMIT_BYTES
from .models import RunResult
from .pc_control_plane_projection import project_artifact_manifest

_SUPPORTED_EXTERNAL_DELIVERY_KINDS = {"image", "file"}


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
        projected_manifest: dict[str, Any] | None = None
        artifact_manifest_projection_error: str | None = None
        projected_items_by_artifact_id: dict[str, dict[str, Any]] = {}
        if result_payload:
            try:
                projected_manifest = project_artifact_manifest(task_root_path, result=RunResult(**result_payload))
            except Exception as exc:
                artifact_manifest_projection_error = f"{type(exc).__name__}: {exc}"
        if isinstance(projected_manifest, dict):
            manifest_items = projected_manifest.get("artifacts")
            if isinstance(manifest_items, list):
                for manifest_item in manifest_items:
                    if not isinstance(manifest_item, dict):
                        continue
                    artifact_id = str(manifest_item.get("artifact_id") or "").strip()
                    if artifact_id:
                        projected_items_by_artifact_id[artifact_id] = manifest_item
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
                artifact_id = str(item.get("artifact_id") or "").strip()
                size_bytes = delivery.get("size_bytes")
                if not isinstance(size_bytes, int):
                    fallback_size = item.get("byte_size")
                    size_bytes = fallback_size if isinstance(fallback_size, int) else None
                projected_item = projected_items_by_artifact_id.get(artifact_id)
                projected_download_ref = None
                projected_download_ref_source = None
                if projected_item is not None:
                    projected_download_ref = str(projected_item.get("download_ref") or "").strip() or None
                    projected_download_ref_source = str(projected_item.get("download_ref_source") or "").strip() or None
                deliveries.append(
                    {
                        "artifact_id": artifact_id,
                        "artifact_name": str(item.get("name") or "").strip(),
                        "kind": str(item.get("kind") or "").strip().lower(),
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
                        "projected_manifest_item_present": projected_item is not None,
                        "projected_download_ref": projected_download_ref,
                        "projected_download_ref_source": projected_download_ref_source,
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
                "artifact_manifest_present": projected_manifest is not None,
                "artifact_manifest_artifact_count": len(projected_items_by_artifact_id),
                "artifact_manifest_projection_error": artifact_manifest_projection_error,
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

    requested_preference = str(owner_preference or "").strip().lower() or None
    normalized_preference = requested_preference
    if normalized_preference not in {None, "auto", "cos", "file_surface"}:
        raise ValueError("owner_preference must be one of None, 'auto', 'cos', or 'file_surface'")
    if normalized_preference == "auto":
        normalized_preference = None

    runs = collect_external_delivery_runs(task_root)
    reported_runs = runs[:limit_runs] if limit_runs is not None else runs

    provider_counts: dict[str, int] = {}
    artifact_kind_counts: dict[str, int] = {}
    expectation_mismatches: list[dict[str, Any]] = []
    unsupported_kind_deliveries: list[dict[str, Any]] = []
    oversize_delivery_count = 0
    artifact_manifest_download_ref_source_counts: dict[str, int] = {}
    artifact_manifest_download_ref_source_mismatches: list[dict[str, Any]] = []
    artifact_manifest_missing_count = 0
    cos_delivery_count = 0

    for run in reported_runs:
        for delivery in run["deliveries"]:
            provider = str(delivery.get("provider") or "").strip() or "unknown"
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if provider == "cos":
                cos_delivery_count += 1
            kind = str(delivery.get("kind") or "").strip().lower() or "unknown"
            artifact_kind_counts[kind] = artifact_kind_counts.get(kind, 0) + 1
            if kind not in _SUPPORTED_EXTERNAL_DELIVERY_KINDS:
                unsupported_kind_deliveries.append(
                    {
                        "thread_id": run["thread_id"],
                        "task_id": run["task_id"],
                        "artifact_id": delivery["artifact_id"],
                        "artifact_name": delivery["artifact_name"],
                        "kind": kind,
                        "run_dir": run["run_dir"],
                        "index_path": run["index_path"],
                    }
                )
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
            else:
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

            expected_download_ref_source = f"external_delivery_index.{provider}" if provider != "unknown" else None
            projected_download_ref_source = delivery.get("projected_download_ref_source")
            if isinstance(projected_download_ref_source, str) and projected_download_ref_source:
                artifact_manifest_download_ref_source_counts[projected_download_ref_source] = (
                    artifact_manifest_download_ref_source_counts.get(projected_download_ref_source, 0) + 1
                )
            delivery["expected_download_ref_source"] = expected_download_ref_source
            delivery["projected_download_ref_matches_delivery_url"] = (
                delivery.get("projected_download_ref") == delivery.get("url")
                if delivery.get("projected_manifest_item_present")
                else None
            )
            if not delivery.get("projected_manifest_item_present"):
                artifact_manifest_missing_count += 1
            projected_source_matches = (
                projected_download_ref_source == expected_download_ref_source
                if expected_download_ref_source is not None and delivery.get("projected_manifest_item_present")
                else None
            )
            delivery["projected_download_ref_source_matches_expectation"] = projected_source_matches
            if expected_download_ref_source is not None and (
                not delivery.get("projected_manifest_item_present") or projected_download_ref_source != expected_download_ref_source
            ):
                artifact_manifest_download_ref_source_mismatches.append(
                    {
                        "thread_id": run["thread_id"],
                        "task_id": run["task_id"],
                        "artifact_id": delivery["artifact_id"],
                        "artifact_name": delivery["artifact_name"],
                        "provider": provider,
                        "expected_download_ref_source": expected_download_ref_source,
                        "projected_download_ref_source": projected_download_ref_source,
                        "projected_manifest_item_present": bool(delivery.get("projected_manifest_item_present")),
                        "run_dir": run["run_dir"],
                        "index_path": run["index_path"],
                    }
                )

    if normalized_preference is None:
        owner_lane_matches_expectation: bool | None = None
    else:
        owner_lane_matches_expectation = len(expectation_mismatches) == 0
    artifact_kinds_supported = len(unsupported_kind_deliveries) == 0
    artifact_manifest_present_for_deliveries = artifact_manifest_missing_count == 0
    artifact_manifest_download_ref_sources_match = len(artifact_manifest_download_ref_source_mismatches) == 0
    window_has_evidence = len(reported_runs) > 0
    cos_deliveries_are_oversize_only = all(
        delivery.get("provider") != "cos" or delivery.get("oversize_for_file_surface") is True
        for run in reported_runs
        for delivery in run["deliveries"]
    )
    owner_preference_is_file_surface = normalized_preference == "file_surface"
    legacy_auto_not_requested = requested_preference != "auto"
    window_has_no_cos_deliveries = cos_delivery_count == 0
    window_failures: list[str] = []
    if not window_has_evidence:
        window_failures.append("no external-delivery runs were found in the requested window")
    if owner_lane_matches_expectation is False:
        window_failures.append("provider selection does not match the configured owner-lane expectation")
    if not artifact_kinds_supported:
        window_failures.append("one or more delivered artifacts use a kind outside the current image|file contract")
    if not artifact_manifest_present_for_deliveries:
        window_failures.append("one or more delivered artifacts are missing candidate artifact_manifest projection")
    if not artifact_manifest_download_ref_sources_match:
        window_failures.append("artifact_manifest.download_ref_source does not match external_delivery_index evidence")
    if normalized_preference is None:
        window_ready = None
    else:
        window_ready = (
            window_has_evidence
            and owner_lane_matches_expectation is True
            and artifact_kinds_supported
            and artifact_manifest_present_for_deliveries
            and artifact_manifest_download_ref_sources_match
        )
    cos_decommission_failures: list[str] = []
    if not window_has_evidence:
        cos_decommission_failures.append("no external-delivery runs were found in the requested window")
    if not owner_preference_is_file_surface:
        cos_decommission_failures.append("owner preference is not explicitly file_surface")
    if not legacy_auto_not_requested:
        cos_decommission_failures.append("legacy auto preference is still requested")
    if owner_lane_matches_expectation is False:
        cos_decommission_failures.append("provider selection does not match the configured owner-lane expectation")
    if not artifact_kinds_supported:
        cos_decommission_failures.append("one or more delivered artifacts use a kind outside the current image|file contract")
    if not artifact_manifest_present_for_deliveries:
        cos_decommission_failures.append("one or more delivered artifacts are missing candidate artifact_manifest projection")
    if not artifact_manifest_download_ref_sources_match:
        cos_decommission_failures.append("artifact_manifest.download_ref_source does not match external_delivery_index evidence")
    if not cos_deliveries_are_oversize_only:
        cos_decommission_failures.append("one or more COS deliveries are not oversize-only compatibility samples")
    if not window_has_no_cos_deliveries:
        cos_decommission_failures.append("COS deliveries are still present in the requested observation window")
    cos_decommission_candidate = (
        window_has_evidence
        and owner_preference_is_file_surface
        and legacy_auto_not_requested
        and owner_lane_matches_expectation is True
        and artifact_kinds_supported
        and artifact_manifest_present_for_deliveries
        and artifact_manifest_download_ref_sources_match
        and cos_deliveries_are_oversize_only
        and window_has_no_cos_deliveries
    )

    return {
        "task_root": str(Path(task_root).resolve()),
        "owner_preference_input": requested_preference,
        "owner_preference": normalized_preference,
        "legacy_auto_requested": requested_preference == "auto",
        "file_surface_limit_bytes": file_surface_limit_bytes,
        "scanned_runs": len(runs),
        "reported_runs": len(reported_runs),
        "delivery_count": sum(int(run["delivery_count"]) for run in reported_runs),
        "oversize_delivery_count": oversize_delivery_count,
        "cos_delivery_count": cos_delivery_count,
        "provider_counts": provider_counts,
        "artifact_kind_counts": artifact_kind_counts,
        "unsupported_kind_count": len(unsupported_kind_deliveries),
        "unsupported_kind_deliveries": unsupported_kind_deliveries,
        "expectation_mismatch_count": len(expectation_mismatches),
        "expectation_mismatches": expectation_mismatches,
        "artifact_manifest_missing_count": artifact_manifest_missing_count,
        "artifact_manifest_download_ref_source_counts": artifact_manifest_download_ref_source_counts,
        "artifact_manifest_download_ref_source_mismatch_count": len(artifact_manifest_download_ref_source_mismatches),
        "artifact_manifest_download_ref_source_mismatches": artifact_manifest_download_ref_source_mismatches,
        "window_checks": {
            "window_has_evidence": window_has_evidence,
            "owner_lane_matches_expectation": owner_lane_matches_expectation,
            "artifact_kinds_supported": artifact_kinds_supported,
            "artifact_manifest_present_for_deliveries": artifact_manifest_present_for_deliveries,
            "artifact_manifest_download_ref_sources_match": artifact_manifest_download_ref_sources_match,
        },
        "window_ready": window_ready,
        "window_failures": window_failures,
        "cos_decommission_checks": {
            "owner_preference_is_file_surface": owner_preference_is_file_surface,
            "legacy_auto_not_requested": legacy_auto_not_requested,
            "window_has_no_cos_deliveries": window_has_no_cos_deliveries,
            "cos_deliveries_are_oversize_only": cos_deliveries_are_oversize_only,
            "owner_lane_matches_expectation": owner_lane_matches_expectation,
            "artifact_kinds_supported": artifact_kinds_supported,
            "artifact_manifest_present_for_deliveries": artifact_manifest_present_for_deliveries,
            "artifact_manifest_download_ref_sources_match": artifact_manifest_download_ref_sources_match,
        },
        "cos_decommission_candidate": cos_decommission_candidate,
        "cos_decommission_failures": cos_decommission_failures,
        "runs": reported_runs,
    }
