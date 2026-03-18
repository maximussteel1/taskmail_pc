"""Inbound mail attachment helpers."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .models import MailAttachment, MailEnvelope

DEFAULT_INCOMING_PREFIX = "_mailin_"
_WINDOWS_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _timestamp_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_attachment_name(filename: str) -> str:
    basename = Path(str(filename or "")).name.strip()
    basename = _WINDOWS_UNSAFE_RE.sub("_", basename)
    return basename or "attachment.bin"


def resolve_workdir(
    repo_path: str,
    workdir: str | None,
    *,
    auto_create_workdir: bool = False,
) -> Path:
    repo_root = Path(repo_path)
    if not repo_root.exists():
        raise FileNotFoundError(f"Task repository path does not exist: {repo_root}")
    if not repo_root.is_dir():
        raise NotADirectoryError(f"Task repository path is not a directory: {repo_root}")

    if not workdir:
        return repo_root

    candidate = Path(workdir)
    target = candidate if candidate.is_absolute() else (repo_root / candidate)
    if target.exists():
        if not target.is_dir():
            raise NotADirectoryError(f"Task working directory is not a directory: {target}")
        return target

    if not auto_create_workdir:
        raise FileNotFoundError(f"Task working directory does not exist: {target}")
    if candidate.is_absolute():
        raise FileNotFoundError(f"Task working directory does not exist: {target}")

    resolved_repo = repo_root.resolve()
    resolved_target = target.resolve(strict=False)
    if not resolved_target.is_relative_to(resolved_repo):
        raise ValueError(f"Auto-created workdir must stay within repo_path: {target}")
    resolved_target.mkdir(parents=True, exist_ok=True)
    return resolved_target


def materialize_incoming_attachments(
    envelope: MailEnvelope,
    *,
    repo_path: str,
    workdir: str | None,
    auto_create_workdir: bool = False,
    filename_prefix: str = DEFAULT_INCOMING_PREFIX,
) -> MailEnvelope:
    if not envelope.attachments:
        return envelope

    destination_root = resolve_workdir(repo_path, workdir, auto_create_workdir=auto_create_workdir)
    timestamp_token = _timestamp_token()
    updated = deepcopy(envelope)
    materialized: list[MailAttachment] = []

    for index, attachment in enumerate(updated.attachments, start=1):
        safe_name = sanitize_attachment_name(attachment.filename)
        output_name = f"{filename_prefix}{timestamp_token}_{index:03d}__{safe_name}"
        target_path = destination_root / output_name
        target_path.write_bytes(attachment.content_bytes)
        digest = hashlib.sha256(attachment.content_bytes).hexdigest() if attachment.content_bytes else None
        materialized.append(
            MailAttachment(
                filename=attachment.filename,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                saved_path=str(target_path),
                raw_saved_path=attachment.raw_saved_path,
                content_id=attachment.content_id,
                is_inline=attachment.is_inline,
                sha256=digest,
                content_bytes=attachment.content_bytes,
            )
        )

    updated.attachments = materialized
    return updated


def attachment_summary_lines(attachments: list[MailAttachment]) -> list[str]:
    if not attachments:
        return []
    lines = ["New incoming attachments materialized in workdir:"]
    for attachment in attachments:
        if attachment.saved_path:
            lines.append(f"- {attachment.saved_path}")
    return lines
