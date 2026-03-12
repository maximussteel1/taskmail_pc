"""Mail IO parsing and transport tests for Phase 2."""

from __future__ import annotations

from email.message import EmailMessage

from mail_runner.config import AppConfig
from mail_runner.mail_io import (
    MailClient,
    SYSTEM_MESSAGE_HEADER,
    SYSTEM_MESSAGE_HEADER_VALUE,
    message_bytes_to_envelope,
)


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


def test_fetch_unseen_messages_skips_system_mail(monkeypatch) -> None:
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

    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.stored: list[tuple[bytes, str, str]] = []

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def search(self, *_args) -> tuple[str, list[bytes]]:
            return "OK", [b"1 2"]

        def fetch(self, raw_id: bytes, _query: str):
            payload = normal.as_bytes() if raw_id == b"1" else system.as_bytes()
            return "OK", [(b"RFC822", payload)]

        def store(self, raw_id: bytes, mode: str, flag: str):
            self.stored.append((raw_id, mode, flag))
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

    envelopes = client.fetch_unseen_messages()

    assert [item.message_id for item in envelopes] == ["<normal@example.com>"]


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
