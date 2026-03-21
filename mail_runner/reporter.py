"""Status mail rendering helpers."""

from __future__ import annotations

import html
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    ExternalDelivery,
    OutgoingAttachment,
    QuestionAnswer,
    QuestionItem,
    RunArtifact,
    RunResult,
    TaskSnapshot,
    ThreadState,
)
from .question_utils import effective_pending_questions, effective_question_set_id
from .run_result_capsule import RUN_RESULT_BEGIN_MARKER, RUN_RESULT_END_MARKER
from .state_capsule import (
    BEGIN_MARKER as STATE_BEGIN_MARKER,
    END_MARKER as STATE_END_MARKER,
    QUESTION_BEGIN_MARKER,
    QUESTION_END_MARKER,
    render_question_capsules,
    render_state_capsule,
)

MAIL_STATUS_ACCEPTED = "ACCEPTED"
MAIL_STATUS_RUNNING = "RUNNING"
MAIL_STATUS_DONE = "DONE"
MAIL_STATUS_FAILED = "FAILED"
MAIL_STATUS_STATUS = "STATUS"
MAIL_STATUS_KILLED = "KILLED"
MAIL_STATUS_QUESTION = "QUESTION"
MAIL_STATUS_PAUSED = "PAUSED"

_ARTIFACT_LINK_RE = re.compile(
    r"^(?P<bullet>- )?\[(?P<label>[^\]]+)\]\(artifact://(?P<artifact_id>[^)]+)\)(?P<suffix>.*)$"
)
_ARTIFACT_IMAGE_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\(artifact://(?P<artifact_id>[^)]+)\)$")
_GENERIC_LINK_RE = re.compile(r"^(?P<bullet>- )?\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)(?P<suffix>.*)$")
_MARKDOWN_BULLET_RE = re.compile(r"^- (?P<text>.*)$")
_KEY_VALUE_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z0-9 /_-]*):\s*(?P<value>.*)$")
_HTML_META_LABELS = {
    "Status",
    "Permission",
    "Session ID",
    "Thread ID",
    "Task ID",
    "Backend",
    "Repo",
    "Workdir",
    "Exit Code",
    "Error Type",
    "Error",
    "Tests Passed",
    "Changed Files",
    "Paused From",
}
_SECTION_CLASS_NAMES = {
    "Artifacts": "task-artifacts",
    "External Deliveries": "task-external-deliveries",
    "Attachment Notices": "task-attachment-notices",
}
_CAPSULE_MARKER_MAP = {
    STATE_BEGIN_MARKER: ("state", STATE_END_MARKER),
    QUESTION_BEGIN_MARKER: ("question", QUESTION_END_MARKER),
    RUN_RESULT_BEGIN_MARKER: ("run-result", RUN_RESULT_END_MARKER),
}
_BODY_STYLE = "margin:0; padding:12px; background:#efe6d4;"
_ARTICLE_STYLE = (
    "margin:0 auto; max-width:680px; padding:18px 16px 22px; background:#fffaf3; color:#1f2328; "
    "font-family:'Segoe UI', Helvetica, Arial, sans-serif; line-height:1.6; border:1px solid #e9dcc8; border-radius:24px;"
)
_SECTION_STYLE = "margin:0 0 14px; padding:14px 14px 16px; border:1px solid #eadfcf; border-radius:18px; background:#fffdf8;"
_SUMMARY_SECTION_STYLE = "margin:0 0 16px; padding:16px 16px 18px; border:1px solid #e0d2ba; border-radius:20px; background:#f4ede0;"
_SECTION_TITLE_STYLE = "margin:0 0 10px; font-size:12px; letter-spacing:0.08em; text-transform:uppercase; color:#7a6242;"
_SUMMARY_TEXT_STYLE = "font-size:18px; font-weight:600; line-height:1.45; color:#221b12;"
_TEXT_BLOCK_STYLE = "white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; font-size:14px;"
_MUTED_TEXT_STYLE = "color:#5f5647;"
_META_ITEM_STYLE = "margin:0 0 10px; padding:0 0 10px; border-bottom:1px solid #f0e6d7;"
_META_LABEL_STYLE = "display:block; margin-bottom:2px; font-size:12px; letter-spacing:0.04em; text-transform:uppercase; color:#8a7357;"
_META_VALUE_STYLE = "font-size:14px; color:#272016; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word;"
_LIST_STYLE = "margin:0; padding-left:20px;"
_PRE_STYLE = (
    "margin:0; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; "
    "background:#f4f1ea; padding:14px; border-radius:12px; font-size:13px; color:#2d2418;"
)
_INLINE_PREVIEW_FIGURE_STYLE = "margin:16px 0 0;"
_INLINE_PREVIEW_IMAGE_STYLE = "display:block; width:100%; max-width:100%; height:auto; border-radius:12px;"
_INLINE_PREVIEW_CAPTION_STYLE = "margin-top:8px; font-size:13px; color:#655947;"


