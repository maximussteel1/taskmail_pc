"""Mail IO parsing and transport tests for Phase 2."""

from __future__ import annotations

import json
from email.message import EmailMessage

from mail_runner.config import AppConfig
from mail_runner.mail_io import (
    MailClient,
    SYSTEM_MESSAGE_HEADER,
    SYSTEM_MESSAGE_HEADER_VALUE,
    message_bytes_to_envelope,
)
from mail_runner.models import OutgoingAttachment


def test_message_bytes_to_envelope_prefers_plain_text() -> None:
    message = EmailMessage()
    message["From"] = "User <user@example.com>"
    message["To"] = "Runner <runner@example.com>"
    message["Subject"] = "[OC] Demo"
    message["Message-ID"] = "<m1@example.com>"
    message["References"] = "<root@example.com> <prev@example.com>"
    message.set_content("Plain body")
    message.add_alternative("<p>HTML body</p>", subtype="html")

    envelope = message_bytes_to_envelope(message.as_bytes(), "1")

    assert envelope.message_id == "<m1@example.com>"
    assert envelope.from_addr == "user@example.com"
    assert envelope.to_addr == "runner@example.com"
    assert envelope.references == ["<root@example.com>", "<prev@example.com>"]
    assert envelope.body_text == "Plain body"


def test_message_bytes_to_envelope_falls_back_to_html() -> None:
    message = EmailMessage()
    message["From"] = "User <user@example.com>"
    message["To"] = "Runner <runner@example.com>"
    message["Subject"] = "[OC] Demo"
    message["Message-ID"] = "<m2@example.com>"
    message.add_alternative("<div>Hello<br>World</div>", subtype="html")

    envelope = message_bytes_to_envelope(message.as_bytes(), "2")

    assert envelope.body_text == "Hello\nWorld"


def test_message_bytes_to_envelope_falls_back_to_html_when_plain_part_is_empty() -> None:
    message = EmailMessage()
    message["From"] = "User <user@example.com>"
    message["To"] = "Runner <runner@example.com>"
    message["Subject"] = "[OC] Demo"
    message["Message-ID"] = "<m2b@example.com>"
    message.set_content("")
    message.add_alternative("<div>Repo:&nbsp;E:\\repo</div><div>Task:<br>Hi</div>", subtype="html")

    envelope = message_bytes_to_envelope(message.as_bytes(), "2b")

    assert envelope.body_text == "Repo:\xa0E:\\repo\nTask:\nHi"


def test_message_bytes_to_envelope_preserves_utf8_chinese_headers_and_body() -> None:
    message = EmailMessage()
    message["From"] = "姜淳 <user@example.com>"
    message["To"] = "Runner <runner@example.com>"
    message["Subject"] = "[OC] 并发验证 A"
    message["Message-ID"] = "<m3@example.com>"
    message.set_content(
        "Repo: E:\\projects\\mail_based_task_manager\n\nTask:\n检查 app.py 和 runner.py 的关系。\n",
        charset="utf-8",
    )

    envelope = message_bytes_to_envelope(message.as_bytes(), "3")

    assert envelope.subject == "[OC] 并发验证 A"
    assert envelope.raw_headers["From"] == "姜淳 <user@example.com>"
    assert "检查 app.py 和 runner.py 的关系。" in envelope.body_text


