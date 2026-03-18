"""Render and parse human-readable task state capsules."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from .models import ThreadState

BEGIN_MARKER = "---TASK-STATE-BEGIN---"
END_MARKER = "---TASK-STATE-END---"
QUESTION_BEGIN_MARKER = "---TASK-QUESTION-BEGIN---"
QUESTION_END_MARKER = "---TASK-QUESTION-END---"
CAPSULE_FIELDS = (
    "thread_id",
    "workspace_id",
    "session_id",
    "session_name",
    "task_id",
    "backend",
    "repo_path",
    "workdir",
    "mode",
    "status",
    "last_summary",
)
QUESTION_FIELDS = (
    "question_set_id",
    "question_id",
    "question_type",
    "required",
    "question_text",
    "choices",
    "choice_labels",
)
_CAPSULE_RE = re.compile(
    rf"{re.escape(BEGIN_MARKER)}\s*(.*?){re.escape(END_MARKER)}",
    re.DOTALL,
)
_QUESTION_RE = re.compile(
    rf"{re.escape(QUESTION_BEGIN_MARKER)}\s*(.*?){re.escape(QUESTION_END_MARKER)}",
    re.DOTALL,
)


def _single_line(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def render_state_capsule(state: ThreadState | dict[str, Any]) -> str:
    state_dict = asdict(state) if isinstance(state, ThreadState) else dict(state)
    lines = [BEGIN_MARKER]
    for field_name in CAPSULE_FIELDS:
        lines.append(f"{field_name}: {_single_line(state_dict.get(field_name, ''))}")
    lines.append(END_MARKER)
    return "\n".join(lines)


def parse_state_capsule(text: str) -> dict[str, str] | None:
    matches = _CAPSULE_RE.findall(text)
    if not matches:
        return None

    parsed: dict[str, str] = {}
    for raw_line in matches[-1].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            return None
        parsed[key.strip()] = value.lstrip()
    return parsed


def render_question_capsule(question: dict[str, Any]) -> str:
    question_dict = dict(question)
    lines = [QUESTION_BEGIN_MARKER]
    for field_name in QUESTION_FIELDS:
        if field_name in {"choices", "choice_labels"}:
            raw_choices = question_dict.get(field_name, [])
            if isinstance(raw_choices, dict):
                rendered = " | ".join(
                    f"{_single_line(key)}={_single_line(value)}"
                    for key, value in raw_choices.items()
                    if _single_line(key) and _single_line(value)
                )
            elif isinstance(raw_choices, list):
                rendered = " | ".join(_single_line(item) for item in raw_choices if _single_line(item))
            else:
                rendered = _single_line(raw_choices)
        else:
            rendered = _single_line(question_dict.get(field_name, ""))
        lines.append(f"{field_name}: {rendered}")
    lines.append(QUESTION_END_MARKER)
    return "\n".join(lines)


def render_question_capsules(questions: list[dict[str, Any]]) -> str:
    return "\n".join(render_question_capsule(question) for question in questions)


def _parse_question_block(block_text: str) -> dict[str, Any] | None:
    parsed: dict[str, Any] = {}
    for raw_line in block_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            return None
        normalized_key = key.strip()
        normalized_value = value.lstrip()
        if normalized_key == "choices":
            parsed[normalized_key] = [item.strip() for item in normalized_value.split("|") if item.strip()]
        elif normalized_key == "choice_labels":
            labels: dict[str, str] = {}
            for raw_item in normalized_value.split("|"):
                item = raw_item.strip()
                if not item:
                    continue
                key_part, separator_part, value_part = item.partition("=")
                if separator_part:
                    labels[key_part.strip()] = value_part.strip()
            parsed[normalized_key] = labels
        elif normalized_key == "required":
            parsed[normalized_key] = normalized_value.lower() in {"1", "true", "yes"}
        else:
            parsed[normalized_key] = normalized_value
    return parsed


def parse_question_capsules(text: str) -> list[dict[str, Any]]:
    matches = _QUESTION_RE.findall(text)
    if not matches:
        return []

    parsed_blocks: list[dict[str, Any]] = []
    question_set_ids: set[str] = set()
    for block in matches:
        parsed = _parse_question_block(block)
        if parsed is None:
            return []
        parsed_blocks.append(parsed)
        question_set_id = str(parsed.get("question_set_id") or "").strip()
        if question_set_id:
            question_set_ids.add(question_set_id)
    if len(question_set_ids) > 1:
        return []
    return parsed_blocks


def parse_question_capsule(text: str) -> dict[str, Any] | None:
    parsed_blocks = parse_question_capsules(text)
    if not parsed_blocks:
        return None
    return parsed_blocks[-1]