def _status_badge_style(status_value: str) -> str:
    palette = {
        MAIL_STATUS_ACCEPTED: ("#6b4a19", "#f5ddae"),
        MAIL_STATUS_RUNNING: ("#0f4f54", "#bfe9df"),
        MAIL_STATUS_DONE: ("#215231", "#c9edcd"),
        MAIL_STATUS_FAILED: ("#6e1d1d", "#f2c0c0"),
        MAIL_STATUS_KILLED: ("#5a2444", "#ebc5dd"),
        MAIL_STATUS_QUESTION: ("#5b3a0e", "#f0d4a8"),
        MAIL_STATUS_PAUSED: ("#4b4164", "#d9d2ef"),
        MAIL_STATUS_STATUS: ("#3c4e6b", "#d5deef"),
    }
    foreground, background = palette.get(status_value.strip().upper(), ("#4f4334", "#e8dcc9"))
    return (
        f"display:inline-block; margin-bottom:12px; padding:4px 10px; border-radius:999px; "
        f"font-size:12px; font-weight:700; letter-spacing:0.04em; color:{foreground}; background:{background};"
    )


def _ensure_inline_content_ids(attachments: list[OutgoingAttachment]) -> list[OutgoingAttachment]:
    inline_index = 0
    for attachment in attachments:
        if not attachment.inline:
            continue
        inline_index += 1
        if not attachment.content_id:
            attachment.content_id = f"mail-runner-inline-{inline_index}"
    return attachments


def build_status_subject(status_label: str, subject_text: str, session_id: str | None = None) -> str:
    subject = subject_text.strip()
    session_tag = f"[S:{session_id.strip()}]" if session_id and session_id.strip() else ""
    return f"[{status_label}]{session_tag} {subject}".rstrip()


def _build_capsule_state(
    state: ThreadState | dict[str, Any],
    task_snapshot: TaskSnapshot | None = None,
) -> dict[str, Any]:
    state_dict = asdict(state) if isinstance(state, ThreadState) else dict(state)
    return {
        "thread_id": state_dict.get("thread_id", ""),
        "workspace_id": state_dict.get("workspace_id", ""),
        "session_id": state_dict.get("session_id", ""),
        "session_name": state_dict.get("session_name", "") or state_dict.get("subject_norm", ""),
        "task_id": task_snapshot.task_id if task_snapshot is not None else (state_dict.get("current_task_id") or state_dict.get("task_id", "")),
        "backend": state_dict.get("backend", ""),
        "repo_path": state_dict.get("repo_path", ""),
        "workdir": state_dict.get("workdir", ""),
        "mode": task_snapshot.mode if task_snapshot is not None else state_dict.get("mode", ""),
        "status": state_dict.get("status", ""),
        "last_summary": state_dict.get("last_summary", ""),
    }


def _artifact_summary_suffix(artifact: RunArtifact) -> str:
    caption = (artifact.caption or "").strip()
    show_caption = caption and caption != artifact.name
    preview_note = artifact.kind == "image" and artifact.inline_preview
    if show_caption and preview_note:
        return f": {caption} (inline preview)"
    if show_caption:
        return f": {caption}"
    if preview_note:
        return " (inline preview)"
    return ""


def _artifact_markdown_lines(artifact: RunArtifact) -> list[str]:
    lines = [
        f"- [{artifact.name}](artifact://{artifact.artifact_id}){_artifact_summary_suffix(artifact)}"
    ]
    if artifact.kind == "image" and artifact.inline_preview:
        alt_text = artifact.caption or artifact.name
        lines.append(f"![{alt_text}](artifact://{artifact.artifact_id})")
    return lines


def _section_markdown_lines(title: str, item_lines: list[str]) -> list[str]:
    if not item_lines:
        return []
    return ["", f"## {title}", "", *item_lines]


def _artifacts_section_markdown_lines(artifacts: list[RunArtifact] | None) -> list[str]:
    if not artifacts:
        return []
    item_lines: list[str] = []
    for artifact in artifacts:
        item_lines.extend(_artifact_markdown_lines(artifact))
    return _section_markdown_lines("Artifacts", item_lines)


