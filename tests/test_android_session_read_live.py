from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mail_runner.android_relay_test_client import derive_android_relay_base_url_from_relay_url
from mail_runner.android_session_read_live import (
    build_android_session_locator_query_params,
    build_android_sessions_query_params,
    resolve_android_app_token,
    resolve_android_read_base_url,
    resolve_android_read_timeout_seconds,
    resolve_android_read_verify,
)


def test_derive_android_relay_base_url_from_relay_url_converts_ws_origin() -> None:
    assert derive_android_relay_base_url_from_relay_url("ws://127.0.0.1:8787/relay") == "http://127.0.0.1:8787"
    assert derive_android_relay_base_url_from_relay_url("wss://relay.example.com/control") == "https://relay.example.com"


def test_build_android_sessions_query_params_only_emits_non_blank_filters() -> None:
    assert build_android_sessions_query_params(
        pc_id="pc-home",
        workspace_id="workspace_001",
        session_id="session_001",
        thread_id="thread_001",
        include_ended=True,
    ) == {
        "pc_id": "pc-home",
        "workspace_id": "workspace_001",
        "session_id": "session_001",
        "thread_id": "thread_001",
        "include_ended": "true",
    }


def test_build_android_session_locator_query_params_requires_session_id_or_thread_id() -> None:
    with pytest.raises(ValueError, match="requires --session-id or --thread-id"):
        build_android_session_locator_query_params()


def test_build_android_session_locator_query_params_keeps_supporting_locator() -> None:
    assert build_android_session_locator_query_params(
        workspace_id="workspace_001",
        repo_path="E:\\projects\\alpha",
        workdir="main",
        session_id="session_001",
    ) == {
        "workspace_id": "workspace_001",
        "repo_path": "E:\\projects\\alpha",
        "workdir": "main",
        "session_id": "session_001",
    }


def test_resolve_android_read_base_url_uses_config_relay_url(tmp_path: Path) -> None:
    config_path = tmp_path / "mail_config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            relay_url: ws://124.223.41.153:8787/relay
            relay_timeout_seconds: 45
            relay_verify_tls: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    assert resolve_android_read_base_url(config_path=config_path) == "http://124.223.41.153:8787"
    assert resolve_android_read_timeout_seconds(config_path=config_path) == 45
    assert resolve_android_read_verify(config_path=config_path) is False


def test_resolve_android_read_verify_rejects_insecure_plus_ca_file() -> None:
    with pytest.raises(ValueError, match="cannot be used together"):
        resolve_android_read_verify(insecure=True, ca_file="ca.pem")


def test_resolve_android_app_token_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_RELAY_ANDROID_APP_TOKEN", "android-secret")

    assert resolve_android_app_token(None) == "android-secret"
