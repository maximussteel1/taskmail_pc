"""Reporter tests."""

from __future__ import annotations

from mail_runner.models import (
    ExternalDelivery,
    OutgoingAttachment,
    QuestionAnswer,
    QuestionItem,
    RunArtifact,
    RunResult,
    TaskSnapshot,
    ThreadState,
)
from mail_runner.reporter import (
    MAIL_STATUS_DONE,
    MAIL_STATUS_FAILED,
    MAIL_STATUS_PAUSED,
    MAIL_STATUS_QUESTION,
    build_status_html,
    build_status_markdown,
    build_status_mail,
    build_status_subject,
    render_status_markdown_to_plain_text,
)
from mail_runner.status import (
    BACKEND_OPENCODE,
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    THREAD_STATUS_DONE,
    THREAD_STATUS_FAILED,
    THREAD_STATUS_PAUSED,
)


def test_build_status_subject_and_mail_include_capsule() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        last_summary="Completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    result = RunResult(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_SUCCESS,
        exit_code=0,
        started_at="2026-03-12T12:00:01",
        finished_at="2026-03-12T12:00:05",
        stdout_file="runs/task_001/stdout.log",
        stderr_file="runs/task_001/stderr.log",
        summary_file="runs/task_001/summary.md",
        artifacts_dir=None,
        changed_files=["src/main.py"],
        tests_passed=True,
        error_message=None,
    )

    subject = build_status_subject(MAIL_STATUS_DONE, "Demo task", "thread_001")
    body = build_status_mail(
        MAIL_STATUS_DONE,
        state,
        task_snapshot=snapshot,
        result=result,
        captured_reply="OpenCode raw reply text.",
    )

    assert subject == "[DONE][S:thread_001] Demo task"
    assert "Status: DONE" in body
    assert "Session ID: thread_001" in body
    assert "Permission: default" in body
    assert "Tests Passed: yes" in body
    assert "Changed Files: src/main.py" in body
    assert "Reply:\nOpenCode raw reply text." in body
    assert "Summary: Completed successfully." not in body
    assert "---TASK-STATE-BEGIN---" in body
    assert "task_id: task_001" in body


def test_build_failed_status_mail_includes_user_error_message() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_002",
        last_task_snapshot_file="snapshots/task_002.json",
        status=THREAD_STATUS_FAILED,
        history_files=["runs/task_002/result.json"],
        last_summary="attempt to write a readonly database",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:10:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_002",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect",
        acceptance=[],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    result = RunResult(
        task_id="task_002",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        status=RUN_STATUS_FAILED,
        exit_code=1,
        started_at="2026-03-12T12:05:00",
        finished_at="2026-03-12T12:10:00",
        stdout_file="runs/task_002/stdout.log",
        stderr_file="runs/task_002/stderr.log",
        summary_file="runs/task_002/summary.md",
        artifacts_dir=None,
        changed_files=[],
        tests_passed=None,
        error_type="validation_error",
        error_message="attempt to write a readonly database",
    )

    body = build_status_mail(
        MAIL_STATUS_FAILED,
        state,
        task_snapshot=snapshot,
        result=result,
        captured_reply="stderr excerpt",
    )

    assert "Status: FAILED" in body
    assert "Permission: default" in body
    assert "Error Type: validation_error" in body
    assert "Error: attempt to write a readonly database" in body
    assert "Reply:\nstderr excerpt" in body


def test_build_question_mail_includes_question_capsule() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<question@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_003",
        last_task_snapshot_file="snapshots/task_003.json",
        status="awaiting_user_input",
        history_files=["runs/task_003/result.json"],
        last_summary="Should I update both files?",
        pending_question_id="question_task_003",
        pending_question_text="Should I update both files?",
        pending_choices=["yes", "no"],
        awaiting_since="2026-03-12T12:15:00",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:15:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_003",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect",
        acceptance=[],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    body = build_status_mail(MAIL_STATUS_QUESTION, state, task_snapshot=snapshot)

    assert "Status: QUESTION" in body
    assert "Question: Should I update both files?" in body
    assert "---TASK-QUESTION-BEGIN---" in body


