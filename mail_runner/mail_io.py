"""IMAP and SMTP integration points."""

from __future__ import annotations

import html
import imaplib
import json
import logging
import mimetypes
import re
import select
import smtplib
import socket
import time
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
_IMAP_RECEIVE_MODE_AUTO = "auto"
_IMAP_RECEIVE_MODE_POLL = "poll"
_IMAP_RECEIVE_MODE_IDLE = "idle"
_IMAP_IDLE_RETRY_AFTER_SECONDS = 60.0
_IMAP_IDLE_HANDSHAKE_TIMEOUT_SECONDS = 10.0
_IMAP_IDLE_PERIODIC_SYNC_SECONDS = 5 * 60.0


class _IdleUnsupportedError(RuntimeError):
    """Raised when the IMAP server does not support IDLE."""


def _decode_imap_line(line: bytes | str | None) -> str:
    if line is None:
        return ""
    if isinstance(line, bytes):
        return line.decode("utf-8", errors="replace").strip()
    return str(line).strip()


def _extract_imap_capabilities(payload: list[bytes] | None) -> set[str]:
    capabilities: set[str] = set()
    for item in payload or []:
        if not isinstance(item, bytes):
            continue
        for part in item.split():
            token = part.decode("ascii", errors="ignore").strip().upper()
            if token:
                capabilities.add(token)
    return capabilities