def test_fetch_unseen_messages_uses_uid_scan_and_skips_system_mail(monkeypatch, tmp_path) -> None:
    normal = EmailMessage()
    normal["From"] = "User <user@example.com>"
    normal["To"] = "Runner <runner@example.com>"
    normal["Subject"] = "[OC] Demo"
    normal["Message-ID"] = "<normal@example.com>"
    normal.set_content("Body")

    system = EmailMessage()
    system["From"] = "Runner <runner@example.com>"
    system["To"] = "Runner <runner@example.com>"
    system["Subject"] = "[DONE] Demo"
    system["Message-ID"] = "<system@example.com>"
    system[SYSTEM_MESSAGE_HEADER] = SYSTEM_MESSAGE_HEADER_VALUE
    system.set_content("System body")

    captured: dict[str, list[object]] = {
        "uid_search": [],
        "uid_fetch": [],
        "uid_store": [],
    }

    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def uid(self, command: str, *args):
            normalized = command.upper()
            if normalized == "SEARCH":
                captured["uid_search"].append(args)
                return "OK", [b"101 102"]
            if normalized == "FETCH":
                uid_text = str(args[0])
                captured["uid_fetch"].append(uid_text)
                payload = normal.as_bytes() if uid_text == "101" else system.as_bytes()
                return "OK", [(b"RFC822", payload)]
            if normalized == "STORE":
                captured["uid_store"].append([str(item) for item in args])
                return "OK", [b""]
            raise AssertionError(f"unexpected uid command: {command}")

        def close(self) -> None:
            return None

        def logout(self) -> None:
            return None

    monkeypatch.setattr("mail_runner.mail_io.imaplib.IMAP4_SSL", FakeImap)
    client = MailClient(
        AppConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="user",
            imap_password="pass",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_addr="runner@example.com",
            task_root=str(tmp_path / "tasks"),
        )
    )

    first_fetch = client.fetch_unseen_messages()
    second_fetch = client.fetch_unseen_messages()

    assert [item.message_id for item in first_fetch] == ["<normal@example.com>"]
    assert second_fetch == []
    assert captured["uid_search"] == [(None, "ALL"), (None, "ALL")]
    assert captured["uid_fetch"] == ["101", "102"]
    assert captured["uid_store"] == [
        ["101", "+FLAGS", "(\\Seen)"],
        ["102", "+FLAGS", "(\\Seen)"],
    ]

    state_path = tmp_path / "tasks" / "_mailbox" / "processed_messages.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    inbox_state = payload["mailboxes"]["INBOX"]
    assert inbox_state["initialized"] is True
    assert inbox_state["last_uid"] == 102
    assert inbox_state["processed_uids"] == ["101", "102"]
    assert inbox_state["processed_message_ids"] == ["<normal@example.com>", "<system@example.com>"]


def test_fetch_unseen_messages_skips_duplicate_message_ids_from_new_uids(monkeypatch, tmp_path) -> None:
    original = EmailMessage()
    original["From"] = "User <user@example.com>"
    original["To"] = "Runner <runner@example.com>"
    original["Subject"] = "[OC] Demo"
    original["Message-ID"] = "<duplicate@example.com>"
    original.set_content("First copy")

    duplicate = EmailMessage()
    duplicate["From"] = "User <user@example.com>"
    duplicate["To"] = "Runner <runner@example.com>"
    duplicate["Subject"] = "[OC] Demo"
    duplicate["Message-ID"] = "<duplicate@example.com>"
    duplicate.set_content("Copied into mailbox again")

    class FakeImap:
        search_results = [b"201", b"201 202"]
        search_call_count = 0

        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def uid(self, command: str, *args):
            normalized = command.upper()
            if normalized == "SEARCH":
                index = min(FakeImap.search_call_count, len(FakeImap.search_results) - 1)
                FakeImap.search_call_count += 1
                return "OK", [FakeImap.search_results[index]]
            if normalized == "FETCH":
                uid_text = str(args[0])
                payload = original.as_bytes() if uid_text == "201" else duplicate.as_bytes()
                return "OK", [(b"RFC822", payload)]
            if normalized == "STORE":
                return "OK", [b""]
            raise AssertionError(f"unexpected uid command: {command}")

        def close(self) -> None:
            return None

        def logout(self) -> None:
            return None

    monkeypatch.setattr("mail_runner.mail_io.imaplib.IMAP4_SSL", FakeImap)
    client = MailClient(
        AppConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="user",
            imap_password="pass",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_addr="runner@example.com",
            task_root=str(tmp_path / "tasks"),
        )
    )

    first_fetch = client.fetch_unseen_messages()
    second_fetch = client.fetch_unseen_messages()

    assert [item.message_id for item in first_fetch] == ["<duplicate@example.com>"]
    assert second_fetch == []

    state_path = tmp_path / "tasks" / "_mailbox" / "processed_messages.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    inbox_state = payload["mailboxes"]["INBOX"]
    assert inbox_state["last_uid"] == 202
    assert inbox_state["processed_uids"] == ["201", "202"]
    assert inbox_state["processed_message_ids"] == ["<duplicate@example.com>"]


