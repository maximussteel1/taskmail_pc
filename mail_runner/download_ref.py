"""Canonical helpers for control-plane download_ref payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_FILE_SURFACE_PATH_PATTERN = re.compile(r"^/v1/files/(?P<file_id>[^/?#]+)(?:/content)?/?$")


def _opt_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _require_text(value: Any, field_name: str) -> str:
    text = _opt_text(value)
    if text is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def build_vps_file_download_ref(
    *,
    file_id: str | None = None,
    metadata_url: str | None = None,
    content_url: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any] | None:
    normalized_file_id = _opt_text(file_id)
    normalized_metadata_url = _opt_text(metadata_url)
    normalized_content_url = _opt_text(content_url)
    normalized_content_type = _opt_text(content_type)

    if normalized_file_id is None:
        normalized_file_id = _extract_file_id(normalized_content_url) or _extract_file_id(normalized_metadata_url)
    if normalized_metadata_url is None and normalized_file_id is not None:
        normalized_metadata_url = _build_metadata_url(normalized_content_url, normalized_file_id)
    if normalized_content_url is None and normalized_file_id is not None:
        normalized_content_url = _build_content_url(normalized_metadata_url, normalized_file_id)

    if normalized_file_id is None and normalized_metadata_url is None and normalized_content_url is None:
        return None

    payload: dict[str, Any] = {"kind": "vps_file"}
    if normalized_file_id is not None:
        payload["file_id"] = normalized_file_id
    if normalized_metadata_url is not None:
        payload["metadata_url"] = normalized_metadata_url
    if normalized_content_url is not None:
        payload["content_url"] = normalized_content_url
    if normalized_content_type is not None:
        payload["content_type"] = normalized_content_type
    return payload


def build_external_download_ref(
    *,
    url: str,
    content_type: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "external_url",
        "url": _require_text(url, "url"),
    }
    normalized_content_type = _opt_text(content_type)
    if normalized_content_type is not None:
        payload["content_type"] = normalized_content_type
    return payload


def resolve_download_ref_url(download_ref: Any) -> str | None:
    if download_ref is None:
        return None
    if isinstance(download_ref, str):
        return _opt_text(download_ref)
    if not isinstance(download_ref, dict):
        return None

    kind = _opt_text(download_ref.get("kind"))
    if kind == "vps_file":
        return _opt_text(download_ref.get("content_url")) or _opt_text(download_ref.get("url"))
    if kind == "external_url":
        return _opt_text(download_ref.get("url"))
    return _opt_text(download_ref.get("content_url")) or _opt_text(download_ref.get("url"))


def normalize_download_ref(download_ref: Any, *, field_name: str = "download_ref") -> dict[str, Any] | None:
    if download_ref is None:
        return None

    if isinstance(download_ref, str):
        normalized_text = _opt_text(download_ref)
        if normalized_text is None:
            return None
        vps_ref = build_vps_file_download_ref(content_url=normalized_text)
        if vps_ref is not None:
            return vps_ref
        return build_external_download_ref(url=normalized_text)

    if not isinstance(download_ref, dict):
        raise ValueError(f"{field_name} must be a mapping or string")

    kind = _require_text(download_ref.get("kind"), f"{field_name}.kind")
    if kind == "vps_file":
        normalized = build_vps_file_download_ref(
            file_id=_opt_text(download_ref.get("file_id")),
            metadata_url=_opt_text(download_ref.get("metadata_url")),
            content_url=_opt_text(download_ref.get("content_url")) or _opt_text(download_ref.get("url")),
            content_type=_opt_text(download_ref.get("content_type")),
        )
        if normalized is None:
            raise ValueError(
                f"{field_name} must include file_id, metadata_url, or content_url for kind=vps_file",
            )
        return normalized

    if kind == "external_url":
        return build_external_download_ref(
            url=_require_text(download_ref.get("url"), f"{field_name}.url"),
            content_type=_opt_text(download_ref.get("content_type")),
        )

    if kind == "inline_data":
        payload = {
            "kind": "inline_data",
            "data": _require_text(download_ref.get("data"), f"{field_name}.data"),
        }
        encoding = _opt_text(download_ref.get("encoding"))
        content_type = _opt_text(download_ref.get("content_type"))
        if encoding is not None:
            payload["encoding"] = encoding
        if content_type is not None:
            payload["content_type"] = content_type
        return payload

    raise ValueError(f"{field_name}.kind is unsupported: {kind}")


def _extract_file_id(url: str | None) -> str | None:
    normalized_url = _opt_text(url)
    if normalized_url is None:
        return None

    parsed = urlsplit(normalized_url)
    path = parsed.path if parsed.scheme else normalized_url
    matched = _FILE_SURFACE_PATH_PATTERN.match(path)
    if matched is None:
        return None
    return matched.group("file_id")


def _build_metadata_url(reference_url: str | None, file_id: str) -> str:
    normalized_reference = _opt_text(reference_url)
    if normalized_reference is None:
        return f"/v1/files/{file_id}"

    parsed = urlsplit(normalized_reference)
    if not parsed.scheme:
        return f"/v1/files/{file_id}"
    return urlunsplit((parsed.scheme, parsed.netloc, f"/v1/files/{file_id}", "", ""))


def _build_content_url(reference_url: str | None, file_id: str) -> str:
    normalized_reference = _opt_text(reference_url)
    if normalized_reference is None:
        return f"/v1/files/{file_id}/content"

    parsed = urlsplit(normalized_reference)
    if not parsed.scheme:
        return f"/v1/files/{file_id}/content"
    return urlunsplit((parsed.scheme, parsed.netloc, f"/v1/files/{file_id}/content", "", ""))
