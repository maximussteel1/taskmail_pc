"""Authenticated consumer smoke for current relay-hosted /v1/files download_ref URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from .artifact_contract_smoke import run_artifact_contract_smoke
from .config import AppConfig, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_tmp_file_surface_consumer_smoke"


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _build_direct_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return session


def _load_live_relay_config(config_path: Path) -> AppConfig:
    config = load_config(str(config_path))
    if str(config.outbound_transport or "").strip().lower() != "relay":
        raise ValueError("file-surface consumer smoke requires outbound_transport=relay")
    if not str(config.relay_url or "").strip():
        raise ValueError("file-surface consumer smoke requires relay_url")
    if not str(config.relay_transport_token or "").strip():
        raise ValueError("file-surface consumer smoke requires relay_transport_token")
    return config


def _relay_verify_arg(config: AppConfig) -> bool | str:
    ca_file = str(config.relay_ca_file or "").strip()
    if ca_file:
        return ca_file
    return bool(config.relay_verify_tls)


def _fetch_consumer_response(
    url: str,
    *,
    auth_token: str | None,
    verify: bool | str,
    timeout_seconds: int,
) -> dict[str, Any]:
    session = _build_direct_requests_session()
    headers: dict[str, str] = {}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    try:
        response = session.get(
            url,
            headers=headers,
            timeout=max(1, int(timeout_seconds)),
            verify=verify,
        )
        content = response.content
        try:
            json_payload = response.json()
        except Exception:
            json_payload = None
        return {
            "status_code": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "etag": response.headers.get("ETag"),
            "json_payload": json_payload,
            "content_length": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest() if content else None,
            "content_preview_text": (
                content.decode("utf-8", errors="replace")
                if content and response.headers.get("Content-Type", "").startswith("text/")
                else None
            ),
        }
    finally:
        session.close()


def run_file_surface_consumer_smoke(
    *,
    output_dir: Path,
    run_name: str,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    run_root = output_dir / run_name
    owner_result = run_artifact_contract_smoke(
        output_dir=run_root / "artifact_contract_smoke",
        run_name=f"{run_name}-artifact-contract",
        config_path=config_path,
    )

    live_mode = config_path is not None
    if live_mode:
        effective_config = _load_live_relay_config(Path(config_path).resolve())
        transport_token = str(effective_config.relay_transport_token or "").strip()
        verify_arg = _relay_verify_arg(effective_config)
        timeout_seconds = int(effective_config.relay_timeout_seconds)
    else:
        transport_token = "relay-secret"
        verify_arg = True
        timeout_seconds = 5

    failures: list[str] = []
    if not owner_result.get("success"):
        failures.append("owner-lane staging smoke failed")

    preview_item = next(
        (
            item
            for item in list(owner_result.get("candidate_artifact_manifest") or [])
            if isinstance(item, dict) and item.get("artifact_id") == "artifact-preview"
        ),
        None,
    )
    if not isinstance(preview_item, dict):
        failures.append("artifact-preview did not appear in the staged artifact_manifest")
        download_ref = ""
        download_ref_source = None
    else:
        download_ref = str(preview_item.get("download_ref") or "").strip()
        download_ref_source = str(preview_item.get("download_ref_source") or "").strip() or None
        if not download_ref:
            failures.append("artifact-preview download_ref was empty")
        if download_ref_source != "external_delivery_index.file_surface":
            failures.append("artifact-preview download_ref_source did not resolve to external_delivery_index.file_surface")

    preview_path = Path(str(owner_result.get("task_root") or "")).parent / "preview.png"
    expected_preview_bytes = preview_path.read_bytes() if preview_path.exists() else b""
    expected_sha256 = hashlib.sha256(expected_preview_bytes).hexdigest() if expected_preview_bytes else None

    authenticated_fetch = (
        _fetch_consumer_response(
            download_ref,
            auth_token=transport_token,
            verify=verify_arg,
            timeout_seconds=timeout_seconds,
        )
        if download_ref
        else None
    )
    anonymous_fetch = (
        _fetch_consumer_response(
            download_ref,
            auth_token=None,
            verify=verify_arg,
            timeout_seconds=timeout_seconds,
        )
        if download_ref
        else None
    )
    wrong_token_fetch = (
        _fetch_consumer_response(
            download_ref,
            auth_token="wrong-transport-token",
            verify=verify_arg,
            timeout_seconds=timeout_seconds,
        )
        if download_ref
        else None
    )

    if authenticated_fetch is None:
        failures.append("authenticated download_ref fetch did not run")
    else:
        authenticated_fetch["content_verified"] = (
            expected_sha256 is not None and authenticated_fetch.get("content_sha256") == expected_sha256
        )
        if authenticated_fetch["status_code"] != 200:
            failures.append(f"authenticated download_ref fetch returned {authenticated_fetch['status_code']} instead of 200")
        elif not authenticated_fetch["content_verified"]:
            failures.append("authenticated download_ref fetch did not return the expected bytes")

    for label, observation in (
        ("anonymous", anonymous_fetch),
        ("wrong_token", wrong_token_fetch),
    ):
        if observation is None:
            failures.append(f"{label} download_ref fetch did not run")
            continue
        error_payload = observation.get("json_payload")
        if observation["status_code"] != 401:
            failures.append(f"{label} download_ref fetch returned {observation['status_code']} instead of 401")
        elif not isinstance(error_payload, dict) or str(error_payload.get("error_code") or "").strip() != "unauthorized":
            failures.append(f"{label} download_ref fetch did not return unauthorized error payload")

    smoke_result = {
        "success": not failures,
        "run_name": run_name,
        "smoke_mode": "live_relay_authenticated_consumer" if live_mode else "local_fixture_authenticated_consumer",
        "config_path": str(Path(config_path).resolve()) if config_path is not None else None,
        "consumer_contract_scope": "transport-token-scoped /v1/files download_ref consumption",
        "owner_smoke_result_path": owner_result.get("smoke_result_path"),
        "owner_smoke_success": bool(owner_result.get("success")),
        "consumer_download_ref": download_ref or None,
        "consumer_download_ref_source": download_ref_source,
        "authenticated_fetch": authenticated_fetch,
        "anonymous_fetch": anonymous_fetch,
        "wrong_token_fetch": wrong_token_fetch,
        "gaps": [
            {
                "kind": "anonymous_or_android_app_token_public_access_not_proven",
                "summary": (
                    "This smoke only proves the current transport-token-scoped consumer path. "
                    "It does not upgrade /v1/files into an anonymous public URL or a general Android app API."
                ),
                "recorded": True,
            }
        ],
        "failures": failures,
    }
    smoke_result_path = run_root / "smoke_result.json"
    smoke_result["smoke_result_path"] = str(smoke_result_path)
    _write_json(smoke_result_path, smoke_result)
    return smoke_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an authenticated consumer smoke for relay-hosted /v1/files download_ref URLs."
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", help="Optional fixed run name.")
    parser.add_argument(
        "--config",
        help="Optional relay-enabled config path. When provided, the smoke targets the real relay host instead of a local fixture server.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_name = args.run_name or f"file-surface-consumer-smoke-{_timestamp_slug()}"
    result = run_file_surface_consumer_smoke(
        output_dir=Path(args.output_dir),
        run_name=run_name,
        config_path=args.config,
    )
    print(f"result: {result['smoke_result_path']}")
    print(json.dumps({"success": result["success"]}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
