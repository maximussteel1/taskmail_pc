"""Minimal reporter output skeleton for the PC-side mail runner.

This file is not tied to a specific mail library. It freezes the output
shape that the runtime should produce before the actual MIME assembly step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Iterable


VISIBLE_TAGS = {
    "ACCEPTED",
    "RUNNING",
    "DONE",
    "FAILED",
    "KILLED",
    "PAUSED",
    "STATUS",
    "QUESTION",
    "SYNC",
}


@dataclass(slots=True)
class ArtifactItem:
    path: str
    name: str
    mime: str
    attach: bool = True
    inline: bool = False
    caption: str | None = None
    external_url: str | None = None
    skipped_reason: str | None = None
    content_id: str | None = None


@dataclass(slots=True)
class QuestionCapsule:
    question_set_id: str
    question_id: str
    question_type: str
    required: bool
    question_text: str
    choices: list[str] = field(default_factory=list)
    choice_labels: dict[str, str] = field(default_factory=dict)

    def to_block(self) -> str:
        lines = [
            "---TASK-QUESTION-BEGIN---",
            f"question_set_id: {self.question_set_id}",
            f"question_id: {self.question_id}",
            f"question_type: {self.question_type}",
            f"required: {'true' if self.required else 'false'}",
            f"question_text: {self.question_text}",
        ]
        if self.choices:
            lines.append(f"choices: {'|'.join(self.choices)}")
        if self.choice_labels:
            rendered = " | ".join(
                f"{key}={label}" for key, label in self.choice_labels.items()
            )
            lines.append(f"choice_labels: {rendered}")
        lines.append("---TASK-QUESTION-END---")
        return "\n".join(lines)


@dataclass(slots=True)
class RunResult:
    changed_files: list[str] = field(default_factory=list)
    tests_passed: bool | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_block(self) -> str:
        lines = [
            "---TASK-RUN-RESULT-BEGIN---",
            f"changed_files: {'|'.join(self.changed_files)}",
            f"tests_passed: {'' if self.tests_passed is None else str(self.tests_passed).lower()}",
            f"error_type: {self.error_type or ''}",
            f"error_message: {self.error_message or ''}",
            "---TASK-RUN-RESULT-END---",
        ]
        return "\n".join(lines)


@dataclass(slots=True)
class StateCapsule:
    thread_id: str
    workspace_id: str
    session_id: str
    session_name: str
    task_id: str
    backend: str
    repo_path: str
    workdir: str
    mode: str
    status: str
    last_summary: str

    def to_block(self) -> str:
        fields = {
            "thread_id": self.thread_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "task_id": self.task_id,
            "backend": self.backend,
            "repo_path": self.repo_path,
            "workdir": self.workdir,
            "mode": self.mode,
            "status": self.status,
            "last_summary": self.last_summary,
        }
        lines = ["---TASK-STATE-BEGIN---"]
        lines.extend(f"{key}: {value}" for key, value in fields.items())
        lines.append("---TASK-STATE-END---")
        return "\n".join(lines)


@dataclass(slots=True)
class ReporterEnvelope:
    tag: str
    display_title: str
    session_id: str
    summary: str
    permission: str | None
    reply_hint: str | None
    state: StateCapsule | None
    questions: list[QuestionCapsule] = field(default_factory=list)
    run_result: RunResult | None = None
    artifacts: list[ArtifactItem] = field(default_factory=list)
    extra_notes: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.tag not in VISIBLE_TAGS:
            raise ValueError(f"Unsupported visible tag: {self.tag}")

        if self.tag != "SYNC" and self.state is None:
            raise ValueError("Non-SYNC task mail must include a state capsule")

        if self.tag == "QUESTION" and not self.questions:
            raise ValueError("QUESTION mail must include at least one question capsule")

    @property
    def subject(self) -> str:
        return f"[{self.tag}] {self.display_title} [S:{self.session_id}]"


def _render_artifacts_text(items: Iterable[ArtifactItem]) -> list[str]:
    lines: list[str] = []
    items = list(items)
    if not items:
        return lines

    lines.append("Artifacts:")
    for item in items:
        suffix = f" ({item.caption})" if item.caption else ""
        lines.append(f"- {item.name}{suffix}")
    return lines


def _render_external_deliveries_text(items: Iterable[ArtifactItem]) -> list[str]:
    lines: list[str] = []
    external = [item for item in items if item.external_url]
    if not external:
        return lines

    lines.append("External Deliveries:")
    for item in external:
        lines.append(f"- {item.name}: {item.external_url}")
    return lines


def _render_attachment_notices_text(items: Iterable[ArtifactItem]) -> list[str]:
    skipped = [item for item in items if item.skipped_reason]
    if not skipped:
        return []

    lines = ["Attachment Notices:"]
    for item in skipped:
        lines.append(f"- {item.name}: {item.skipped_reason}")
    return lines


def build_plain_text(envelope: ReporterEnvelope) -> str:
    envelope.validate()

    lines: list[str] = [f"Summary: {envelope.summary}"]
    if envelope.permission:
        lines.append(f"Permission: {envelope.permission}")
    if envelope.reply_hint:
        lines.append(f"Reply: {envelope.reply_hint}")

    artifact_lines = _render_artifacts_text(envelope.artifacts)
    external_lines = _render_external_deliveries_text(envelope.artifacts)
    skipped_lines = _render_attachment_notices_text(envelope.artifacts)

    if artifact_lines:
        lines.extend(["", *artifact_lines])
    if external_lines:
        lines.extend(["", *external_lines])
    if skipped_lines:
        lines.extend(["", *skipped_lines])

    if envelope.extra_notes:
        lines.extend(["", *envelope.extra_notes])

    if envelope.run_result is not None:
        lines.extend(["", envelope.run_result.to_block()])

    if envelope.state is not None:
        lines.extend(["", envelope.state.to_block()])

    for question in envelope.questions:
        lines.extend(["", question.to_block()])

    return "\n".join(lines).strip() + "\n"


def _render_artifacts_html(items: Iterable[ArtifactItem]) -> str:
    items = list(items)
    if not items:
        return ""

    li_html: list[str] = []
    inline_previews: list[str] = []

    for item in items:
        label = escape(item.name)
        if item.content_id and item.inline and not item.external_url:
            href = f"cid:{escape(item.content_id)}"
            li_html.append(f'<li><a href="{href}">{label}</a></li>')
            if item.mime.startswith("image/"):
                alt = escape(item.caption or item.name)
                inline_previews.append(f'<p><img src="{href}" alt="{alt}" /></p>')
        elif item.external_url:
            href = escape(item.external_url, quote=True)
            li_html.append(
                f'<li><a href="{href}" rel="noopener noreferrer">{label}</a></li>'
            )
        else:
            li_html.append(f"<li>{label}</li>")

    return (
        '<section class="task-artifacts">'
        "<h4>Artifacts</h4>"
        f"<ul>{''.join(li_html)}</ul>"
        f"{''.join(inline_previews)}"
        "</section>"
    )


def build_html_fragment(envelope: ReporterEnvelope) -> str:
    envelope.validate()

    summary_html = escape(envelope.summary)
    permission_html = (
        f'<p><strong>Permission:</strong> {escape(envelope.permission)}</p>'
        if envelope.permission
        else ""
    )
    reply_html = (
        f'<p><strong>Reply:</strong> {escape(envelope.reply_hint)}</p>'
        if envelope.reply_hint
        else ""
    )

    notes_html = "".join(f"<p>{escape(note)}</p>" for note in envelope.extra_notes)
    artifacts_html = _render_artifacts_html(envelope.artifacts)
    run_result_html = (
        f'<pre class="task-run-result">{escape(envelope.run_result.to_block())}</pre>'
        if envelope.run_result is not None
        else ""
    )
    state_html = (
        f'<pre class="task-state-capsule">{escape(envelope.state.to_block())}</pre>'
        if envelope.state is not None
        else ""
    )
    questions_html = "".join(
        f'<pre class="task-question-capsule">{escape(question.to_block())}</pre>'
        for question in envelope.questions
    )

    return (
        '<article class="task-mail">'
        '<section class="task-summary">'
        "<h3>Summary</h3>"
        f"<p>{summary_html}</p>"
        "</section>"
        '<section class="task-meta">'
        f"{permission_html}{reply_html}"
        "</section>"
        f"{artifacts_html}"
        f"{notes_html}"
        f"{run_result_html}"
        f"{state_html}"
        f"{questions_html}"
        "</article>"
    )


def build_inline_attachment_map(artifacts: Iterable[ArtifactItem]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for item in artifacts:
        if item.content_id and item.attach and item.inline and not item.external_url:
            mapping[item.content_id] = Path(item.path)
    return mapping


if __name__ == "__main__":
    # Small smoke example for local testing.
    envelope = ReporterEnvelope(
        tag="DONE",
        display_title="Task Detail Android",
        session_id="s_20260318_001",
        summary="Implemented the task detail header and timeline cache wiring.",
        permission="highest",
        reply_hint="Continue the session if further refinement is needed.",
        state=StateCapsule(
            thread_id="thread_020",
            workspace_id="ws_repo_workdir",
            session_id="s_20260318_001",
            session_name="task_detail_android",
            task_id="task_20260318_001",
            backend="codex",
            repo_path=r"E:\repo",
            workdir=r"E:\repo",
            mode="modify",
            status="done",
            last_summary="Header and timeline cache are now wired.",
        ),
        run_result=RunResult(
            changed_files=[
                "feature/taskmail/presentation/detail/TaskDetailViewModel.kt",
                "feature/taskmail/data/repository/TaskTimelineRepositoryImpl.kt",
            ],
            tests_passed=True,
        ),
        artifacts=[
            ArtifactItem(
                path=r"E:\repo\artifacts\timeline_preview.png",
                name="timeline_preview.png",
                mime="image/png",
                attach=True,
                inline=True,
                caption="timeline preview",
                content_id="artifact-2",
            )
        ],
    )

    print(envelope.subject)
    print(build_plain_text(envelope))
    print(build_html_fragment(envelope))
