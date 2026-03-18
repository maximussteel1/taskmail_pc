"""Live COS smoke test: upload, presigned download, hash verify, and cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from qcloud_cos import CosConfig, CosS3Client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cos_config(path: Path) -> dict[str, str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"COS config must be a mapping: {path}")
    required = {
        "cos_region": "region",
        "cos_bucket": "bucket",
        "cos_secret_id": "secret_id",
        "cos_secret_key": "secret_key",
    }
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for field_name, label in required.items():
        value = str(raw.get(field_name) or "").strip()
        if value:
            resolved[label] = value
        else:
            missing.append(field_name)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"Missing COS config fields in {path}: {missing_text}")
    return resolved


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a file to COS, download it back through a presigned URL, verify hashes, and delete it."
    )
    parser.add_argument(
        "--cos-config",
        default=str(PROJECT_ROOT / "mail_config.cos.local.yaml"),
        help="Path to the local COS config file.",
    )
    parser.add_argument(
        "--source",
        default=str(PROJECT_ROOT / "README.md"),
        help="Local file to upload. Defaults to the repository README.md.",
    )
    parser.add_argument(
        "--expire-seconds",
        type=int,
        default=600,
        help="Presigned download URL lifetime in seconds.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "_tmp_live_cos_smoke"),
        help="Directory where smoke-test artifacts are written.",
    )
    parser.add_argument(
        "--object-prefix",
        default="live-smoke",
        help="Key prefix under the bucket.",
    )
    parser.add_argument(
        "--keep-object",
        action="store_true",
        help="Keep the uploaded object instead of deleting it after verification.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    source_path = Path(args.source).resolve()
    if not source_path.exists() or not source_path.is_file():
        raise SystemExit(f"Source file does not exist: {source_path}")

    cos_config = _load_cos_config(Path(args.cos_config).resolve())
    run_token = f"cos-{_timestamp_slug()}-{secrets.token_hex(3)}"
    run_dir = Path(args.output_dir).resolve() / run_token
    run_dir.mkdir(parents=True, exist_ok=True)

    object_key = "/".join(
        part.strip("/")
        for part in (
            args.object_prefix.strip("/"),
            run_token,
            source_path.name,
        )
        if part and part.strip("/")
    )
    downloaded_path = run_dir / f"downloaded_{source_path.name}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=args.expire_seconds)

    summary: dict[str, Any] = {
        "run_token": run_token,
        "source_path": str(source_path),
        "bucket": cos_config["bucket"],
        "region": cos_config["region"],
        "object_key": object_key,
        "expire_seconds": args.expire_seconds,
        "expires_at_utc": expires_at.replace(microsecond=0).isoformat(),
        "passed": False,
        "cleanup_deleted": False,
    }

    client = CosS3Client(
        CosConfig(
            Region=cos_config["region"],
            SecretId=cos_config["secret_id"],
            SecretKey=cos_config["secret_key"],
            Scheme="https",
        )
    )

    source_sha256 = _sha256(source_path)
    summary["source_sha256"] = source_sha256
    summary["source_size_bytes"] = source_path.stat().st_size

    try:
        upload_response = client.upload_file(
            Bucket=cos_config["bucket"],
            Key=object_key,
            LocalFilePath=str(source_path),
            EnableMD5=True,
        )
        summary["upload_response"] = {
            "etag": str(upload_response.get("ETag") or ""),
            "x_cos_request_id": str(upload_response.get("x-cos-request-id") or ""),
        }

        download_url = client.get_presigned_download_url(
            Bucket=cos_config["bucket"],
            Key=object_key,
            Expired=args.expire_seconds,
        )
        summary["download_url"] = download_url

        with urllib.request.urlopen(download_url) as response:
            payload = response.read()
            downloaded_path.write_bytes(payload)
            summary["download_http_status"] = getattr(response, "status", None)
            summary["download_content_length"] = len(payload)

        downloaded_sha256 = _sha256(downloaded_path)
        summary["downloaded_path"] = str(downloaded_path)
        summary["downloaded_sha256"] = downloaded_sha256
        summary["hash_match"] = downloaded_sha256 == source_sha256
        if not summary["hash_match"]:
            raise RuntimeError("Downloaded file hash does not match source file hash.")

        if not args.keep_object:
            delete_response = client.delete_object(Bucket=cos_config["bucket"], Key=object_key)
            summary["cleanup_deleted"] = True
            summary["delete_response"] = {
                "x_cos_request_id": str(delete_response.get("x-cos-request-id") or ""),
            }

        summary["passed"] = True
        _write_json(run_dir / "result.json", summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        summary["error"] = str(exc)
        _write_json(run_dir / "result.json", summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
