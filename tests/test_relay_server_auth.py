from __future__ import annotations

from mail_runner.relay_server.auth import token_fingerprint, validate_transport_token


def test_validate_transport_token_uses_exact_match() -> None:
    assert validate_transport_token("secret-token", "secret-token") is True
    assert validate_transport_token("secret-token", "other-token") is False
    assert validate_transport_token("secret-token", "") is False


def test_token_fingerprint_is_stable_short_hash() -> None:
    assert token_fingerprint("secret-token") == token_fingerprint("secret-token")
    assert len(token_fingerprint("secret-token")) == 12
