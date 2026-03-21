from __future__ import annotations

from mail_runner.codex_process_registry import (
    PROCESS_RECORD_FILENAME,
    iter_process_record_paths,
    iter_process_records,
    load_process_record,
    process_record_path,
    remove_process_record,
    write_process_record,
)


def test_process_record_round_trip(tmp_path) -> None:
    run_dir = tmp_path / "thread_001" / "runs" / "task_001"
    run_dir.mkdir(parents=True)

    path = write_process_record(
        run_dir,
        pid=4242,
        task_id="task_001",
        thread_id="thread_001",
        started_at="2026-03-21T12:00:00",
        repo_path=str(tmp_path / "repo"),
        workdir=str(tmp_path / "repo"),
        command=["node", "fake-sidecar.js"],
    )

    assert path == process_record_path(run_dir)
    record = load_process_record(path)
    assert record is not None
    assert record.pid == 4242
    assert record.task_id == "task_001"
    assert record.thread_id == "thread_001"
    assert record.command == ["node", "fake-sidecar.js"]

    remove_process_record(run_dir)
    assert not path.exists()


def test_iter_process_records_finds_run_records(tmp_path) -> None:
    first_run = tmp_path / "thread_001" / "runs" / "task_001"
    second_run = tmp_path / "thread_002" / "runs" / "task_002"
    first_run.mkdir(parents=True)
    second_run.mkdir(parents=True)
    (tmp_path / "_scheduler").mkdir()
    (tmp_path / "_scheduler" / PROCESS_RECORD_FILENAME).write_text("{}", encoding="utf-8")

    write_process_record(
        first_run,
        pid=1001,
        task_id="task_001",
        thread_id="thread_001",
        started_at="2026-03-21T12:00:00",
        repo_path="E:/projects/repo1",
        workdir="E:/projects/repo1",
        command=["node", "first.js"],
    )
    write_process_record(
        second_run,
        pid=1002,
        task_id="task_002",
        thread_id="thread_002",
        started_at="2026-03-21T12:01:00",
        repo_path="E:/projects/repo2",
        workdir="E:/projects/repo2",
        command=["node", "second.js"],
    )

    paths = iter_process_record_paths(tmp_path)
    records = iter_process_records(tmp_path)

    assert len(paths) == 2
    assert [record.pid for record in records] == [1001, 1002]
