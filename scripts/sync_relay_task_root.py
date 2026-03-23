from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync a local mail-runner task_root snapshot to a remote relay-visible task_root over ssh/scp."
    )
    parser.add_argument("--host", required=True, help="Remote VPS host or IP.")
    parser.add_argument("--user", default="ubuntu", help="Remote SSH username.")
    parser.add_argument("--key-path", required=True, help="SSH private key path.")
    parser.add_argument("--local-task-root", required=True, help="Local task_root directory to sync.")
    parser.add_argument("--remote-task-root", required=True, help="Remote task_root directory visible to the relay.")
    parser.add_argument("--run-user", default="ubuntu", help="Remote owner for the synced task_root.")
    parser.add_argument(
        "--repeat-seconds",
        type=float,
        default=0.0,
        help="If > 0, keep polling the local task_root and resync when content changes.",
    )
    return parser


def _normalize_remote_path(path: str) -> str:
    return path.replace("\\", "/")


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    if input_text is None:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        stdout = result.stdout
        stderr = result.stderr
    else:
        result = subprocess.run(
            command,
            input=input_text.encode("utf-8"),
            text=False,
            capture_output=True,
            check=False,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    result.stdout = stdout
    result.stderr = stderr
    return result


def _current_user() -> str:
    if os.name == "nt":
        return os.environ.get("USERNAME", "").strip() or "Administrator"
    return os.environ.get("USER", "").strip() or "user"


def _prepare_private_key_copy(key_path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if os.name != "nt":
        return key_path, None

    temp_dir = tempfile.TemporaryDirectory(prefix="relay-key-")
    temp_key = Path(temp_dir.name) / key_path.name
    shutil.copyfile(key_path, temp_key)
    user_name = _current_user()
    _run(["icacls", str(temp_key), "/inheritance:r"])
    _run(["icacls", str(temp_key), "/grant:r", f"{user_name}:R"])
    return temp_key, temp_dir


def _ssh_base_args(user: str, host: str, key_path: Path) -> list[str]:
    return [
        "ssh",
        "-F",
        _ssh_config_path_without_proxy(),
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "IdentitiesOnly=yes",
        f"{user}@{host}",
    ]


def _scp_base_args(user: str, host: str, key_path: Path) -> list[str]:
    return [
        "scp",
        "-F",
        _ssh_config_path_without_proxy(),
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "IdentitiesOnly=yes",
    ]


def _ssh_config_path_without_proxy() -> str:
    # Force direct PC->VPS sync traffic and ignore ambient SSH proxy / jump-host
    # settings from the operator environment.
    return "NUL" if os.name == "nt" else "/dev/null"


def _iter_task_root_entries(local_task_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(local_task_root.rglob("*")):
        paths.append(path)
    return paths


def compute_task_root_fingerprint(local_task_root: str | Path) -> str:
    root = Path(local_task_root)
    if not root.is_dir():
        raise ValueError(f"local_task_root does not exist or is not a directory: {root}")
    digest = hashlib.sha256()
    digest.update(b"task_root_v1\0")
    for path in _iter_task_root_entries(root):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        entry_kind = "dir" if path.is_dir() else "file"
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry_kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_task_root_archive(local_task_root: str | Path, archive_path: str | Path) -> int:
    root = Path(local_task_root)
    if not root.is_dir():
        raise ValueError(f"local_task_root does not exist or is not a directory: {root}")
    archive = Path(archive_path)
    file_count = 0
    with tarfile.open(archive, "w:gz") as tar:
        for child in sorted(root.iterdir()):
            tar.add(child, arcname=child.name)
        for path in _iter_task_root_entries(root):
            if path.is_file():
                file_count += 1
    return file_count


def _remote_sync_script(
    *,
    remote_task_root: str,
    archive_remote_path: str,
    run_user: str,
    sync_id: str,
) -> str:
    normalized_task_root = _normalize_remote_path(remote_task_root)
    incoming_root = f"{normalized_task_root}.incoming_{sync_id}"
    return "\n".join(
        [
            "set -euo pipefail",
            f"REMOTE_TASK_ROOT={normalized_task_root}",
            f"INCOMING_ROOT={incoming_root}",
            f"RUN_USER={run_user}",
            "REMOTE_PARENT=$(dirname \"$REMOTE_TASK_ROOT\")",
            "sudo mkdir -p \"$REMOTE_PARENT\"",
            "sudo chown -R \"$RUN_USER\":\"$RUN_USER\" \"$REMOTE_PARENT\"",
            "rm -rf \"$INCOMING_ROOT\"",
            "mkdir -p \"$INCOMING_ROOT\"",
            f"tar -xzf {archive_remote_path} -C \"$INCOMING_ROOT\"",
            "rm -rf \"$REMOTE_TASK_ROOT\"",
            "mv \"$INCOMING_ROOT\" \"$REMOTE_TASK_ROOT\"",
            f"rm -f {archive_remote_path}",
        ]
    )


def sync_task_root_once(
    *,
    host: str,
    user: str,
    key_path: Path,
    local_task_root: Path,
    remote_task_root: str,
    run_user: str,
) -> dict[str, str | int]:
    if not local_task_root.is_dir():
        raise ValueError(f"local_task_root does not exist or is not a directory: {local_task_root}")

    sync_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    with tempfile.TemporaryDirectory(prefix="relay-task-root-sync-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        archive_path = temp_dir / f"task_root_{sync_id}.tar.gz"
        file_count = build_task_root_archive(local_task_root, archive_path)
        prepared_key, prepared_key_dir = _prepare_private_key_copy(key_path)
        try:
            scp_base = _scp_base_args(user, host, prepared_key)
            ssh_base = _ssh_base_args(user, host, prepared_key)
            archive_remote_path = f"/tmp/{archive_path.name}"
            _run([*scp_base, str(archive_path), f"{user}@{host}:{archive_remote_path}"])
            remote_script = _remote_sync_script(
                remote_task_root=remote_task_root,
                archive_remote_path=archive_remote_path,
                run_user=run_user,
                sync_id=sync_id,
            )
            _run([*ssh_base, "bash", "-s"], input_text=remote_script)
        finally:
            if prepared_key_dir is not None:
                prepared_key_dir.cleanup()
    return {
        "sync_id": sync_id,
        "local_task_root": str(local_task_root.resolve()),
        "remote_task_root": _normalize_remote_path(remote_task_root),
        "file_count": file_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    key_path = Path(args.key_path).resolve()
    local_task_root = Path(args.local_task_root).resolve()
    repeat_seconds = float(args.repeat_seconds)
    if repeat_seconds < 0:
        raise ValueError("--repeat-seconds must be >= 0")

    last_fingerprint: str | None = None
    while True:
        fingerprint = compute_task_root_fingerprint(local_task_root)
        if fingerprint != last_fingerprint:
            result = sync_task_root_once(
                host=args.host,
                user=args.user,
                key_path=key_path,
                local_task_root=local_task_root,
                remote_task_root=args.remote_task_root,
                run_user=args.run_user,
            )
            print(
                "\n".join(
                    [
                        f"sync_id={result['sync_id']}",
                        f"local_task_root={result['local_task_root']}",
                        f"remote_task_root={result['remote_task_root']}",
                        f"file_count={result['file_count']}",
                        f"fingerprint={fingerprint}",
                    ]
                )
            )
            last_fingerprint = fingerprint
        elif repeat_seconds > 0:
            print(f"unchanged_fingerprint={fingerprint}")
        if repeat_seconds <= 0:
            return 0
        time.sleep(repeat_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