class _IdleMailboxSession:
    def __init__(
        self,
        config: AppConfig,
        *,
        mailbox: str = _MAILBOX_STATE_MAILBOX,
        imap_factory=imaplib.IMAP4_SSL,
        select_fn=select.select,
        monotonic_fn=time.monotonic,
    ) -> None:
        self._config = config
        self._mailbox = mailbox
        self._imap_factory = imap_factory
        self._select_fn = select_fn
        self._monotonic_fn = monotonic_fn
        self._client = None
        self._idle_tag: bytes | None = None
        self._idle_started_at: float | None = None

    def wait_for_event(self, timeout_seconds: float) -> bool:
        deadline = self._monotonic_fn() + max(0.0, float(timeout_seconds))
        while True:
            self._ensure_connected()
            self._ensure_idle()

            now = self._monotonic_fn()
            remaining = deadline - now
            if remaining <= 0:
                return False

            renew_remaining = self._idle_renew_remaining(now)
            if renew_remaining <= 0:
                self._renew_idle()
                continue

            wait_seconds = min(remaining, renew_remaining)
            ready, _, _ = self._select_fn([self._socket()], [], [], wait_seconds)
            if not ready:
                if self._idle_renew_remaining(self._monotonic_fn()) <= 0:
                    self._renew_idle()
                    continue
                return False

            line = self._read_line(timeout_seconds=wait_seconds)
            if not line:
                raise RuntimeError("IMAP IDLE connection closed unexpectedly.")
            upper = line.upper()
            if upper.startswith(b"* BYE"):
                raise RuntimeError(f"IMAP server closed IDLE connection: {_decode_imap_line(line)}")
            if upper.startswith(b"+"):
                continue
            if self._idle_tag is not None and upper.startswith(self._idle_tag.upper() + b" "):
                self._idle_tag = None
                self._idle_started_at = None
                continue
            if upper.startswith(b"* "):
                if b" EXISTS" in upper or b" RECENT" in upper:
                    LOGGER.info(
                        "IMAP IDLE detected mailbox activity. host=%s mailbox=%s line=%s",
                        self._config.imap_host,
                        self._mailbox,
                        _decode_imap_line(line),
                    )
                    return True
                LOGGER.debug(
                    "Ignoring non-delivery IMAP IDLE line. host=%s mailbox=%s line=%s",
                    self._config.imap_host,
                    self._mailbox,
                    _decode_imap_line(line),
                )

    def close(self) -> None:
        try:
            if self._client is not None and self._idle_tag is not None:
                self._exit_idle()
        except Exception:
            LOGGER.debug("Failed to exit IMAP IDLE cleanly.", exc_info=True)
        finally:
            client = self._client
            self._client = None
            self._idle_tag = None
            self._idle_started_at = None
            if client is None:
                return
            try:
                client.logout()
            except Exception:
                try:
                    client.shutdown()
                except Exception:
                    pass

    def _ensure_connected(self) -> None:
        if self._client is not None:
            return

        client = self._imap_factory(self._config.imap_host, self._config.imap_port)
        try:
            client.login(self._config.imap_user, self._config.imap_password)
            status, capability_data = client.capability()
            if status != "OK":
                raise RuntimeError("Unable to query IMAP CAPABILITY for IDLE.")
            capabilities = _extract_imap_capabilities(capability_data)
            if "IDLE" not in capabilities:
                raise _IdleUnsupportedError(
                    f"Server {self._config.imap_host} does not advertise IMAP IDLE."
                )
            status, _ = client.select(self._mailbox)
            if status != "OK":
                raise RuntimeError(f"Unable to select mailbox for IMAP IDLE: {self._mailbox}")
        except Exception:
            try:
                client.logout()
            except Exception:
                try:
                    client.shutdown()
                except Exception:
                    pass
            raise

        self._client = client
        LOGGER.info(
            "IMAP IDLE watcher connected. host=%s mailbox=%s",
            self._config.imap_host,
            self._mailbox,
        )

    def _ensure_idle(self) -> None:
        if self._idle_tag is not None:
            return
        client = self._require_client()
        tag = client._new_tag()
        client.send(tag + b" IDLE\r\n")
        response = self._read_line(timeout_seconds=_IMAP_IDLE_HANDSHAKE_TIMEOUT_SECONDS)
        if not response.startswith(b"+"):
            raise RuntimeError(f"IMAP IDLE was rejected: {_decode_imap_line(response)}")
        self._idle_tag = tag
        self._idle_started_at = self._monotonic_fn()

    def _renew_idle(self) -> None:
        self._exit_idle()
        self._ensure_idle()

    def _exit_idle(self) -> None:
        if self._idle_tag is None:
            self._idle_started_at = None
            return
        client = self._require_client()
        tag = self._idle_tag
        client.send(b"DONE\r\n")
        while True:
            line = self._read_line(timeout_seconds=_IMAP_IDLE_HANDSHAKE_TIMEOUT_SECONDS)
            if not line:
                raise RuntimeError("IMAP IDLE terminated before DONE completed.")
            upper = line.upper()
            if upper.startswith(b"* BYE"):
                raise RuntimeError(f"IMAP server closed IDLE during DONE: {_decode_imap_line(line)}")
            if upper.startswith(tag.upper() + b" "):
                break
        self._idle_tag = None
        self._idle_started_at = None

    def _idle_renew_remaining(self, now: float) -> float:
        if self._idle_started_at is None:
            return 0.0
        renew_seconds = max(1, int(self._config.imap_idle_renew_seconds))
        return max(0.0, renew_seconds - (now - self._idle_started_at))

    def _socket(self):
        client = self._require_client()
        return client.sock

    def _read_line(self, timeout_seconds: float | None = None) -> bytes:
        client = self._require_client()
        sock = getattr(client, "sock", None)
        previous_timeout = None
        can_restore_timeout = False
        if timeout_seconds is not None and sock is not None and hasattr(sock, "gettimeout") and hasattr(sock, "settimeout"):
            previous_timeout = sock.gettimeout()
            sock.settimeout(max(0.1, float(timeout_seconds)))
            can_restore_timeout = True
        try:
            return client._get_line()
        except (socket.timeout, TimeoutError) as exc:
            raise RuntimeError(
                f"IMAP IDLE read timed out after {max(0.1, float(timeout_seconds or 0.0)):.1f}s"
            ) from exc
        except imaplib.IMAP4.abort as exc:
            if "timed out" in str(exc).lower():
                raise RuntimeError(
                    f"IMAP IDLE read timed out after {max(0.1, float(timeout_seconds or 0.0)):.1f}s"
                ) from exc
            raise
        finally:
            if can_restore_timeout:
                sock.settimeout(previous_timeout)

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("IMAP IDLE session is not connected.")
        return self._client


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
        self._idle_session: _IdleMailboxSession | None = None
        self._idle_supported: bool | None = None
        self._idle_retry_after_monotonic: float = 0.0
        self._idle_force_sync_after_monotonic: float = 0.0

    def _imap_client(self):
        return imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)

    def _build_idle_session(self) -> _IdleMailboxSession:
        return _IdleMailboxSession(self._config)

    def _processed_message_index(self) -> _ProcessedMessageIndex:
        task_root = self._config.resolve_task_root()
        return _ProcessedMessageIndex(task_root / "_mailbox" / _MAILBOX_STATE_FILENAME)

    def receive_mode(self) -> str:
        requested = str(self._config.imap_receive_mode or _IMAP_RECEIVE_MODE_AUTO).strip().lower()
        if requested == _IMAP_RECEIVE_MODE_POLL:
            return _IMAP_RECEIVE_MODE_POLL
        if self._idle_supported is False:
            return _IMAP_RECEIVE_MODE_POLL
        if requested in {_IMAP_RECEIVE_MODE_AUTO, _IMAP_RECEIVE_MODE_IDLE}:
            return _IMAP_RECEIVE_MODE_IDLE
        return _IMAP_RECEIVE_MODE_POLL

    def wait_for_new_messages(self, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        if timeout <= 0:
            return False
        if self.receive_mode() == _IMAP_RECEIVE_MODE_POLL:
            time.sleep(timeout)
            return False

        started_at = time.monotonic()
        if self._idle_force_sync_after_monotonic <= 0.0:
            self._idle_force_sync_after_monotonic = started_at + _IMAP_IDLE_PERIODIC_SYNC_SECONDS
        if started_at >= self._idle_force_sync_after_monotonic:
            self._close_idle_session()
            self._idle_force_sync_after_monotonic = started_at + _IMAP_IDLE_PERIODIC_SYNC_SECONDS
            LOGGER.info(
                "Ending IMAP IDLE wait to force a periodic mailbox sync. host=%s interval=%.0fs",
                self._config.imap_host,
                _IMAP_IDLE_PERIODIC_SYNC_SECONDS,
            )
            return True
        if self._idle_retry_after_monotonic > started_at:
            time.sleep(timeout)
            return False

        try:
            if self._idle_session is None:
                self._idle_session = self._build_idle_session()
            event_detected = self._idle_session.wait_for_event(timeout)
            self._idle_supported = True
            self._idle_retry_after_monotonic = 0.0
            if event_detected:
                self._close_idle_session()
                self._idle_force_sync_after_monotonic = time.monotonic() + _IMAP_IDLE_PERIODIC_SYNC_SECONDS
            return event_detected
        except _IdleUnsupportedError as exc:
            self._idle_supported = False
            self._close_idle_session()
            self._idle_force_sync_after_monotonic = 0.0
            LOGGER.info(
                "IMAP IDLE is unavailable for host=%s; falling back to polling. reason=%s",
                self._config.imap_host,
                exc,
            )
        except Exception as exc:
            self._idle_supported = True
            self._close_idle_session()
            self._idle_retry_after_monotonic = time.monotonic() + _IMAP_IDLE_RETRY_AFTER_SECONDS
            LOGGER.warning(
                "IMAP IDLE wait failed for host=%s; falling back to polling for %.0fs. error=%s",
                self._config.imap_host,
                _IMAP_IDLE_RETRY_AFTER_SECONDS,
                exc,
            )

        elapsed = time.monotonic() - started_at
        remaining = timeout - elapsed
        if remaining > 0:
            time.sleep(remaining)
        return False

    def close(self) -> None:
        self._close_idle_session()

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

    def _close_idle_session(self) -> None:
        idle_session = self._idle_session
        self._idle_session = None
        if idle_session is None:
            return
        idle_session.close()