def test_build_question_mail_includes_multi_question_template() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<question@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_003",
        last_task_snapshot_file="snapshots/task_003.json",
        status="awaiting_user_input",
        history_files=["runs/task_003/result.json"],
        pending_question_set_id="phase2",
        pending_questions=[
            QuestionItem(
                question_set_id="phase2",
                question_id="phase2_entry_position",
                question_type="single_choice",
                question_text="Where should the entry go?",
                choices=["top", "below"],
                choice_labels={"top": "Top", "below": "Below"},
            ),
            QuestionItem(
                question_set_id="phase2",
                question_id="phase2_icon_strings",
                question_type="single_choice",
                question_text="Who provides strings?",
                choices=["provide", "reuse"],
                choice_labels={"provide": "You provide", "reuse": "Reuse existing"},
            ),
        ],
        collected_answers=[
            QuestionAnswer(
                question_id="phase2_entry_position",
                value="below",
                raw_value="Below",
            )
        ],
        awaiting_since="2026-03-12T12:15:00",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:15:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_003",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect",
        acceptance=[],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    body = build_status_mail(MAIL_STATUS_QUESTION, state, task_snapshot=snapshot)

    assert "Question Set ID: phase2" in body
    assert "Received Answers:" in body
    assert "Answers:" in body
    assert "phase2_icon_strings:" in body
    assert "Labels: top=Top | below=Below" in body


def test_build_paused_mail_includes_paused_source_status() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<paused@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_004",
        last_task_snapshot_file="snapshots/task_004.json",
        status=THREAD_STATUS_PAUSED,
        paused_from_status="done",
        history_files=["runs/task_004/result.json"],
        last_summary="Completed successfully.",
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:20:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_004",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Inspect",
        acceptance=[],
        timeout_minutes=60,
        mode="analysis_only",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )

    body = build_status_mail(MAIL_STATUS_PAUSED, state, task_snapshot=snapshot)

    assert "Status: PAUSED" in body
    assert "Paused From: done" in body
    assert "Summary: Completed successfully." in body


def test_build_status_html_includes_inline_image_preview_and_notices() -> None:
    attachments = [
        OutgoingAttachment(
            path="E:\\repo\\preview.png",
            name="preview.png",
            content_type="image/png",
            attach=True,
            inline=True,
            caption="Preview image",
        )
    ]

    html = build_status_html("Status: DONE\nReply:\nRendered.", attachments, ["Skipped attachment because missing"])

    assert "cid:mail-runner-inline-1" in html
    assert "Preview image" in html
    assert "Skipped attachment because missing" in html


def test_build_status_markdown_includes_artifact_links_and_image_refs() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        permission="highest",
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    artifacts = [
        RunArtifact(
            artifact_id="artifact-preview",
            path="E:\\repo\\preview.png",
            name="preview.png",
            kind="image",
            content_type="image/png",
            source="directory_fallback",
            attach=True,
            inline_preview=True,
            caption="Preview image",
        ),
        RunArtifact(
            artifact_id="artifact-report",
            path="E:\\repo\\report.md",
            name="report.md",
            kind="file",
            content_type="text/markdown",
            source="directory_fallback",
            attach=True,
            inline_preview=False,
            caption="Final report",
        ),
    ]

    markdown_body = build_status_markdown(
        MAIL_STATUS_DONE,
        state,
        task_snapshot=snapshot,
        artifacts=artifacts,
        skipped_messages=["Skipped attachment because missing"],
    )
    plain_body = render_status_markdown_to_plain_text(markdown_body)

    assert "## Artifacts" in markdown_body
    assert "Permission: highest" in markdown_body
    assert markdown_body.count("## Artifacts") == 1
    assert "## Images" not in markdown_body
    assert "## Files" not in markdown_body
    assert "- [preview.png](artifact://artifact-preview): Preview image (inline preview)" in markdown_body
    assert "![Preview image](artifact://artifact-preview)" in markdown_body
    assert "- [report.md](artifact://artifact-report): Final report" in markdown_body
    assert "## Attachment Notices" in markdown_body
    assert markdown_body.index("## Artifacts") < markdown_body.index("## Attachment Notices")
    assert "Artifacts:" in plain_body
    assert plain_body.count("Artifacts:") == 1
    assert "- preview.png: Preview image (inline preview)" in plain_body
    assert "- report.md: Final report" in plain_body
    assert "[Image Preview] Preview image" in plain_body
    assert "Attachment Notices:" in plain_body
    assert plain_body.index("Artifacts:") < plain_body.index("Attachment Notices:")


