"""Thread store tests for Phase 1 local persistence."""

from __future__ import annotations

from mail_runner.models import MailEnvelope
from mail_runner.status import BACKEND_OPENCODE, THREAD_STATUS_ACCEPTED
from mail_runner.thread_store import create_thread, load_thread_state, resolve_thread, save_raw_mail


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

    assert resolve_thread(exact_match, task_root) == "thread_001"
    assert resolve_thread(reply_like, task_root) == "thread_001"
    assert resolve_thread(accepted_reply, task_root) == "thread_001"
    assert resolve_thread(capsule_reply, task_root, capsule_state={"thread_id": "thread_001"}) == "thread_001"
