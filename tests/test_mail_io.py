"""Mail IO parsing and transport tests for Phase 2."""

from __future__ import annotations

import json
from email.message import EmailMessage

from mail_runner.config import AppConfig
from mail_runner.mail_io import (
    MailClient,
    SYSTEM_MESSAGE_HEADER,
    SYSTEM_MESSAGE_HEADER_VALUE,
    _IdleMailboxSession,
    _IdleUnsupportedError,
    message_bytes_to_envelope,
)
from mail_runner.models import OutgoingAttachment
from mail_runner.transport_probe_mail import (
    TRANSPORT_PROBE_ID_HEADER,
    TRANSPORT_PROBE_MAIL_HEADER,
    TRANSPORT_PROBE_MAIL_HEADER_VALUE,
    TRANSPORT_PROBE_PACKET_ID_HEADER,
    TRANSPORT_PROBE_REQUEST_ID_HEADER,
    TRANSPORT_PROBE_TRACE_ID_HEADER,
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


def test_send_mail_preserves_html_alternative_and_inline_related_image(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        def login(self, user: str, password: str) -> None:
            captured["login"] = (user, password)

        def send_message(self, message: EmailMessage) -> None:
            captured["message"] = message

        def quit(self) -> None:
            captured["quit"] = True

    monkeypatch.setattr("mail_runner.mail_io.smtplib.SMTP_SSL", FakeSmtp)

    image_path = tmp_path / "preview.png"
    image_path.write_bytes(b"fake-png")

    client = MailClient(
        AppConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="runner@example.com",
            smtp_password="secret",
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="runner@example.com",
            imap_password="secret",
            from_name="Runner",
        )
    )

    client.send_mail(
        to_addr="user@example.com",
        subject="[DONE] Demo",
        body="Plain body",
        html_body='<article class="task-mail"><section class="task-summary"><p>HTML body</p></section></article>',
        attachments=[
            OutgoingAttachment(
                path=str(image_path),
                name="preview.png",
                content_type="image/png",
                attach=True,
                inline=True,
                caption="Preview image",
                content_id="preview-cid",
            )
        ],
    )

    message = captured["message"]
    assert isinstance(message, EmailMessage)
    content_types = [part.get_content_type() for part in message.walk()]
    assert "multipart/alternative" in content_types
    assert "multipart/related" in content_types
    assert "text/plain" in content_types
    assert "text/html" in content_types
    html_part = message.get_body(("html",))
    assert html_part is not None
    assert 'article class="task-mail"' in html_part.get_content()
    assert any(part.get("Content-ID") == "<preview-cid>" for part in message.walk())
    preview_parts = [part for part in message.walk() if part.get("Content-ID") == "<preview-cid>"]
    assert len(preview_parts) == 1
    assert preview_parts[0].get_content_disposition() == "inline"
    assert preview_parts[0].get_filename() is None
    assert any(
        part.get_filename() == "preview.png" and part.get_content_disposition() == "attachment"
        for part in message.walk()
    )
    assert sum(1 for part in message.walk() if part.get_filename() == "preview.png") == 1


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


def test_fetch_unseen_messages_passthroughs_transport_probe_system_mail(monkeypatch, tmp_path) -> None:
    normal = EmailMessage()
    normal["From"] = "User <user@example.com>"
    normal["To"] = "Runner <runner@example.com>"
    normal["Subject"] = "[OC] Demo"
    normal["Message-ID"] = "<normal@example.com>"
    normal.set_content("Body")

    probe = EmailMessage()
    probe["From"] = "Runner <relay@example.com>"
    probe["To"] = "Runner <bot@example.com>"
    probe["Subject"] = "[TPROBE][A2P][MAIL] probe-transport-001"
    probe["Message-ID"] = "<probe@example.com>"
    probe[SYSTEM_MESSAGE_HEADER] = SYSTEM_MESSAGE_HEADER_VALUE
    probe[TRANSPORT_PROBE_MAIL_HEADER] = TRANSPORT_PROBE_MAIL_HEADER_VALUE
    probe[TRANSPORT_PROBE_ID_HEADER] = "probe-transport-001"
    probe[TRANSPORT_PROBE_REQUEST_ID_HEADER] = "req-transport-001"
    probe[TRANSPORT_PROBE_PACKET_ID_HEADER] = "packet-transport-001"
    probe[TRANSPORT_PROBE_TRACE_ID_HEADER] = "trace-transport-001"
    probe.set_content(
        "\n".join(
            [
                "Probe-Version: taskmail-transport-probe-payload-v1",
                "Probe-Id: probe-transport-001",
                "Scenario: android_direct_ping_to_vps_to_pc",
                "Direction: android_to_pc",
                "Transport-Kind: mail",
                "Timeout-Seconds: 30",
                "Payload-Text: hello-probe",
                "",
            ]
        )
    )

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
                return "OK", [b"101 102"]
            if normalized == "FETCH":
                uid_text = str(args[0])
                payload = normal.as_bytes() if uid_text == "101" else probe.as_bytes()
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

    assert [item.message_id for item in first_fetch] == ["<normal@example.com>", "<probe@example.com>"]
    assert second_fetch == []

    state_path = tmp_path / "tasks" / "_mailbox" / "processed_messages.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    inbox_state = payload["mailboxes"]["INBOX"]
    assert inbox_state["last_uid"] == 102
    assert inbox_state["processed_uids"] == ["101", "102"]
    assert inbox_state["processed_message_ids"] == ["<normal@example.com>", "<probe@example.com>"]


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


def test_idle_mailbox_session_detects_exists_and_exits_cleanly() -> None:
    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.sock = object()
            self.sent: list[bytes] = []
            self.lines: list[bytes] = []
            self.current_tag = b""
            self.logged_out = False

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def capability(self) -> tuple[str, list[bytes]]:
            return "OK", [b"IMAP4rev1 IDLE UIDPLUS"]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def _new_tag(self) -> bytes:
            self.current_tag = b"T1"
            return self.current_tag

        def send(self, data: bytes) -> None:
            self.sent.append(data)
            if data.endswith(b" IDLE\r\n"):
                self.lines.append(b"+ idling")
            elif data == b"DONE\r\n":
                self.lines.append(self.current_tag + b" OK IDLE terminated")

        def _get_line(self) -> bytes:
            return self.lines.pop(0)

        def logout(self) -> None:
            self.logged_out = True

        def shutdown(self) -> None:
            self.logged_out = True

    fake_imap = FakeImap("imap.example.com", 993)
    select_calls = {"count": 0}

    def fake_factory(host: str, port: int) -> FakeImap:
        assert host == "imap.example.com"
        assert port == 993
        return fake_imap

    def fake_select(readers, _writers, _errors, timeout):
        assert readers == [fake_imap.sock]
        assert timeout == 5.0
        if select_calls["count"] == 0:
            fake_imap.lines.append(b"* 9 EXISTS")
        select_calls["count"] += 1
        return readers, [], []

    session = _IdleMailboxSession(
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
        ),
        imap_factory=fake_factory,
        select_fn=fake_select,
        monotonic_fn=lambda: 0.0,
    )

    assert session.wait_for_event(5.0) is True
    session.close()

    assert fake_imap.sent == [b"T1 IDLE\r\n", b"DONE\r\n"]
    assert fake_imap.logged_out is True


