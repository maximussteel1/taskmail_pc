"""IMAP and SMTP integration points."""

from __future__ import annotations

import html
import imaplib
import mimetypes
import re
import smtplib
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

from .config import AppConfig
from .models import MailEnvelope

SYSTEM_MESSAGE_HEADER = "X-Mail-Runner"
SYSTEM_MESSAGE_HEADER_VALUE = "1"
_REFERENCE_RE = re.compile(r"<[^>]+>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_BREAK_TAG_RE = re.compile(r"(?i)</?(?:br|p|div|li|tr|h[1-6])[^>]*>")
_STYLE_SCRIPT_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _extract_references(value: str | None) -> list[str]:
    if not value:
        return []
    matches = _REFERENCE_RE.findall(value)
    if matches:
        return matches
    return [item for item in value.split() if item]


def _html_to_text(value: str) -> str:
    cleaned = _STYLE_SCRIPT_RE.sub("", value)
    cleaned = _BREAK_TAG_RE.sub("\n", cleaned)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def _extract_body_text(message: EmailMessage) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        try:
            content = part.get_content()
        except LookupError:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
        if not isinstance(content, str):
            continue
        if content_type == "text/plain":
            plain_parts.append(content.strip())
        elif content_type == "text/html":
            html_parts.append(_html_to_text(content))

    if plain_parts:
        return "\n\n".join(part for part in plain_parts if part).strip()
    if html_parts:
        return "\n\n".join(part for part in html_parts if part).strip()
    return ""


def message_bytes_to_envelope(message_bytes: bytes, fallback_id: str) -> MailEnvelope:
    message = message_from_bytes(message_bytes, policy=policy.default)
    raw_headers = {key: _decode_header_value(value) for key, value in message.items()}
    subject = _decode_header_value(message.get("Subject"))
    from_addr = parseaddr(_decode_header_value(message.get("From")))[1]
    to_addr = parseaddr(_decode_header_value(message.get("To")))[1]
    message_id = _decode_header_value(message.get("Message-ID")) or f"<imap-{fallback_id}@mail-runner.local>"
    in_reply_to = _decode_header_value(message.get("In-Reply-To")) or None
    references = _extract_references(_decode_header_value(message.get("References")))
    date = _decode_header_value(message.get("Date"))
    body_text = _extract_body_text(message)
    return MailEnvelope(
        message_id=message_id,
        subject=subject or "(no subject)",
        from_addr=from_addr or "",
        to_addr=to_addr or "",
        date=date or "",
        in_reply_to=in_reply_to,
        references=references,
        body_text=body_text,
        raw_headers=raw_headers,
    )


class MailClient:
    """Thin IMAP/SMTP client for Phase 2."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def fetch_unseen_messages(self) -> list[MailEnvelope]:
        messages: list[MailEnvelope] = []
        client = imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)
        try:
            client.login(self._config.imap_user, self._config.imap_password)
            status, _ = client.select("INBOX")
            if status != "OK":
                raise RuntimeError("Unable to select INBOX.")
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("Unable to search for unseen mail.")
            for raw_id in data[0].split():
                status, payload = client.fetch(raw_id, "(RFC822)")
                client.store(raw_id, "+FLAGS", "\\Seen")
                if status != "OK" or not payload or not payload[0]:
                    continue
                message_bytes = payload[0][1]
                envelope = message_bytes_to_envelope(message_bytes, raw_id.decode("ascii", errors="ignore"))
                if envelope.raw_headers.get(SYSTEM_MESSAGE_HEADER) == SYSTEM_MESSAGE_HEADER_VALUE:
                    continue
                messages.append(envelope)
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass
        return messages

    def send_mail(
        self,
        to_addr: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        message = EmailMessage()
        from_addr = self._config.from_addr or self._config.smtp_user or self._config.imap_user
        message_id = make_msgid(domain="mail-runner.local")
        message["From"] = formataddr((self._config.from_name, from_addr))
        message["To"] = to_addr
        message["Subject"] = subject
        message["Message-ID"] = message_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = " ".join(references)
        for key, value in (headers or {}).items():
            message[key] = value
        message.set_content(body)

        for attachment_path in attachments or []:
            path = Path(attachment_path)
            mime_type, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

        smtp = smtplib.SMTP_SSL(self._config.smtp_host, self._config.smtp_port)
        try:
            smtp.login(self._config.smtp_user, self._config.smtp_password)
            smtp.send_message(message)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return message_id
