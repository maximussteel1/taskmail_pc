"""IMAP and SMTP integration points."""

from __future__ import annotations

import html
import imaplib
import mimetypes
import re
import smtplib
from email import message_from_bytes, policy
from email.header import decode_header
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


def _decode_bytes_value(payload: bytes, charset: str | None = None) -> str:
    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in (charset, "utf-8", "gb18030", "gbk", "latin-1"):
        if not candidate:
            continue
        normalized = candidate.strip().strip('"').lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)

    for candidate in candidates:
        try:
            return payload.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _normalize_mail_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(_decode_bytes_value(chunk, charset))
        else:
            parts.append(chunk)
    return _normalize_mail_text("".join(parts))


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
    cleaned = _normalize_mail_text(cleaned)
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
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            content = _decode_bytes_value(payload, part.get_content_charset())
        if not isinstance(content, str):
            continue
        content = _normalize_mail_text(content)
        if content_type == "text/plain":
            plain_parts.append(content.strip())
        elif content_type == "text/html":
            html_parts.append(_html_to_text(content))

    rendered_plain = "\n\n".join(part for part in plain_parts if part).strip()
    if rendered_plain:
        return rendered_plain

    rendered_html = "\n\n".join(part for part in html_parts if part).strip()
    if rendered_html:
        return rendered_html
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

    def _imap_client(self):
        return imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)

    def fetch_unseen_messages(self) -> list[MailEnvelope]:
        messages: list[MailEnvelope] = []
        client = self._imap_client()
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

    def delete_messages_by_message_ids(self, message_ids: list[str], mailbox: str = "INBOX") -> list[str]:
        normalized_ids: list[str] = []
        seen_ids: set[str] = set()
        for raw_message_id in message_ids:
            message_id = str(raw_message_id or "").strip()
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)
            normalized_ids.append(message_id)
        if not normalized_ids:
            return []

        client = self._imap_client()
        deleted_ids: list[str] = []
        try:
            client.login(self._config.imap_user, self._config.imap_password)
            status, _ = client.select(mailbox)
            if status != "OK":
                raise RuntimeError(f"Unable to select mailbox: {mailbox}")

            matched_imap_ids: list[bytes] = []
            matched_seen: set[bytes] = set()
            for message_id in normalized_ids:
                status, data = client.search(None, "HEADER", "Message-ID", f'"{message_id}"')
                if status != "OK" or not data:
                    continue
                current_matches = [item for item in data[0].split() if item]
                if not current_matches:
                    continue
                deleted_ids.append(message_id)
                for raw_id in current_matches:
                    if raw_id in matched_seen:
                        continue
                    matched_seen.add(raw_id)
                    matched_imap_ids.append(raw_id)

            deleted_any = False
            for raw_id in matched_imap_ids:
                status, _ = client.store(raw_id, "+FLAGS", "\\Deleted")
                if status == "OK":
                    deleted_any = True
            if deleted_any:
                client.expunge()
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass
        return deleted_ids

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
