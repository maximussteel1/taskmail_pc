from __future__ import annotations

import os
import tarfile

import scripts.sync_relay_task_root as sync_script
from scripts.sync_relay_task_root import (
    _scp_base_args,
    _ssh_base_args,
    build_task_root_archive,
    compute_task_root_fingerprint,
)


def test_compute_task_root_fingerprint_changes_when_task_root_changes(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    thread_dir = task_root / "thread_001"
    thread_dir.mkdir()
    state_path = thread_dir / "thread_state.json"
    state_path.write_text('{"status":"running"}\n', encoding="utf-8")

    first = compute_task_root_fingerprint(task_root)
    state_path.write_text('{"status":"done"}\n', encoding="utf-8")
    second = compute_task_root_fingerprint(task_root)

    assert first != second


def test_build_task_root_archive_preserves_relative_paths(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    (task_root / "thread_001").mkdir()
    (task_root / "thread_001" / "thread_state.json").write_text('{"status":"running"}\n', encoding="utf-8")
    (task_root / "_mailbox").mkdir()
    (task_root / "_mailbox" / "index.json").write_text("{}\n", encoding="utf-8")
    archive_path = tmp_path / "task_root.tar.gz"

    file_count = build_task_root_archive(task_root, archive_path)

    assert file_count == 2
    with tarfile.open(archive_path, "r:gz") as tar:
        names = sorted(member.name for member in tar.getmembers())
    assert "thread_001/thread_state.json" in names
    assert "_mailbox/index.json" in names


def test_compute_task_root_fingerprint_ignores_entries_deleted_after_enumeration(tmp_path, monkeypatch) -> None:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    vanished = task_root / "vanished.json"
    vanished.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sync_script, "_iter_task_root_entries", lambda root: [vanished])
    vanished.unlink()

    fingerprint = compute_task_root_fingerprint(task_root)

    assert len(fingerprint) == 64


def test_build_task_root_archive_skips_entries_deleted_after_enumeration(tmp_path, monkeypatch) -> None:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    keep = task_root / "keep.txt"
    keep.write_text("keep\n", encoding="utf-8")
    vanished = task_root / "vanished.txt"
    vanished.write_text("gone\n", encoding="utf-8")
    archive_path = tmp_path / "task_root.tar.gz"

    monkeypatch.setattr(sync_script, "_iter_task_root_entries", lambda root: [keep, vanished])
    vanished.unlink()

    file_count = build_task_root_archive(task_root, archive_path)

    assert file_count == 1
    with tarfile.open(archive_path, "r:gz") as tar:
        names = sorted(member.name for member in tar.getmembers())
    assert names == ["keep.txt"]


def test_sync_task_root_ssh_and_scp_ignore_proxy_and_jump_settings(tmp_path) -> None:
    key_path = tmp_path / "work_bot.pem"
    key_path.write_text("demo", encoding="utf-8")

    ssh_args = _ssh_base_args("ubuntu", "relay.example.com", key_path)
    scp_args = _scp_base_args("ubuntu", "relay.example.com", key_path)
    expected_config_path = "NUL" if os.name == "nt" else "/dev/null"

    assert ssh_args[:8] == [
        "ssh",
        "-F",
        expected_config_path,
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
    ]
    assert scp_args[:8] == [
        "scp",
        "-F",
        expected_config_path,
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
    ]
