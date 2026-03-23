"""Resolve outgoing run artifacts, persist artifact indexes, and project to mail attachments."""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .file_surface import ARTIFACT_FILE_BINDING_INDEX_FILENAME
from .models import OutgoingAttachment, RunArtifact, RunResult, ThreadState

_IMAGE_MIME_PREFIX = "image/"
_MANIFEST_FILENAME = "manifest.json"
_ARTIFACT_INDEX_FILENAME = "artifact_index.json"
_ARTIFACT_ID_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class _ResolvedArtifacts:
    artifacts_root: Path
    source: str
    artifacts: list[RunArtifact]
    skipped: list[str]


def _resolve_artifacts_root(task_root: Path, result: RunResult) -> Path | None:
    if result.artifacts_dir:
        candidate = Path(result.artifacts_dir)
        if candidate.is_absolute():
            return candidate
        return task_root / result.thread_id / candidate
    default_dir = task_root / result.thread_id / "runs" / result.task_id / "artifacts"
    if default_dir.exists():
        return default_dir
    return None


def _guess_content_type(path: Path, declared: str | None = None) -> str:
    if declared and "/" in declared:
        return declared
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _artifact_kind(content_type: str) -> str:
    if content_type.startswith(_IMAGE_MIME_PREFIX):
        return "image"
    return "file"


def _normalize_artifact_id(raw_value: str) -> str:
    normalized = _ARTIFACT_ID_RE.sub("-", raw_value.strip().lower()).strip("-")
    return normalized or "artifact"


def _allocate_artifact_id(raw_value: str, used_ids: set[str]) -> str:
    base_id = _normalize_artifact_id(raw_value)
    candidate = base_id
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _artifact_id_seed(path: Path, payload: dict[str, object], fallback_prefix: str) -> str:
    declared = str(payload.get("artifact_id") or "").strip()
    if declared:
        return declared
    raw_name = str(payload.get("name") or "").strip()
    name = Path(raw_name).stem if raw_name else (path.stem or fallback_prefix)
    return f"artifact-{name}"


def _build_run_artifact(
    path: Path,
    payload: dict[str, object],
    *,
    source: str,
    used_ids: set[str],
    default_inline_preview: bool,
) -> RunArtifact:
    content_type = _guess_content_type(path, str(payload.get("mime") or "").strip() or None)
    kind = _artifact_kind(content_type)
    inline_requested = bool(payload.get("inline", default_inline_preview))
    return RunArtifact(
        artifact_id=_allocate_artifact_id(_artifact_id_seed(path, payload, "artifact"), used_ids),
        path=str(path),
        name=str(payload.get("name") or "").strip() or path.name,
        kind=kind,
        content_type=content_type,
        source=source,
        attach=bool(payload.get("attach", True)),
        inline_preview=inline_requested and kind == "image",
        caption=str(payload.get("caption") or "").strip() or None,
    )


def _resolve_manifest_item(
    artifacts_root: Path,
    item: object,
    *,
    used_ids: set[str],
) -> tuple[RunArtifact | None, str | None]:
    if not isinstance(item, dict):
        return None, "Skipped manifest item because it is not an object."

    raw_path = str(item.get("path") or "").strip()
    if not raw_path:
        return None, "Skipped manifest item because path is empty."

    candidate = Path(raw_path)
    path = candidate if candidate.is_absolute() else (artifacts_root / candidate)
    if not path.exists():
        return None, f"Skipped attachment because file does not exist: {path}"
    if not path.is_file():
        return None, f"Skipped attachment because path is not a file: {path}"

    return _build_run_artifact(
        path,
        item,
        source="manifest",
        used_ids=used_ids,
        default_inline_preview=False,
    ), None


