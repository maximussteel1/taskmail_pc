"""Phase 2 app integration tests."""

from __future__ import annotations

import json
import time
from pathlib import Path

from mail_runner.adapters.base import WorkerAdapter
from mail_runner.app import _process_batch, bootstrap, process_once
from mail_runner.config import AppConfig
from mail_runner.dispatcher import Dispatcher
from mail_runner.models import MailAttachment, MailEnvelope, RunResult, TaskSnapshot
from mail_runner.adapters.mock_adapter import MockAdapter
from mail_runner.runner import SerialTaskRunner
from mail_runner.status import RUN_STATUS_SUCCESS
from mail_runner.thread_store import build_workspace_id, load_thread_state, load_workspace_state


class FakeMailClient:
    def __init__(self, envelopes):
        self._envelopes = list(envelopes)
        self.sent_messages: list[dict] = []
        self.deleted_message_batches: list[list[str]] = []
        self._sent_count = 0

    def fetch_unseen_messages(self):
        return list(self._envelopes)

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        self._sent_count += 1
        return f"<sent-{self._sent_count}@example.com>"

    def delete_messages_by_message_ids(self, message_ids, mailbox="INBOX"):
        self.deleted_message_batches.append(list(message_ids))
        return list(message_ids)


def test_process_once_runs_new_task_happy_path(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="\n".join(
            [
                "Repo: D:\\repo",
                "Workdir: src",
                "",
                "Task:",
                "Refactor the module.",
                "",
                "Acceptance:",
                "1. pytest passes",
            ]
        ),
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    task_root = tmp_path / "tasks" / "thread_001"
    state = json.loads((task_root / "thread_state.json").read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Demo task",
        "[RUNNING][S:thread_001] Demo task",
        "[DONE][S:thread_001] Demo task",
    ]
    assert (task_root / "mail" / "raw_001.json").exists()
    assert state["status"] == "done"
    assert state["session_name"] == "Demo task"
    assert (task_root / "runs" / state["current_task_id"] / "result.json").exists()


def test_process_once_skips_unmatched_reply_mail(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<reply@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        in_reply_to="<root@example.com>",
        references=["<root@example.com>"],
        body_text="Repo: D:\\repo\nTask:\nRefactor the module.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 0, "skipped": 1, "failed": 0}
    assert client.sent_messages == []


def test_process_once_handles_project_folder_sync_without_creating_task(tmp_path) -> None:
    sync_root_a = tmp_path / "sync_a"
    sync_root_b = tmp_path / "sync_b"
    (sync_root_a / "alpha").mkdir(parents=True)
    (sync_root_a / "alpha" / "nested").mkdir(parents=True)
    (sync_root_a / "note.txt").write_text("ignore", encoding="utf-8")
    (sync_root_b / "beta").mkdir(parents=True)

    envelope = MailEnvelope(
        message_id="<sync@example.com>",
        subject="[SYNC]",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-16T08:00:00",
        body_text="",
        raw_headers={"Subject": "[SYNC]"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(
        from_addr="user@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        project_sync_roots=[str(sync_root_a), str(sync_root_b)],
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == ["[SYNC] Project Folder List"]
    assert client.sent_messages[0]["in_reply_to"] == "<sync@example.com>"
    assert "Project folder sync completed. No task was created." in client.sent_messages[0]["body"]
    assert f"- alpha | {sync_root_a / 'alpha'}" in client.sent_messages[0]["body"]
    assert f"- beta | {sync_root_b / 'beta'}" in client.sent_messages[0]["body"]
    assert str(sync_root_a / "alpha" / "nested") not in client.sent_messages[0]["body"]
    assert "note.txt" not in client.sent_messages[0]["body"]
    assert client.deleted_message_batches == []
    assert not (tmp_path / "tasks" / "thread_001").exists()


def test_process_once_reports_unavailable_project_sync_root(tmp_path) -> None:
    existing_root = tmp_path / "sync_existing"
    existing_root.mkdir()
    missing_root = tmp_path / "sync_missing"

    envelope = MailEnvelope(
        message_id="<sync-unavailable@example.com>",
        subject="[SYNC] anything",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-16T08:05:00",
        body_text="please sync",
        raw_headers={"Subject": "[SYNC] anything"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(
        from_addr="user@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        project_sync_roots=[str(existing_root), str(missing_root)],
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert f"- {missing_root} | unavailable | path does not exist" in client.sent_messages[0]["body"]


def test_process_once_prunes_older_sync_control_replies_globally(tmp_path) -> None:
    first = MailEnvelope(
        message_id="<sync-one@example.com>",
        subject="[SYNC]",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-16T08:00:00",
        body_text="",
        raw_headers={"Subject": "[SYNC]"},
    )
    second = MailEnvelope(
        message_id="<sync-two@example.com>",
        subject="[SYNC]",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-16T08:01:00",
        body_text="",
        raw_headers={"Subject": "[SYNC]"},
    )
    sync_root = tmp_path / "sync_root"
    (sync_root / "alpha").mkdir(parents=True)

    client = FakeMailClient([first, second])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(
        from_addr="user@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        project_sync_roots=[str(sync_root)],
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 2, "processed": 2, "skipped": 0, "failed": 0}
    assert [item["subject"] for item in client.sent_messages] == [
        "[SYNC] Project Folder List",
        "[SYNC] Project Folder List",
    ]
    assert client.deleted_message_batches == [["<sent-1@example.com>"]]
    assert not (tmp_path / "tasks" / "thread_001").exists()


class SummaryAdapter(WorkerAdapter):
    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        (run_path / "stdout.log").write_text("Repository is minimal. No files were modified.\n", encoding="utf-8")
        (run_path / "stderr.log").write_text("", encoding="utf-8")
        (run_path / "summary.md").write_text(
            "Repository is minimal. No files were modified.\n\nBackend: OpenCode\n",
            encoding="utf-8",
        )
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-12T12:20:01",
            finished_at="2026-03-12T12:20:03",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            artifacts_dir=None,
            changed_files=[],
            tests_passed=None,
            error_message=None,
            backend_session_id=f"summary-session-{task.thread_id}",
            backend_session_resumable=True,
        )

    def kill(self, task_id: str) -> bool:
        return False


class ArtifactAdapter(WorkerAdapter):
    def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
        run_path = Path(run_dir)
        artifacts_dir = run_path / "artifacts"
        run_path.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (run_path / "stdout.log").write_text("Generated preview image.\n", encoding="utf-8")
        (run_path / "stderr.log").write_text("", encoding="utf-8")
        (run_path / "summary.md").write_text("Generated preview image.\n", encoding="utf-8")
        return RunResult(
            task_id=task.task_id,
            thread_id=task.thread_id,
            backend=task.backend,
            status=RUN_STATUS_SUCCESS,
            exit_code=0,
            started_at="2026-03-12T12:20:01",
            finished_at="2026-03-12T12:20:03",
            stdout_file=f"runs/{task.task_id}/stdout.log",
            stderr_file=f"runs/{task.task_id}/stderr.log",
            summary_file=f"runs/{task.task_id}/summary.md",
            artifacts_dir=f"runs/{task.task_id}/artifacts",
            changed_files=[],
            tests_passed=None,
            error_message=None,
        )

    def kill(self, task_id: str) -> bool:
        return False


def test_process_once_uses_user_summary_in_done_mail(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root-summary@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:20:00",
        body_text="\n".join(
            [
                "Repo: D:\\repo",
                "",
                "Task:",
                "Inspect the repository.",
            ]
        ),
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(SummaryAdapter(), SummaryAdapter())
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert state["last_summary"] == "Repository is minimal. No files were modified."
    assert "Reply:\nRepository is minimal. No files were modified." in client.sent_messages[-1]["body"]
    assert "Summary: Repository is minimal. No files were modified." not in client.sent_messages[-1]["body"]


def test_process_once_prunes_older_status_mails_after_new_status_is_sent(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="Repo: D:\\repo\nTask:\nRefactor the module.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert client.deleted_message_batches == [
        ["<sent-1@example.com>"],
        ["<sent-1@example.com>", "<sent-2@example.com>"],
    ]
    assert "<sent-3@example.com>" not in client.deleted_message_batches[-1]


def test_process_once_retains_receipts_but_prunes_old_progress_mails_before_follow_up_run(tmp_path) -> None:
    initial = MailEnvelope(
        message_id="<root@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="Repo: D:\\repo\nTask:\nRefactor the module.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    follow_up = MailEnvelope(
        message_id="<reply@example.com>",
        subject="Re: [DONE][S:thread_001] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:11:00",
        body_text="Please continue with a follow-up pass.\n",
        raw_headers={"Subject": "Re: [DONE][S:thread_001] Demo task"},
    )
    client = FakeMailClient([initial, follow_up])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 2, "processed": 2, "skipped": 0, "failed": 0}
    assert len(client.sent_messages) == 6
    assert client.deleted_message_batches == [
        ["<sent-1@example.com>"],
        ["<sent-1@example.com>", "<sent-2@example.com>"],
        ["<sent-1@example.com>", "<sent-2@example.com>"],
        ["<sent-1@example.com>", "<sent-2@example.com>", "<sent-4@example.com>"],
        [
            "<sent-1@example.com>",
            "<sent-2@example.com>",
            "<sent-4@example.com>",
            "<sent-5@example.com>",
        ],
    ]
    assert "<sent-3@example.com>" not in client.deleted_message_batches[-1]
    assert "<sent-6@example.com>" not in client.deleted_message_batches[-1]


def test_process_once_creates_new_session_even_for_same_workspace_title(tmp_path) -> None:
    first = MailEnvelope(
        message_id="<root-one@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:30:00",
        body_text="Repo: D:\\repo\nWorkdir: src\nTask:\nFirst version.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    second = MailEnvelope(
        message_id="<root-two@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:31:00",
        body_text="Repo: D:\\repo\nWorkdir: src\nTask:\nSecond version.\n",
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([first, second])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    first_state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    second_state = json.loads((tmp_path / "tasks" / "thread_002" / "thread_state.json").read_text(encoding="utf-8"))
    latest_snapshot = json.loads((tmp_path / "tasks" / "thread_002" / second_state["last_task_snapshot_file"]).read_text(encoding="utf-8"))

    assert stats == {"fetched": 2, "processed": 2, "skipped": 0, "failed": 0}
    assert first_state["session_name"] == "Demo task"
    assert second_state["session_name"] == "Demo task"
    assert latest_snapshot["task_text"] == "Second version."


def test_process_once_creates_new_session_for_new_title_in_same_workspace(tmp_path) -> None:
    first = MailEnvelope(
        message_id="<root-alpha@example.com>",
        subject="[OC] Alpha task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:40:00",
        body_text="Repo: D:\\repo\nWorkdir: src\nTask:\nAlpha version.\n",
        raw_headers={"Subject": "[OC] Alpha task"},
    )
    second = MailEnvelope(
        message_id="<root-beta@example.com>",
        subject="[OC] Beta task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:41:00",
        body_text="Repo: D:\\repo\nWorkdir: src\nTask:\nBeta version.\n",
        raw_headers={"Subject": "[OC] Beta task"},
    )
    client = FakeMailClient([first, second])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    first_state = json.loads((tmp_path / "tasks" / "thread_001" / "thread_state.json").read_text(encoding="utf-8"))
    second_state = json.loads((tmp_path / "tasks" / "thread_002" / "thread_state.json").read_text(encoding="utf-8"))

    assert stats == {"fetched": 2, "processed": 2, "skipped": 0, "failed": 0}
    assert first_state["workspace_id"] == second_state["workspace_id"]
    assert first_state["session_name"] == "Alpha task"
    assert second_state["session_name"] == "Beta task"


def test_background_batch_queues_second_session_instead_of_sending_busy_status(tmp_path) -> None:
    first = MailEnvelope(
        message_id="<root-alpha@example.com>",
        subject="[OC] Alpha task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:40:00",
        body_text="Repo: D:\\repo\nWorkdir: src\nTask:\nAlpha version.\n",
        raw_headers={"Subject": "[OC] Alpha task"},
    )
    second = MailEnvelope(
        message_id="<root-beta@example.com>",
        subject="[OC] Beta task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:41:00",
        body_text="Repo: D:\\repo\nWorkdir: src\nTask:\nBeta version.\n",
        raw_headers={"Subject": "[OC] Beta task"},
    )
    client = FakeMailClient([first, second])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")
    details = bootstrap(config, tmp_path)
    task_root = Path(details["task_root"])
    runner = SerialTaskRunner(task_root, dispatcher)

    stats = _process_batch(config, task_root, client, runner, background=True)

    workspace_state = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    second_state = load_thread_state("thread_002", task_root)

    assert stats == {"fetched": 2, "processed": 2, "skipped": 0, "failed": 0}
    assert workspace_state.active_session_id == "thread_001"
    assert workspace_state.queued_session_ids == ["thread_002"]
    assert second_state.status == "accepted"
    assert [item["subject"] for item in client.sent_messages[:3]] == [
        "[ACCEPTED][S:thread_001] Alpha task",
        "[RUNNING][S:thread_001] Alpha task",
        "[ACCEPTED][S:thread_002] Beta task",
    ]

    runner.wait_until_idle()

    final_workspace_state = load_workspace_state(build_workspace_id("D:\\repo", "src"), task_root)
    assert final_workspace_state.active_session_id is None
    assert final_workspace_state.queued_session_ids == []
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Alpha task",
        "[RUNNING][S:thread_001] Alpha task",
        "[ACCEPTED][S:thread_002] Beta task",
        "[DONE][S:thread_001] Alpha task",
        "[RUNNING][S:thread_002] Beta task",
        "[DONE][S:thread_002] Beta task",
    ]


def test_background_batch_runs_different_workspaces_concurrently(tmp_path) -> None:
    first = MailEnvelope(
        message_id="<root-alpha@example.com>",
        subject="[OC] Alpha task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:40:00",
        body_text="Repo: D:\\repo\nWorkdir: src_a\nTask:\nAlpha version.\n",
        raw_headers={"Subject": "[OC] Alpha task"},
    )
    second = MailEnvelope(
        message_id="<root-beta@example.com>",
        subject="[OC] Beta task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:41:00",
        body_text="Repo: D:\\repo\nWorkdir: src_b\nTask:\nBeta version.\n",
        raw_headers={"Subject": "[OC] Beta task"},
    )
    client = FakeMailClient([first, second])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0.5), MockAdapter(sleep_seconds=0.5))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks", max_concurrent_runs=2)
    details = bootstrap(config, tmp_path)
    task_root = Path(details["task_root"])
    runner = SerialTaskRunner(task_root, dispatcher, max_concurrent_runs=config.max_concurrent_runs)

    stats = _process_batch(config, task_root, client, runner, background=True)
    time.sleep(0.05)

    first_state = load_thread_state("thread_001", task_root)
    second_state = load_thread_state("thread_002", task_root)

    assert stats == {"fetched": 2, "processed": 2, "skipped": 0, "failed": 0}
    assert runner.active_count() == 2
    assert first_state.status == "running"
    assert second_state.status == "running"
    assert [item["subject"] for item in client.sent_messages] == [
        "[ACCEPTED][S:thread_001] Alpha task",
        "[RUNNING][S:thread_001] Alpha task",
        "[ACCEPTED][S:thread_002] Beta task",
        "[RUNNING][S:thread_002] Beta task",
    ]


def test_process_once_materializes_incoming_attachments_into_workdir(tmp_path) -> None:
    repo_path = tmp_path / "repo"
    workdir_path = repo_path / "src"
    workdir_path.mkdir(parents=True)
    envelope = MailEnvelope(
        message_id="<root-attachment@example.com>",
        subject="[OC] Demo task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="\n".join(
            [
                f"Repo: {repo_path}",
                "Workdir: src",
                "",
                "Task:",
                "Review the attached screenshot.",
            ]
        ),
        attachments=[
            MailAttachment(
                filename="photo.png",
                content_type="image/png",
                size_bytes=4,
                content_bytes=b"png!",
            )
        ],
        raw_headers={"Subject": "[OC] Demo task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(MockAdapter(sleep_seconds=0), MockAdapter(sleep_seconds=0))
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    materialized = list(workdir_path.glob("_mailin_*__photo.png"))
    assert len(materialized) == 1
    state = load_thread_state("thread_001", tmp_path / "tasks")
    latest_snapshot = json.loads((tmp_path / "tasks" / "thread_001" / state.last_task_snapshot_file).read_text(encoding="utf-8"))
    assert latest_snapshot["attachments"] == [str(materialized[0])]


def test_process_once_writes_artifact_index_and_sends_projected_attachments(tmp_path) -> None:
    envelope = MailEnvelope(
        message_id="<root-artifact@example.com>",
        subject="[OC] Artifact task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="Repo: D:\\repo\nTask:\nGenerate a preview image.\n",
        raw_headers={"Subject": "[OC] Artifact task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(ArtifactAdapter(), ArtifactAdapter())
    config = AppConfig(from_addr="user@example.com", from_name="Mail Runner", task_root="tasks")

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    thread_dir = tmp_path / "tasks" / "thread_001"
    state = load_thread_state("thread_001", tmp_path / "tasks")
    index_path = thread_dir / "runs" / state.current_task_id / "artifacts" / "artifact_index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))

    assert payload["items"][0]["artifact_id"] == "artifact-preview"
    assert payload["items"][0]["kind"] == "image"
    assert payload["items"][0]["inline_preview"] is True
    assert [item.name for item in client.sent_messages[-1]["attachments"]] == ["preview.png"]
    assert "Artifacts:" in client.sent_messages[-1]["body"]
    assert "- preview.png" in client.sent_messages[-1]["body"]
    assert "artifact://artifact-preview" not in client.sent_messages[-1]["body"]
    assert "cid:mail-runner-inline-1" in (client.sent_messages[-1]["html_body"] or "")


def test_process_once_externalizes_oversized_artifact_to_cos_link(tmp_path, monkeypatch) -> None:
    envelope = MailEnvelope(
        message_id="<root-artifact-cos@example.com>",
        subject="[OC] Artifact task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="Repo: D:\\repo\nTask:\nGenerate a preview image.\n",
        raw_headers={"Subject": "[OC] Artifact task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(ArtifactAdapter(), ArtifactAdapter())
    config = AppConfig(
        from_addr="user@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        cos_region="ap-shanghai",
        cos_bucket="mailbot-1412015279",
        cos_secret_id="secret-id",
        cos_secret_key="secret-key",
        external_delivery_threshold_mb=0,
        cos_presign_expire_seconds=600,
    )

    class FakeCosClient:
        def upload_file(self, **kwargs):
            return {"ETag": '"demo"'}

        def get_presigned_download_url(self, **kwargs):
            return f"https://cos.example/{kwargs['Key']}"

    monkeypatch.setattr(
        "mail_runner.external_delivery._build_cos_client",
        lambda settings: FakeCosClient(),
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert client.sent_messages[-1]["attachments"] == []
    assert "External Deliveries:" in client.sent_messages[-1]["body"]
    assert "https://cos.example/mail-runner/thread_001/" in client.sent_messages[-1]["body"]
    assert "cid:mail-runner-inline-1" not in (client.sent_messages[-1]["html_body"] or "")


def test_process_once_externalizes_apk_with_bin_object_name_notice(tmp_path, monkeypatch) -> None:
    class ApkArtifactAdapter(WorkerAdapter):
        def run(self, task: TaskSnapshot, run_dir: str) -> RunResult:
            run_path = Path(run_dir)
            artifacts_dir = run_path / "artifacts"
            run_path.mkdir(parents=True, exist_ok=True)
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / "app.apk").write_bytes(b"apk-payload")
            (run_path / "stdout.log").write_text("Generated debug APK.\n", encoding="utf-8")
            (run_path / "stderr.log").write_text("", encoding="utf-8")
            (run_path / "summary.md").write_text("Generated debug APK.\n", encoding="utf-8")
            return RunResult(
                task_id=task.task_id,
                thread_id=task.thread_id,
                backend=task.backend,
                status=RUN_STATUS_SUCCESS,
                exit_code=0,
                started_at="2026-03-12T12:20:01",
                finished_at="2026-03-12T12:20:03",
                stdout_file=f"runs/{task.task_id}/stdout.log",
                stderr_file=f"runs/{task.task_id}/stderr.log",
                summary_file=f"runs/{task.task_id}/summary.md",
                artifacts_dir=f"runs/{task.task_id}/artifacts",
                changed_files=[],
                tests_passed=None,
                error_message=None,
            )

        def kill(self, task_id: str) -> bool:
            return False

    envelope = MailEnvelope(
        message_id="<root-artifact-apk-cos@example.com>",
        subject="[OC] Artifact task",
        from_addr="user@example.com",
        to_addr="user@example.com",
        date="2026-03-12T12:10:00",
        body_text="Repo: D:\\repo\nTask:\nGenerate an APK.\n",
        raw_headers={"Subject": "[OC] Artifact task"},
    )
    client = FakeMailClient([envelope])
    dispatcher = Dispatcher(ApkArtifactAdapter(), ApkArtifactAdapter())
    config = AppConfig(
        from_addr="user@example.com",
        from_name="Mail Runner",
        task_root="tasks",
        cos_region="ap-shanghai",
        cos_bucket="mailbot-1412015279",
        cos_secret_id="secret-id",
        cos_secret_key="secret-key",
        external_delivery_threshold_mb=0,
        cos_presign_expire_seconds=600,
    )

    class FakeCosClient:
        def upload_file(self, **kwargs):
            return {"ETag": '"demo"'}

        def get_presigned_download_url(self, **kwargs):
            return f"https://cos.example/{kwargs['Key']}"

    monkeypatch.setattr(
        "mail_runner.external_delivery._build_cos_client",
        lambda settings: FakeCosClient(),
    )

    stats = process_once(config, base_dir=tmp_path, mail_client=client, dispatcher=dispatcher)

    assert stats == {"fetched": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert client.sent_messages[-1]["attachments"] == []
    assert "app.apk.bin" in client.sent_messages[-1]["body"]
    assert "blocks direct APK distribution" in client.sent_messages[-1]["body"]
