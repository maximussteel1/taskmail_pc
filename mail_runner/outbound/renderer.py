"""Outbound status-mail rendering facade."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    ExternalDelivery,
    OutgoingAttachment,
    QuestionAnswer,
    QuestionItem,
    RunArtifact,
    RunResult,
    TaskSnapshot,
    ThreadState,
)
from ..reporter import (
    build_status_html,
    build_status_markdown,
    build_status_subject,
    render_status_markdown_to_plain_text,
)


@dataclass(frozen=True)
class RenderedStatusMail:
    subject: str
    plain_body: str
    html_body: str


def render_status_mail(
    *,
    status_label: str,
    subject_text: str,
    state: ThreadState,
    task_snapshot: TaskSnapshot,
    attachments: list[OutgoingAttachment] | None = None,
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
) -> RenderedStatusMail:
    markdown_body = build_status_markdown(
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
    plain_body = render_status_markdown_to_plain_text(markdown_body)
    html_body = build_status_html(
        plain_body,
        attachments,
        skipped_messages,
        markdown_body=markdown_body,
        artifacts=artifacts,
    )
    subject = build_status_subject(status_label, subject_text, state.session_id or state.thread_id)
    return RenderedStatusMail(subject=subject, plain_body=plain_body, html_body=html_body)