def test_idle_mailbox_session_times_out_stalled_line_reads() -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.timeout = None
            self.history: list[float | None] = []

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value) -> None:
            self.timeout = value
            self.history.append(value)

    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.sock = FakeSocket()
            self.sent: list[bytes] = []
            self.lines: list[bytes] = []
            self.current_tag = b""

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def capability(self) -> tuple[str, list[bytes]]:
            return "OK", [b"IMAP4rev1 IDLE UIDPLUS"]

        def select(self, mailbox: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def _new_tag(self) -> bytes:
            self.current_tag = b"T1"
            return self.current_tag

        def send(self, data: bytes) -> None:
            self.sent.append(data)
            if data.endswith(b" IDLE\r\n"):
                self.lines.append(b"+ idling")

        def _get_line(self) -> bytes:
            if self.lines:
                return self.lines.pop(0)
            raise TimeoutError("simulated stalled readline")

    fake_imap = FakeImap("imap.example.com", 993)

    def fake_factory(host: str, port: int) -> FakeImap:
        return fake_imap

    def fake_select(readers, _writers, _errors, timeout):
        assert readers == [fake_imap.sock]
        return readers, [], []

    session = _IdleMailboxSession(
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
        ),
        imap_factory=fake_factory,
        select_fn=fake_select,
        monotonic_fn=lambda: 0.0,
    )

    try:
        session.wait_for_event(1.0)
        raise AssertionError("Expected stalled IMAP IDLE readline to time out.")
    except RuntimeError as exc:
        assert "timed out" in str(exc)

    assert fake_imap.sock.history == [10.0, None, 1.0, None]


