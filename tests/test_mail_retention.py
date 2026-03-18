from __future__ import annotations

import json

from mail_runner.mail_retention import (
    SYNC_PROJECT_FOLDER_LIST_SUBJECT,
    SystemMessageRef,
    collect_stale_sync_message_ids,
    collect_stale_thread_status_message_ids,
)
from mail_runner.mail_io import SYSTEM_MESSAGE_HEADER, SYSTEM_MESSAGE_HEADER_VALUE


def _write_raw_mail(task_root, thread_id: str, raw_index: int, payload: dict) -> None:
    mail_dir = task_root / thread_id / "mail"
    mail_dir.mkdir(parents=True, exist_ok=True)
    (mail_dir / f"raw_{raw_index:03d}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_collect_stale_thread_status_message_ids_keeps_latest_per_thread(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    system_headers = {
        SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
    }
    _write_raw_mail(
        task_root,
        "thread_001",
        1,
        {
            "message_id": "<accepted-1@example.com>",
            "subject": "[ACCEPTED][S:thread_001] Demo",
            "raw_headers": {**system_headers, "Subject": "[ACCEPTED][S:thread_001] Demo"},
        },
    )
    _write_raw_mail(
        task_root,
        "thread_001",
        2,
        {
            "message_id": "<running-1@example.com>",
            "subject": "[RUNNING][S:thread_001] Demo",
            "raw_headers": {**system_headers, "Subject": "[RUNNING][S:thread_001] Demo"},
        },
    )
    _write_raw_mail(
        task_root,
        "thread_001",
        3,
        {
            "message_id": "<done-1@example.com>",
            "subject": "[DONE][S:thread_001] Demo",
            "raw_headers": {**system_headers, "Subject": "[DONE][S:thread_001] Demo"},
        },
    )
    _write_raw_mail(
        task_root,
        "thread_002",
        1,
        {
            "message_id": "<status-2@example.com>",
            "subject": "[STATUS][S:thread_002] Demo",
            "raw_headers": {**system_headers, "Subject": "[STATUS][S:thread_002] Demo"},
        },
    )
    _write_raw_mail(
        task_root,
        "thread_002",
        2,
        {
            "message_id": "<question-2@example.com>",
            "subject": "[QUESTION][S:thread_002] Demo",
            "raw_headers": {**system_headers, "Subject": "[QUESTION][S:thread_002] Demo"},
        },
    )
    _write_raw_mail(
        task_root,
        "thread_003",
        1,
        {
            "message_id": "<accepted-3@example.com>",
            "subject": "[ACCEPTED][S:thread_003] Demo",
            "raw_headers": {**system_headers, "Subject": "[ACCEPTED][S:thread_003] Demo"},
        },
    )
    _write_raw_mail(
        task_root,
        "thread_003",
        2,
        {
            "message_id": "<status-3@example.com>",
            "subject": "[STATUS][S:thread_003] Demo",
            "raw_headers": {**system_headers, "Subject": "[STATUS][S:thread_003] Demo"},
        },
    )

    assert collect_stale_thread_status_message_ids(task_root) == [
        "<accepted-1@example.com>",
        "<running-1@example.com>",
        "<status-2@example.com>",
        "<accepted-3@example.com>",
    ]


def test_collect_stale_thread_status_message_ids_ignores_non_system_and_sync_mail(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    _write_raw_mail(
        task_root,
        "thread_001",
        1,
        {
            "message_id": "<sync@example.com>",
            "subject": SYNC_PROJECT_FOLDER_LIST_SUBJECT,
            "raw_headers": {
                SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE,
                "Subject": SYNC_PROJECT_FOLDER_LIST_SUBJECT,
            },
        },
    )
    _write_raw_mail(
        task_root,
        "thread_001",
        2,
        {
            "message_id": "<user-reply@example.com>",
            "subject": "Re: [DONE][S:thread_001] Demo",
            "raw_headers": {"Subject": "Re: [DONE][S:thread_001] Demo"},
        },
    )

    assert collect_stale_thread_status_message_ids(task_root) == []


def test_collect_stale_thread_status_message_ids_preserves_receipts_and_action_required(tmp_path) -> None:
    task_root = tmp_path / "tasks"
    system_headers = {SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE}
    for raw_index, subject, message_id in [
        (1, "[DONE][S:thread_001] Demo", "<done@example.com>"),
        (2, "[FAILED][S:thread_001] Demo", "<failed@example.com>"),
        (3, "[KILLED][S:thread_001] Demo", "<killed@example.com>"),
        (4, "[QUESTION][S:thread_001] Demo", "<question@example.com>"),
        (5, "[PAUSED][S:thread_001] Demo", "<paused@example.com>"),
    ]:
        _write_raw_mail(
            task_root,
            "thread_001",
            raw_index,
            {
                "message_id": message_id,
                "subject": subject,
                "raw_headers": {**system_headers, "Subject": subject},
            },
        )

    assert collect_stale_thread_status_message_ids(task_root) == []


def test_collect_stale_sync_message_ids_keeps_latest_reply() -> None:
    stale_ids = collect_stale_sync_message_ids(
        [
            SystemMessageRef(message_id="<sync-1@example.com>", subject=SYNC_PROJECT_FOLDER_LIST_SUBJECT),
            SystemMessageRef(message_id="<done@example.com>", subject="[DONE][S:thread_001] Demo"),
            SystemMessageRef(message_id="<sync-2@example.com>", subject=SYNC_PROJECT_FOLDER_LIST_SUBJECT),
            SystemMessageRef(message_id="<sync-3@example.com>", subject=SYNC_PROJECT_FOLDER_LIST_SUBJECT),
        ]
    )

    assert stale_ids == ["<sync-1@example.com>", "<sync-2@example.com>"]
