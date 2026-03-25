"""Relay transport authentication helpers."""

from __future__ import annotations

import hashlib
import hmac


def validate_bearer_token(provided_token: str, expected_token: str) -> bool:
    normalized_expected = str(expected_token or "")
    if not normalized_expected:
        return False
    return hmac.compare_digest(str(provided_token or ""), normalized_expected)


def validate_transport_token(provided_token: str, expected_token: str) -> bool:
    return validate_bearer_token(provided_token, expected_token)


def token_fingerprint(token: str) -> str:
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    return digest[:12]
