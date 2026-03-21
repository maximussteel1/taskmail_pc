"""Thread store tests for Phase 1 local persistence."""

from __future__ import annotations

from mail_runner.models import MailAttachment, MailEnvelope
from mail_runner.status import BACKEND_OPENCODE, THREAD_STATUS_ACCEPTED
from mail_runner.thread_store import (
    build_workspace_id,
    create_thread,
    find_session,
    find_thread_for_workspace_session,
    list_all_thread_states,
    load_thread_state,
    load_workspace_state,
    resolve_thread,
    save_raw_mail,
)


def _create_thread(task_root, thread_id: str | None = None):
    return create_thread(
        thread_id=thread_id,
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="local-demo",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        task_root=task_root,
        status=THREAD_STATUS_ACCEPTED,
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T11:20:00",
        updated_at="2026-03-12T11:20:00",
    )


def test_create_thread_assigns_sequential_ids(tmp_path) -> None:
    task_root = tmp_path / "tasks"

    first = _create_thread(task_root)
    second = _create_thread(task_root)

    assert first.thread_id == "thread_001"
    assert second.thread_id == "thread_002"


def test_load_thread_state_and_save_raw_mail(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    created = _create_thread(task_root, "thread_010")

    loaded = load_thread_state("thread_010", task_root)
    raw_one = save_raw_mail("thread_010", {"message_id": "<m1@example.com>"}, task_root)
    raw_two = save_raw_mail("thread_010", {"message_id": "<m2@example.com>"}, task_root)

    assert loaded == created
    assert raw_one.name == "raw_001.json"
    assert raw_two.name == "raw_002.json"

    workspace_id = build_workspace_id("D:\\repo", "src")
    workspace_state = load_workspace_state(workspace_id, task_root)
    session_state = find_session("D:\\repo", "src", "local-demo", task_root)

    assert workspace_state.session_ids == ["thread_010"]
    assert workspace_state.active_session_ids == []
    assert workspace_state.active_session_id is None
    assert workspace_state.queued_session_ids == ["thread_010"]
    assert session_state is not None
    assert session_state.thread_id == "thread_010"
    assert session_state.lifecycle == "active"
    assert session_state.last_active_at == "2026-03-12T11:20:00"
    assert session_state.last_progress_at == "2026-03-12T11:20:00"
    assert session_state.pending_task_count == 0
    assert find_thread_for_workspace_session("D:\\repo", "src", "local-demo", task_root) == "thread_010"
    assert [item.thread_id for item in list_all_thread_states(task_root)] == ["thread_010"]


def test_resolve_thread_matches_reply_headers_and_capsule(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _create_thread(task_root, "thread_001")
    save_raw_mail("thread_001", {"message_id": "<accepted@example.com>"}, task_root)

    exact_match = MailEnvelope(
        message_id="<root@example.com>",
        subject="Re: anything",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T11:20:00",
    )
    reply_like = MailEnvelope(
        message_id="<reply@example.com>",
        subject="Re: anything",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T11:21:00",
        in_reply_to="<root@example.com>",
        references=["<root@example.com>"],
    )
    accepted_reply = MailEnvelope(
        message_id="<reply-accepted@example.com>",
        subject="Re: [DONE] Local Demo",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T11:22:00",
        in_reply_to="<accepted@example.com>",
        references=["<root@example.com>", "<accepted@example.com>"],
    )
    capsule_reply = MailEnvelope(
        message_id="<reply-capsule@example.com>",
        subject="Something else",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T11:23:00",
        body_text="---TASK-STATE-BEGIN---\nthread_id: thread_001\n---TASK-STATE-END---",
    )
    tagged_reply = MailEnvelope(
        message_id="<reply-tagged@example.com>",
        subject="Re: [DONE][S:thread_001] Local Demo",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T11:24:00",
    )
    subject_only_reply = MailEnvelope(
        message_id="<reply-subject-only@example.com>",
        subject="Re: [DONE] Local Demo",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-12T11:25:00",
    )

    assert resolve_thread(exact_match, task_root) == "thread_001"
    assert resolve_thread(reply_like, task_root) == "thread_001"
    assert resolve_thread(accepted_reply, task_root) == "thread_001"
    assert resolve_thread(capsule_reply, task_root, capsule_state={"thread_id": "thread_001"}) == "thread_001"
    assert resolve_thread(tagged_reply, task_root) == "thread_001"
    assert resolve_thread(subject_only_reply, task_root) is None


def test_find_thread_for_workspace_session_separates_workspaces(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    create_thread(
        thread_id="thread_001",
        root_message_id="<root-1@example.com>",
        latest_message_id="<latest-1@example.com>",
        subject_norm="demo task",
        session_name="Demo task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo-one",
        workdir="src",
        current_task_id="task_001",
        last_task_snapshot_file="snapshots/task_001.json",
        task_root=task_root,
        status=THREAD_STATUS_ACCEPTED,
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T11:20:00",
        updated_at="2026-03-12T11:20:00",
    )
    create_thread(
        thread_id="thread_002",
        root_message_id="<root-2@example.com>",
        latest_message_id="<latest-2@example.com>",
        subject_norm="other task",
        session_name="Other task",
        backend=BACKEND_OPENCODE,
        profile=None,
        repo_path="D:\\repo-two",
        workdir="src",
        current_task_id="task_002",
        last_task_snapshot_file="snapshots/task_002.json",
        task_root=task_root,
        status=THREAD_STATUS_ACCEPTED,
        history_files=[],
        last_summary=None,
        created_at="2026-03-12T11:21:00",
        updated_at="2026-03-12T11:21:00",
    )

    assert find_thread_for_workspace_session("D:\\repo-one", "src", "Demo task", task_root) == "thread_001"
    assert find_thread_for_workspace_session("D:\\repo-two", "src", "Demo task", task_root) is None


def test_save_raw_mail_persists_attachment_payloads(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root@example.com>",
        subject="[OC] Demo",
        from_addr="user@example.com",
        to_addr="runner@example.com",
        date="2026-03-14T10:00:00",
        body_text="See attachment",
        attachments=[
            MailAttachment(
                filename="photo.png",
                content_type="image/png",
                size_bytes=4,
                saved_path="E:\\repo\\_mailin_20260314_001__photo.png",
                content_bytes=b"png!",
            )
        ],
    )

    raw_path = save_raw_mail("thread_001", envelope, tmp_path / "tasks")

    assert "photo.png" in raw_path.read_text(encoding="utf-8")
    attachment_dir = raw_path.with_name("raw_001_attachments")
    assert (attachment_dir / "001_photo.png").read_bytes() == b"png!"


def test_thread_store_defaults_to_mail_runner_task_root_env(tmp_path, monkeypatch) -> None:
    task_root = tmp_path / "tasks_env"
    created = _create_thread(task_root, "thread_011")
    monkeypatch.setenv("MAIL_RUNNER_TASK_ROOT", str(task_root))

    loaded = load_thread_state("thread_011")

    assert loaded == created