def test_build_status_html_projects_markdown_artifact_image_refs_to_cid() -> None:
    markdown_body = "\n".join(
        [
            "Status: DONE",
            "",
            "## Artifacts",
            "",
            "- [preview.png](artifact://artifact-preview): Preview image (inline preview)",
            "![Preview image](artifact://artifact-preview)",
            "",
            "## Attachment Notices",
            "",
            "- Skipped attachment because missing",
        ]
    )
    artifacts = [
        RunArtifact(
            artifact_id="artifact-preview",
            path="E:\\repo\\preview.png",
            name="preview.png",
            kind="image",
            content_type="image/png",
            source="directory_fallback",
            attach=True,
            inline_preview=True,
            caption="Preview image",
        )
    ]
    attachments = [
        OutgoingAttachment(
            path="E:\\repo\\preview.png",
            name="preview.png",
            content_type="image/png",
            attach=True,
            inline=True,
            caption="Preview image",
        )
    ]

    plain_body = render_status_markdown_to_plain_text(markdown_body)
    html = build_status_html(
        plain_body,
        attachments,
        markdown_body=markdown_body,
        artifacts=artifacts,
    )

    assert "<h2>Artifacts</h2>" in html
    assert html.count("<h2>Artifacts</h2>") == 1
    assert "<h2>Images</h2>" not in html
    assert "<h2>Files</h2>" not in html
    assert "<li>preview.png: Preview image (inline preview)</li>" in html
    assert "<h2>Attachment Notices</h2>" in html
    assert html.index("<h2>Artifacts</h2>") < html.index("<h2>Attachment Notices</h2>")
    assert "<li>Skipped attachment because missing</li>" in html
    assert "cid:mail-runner-inline-1" in html
    assert "Preview image" in html


def test_build_status_markdown_includes_external_delivery_links() -> None:
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<root@example.com>",
        subject_norm="demo",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        status=THREAD_STATUS_DONE,
        history_files=["runs/task_001/result.json"],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:05",
    )
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend=BACKEND_OPENCODE,
        repo_path="D:\\repo",
        workdir="src",
        task_text="Refactor",
        acceptance=[],
        timeout_minutes=60,
        mode="modify",
        attachments=[],
        created_at="2026-03-12T12:00:00",
        updated_at="2026-03-12T12:00:00",
    )
    deliveries = [
        ExternalDelivery(
            artifact_id="artifact-apk",
            name="app.apk",
            provider="cos",
            url="https://cos.example/mail-runner/thread_001/task_001/app.apk",
            expires_at="2026-03-24T12:00:00+00:00",
            object_key="mail-runner/thread_001/task_001/app.apk",
            size_bytes=44 * 1024 * 1024,
            content_type="application/vnd.android.package-archive",
            bucket="mailbot-1412015279",
            path="E:\\repo\\app.apk",
        )
    ]

    markdown_body = build_status_markdown(
        MAIL_STATUS_DONE,
        state,
        task_snapshot=snapshot,
        external_deliveries=deliveries,
    )
    plain_body = render_status_markdown_to_plain_text(markdown_body)
    html = build_status_html(plain_body, markdown_body=markdown_body, artifacts=[])

    assert "## External Deliveries" in markdown_body
    assert "[app.apk](https://cos.example/mail-runner/thread_001/task_001/app.apk)" in markdown_body
    assert "External Deliveries:" in plain_body
    assert "app.apk: Delivered via COS" in plain_body
    assert " | https://cos.example/mail-runner/thread_001/task_001/app.apk" in plain_body
    assert '<h2>External Deliveries</h2>' in html
    assert '<a href="https://cos.example/mail-runner/thread_001/task_001/app.apk">app.apk</a>' in html
