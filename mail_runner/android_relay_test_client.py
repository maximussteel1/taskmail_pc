"""Thin Android app-facing relay client helpers for live probes."""

from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

DEFAULT_ANDROID_RELAY_BASE_URL = "http://127.0.0.1:8787"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def normalize_android_relay_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip()
    if not normalized:
        raise ValueError("base_url must be a non-empty string")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base_url must use http:// or https://")
    if not parsed.netloc:
        raise ValueError("base_url must include host[:port]")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def derive_android_relay_base_url_from_relay_url(relay_url: str) -> str:
    normalized = str(relay_url or "").strip()
    if not normalized:
        raise ValueError("relay_url must be a non-empty string")
    parsed = urllib.parse.urlsplit(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise ValueError("relay_url must use ws:// or wss://")
    http_scheme = "https" if scheme == "wss" else "http"
    return urlunsplit((http_scheme, parsed.netloc, "", "", ""))


def build_android_relay_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def build_create_session_payload(
    *,
    pc_id: str,
    workspace_id: str,
    prompt: str,
    backend: str = "codex",
    profile: str | None = "default",
    permission: str | None = "default",
    backend_transport: str | None = "sdk",
    mode: str | None = None,
    timeout_seconds: int | None = None,
    acceptance: list[str] | None = None,
    repo_path: str | None = None,
    workdir: str | None = None,
    canonical_reply_recipient: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pc_id": str(pc_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "prompt": str(prompt or "").strip(),
        "execution_policy": {
            "backend": str(backend or "").strip(),
        },
    }
    if profile:
        payload["execution_policy"]["profile"] = str(profile).strip()
    if permission:
        payload["execution_policy"]["permission"] = str(permission).strip()
    if backend_transport:
        payload["execution_policy"]["backend_transport"] = str(backend_transport).strip()
    if mode:
        payload["mode"] = str(mode).strip()
    if timeout_seconds is not None:
        payload["timeout_seconds"] = int(timeout_seconds)
    if acceptance:
        payload["acceptance"] = [str(item).strip() for item in acceptance if str(item).strip()]
    if repo_path:
        payload["repo_path"] = str(repo_path).strip()
    if workdir:
        payload["workdir"] = str(workdir).strip()
    if canonical_reply_recipient:
        payload["canonical_reply_recipient"] = str(canonical_reply_recipient).strip()
    if source:
        payload["source"] = str(source).strip()
    return payload


def build_fake_reply_payload(
    *,
    session_id: str,
    reply_text: str,
    workspace_id: str | None = None,
    thread_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    target: dict[str, Any] = {
        "session_id": str(session_id or "").strip(),
    }
    if workspace_id:
        target["workspace_id"] = str(workspace_id).strip()
    if thread_id:
        target["thread_id"] = str(thread_id).strip()
    return {
        "request_id": str(request_id or "").strip()
        or f"req_fake_reply_{_timestamp_slug()}_{uuid.uuid4().hex[:8]}",
        "action": "reply",
        "target": target,
        "reply": {
            "reply_text": str(reply_text or "").strip(),
        },
    }


def post_android_relay_json(
    *,
    base_url: str,
    path: str,
    android_app_token: str,
    payload: dict[str, Any],
    timeout_seconds: int = 30,
    verify: bool | str = True,
) -> dict[str, Any]:
    normalized_base_url = normalize_android_relay_base_url(base_url)
    normalized_path = str(path or "").strip()
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    session = build_android_relay_requests_session()
    try:
        response = session.post(
            f"{normalized_base_url}{normalized_path}",
            headers={
                "Authorization": f"Bearer {android_app_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=max(1, int(timeout_seconds)),
            verify=verify,
        )
    finally:
        session.close()
    body: Any
    try:
        body = response.json()
    except Exception:
        body = {
            "status": "error",
            "error_code": "non_json_response",
            "error_message": response.text,
            "retryable": False,
        }
    return {
        "http_status": response.status_code,
        "payload": body,
    }


def get_android_relay_json(
    *,
    base_url: str,
    path: str,
    android_app_token: str,
    query: dict[str, str] | None = None,
    timeout_seconds: int = 30,
    verify: bool | str = True,
) -> dict[str, Any]:
    normalized_base_url = normalize_android_relay_base_url(base_url)
    normalized_path = str(path or "").strip()
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    session = build_android_relay_requests_session()
    try:
        response = session.get(
            f"{normalized_base_url}{normalized_path}",
            headers={
                "Authorization": f"Bearer {android_app_token}",
                "Accept": "application/json",
            },
            params=(query or None),
            timeout=max(1, int(timeout_seconds)),
            verify=verify,
        )
    finally:
        session.close()
    body: Any
    try:
        body = response.json()
    except Exception:
        body = {
            "status": "error",
            "error_code": "non_json_response",
            "error_message": response.text,
            "retryable": False,
        }
    return {
        "http_status": response.status_code,
        "payload": body,
    }


def write_probe_output(path: str | Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
