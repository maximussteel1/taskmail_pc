"""Outgoing artifact resolution tests."""

from __future__ import annotations

import json

from mail_runner.artifact_resolver import (
    project_run_artifacts_to_outgoing_attachments,
    resolve_outgoing_attachments,
    resolve_run_artifacts,
    write_artifact_index,
)
from mail_runner.models import RunResult, ThreadState
from mail_runner.status import BACKEND_OPENCODE, RUN_STATUS_SUCCESS, THREAD_STATUS_DONE


def _state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        created_at="2026-03-14T10:00:00",
        updated_at="2026-03-14T10:01:00",
    )


def _result() -> RunResult:
    return RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-14T10:00:00",
        finished_at="2026-03-14T10:01:00",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir="runs/task_001/artifacts",
        changed_files=[],
        tests_passed=True,
        error_message=None,
    )


def test_resolve_outgoing_attachments_reads_manifest_with_absolute_paths(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    image_file = tmp_path / "preview.png"
    report_file = tmp_path / "report.md"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    report_file.write_text("# report\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "items": [
            {
                "path": str(image_file),
                "name": "preview.png",
                "mime": "image/png",
                "attach": True,
                "inline": True,
                "caption": "Preview",
            },
            {
                "path": str(report_file),
                "name": "report.md",
                "mime": "text/markdown",
                "attach": True,
                "inline": False,
            },
        ],
    }
    (artifacts_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    attachments, skipped = resolve_outgoing_attachments(task_root, _state(), _result())

    assert skipped == []
    assert [item.name for item in attachments] == ["preview.png", "report.md"]
    assert attachments[0].inline is True
    assert attachments[0].attach is True
    assert attachments[1].inline is False


def test_resolve_run_artifacts_exposes_metadata_for_manifest_items(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    image_file = tmp_path / "preview.png"
    report_file = tmp_path / "report.md"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    report_file.write_text("# report\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "items": [
            {
                "path": str(image_file),
                "name": "preview.png",
                "mime": "image/png",
                "attach": True,
                "inline": True,
                "caption": "Preview",
            },
            {
                "path": str(report_file),
                "name": "report.md",
                "mime": "text/markdown",
                "attach": True,
                "inline": False,
            },
        ],
    }
    (artifacts_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    artifacts, skipped = resolve_run_artifacts(task_root, _state(), _result())
    projected = project_run_artifacts_to_outgoing_attachments(artifacts)

    assert skipped == []
    assert [item.artifact_id for item in artifacts] == ["artifact-preview", "artifact-report"]
    assert [item.kind for item in artifacts] == ["image", "file"]
    assert artifacts[0].inline_preview is True
    assert artifacts[1].inline_preview is False
    assert projected[0].inline is True
    assert projected[1].inline is False


def test_resolve_outgoing_attachments_falls_back_to_artifacts_directory(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (artifacts_dir / "notes.txt").write_text("hello", encoding="utf-8")

    attachments, skipped = resolve_outgoing_attachments(task_root, _state(), _result())

    assert skipped == []
    assert [item.name for item in attachments] == ["chart.png", "notes.txt"]
    assert attachments[0].inline is True
    assert attachments[1].inline is False


def test_resolve_outgoing_attachments_ignores_generated_artifact_index_file(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (artifacts_dir / "artifact_index.json").write_text("{}", encoding="utf-8")
    (artifacts_dir / "artifact_file_binding_index.json").write_text("{}", encoding="utf-8")

    attachments, skipped = resolve_outgoing_attachments(task_root, _state(), _result())

    assert skipped == []
    assert [item.name for item in attachments] == ["chart.png"]


def test_resolve_outgoing_attachments_reports_missing_manifest_files(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    manifest = {
        "version": 1,
        "items": [
            {
                "path": str(tmp_path / "missing.png"),
                "name": "missing.png",
                "mime": "image/png",
                "attach": True,
                "inline": True,
            }
        ],
    }
    (artifacts_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    attachments, skipped = resolve_outgoing_attachments(task_root, _state(), _result())

    assert attachments == []
    assert len(skipped) == 1
    assert "does not exist" in skipped[0]


def test_resolve_outgoing_attachments_accepts_utf8_bom_manifest(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    image_file = tmp_path / "preview.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    manifest = {
        "version": 1,
        "items": [
            {
                "path": str(image_file),
                "name": "preview.png",
                "mime": "image/png",
                "attach": True,
                "inline": True,
            }
        ],
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    (artifacts_dir / "manifest.json").write_text(manifest_text, encoding="utf-8-sig")

    attachments, skipped = resolve_outgoing_attachments(task_root, _state(), _result())

    assert skipped == []
    assert len(attachments) == 1
    assert attachments[0].name == "preview.png"
    assert attachments[0].inline is True


def test_write_artifact_index_persists_resolved_items_and_skipped_messages(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    artifacts_dir = task_root / "thread_001" / "runs" / "task_001" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    image_file = tmp_path / "preview.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    manifest = {
        "version": 1,
        "items": [
            {
                "path": str(image_file),
                "name": "preview.png",
                "mime": "image/png",
                "attach": True,
                "inline": True,
            },
            {
                "path": str(tmp_path / "missing.txt"),
                "name": "missing.txt",
                "mime": "text/plain",
                "attach": True,
                "inline": False,
            },
        ],
    }
    (artifacts_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    artifacts, skipped = resolve_run_artifacts(task_root, _state(), _result())
    index_path = write_artifact_index(task_root, _result(), artifacts, skipped)

    assert index_path is not None
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["task_id"] == "task_001"
    assert payload["artifacts_root"] == "runs/task_001/artifacts"
    assert payload["source"] == "manifest"
    assert payload["items"][0]["artifact_id"] == "artifact-preview"
    assert payload["items"][0]["kind"] == "image"
    assert payload["items"][0]["inline_preview"] is True
    assert len(payload["skipped"]) == 1
    assert "does not exist" in payload["skipped"][0]
