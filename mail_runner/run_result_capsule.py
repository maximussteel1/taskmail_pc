"""Parse and remove structured run-result capsules from backend output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

RUN_RESULT_BEGIN_MARKER = "---TASK-RUN-RESULT-BEGIN---"
RUN_RESULT_END_MARKER = "---TASK-RUN-RESULT-END---"
RUN_RESULT_FIELDS = ("changed_files", "tests_passed", "error_type", "error_message")
_RUN_RESULT_RE = re.compile(
    rf"{re.escape(RUN_RESULT_BEGIN_MARKER)}\s*(.*?){re.escape(RUN_RESULT_END_MARKER)}",
    re.DOTALL,
)


@dataclass(slots=True)
class StructuredRunResult:
    changed_files: list[str] = field(default_factory=list)
    tests_passed: bool | None = None
    error_type: str | None = None
    error_message: str | None = None


def _single_line(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def render_run_result_capsule(result: StructuredRunResult | dict[str, Any]) -> str:
    payload = result if isinstance(result, StructuredRunResult) else StructuredRunResult(**result)
    changed_files = " | ".join(_single_line(item) for item in payload.changed_files if _single_line(item))
    tests_passed = "unknown"
    if payload.tests_passed is True:
        tests_passed = "true"
    elif payload.tests_passed is False:
        tests_passed = "false"
    lines = [
        RUN_RESULT_BEGIN_MARKER,
        f"changed_files: {changed_files}",
        f"tests_passed: {tests_passed}",
        f"error_type: {_single_line(payload.error_type)}",
        f"error_message: {_single_line(payload.error_message)}",
        RUN_RESULT_END_MARKER,
    ]
    return "\n".join(lines)


def _parse_tests_passed(value: str) -> bool | None:
    normalized = value.strip().lower()
    if not normalized or normalized in {"unknown", "none", "null", "n/a"}:
        return None
    if normalized in {"true", "yes", "1", "passed", "pass"}:
        return True
    if normalized in {"false", "no", "0", "failed", "fail"}:
        return False
    return None


def _parse_block(block_text: str) -> StructuredRunResult | None:
    parsed: dict[str, str] = {}
    for raw_line in block_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            return None
        parsed[key.strip()] = value.lstrip()
    changed_files_raw = parsed.get("changed_files", "")
    changed_files = [item.strip() for item in changed_files_raw.split("|") if item.strip()]
    return StructuredRunResult(
        changed_files=changed_files,
        tests_passed=_parse_tests_passed(parsed.get("tests_passed", "")),
        error_type=_single_line(parsed.get("error_type", "")) or None,
        error_message=_single_line(parsed.get("error_message", "")) or None,
    )


def parse_run_result_capsule(text: str) -> StructuredRunResult | None:
    matches = _RUN_RESULT_RE.findall(text)
    if not matches:
        return None
    return _parse_block(matches[-1])


def strip_run_result_capsules(text: str) -> str:
    stripped = _RUN_RESULT_RE.sub("", text)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped.strip()
