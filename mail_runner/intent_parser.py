"""Rule-based reply intent parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import ParsedMailAction, QuestionAnswer, QuestionItem
from .question_utils import effective_pending_questions, missing_required_question_ids
from .status import THREAD_STATUS_AWAITING_USER_INPUT, THREAD_STATUS_PAUSED

_HEADER_RE = re.compile(r"^\s*(Task|Acceptance|Timeout|Mode|Profile|Permission)\s*:\s*(.*)$", re.IGNORECASE)
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")
_TIMEOUT_RE = re.compile(r"(?i)timeout[^\d]{0,12}(\d+)|(\d+)\s*(?:minutes?|mins?|分钟)")
_COMMAND_RE = re.compile(r"^\s*/([a-z][a-z0-9_-]*)(?:\s+(.+?))?\s*$", re.IGNORECASE)
_ANSWER_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*[:：]\s*(.+?)\s*$")
_ANALYSIS_PATTERNS = ("analysis_only", "analysis only", "only analyze", "do not change code", "不要改代码", "只分析")
_MODIFY_PATTERNS = ("modify", "start modifying", "可以改代码", "开始修改")
_KNOWN_PROFILE_HINTS = {"fast", "strong", "vision"}
_PROFILE_PATTERNS = (
    re.compile(r"(?i)\bprofile\s*[:=]?\s*([a-z][a-z0-9_-]{1,31})\b"),
    re.compile(r"(?i)\b(?:use|switch to|change to|set(?: it)? to)\s+([a-z][a-z0-9_-]{1,31})\b"),
    re.compile(r"(?i)(?:改成|切到|用)\s*([a-z][a-z0-9_-]{1,31})"),
)


@dataclass(slots=True)
class StructuredAnswerParseResult:
    answers: list[QuestionAnswer]
    unknown_question_ids: list[str]
    invalid_answers: list[str]
    missing_required_question_ids: list[str]
    used_structured_format: bool


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    for pattern in patterns:
        normalized = pattern.lower()
        if re.search(r"[a-z]", normalized):
            if re.search(rf"(?<![a-z]){re.escape(normalized)}(?![a-z])", lowered):
                return True
            continue
        if normalized in lowered:
            return True
    return False


def _split_leading_command(text: str) -> tuple[str | None, str | None, str, bool]:
    normalized_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first_content_index: int | None = None
    for index, raw_line in enumerate(normalized_lines):
        if raw_line.strip():
            first_content_index = index
            break
    if first_content_index is None:
        return None, None, "", False

    first_line = normalized_lines[first_content_index].strip()
    if not first_line.startswith("/"):
        return None, None, text.strip(), False

    match = _COMMAND_RE.match(first_line)
    body = "\n".join(normalized_lines[first_content_index + 1 :]).strip()
    if not match:
        return None, None, body, True
    command = match.group(1).strip().lower()
    argument = (match.group(2) or "").strip() or None
    return command, argument, body, False


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
            candidate = match.group(1).strip().lower()
            if candidate in _KNOWN_PROFILE_HINTS:
                return candidate
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
            if label in {"timeout", "mode", "profile", "permission"}:
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
    permission = scalar_values.get("permission", "").strip().lower() or None
    return {
        "task_text_delta": task_text,
        "acceptance_delta": acceptance if acceptance or current_section == "acceptance" else None,
        "timeout_minutes": timeout_minutes,
        "mode": mode,
        "profile": profile,
        "permission": permission,
    }


def _normalize_choice_value(question: QuestionItem, raw_value: str) -> str | None:
    normalized_raw = raw_value.strip()
    if not normalized_raw:
        return None
    choices = list(question.choices)
    if not choices:
        return normalized_raw
    normalized_lookup = normalized_raw.lower()
    for choice in choices:
        if choice == normalized_raw or choice.lower() == normalized_lookup:
            return choice
    for key, label in question.choice_labels.items():
        if label == normalized_raw:
            return key
        if " ".join(label.split()).lower() == " ".join(normalized_raw.split()).lower():
            return key
    return None


def parse_structured_answers(text: str, pending_questions: list[QuestionItem]) -> StructuredAnswerParseResult:
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    known_questions = {item.question_id: item for item in pending_questions}
    answers_by_question: dict[str, QuestionAnswer] = {}
    unknown_question_ids: list[str] = []
    invalid_answers: list[str] = []
    used_structured_format = False
    pending_question_id_for_next_line: str | None = None

    for raw_line in normalized_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if pending_question_id_for_next_line is not None:
            question = known_questions.get(pending_question_id_for_next_line)
            if question is None:
                if pending_question_id_for_next_line not in unknown_question_ids:
                    unknown_question_ids.append(pending_question_id_for_next_line)
                pending_question_id_for_next_line = None
                continue
            normalized_value = _normalize_choice_value(question, stripped)
            if normalized_value is None:
                invalid_answers.append(pending_question_id_for_next_line)
                pending_question_id_for_next_line = None
                continue
            answers_by_question[pending_question_id_for_next_line] = QuestionAnswer(
                question_id=pending_question_id_for_next_line,
                value=normalized_value,
                raw_value=stripped,
            )
            pending_question_id_for_next_line = None
            used_structured_format = True
            continue
        if stripped.lower() == "answers:":
            used_structured_format = True
            continue
        match = _ANSWER_LINE_RE.match(raw_line)
        if not match:
            continue
        used_structured_format = True
        question_id = match.group(1).strip()
        raw_value = match.group(2).strip()
        if question_id == "question_id":
            pending_question_id_for_next_line = raw_value
            continue
        question = known_questions.get(question_id)
        if question is None:
            if question_id not in unknown_question_ids:
                unknown_question_ids.append(question_id)
            continue
        normalized_value = _normalize_choice_value(question, raw_value)
        if normalized_value is None:
            invalid_answers.append(question_id)
            continue
        answers_by_question[question_id] = QuestionAnswer(
            question_id=question_id,
            value=normalized_value,
            raw_value=raw_value,
        )

    answers = list(answers_by_question.values())
    missing_required = missing_required_question_ids(pending_questions, answers)
    return StructuredAnswerParseResult(
        answers=answers,
        unknown_question_ids=unknown_question_ids,
        invalid_answers=invalid_answers,
        missing_required_question_ids=missing_required,
        used_structured_format=used_structured_format,
    )


def _build_structured_answer_action(
    *,
    effective_text: str,
    profile: str | None,
    permission: str | None,
    task_text_delta: str | None,
    acceptance_delta: list[str] | None,
    timeout_minutes: int | None,
    mode: str | None,
    pending_questions: list[QuestionItem],
    confidence: float,
) -> ParsedMailAction:
    parsed_answers = parse_structured_answers(effective_text, pending_questions)
    notes: list[str] = []
    if parsed_answers.unknown_question_ids:
        notes.append("Unknown question ids: " + ", ".join(parsed_answers.unknown_question_ids))
    if parsed_answers.invalid_answers:
        notes.append("Invalid values for question ids: " + ", ".join(parsed_answers.invalid_answers))
    if not parsed_answers.used_structured_format:
        return ParsedMailAction(
            action="UNKNOWN",
            confidence=0.0,
            profile=profile,
            permission=permission,
            task_text_delta=task_text_delta,
            acceptance_delta=acceptance_delta,
            timeout_minutes=timeout_minutes,
            mode=mode,
            raw_user_text=effective_text,
            notes="Reply did not include structured answers for the pending question set.",
        )
    return ParsedMailAction(
        action="ANSWER_QUESTION",
        confidence=confidence,
        profile=profile,
        permission=permission,
        task_text_delta=task_text_delta,
        acceptance_delta=acceptance_delta,
        timeout_minutes=timeout_minutes,
        mode=mode,
        raw_user_text=effective_text,
        question_answers=parsed_answers.answers,
        missing_question_ids=parsed_answers.missing_required_question_ids,
        invalid_answer_messages=notes,
        used_structured_answers=parsed_answers.used_structured_format,
        notes="; ".join(notes) if notes else None,
    )


def parse_action(context: dict[str, Any], subject_info: dict[str, Any] | None = None) -> ParsedMailAction:
    raw_user_text = str(context.get("reply_delta") or context.get("raw_user_text") or "").strip()
    thread_state = context.get("thread_state")
    pending_questions = list(context.get("pending_questions") or effective_pending_questions(thread_state))
    thread_status = getattr(thread_state, "status", None)
    awaiting_user_input = bool(thread_status == THREAD_STATUS_AWAITING_USER_INPUT)
    paused = bool(thread_status == THREAD_STATUS_PAUSED)
    question_reply_state = awaiting_user_input or (paused and bool(pending_questions))
    has_incoming_attachments = bool(context.get("incoming_attachments"))
    if subject_info and subject_info.get("action") == "KILL":
        return ParsedMailAction(action="KILL", confidence=1.0, raw_user_text=raw_user_text)

    command_name, target_session_id, command_body, invalid_command = _split_leading_command(raw_user_text)
    effective_text = command_body if command_name is not None or invalid_command else raw_user_text
    effective_text = effective_text.strip()

    if invalid_command:
        return ParsedMailAction(
            action="UNKNOWN",
            confidence=0.0,
            raw_user_text=effective_text,
            notes="Unknown slash command.",
        )

    if command_name == "sessions":
        return ParsedMailAction(
            action="LIST_SESSIONS",
            confidence=1.0,
            raw_user_text="",
            target_session_id=target_session_id,
        )
    if command_name == "status":
        return ParsedMailAction(
            action="STATUS_QUERY",
            confidence=1.0,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
        )
    if command_name == "kill":
        return ParsedMailAction(
            action="KILL",
            confidence=1.0,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
        )
    if command_name == "rerun":
        return ParsedMailAction(
            action="RERUN",
            confidence=1.0,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
        )
    if command_name == "pause":
        return ParsedMailAction(
            action="PAUSE_SESSION",
            confidence=1.0,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
        )
    if command_name == "end":
        return ParsedMailAction(
            action="END_SESSION",
            confidence=1.0,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
        )

    if not raw_user_text and command_name is None:
        if has_incoming_attachments:
            return ParsedMailAction(
                action="ANSWER_QUESTION" if awaiting_user_input else "CONTINUE_SESSION",
                confidence=0.8,
                raw_user_text="",
            )
        return ParsedMailAction(action="UNKNOWN", confidence=0.0, raw_user_text="")

    structured = _parse_structured_update(effective_text)
    timeout_minutes = structured["timeout_minutes"] or _extract_timeout(effective_text)
    mode = structured["mode"] or _extract_mode(effective_text)
    profile = structured["profile"] or _extract_profile(effective_text)
    permission = structured["permission"]
    task_text_delta = structured["task_text_delta"]
    acceptance_delta = structured["acceptance_delta"]

    if command_name == "resume":
        if paused:
            if question_reply_state and effective_text:
                if len(pending_questions) > 1:
                    return _build_structured_answer_action(
                        effective_text=effective_text,
                        profile=profile,
                        permission=permission,
                        task_text_delta=task_text_delta,
                        acceptance_delta=acceptance_delta,
                        timeout_minutes=timeout_minutes,
                        mode=mode,
                        pending_questions=pending_questions,
                        confidence=1.0,
                    )
                return ParsedMailAction(
                    action="ANSWER_QUESTION",
                    confidence=1.0,
                    profile=profile,
                    permission=permission,
                    task_text_delta=task_text_delta,
                    acceptance_delta=acceptance_delta,
                    timeout_minutes=timeout_minutes,
                    mode=mode,
                    raw_user_text=effective_text,
                    target_session_id=target_session_id,
                )
            return ParsedMailAction(
                action="RESUME_SESSION",
                confidence=1.0,
                profile=profile,
                permission=permission,
                task_text_delta=task_text_delta,
                acceptance_delta=acceptance_delta,
                timeout_minutes=timeout_minutes,
                mode=mode,
                raw_user_text=effective_text,
                target_session_id=target_session_id,
            )
        if awaiting_user_input and effective_text:
            return ParsedMailAction(
                action="ANSWER_QUESTION",
                confidence=1.0,
                profile=profile,
                permission=permission,
                task_text_delta=task_text_delta,
                acceptance_delta=acceptance_delta,
                timeout_minutes=timeout_minutes,
                mode=mode,
                raw_user_text=effective_text,
                target_session_id=target_session_id,
            )
        if awaiting_user_input and not effective_text:
            return ParsedMailAction(action="UNKNOWN", confidence=0.0, raw_user_text="")
        return ParsedMailAction(
            action="CONTINUE_SESSION",
            confidence=1.0,
            profile=profile,
            permission=permission,
            task_text_delta=task_text_delta,
            acceptance_delta=acceptance_delta,
            timeout_minutes=timeout_minutes,
            mode=mode,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
        )

    if command_name == "new":
        return ParsedMailAction(
            action="NEW_SESSION",
            confidence=1.0,
            profile=profile,
            permission=permission,
            task_text_delta=task_text_delta,
            acceptance_delta=acceptance_delta,
            timeout_minutes=timeout_minutes,
            mode=mode,
            raw_user_text=effective_text,
        )

    if command_name is not None:
        return ParsedMailAction(
            action="UNKNOWN",
            confidence=0.0,
            raw_user_text=effective_text,
            target_session_id=target_session_id,
            notes=f"Unsupported slash command: /{command_name}",
        )

    if awaiting_user_input:
        if len(pending_questions) > 1:
            return _build_structured_answer_action(
                effective_text=effective_text,
                profile=profile,
                permission=permission,
                task_text_delta=task_text_delta,
                acceptance_delta=acceptance_delta,
                timeout_minutes=timeout_minutes,
                mode=mode,
                pending_questions=pending_questions,
                confidence=0.95,
            )
        return ParsedMailAction(
            action="ANSWER_QUESTION",
            confidence=0.85,
            profile=profile,
            permission=permission,
            task_text_delta=task_text_delta,
            acceptance_delta=acceptance_delta,
            timeout_minutes=timeout_minutes,
            mode=mode,
            raw_user_text=effective_text,
        )

    return ParsedMailAction(
        action="CONTINUE_SESSION",
        confidence=0.7,
        profile=profile,
        permission=permission,
        task_text_delta=task_text_delta,
        acceptance_delta=acceptance_delta,
        timeout_minutes=timeout_minutes,
        mode=mode,
        raw_user_text=effective_text,
    )