def test_idle_mailbox_session_rejects_servers_without_idle() -> None:
    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.logged_out = False

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def capability(self) -> tuple[str, list[bytes]]:
            return "OK", [b"IMAP4rev1 UIDPLUS"]

        def logout(self) -> None:
            self.logged_out = True

        def shutdown(self) -> None:
            self.logged_out = True

    fake_imap = FakeImap("imap.example.com", 993)
    session = _IdleMailboxSession(
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
        ),
        imap_factory=lambda host, port: fake_imap,
        select_fn=lambda *_args: ([], [], []),
        monotonic_fn=lambda: 0.0,
    )

    try:
        session.wait_for_event(1.0)
        raise AssertionError("Expected IMAP IDLE to be rejected.")
    except _IdleUnsupportedError:
        pass

    assert fake_imap.logged_out is True


def test_mail_client_wait_for_new_messages_falls_back_after_idle_unsupported(monkeypatch) -> None:
    slept: list[float] = []

    class UnsupportedIdleClient(MailClient):
        def _build_idle_session(self):
            class UnsupportedSession:
                def wait_for_event(self, timeout_seconds: float) -> bool:
                    raise _IdleUnsupportedError("server does not support IDLE")

                def close(self) -> None:
                    return None

            return UnsupportedSession()

    monkeypatch.setattr("mail_runner.mail_io.time.sleep", slept.append)
    monkeypatch.setattr("mail_runner.mail_io.time.monotonic", lambda: 10.0)

    client = UnsupportedIdleClient(
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
            imap_receive_mode="auto",
        )
    )

    assert client.wait_for_new_messages(0.5) is False
    assert client.receive_mode() == "poll"
    assert slept == [0.5]


def test_mail_client_wait_for_new_messages_falls_back_after_idle_wait_failure(monkeypatch) -> None:
    slept: list[float] = []
    closed: list[str] = []

    class FlakyIdleClient(MailClient):
        def _build_idle_session(self):
            class FlakySession:
                def wait_for_event(self, timeout_seconds: float) -> bool:
                    raise RuntimeError("IMAP IDLE read timed out after 1.0s")

                def close(self) -> None:
                    closed.append("closed")

            return FlakySession()

    monkeypatch.setattr("mail_runner.mail_io.time.sleep", slept.append)
    monkeypatch.setattr("mail_runner.mail_io.time.monotonic", lambda: 10.0)

    client = FlakyIdleClient(
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
            imap_receive_mode="auto",
        )
    )

    assert client.wait_for_new_messages(0.5) is False
    assert client._idle_retry_after_monotonic == 70.0
    assert client._idle_session is None
    assert closed == ["closed"]
    assert slept == [0.5]


def test_mail_client_wait_for_new_messages_forces_periodic_sync_and_rebuild(monkeypatch) -> None:
    closed: list[str] = []

    class ExistingIdleSession:
        def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr("mail_runner.mail_io.time.monotonic", lambda: 300.0)

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
            imap_receive_mode="auto",
        )
    )
    client._idle_session = ExistingIdleSession()
    client._idle_force_sync_after_monotonic = 299.0

    assert client.wait_for_new_messages(1.0) is True
    assert client._idle_session is None
    assert client._idle_force_sync_after_monotonic == 600.0
    assert closed == ["closed"]


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
    assert sum(1 for part in message.walk() if part.get_filename() == "preview.png") == 1
