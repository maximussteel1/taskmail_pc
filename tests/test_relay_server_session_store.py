from __future__ import annotations

from mail_runner.relay_server.session_store import InMemorySessionStore, PersistentSessionStore


def test_in_memory_session_store_tracks_session_lifecycle() -> None:
    store = InMemorySessionStore()

    session = store.upsert_session(
        connection_id="conn-001",
        client_id="pc-001",
        connected_at="2026-03-20T13:35:00",
        last_seen_at="2026-03-20T13:35:00",
    )
    touched = store.touch_session("conn-001", last_seen_at="2026-03-20T13:35:05")
    listed = store.list_sessions()

    assert session.connection_id == "conn-001"
    assert touched is not None
    assert touched.last_seen_at == "2026-03-20T13:35:05"
    assert listed[0].client_id == "pc-001"
    assert store.count() == 1
    removed = store.remove_session("conn-001")
    assert removed is not None
    assert store.count() == 0


def test_touch_session_returns_none_for_unknown_connection() -> None:
    store = InMemorySessionStore()

    missing = store.touch_session("missing-conn", last_seen_at="2026-03-20T13:40:00")

    assert missing is None


def test_persistent_session_store_survives_restart(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    store = PersistentSessionStore(state_dir)

    store.upsert_session(
        connection_id="conn-001",
        client_id="pc-001",
        connected_at="2026-03-20T13:35:00",
        last_seen_at="2026-03-20T13:35:00",
    )
    store.close_session("conn-001", closed_at="2026-03-20T13:36:00")

    reloaded = PersistentSessionStore(state_dir)
    session = reloaded.get_session("conn-001")

    assert session is not None
    assert session.client_id == "pc-001"
    assert session.closed_at == "2026-03-20T13:36:00"
