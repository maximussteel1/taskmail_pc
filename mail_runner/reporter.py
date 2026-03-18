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
from .state_capsule import render_question_capsules, render_state_capsule

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
    state_dict = asdict(state) if isinstance(state, ThreadState) else dict(state)
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
    if captured_reply:
        lines.extend(
            [
                "",
                "Reply:",
                captured_reply.rstrip(),
            ]
        )
    elif state_dict.get("last_summary"):
        lines.append(f"Summary: {state_dict['last_summary']}")
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
    return _build_status_markdown(
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


def render_status_markdown_to_html(
    markdown_body: str,
    *,
    artifacts: list[RunArtifact] | None = None,
    attachments: list[OutgoingAttachment] | None = None,
) -> str:
    normalized_attachments = _ensure_inline_content_ids(list(attachments or []))
    artifact_by_id = _artifact_map(list(artifacts or []))
    parts = ["<html>", "<body>"]
    text_buffer: list[str] = []
    list_items: list[str] = []

    def flush_text_buffer() -> None:
        if not text_buffer:
            return
        body_html = html.escape("\n".join(text_buffer)).replace("\n", "<br>\n")
        parts.extend(
            [
                '<div style="font-family:Consolas,Menlo,monospace; white-space:pre-wrap;">',
                body_html,
                "</div>",
            ]
        )
        text_buffer.clear()

    def flush_list_items() -> None:
        if not list_items:
            return
        parts.append("<ul>")
        parts.extend(list_items)
        parts.append("</ul>")
        list_items.clear()

    for raw_line in markdown_body.rstrip().splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_list_items()
            text_buffer.append("")
            continue
        if stripped.startswith("## "):
            flush_list_items()
            flush_text_buffer()
            parts.append(f"<h2>{html.escape(stripped[3:])}</h2>")
            continue
        image_match = _ARTIFACT_IMAGE_RE.match(stripped)
        if image_match:
            flush_list_items()
            flush_text_buffer()
            artifact = artifact_by_id.get(image_match.group("artifact_id").strip())
            alt_text = image_match.group("alt").strip() or (artifact.caption if artifact else None) or (artifact.name if artifact else None) or image_match.group("artifact_id").strip()
            if artifact is not None:
                attachment = _find_attachment_for_artifact(artifact, normalized_attachments)
                if attachment is not None and attachment.inline and attachment.content_type and attachment.content_type.startswith("image/"):
                    if not attachment.content_id:
                        attachment.content_id = f"mail-runner-inline-{len([item for item in normalized_attachments if item.content_id]) + 1}"
                    parts.extend(
                        [
                            '<figure style="margin:16px 0;">',
                            f'<img src="cid:{attachment.content_id}" alt="{html.escape(alt_text)}" style="max-width:100%; height:auto;">',
                            f"<figcaption>{html.escape(alt_text)}</figcaption>",
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
            list_items.append(f"<li>{label}{suffix}</li>")
            continue
        generic_link_match = _GENERIC_LINK_RE.match(stripped)
        if generic_link_match:
            flush_text_buffer()
            label = html.escape(generic_link_match.group("label"))
            suffix = html.escape(generic_link_match.group("suffix") or "")
            url = html.escape(generic_link_match.group("url"), quote=True)
            list_items.append(f'<li><a href="{url}">{label}</a>{suffix}</li>')
            continue
        bullet_match = _MARKDOWN_BULLET_RE.match(stripped)
        if bullet_match:
            flush_text_buffer()
            list_items.append(f"<li>{html.escape(bullet_match.group('text'))}</li>")
            continue
        flush_list_items()
        text_buffer.append(raw_line)

    flush_list_items()
    flush_text_buffer()
    parts.extend(["</body>", "</html>"])
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
    body_html = html.escape(plain_body).replace("\n", "<br>\n")
    parts = [
        "<html>",
        "<body>",
        '<div style="font-family:Consolas,Menlo,monospace; white-space:pre-wrap;">',
        body_html,
        "</div>",
    ]
    if skipped_messages:
        parts.append('<hr><div><strong>Attachment Notices</strong><ul>')
        for item in skipped_messages:
            parts.append(f"<li>{html.escape(item)}</li>")
        parts.append("</ul></div>")
    inline_images = [
        item
        for item in normalized_attachments
        if item.inline and item.content_type and item.content_type.startswith("image/") and item.content_id
    ]
    if inline_images:
        parts.append('<hr><div><strong>Image Previews</strong></div>')
        for item in inline_images:
            caption = html.escape(item.caption or item.name or item.path)
            parts.extend(
                [
                    '<figure style="margin:16px 0;">',
                    f'<img src="cid:{item.content_id}" alt="{caption}" style="max-width:100%; height:auto;">',
                    f"<figcaption>{caption}</figcaption>",
                    "</figure>",
                ]
            )
    parts.extend(["</body>", "</html>"])
    return "".join(parts)
