"""Helpers for loading and validating Phase 3 direct inbound fixture packages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import (
    ProtocolValidationError,
    RelayPacketAckMessage,
    RelayPacketMessage,
    RelaySessionUpdateMessage,
    parse_client_message,
    parse_server_message,
)

_FIXTURE_SCHEMA_VERSION = "phase3-direct-inbound-fixture-package-v1"
_FIXTURE_MANIFEST = "manifest.json"
_EXPECTED_PROJECTION_FIELDS = (
    "canonical_workspace_id",
    "canonical_session_id",
    "canonical_thread_id",
    "header_status",
    "header_lifecycle",
    "last_summary",
    "question_set_id",
    "pending_question_ids",
    "quick_answer_choices",
    "visible_business_event_keys",
    "suppressed_direct_business_event_keys",
)


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolValidationError(f"{field_name} must be a dict")
    return dict(value)


def _require_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolValidationError(f"{field_name} must be a list")
    return list(value)


@dataclass(slots=True)
class Phase3FixtureManifestEntry:
    fixture_id: str
    file: str
    category: str

    def __post_init__(self) -> None:
        self.fixture_id = _require_text(self.fixture_id, "fixture_id")
        self.file = _require_text(self.file, "file")
        self.category = _require_text(self.category, "category")


@dataclass(slots=True)
class Phase3FixtureManifest:
    schema_version: str
    package_id: str
    generated_at: str
    fixtures: list[Phase3FixtureManifestEntry]

    def __post_init__(self) -> None:
        self.schema_version = _require_text(self.schema_version, "schema_version")
        if self.schema_version != _FIXTURE_SCHEMA_VERSION:
            raise ProtocolValidationError(f"schema_version must be {_FIXTURE_SCHEMA_VERSION}")
        self.package_id = _require_text(self.package_id, "package_id")
        self.generated_at = _require_text(self.generated_at, "generated_at")
        if not isinstance(self.fixtures, list) or not self.fixtures:
            raise ProtocolValidationError("fixtures must be a non-empty list")
        fixture_ids: set[str] = set()
        files: set[str] = set()
        for entry in self.fixtures:
            if not isinstance(entry, Phase3FixtureManifestEntry):
                raise ProtocolValidationError("fixtures entries must be Phase3FixtureManifestEntry")
            if entry.fixture_id in fixture_ids:
                raise ProtocolValidationError(f"duplicate fixture_id: {entry.fixture_id}")
            if entry.file in files:
                raise ProtocolValidationError(f"duplicate fixture file: {entry.file}")
            fixture_ids.add(entry.fixture_id)
            files.add(entry.file)


@dataclass(slots=True)
class Phase3FixtureUnit:
    fixture_meta: dict[str, Any]
    subscribe_exchange: dict[str, Any]
    session_updates: list[dict[str, Any]]
    recovery_exchange: dict[str, Any] | None
    mail_companion: dict[str, Any]
    expected_projection: dict[str, Any]

    def __post_init__(self) -> None:
        self.fixture_meta = _require_mapping(self.fixture_meta, "fixture_meta")
        self.fixture_meta["fixture_id"] = _require_text(self.fixture_meta.get("fixture_id"), "fixture_meta.fixture_id")
        self.fixture_meta["schema_version"] = _require_text(
            self.fixture_meta.get("schema_version"),
            "fixture_meta.schema_version",
        )
        if self.fixture_meta["schema_version"] != _FIXTURE_SCHEMA_VERSION:
            raise ProtocolValidationError(f"fixture_meta.schema_version must be {_FIXTURE_SCHEMA_VERSION}")
        self.fixture_meta["intent"] = _require_text(self.fixture_meta.get("intent"), "fixture_meta.intent")

        self.subscribe_exchange = _require_mapping(self.subscribe_exchange, "subscribe_exchange")
        subscribe_request = parse_client_message(
            _require_mapping(self.subscribe_exchange.get("request"), "subscribe_exchange.request")
        )
        if not isinstance(subscribe_request, RelayPacketMessage):
            raise ProtocolValidationError("subscribe_exchange.request must parse as RelayPacketMessage")
        subscribe_ack = parse_server_message(_require_mapping(self.subscribe_exchange.get("ack"), "subscribe_exchange.ack"))
        if not isinstance(subscribe_ack, RelayPacketAckMessage):
            raise ProtocolValidationError("subscribe_exchange.ack must parse as RelayPacketAckMessage")

        raw_updates = _require_list(self.session_updates, "session_updates")
        updates: list[dict[str, Any]] = []
        for index, raw_update in enumerate(raw_updates):
            parsed = parse_server_message(_require_mapping(raw_update, f"session_updates[{index}]"))
            if not isinstance(parsed, RelaySessionUpdateMessage):
                raise ProtocolValidationError(f"session_updates[{index}] must parse as RelaySessionUpdateMessage")
            updates.append(dict(raw_update))
        self.session_updates = updates
        if subscribe_ack.accepted and not self.session_updates:
            raise ProtocolValidationError("accepted fixture must include at least one session_update")
        if not subscribe_ack.accepted and self.session_updates:
            raise ProtocolValidationError("rejected fixture must not include session_updates")

        if self.recovery_exchange is not None:
            self.recovery_exchange = _require_mapping(self.recovery_exchange, "recovery_exchange")
            recovery_request = parse_client_message(
                _require_mapping(self.recovery_exchange.get("request"), "recovery_exchange.request")
            )
            if not isinstance(recovery_request, RelayPacketMessage):
                raise ProtocolValidationError("recovery_exchange.request must parse as RelayPacketMessage")
            recovery_ack = parse_server_message(_require_mapping(self.recovery_exchange.get("ack"), "recovery_exchange.ack"))
            if not isinstance(recovery_ack, RelayPacketAckMessage):
                raise ProtocolValidationError("recovery_exchange.ack must parse as RelayPacketAckMessage")

        self.mail_companion = _require_mapping(self.mail_companion, "mail_companion")
        mail_items = _require_list(self.mail_companion.get("items"), "mail_companion.items")
        for index, item in enumerate(mail_items):
            mail_item = _require_mapping(item, f"mail_companion.items[{index}]")
            if _require_text(mail_item.get("source"), f"mail_companion.items[{index}].source") != "mail":
                raise ProtocolValidationError(f"mail_companion.items[{index}].source must equal 'mail'")
            _require_text(mail_item.get("business_event_key"), f"mail_companion.items[{index}].business_event_key")
            _require_text(mail_item.get("item_type"), f"mail_companion.items[{index}].item_type")
            _require_text(mail_item.get("status"), f"mail_companion.items[{index}].status")
            _require_text(mail_item.get("summary"), f"mail_companion.items[{index}].summary")
            _require_text(mail_item.get("arrived_at"), f"mail_companion.items[{index}].arrived_at")

        self.expected_projection = _require_mapping(self.expected_projection, "expected_projection")
        for field_name in _EXPECTED_PROJECTION_FIELDS:
            if field_name not in self.expected_projection:
                raise ProtocolValidationError(f"expected_projection must include {field_name}")
        self.expected_projection["canonical_workspace_id"] = _require_optional_text(
            self.expected_projection.get("canonical_workspace_id"),
            "expected_projection.canonical_workspace_id",
        )
        self.expected_projection["canonical_session_id"] = _require_optional_text(
            self.expected_projection.get("canonical_session_id"),
            "expected_projection.canonical_session_id",
        )
        self.expected_projection["canonical_thread_id"] = _require_optional_text(
            self.expected_projection.get("canonical_thread_id"),
            "expected_projection.canonical_thread_id",
        )
        self.expected_projection["header_status"] = _require_text(
            self.expected_projection.get("header_status"),
            "expected_projection.header_status",
        )
        self.expected_projection["header_lifecycle"] = _require_optional_text(
            self.expected_projection.get("header_lifecycle"),
            "expected_projection.header_lifecycle",
        )
        self.expected_projection["last_summary"] = _require_optional_text(
            self.expected_projection.get("last_summary"),
            "expected_projection.last_summary",
        )
        self.expected_projection["question_set_id"] = _require_optional_text(
            self.expected_projection.get("question_set_id"),
            "expected_projection.question_set_id",
        )
        self.expected_projection["pending_question_ids"] = [
            _require_text(item, f"expected_projection.pending_question_ids[{index}]")
            for index, item in enumerate(_require_list(self.expected_projection.get("pending_question_ids"), "expected_projection.pending_question_ids"))
        ]
        quick_answer_choices = _require_list(
            self.expected_projection.get("quick_answer_choices"),
            "expected_projection.quick_answer_choices",
        )
        normalized_quick_answer_choices: list[dict[str, str]] = []
        for index, item in enumerate(quick_answer_choices):
            choice = _require_mapping(item, f"expected_projection.quick_answer_choices[{index}]")
            normalized_quick_answer_choices.append(
                {
                    "value": _require_text(choice.get("value"), f"expected_projection.quick_answer_choices[{index}].value"),
                    "label": _require_text(choice.get("label"), f"expected_projection.quick_answer_choices[{index}].label"),
                }
            )
        self.expected_projection["quick_answer_choices"] = normalized_quick_answer_choices
        self.expected_projection["visible_business_event_keys"] = [
            _require_text(item, f"expected_projection.visible_business_event_keys[{index}]")
            for index, item in enumerate(
                _require_list(
                    self.expected_projection.get("visible_business_event_keys"),
                    "expected_projection.visible_business_event_keys",
                )
            )
        ]
        self.expected_projection["suppressed_direct_business_event_keys"] = [
            _require_text(item, f"expected_projection.suppressed_direct_business_event_keys[{index}]")
            for index, item in enumerate(
                _require_list(
                    self.expected_projection.get("suppressed_direct_business_event_keys"),
                    "expected_projection.suppressed_direct_business_event_keys",
                )
            )
        ]


def phase3_fixture_root() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "plans" / "fixtures" / "phase3_direct_inbound_v1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProtocolValidationError(f"fixture file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolValidationError(f"fixture file is not valid JSON: {path}") from exc
    return _require_mapping(payload, str(path))


def load_phase3_fixture_manifest(root: Path | None = None) -> Phase3FixtureManifest:
    fixture_root = Path(root) if root is not None else phase3_fixture_root()
    payload = _read_json(fixture_root / _FIXTURE_MANIFEST)
    entries = [
        Phase3FixtureManifestEntry(
            fixture_id=entry.get("fixture_id"),
            file=entry.get("file"),
            category=entry.get("category"),
        )
        for entry in _require_list(payload.get("fixtures"), "fixtures")
    ]
    manifest = Phase3FixtureManifest(
        schema_version=payload.get("schema_version"),
        package_id=payload.get("package_id"),
        generated_at=payload.get("generated_at"),
        fixtures=entries,
    )
    for entry in manifest.fixtures:
        fixture_path = fixture_root / entry.file
        if not fixture_path.exists():
            raise ProtocolValidationError(f"manifest references missing fixture file: {entry.file}")
    return manifest


def load_phase3_fixture_unit(fixture_id: str, root: Path | None = None) -> Phase3FixtureUnit:
    fixture_root = Path(root) if root is not None else phase3_fixture_root()
    manifest = load_phase3_fixture_manifest(fixture_root)
    for entry in manifest.fixtures:
        if entry.fixture_id == fixture_id:
            payload = _read_json(fixture_root / entry.file)
            return Phase3FixtureUnit(
                fixture_meta=payload.get("fixture_meta"),
                subscribe_exchange=payload.get("subscribe_exchange"),
                session_updates=payload.get("session_updates"),
                recovery_exchange=payload.get("recovery_exchange"),
                mail_companion=payload.get("mail_companion"),
                expected_projection=payload.get("expected_projection"),
            )
    raise ProtocolValidationError(f"unknown fixture_id: {fixture_id}")


def iter_phase3_fixture_units(root: Path | None = None) -> list[Phase3FixtureUnit]:
    fixture_root = Path(root) if root is not None else phase3_fixture_root()
    manifest = load_phase3_fixture_manifest(fixture_root)
    return [load_phase3_fixture_unit(entry.fixture_id, fixture_root) for entry in manifest.fixtures]