def test_delete_messages_by_message_ids_searches_inbox_by_message_id(monkeypatch) -> None:
    captured: dict[str, object] = {"searches": [], "stored": [], "expunged": 0}

    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            captured["login"] = (user, password)
            return "OK", [b""]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            captured["mailbox"] = mailbox
            return "OK", [b""]

        def search(self, _charset, *criteria):
            searches = captured["searches"]
            assert isinstance(searches, list)
            searches.append(criteria)
            message_id = criteria[-1]
            if message_id == '"<old-1@example.com>"':
                return "OK", [b"7"]
            if message_id == '"<old-2@example.com>"':
                return "OK", [b"11"]
            return "OK", [b""]

        def store(self, raw_id: bytes, mode: str, flag: str):
            stored = captured["stored"]
            assert isinstance(stored, list)
            stored.append((raw_id, mode, flag))
            return "OK", [b""]

        def expunge(self):
            captured["expunged"] = int(captured["expunged"]) + 1
            return "OK", [b""]

        def close(self) -> None:
            return None

        def logout(self) -> None:
            return None

    monkeypatch.setattr("mail_runner.mail_io.imaplib.IMAP4_SSL", FakeImap)
    client = MailClient(
        AppConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="user",
            imap_password="pass",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_addr="runner@example.com",
        )
    )

    deleted = client.delete_messages_by_message_ids(
        ["<old-1@example.com>", "<old-2@example.com>", "<missing@example.com>", "<old-1@example.com>"]
    )

    assert deleted == ["<old-1@example.com>", "<old-2@example.com>"]
    assert captured["mailbox"] == "INBOX"
    assert captured["searches"] == [
        ("HEADER", "Message-ID", '"<old-1@example.com>"'),
        ("HEADER", "Message-ID", '"<old-2@example.com>"'),
        ("HEADER", "Message-ID", '"<missing@example.com>"'),
    ]
    assert captured["stored"] == [
        (b"7", "+FLAGS", "\\Deleted"),
        (b"11", "+FLAGS", "\\Deleted"),
    ]
    assert captured["expunged"] == 1


def test_list_system_message_headers_reads_only_system_messages(monkeypatch) -> None:
    system_one = EmailMessage()
    system_one["From"] = "Runner <runner@example.com>"
    system_one["To"] = "Runner <runner@example.com>"
    system_one["Subject"] = "[DONE] Demo"
    system_one["Message-ID"] = "<system-1@example.com>"
    system_one[SYSTEM_MESSAGE_HEADER] = SYSTEM_MESSAGE_HEADER_VALUE
    system_one.set_content("System body")

    system_two = EmailMessage()
    system_two["From"] = "Runner <runner@example.com>"
    system_two["To"] = "Runner <runner@example.com>"
    system_two["Subject"] = "[SYNC] Project Folder List"
    system_two["Message-ID"] = "<system-2@example.com>"
    system_two[SYSTEM_MESSAGE_HEADER] = SYSTEM_MESSAGE_HEADER_VALUE
    system_two.set_content("Sync body")

    captured: dict[str, object] = {"searches": [], "fetches": []}

    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            captured["login"] = (user, password)
            return "OK", [b""]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            captured["mailbox"] = mailbox
            return "OK", [b""]

        def search(self, _charset, *criteria):
            searches = captured["searches"]
            assert isinstance(searches, list)
            searches.append(criteria)
            return "OK", [b"7 11"]

        def fetch(self, raw_id: bytes, query: str):
            fetches = captured["fetches"]
            assert isinstance(fetches, list)
            fetches.append((raw_id, query))
            payload = system_one.as_bytes() if raw_id == b"7" else system_two.as_bytes()
            return "OK", [(b"RFC822.HEADER", payload)]

        def close(self) -> None:
            return None

        def logout(self) -> None:
            return None

    monkeypatch.setattr("mail_runner.mail_io.imaplib.IMAP4_SSL", FakeImap)
    client = MailClient(
        AppConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="user",
            imap_password="pass",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_addr="runner@example.com",
        )
    )

    messages = client.list_system_message_headers()

    assert [item.message_id for item in messages] == ["<system-1@example.com>", "<system-2@example.com>"]
    assert [item.subject for item in messages] == ["[DONE] Demo", "[SYNC] Project Folder List"]
    assert captured["mailbox"] == "INBOX"
    assert captured["searches"] == [("HEADER", SYSTEM_MESSAGE_HEADER, '"1"')]
    assert captured["fetches"] == [
        (b"7", "(BODY.PEEK[HEADER])"),
        (b"11", "(BODY.PEEK[HEADER])"),
    ]


