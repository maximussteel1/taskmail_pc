"""Rule-based reply intent parsing."""

from __future__ import annotations

import re
from typing import Any

from .models import ParsedMailAction
from .status import THREAD_STATUS_AWAITING_USER_INPUT

_HEADER_RE = re.compile(r"^\s*(Task|Acceptance|Timeout|Mode|Profile)\s*:\s*(.*)$", re.IGNORECASE)
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")
_TIMEOUT_RE = re.compile(r"(?i)timeout[^\d]{0,12}(\d+)|(\d+)\s*(?:minutes?|mins?|分钟)")
_STATUS_PATTERNS = ("status", "progress", "how is it going", "what is the status", "状态", "进展", "现在如何")
_RERUN_PATTERNS = ("rerun", "run again", "retry", "重新跑", "重跑", "再跑一次", "重新执行")
_KILL_PATTERNS = ("kill", "terminate", "stop the task", "stop current task", "终止", "停止当前任务", "杀掉")
_ANALYSIS_PATTERNS = ("analysis_only", "analysis only", "only analyze", "do not change code", "不要改代码", "只分析")
_MODIFY_PATTERNS = ("modify", "start modifying", "可以改代码", "开始修改")
_PROFILE_PATTERNS = (
    re.compile(r"(?i)\bprofile\s*[:=]?\s*([a-z][a-z0-9_-]{1,31})\b"),
    re.compile(r"(?i)\b(?:use|switch to|change to|set(?: it)? to)\s+([a-z][a-z0-9_-]{1,31})\b"),
    re.compile(r"(?i)(?:改成|切到|用)\s*([a-z][a-z0-9_-]{1,31})"),
)


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def _extract_timeout(text: str) -> int | None:
    match = _TIMEOUT_RE.search(text)
    if not match:
        return None
    raw_value = match.group(1) or match.group(2)
    return int(raw_value) if raw_value else None


def _extract_mode(text: str) -> str | None:
    lowered = text.lower()
    if any(pattern in lowered for pattern in _ANALYSIS_PATTERNS):
        return "analysis_only"
    if any(pattern in lowered for pattern in _MODIFY_PATTERNS):
        return "modify"
    return None


def _extract_profile(text: str) -> str | None:
    for pattern in _PROFILE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip().lower()
    return None


def _parse_structured_update(text: str) -> dict[str, Any]:
    scalar_values: dict[str, str] = {}
    task_lines: list[str] = []
    acceptance_lines: list[str] = []
    current_section: str | None = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _HEADER_RE.match(raw_line)
        if match:
            label = match.group(1).lower()
            remainder = match.group(2).strip()
            if label in {"timeout", "mode", "profile"}:
                scalar_values[label] = remainder
                current_section = None
            elif label == "task":
                current_section = "task"
                if remainder:
                    task_lines.append(remainder)
            elif label == "acceptance":
                current_section = "acceptance"
                if remainder:
                    acceptance_lines.append(remainder)
            continue

        if current_section == "task":
            task_lines.append(raw_line)
        elif current_section == "acceptance":
            acceptance_lines.append(raw_line)

    task_text = "\n".join(line.rstrip() for line in task_lines).strip() or None
    acceptance: list[str] = []
    for raw_line in acceptance_lines:
        cleaned = _LIST_PREFIX_RE.sub("", raw_line).strip()
        if cleaned:
            acceptance.append(cleaned)

    timeout_minutes = int(scalar_values["timeout"]) if scalar_values.get("timeout") else None
    mode = scalar_values.get("mode", "").strip().lower() or None
    profile = scalar_values.get("profile", "").strip().lower() or None
    return {
        "task_text_delta": task_text,
        "acceptance_delta": acceptance if acceptance or current_section == "acceptance" else None,
        "timeout_minutes": timeout_minutes,
        "mode": mode,
        "profile": profile,
    }


def parse_action(context: dict[str, Any], subject_info: dict[str, Any] | None = None) -> ParsedMailAction:
    raw_user_text = str(context.get("reply_delta") or context.get("raw_user_text") or "").strip()
    thread_state = context.get("thread_state")
    awaiting_user_input = bool(getattr(thread_state, "status", None) == THREAD_STATUS_AWAITING_USER_INPUT)
    if subject_info and subject_info.get("action") == "KILL":
        return ParsedMailAction(action="KILL", confidence=1.0, raw_user_text=raw_user_text)

    if not raw_user_text:
        return ParsedMailAction(action="UNKNOWN", confidence=0.0, raw_user_text="")

    if _contains_any(raw_user_text, _KILL_PATTERNS):
        return ParsedMailAction(action="KILL", confidence=0.95, raw_user_text=raw_user_text)
    if _contains_any(raw_user_text, _RERUN_PATTERNS):
        return ParsedMailAction(action="RERUN", confidence=0.9, raw_user_text=raw_user_text)
    if _contains_any(raw_user_text, _STATUS_PATTERNS):
        return ParsedMailAction(action="STATUS_QUERY", confidence=0.85, raw_user_text=raw_user_text)

    structured = _parse_structured_update(raw_user_text)
    timeout_minutes = structured["timeout_minutes"] or _extract_timeout(raw_user_text)
    mode = structured["mode"] or _extract_mode(raw_user_text)
    profile = structured["profile"] or _extract_profile(raw_user_text)
    task_text_delta = structured["task_text_delta"]
    acceptance_delta = structured["acceptance_delta"]
    if awaiting_user_input:
        return ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=0.85,
            profile=profile,
            task_text_delta=task_text_delta,
            acceptance_delta=acceptance_delta,
            timeout_minutes=timeout_minutes,
            mode=mode,
            raw_user_text=raw_user_text,
        )
    if any(
        value is not None
        for value in [task_text_delta, acceptance_delta, timeout_minutes, mode, profile]
    ):
        return ParsedMailAction(
            action="UPDATE_TASK",
            confidence=0.8,
            profile=profile,
            task_text_delta=task_text_delta,
            acceptance_delta=acceptance_delta,
            timeout_minutes=timeout_minutes,
            mode=mode,
            raw_user_text=raw_user_text,
        )

    return ParsedMailAction(action="APPEND_CONTEXT", confidence=0.6, raw_user_text=raw_user_text)
