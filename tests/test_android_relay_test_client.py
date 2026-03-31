from __future__ import annotations

import pytest

from mail_runner.android_relay_test_client import (
    build_create_session_payload,
    build_fake_reply_payload,
    normalize_android_relay_base_url,
)


def test_normalize_android_relay_base_url_trims_path_and_keeps_origin() -> None:
    assert normalize_android_relay_base_url("http://127.0.0.1:8787/healthz") == "http://127.0.0.1:8787"


def test_normalize_android_relay_base_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="base_url must use http:// or https://"):
        normalize_android_relay_base_url("ws://127.0.0.1:8787")


def test_build_create_session_payload_omits_blank_optional_fields() -> None:
    payload = build_create_session_payload(
        pc_id="pc-home",
        workspace_id="workspace_001",
        prompt="say hi",
        backend="codex",
        profile="default",
        permission="default",
        backend_transport="sdk",
        acceptance=["return hi", "  "],
        source="vps_probe",
    )

    assert payload == {
        "pc_id": "pc-home",
        "workspace_id": "workspace_001",
        "prompt": "say hi",
        "execution_policy": {
            "backend": "codex",
            "profile": "default",
            "permission": "default",
            "backend_transport": "sdk",
        },
        "acceptance": ["return hi"],
        "source": "vps_probe",
    }


def test_build_fake_reply_payload_generates_request_id_and_supporting_locator() -> None:
    payload = build_fake_reply_payload(
        session_id="thread_001",
        reply_text="say ho",
        workspace_id="workspace_001",
        thread_id="thread_001",
    )

    assert payload["request_id"].startswith("req_fake_reply_")
    assert payload["action"] == "reply"
    assert payload["target"] == {
        "session_id": "thread_001",
        "workspace_id": "workspace_001",
        "thread_id": "thread_001",
    }
    assert payload["reply"] == {"reply_text": "say ho"}
