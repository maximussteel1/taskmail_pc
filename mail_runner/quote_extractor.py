"""Reply quote trimming helpers."""

from __future__ import annotations

import re

from .state_capsule import (
    BEGIN_MARKER,
    END_MARKER,
    QUESTION_BEGIN_MARKER,
    QUESTION_END_MARKER,
)

_CAPSULE_RE = re.compile(
    rf"{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}",
    re.DOTALL,
)
_QUESTION_RE = re.compile(
    rf"{re.escape(QUESTION_BEGIN_MARKER)}.*?{re.escape(QUESTION_END_MARKER)}",
    re.DOTALL,
)
_QUOTE_SPLIT_PATTERNS = (
    re.compile(r"(?im)^On .+wrote:\s*$"),
    re.compile(r"(?im)^在.+写道[:：]\s*$"),
    re.compile(r"(?im)^-----Original Message-----\s*$"),
    re.compile(r"(?im)^From:\s+.+\nSent:\s+.+\nTo:\s+.+\nSubject:\s+.+$"),
    re.compile(r"(?m)^>+"),
)


def _normalize_newlines(body_text: str) -> str:
    return (body_text or "").replace("\r\n", "\n").replace("\r", "\n")


def extract_reply_delta(body_text: str) -> str:
    """Extract the newly added text from a reply body."""

    text = _normalize_newlines(body_text)
    text = _CAPSULE_RE.sub("", text)
    text = _QUESTION_RE.sub("", text)

    cut_points = [match.start() for pattern in _QUOTE_SPLIT_PATTERNS for match in [pattern.search(text)] if match]
    if cut_points:
        text = text[: min(cut_points)]

    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()