def _resolve_manifest(artifacts_root: Path, manifest_path: Path) -> _ResolvedArtifacts:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return _ResolvedArtifacts(
            artifacts_root=artifacts_root,
            source="manifest",
            artifacts=[],
            skipped=[f"Skipped manifest because it could not be parsed: {exc}"],
        )

    items = payload.get("items")
    if not isinstance(items, list):
        return _ResolvedArtifacts(
            artifacts_root=artifacts_root,
            source="manifest",
            artifacts=[],
            skipped=["Skipped manifest because items is missing or invalid."],
        )

    used_ids: set[str] = set()
    artifacts: list[RunArtifact] = []
    skipped: list[str] = []
    for item in items:
        resolved, message = _resolve_manifest_item(artifacts_root, item, used_ids=used_ids)
        if resolved is not None:
            artifacts.append(resolved)
        elif message:
            skipped.append(message)
    return _ResolvedArtifacts(artifacts_root=artifacts_root, source="manifest", artifacts=artifacts, skipped=skipped)


def _resolve_directory_fallback(artifacts_root: Path) -> _ResolvedArtifacts:
    artifacts: list[RunArtifact] = []
    used_ids: set[str] = set()
    for path in sorted(artifacts_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {_MANIFEST_FILENAME, _ARTIFACT_INDEX_FILENAME, ARTIFACT_FILE_BINDING_INDEX_FILENAME}:
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        artifacts.append(
            _build_run_artifact(
                path,
                {},
                source="directory_fallback",
                used_ids=used_ids,
                default_inline_preview=True,
            )
        )
    return _ResolvedArtifacts(artifacts_root=artifacts_root, source="directory_fallback", artifacts=artifacts, skipped=[])


def _resolve_run_artifacts_details(task_root: str | Path, result: RunResult | None) -> _ResolvedArtifacts | None:
    if result is None:
        return None

    artifacts_root = _resolve_artifacts_root(Path(task_root), result)
    if artifacts_root is None or not artifacts_root.exists():
        return None

    manifest_path = artifacts_root / _MANIFEST_FILENAME
    if manifest_path.exists():
        return _resolve_manifest(artifacts_root, manifest_path)

    return _resolve_directory_fallback(artifacts_root)


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


def resolve_run_artifacts(
    task_root: str | Path,
    state: ThreadState,
    result: RunResult | None,
) -> tuple[list[RunArtifact], list[str]]:
    del state
    resolved = _resolve_run_artifacts_details(task_root, result)
    if resolved is None:
        return [], []
    return list(resolved.artifacts), list(resolved.skipped)


def project_run_artifacts_to_outgoing_attachments(artifacts: list[RunArtifact]) -> list[OutgoingAttachment]:
    projected: list[OutgoingAttachment] = []
    for artifact in artifacts:
        projected.append(
            OutgoingAttachment(
                path=artifact.path,
                name=artifact.name,
                content_type=artifact.content_type,
                attach=artifact.attach,
                inline=artifact.inline_preview and artifact.kind == "image",
                caption=artifact.caption,
            )
        )
    return projected


def write_artifact_index(
    task_root: str | Path,
    result: RunResult | None,
    artifacts: list[RunArtifact],
    skipped: list[str],
) -> Path | None:
    resolved = _resolve_run_artifacts_details(task_root, result)
    if resolved is None or result is None:
        return None

    payload = {
        "version": 1,
        "task_id": result.task_id,
        "artifacts_root": _artifacts_root_label(task_root, result, resolved.artifacts_root),
        "source": resolved.source,
        "items": [asdict(item) for item in artifacts],
        "skipped": list(skipped),
    }
    target = resolved.artifacts_root / _ARTIFACT_INDEX_FILENAME
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def resolve_outgoing_attachments(
    task_root: str | Path,
    state: ThreadState,
    result: RunResult | None,
) -> tuple[list[OutgoingAttachment], list[str]]:
    artifacts, skipped = resolve_run_artifacts(task_root, state, result)
    return project_run_artifacts_to_outgoing_attachments(artifacts), skipped
