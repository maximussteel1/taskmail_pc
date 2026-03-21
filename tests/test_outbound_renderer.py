from __future__ import annotations

from mail_runner.models import OutgoingAttachment, RunArtifact, TaskSnapshot, ThreadState
from mail_runner.outbound.renderer import render_status_mail
from mail_runner.reporter import (
    MAIL_STATUS_STATUS,
    build_status_html,
    build_status_markdown,
    build_status_subject,
    render_status_markdown_to_plain_text,
)
from mail_runner.status import THREAD_STATUS_DONE


def _state() -> ThreadState:
    return ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<done@example.com>",
        subject_norm="demo task",
        backend="opencode",
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        session_id="session_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        last_summary="Mock run completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )


def _snapshot() -> TaskSnapshot:
    return TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="opencode",
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect the module.",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )


def test_render_status_mail_matches_reporter_projections() -> None:
    state = _state()
    snapshot = _snapshot()
    artifacts = [
        RunArtifact(
            artifact_id="artifact-001",
            path="D:\\repo\\runs\\task_001\\chart.png",
            name="chart.png",
            kind="image",
            content_type="image/png",
            source="run",
            inline_preview=True,
            caption="Execution chart",
        )
    ]
    attachments = [
        OutgoingAttachment(
            path="D:\\repo\\runs\\task_001\\chart.png",
            name="chart.png",
            content_type="image/png",
            inline=True,
            content_id="chart-preview",
            caption="Execution chart",
        )
    ]
    skipped_messages = ["Skipped large log attachment; available in artifacts."]

    rendered = render_status_mail(
        status_label=MAIL_STATUS_STATUS,
        subject_text="Demo task",
        state=state,
        task_snapshot=snapshot,
        attachments=attachments,
        artifacts=artifacts,
        skipped_messages=skipped_messages,
        summary_override="Current local status only.",
    )

    expected_markdown = build_status_markdown(
        MAIL_STATUS_STATUS,
        state,
        task_snapshot=snapshot,
        artifacts=artifacts,
        skipped_messages=skipped_messages,
        summary_override="Current local status only.",
    )
    expected_plain = render_status_markdown_to_plain_text(expected_markdown)
    expected_html = build_status_html(
        expected_plain,
        attachments,
        skipped_messages,
        markdown_body=expected_markdown,
        artifacts=artifacts,
    )
    expected_subject = build_status_subject(MAIL_STATUS_STATUS, "Demo task", "session_001")

    assert rendered.subject == expected_subject
    assert rendered.plain_body == expected_plain
    assert rendered.html_body == expected_html
