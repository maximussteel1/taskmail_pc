"""IMAP and SMTP integration points."""

from __future__ import annotations

import html
import imaplib
import json
import logging
import mimetypes
import re
import smtplib
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

from .config import AppConfig
from .models import MailAttachment, MailEnvelope, OutgoingAttachment

LOGGER = logging.getLogger(__name__)
SYSTEM_MESSAGE_HEADER = "X-Mail-Runner"
SYSTEM_MESSAGE_HEADER_VALUE = "1"
_REFERENCE_RE = re.compile(r"<[^>]+>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_BREAK_TAG_RE = re.compile(r"(?i)</?(?:br|p|div|li|tr|h[1-6])[^>]*>")
_STYLE_SCRIPT_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_MAILBOX_STATE_FILENAME = "processed_messages.json"
_MAILBOX_STATE_MAILBOX = "INBOX"
_MAILBOX_STATE_VERSION = 1
_BOOTSTRAP_UID_SCAN_LIMIT = 200
_MAX_TRACKED_PROCESSED_IDS = 2000


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


def _decode_part_filename(part: EmailMessage) -> str | None:
    filename = part.get_filename()
    if not filename:
        return None
    return _decode_header_value(filename).strip() or None


def _extract_attachments(message: EmailMessage) -> list[MailAttachment]:
    attachments: list[MailAttachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = _decode_part_filename(part)
        content_type = part.get_content_type()
        is_inline_image = disposition == "inline" and content_type.startswith("image/")
        if disposition != "attachment" and not filename and not is_inline_image:
            continue
        payload = part.get_payload(decode=True) or b""
        content_id = _decode_header_value(part.get("Content-ID")).strip()
        attachments.append(
            MailAttachment(
                filename=filename or "attachment.bin",
                content_type=content_type or "application/octet-stream",
                size_bytes=len(payload),
                content_id=content_id or None,
                is_inline=disposition == "inline",
                content_bytes=payload,
            )
        )
    return attachments


def _extract_message_bytes(payload: object) -> bytes | None:
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _normalize_uid(raw_uid: bytes | str | int | None) -> int | None:
    if raw_uid is None:
        return None
    if isinstance(raw_uid, int):
        return raw_uid if raw_uid > 0 else None
    if isinstance(raw_uid, bytes):
        text = raw_uid.decode("ascii", errors="ignore").strip()
    else:
        text = str(raw_uid).strip()
    if not text or not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


class _ProcessedMessageIndex:
    def __init__(self, path: Path, *, mailbox: str = _MAILBOX_STATE_MAILBOX) -> None:
        self._path = path
        self._mailbox = mailbox
        self._payload = self._load()
        self._dirty = False

    def _default_payload(self) -> dict[str, object]:
        return {
            "version": _MAILBOX_STATE_VERSION,
            "mailboxes": {},
        }

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return self._default_payload()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.warning("Unable to read processed-mail index: %s", self._path, exc_info=True)
            return self._default_payload()
        if not isinstance(payload, dict):
            return self._default_payload()
        mailboxes = payload.get("mailboxes")
        if not isinstance(mailboxes, dict):
            payload["mailboxes"] = {}
        return payload

    def _mailbox_state(self) -> dict[str, object]:
        mailboxes = self._payload.setdefault("mailboxes", {})
        if not isinstance(mailboxes, dict):
            mailboxes = {}
            self._payload["mailboxes"] = mailboxes
            self._dirty = True
        state = mailboxes.get(self._mailbox)
        if not isinstance(state, dict):
            state = {
                "initialized": False,
                "last_uid": 0,
                "processed_uids": [],
                "processed_message_ids": [],
            }
            mailboxes[self._mailbox] = state
            self._dirty = True
        state.setdefault("initialized", False)
        state.setdefault("last_uid", 0)
        state.setdefault("processed_uids", [])
        state.setdefault("processed_message_ids", [])
        return state

    def initialized(self) -> bool:
        return bool(self._mailbox_state().get("initialized"))

    def last_uid(self) -> int:
        return _normalize_uid(self._mailbox_state().get("last_uid")) or 0

    def candidate_uids(self, available_uids: list[int]) -> list[int]:
        ordered = sorted({uid for uid in available_uids if uid > 0})
        if not ordered:
            return []
        if self.initialized():
            current_last_uid = self.last_uid()
            return [uid for uid in ordered if uid > current_last_uid]
        return ordered[-_BOOTSTRAP_UID_SCAN_LIMIT:]

    def seen(self, uid: int, message_id: str | None) -> bool:
        state = self._mailbox_state()
        processed_uids = {str(item).strip() for item in state.get("processed_uids", [])}
        if str(uid) in processed_uids:
            return True
        if message_id:
            processed_message_ids = {str(item).strip() for item in state.get("processed_message_ids", [])}
            if message_id in processed_message_ids:
                return True
        return False

    def remember(self, uid: int, message_id: str | None) -> None:
        state = self._mailbox_state()
        uid_text = str(uid)
        processed_uids = [str(item).strip() for item in state.get("processed_uids", []) if str(item).strip()]
        if uid_text not in processed_uids:
            processed_uids.append(uid_text)
            state["processed_uids"] = processed_uids[-_MAX_TRACKED_PROCESSED_IDS:]
            self._dirty = True

        if message_id:
            processed_message_ids = [
                str(item).strip() for item in state.get("processed_message_ids", []) if str(item).strip()
            ]
            if message_id not in processed_message_ids:
                processed_message_ids.append(message_id)
                state["processed_message_ids"] = processed_message_ids[-_MAX_TRACKED_PROCESSED_IDS:]
                self._dirty = True

    def advance(self, uid: int) -> None:
        state = self._mailbox_state()
        current_last_uid = self.last_uid()
        if uid > current_last_uid:
            state["last_uid"] = uid
            self._dirty = True
        if not bool(state.get("initialized")):
            state["initialized"] = True
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._dirty = False


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
    attachments = _extract_attachments(message)
    return MailEnvelope(
        message_id=message_id,
        subject=subject or "(no subject)",
        from_addr=from_addr or "",
        to_addr=to_addr or "",
        date=date or "",
        in_reply_to=in_reply_to,
        references=references,
        body_text=body_text,
        attachments=attachments,
        raw_headers=raw_headers,
    )


class MailClient:
    """Thin IMAP/SMTP client for Phase 2."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def _imap_client(self):
        return imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)

    def _processed_message_index(self) -> _ProcessedMessageIndex:
        task_root = self._config.resolve_task_root()
        return _ProcessedMessageIndex(task_root / "_mailbox" / _MAILBOX_STATE_FILENAME)

    def fetch_unseen_messages(self) -> list[MailEnvelope]:
        messages: list[MailEnvelope] = []
        processed_index = self._processed_message_index()
        client = self._imap_client()
        try:
            client.login(self._config.imap_user, self._config.imap_password)
            status, _ = client.select("INBOX")
            if status != "OK":
                raise RuntimeError("Unable to select INBOX.")
            status, data = client.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise RuntimeError("Unable to search mailbox by UID.")

            raw_uid_items = data[0].split() if data and data[0] else []
            all_uids = [_normalize_uid(raw_uid) for raw_uid in raw_uid_items]
            candidate_uids = processed_index.candidate_uids([uid for uid in all_uids if uid is not None])
            if not candidate_uids and not processed_index.initialized():
                processed_index.advance(max((uid for uid in all_uids if uid is not None), default=0))

            for uid in candidate_uids:
                uid_text = str(uid)
                status, payload = client.uid("FETCH", uid_text, "(BODY.PEEK[])")
                if status != "OK":
                    LOGGER.warning("Unable to fetch mailbox message uid=%s", uid_text)
                    break
                message_bytes = _extract_message_bytes(payload)
                if not message_bytes:
                    LOGGER.warning("Mailbox message payload was empty for uid=%s", uid_text)
                    break

                envelope = message_bytes_to_envelope(message_bytes, uid_text)
                try:
                    client.uid("STORE", uid_text, "+FLAGS", "(\\Seen)")
                except Exception:
                    LOGGER.warning("Unable to mark mailbox message as seen for uid=%s", uid_text, exc_info=True)

                if envelope.raw_headers.get(SYSTEM_MESSAGE_HEADER) == SYSTEM_MESSAGE_HEADER_VALUE:
                    processed_index.remember(uid, envelope.message_id)
                    processed_index.advance(uid)
                    continue
                if processed_index.seen(uid, envelope.message_id):
                    processed_index.remember(uid, envelope.message_id)
                    processed_index.advance(uid)
                    continue
                messages.append(envelope)
                processed_index.remember(uid, envelope.message_id)
                processed_index.advance(uid)
        finally:
            processed_index.save()
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass
        return messages

    def list_system_message_headers(self, mailbox: str = "INBOX") -> list[MailEnvelope]:
        messages: list[MailEnvelope] = []
        client = self._imap_client()
        try:
            client.login(self._config.imap_user, self._config.imap_password)
            status, _ = client.select(mailbox)
            if status != "OK":
                raise RuntimeError(f"Unable to select mailbox: {mailbox}")
            status, data = client.search(None, "HEADER", SYSTEM_MESSAGE_HEADER, f'"{SYSTEM_MESSAGE_HEADER_VALUE}"')
            if status != "OK":
                raise RuntimeError(f"Unable to search mailbox for system mails: {mailbox}")
            mail_ids = data[0].split() if data and data[0] else []
            for raw_id in mail_ids:
                status, payload = client.fetch(raw_id, "(BODY.PEEK[HEADER])")
                if status != "OK":
                    LOGGER.warning("Unable to fetch system mailbox message id=%s", raw_id)
                    continue
                message_bytes = _extract_message_bytes(payload)
                if not message_bytes:
                    LOGGER.warning("System mailbox message payload was empty for id=%s", raw_id)
                    continue
                envelope = message_bytes_to_envelope(
                    message_bytes,
                    raw_id.decode("ascii", errors="ignore"),
                )
                if envelope.raw_headers.get(SYSTEM_MESSAGE_HEADER) != SYSTEM_MESSAGE_HEADER_VALUE:
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
        attachments: list[str | OutgoingAttachment] | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        headers: dict[str, str] | None = None,
        html_body: str | None = None,
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
        if html_body:
            message.add_alternative(html_body, subtype="html")

        normalized_attachments: list[OutgoingAttachment] = []
        for item in attachments or []:
            if isinstance(item, OutgoingAttachment):
                normalized_attachments.append(item)
            else:
                normalized_attachments.append(OutgoingAttachment(path=str(item)))

        html_part = message.get_body(("html",))
        for attachment in normalized_attachments:
            path = Path(attachment.path)
            mime_type, _ = mimetypes.guess_type(attachment.name or path.name)
            resolved_content_type = attachment.content_type or mime_type or "application/octet-stream"
            if "/" not in resolved_content_type:
                resolved_content_type = "application/octet-stream"
            maintype, subtype = resolved_content_type.split("/", 1)
            payload = path.read_bytes()
            filename = attachment.name or path.name

            if attachment.inline and html_part is not None and maintype == "image":
                content_id = attachment.content_id or make_msgid(domain="mail-runner.local").strip("<>")
                attachment.content_id = content_id
                html_part.add_related(
                    payload,
                    maintype=maintype,
                    subtype=subtype,
                    cid=f"<{content_id}>",
                    filename=filename,
                )

            if attachment.attach:
                message.add_attachment(
                    payload,
                    maintype=maintype,
                    subtype=subtype,
                    filename=filename,
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
