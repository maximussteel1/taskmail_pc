"""Shared helpers for TaskMail transport-probe system mail."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from .models import MailEnvelope

TRANSPORT_PROBE_MAIL_HEADER = "X-TaskMail-Transport-Probe"
TRANSPORT_PROBE_MAIL_HEADER_VALUE = "1"
TRANSPORT_PROBE_ID_HEADER = "X-TaskMail-Probe-Id"
TRANSPORT_PROBE_PACKET_ID_HEADER = "X-TaskMail-Relay-Packet-Id"
TRANSPORT_PROBE_REQUEST_ID_HEADER = "X-TaskMail-Relay-Request-Id"
TRANSPORT_PROBE_TRACE_ID_HEADER = "X-TaskMail-Trace-Id"
TRANSPORT_PROBE_SUBJECT_PREFIX = "[TPROBE][A2P][MAIL]"
TRANSPORT_PROBE_MAIL_SCHEMA_VERSION = "taskmail-transport-probe-payload-v1"
TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION = "taskmail-transport-probe-observation-v1"
TRANSPORT_PROBE_OBSERVATION_SURFACE = "pc_mailbox_ingress"
TRANSPORT_PROBE_OBSERVATION_DIRNAME = "transport_probes"


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _require_non_negative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a dict")
    return dict(value)


def _require_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list[str]")
    normalized: list[str] = []
    for index, item in enumerate(value):
        normalized.append(_require_text(item, f"{field_name}[{index}]"))
    return normalized


def _normalize_header_map(raw_headers: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(raw_headers, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw_headers.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        normalized[key_text] = str(value or "").strip()
    return normalized


def _parse_probe_body(body_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in str(body_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_text = key.strip()
        value_text = value.strip()
        if key_text:
            fields[key_text] = value_text
    return fields


def is_transport_probe_mail(envelope: MailEnvelope) -> bool:
    headers = _normalize_header_map(envelope.raw_headers)
    if headers.get(TRANSPORT_PROBE_MAIL_HEADER) != TRANSPORT_PROBE_MAIL_HEADER_VALUE:
        return False
    probe_id = str(headers.get(TRANSPORT_PROBE_ID_HEADER) or "").strip()
    if not probe_id:
        return False
    subject = str(envelope.subject or headers.get("Subject") or "").strip()
    if not subject.startswith(TRANSPORT_PROBE_SUBJECT_PREFIX):
        return False
    return True


@dataclass(slots=True)
class TransportProbeMail:
    probe_id: str
    request_id: str
    packet_id: str
    trace_id: str
    subject: str
    from_addr: str
    to_addr: str
    mail_date: str
    transport_message_id: str
    schema_version: str
    scenario: str
    direction: str
    transport_kind: str
    payload_text: str
    timeout_seconds: int
    body_text: str


def parse_transport_probe_mail(envelope: MailEnvelope) -> TransportProbeMail:
    headers = _normalize_header_map(envelope.raw_headers)
    if headers.get(TRANSPORT_PROBE_MAIL_HEADER) != TRANSPORT_PROBE_MAIL_HEADER_VALUE:
        raise ValueError(f"{TRANSPORT_PROBE_MAIL_HEADER} must be {TRANSPORT_PROBE_MAIL_HEADER_VALUE}")

    subject = _require_text(envelope.subject, "subject")
    if not subject.startswith(TRANSPORT_PROBE_SUBJECT_PREFIX):
        raise ValueError(f"subject must start with {TRANSPORT_PROBE_SUBJECT_PREFIX}")

    header_probe_id = _require_text(headers.get(TRANSPORT_PROBE_ID_HEADER), TRANSPORT_PROBE_ID_HEADER)
    if subject != f"{TRANSPORT_PROBE_SUBJECT_PREFIX} {header_probe_id}":
        raise ValueError("subject must equal the canonical transport-probe subject")

    body_fields = _parse_probe_body(envelope.body_text)
    body_probe_id = _require_text(body_fields.get("Probe-Id"), "Probe-Id")
    if body_probe_id != header_probe_id:
        raise ValueError("Probe-Id body field must equal X-TaskMail-Probe-Id")

    schema_version = _require_text(body_fields.get("Probe-Version"), "Probe-Version")
    if schema_version != TRANSPORT_PROBE_MAIL_SCHEMA_VERSION:
        raise ValueError(f"Probe-Version must be {TRANSPORT_PROBE_MAIL_SCHEMA_VERSION}")

    return TransportProbeMail(
        probe_id=header_probe_id,
        request_id=_require_text(headers.get(TRANSPORT_PROBE_REQUEST_ID_HEADER), TRANSPORT_PROBE_REQUEST_ID_HEADER),
        packet_id=_require_text(headers.get(TRANSPORT_PROBE_PACKET_ID_HEADER), TRANSPORT_PROBE_PACKET_ID_HEADER),
        trace_id=_require_text(headers.get(TRANSPORT_PROBE_TRACE_ID_HEADER), TRANSPORT_PROBE_TRACE_ID_HEADER),
        subject=subject,
        from_addr=_require_text(envelope.from_addr, "from_addr"),
        to_addr=_require_text(envelope.to_addr, "to_addr"),
        mail_date=_require_text(str(envelope.date or ""), "date"),
        transport_message_id=_require_text(envelope.message_id, "message_id"),
        schema_version=schema_version,
        scenario=_require_text(body_fields.get("Scenario"), "Scenario"),
        direction=_require_text(body_fields.get("Direction"), "Direction"),
        transport_kind=_require_text(body_fields.get("Transport-Kind"), "Transport-Kind"),
        payload_text=_require_text(body_fields.get("Payload-Text"), "Payload-Text"),
        timeout_seconds=_require_positive_int(
            int(_require_text(body_fields.get("Timeout-Seconds"), "Timeout-Seconds")),
            "Timeout-Seconds",
        ),
        body_text=str(envelope.body_text or ""),
    )


def transport_probe_observation_path(task_root: str | Path, probe_id: str) -> Path:
    normalized_probe_id = quote(_require_text(probe_id, "probe_id"), safe="-_.")
    return Path(task_root) / "_mailbox" / TRANSPORT_PROBE_OBSERVATION_DIRNAME / f"{normalized_probe_id}.json"


def load_transport_probe_observation(task_root: str | Path, probe_id: str) -> dict[str, Any] | None:
    path = transport_probe_observation_path(task_root, probe_id)
    if not path.exists():
        return None
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    payload = _require_mapping(raw_payload, "transport_probe_observation")
    normalized_probe_id = _require_text(payload.get("probe_id"), "probe_id")
    if normalized_probe_id != _require_text(probe_id, "probe_id"):
        raise ValueError("transport probe observation probe_id does not match requested probe_id")
    schema_version = _require_text(payload.get("schema_version"), "schema_version")
    if schema_version != TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION}")
    delivery = _require_mapping(payload.get("delivery"), "delivery")
    probe_mail = _require_mapping(payload.get("probe_mail"), "probe_mail")
    headers = _require_mapping(payload.get("headers"), "headers")
    mail_header_value = _require_text(
        headers.get(TRANSPORT_PROBE_MAIL_HEADER),
        f"headers.{TRANSPORT_PROBE_MAIL_HEADER}",
    )
    if mail_header_value != TRANSPORT_PROBE_MAIL_HEADER_VALUE:
        raise ValueError(f"headers.{TRANSPORT_PROBE_MAIL_HEADER} must be {TRANSPORT_PROBE_MAIL_HEADER_VALUE}")
    return {
        "schema_version": schema_version,
        "probe_id": normalized_probe_id,
        "request_id": _require_text(payload.get("request_id"), "request_id"),
        "packet_id": _require_text(payload.get("packet_id"), "packet_id"),
        "trace_id": _require_text(payload.get("trace_id"), "trace_id"),
        "status": _require_text(payload.get("status"), "status"),
        "observation_scope": _require_text(payload.get("observation_scope"), "observation_scope"),
        "first_observed_at": _require_text(payload.get("first_observed_at"), "first_observed_at"),
        "last_observed_at": _require_text(payload.get("last_observed_at"), "last_observed_at"),
        "seen_count": _require_non_negative_int(payload.get("seen_count"), "seen_count"),
        "observed_message_ids": _require_string_list(payload.get("observed_message_ids"), "observed_message_ids"),
        "delivery": {
            "transport_message_id": _require_text(
                delivery.get("transport_message_id"),
                "delivery.transport_message_id",
            ),
            "subject": _require_text(delivery.get("subject"), "delivery.subject"),
            "from_addr": _require_text(delivery.get("from_addr"), "delivery.from_addr"),
            "to_addr": _require_text(delivery.get("to_addr"), "delivery.to_addr"),
            "mail_date": _require_text(delivery.get("mail_date"), "delivery.mail_date"),
        },
        "probe_mail": {
            "schema_version": _require_text(probe_mail.get("schema_version"), "probe_mail.schema_version"),
            "scenario": _require_text(probe_mail.get("scenario"), "probe_mail.scenario"),
            "direction": _require_text(probe_mail.get("direction"), "probe_mail.direction"),
            "transport_kind": _require_text(probe_mail.get("transport_kind"), "probe_mail.transport_kind"),
            "payload_text": _require_text(probe_mail.get("payload_text"), "probe_mail.payload_text"),
            "timeout_seconds": _require_positive_int(
                probe_mail.get("timeout_seconds"),
                "probe_mail.timeout_seconds",
            ),
            "body_text": _require_text(probe_mail.get("body_text"), "probe_mail.body_text"),
        },
        "headers": {
            TRANSPORT_PROBE_MAIL_HEADER: mail_header_value,
            TRANSPORT_PROBE_ID_HEADER: _require_text(
                headers.get(TRANSPORT_PROBE_ID_HEADER),
                f"headers.{TRANSPORT_PROBE_ID_HEADER}",
            ),
            TRANSPORT_PROBE_REQUEST_ID_HEADER: _require_text(
                headers.get(TRANSPORT_PROBE_REQUEST_ID_HEADER),
                f"headers.{TRANSPORT_PROBE_REQUEST_ID_HEADER}",
            ),
            TRANSPORT_PROBE_PACKET_ID_HEADER: _require_text(
                headers.get(TRANSPORT_PROBE_PACKET_ID_HEADER),
                f"headers.{TRANSPORT_PROBE_PACKET_ID_HEADER}",
            ),
            TRANSPORT_PROBE_TRACE_ID_HEADER: _require_text(
                headers.get(TRANSPORT_PROBE_TRACE_ID_HEADER),
                f"headers.{TRANSPORT_PROBE_TRACE_ID_HEADER}",
            ),
        },
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(rendered, encoding="utf-8")
    temp_path.replace(path)
    return path


def record_transport_probe_observation(
    task_root: str | Path,
    envelope: MailEnvelope,
    *,
    observed_at: str | None = None,
) -> tuple[TransportProbeMail, Path]:
    parsed = parse_transport_probe_mail(envelope)
    timestamp = str(observed_at or _timestamp()).strip() or _timestamp()
    target = transport_probe_observation_path(task_root, parsed.probe_id)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError(f"transport probe observation {target} must be a JSON object")
        if existing.get("schema_version") != TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"transport probe observation {target} has unsupported schema_version")
    else:
        existing = {}

    observed_message_ids = []
    for value in existing.get("observed_message_ids", []):
        text = str(value or "").strip()
        if text and text not in observed_message_ids:
            observed_message_ids.append(text)
    if parsed.transport_message_id not in observed_message_ids:
        observed_message_ids.append(parsed.transport_message_id)

    payload = {
        "schema_version": TRANSPORT_PROBE_OBSERVATION_SCHEMA_VERSION,
        "probe_id": parsed.probe_id,
        "request_id": parsed.request_id,
        "packet_id": parsed.packet_id,
        "trace_id": parsed.trace_id,
        "status": "observed",
        "observation_scope": TRANSPORT_PROBE_OBSERVATION_SURFACE,
        "first_observed_at": str(existing.get("first_observed_at") or timestamp),
        "last_observed_at": timestamp,
        "seen_count": len(observed_message_ids),
        "observed_message_ids": observed_message_ids,
        "delivery": {
            "transport_message_id": parsed.transport_message_id,
            "subject": parsed.subject,
            "from_addr": parsed.from_addr,
            "to_addr": parsed.to_addr,
            "mail_date": parsed.mail_date,
        },
        "probe_mail": {
            "schema_version": parsed.schema_version,
            "scenario": parsed.scenario,
            "direction": parsed.direction,
            "transport_kind": parsed.transport_kind,
            "payload_text": parsed.payload_text,
            "timeout_seconds": parsed.timeout_seconds,
            "body_text": parsed.body_text,
        },
        "headers": {
            TRANSPORT_PROBE_MAIL_HEADER: TRANSPORT_PROBE_MAIL_HEADER_VALUE,
            TRANSPORT_PROBE_ID_HEADER: parsed.probe_id,
            TRANSPORT_PROBE_REQUEST_ID_HEADER: parsed.request_id,
            TRANSPORT_PROBE_PACKET_ID_HEADER: parsed.packet_id,
            TRANSPORT_PROBE_TRACE_ID_HEADER: parsed.trace_id,
        },
    }
    return parsed, _atomic_write_json(target, payload)
