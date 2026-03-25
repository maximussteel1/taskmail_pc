from __future__ import annotations

from mail_runner.relay_server.pc_credential_registry import (
    InMemoryPcCredentialRegistry,
    PcCredentialRecord,
    PersistentPcCredentialRegistry,
    hash_transport_token,
)


def test_pc_credential_registry_resolves_explicit_record(tmp_path) -> None:
    registry_path = tmp_path / "pc_credentials.json"
    registry = PersistentPcCredentialRegistry(registry_path)
    registry.upsert_credential(
        PcCredentialRecord(
            auth_credential_id="cred_pc_home",
            token_sha256=hash_transport_token("pc-secret"),
            pc_id="pc_home",
            display_name="Home PC",
        )
    )

    resolved = registry.resolve_token("pc-secret")

    assert resolved is not None
    assert resolved.auth_credential_id == "cred_pc_home"
    assert resolved.pc_id == "pc_home"


def test_pc_credential_registry_falls_back_to_default_transport_token() -> None:
    registry = InMemoryPcCredentialRegistry(default_transport_token="relay-secret")

    resolved = registry.resolve_token("relay-secret")

    assert resolved is not None
    assert resolved.auth_credential_id.startswith("legacy:")
    assert resolved.pc_id is None