def _attachment_notices_section_markdown_lines(skipped_messages: list[str] | None) -> list[str]:
    if not skipped_messages:
        return []
    item_lines = [f"- {item}" for item in skipped_messages]
    return _section_markdown_lines("Attachment Notices", item_lines)


def _format_size_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _external_delivery_markdown_lines(delivery: ExternalDelivery) -> list[str]:
    suffix = (
        f": Delivered via {delivery.provider.upper()} "
        f"({_format_size_bytes(delivery.size_bytes)}, expires {delivery.expires_at})"
    )
    return [f"- [{delivery.name}]({delivery.url}){suffix}"]


def _external_deliveries_section_markdown_lines(external_deliveries: list[ExternalDelivery] | None) -> list[str]:
    if not external_deliveries:
        return []
    item_lines: list[str] = []
    for delivery in external_deliveries:
        item_lines.extend(_external_delivery_markdown_lines(delivery))
    return _section_markdown_lines("External Deliveries", item_lines)


def _build_status_markdown(
    status_label: str,
    state: ThreadState | dict[str, Any],
    *,
    task_snapshot: TaskSnapshot | None = None,
    result: RunResult | None = None,
    captured_reply: str | None = None,
    reply_override: str | None = None,
    intro: str | None = None,
    question_id: str | None = None,
    question_text: str | None = None,
    pending_choices: list[str] | None = None,
    question_set_id: str | None = None,
    pending_questions: list[QuestionItem] | None = None,
    collected_answers: list[QuestionAnswer] | None = None,
    artifacts: list[RunArtifact] | None = None,
    external_deliveries: list[ExternalDelivery] | None = None,
    skipped_messages: list[str] | None = None,
    summary_override: str | None = None,
) -> str:
    state_dict = asdict(state) if isinstance(state, ThreadState) else dict(state)
    visible_reply = reply_override if reply_override is not None else captured_reply
    lines: list[str] = []
    if intro:
        lines.append(intro)
        lines.append("")
    lines.extend(
        [
            f"Status: {status_label}",
            f"Session ID: {state_dict.get('session_id') or state_dict.get('thread_id', '')}",
            f"Thread ID: {state_dict.get('thread_id', '')}",
            f"Task ID: {task_snapshot.task_id if task_snapshot is not None else (state_dict.get('current_task_id') or state_dict.get('task_id', ''))}",
            f"Backend: {state_dict.get('backend', '')}",
            f"Permission: {state_dict.get('permission') or 'default'}",
            f"Repo: {state_dict.get('repo_path', '')}",
            f"Workdir: {state_dict.get('workdir') or ''}",
        ]
    )
    if result is not None:
        lines.append(f"Exit Code: {result.exit_code if result.exit_code is not None else ''}")
        if result.error_type:
            lines.append(f"Error Type: {result.error_type}")
        if result.error_message:
            lines.append(f"Error: {result.error_message}")
        if result.tests_passed is not None:
            lines.append(f"Tests Passed: {'yes' if result.tests_passed else 'no'}")
        if result.changed_files:
            lines.append(f"Changed Files: {' | '.join(result.changed_files)}")
    paused_from_status = state_dict.get("paused_from_status")
    if paused_from_status:
        lines.append(f"Paused From: {paused_from_status}")
    if summary_override:
        lines.append(f"Summary: {summary_override}")
    elif not visible_reply and state_dict.get("last_summary"):
        lines.append(f"Summary: {state_dict['last_summary']}")
    if visible_reply:
        lines.extend(
            [
                "",
                "Reply:",
                visible_reply.rstrip(),
            ]
        )
    resolved_pending_questions = list(
        pending_questions
        or effective_pending_questions(
            state if isinstance(state, ThreadState) else None,
            result=result,
            fallback_task_id=task_snapshot.task_id if task_snapshot is not None else state_dict.get("current_task_id") or "task",
        )
    )
    resolved_question_set_id = (
        question_set_id
        or effective_question_set_id(
            state if isinstance(state, ThreadState) else None,
            result=result,
            fallback_task_id=task_snapshot.task_id if task_snapshot is not None else state_dict.get("current_task_id") or "task",
        )
    )
    raw_collected_answers = collected_answers or (
        list(getattr(state, "collected_answers", []))
        if isinstance(state, ThreadState)
        else list(state_dict.get("collected_answers") or [])
    )
    resolved_collected_answers = [
        item if isinstance(item, QuestionAnswer) else QuestionAnswer(**item)
        for item in raw_collected_answers
    ]
    if not resolved_pending_questions and (question_text or state_dict.get("pending_question_text")):
        resolved_pending_questions = [
            QuestionItem(
                question_set_id=resolved_question_set_id or question_id or state_dict.get("pending_question_id") or "question",
                question_id=question_id or state_dict.get("pending_question_id") or "question",
                question_type="single_choice" if list(pending_choices or state_dict.get("pending_choices") or []) else "short_text",
                question_text=question_text or state_dict.get("pending_question_text") or "",
                required=True,
                choices=list(pending_choices or state_dict.get("pending_choices") or []),
                choice_labels={},
            )
        ]
    if resolved_pending_questions:
        lines.extend(["", f"Question Set ID: {resolved_question_set_id or resolved_pending_questions[0].question_set_id}"])
        if resolved_collected_answers:
            lines.append("Received Answers:")
            for answer in resolved_collected_answers:
                lines.append(f"- {answer.question_id}: {answer.value}")
        lines.append("Pending Questions:")
        for item in resolved_pending_questions:
            lines.append(f"- {item.question_id}: {item.question_text}")
            if item.choices:
                lines.append(f"  Choices: {' | '.join(item.choices)}")
            if item.choice_labels:
                lines.append(
                    "  Labels: "
                    + " | ".join(f"{key}={value}" for key, value in item.choice_labels.items())
                )
        if len(resolved_pending_questions) > 1:
            lines.extend(["", "Reply using this format:", "", "Answers:"])
            for item in resolved_pending_questions:
                lines.append(f"{item.question_id}:")
        elif len(resolved_pending_questions) == 1:
            item = resolved_pending_questions[0]
            lines.extend(["", f"Question ID: {item.question_id}", f"Question: {item.question_text}"])
            if item.choices:
                lines.append(f"Choices: {' | '.join(item.choices)}")
    lines.extend(_artifacts_section_markdown_lines(artifacts))
    lines.extend(_external_deliveries_section_markdown_lines(external_deliveries))
    lines.extend(_attachment_notices_section_markdown_lines(skipped_messages))
    lines.append("")
    lines.append(render_state_capsule(_build_capsule_state(state, task_snapshot)))
    if resolved_pending_questions:
        lines.append("")
        lines.append(render_question_capsules([asdict(item) for item in resolved_pending_questions]))
    return "\n".join(lines).strip() + "\n"


