"""Operator-focused runtime health diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .health_semantics import DerivedHealth, derive_thread_health
from .models import RunResult, ThreadState
from .observe import ObserveContext, build_context, load_latest_run_result
from .stream_events import StreamEvent, load_stream_events, stream_events_path
from .workspace import WorkspaceManager

_POLL_CYCLE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s+INFO mail_runner\.app: Polling cycle complete\. "
    r"fetched=(?P<fetched>\d+) processed=(?P<processed>\d+) skipped=(?P<skipped>\d+) "
    r"failed=(?P<failed>\d+) busy=(?P<busy>True|False)$"
)

_ISSUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("websocket_tls_handshake_eof", re.compile(r"tls handshake eof", re.IGNORECASE)),
    ("websocket_connect_failure", re.compile(r"failed to connect to websocket", re.IGNORECASE)),
    (
        "responses_send_error",
        re.compile(r"error sending request for url \(https://chatgpt\.com/backend-api/codex/responses\)", re.IGNORECASE),
    ),
    (
        "websocket_fallback_https",
        re.compile(r"Falling back from WebSockets to HTTPS transport", re.IGNORECASE),
    ),
    (
        "models_refresh_timeout",
        re.compile(r"failed to refresh available models: timeout waiting for child process to exit", re.IGNORECASE),
    ),
)
_TRANSPORT_ISSUE_KINDS = {
    "websocket_tls_handshake_eof",
    "websocket_connect_failure",
    "responses_send_error",
    "websocket_fallback_https",
    "models_refresh_timeout",
}


@dataclass(slots=True)
class PollCycle:
    ts: str
    fetched: int
    processed: int
    skipped: int
    failed: int
    busy: bool


@dataclass(slots=True)
class LoopSummary:
    cycle_count: int
    last_cycle_at: str | None
    busy_now: bool | None
    fetched_total: int
    processed_total: int
    skipped_total: int
    failed_total: int


@dataclass(slots=True)
class IssueEvidence:
    kind: str
    snippets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ThreadDiagnosis:
    thread_id: str
    status: str
    lifecycle: str
    backend: str
    backend_transport: str
    current_task_id: str | None
    health: str
    assessment: str
    latest_run_task_id: str | None
    latest_run_status: str | None
    latest_run_exit_code: int | None
    latest_run_started_at: str | None
    latest_run_finished_at: str | None
    latest_stream_ts: str | None
    latest_stream_kind: str | None
    issue_kinds: list[str] = field(default_factory=list)
    issue_snippets: list[str] = field(default_factory=list)
    recovery_issue_kinds: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    summary_excerpt: str | None = None


@dataclass(slots=True)
class RuntimeHealthReport:
    host_status: str
    host_pid: int | None
    host_alive: bool
    config_path: str
    runtime_dir: str
    task_root: str
    loop_summary: LoopSummary
    threads: list[ThreadDiagnosis] = field(default_factory=list)
    missing_threads: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_runtime_health_report(
    *,
    config_path: str | None = None,
    runtime_dir: str | None = None,
    task_root: str | None = None,
    thread_ids: list[str] | None = None,
    recent_cycles: int = 120,
    max_threads: int = 5,
) -> RuntimeHealthReport:
    context = build_context(config_path=config_path, runtime_dir=runtime_dir, task_root=task_root)
    host_state = context.host_state or {}
    host_status = str(host_state.get("status") or "missing").strip() or "missing"
    host_pid = _coerce_int(host_state.get("pid"))
    host_alive = _pid_is_alive(host_pid)
    loop_summary = summarize_loop_cycles(context.runtime_dir, recent_cycles=recent_cycles)
    selected_threads, missing_threads = _select_threads(context, thread_ids=thread_ids, max_threads=max_threads)
    return RuntimeHealthReport(
        host_status=host_status,
        host_pid=host_pid,
        host_alive=host_alive,
        config_path=str(context.config_path),
        runtime_dir=str(context.runtime_dir),
        task_root=str(context.task_root),
        loop_summary=loop_summary,
        threads=[diagnose_thread(context, thread, host_alive=host_alive) for thread in selected_threads],
        missing_threads=missing_threads,
    )


def summarize_loop_cycles(runtime_dir: str | Path, *, recent_cycles: int = 120) -> LoopSummary:
    log_path = Path(runtime_dir) / "loop.stderr.log"
    if not log_path.exists():
        return LoopSummary(
            cycle_count=0,
            last_cycle_at=None,
            busy_now=None,
            fetched_total=0,
            processed_total=0,
            skipped_total=0,
            failed_total=0,
        )
    cycles = parse_poll_cycles(log_path.read_text(encoding="utf-8", errors="replace"), recent_cycles=recent_cycles)
    if not cycles:
        return LoopSummary(
            cycle_count=0,
            last_cycle_at=None,
            busy_now=None,
            fetched_total=0,
            processed_total=0,
            skipped_total=0,
            failed_total=0,
        )
    last_cycle = cycles[-1]
    return LoopSummary(
        cycle_count=len(cycles),
        last_cycle_at=last_cycle.ts,
        busy_now=last_cycle.busy,
        fetched_total=sum(item.fetched for item in cycles),
        processed_total=sum(item.processed for item in cycles),
        skipped_total=sum(item.skipped for item in cycles),
        failed_total=sum(item.failed for item in cycles),
    )


def parse_poll_cycles(text: str, *, recent_cycles: int = 120) -> list[PollCycle]:
    cycles: list[PollCycle] = []
    for raw_line in text.splitlines():
        match = _POLL_CYCLE_RE.match(raw_line.strip())
        if not match:
            continue
        cycles.append(
            PollCycle(
                ts=match.group("ts"),
                fetched=int(match.group("fetched")),
                processed=int(match.group("processed")),
                skipped=int(match.group("skipped")),
                failed=int(match.group("failed")),
                busy=match.group("busy") == "True",
            )
        )
    if recent_cycles > 0:
        return cycles[-recent_cycles:]
    return cycles


def diagnose_thread(context: ObserveContext, thread: ThreadState, *, host_alive: bool) -> ThreadDiagnosis:
    session = next((item for item in context.sessions if item.thread_id == thread.thread_id), None)
    health = derive_thread_health(thread, host_alive=host_alive, session=session, task_root=context.task_root)
    run = _current_run_artifacts(context.task_root, thread)
    latest_stream = run["stream_events"][-1] if run["stream_events"] else None
    current_issue_evidence = detect_issue_evidence(
        run["summary_text"],
        run["stderr_text"],
        *(event.text or "" for event in run["stream_events"]),
    )

    previous_transport_kinds: list[str] = []
    previous_transport_task_id: str | None = None
    if run["result"] is not None and run["result"].status == "success":
        previous_result, previous_texts = _previous_result_and_texts(context.task_root, thread)
        previous_evidence = detect_issue_evidence(*previous_texts)
        previous_transport_kinds = [item.kind for item in previous_evidence if item.kind in _TRANSPORT_ISSUE_KINDS]
        if previous_result is not None and previous_transport_kinds:
            previous_transport_task_id = previous_result.task_id

    issue_kinds = [item.kind for item in current_issue_evidence]
    issue_snippets = [snippet for item in current_issue_evidence for snippet in item.snippets]
    assessment = classify_thread_assessment(
        thread=thread,
        health=health,
        result=run["result"],
        has_live_run=run["has_live_run"],
        stream_events=run["stream_events"],
        issue_evidence=current_issue_evidence,
        previous_transport_kinds=previous_transport_kinds,
    )
    summary_excerpt = first_line(run["summary_text"]) or thread.last_summary

    evidence = [
        f"thread_status={thread.status} lifecycle={thread.lifecycle} health={health.status}",
    ]
    if run["result"] is not None:
        evidence.append(
            f"latest_run={run['result'].task_id} status={run['result'].status} exit={_text_or_dash(run['result'].exit_code)}"
        )
    elif run["has_live_run"]:
        evidence.append(f"live_run={_text_or_dash(thread.current_task_id)} result=pending")
    if latest_stream is not None:
        evidence.append(f"latest_stream={latest_stream.kind}@{latest_stream.ts}")
    if previous_transport_task_id and previous_transport_kinds:
        evidence.append(
            f"previous_transport_issue={previous_transport_task_id} kinds={','.join(previous_transport_kinds)}"
        )

    return ThreadDiagnosis(
        thread_id=thread.thread_id,
        status=thread.status,
        lifecycle=thread.lifecycle,
        backend=thread.backend,
        backend_transport=thread.backend_transport,
        current_task_id=thread.current_task_id,
        health=health.status,
        assessment=assessment,
        latest_run_task_id=run["task_id"],
        latest_run_status=run["result"].status if run["result"] is not None else None,
        latest_run_exit_code=run["result"].exit_code if run["result"] is not None else None,
        latest_run_started_at=run["result"].started_at if run["result"] is not None else None,
        latest_run_finished_at=run["result"].finished_at if run["result"] is not None else None,
        latest_stream_ts=latest_stream.ts if latest_stream is not None else None,
        latest_stream_kind=latest_stream.kind if latest_stream is not None else None,
        issue_kinds=issue_kinds,
        issue_snippets=issue_snippets,
        recovery_issue_kinds=previous_transport_kinds,
        evidence=evidence,
        summary_excerpt=summary_excerpt,
    )


def classify_thread_assessment(
    *,
    thread: ThreadState,
    health: DerivedHealth,
    result: RunResult | None,
    has_live_run: bool,
    stream_events: list[StreamEvent],
    issue_evidence: list[IssueEvidence],
    previous_transport_kinds: list[str],
) -> str:
    transport_issue_kinds = [item.kind for item in issue_evidence if item.kind in _TRANSPORT_ISSUE_KINDS]
    if health.status == "orphaned":
        return "orphaned"
    if health.status == "suspected_stuck":
        return "suspected_stuck"
    if health.status == "stale":
        return "stale"
    if result is not None:
        if result.status == "success":
            if previous_transport_kinds:
                return "transport_recovered"
            return "healthy_done"
        if result.status == "failed":
            if transport_issue_kinds:
                return "transport_failure"
            return "runtime_failure"
        if result.status == "killed":
            return "killed"
        if result.status == "paused":
            return "paused"
        if result.status == "awaiting_user_input":
            return "awaiting_user_input"
    if has_live_run:
        if transport_issue_kinds:
            return "running_with_transport_warnings"
        if stream_events:
            return "healthy_progressing"
        return "running_without_stream"
    if thread.status == "done":
        return "healthy_done"
    if thread.status == "failed":
        return "runtime_failure"
    return "idle"


def detect_issue_evidence(*texts: str) -> list[IssueEvidence]:
    snippets_by_kind: dict[str, list[str]] = {}
    for text in texts:
        normalized = str(text or "").strip()
        if not normalized:
            continue
        for raw_line in normalized.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            for kind, pattern in _ISSUE_PATTERNS:
                if not pattern.search(line):
                    continue
                snippets = snippets_by_kind.setdefault(kind, [])
                if line not in snippets:
                    snippets.append(line)
    return [IssueEvidence(kind=kind, snippets=snippets) for kind, snippets in snippets_by_kind.items()]


def render_runtime_health_report(report: RuntimeHealthReport) -> str:
    loop = report.loop_summary
    lines = [
        "Summary",
        f"Host: {report.host_status} | pid={_text_or_dash(report.host_pid)} | alive={'yes' if report.host_alive else 'no'}",
        (
            "Recent polling: "
            f"cycles={loop.cycle_count} "
            f"last={_text_or_dash(loop.last_cycle_at)} "
            f"busy_now={_text_or_dash(loop.busy_now)} "
            f"fetched_total={loop.fetched_total} "
            f"processed_total={loop.processed_total} "
            f"failed_total={loop.failed_total}"
        ),
        f"Runtime Dir: {report.runtime_dir}",
        f"Task Root: {report.task_root}",
        f"Threads Inspected: {len(report.threads)}",
    ]
    if report.missing_threads:
        lines.append(f"Missing Threads: {', '.join(report.missing_threads)}")
    lines.append("")
    lines.append("Threads")
    if not report.threads:
        lines.append("(none)")
        return "\n".join(lines)
    for item in report.threads:
        lines.append(
            " | ".join(
                [
                    item.thread_id,
                    f"assessment={item.assessment}",
                    f"status={item.status}",
                    f"lifecycle={item.lifecycle}",
                    f"health={item.health}",
                    f"backend={item.backend}/{item.backend_transport}",
                    f"task={_text_or_dash(item.latest_run_task_id or item.current_task_id)}",
                ]
            )
        )
        if item.latest_run_status is not None:
            lines.append(
                "latest_run: "
                f"status={item.latest_run_status} "
                f"exit={_text_or_dash(item.latest_run_exit_code)} "
                f"started={_text_or_dash(item.latest_run_started_at)} "
                f"finished={_text_or_dash(item.latest_run_finished_at)}"
            )
        elif item.latest_stream_ts is not None:
            lines.append(f"latest_stream: {_text_or_dash(item.latest_stream_kind)} at {_text_or_dash(item.latest_stream_ts)}")
        if item.issue_kinds:
            lines.append(f"issues: {', '.join(item.issue_kinds)}")
        if item.recovery_issue_kinds:
            lines.append(f"recovery: previous transport issues {', '.join(item.recovery_issue_kinds)}")
        if item.summary_excerpt:
            lines.append(f"summary: {item.summary_excerpt}")
        for evidence in item.evidence:
            lines.append(f"evidence: {evidence}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize runtime health using host, loop, run, and stream evidence.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--task-root", default=None)
    parser.add_argument("--thread-id", action="append", dest="thread_ids", default=None)
    parser.add_argument("--recent-cycles", type=int, default=120)
    parser.add_argument("--max-threads", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_runtime_health_report(
        config_path=args.config,
        runtime_dir=args.runtime_dir,
        task_root=args.task_root,
        thread_ids=args.thread_ids,
        recent_cycles=max(1, args.recent_cycles),
        max_threads=max(1, args.max_threads),
    )
    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(render_runtime_health_report(report) + "\n")
    return 0


def _current_run_artifacts(task_root: Path, thread: ThreadState) -> dict[str, object]:
    workspace = WorkspaceManager(task_root)
    task_id = str(thread.current_task_id or "").strip() or None
    run_dir = workspace.run_dir(thread.thread_id, task_id) if task_id is not None else None
    has_live_run = bool(run_dir and run_dir.exists())
    result = _load_run_result(run_dir / "result.json") if run_dir is not None else None
    summary_text = _read_text(run_dir / "summary.md") if run_dir is not None else ""
    stderr_text = _read_text(run_dir / "stderr.log") if run_dir is not None else ""
    stream_events = load_stream_events(stream_events_path(task_root, thread.thread_id, task_id)) if task_id else []
    if result is None and not has_live_run:
        latest_result = load_latest_run_result(task_root, thread)
        if latest_result is not None and latest_result.task_id != task_id:
            run_dir = workspace.run_dir(thread.thread_id, latest_result.task_id)
            summary_text = _read_text(run_dir / "summary.md")
            stderr_text = _read_text(run_dir / "stderr.log")
            result = latest_result
            task_id = latest_result.task_id
            stream_events = load_stream_events(stream_events_path(task_root, thread.thread_id, latest_result.task_id))
    return {
        "task_id": task_id,
        "run_dir": run_dir,
        "result": result,
        "summary_text": summary_text,
        "stderr_text": stderr_text,
        "stream_events": stream_events,
        "has_live_run": has_live_run,
    }


def _previous_result_and_texts(task_root: Path, thread: ThreadState) -> tuple[RunResult | None, tuple[str, str]]:
    if len(thread.history_files) < 2:
        return None, ("", "")
    workspace = WorkspaceManager(task_root)
    previous_path = workspace.thread_dir(thread.thread_id) / thread.history_files[-2]
    if not previous_path.exists():
        return None, ("", "")
    result = RunResult(**workspace.load_json(previous_path))
    run_dir = workspace.run_dir(thread.thread_id, result.task_id)
    return result, (_read_text(run_dir / "summary.md"), _read_text(run_dir / "stderr.log"))


def _select_threads(
    context: ObserveContext,
    *,
    thread_ids: list[str] | None,
    max_threads: int,
) -> tuple[list[ThreadState], list[str]]:
    thread_by_id = {item.thread_id: item for item in context.threads}
    if thread_ids:
        selected: list[ThreadState] = []
        missing: list[str] = []
        for thread_id in thread_ids:
            thread = thread_by_id.get(thread_id)
            if thread is None:
                missing.append(thread_id)
                continue
            selected.append(thread)
        return selected, missing
    active = [item for item in context.threads if item.lifecycle == "active"]
    if active:
        return active[:max_threads], []
    return context.threads[:max_threads], []


def _load_run_result(path: Path) -> RunResult | None:
    if not path.exists():
        return None
    return RunResult(**json.loads(path.read_text(encoding="utf-8")))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _coerce_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        process = kernel32.OpenProcess(0x1000, False, wintypes.DWORD(pid))
        if not process:
            return False
        kernel32.CloseHandle(process)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _text_or_dash(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text or "-"


def first_line(text: str | None) -> str | None:
    if text is None:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return None