def test_send_mail_adds_headers_and_reply_metadata(monkeypatch) -> None:
    captured = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        def login(self, user: str, password: str) -> None:
            captured["login"] = (user, password)

        def send_message(self, message) -> None:
            captured["message"] = message

        def quit(self) -> None:
            captured["quit"] = True

    monkeypatch.setattr("mail_runner.mail_io.smtplib.SMTP_SSL", FakeSmtp)
    client = MailClient(
        AppConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_name="Mail Runner",
            from_addr="runner@example.com",
        )
    )

    client.send_mail(
        to_addr="user@example.com",
        subject="[DONE] Demo",
        body="Done",
        in_reply_to="<root@example.com>",
        references=["<root@example.com>"],
        headers={SYSTEM_MESSAGE_HEADER: SYSTEM_MESSAGE_HEADER_VALUE},
    )

    message = captured["message"]
    assert message["Subject"] == "[DONE] Demo"
    assert message["In-Reply-To"] == "<root@example.com>"
    assert message["References"] == "<root@example.com>"
    assert message[SYSTEM_MESSAGE_HEADER] == SYSTEM_MESSAGE_HEADER_VALUE


def test_message_bytes_to_envelope_extracts_attachments() -> None:
    message = EmailMessage()
    message["From"] = "User <user@example.com>"
    message["To"] = "Runner <runner@example.com>"
    message["Subject"] = "[OC] Demo"
    message["Message-ID"] = "<m4@example.com>"
    message.set_content("See attachment")
    message.add_attachment(b"hello", maintype="text", subtype="plain", filename="notes.txt")

    envelope = message_bytes_to_envelope(message.as_bytes(), "4")

    assert len(envelope.attachments) == 1
    assert envelope.attachments[0].filename == "notes.txt"
    assert envelope.attachments[0].content_type == "text/plain"
    assert envelope.attachments[0].content_bytes == b"hello"


def test_send_mail_supports_html_and_inline_image_attachments(monkeypatch, tmp_path) -> None:
    captured = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        def login(self, user: str, password: str) -> None:
            captured["login"] = (user, password)

        def send_message(self, message) -> None:
            captured["message"] = message

        def quit(self) -> None:
            captured["quit"] = True

    inline_image = tmp_path / "preview.png"
    inline_image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr("mail_runner.mail_io.smtplib.SMTP_SSL", FakeSmtp)
    client = MailClient(
        AppConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            from_name="Mail Runner",
            from_addr="runner@example.com",
        )
    )

    client.send_mail(
        to_addr="user@example.com",
        subject="[DONE] Demo",
        body="Done",
        html_body="<html><body><img src=\"cid:preview-1\"></body></html>",
        attachments=[
            OutgoingAttachment(
                path=str(inline_image),
                name="preview.png",
                content_type="image/png",
                attach=True,
                inline=True,
                content_id="preview-1",
            )
        ],
    )

    message = captured["message"]
    assert message.get_body(("html",)) is not None
    content_ids = [part.get("Content-ID") for part in message.walk() if part.get("Content-ID")]
    assert "<preview-1>" in content_ids
    all_filenames = [part.get_filename() for part in message.iter_attachments()]
    assert "preview.png" in all_filenames
