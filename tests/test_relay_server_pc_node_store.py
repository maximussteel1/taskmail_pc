from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_node_store import InMemoryPcNodeStore, PcNodeFenceError


def _capabilities() -> dict[str, object]:
    return {
        "supported_backends": ["codex"],
        "profile_catalogs": {"codex": ["fast", "strong"]},
        "permission_modes": ["default", "highest"],
        "backend_transport_modes": {"codex": ["cli", "sdk"]},
    }


def test_pc_node_store_registers_connections_and_increments_epoch() -> None:
    store = InMemoryPcNodeStore()

    first = store.register_connection(
        pc_id="pc_home",
        display_name="Home PC",
        auth_credential_id="cred_pc_home",
        connection_id="conn_001",
        connected_at="2026-03-25T10:00:00",
        last_seen_at="2026-03-25T10:00:00",
        client_version="0.1.0",
        host_fingerprint="host_001",
        runtime_fingerprint="runtime_001",
        capabilities=_capabilities(),
    )
    second = store.register_connection(
        pc_id="pc_home",
        display_name="Home PC",
        auth_credential_id="cred_pc_home",
        connection_id="conn_002",
        connected_at="2026-03-25T10:01:00",
        last_seen_at="2026-03-25T10:01:00",
        client_version="0.1.1",
        host_fingerprint="host_001",
        runtime_fingerprint="runtime_001",
        capabilities=_capabilities(),
    )

    assert first.current_connection_epoch == 1
    assert second.current_connection_epoch == 2
    assert store.get_node("pc_home").current_connection_id == "conn_002"


def test_pc_node_store_rejects_stale_epoch_on_touch() -> None:
    store = InMemoryPcNodeStore()
    store.register_connection(
        pc_id="pc_home",
        display_name="Home PC",
        auth_credential_id="cred_pc_home",
        connection_id="conn_001",
        connected_at="2026-03-25T10:00:00",
        last_seen_at="2026-03-25T10:00:00",
        client_version="0.1.0",
        host_fingerprint="host_001",
        runtime_fingerprint="runtime_001",
        capabilities=_capabilities(),
    )
    store.register_connection(
        pc_id="pc_home",
        display_name="Home PC",
        auth_credential_id="cred_pc_home",
        connection_id="conn_002",
        connected_at="2026-03-25T10:01:00",
        last_seen_at="2026-03-25T10:01:00",
        client_version="0.1.0",
        host_fingerprint="host_001",
        runtime_fingerprint="runtime_001",
        capabilities=_capabilities(),
    )

    with pytest.raises(PcNodeFenceError, match="stale connection_epoch"):
        store.touch_connection(
            pc_id="pc_home",
            connection_id="conn_001",
            connection_epoch=1,
            last_seen_at="2026-03-25T10:01:05",
        )
