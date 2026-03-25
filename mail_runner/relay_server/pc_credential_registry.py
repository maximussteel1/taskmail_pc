"""Credential registry for the PC control plane."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock

from .auth import token_fingerprint


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload is not None else default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def hash_transport_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


@dataclass(slots=True)
class PcCredentialRecord:
    auth_credential_id: str
    token_sha256: str
    enabled: bool = True
    pc_id: str | None = None
    display_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        self.auth_credential_id = _require_text(self.auth_credential_id, "auth_credential_id")
        self.token_sha256 = _require_text(self.token_sha256, "token_sha256")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a bool")
        self.pc_id = _require_optional_text(self.pc_id, "pc_id")
        self.display_name = _require_optional_text(self.display_name, "display_name")
        self.created_at = _require_optional_text(self.created_at, "created_at")
        self.updated_at = _require_optional_text(self.updated_at, "updated_at")


class InMemoryPcCredentialRegistry:
    def __init__(
        self,
        records: list[PcCredentialRecord] | None = None,
        *,
        default_transport_token: str | None = None,
    ) -> None:
        self._records: dict[str, PcCredentialRecord] = {}
        self._lock = Lock()
        self._default_transport_token = str(default_transport_token or "").strip()
        for record in records or []:
            self._records[record.auth_credential_id] = record

    def resolve_token(self, provided_token: str) -> PcCredentialRecord | None:
        normalized_token = str(provided_token or "").strip()
        if not normalized_token:
            return None
        normalized_hash = hash_transport_token(normalized_token)
        with self._lock:
            for record in self._records.values():
                if not record.enabled:
                    continue
                if hmac.compare_digest(record.token_sha256, normalized_hash):
                    return record
        if self._default_transport_token and hmac.compare_digest(normalized_token, self._default_transport_token):
            fingerprint = token_fingerprint(self._default_transport_token)
            return PcCredentialRecord(
                auth_credential_id=f"legacy:{fingerprint}",
                token_sha256=hash_transport_token(self._default_transport_token),
                enabled=True,
            )
        return None

    def upsert_credential(self, record: PcCredentialRecord) -> PcCredentialRecord:
        with self._lock:
            self._records[record.auth_credential_id] = record
            return record

    def list_credentials(self) -> list[PcCredentialRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda item: item.auth_credential_id)


class PersistentPcCredentialRegistry(InMemoryPcCredentialRegistry):
    def __init__(
        self,
        path: str | Path,
        *,
        default_transport_token: str | None = None,
    ) -> None:
        self._path = Path(path)
        payload = _load_json(self._path, default={"credentials": []})
        records: list[PcCredentialRecord] = []
        for item in payload.get("credentials", []) if isinstance(payload, dict) else []:
            records.append(PcCredentialRecord(**item))
        super().__init__(records, default_transport_token=default_transport_token)

    def upsert_credential(self, record: PcCredentialRecord) -> PcCredentialRecord:
        updated = super().upsert_credential(record)
        self._save()
        return updated

    def _save(self) -> None:
        payload = {
            "version": 1,
            "credentials": [asdict(item) for item in self.list_credentials()],
        }
        _write_json(self._path, payload)
