from __future__ import annotations

from mail_runner.models import RunResult, TaskSnapshot
from mail_runner.relay_server.android_session_snapshot_facade import build_android_session_snapshot
from mail_runner.status import THREAD_STATUS_RUNNING
from mail_runner.thread_store import build_workspace_id, create_thread, load_session_state, load_thread_state, save_thread_state
from mail_runner.workspace import WorkspaceManager


def test_android_session_snapshot_includes_history_rounds_from_durable_snapshots_and_results(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    workspace = WorkspaceManager(task_root)
    repo_path = "E:\\projects\\android_task_manager"
    workdir = "feature/taskmail"
    workspace_id = build_workspace_id(repo_path, workdir)
    thread_id = "thread_001"
    session_id = "session_001"

    create_thread(
        thread_id=thread_id,
        root_message_id=f"<root:{thread_id}@example.com>",
        latest_message_id=f"<latest:{thread_id}@example.com>",
        subject_norm="history-rounds",
        backend="codex",
        profile="default",
        permission="default",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id="task_002",
        last_task_snapshot_file="snapshots/task_002.json",
        task_root=task_root,
        status=THREAD_STATUS_RUNNING,
        history_files=["runs/task_001/result.json"],
        last_summary="Still processing the latest homepage follow-up.",
        last_active_at="2026-03-27T11:02:00",
        last_progress_at="2026-03-27T11:02:00",
        created_at="2026-03-27T10:00:00",
        updated_at="2026-03-27T11:02:00",
        session_id=session_id,
        session_name="TaskMail history rounds",
        backend_transport="sdk",
    )

    first_input = workspace.thread_dir(thread_id) / "mail" / "brief.md"
    first_input.parent.mkdir(parents=True, exist_ok=True)
    first_input.write_text("Homepage brief", encoding="utf-8")
    second_input = workspace.thread_dir(thread_id) / "mail" / "homepage-followup.md"
    second_input.write_text("Follow-up notes", encoding="utf-8")

    workspace.save_snapshot(
        TaskSnapshot(
            task_id="task_001",
            thread_id=thread_id,
            backend="codex",
            repo_path=repo_path,
            workdir=workdir,
            task_text="Draft the first homepage tree view.",
            attachments=[workspace.to_thread_relative(thread_id, first_input)],
            created_at="2026-03-27T10:00:00",
            updated_at="2026-03-27T10:00:00",
            backend_transport="sdk",
        )
    )
    workspace.save_snapshot(
        TaskSnapshot(
            task_id="task_002",
            thread_id=thread_id,
            backend="codex",
            repo_path=repo_path,
            workdir=workdir,
            task_text="Continue the previous task.",
            attachments=[workspace.to_thread_relative(thread_id, second_input)],
            created_at="2026-03-27T11:00:00",
            updated_at="2026-03-27T11:01:00",
            turn_text="Review the latest homepage sketch and keep the task tree.",
            backend_transport="sdk",
        )
    )

    summary_path = workspace.run_file_path(thread_id, "task_001", "summary.md")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        "# Round 1 result\nAdded the tree-based homepage draft and updated the history card layout.\n",
        encoding="utf-8",
    )
    artifact_dir = workspace.run_dir(thread_id, "task_001") / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "home-tree.png"
    artifact_path.write_bytes(b"png")

    result_path = workspace.save_run_result(
        thread_id,
        "task_001",
        RunResult(
            task_id="task_001",
            thread_id=thread_id,
            backend="codex",
            status="success",
            exit_code=0,
            started_at="2026-03-27T10:00:00",
            finished_at="2026-03-27T10:10:00",
            stdout_file="runs/task_001/stdout.log",
            stderr_file="runs/task_001/stderr.log",
            summary_file="runs/task_001/summary.md",
            artifacts_dir="runs/task_001/artifacts",
            changed_files=["feature/taskmail/Home.kt"],
            tests_passed=True,
            backend_transport="sdk",
        ),
    )

    thread_state = load_thread_state(thread_id, task_root)
    thread_state.history_files = [workspace.to_thread_relative(thread_id, result_path)]
    thread_state.current_task_id = "task_002"
    thread_state.last_task_snapshot_file = "snapshots/task_002.json"
    thread_state.last_summary = "Still processing the latest homepage follow-up."
    save_thread_state(thread_state, task_root)
    session_state = load_session_state(workspace_id, session_id, task_root)
    assert session_state.current_task_id == "task_002"

    payload = build_android_session_snapshot(
        query={
            "workspace_id": [workspace_id],
            "session_id": [session_id],
        },
        task_root=task_root,
    )

    history_rounds = payload["session_snapshot"]["history_rounds"]
    assert [item["round_number"] for item in history_rounds] == [2, 1]

    latest_round = history_rounds[0]
    assert latest_round["status"] == "running"
    assert latest_round["input"]["text"] == "Review the latest homepage sketch and keep the task tree."
    assert latest_round["process"]["items"][0]["text"] == "Still processing the latest homepage follow-up."
    assert latest_round["input"]["attachments"][0]["display_name"] == "homepage-followup.md"

    older_round = history_rounds[1]
    assert older_round["status"] == "done"
    assert older_round["result"]["text"].startswith("# Round 1 result")
    assert older_round["input"]["attachments"][0]["display_name"] == "brief.md"
    assert older_round["result"]["attachments"][0]["display_name"] == "home-tree.png"
