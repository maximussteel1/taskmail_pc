"""Minimal read-only observability commands for the local mail runner runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

from .config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config
from .health_semantics import DerivedHealth, derive_session_health, derive_thread_health
from .host import resolve_runtime_dir
from .host_state import load_host_state
from .models import RunResult, SessionState, ThreadState
from .session_semantics import thread_monitor_exit_reason, thread_monitor_should_stay_open
from .stream_events import StreamEvent, load_stream_events, stream_events_path
from .thread_store import build_workspace_id, load_thread_state
from .transcript_export import TranscriptTurn, build_thread_transcript
from .workspace import WorkspaceManager


@dataclass(slots=True)
class ObserveContext:
    config_path: Path
    runtime_dir: Path
    task_root: Path
    host_state: dict[str, object] | None
    threads: list[ThreadState]
    sessions: list[SessionState]


@dataclass(slots=True)
class ThreadFollowCursor:
    last_transcript_index: int = 0
    stream_seq_by_task: dict[str, int] = field(default_factory=dict)
    current_live_task_id: str | None = None
    assistant_stream_open: bool = False


def resolve_observe_config_path(config_path: str | None, runtime_dir: Path) -> Path:
    if config_path:
        return Path(config_path).resolve()

    candidates = [
        runtime_dir / "mail_config.loop_30s.yaml",
        PROJECT_ROOT / "mail_config.bot.local.yaml",
        PROJECT_ROOT / "mail_config.local.yaml",
        DEFAULT_CONFIG_PATH,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return DEFAULT_CONFIG_PATH.resolve()


def build_context(
    *,
    config_path: str | None = None,
    runtime_dir: str | None = None,
    task_root: str | None = None,
) -> ObserveContext:
    resolved_runtime_dir = resolve_runtime_dir(runtime_dir)
    resolved_config_path = resolve_observe_config_path(config_path, resolved_runtime_dir)
    if task_root:
        resolved_task_root = Path(task_root).resolve()
    else:
        config = load_config(str(resolved_config_path))
        resolved_task_root = config.resolve_task_root(resolved_config_path.parent)
    threads = load_all_thread_states(resolved_task_root)
    sessions = load_all_session_states(resolved_task_root)
    return ObserveContext(
        config_path=resolved_config_path,
        runtime_dir=resolved_runtime_dir,
        task_root=resolved_task_root,
        host_state=load_host_state(resolved_runtime_dir),
        threads=threads,
        sessions=sessions,
    )


def load_all_thread_states(task_root: str | Path) -> list[ThreadState]:
    workspace = WorkspaceManager(task_root)
    if not workspace.task_root.exists():
        return []
    states: list[ThreadState] = []
    for state_path in sorted(workspace.task_root.glob("thread_*/thread_state.json")):
        thread_id = state_path.parent.name
        states.append(load_thread_state(thread_id, workspace.task_root))
    return sorted(states, key=lambda item: item.updated_at, reverse=True)


def load_all_session_states(task_root: str | Path) -> list[SessionState]:
    workspace = WorkspaceManager(task_root)
    sessions_dir = workspace.workspaces_dir()
    if not sessions_dir.exists():
        return []
    sessions: list[SessionState] = []
    for state_path in sorted(sessions_dir.glob("*/sessions/*.json")):
        payload = workspace.load_json(state_path)
        sessions.append(SessionState(**payload))
    return sorted(sessions, key=lambda item: item.updated_at, reverse=True)


def build_queue_entries(sessions: list[SessionState]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for session in sessions:
        if session.lifecycle != "active":
            continue
        if session.status == "queued":
            entries.append(
                {
                    "kind": "queued-session",
                    "thread_id": session.thread_id,
                    "session_id": session.session_id,
                    "backend": session.backend,
                    "backend_transport": session.backend_transport,
                    "task_id": session.current_task_id,
                    "repo_path": session.repo_path,
                    "workdir": session.workdir or ".",
                    "updated_at": session.updated_at,
                }
            )
        if session.queued_task_id and session.status != "queued":
            entries.append(
                {
                    "kind": "follow-up",
                    "thread_id": session.thread_id,
                    "session_id": session.session_id,
                    "backend": session.backend,
                    "backend_transport": session.backend_transport,
                    "task_id": session.queued_task_id,
                    "repo_path": session.repo_path,
                    "workdir": session.workdir or ".",
                    "updated_at": session.updated_at,
                }
            )
    return sorted(entries, key=lambda item: item["updated_at"], reverse=True)


def load_latest_run_result(task_root: Path, thread: ThreadState) -> RunResult | None:
    if not thread.history_files:
        return None
    latest_rel = thread.history_files[-1]
    latest_path = task_root / thread.thread_id / latest_rel
    if not latest_path.exists():
        return None
    payload = WorkspaceManager(task_root).load_json(latest_path)
    return RunResult(**payload)


def render_status(context: ObserveContext) -> str:
    active_sessions = [item for item in context.sessions if item.lifecycle == "active"]
    ended_sessions = [item for item in context.sessions if item.lifecycle == "ended"]
    running_sessions = [item for item in active_sessions if item.status == "running"]
    waiting_sessions = [item for item in active_sessions if item.status == "waiting_user"]
    paused_sessions = [item for item in active_sessions if item.status == "paused"]
    failed_threads = [item for item in context.threads if item.status == "failed"]
    queue_entries = build_queue_entries(context.sessions)
    host_state = context.host_state or {}
    host_status = _text_or_dash(host_state.get("status"))
    host_pid = _text_or_dash(host_state.get("pid"))
    host_alive = _pid_is_alive(host_state.get("pid"))
    host_pid_alive = "yes" if host_alive else "no"
    active_health = [derive_session_health(item, host_alive=host_alive) for item in active_sessions]
    stale_sessions = sum(1 for item in active_health if item.status == "stale")
    suspected_stuck_sessions = sum(1 for item in active_health if item.status == "suspected_stuck")
    orphaned_sessions = sum(1 for item in active_health if item.status == "orphaned")

    lines = [
        f"Host: {host_status}",
        f"PID: {host_pid}",
        f"PID Alive: {host_pid_alive}",
        f"Config: {context.config_path}",
        f"Runtime Dir: {context.runtime_dir}",
        f"Task Root: {context.task_root}",
        "",
        f"Threads Total: {len(context.threads)}",
        f"Sessions Total: {len(context.sessions)}",
        f"Active Sessions: {len(active_sessions)}",
        f"Ended Sessions: {len(ended_sessions)}",
        f"Running Sessions: {len(running_sessions)}",
        f"Queue Items: {len(queue_entries)}",
        f"Waiting User Sessions: {len(waiting_sessions)}",
        f"Paused Sessions: {len(paused_sessions)}",
        f"Stale Sessions: {stale_sessions}",
        f"Suspected Stuck Sessions: {suspected_stuck_sessions}",
        f"Orphaned Sessions: {orphaned_sessions}",
        f"Failed Threads: {len(failed_threads)}",
    ]
    return "\n".join(lines)


def render_running_sessions(context: ObserveContext) -> str:
    running_sessions = [item for item in context.sessions if item.lifecycle == "active" and item.status == "running"]
    if not running_sessions:
        return "(none)"
    host_alive = _pid_is_alive((context.host_state or {}).get("pid"))
    return "\n".join(_format_session_line(item, derive_session_health(item, host_alive=host_alive)) for item in running_sessions)


def render_queue_entries(context: ObserveContext) -> str:
    entries = build_queue_entries(context.sessions)
    if not entries:
        return "(none)"
    lines = []
    for item in entries:
        lines.append(
            " | ".join(
                [
                    item["kind"],
                    f"thread={item['thread_id']}",
                    f"session={item['session_id']}",
                    f"backend={item['backend']}",
                    f"transport={item['backend_transport']}",
                    f"task={item['task_id']}",
                    f"repo={item['repo_path']}",
                    f"workdir={item['workdir']}",
                    f"updated={item['updated_at']}",
                ]
            )
        )
    return "\n".join(lines)


def render_thread_details(context: ObserveContext, thread_id: str) -> str | None:
    thread = next((item for item in context.threads if item.thread_id == thread_id), None)
    if thread is None:
        return None
    session = next((item for item in context.sessions if item.thread_id == thread.thread_id), None)
    latest_result = load_latest_run_result(context.task_root, thread)
    host_alive = _pid_is_alive((context.host_state or {}).get("pid"))
    health = derive_thread_health(thread, host_alive=host_alive, session=session, task_root=context.task_root)
    lines = [
        f"Thread ID: {thread.thread_id}",
        f"Status: {thread.status}",
        f"Session ID: {_text_or_dash(thread.session_id or thread.thread_id)}",
        f"Workspace ID: {_text_or_dash(thread.workspace_id or build_workspace_id(thread.repo_path, thread.workdir))}",
        f"Backend: {thread.backend}",
        f"Backend Transport: {thread.backend_transport}",
        f"Profile: {_text_or_dash(thread.profile)}",
        f"Permission: {_text_or_dash(thread.permission)}",
        f"Repo: {thread.repo_path}",
        f"Workdir: {_text_or_dash(thread.workdir or '.')}",
        f"Current Task ID: {thread.current_task_id}",
        f"Queued Task ID: {_text_or_dash(thread.queued_task_id)}",
        f"Lifecycle: {thread.lifecycle}",
        f"Last Active At: {_text_or_dash(thread.last_active_at)}",
        f"Last Progress At: {_text_or_dash(health.last_progress_at)}",
        f"Health: {health.status}",
        f"Idle For: {_format_idle_seconds(health.idle_seconds)}",
        f"Backend Session ID: {_text_or_dash(thread.backend_session_id)}",
        f"Backend Session Resumable: {'true' if thread.backend_session_resumable else 'false'}",
        f"Updated At: {thread.updated_at}",
        f"Last Summary: {_text_or_dash(thread.last_summary)}",
        f"History Count: {len(thread.history_files)}",
    ]
    if health.reason:
        lines.append(f"Health Reason: {health.reason}")
    if session is not None:
        lines.extend(
            [
                f"Session Status: {session.status}",
                f"Session Lifecycle: {session.lifecycle}",
                f"Session Last Progress At: {_text_or_dash(session.last_progress_at)}",
                f"Pending Task Count: {session.pending_task_count}",
            ]
        )
    else:
        lines.append("Session Status: -")
    if latest_result is not None:
        lines.extend(
            [
                f"Latest Run Status: {latest_result.status}",
                f"Latest Run Exit Code: {_text_or_dash(latest_result.exit_code)}",
                f"Latest Run Finished At: {_text_or_dash(latest_result.finished_at)}",
                f"Latest Run Summary File: {_text_or_dash(latest_result.summary_file)}",
            ]
        )
    else:
        lines.append("Latest Run Status: -")
    return "\n".join(lines)


def render_thread_live(context: ObserveContext, thread_id: str) -> str | None:
    thread = next((item for item in context.threads if item.thread_id == thread_id), None)
    if thread is None:
        return None
    session = next((item for item in context.sessions if item.thread_id == thread.thread_id), None)
    latest_result = load_latest_run_result(context.task_root, thread)
    transcript_turns = _load_transcript_turns(context.task_root, thread.thread_id)
    stream_path = stream_events_path(context.task_root, thread.thread_id, thread.current_task_id)
    stream_events, stream_error = _load_live_stream(stream_path)
    assistant_text, assistant_started_at, assistant_last_update_at, assistant_completed = _collect_live_assistant(stream_events)
    live_event_lines = _render_live_event_lines(stream_events)
    host_alive = _pid_is_alive((context.host_state or {}).get("pid"))
    health = derive_thread_health(thread, host_alive=host_alive, session=session, task_root=context.task_root)

    lines = [
        f"Thread ID: {thread.thread_id}",
        f"Status: {thread.status}",
        f"Session Status: {_text_or_dash(session.status if session is not None else None)}",
        f"Backend: {thread.backend}",
        f"Backend Transport: {thread.backend_transport}",
        f"Current Task ID: {thread.current_task_id}",
        f"Lifecycle: {thread.lifecycle}",
        f"Last Active At: {_text_or_dash(thread.last_active_at)}",
        f"Last Progress At: {_text_or_dash(health.last_progress_at)}",
        f"Health: {health.status}",
        f"Idle For: {_format_idle_seconds(health.idle_seconds)}",
        f"Stream Log: {stream_path}",
    ]
    if health.reason:
        lines.append(f"Health Reason: {health.reason}")
    if latest_result is not None:
        lines.extend(
            [
                f"Latest Run Status: {latest_result.status}",
                f"Latest Run Finished At: {_text_or_dash(latest_result.finished_at)}",
            ]
        )
    else:
        lines.append("Latest Run Status: -")

    if stream_error is not None:
        lines.append(f"Live Stream: unavailable ({stream_error})")
    elif stream_events:
        lines.append(f"Live Stream: available ({len(stream_events)} events)")
    elif stream_path.exists():
        lines.append("Live Stream: available (no events yet)")
    else:
        lines.append("Live Stream: unavailable")

    lines.extend(["", "=== TRANSCRIPT ==="])
    if transcript_turns:
        for turn in transcript_turns:
            lines.extend(_render_transcript_turn(turn))
    else:
        lines.append("(no archived transcript)")

    lines.extend(["", "=== LIVE ASSISTANT ==="])
    if assistant_text:
        lines.append(f"Started At: {_text_or_dash(assistant_started_at)}")
        lines.append(f"Last Update At: {_text_or_dash(assistant_last_update_at)}")
        lines.append(f"Completion: {'completed' if assistant_completed else 'streaming'}")
        lines.append("")
        lines.append(assistant_text)
    else:
        lines.append("(no assistant stream yet)")

    lines.extend(["", "=== LIVE EVENTS ==="])
    if live_event_lines:
        lines.extend(live_event_lines)
    else:
        lines.append("(no live events)")

    return "\n".join(lines)


def follow_thread_live(
    *,
    config_path: str | None,
    runtime_dir: str | None,
    task_root: str | None,
    thread_id: str,
    poll_seconds: float,
    iterations: int,
    history_limit: int,
    exit_when_inactive: bool,
) -> int:
    cursor = ThreadFollowCursor()
    completed_iterations = 0
    while True:
        context = build_context(config_path=config_path, runtime_dir=runtime_dir, task_root=task_root)
        thread = _find_thread(context, thread_id)
        if thread is None:
            print(f"Thread not found: {thread_id}")
            return 1

        is_initial = completed_iterations == 0
        chunks = _collect_follow_chunks(
            context=context,
            thread=thread,
            cursor=cursor,
            include_history=is_initial,
            history_limit=history_limit,
        )
        _write_follow_chunks(chunks)

        if exit_when_inactive and not thread_monitor_should_stay_open(thread):
            _write_follow_chunks(_finalize_follow_output(cursor))
            print(f"Thread {thread.thread_id} monitor closed: {thread_monitor_exit_reason(thread)}.")
            return 0

        completed_iterations += 1
        if iterations > 0 and completed_iterations >= iterations:
            _write_follow_chunks(_finalize_follow_output(cursor))
            return 0
        if poll_seconds > 0:
            time.sleep(poll_seconds)


def _find_thread(context: ObserveContext, thread_id: str) -> ThreadState | None:
    return next((item for item in context.threads if item.thread_id == thread_id), None)


def _collect_follow_chunks(
    *,
    context: ObserveContext,
    thread: ThreadState,
    cursor: ThreadFollowCursor,
    include_history: bool,
    history_limit: int,
) -> list[str]:
    chunks: list[str] = []
    transcript_turns = _load_transcript_turns(context.task_root, thread.thread_id)
    if include_history:
        chunks.extend(_line_chunks(_render_follow_header(thread)))
        chunks.extend(_render_follow_history(transcript_turns, history_limit))
        if transcript_turns:
            cursor.last_transcript_index = transcript_turns[-1].index
    else:
        new_turns = [turn for turn in transcript_turns if turn.index > cursor.last_transcript_index]
        if new_turns:
            chunks.extend(_finalize_follow_output(cursor))
            for turn in new_turns:
                chunks.extend(_line_chunks(_render_transcript_turn(turn)))
            cursor.last_transcript_index = transcript_turns[-1].index

    current_task_id = (thread.current_task_id or "").strip()
    if current_task_id:
        stream_path = stream_events_path(context.task_root, thread.thread_id, current_task_id)
        stream_events, _ = _load_live_stream(stream_path)
        if stream_events:
            if include_history and thread.status not in {"accepted", "running"}:
                cursor.stream_seq_by_task[current_task_id] = stream_events[-1].seq
            else:
                last_seq = cursor.stream_seq_by_task.get(current_task_id, 0)
                new_events = [event for event in stream_events if event.seq > last_seq]
                if new_events:
                    chunks.extend(_render_follow_stream_events(new_events, cursor, task_id=current_task_id))
                    cursor.stream_seq_by_task[current_task_id] = stream_events[-1].seq
    return chunks


def _render_follow_header(thread: ThreadState) -> list[str]:
    return [
        f"Live Thread Monitor: {thread.thread_id}",
        "This window appends new user messages and live backend output. Press Ctrl+C to close.",
        (
            f"Session: {_text_or_dash(thread.session_id or thread.thread_id)}"
            f" | lifecycle={thread.lifecycle}"
            f" | status={thread.status}"
            f" | backend={thread.backend}/{thread.backend_transport}"
            f" | task={thread.current_task_id}"
            f" | resumable={'true' if thread.backend_session_resumable else 'false'}"
        ),
        f"Repo: {thread.repo_path}",
        f"Workdir: {_text_or_dash(thread.workdir or '.')}",
        "",
    ]


def _render_follow_history(turns: list[TranscriptTurn], history_limit: int) -> list[str]:
    lines = ["=== RECENT TRANSCRIPT ==="]
    if not turns:
        lines.extend(["(no archived transcript)", ""])
        return _line_chunks(lines)
    recent_turns = turns[-max(1, history_limit) :]
    if len(recent_turns) < len(turns):
        lines.append(f"(showing last {len(recent_turns)} of {len(turns)} archived turns)")
        lines.append("")
    for turn in recent_turns:
        lines.extend(_render_transcript_turn(turn))
    return _line_chunks(lines)


def _render_follow_stream_events(
    events: list[StreamEvent],
    cursor: ThreadFollowCursor,
    *,
    task_id: str,
) -> list[str]:
    chunks: list[str] = []
    if task_id != cursor.current_live_task_id:
        chunks.extend(_finalize_follow_output(cursor))
        chunks.append(f"--- live task {task_id} ---\n")
        cursor.current_live_task_id = task_id
    for event in events:
        if event.kind == "assistant.delta":
            delta = event.delta or event.text or ""
            if not delta:
                continue
            if not cursor.assistant_stream_open:
                chunks.append(f"{event.ts} | Assistant\n")
                cursor.assistant_stream_open = True
            chunks.append(delta)
            continue
        if event.kind == "assistant.completed":
            if cursor.assistant_stream_open:
                chunks.extend(_finalize_follow_output(cursor))
                continue
            text = event.text or ""
            if text:
                chunks.append(f"{event.ts} | Assistant\n{text}\n")
            continue
        chunks.extend(_finalize_follow_output(cursor))
        text = event.text or _payload_summary(event) or event.kind
        chunks.append(f"{event.ts} | {event.kind} | {text}\n")
    return chunks


def _finalize_follow_output(cursor: ThreadFollowCursor) -> list[str]:
    if not cursor.assistant_stream_open:
        return []
    cursor.assistant_stream_open = False
    return ["\n"]


def _write_follow_chunks(chunks: list[str]) -> None:
    if not chunks:
        return
    for chunk in chunks:
        sys.stdout.write(chunk)
    sys.stdout.flush()


def _line_chunks(lines: list[str]) -> list[str]:
    return [f"{line}\n" for line in lines]


def _format_session_line(session: SessionState, health: DerivedHealth) -> str:
    return " | ".join(
        [
            session.thread_id,
            f"session={session.session_id}",
            f"lifecycle={session.lifecycle}",
            f"backend={session.backend}",
            f"transport={session.backend_transport}",
            f"task={session.current_task_id}",
            f"health={health.status}",
            f"last_progress={_text_or_dash(health.last_progress_at)}",
            f"repo={session.repo_path}",
            f"workdir={session.workdir or '.'}",
            f"updated={session.updated_at}",
        ]
    )


def _format_idle_seconds(value: int | None) -> str:
    if value is None:
        return "-"
    minutes, seconds = divmod(value, 60)
    if minutes <= 0:
        return f"{seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours <= 0:
        return f"{minutes}m {seconds}s"
    return f"{hours}h {minutes}m {seconds}s"


def _load_transcript_turns(task_root: Path, thread_id: str) -> list[TranscriptTurn]:
    try:
        return build_thread_transcript(thread_id, task_root)
    except FileNotFoundError:
        return []


def _load_live_stream(stream_path_value: Path) -> tuple[list[StreamEvent], str | None]:
    try:
        return load_stream_events(stream_path_value), None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], str(exc)


def _render_transcript_turn(turn: TranscriptTurn) -> list[str]:
    label = "User" if turn.role == "user" else "Assistant"
    if turn.status:
        label = f"{label} [{turn.status}]"
    return [
        f"{turn.index:03d} | {_text_or_dash(turn.date)} | {label}",
        turn.content or "(empty)",
        "",
    ]


def _collect_live_assistant(events: list[StreamEvent]) -> tuple[str, str | None, str | None, bool]:
    chunks: list[str] = []
    started_at: str | None = None
    last_update_at: str | None = None
    completed = False
    for event in events:
        if event.kind == "assistant.delta" and event.delta:
            if started_at is None:
                started_at = event.ts
            last_update_at = event.ts
            chunks.append(event.delta)
        elif event.kind == "assistant.completed":
            completed = True
            last_update_at = event.ts
            if started_at is None:
                started_at = event.ts
            if not chunks and event.text:
                chunks.append(event.text)
    return "".join(chunks).strip(), started_at, last_update_at, completed


def _render_live_event_lines(events: list[StreamEvent]) -> list[str]:
    lines: list[str] = []
    for event in events:
        if event.kind == "assistant.delta":
            continue
        if event.kind == "assistant.completed":
            text = "assistant message completed"
        else:
            text = event.text or _payload_summary(event) or event.kind
        lines.append(f"{event.ts} | {event.kind} | {text}")
    return lines[-8:]


def _payload_summary(event: StreamEvent) -> str | None:
    payload = event.payload
    if not payload:
        return None
    if "command" in payload:
        command = str(payload.get("command") or "").strip()
        exit_code = payload.get("exit_code")
        if exit_code is None:
            return command or None
        return f"{command} (exit {exit_code})".strip()
    if "tool" in payload:
        server = str(payload.get("server") or "").strip()
        tool = str(payload.get("tool") or "").strip()
        if server and tool:
            return f"{server}.{tool}"
        return tool or server or None
    if "query" in payload:
        query = str(payload.get("query") or "").strip()
        return query or None
    if "message" in payload:
        message = str(payload.get("message") or "").strip()
        return message or None
    if "changes" in payload and isinstance(payload.get("changes"), list):
        change_count = len(payload["changes"])
        return f"{change_count} file change(s)"
    return None


def _pid_is_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code_process.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            close_handle(handle)
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal read-only observability for the mail runner runtime.")
    parser.add_argument("--config", help="Optional path to the runtime mail config.")
    parser.add_argument("--runtime-dir", help="Optional runtime directory. Defaults to .\\_tmp_live_mail_runner.")
    parser.add_argument("--task-root", help="Optional task root override.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show host state and aggregate runtime counts.")
    subparsers.add_parser("list-running", help="List sessions currently marked running.")
    subparsers.add_parser("list-queue", help="List queued sessions and queued follow-up work.")
    show_thread = subparsers.add_parser("show-thread", help="Show a thread summary from thread_state.json.")
    show_thread.add_argument("thread_id", help="Thread id, for example thread_048")
    show_thread_live = subparsers.add_parser("show-thread-live", help="Show a thread summary plus transcript and live stream view.")
    show_thread_live.add_argument("thread_id", help="Thread id, for example thread_048")
    follow_thread_live_parser = subparsers.add_parser(
        "follow-thread-live",
        help="Continuously append new transcript turns and live stream output for a thread.",
    )
    follow_thread_live_parser.add_argument("thread_id", help="Thread id, for example thread_048")
    follow_thread_live_parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.0,
        help="Polling interval for new transcript turns and stream events.",
    )
    follow_thread_live_parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="Optional poll iteration limit. 0 means run until interrupted or closed.",
    )
    follow_thread_live_parser.add_argument(
        "--history-limit",
        type=int,
        default=12,
        help="How many archived transcript turns to print on startup.",
    )
    follow_thread_live_parser.add_argument(
        "--exit-when-inactive",
        action="store_true",
        help="Close once the thread is no longer active/resumable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    context = build_context(config_path=args.config, runtime_dir=args.runtime_dir, task_root=args.task_root)

    if args.command == "status":
        print(render_status(context))
        return 0
    if args.command == "list-running":
        print(render_running_sessions(context))
        return 0
    if args.command == "list-queue":
        print(render_queue_entries(context))
        return 0
    if args.command == "show-thread":
        rendered = render_thread_details(context, args.thread_id)
        if rendered is None:
            print(f"Thread not found: {args.thread_id}")
            return 1
        print(rendered)
        return 0
    if args.command == "show-thread-live":
        rendered = render_thread_live(context, args.thread_id)
        if rendered is None:
            print(f"Thread not found: {args.thread_id}")
            return 1
        print(rendered)
        return 0
    if args.command == "follow-thread-live":
        try:
            return follow_thread_live(
                config_path=args.config,
                runtime_dir=args.runtime_dir,
                task_root=args.task_root,
                thread_id=args.thread_id,
                poll_seconds=max(0.0, float(args.poll_seconds)),
                iterations=max(0, int(args.iterations)),
                history_limit=max(1, int(args.history_limit)),
                exit_when_inactive=bool(args.exit_when_inactive),
            )
        except KeyboardInterrupt:
            print("")
            return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