def build_status_markdown(
    status_label: str,
    state: ThreadState | dict[str, Any],
    *,
    task_snapshot: TaskSnapshot | None = None,
    result: RunResult | None = None,
    captured_reply: str | None = None,
    reply_override: str | None = None,
    intro: str | None = None,
    question_id: str | None = None,
    question_text: str | None = None,
    pending_choices: list[str] | None = None,
    question_set_id: str | None = None,
    pending_questions: list[QuestionItem] | None = None,
    collected_answers: list[QuestionAnswer] | None = None,
    artifacts: list[RunArtifact] | None = None,
    external_deliveries: list[ExternalDelivery] | None = None,
    skipped_messages: list[str] | None = None,
    summary_override: str | None = None,
) -> str:
    return _build_status_markdown(
        status_label,
        state,
        task_snapshot=task_snapshot,
        result=result,
        captured_reply=captured_reply,
        reply_override=reply_override,
        intro=intro,
        question_id=question_id,
        question_text=question_text,
        pending_choices=pending_choices,
        question_set_id=question_set_id,
        pending_questions=pending_questions,
        collected_answers=collected_answers,
        artifacts=artifacts,
        external_deliveries=external_deliveries,
        skipped_messages=skipped_messages,
        summary_override=summary_override,
    )


def render_status_markdown_to_plain_text(markdown_body: str) -> str:
    rendered: list[str] = []
    for raw_line in markdown_body.rstrip().splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            rendered.append(f"{stripped[3:]}:")
            continue
        image_match = _ARTIFACT_IMAGE_RE.match(stripped)
        if image_match:
            alt_text = image_match.group("alt").strip() or image_match.group("artifact_id").strip()
            rendered.append(f"[Image Preview] {alt_text}")
            continue
        link_match = _ARTIFACT_LINK_RE.match(stripped)
        if link_match:
            prefix = link_match.group("bullet") or ""
            suffix = link_match.group("suffix") or ""
            rendered.append(f"{prefix}{link_match.group('label')}{suffix}")
            continue
        generic_link_match = _GENERIC_LINK_RE.match(stripped)
        if generic_link_match:
            prefix = generic_link_match.group("bullet") or ""
            suffix = generic_link_match.group("suffix") or ""
            rendered.append(
                f"{prefix}{generic_link_match.group('label')}{suffix} | {generic_link_match.group('url')}"
            )
            continue
        rendered.append(raw_line)
    return "\n".join(rendered).strip() + "\n"


def _artifact_map(artifacts: list[RunArtifact]) -> dict[str, RunArtifact]:
    return {item.artifact_id: item for item in artifacts}


def _find_attachment_for_artifact(
    artifact: RunArtifact,
    attachments: list[OutgoingAttachment],
) -> OutgoingAttachment | None:
    for attachment in attachments:
        if Path(attachment.path) == Path(artifact.path) and (attachment.name or Path(attachment.path).name) == artifact.name:
            return attachment
    return None


def _trim_blank_lines(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def _split_status_markdown_blocks(
    markdown_body: str,
) -> tuple[list[str], list[tuple[str, list[str]]], list[tuple[str, str]]]:
    frontmatter: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    capsules: list[tuple[str, str]] = []
    current_section_title: str | None = None
    current_section_lines: list[str] = []
    lines = markdown_body.rstrip().splitlines()
    in_frontmatter = True
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        capsule_info = _CAPSULE_MARKER_MAP.get(stripped)
        if capsule_info is not None:
            if current_section_title is not None:
                sections.append((current_section_title, _trim_blank_lines(current_section_lines)))
                current_section_title = None
                current_section_lines = []
            capsule_kind, end_marker = capsule_info
            block_lines = [raw_line]
            index += 1
            while index < len(lines):
                block_lines.append(lines[index])
                if lines[index].strip() == end_marker:
                    break
                index += 1
            capsules.append((capsule_kind, "\n".join(block_lines).strip()))
            in_frontmatter = False
            index += 1
            continue
        if stripped.startswith("## "):
            if current_section_title is not None:
                sections.append((current_section_title, _trim_blank_lines(current_section_lines)))
            current_section_title = stripped[3:]
            current_section_lines = []
            in_frontmatter = False
            index += 1
            continue
        if current_section_title is not None:
            current_section_lines.append(raw_line)
        elif in_frontmatter:
            frontmatter.append(raw_line)
        index += 1

    if current_section_title is not None:
        sections.append((current_section_title, _trim_blank_lines(current_section_lines)))
    return _trim_blank_lines(frontmatter), sections, capsules


def _split_leading_paragraph(lines: list[str]) -> tuple[list[str], list[str]]:
    paragraph: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or _KEY_VALUE_RE.match(stripped):
            break
        paragraph.append(lines[index].rstrip())
        index += 1
    remainder = lines[index:]
    while remainder and not remainder[0].strip():
        remainder = remainder[1:]
    return paragraph, remainder


def _parse_html_frontmatter(
    frontmatter_lines: list[str],
) -> tuple[list[str], list[tuple[str, str]], list[str], list[str]]:
    leading_paragraph, remaining_lines = _split_leading_paragraph(frontmatter_lines)
    summary_lines: list[str] = []
    meta_items: list[tuple[str, str]] = []
    reply_lines: list[str] = []
    body_lines: list[str] = []
    reply_mode = False

    for raw_line in remaining_lines:
        stripped = raw_line.strip()
        if not stripped:
            if reply_mode:
                reply_lines.append("")
            else:
                body_lines.append("")
            continue
        match = _KEY_VALUE_RE.match(stripped)
        if match:
            label = match.group("label")
            value = match.group("value")
            if label == "Summary":
                summary_lines = [value] if value else []
                reply_mode = False
                continue
            if label == "Reply":
                reply_mode = True
                if value:
                    reply_lines.append(value)
                continue
            if label in _HTML_META_LABELS:
                reply_mode = False
                meta_items.append((label, value))
                continue
        if reply_mode:
            reply_lines.append(raw_line.rstrip())
        else:
            body_lines.append(raw_line.rstrip())

    if not summary_lines and leading_paragraph:
        summary_lines = leading_paragraph
    else:
        if leading_paragraph:
            if body_lines:
                body_lines = [*leading_paragraph, "", *body_lines]
            else:
                body_lines = leading_paragraph
    return summary_lines, meta_items, _trim_blank_lines(reply_lines), _trim_blank_lines(body_lines)


def _split_inline_preview_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    content_lines: list[str] = []
    preview_lines: list[str] = []
    for raw_line in lines:
        if _ARTIFACT_IMAGE_RE.match(raw_line.strip()):
            preview_lines.append(raw_line.rstrip())
            continue
        content_lines.append(raw_line.rstrip())
    return _trim_blank_lines(content_lines), _trim_blank_lines(preview_lines)


def _render_html_content_lines(
    lines: list[str],
    *,
    artifacts: list[RunArtifact] | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> str:
    normalized_attachments = _ensure_inline_content_ids(list(attachments or []))
    artifact_by_id = _artifact_map(list(artifacts or []))
    parts: list[str] = []
    text_buffer: list[str] = []
    list_items: list[str] = []

    def flush_text_buffer() -> None:
        if not text_buffer:
            return
        body_html = html.escape("\n".join(text_buffer)).replace("\n", "<br>\n")
        parts.extend(
            [
                f'<div class="task-text-block" style="{_TEXT_BLOCK_STYLE}">',
                body_html,
                "</div>",
            ]
        )
        text_buffer.clear()

    def flush_list_items() -> None:
        if not list_items:
            return
        parts.append(f'<ul style="{_LIST_STYLE}">')
        parts.extend(list_items)
        parts.append("</ul>")
        list_items.clear()

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            flush_list_items()
            text_buffer.append("")
            continue
        image_match = _ARTIFACT_IMAGE_RE.match(stripped)
        if image_match:
            flush_list_items()
            flush_text_buffer()
            artifact = artifact_by_id.get(image_match.group("artifact_id").strip())
            alt_text = (
                image_match.group("alt").strip()
                or (artifact.caption if artifact else None)
                or (artifact.name if artifact else None)
                or image_match.group("artifact_id").strip()
            )
            if artifact is not None:
                attachment = _find_attachment_for_artifact(artifact, normalized_attachments)
                if attachment is not None and attachment.inline and attachment.content_type and attachment.content_type.startswith("image/"):
                    if not attachment.content_id:
                        attachment.content_id = f"mail-runner-inline-{len([item for item in normalized_attachments if item.content_id]) + 1}"
                    parts.extend(
                        [
                            f'<figure class="task-inline-preview" style="{_INLINE_PREVIEW_FIGURE_STYLE}">',
                            f'<img src="cid:{attachment.content_id}" alt="{html.escape(alt_text)}" style="{_INLINE_PREVIEW_IMAGE_STYLE}">',
                            f'<figcaption style="{_INLINE_PREVIEW_CAPTION_STYLE}">{html.escape(alt_text)}</figcaption>',
                            "</figure>",
                        ]
                    )
                    continue
            text_buffer.append(f"[Image Preview] {alt_text}")
            continue
        link_match = _ARTIFACT_LINK_RE.match(stripped)
        if link_match:
            flush_text_buffer()
            label = html.escape(link_match.group("label"))
            suffix = html.escape(link_match.group("suffix") or "")
            list_items.append(f'<li style="margin:0 0 8px;">{label}{suffix}</li>')
            continue
        generic_link_match = _GENERIC_LINK_RE.match(stripped)
        if generic_link_match:
            flush_text_buffer()
            label = html.escape(generic_link_match.group("label"))
            suffix = html.escape(generic_link_match.group("suffix") or "")
            url = html.escape(generic_link_match.group("url"), quote=True)
            list_items.append(
                f'<li style="margin:0 0 8px;"><a href="{url}" rel="noopener noreferrer" style="color:#1f5d66; text-decoration:none;">{label}</a>{suffix}</li>'
            )
            continue
        bullet_match = _MARKDOWN_BULLET_RE.match(stripped)
        if bullet_match:
            flush_text_buffer()
            list_items.append(f'<li style="margin:0 0 8px;">{html.escape(bullet_match.group("text"))}</li>')
            continue
        flush_list_items()
        text_buffer.append(raw_line.rstrip())

    flush_list_items()
    flush_text_buffer()
    return "".join(parts)


def _render_summary_section(summary_lines: list[str], status_value: str | None = None) -> str:
    if not summary_lines and not status_value:
        return ""
    summary_html = html.escape("\n".join(summary_lines)).replace("\n", "<br>\n") if summary_lines else ""
    badge_html = ""
    if status_value:
        badge_html = f'<div class="task-status-badge" style="{_status_badge_style(status_value)}">{html.escape(status_value)}</div>'
    return (
        f'<section class="task-summary" style="{_SUMMARY_SECTION_STYLE}">'
        f"{badge_html}"
        f'<h2 style="{_SECTION_TITLE_STYLE}">Summary</h2>'
        f'<div class="task-summary-text" style="{_SUMMARY_TEXT_STYLE}">{summary_html}</div>'
        "</section>"
    )


def _render_meta_section(meta_items: list[tuple[str, str]], reply_lines: list[str]) -> str:
    if not meta_items and not reply_lines:
        return ""
    parts = [
        f'<section class="task-meta" style="{_SECTION_STYLE}">',
        f'<h2 style="{_SECTION_TITLE_STYLE}">Context</h2>',
    ]
    for label, value in meta_items:
        rendered_value = html.escape(value) if value else ""
        parts.append(
            f'<div class="task-meta-line" style="{_META_ITEM_STYLE}">'
            f'<span class="task-meta-label" style="{_META_LABEL_STYLE}">{html.escape(label)}</span>'
            f'<span class="task-meta-value" style="{_META_VALUE_STYLE}">{rendered_value}</span>'
            "</div>"
        )
    if reply_lines:
        reply_html = html.escape("\n".join(reply_lines)).replace("\n", "<br>\n")
        parts.extend(
            [
                '<div class="task-meta-reply" style="margin-top:4px;">',
                f'<span class="task-meta-label" style="{_META_LABEL_STYLE}">Reply</span>',
                f'<div class="task-text-block" style="{_TEXT_BLOCK_STYLE} {_MUTED_TEXT_STYLE}">{reply_html}</div>',
                "</div>",
            ]
        )
    parts.append("</section>")
    return "".join(parts)


def _render_body_section(
    body_lines: list[str],
    *,
    artifacts: list[RunArtifact] | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> str:
    if not body_lines:
        return ""
    content = _render_html_content_lines(body_lines, artifacts=artifacts, attachments=attachments)
    if not content:
        return ""
    return (
        f'<section class="task-body" style="{_SECTION_STYLE}">'
        f'<h2 style="{_SECTION_TITLE_STYLE}">Details</h2>'
        f"{content}"
        "</section>"
    )


def _render_named_markdown_section(
    title: str,
    lines: list[str],
    *,
    artifacts: list[RunArtifact] | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> str:
    if not lines:
        return ""
    section_class = _SECTION_CLASS_NAMES.get(title, "task-section")
    content = _render_html_content_lines(lines, artifacts=artifacts, attachments=attachments)
    if not content:
        return ""
    return (
        f'<section class="{section_class}" style="{_SECTION_STYLE}">'
        f'<h2 style="{_SECTION_TITLE_STYLE}">{html.escape(title)}</h2>'
        f"{content}"
        "</section>"
    )


def _render_inline_previews_section(
    lines: list[str],
    *,
    artifacts: list[RunArtifact] | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> str:
    if not lines:
        return ""
    content = _render_html_content_lines(lines, artifacts=artifacts, attachments=attachments)
    if not content:
        return ""
    return (
        f'<section class="task-inline-previews" style="{_SECTION_STYLE}">'
        f'<h2 style="{_SECTION_TITLE_STYLE}">Inline Previews</h2>'
        f"{content}"
        "</section>"
    )


def _render_capsule_section(capsule_kind: str, block_text: str) -> str:
    heading = {
        "state": "State Capsule",
        "question": "Question Capsule",
        "run-result": "Run Result",
    }.get(capsule_kind, "Capsule")
    css_class = {
        "state": "task-state-capsule",
        "question": "task-question-capsule",
        "run-result": "task-run-result",
    }.get(capsule_kind, "task-capsule")
    section_class = {
        "state": "task-state",
        "question": "task-questions",
        "run-result": "task-run-result-section",
    }.get(capsule_kind, "task-capsule-section")
    return (
        f'<section class="{section_class}" style="{_SECTION_STYLE}">'
        f'<h2 style="{_SECTION_TITLE_STYLE}">{heading}</h2>'
        f'<pre class="{css_class}" style="{_PRE_STYLE}">'
        f"{html.escape(block_text)}"
        "</pre>"
        "</section>"
    )


def render_status_markdown_to_html(
    markdown_body: str,
    *,
    artifacts: list[RunArtifact] | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> str:
    frontmatter_lines, sections, capsules = _split_status_markdown_blocks(markdown_body)
    summary_lines, meta_items, reply_lines, body_lines = _parse_html_frontmatter(frontmatter_lines)
    status_value: str | None = None
    if summary_lines:
        filtered_meta_items: list[tuple[str, str]] = []
        for label, value in meta_items:
            if status_value is None and label == "Status" and value:
                status_value = value
                continue
            filtered_meta_items.append((label, value))
        meta_items = filtered_meta_items
    parts = [
        "<html>",
        f'<body style="{_BODY_STYLE}">',
        f'<article class="task-mail" style="{_ARTICLE_STYLE}">',
    ]
    summary_section = _render_summary_section(summary_lines, status_value)
    if summary_section:
        parts.append(summary_section)
    meta_section = _render_meta_section(meta_items, reply_lines)
    if meta_section:
        parts.append(meta_section)
    body_section = _render_body_section(body_lines, artifacts=artifacts, attachments=attachments)
    if body_section:
        parts.append(body_section)
    inline_preview_lines: list[str] = []
    for title, section_lines in sections:
        visible_lines = section_lines
        if title == "Artifacts":
            visible_lines, artifact_preview_lines = _split_inline_preview_lines(section_lines)
            inline_preview_lines.extend(artifact_preview_lines)
        rendered_section = _render_named_markdown_section(
            title,
            visible_lines,
            artifacts=artifacts,
            attachments=attachments,
        )
        if rendered_section:
            parts.append(rendered_section)
    inline_previews_section = _render_inline_previews_section(
        inline_preview_lines,
        artifacts=artifacts,
        attachments=attachments,
    )
    if inline_previews_section:
        parts.append(inline_previews_section)
    for capsule_kind, block_text in capsules:
        parts.append(_render_capsule_section(capsule_kind, block_text))
    parts.extend(["</article>", "</body>", "</html>"])
    return "".join(parts)


def build_status_mail(
    status_label: str,
    state: ThreadState | dict[str, Any],
    *,
    task_snapshot: TaskSnapshot | None = None,
    result: RunResult | None = None,
    captured_reply: str | None = None,
    intro: str | None = None,
    question_id: str | None = None,
    question_text: str | None = None,
    pending_choices: list[str] | None = None,
    question_set_id: str | None = None,
    pending_questions: list[QuestionItem] | None = None,
    collected_answers: list[QuestionAnswer] | None = None,
    artifacts: list[RunArtifact] | None = None,
    external_deliveries: list[ExternalDelivery] | None = None,
    skipped_messages: list[str] | None = None,
) -> str:
    markdown_body = build_status_markdown(
        status_label,
        state,
        task_snapshot=task_snapshot,
        result=result,
        captured_reply=captured_reply,
        intro=intro,
        question_id=question_id,
        question_text=question_text,
        pending_choices=pending_choices,
        question_set_id=question_set_id,
        pending_questions=pending_questions,
        collected_answers=collected_answers,
        artifacts=artifacts,
        external_deliveries=external_deliveries,
        skipped_messages=skipped_messages,
    )
    return render_status_markdown_to_plain_text(markdown_body)


def build_status_html(
    plain_body: str,
    attachments: list[OutgoingAttachment] | None = None,
    skipped_messages: list[str] | None = None,
    *,
    markdown_body: str | None = None,
    artifacts: list[RunArtifact] | None = None,
) -> str:
    if markdown_body is not None:
        return render_status_markdown_to_html(
            markdown_body,
            artifacts=artifacts,
            attachments=attachments,
        )
    normalized_attachments = _ensure_inline_content_ids(list(attachments or []))
    parts = [
        "<html>",
        f'<body style="{_BODY_STYLE}">',
        f'<article class="task-mail" style="{_ARTICLE_STYLE}">',
        f'<section class="task-body" style="{_SECTION_STYLE}">',
        f'<h2 style="{_SECTION_TITLE_STYLE}">Details</h2>',
        f'<div class="task-text-block" style="{_TEXT_BLOCK_STYLE}">',
        html.escape(plain_body).replace("\n", "<br>\n"),
        "</div>",
        "</section>",
    ]
    if skipped_messages:
        parts.append(f'<section class="task-attachment-notices" style="{_SECTION_STYLE}">')
        parts.append(f'<h2 style="{_SECTION_TITLE_STYLE}">Attachment Notices</h2><ul style="{_LIST_STYLE}">')
        for item in skipped_messages:
            parts.append(f'<li style="margin:0 0 8px;">{html.escape(item)}</li>')
        parts.append("</ul></section>")
    inline_images = [
        item
        for item in normalized_attachments
        if item.inline and item.content_type and item.content_type.startswith("image/") and item.content_id
    ]
    if inline_images:
        parts.append(f'<section class="task-inline-previews" style="{_SECTION_STYLE}">')
        parts.append(f'<h2 style="{_SECTION_TITLE_STYLE}">Inline Previews</h2>')
        for item in inline_images:
            caption = html.escape(item.caption or item.name or item.path)
            parts.extend(
                [
                    f'<figure class="task-inline-preview" style="{_INLINE_PREVIEW_FIGURE_STYLE}">',
                    f'<img src="cid:{item.content_id}" alt="{caption}" style="{_INLINE_PREVIEW_IMAGE_STYLE}">',
                    f'<figcaption style="{_INLINE_PREVIEW_CAPTION_STYLE}">{caption}</figcaption>',
                    "</figure>",
                ]
            )
        parts.append("</section>")
    parts.extend(["</article>", "</body>", "</html>"])
    return "".join(parts)
