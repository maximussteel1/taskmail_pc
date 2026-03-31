from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import inspect
import json
from pathlib import Path

import mail_runner.pc_control_plane_client as pc_control_plane_client_module
from mail_runner.adapters.mock_adapter import SUMMARY_LINE
from mail_runner.artifact_resolver import write_artifact_index
from mail_runner.config import AppConfig
from mail_runner.file_surface import write_artifact_upload_success_binding
from mail_runner.models import QuestionAnswer, QuestionItem, RunArtifact, RunResult, TaskSnapshot, ThreadState
from mail_runner.pc_control_plane_client import PcControlPlaneClient, build_pc_control_plane_client
from mail_runner.question_utils import canonical_answer_context, canonical_answer_summary
from mail_runner.relay_server.app import build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_command_store import PcCommandRecord
from mail_runner.relay_server.pc_control_protocol import (
    PcCommandAckMessage,
    PcDeliveryAckMessage,
    PcCommandResultMessage,
    PcOutputChunkMessage,
    PcProjectionBatchMessage,
    build_command_dispatch,
    build_delivery_ack,
    build_output_chunk,
    build_output_resume_request,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import PersistentSessionStore
from mail_runner.stream_events import STREAM_EVENTS_FILENAME, StreamEvent, append_stream_event
from mail_runner.thread_store import build_workspace_id, build_workspace_norm, load_thread_state, save_raw_mail, save_thread_state
from mail_runner.workspace import WorkspaceManager


def test_build_pc_control_plane_client_is_disabled_in_mail_first_mode() -> None:
    client = build_pc_control_plane_client(
        AppConfig(
            control_plane_mode="mail_first",
            relay_url="ws://relay.example.com/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
        )
    )

    assert client is None


class _ImmediateSuccessRunner:
    def __init__(self, task_root) -> None:
        self.snapshots: list[TaskSnapshot] = []
        self.workspace = WorkspaceManager(task_root)
        self.task_root = task_root

    def active_count(self) -> int:
        return 0

    def queued_count(self) -> int:
        return 0

    def start_background_task(
        self,
        snapshot: TaskSnapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        session_name: str | None = None,
        on_accepted=None,
        on_running=None,
        on_finished=None,
    ) -> ThreadState:
        self.snapshots.append(snapshot)
        run_dir = self.workspace.create_run_dir(snapshot.thread_id, snapshot.task_id, exist_ok=True)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        summary_path = run_dir / "summary.md"
        stdout_path.write_text("Mock runner stdout\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        summary_path.write_text(SUMMARY_LINE + "\n", encoding="utf-8")
        stream_path = run_dir / STREAM_EVENTS_FILENAME
        stream_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-03-25T10:00:22",
                            "seq": 1,
                            "thread_id": snapshot.thread_id,
                            "task_id": snapshot.task_id,
                            "backend": snapshot.backend,
                            "backend_transport": snapshot.backend_transport,
                            "kind": "assistant.delta",
                            "delta": "Hello",
                            "status": "streaming",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-03-25T10:00:23",
                            "seq": 2,
                            "thread_id": snapshot.thread_id,
                            "task_id": snapshot.task_id,
                            "backend": snapshot.backend,
                            "backend_transport": snapshot.backend_transport,
                            "kind": "assistant.completed",
                            "text": "Hello world",
                            "status": "completed",
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        preview_path = artifacts_dir / "preview.png"
        report_path = artifacts_dir / "report.md"
        preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        report_path.write_text("# run report\n", encoding="utf-8")
        accepted_state = ThreadState(
            thread_id=snapshot.thread_id,
            root_message_id=root_message_id or "<root@local>",
            latest_message_id=latest_message_id or "<latest@local>",
            subject_norm=subject_norm or f"pc-control:{snapshot.thread_id}",
            backend=snapshot.backend,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file="runs/current_snapshot.json",
            status="accepted",
            profile=snapshot.profile,
            permission=snapshot.permission,
            workspace_id="workspace_001",
            workspace_norm="workspace_norm_001",
            session_id=snapshot.thread_id,
            session_name=session_name or snapshot.thread_id,
            session_norm=(session_name or snapshot.thread_id).lower(),
            backend_transport=snapshot.backend_transport,
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:21",
        )
        if on_accepted is not None:
            on_accepted(accepted_state)
        save_thread_state(accepted_state, self.task_root)

        running_state = ThreadState(
            thread_id=snapshot.thread_id,
            root_message_id=accepted_state.root_message_id,
            latest_message_id=accepted_state.latest_message_id,
            subject_norm=accepted_state.subject_norm,
            backend=snapshot.backend,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file=accepted_state.last_task_snapshot_file,
            status="running",
            profile=snapshot.profile,
            permission=snapshot.permission,
            workspace_id="workspace_001",
            workspace_norm="workspace_norm_001",
            session_id=snapshot.thread_id,
            session_name=session_name or snapshot.thread_id,
            session_norm=(session_name or snapshot.thread_id).lower(),
            backend_transport=snapshot.backend_transport,
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:22",
        )
        if on_running is not None:
            on_running(running_state)
        save_thread_state(running_state, self.task_root)

        final_state = ThreadState(
            thread_id=snapshot.thread_id,
            root_message_id=accepted_state.root_message_id,
            latest_message_id=accepted_state.latest_message_id,
            subject_norm=accepted_state.subject_norm,
            backend=snapshot.backend,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file=accepted_state.last_task_snapshot_file,
            status="done",
            profile=snapshot.profile,
            permission=snapshot.permission,
            last_summary=SUMMARY_LINE,
            workspace_id="workspace_001",
            workspace_norm="workspace_norm_001",
            session_id=snapshot.thread_id,
            session_name=session_name or snapshot.thread_id,
            session_norm=(session_name or snapshot.thread_id).lower(),
            backend_transport=snapshot.backend_transport,
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:23",
        )
        result = RunResult(
            task_id=snapshot.task_id,
            thread_id=snapshot.thread_id,
            backend=snapshot.backend,
            status="success",
            exit_code=0,
            started_at="2026-03-25T10:00:22",
            finished_at="2026-03-25T10:00:23",
            stdout_file=self.workspace.to_thread_relative(snapshot.thread_id, stdout_path),
            stderr_file=self.workspace.to_thread_relative(snapshot.thread_id, stderr_path),
            summary_file=self.workspace.to_thread_relative(snapshot.thread_id, summary_path),
            artifacts_dir=self.workspace.to_thread_relative(snapshot.thread_id, artifacts_dir),
            changed_files=[],
            tests_passed=True,
            backend_session_id="mock-session-codex-thread_cmd_001",
            backend_session_resumable=True,
            backend_transport=snapshot.backend_transport,
        )
        artifacts = [
            RunArtifact(
                artifact_id="artifact-preview",
                path=str(preview_path),
                name="preview.png",
                kind="image",
                content_type="image/png",
                source="manifest",
                inline_preview=True,
                caption="Preview",
            ),
            RunArtifact(
                artifact_id="artifact-report",
                path=str(report_path),
                name="report.md",
                kind="file",
                content_type="text/markdown",
                source="manifest",
            ),
        ]
        write_artifact_index(self.workspace.task_root, result, artifacts, [])
        write_artifact_upload_success_binding(
            self.workspace.task_root,
            result,
            artifacts[0],
            role="artifact_delivery",
            file_id="file_preview_001",
            metadata_url="/v1/files/file_preview_001",
            download_url="/v1/files/file_preview_001/content",
            uploaded_at="2026-03-25T10:00:24",
            trace_id="trace_cmd_001",
        )
        write_artifact_upload_success_binding(
            self.workspace.task_root,
            result,
            artifacts[0],
            role="artifact_delivery",
            file_id="file_preview_002",
            metadata_url="/v1/files/file_preview_002",
            download_url="/v1/files/file_preview_002/content",
            uploaded_at="2026-03-25T10:00:25",
            trace_id="trace_cmd_001",
        )
        self.workspace.save_run_result(snapshot.thread_id, snapshot.task_id, result)
        if on_finished is not None:
            on_finished(final_state, result)
        save_thread_state(final_state, self.task_root)
        return accepted_state


class _DeferredSuccessRunner:
    def __init__(self, task_root) -> None:
        self.snapshots: list[TaskSnapshot] = []
        self.workspace = WorkspaceManager(task_root)
        self.task_root = task_root
        self._pending_finish: dict[str, object] | None = None

    def active_count(self) -> int:
        return 1 if self._pending_finish is not None else 0

    def queued_count(self) -> int:
        return 0

    def start_background_task(
        self,
        snapshot: TaskSnapshot,
        *,
        root_message_id: str | None = None,
        latest_message_id: str | None = None,
        subject_norm: str | None = None,
        session_name: str | None = None,
        on_accepted=None,
        on_running=None,
        on_finished=None,
    ) -> ThreadState:
        self.snapshots.append(snapshot)
        accepted_state = ThreadState(
            thread_id=snapshot.thread_id,
            root_message_id=root_message_id or "<root@local>",
            latest_message_id=latest_message_id or "<latest@local>",
            subject_norm=subject_norm or f"pc-control:{snapshot.thread_id}",
            backend=snapshot.backend,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file="runs/current_snapshot.json",
            status="accepted",
            profile=snapshot.profile,
            permission=snapshot.permission,
            workspace_id="workspace_001",
            workspace_norm="workspace_norm_001",
            session_id=snapshot.thread_id,
            session_name=session_name or snapshot.thread_id,
            session_norm=(session_name or snapshot.thread_id).lower(),
            backend_transport=snapshot.backend_transport,
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:21",
        )
        if on_accepted is not None:
            on_accepted(accepted_state)
        save_thread_state(accepted_state, self.task_root)

        running_state = ThreadState(
            thread_id=snapshot.thread_id,
            root_message_id=accepted_state.root_message_id,
            latest_message_id=accepted_state.latest_message_id,
            subject_norm=accepted_state.subject_norm,
            backend=snapshot.backend,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file=accepted_state.last_task_snapshot_file,
            status="running",
            profile=snapshot.profile,
            permission=snapshot.permission,
            workspace_id="workspace_001",
            workspace_norm="workspace_norm_001",
            session_id=snapshot.thread_id,
            session_name=session_name or snapshot.thread_id,
            session_norm=(session_name or snapshot.thread_id).lower(),
            backend_transport=snapshot.backend_transport,
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:22",
            last_progress_at="2026-03-25T10:00:22",
        )
        if on_running is not None:
            on_running(running_state)
        save_thread_state(running_state, self.task_root)

        self._pending_finish = {
            "snapshot": snapshot,
            "accepted_state": accepted_state,
            "session_name": session_name or snapshot.thread_id,
            "on_finished": on_finished,
        }
        return accepted_state

    def finish(self) -> tuple[ThreadState, RunResult]:
        if self._pending_finish is None:
            raise AssertionError("No deferred run is pending")
        snapshot = self._pending_finish["snapshot"]
        accepted_state = self._pending_finish["accepted_state"]
        session_name = self._pending_finish["session_name"]
        on_finished = self._pending_finish["on_finished"]
        assert isinstance(snapshot, TaskSnapshot)
        assert isinstance(accepted_state, ThreadState)
        assert isinstance(session_name, str)

        run_dir = self.workspace.create_run_dir(snapshot.thread_id, snapshot.task_id, exist_ok=True)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        summary_path = run_dir / "summary.md"
        stdout_path.write_text("Mock runner stdout\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        summary_path.write_text(SUMMARY_LINE + "\n", encoding="utf-8")

        result = RunResult(
            task_id=snapshot.task_id,
            thread_id=snapshot.thread_id,
            backend=snapshot.backend,
            status="success",
            exit_code=0,
            started_at="2026-03-25T10:00:22",
            finished_at="2026-03-25T10:00:23",
            stdout_file=self.workspace.to_thread_relative(snapshot.thread_id, stdout_path),
            stderr_file=self.workspace.to_thread_relative(snapshot.thread_id, stderr_path),
            summary_file=self.workspace.to_thread_relative(snapshot.thread_id, summary_path),
            artifacts_dir=None,
            changed_files=[],
            tests_passed=True,
            backend_session_id="mock-session-codex-thread_cmd_001",
            backend_session_resumable=True,
            backend_transport=snapshot.backend_transport,
        )
        self.workspace.save_run_result(snapshot.thread_id, snapshot.task_id, result)

        final_state = ThreadState(
            thread_id=snapshot.thread_id,
            root_message_id=accepted_state.root_message_id,
            latest_message_id=accepted_state.latest_message_id,
            subject_norm=accepted_state.subject_norm,
            backend=snapshot.backend,
            repo_path=snapshot.repo_path,
            workdir=snapshot.workdir,
            current_task_id=snapshot.task_id,
            last_task_snapshot_file=accepted_state.last_task_snapshot_file,
            status="done",
            profile=snapshot.profile,
            permission=snapshot.permission,
            last_summary=SUMMARY_LINE,
            workspace_id="workspace_001",
            workspace_norm="workspace_norm_001",
            session_id=snapshot.thread_id,
            session_name=session_name,
            session_norm=session_name.lower(),
            backend_transport=snapshot.backend_transport,
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:23",
            last_progress_at="2026-03-25T10:00:23",
        )
        if on_finished is not None:
            on_finished(final_state, result)
        save_thread_state(final_state, self.task_root)
        self._pending_finish = None
        return final_state, result


class _RecordingWebSocket:
    def __init__(self, *, on_send=None) -> None:
        self.sent_frames: list[dict[str, object]] = []
        self._on_send = on_send

    async def send(self, payload: str) -> None:
        frame = json.loads(payload)
        self.sent_frames.append(frame)
        if self._on_send is not None:
            maybe_awaitable = self._on_send(frame)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable


def _auto_ack_reliable_payload(client: PcControlPlaneClient, frame: dict[str, object], *, connection_epoch: int) -> None:
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return
    message_type = str(frame.get("type") or "").strip()
    if message_type == "projection_batch":
        ack = parse_pc_control_server_message(
            build_delivery_ack(
                message_id="msg_delivery_ack_projection",
                trace_id=str(frame.get("trace_id") or "trace_delivery_ack_projection"),
                pc_id=str(frame.get("pc_id") or client._pc_id),
                connection_epoch=int(frame.get("connection_epoch") or connection_epoch),
                sent_at="2026-03-25T10:00:40",
                request_id=str(payload.get("batch_id") or ""),
                message_type="projection_batch",
                delivery_status="committed",
            )
        )
        assert isinstance(ack, PcDeliveryAckMessage)
        assert client._resolve_delivery_ack(ack) is True
        return
    if message_type == "result":
        ack = parse_pc_control_server_message(
            build_delivery_ack(
                message_id="msg_delivery_ack_result",
                trace_id=str(frame.get("trace_id") or "trace_delivery_ack_result"),
                pc_id=str(frame.get("pc_id") or client._pc_id),
                connection_epoch=int(frame.get("connection_epoch") or connection_epoch),
                sent_at="2026-03-25T10:00:41",
                request_id=str(payload.get("result_id") or ""),
                message_type="result",
                delivery_status="committed",
            )
        )
        assert isinstance(ack, PcDeliveryAckMessage)
        assert client._resolve_delivery_ack(ack) is True


class _ImmediateCancelledFuture:
    def add_done_callback(self, callback) -> None:
        callback(self)

    def result(self) -> None:
        raise concurrent.futures.CancelledError()


class _FakeMailClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_mail(self, **kwargs):
        self.sent_messages.append(kwargs)
        return f"<sent-{len(self.sent_messages)}@example.com>"


def _workspace_item(*, repo_path: str, workdir: str | None) -> dict[str, object]:
    return {
        "workspace_id": build_workspace_id(repo_path, workdir),
        "workspace_norm": build_workspace_norm(repo_path, workdir),
        "repo_path": repo_path,
        "workdir": workdir,
        "display_name": "android_task_manager",
        "source": "project_sync_roots",
        "capabilities": {
            "streaming": True,
            "artifact_manifest": True,
            "workspace_snapshot": True,
            "supported_backends": ["codex"],
            "profile_catalogs": {"codex": ["default", "strong"]},
            "permission_modes": ["default", "highest"],
            "backend_transport_modes": {"codex": ["cli", "sdk"]},
        },
    }


def _build_existing_thread_state(
    task_root,
    *,
    status: str = "done",
    last_summary: str = "Completed.",
    paused_from_status: str | None = None,
    pending_questions: list[QuestionItem] | None = None,
    collected_answers: list[QuestionAnswer] | None = None,
    backend_session_id: str | None = None,
    backend_session_resumable: bool = False,
    lifecycle: str = "active",
    canonical_reply_recipient: str | None = "user@example.com",
    save_inbound_mail: bool = False,
    repo_path: str = "E:\\projects\\android_task_manager",
    workdir: str | None = "feature/taskmail/internal",
) -> ThreadState:
    workspace = WorkspaceManager(task_root)
    snapshot = TaskSnapshot(
        task_id="task_001",
        thread_id="thread_001",
        backend="codex",
        profile="strong",
        permission="highest",
        repo_path=repo_path,
        workdir=workdir,
        task_text="Phase 3 detail bridge",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-25T10:00:20",
        updated_at="2026-03-25T10:00:20",
        run_mode="new",
        backend_session_id=None,
        turn_text=None,
        backend_transport="sdk",
    )
    snapshot_path = workspace.save_snapshot(snapshot)
    state = ThreadState(
        thread_id="thread_001",
        root_message_id="<root@example.com>",
        latest_message_id="<latest@example.com>",
        subject_norm="phase 3 detail bridge",
        backend="codex",
        repo_path=repo_path,
        workdir=workdir,
        current_task_id=snapshot.task_id,
        last_task_snapshot_file=workspace.to_thread_relative(snapshot.thread_id, snapshot_path),
        status=status,
        profile="strong",
        permission="highest",
        last_summary=last_summary,
        lifecycle=lifecycle,
        last_active_at="2026-03-25T10:00:20",
        last_progress_at="2026-03-25T10:00:20",
        workspace_id=build_workspace_id(repo_path, workdir),
        workspace_norm=build_workspace_norm(repo_path, workdir),
        session_id="session_001",
        session_name="Phase 3 detail bridge",
        session_norm="phase 3 detail bridge",
        canonical_reply_recipient=canonical_reply_recipient,
        collected_answers=list(collected_answers or []),
        backend_session_id=backend_session_id,
        backend_session_resumable=backend_session_resumable,
        backend_transport="sdk",
        pending_questions=list(pending_questions or []),
        paused_from_status=paused_from_status,
        created_at="2026-03-25T10:00:20",
        updated_at="2026-03-25T10:00:20",
    )
    save_thread_state(state, task_root)
    if save_inbound_mail:
        save_raw_mail(
            state.thread_id,
            {
                "message_id": "<user-inbound@example.com>",
                "subject": "Re: [S:session_001] Phase 3 detail bridge",
                "from_addr": "user@example.com",
                "to_addr": "bot@example.com",
                "date": "2026-03-25T10:00:19",
                "body_text": "Please continue with the cleanup.",
                "raw_headers": {},
            },
            task_root,
        )
    return state


async def _dispatch_messages(client: PcControlPlaneClient, command, *, connection_epoch: int = 1):
    websocket = _RecordingWebSocket(
        on_send=lambda frame: _auto_ack_reliable_payload(client, frame, connection_epoch=connection_epoch)
    )
    send_lock = asyncio.Lock()
    await client._handle_command_dispatch(
        websocket,
        message=command,
        connection_epoch=connection_epoch,
        send_lock=send_lock,
    )
    await client._flush_pending_client_messages(websocket, send_lock)
    return [parse_pc_control_client_message(frame) for frame in websocket.sent_frames], websocket


def _ack_result_and_projection_batches(messages):
    ack = next(message for message in messages if isinstance(message, PcCommandAckMessage))
    result = next(message for message in messages if isinstance(message, PcCommandResultMessage))
    projection_batches = [message for message in messages if isinstance(message, PcProjectionBatchMessage)]
    return ack, result, projection_batches


def test_pc_control_plane_client_projection_round_prefers_turn_text_and_stdout_file(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(task_root, status="running", last_summary="Short summary only.")
    runner = _ImmediateSuccessRunner(task_root)
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=runner,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:00:30",
    )
    snapshot = TaskSnapshot(
        task_id=thread_state.current_task_id,
        thread_id=thread_state.thread_id,
        backend=thread_state.backend,
        profile=thread_state.profile,
        permission=thread_state.permission,
        repo_path=thread_state.repo_path,
        workdir=thread_state.workdir,
        task_text="Continue the previous task.",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-25T10:00:20",
        updated_at="2026-03-25T10:00:21",
        run_mode="resume",
        backend_session_id=thread_state.backend_session_id,
        turn_text="Please continue with the cleanup.",
        backend_transport="sdk",
    )
    summary_path = runner.workspace.run_file_path(thread_state.thread_id, thread_state.current_task_id, "summary.md")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("# Detailed result\nImplemented the requested cleanup.\n", encoding="utf-8")
    stdout_path = runner.workspace.run_file_path(thread_state.thread_id, thread_state.current_task_id, "stdout.log")
    stdout_path.write_text("Implemented the requested cleanup.\n\nKept the original spacing.", encoding="utf-8")
    result = RunResult(
        task_id=thread_state.current_task_id,
        thread_id=thread_state.thread_id,
        backend=thread_state.backend,
        status="success",
        exit_code=0,
        started_at="2026-03-25T10:00:22",
        finished_at="2026-03-25T10:00:23",
        stdout_file=runner.workspace.to_thread_relative(thread_state.thread_id, stdout_path),
        stderr_file="runs/task_001/stderr.log",
        summary_file=runner.workspace.to_thread_relative(thread_state.thread_id, summary_path),
        artifacts_dir=None,
        changed_files=[],
        tests_passed=True,
        backend_transport="sdk",
    )

    round_payload = client._build_projection_round(thread_state, snapshot, result)

    assert round_payload is not None
    assert round_payload["input_text"] == "Please continue with the cleanup."
    assert round_payload["result_text"] == "Implemented the requested cleanup.\n\nKept the original spacing."
    assert round_payload["process_items"] == []


def test_pc_control_plane_client_projection_version_continues_monotonically_after_restart(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(task_root, status="running", last_summary="First projection state.")
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    snapshot1 = TaskSnapshot(
        task_id=thread_state.current_task_id,
        thread_id=thread_state.thread_id,
        backend=thread_state.backend,
        profile=thread_state.profile,
        permission=thread_state.permission,
        repo_path=thread_state.repo_path,
        workdir=thread_state.workdir,
        task_text="Continue the previous task.",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-25T10:00:20",
        updated_at="2026-03-25T10:00:21",
        run_mode="resume",
        backend_session_id=thread_state.backend_session_id,
        turn_text="First follow-up.",
        backend_transport="sdk",
    )
    client1 = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:00:30",
    )
    batch1 = client1._build_session_projection_batch(
        state=thread_state,
        task_snapshot=snapshot1,
        result=None,
        closeouts=[],
        closeout_only=False,
    )

    thread_state.current_task_id = "task_002"
    thread_state.updated_at = "2026-03-25T10:05:00"
    thread_state.last_progress_at = thread_state.updated_at
    thread_state.last_summary = "Second projection state."
    snapshot2 = TaskSnapshot(
        task_id=thread_state.current_task_id,
        thread_id=thread_state.thread_id,
        backend=thread_state.backend,
        profile=thread_state.profile,
        permission=thread_state.permission,
        repo_path=thread_state.repo_path,
        workdir=thread_state.workdir,
        task_text="Continue the previous task with updated context.",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-25T10:05:00",
        updated_at="2026-03-25T10:05:00",
        run_mode="resume",
        backend_session_id=thread_state.backend_session_id,
        turn_text="Second follow-up.",
        backend_transport="sdk",
    )
    client2 = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:05:10",
    )
    batch2 = client2._build_session_projection_batch(
        state=thread_state,
        task_snapshot=snapshot2,
        result=None,
        closeouts=[],
        closeout_only=False,
    )

    assert batch1 is not None
    assert batch2 is not None
    assert int(batch1["projection_version"]) > 1
    assert int(batch2["projection_version"]) > int(batch1["projection_version"])


def test_pc_control_plane_client_strict_lease_blocks_mailbox_without_active_lease(tmp_path) -> None:
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(
            relay_mailbox_lease_mode="strict",
            relay_mailbox_lease_ttl_seconds=45,
            imap_host="imap.example.com",
            imap_user="bot@example.com",
        ),
        runner=_ImmediateSuccessRunner(tmp_path / "task_root"),
    )

    assert client.can_consume_mailbox() is False

    client._update_lease_state_from_ack(
        {
            "lease_status": "active",
            "lease_epoch": 2,
            "lease_holder_id": client.mailbox_lease_state()["runner_id"],
            "lease_pc_id": "pc_home",
            "expires_at": "2026-03-25T10:01:13",
            "reason": None,
            "degraded_mode": False,
        }
    )

    assert client.can_consume_mailbox() is True
    client._set_connection_state(connected=False)
    assert client.can_consume_mailbox() is False


def test_pc_control_plane_client_degraded_mode_falls_back_locally_when_disconnected(tmp_path) -> None:
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(
            relay_mailbox_lease_mode="degraded",
            relay_mailbox_lease_ttl_seconds=45,
            imap_host="imap.example.com",
            imap_user="bot@example.com",
        ),
        runner=_ImmediateSuccessRunner(tmp_path / "task_root"),
    )

    decision = client.register_ingress_candidate(
        envelope=type(
            "Envelope",
            (),
            {
                "message_id": "<ingress@example.com>",
                "subject": "[OC] Demo",
                "from_addr": "user@example.com",
                "date": "2026-03-25T10:00:00",
                "in_reply_to": None,
                "references": [],
                "imap_uid": 101,
                "imap_uid_validity": 777,
            },
        )(),
        classification="new_task",
        subject_norm="demo",
        candidate_status="ready",
    )

    assert client.can_consume_mailbox() is True
    assert decision["decision"] == "accepted"
    assert decision["degraded_mode"] is True


def test_pc_control_plane_client_status_command_returns_runtime_authoritative_result(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(task_root)
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:05:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_status_001",
            trace_id="trace_cmd_status_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:05:00",
            command_id="cmd_status_001",
            command_type="status",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "status": {},
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    session_action_result = structured_payload["session_action_result"]
    assert session_action_result["action_type"] == "status"
    assert session_action_result["result_scope"] == "runtime_execution"
    assert session_action_result["canonical_outcome_via"] == "relay_runtime"
    assert session_action_result["execution_status"] == "completed"
    assert session_action_result["state_changed"] is False
    assert session_action_result["run_result"] is None
    closeout = session_action_result["session_action_closeout"]
    assert closeout["request_id"] == "cmd_status_001"
    assert closeout["packet_id"] == "pc-control:session-action:cmd_status_001"
    assert closeout["target_session_identity"] == {
        "workspace_id": thread_state.workspace_id,
        "session_id": thread_state.session_id,
        "thread_id": thread_state.thread_id,
    }
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_reply_command_emits_session_action_result(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(
        task_root,
        backend_session_id="sdk-session-reply-001",
        backend_session_resumable=True,
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:06:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_reply_001",
            trace_id="trace_cmd_reply_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:06:00",
            command_id="cmd_reply_001",
            command_type="reply",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    session_action_result = structured_payload["session_action_result"]
    assert session_action_result["action_type"] == "reply"
    assert session_action_result["result_scope"] == "runtime_execution"
    assert session_action_result["canonical_outcome_via"] == "relay_runtime"
    assert session_action_result["execution_status"] == "completed"
    assert session_action_result["run_result"]["run_status"] == "success"
    closeout = session_action_result["session_action_closeout"]
    assert closeout["request_id"] == "cmd_reply_001"
    assert closeout["terminal_mail_subject"] is None
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_reply_command_applies_permission_override_from_reply_text(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(
        task_root,
        backend_session_id="sdk-session-reply-permission-001",
        backend_session_resumable=True,
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    runner = _ImmediateSuccessRunner(task_root)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=runner,
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:06:05",
    )
    reply_text = "Permission: default\n\nReply with exactly one line and nothing else:\nPERM_RESET_TO_DEFAULT"
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_reply_permission_001",
            trace_id="trace_cmd_reply_permission_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:06:05",
            command_id="cmd_reply_permission_001",
            command_type="reply",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": reply_text,
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    latest_snapshot = runner.snapshots[-1]

    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    assert latest_snapshot.permission == "default"
    assert latest_snapshot.turn_text == reply_text
    assert updated_state.permission == "default"
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_reply_command_publishes_finished_projection_after_deferred_completion(tmp_path) -> None:
    async def _run() -> None:
        task_root = tmp_path / "task_root"
        thread_state = _build_existing_thread_state(
            task_root,
            backend_session_id="sdk-session-reply-deferred-001",
            backend_session_resumable=True,
        )
        fake_mail_client = _FakeMailClient()
        workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
        runner = _DeferredSuccessRunner(task_root)
        client = PcControlPlaneClient(
            relay_url="ws://127.0.0.1:8787/relay",
            transport_token="relay-secret",
            pc_id="pc_home",
            client_version="0.1.0",
            display_name="pc_home",
            config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
            runner=runner,
            mail_client=fake_mail_client,
            workspace_provider=lambda: [workspace_item],
            clock=lambda: "2026-03-25T10:06:10",
        )
        command = parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_cmd_reply_deferred_001",
                trace_id="trace_cmd_reply_deferred_001",
                pc_id="pc_home",
                connection_epoch=1,
                sent_at="2026-03-25T10:06:10",
                command_id="cmd_reply_deferred_001",
                command_type="reply",
                workspace_id=str(workspace_item["workspace_id"]),
                session_id=thread_state.session_id,
                execution_policy={},
                command_payload={
                    "target": {
                        "scope": "current_session",
                        "workspace_id": thread_state.workspace_id,
                        "session_id": thread_state.session_id,
                        "thread_id": thread_state.thread_id,
                    },
                    "reply": {
                        "reply_text": "Please continue with the cleanup.",
                    },
                },
            )
        )

        websocket = _RecordingWebSocket(
            on_send=lambda frame: _auto_ack_reliable_payload(client, frame, connection_epoch=1)
        )
        send_lock = asyncio.Lock()
        await client._handle_command_dispatch(
            websocket,
            message=command,
            connection_epoch=1,
            send_lock=send_lock,
        )
        await client._flush_pending_client_messages(websocket, send_lock)
        initial_messages = [parse_pc_control_client_message(frame) for frame in websocket.sent_frames]
        ack = next(message for message in initial_messages if isinstance(message, PcCommandAckMessage))
        initial_results = [message for message in initial_messages if isinstance(message, PcCommandResultMessage)]
        initial_projection_batches = [
            message for message in initial_messages if isinstance(message, PcProjectionBatchMessage)
        ]
        assert ack.payload["ack_status"] == "accepted"
        assert initial_results == []
        assert initial_projection_batches

        initial_frame_count = len(websocket.sent_frames)
        runner.finish()
        await client._flush_pending_client_messages(websocket, send_lock)

        late_messages = [parse_pc_control_client_message(frame) for frame in websocket.sent_frames[initial_frame_count:]]
        late_results = [message for message in late_messages if isinstance(message, PcCommandResultMessage)]
        late_projection_batches = [message for message in late_messages if isinstance(message, PcProjectionBatchMessage)]
        assert len(late_results) == 1
        assert late_results[0].payload["structured_payload"]["session_action_result"]["execution_status"] == "completed"
        assert late_projection_batches

        session_projection_items = [
            item
            for message in late_projection_batches
            for item in message.payload["items"]
            if item.get("type") == "session_projection_upsert"
        ]
        assert any(
            item.get("snapshot_status") == "done"
            and item.get("list_status") == "done"
            and int(item.get("projection_version") or 0) >= 2
            for item in session_projection_items
        )
        round_items = [
            item
            for message in late_projection_batches
            for item in message.payload["items"]
            if item.get("type") == "session_round_upsert"
        ]
        assert any(item.get("status") == "done" for item in round_items)

    asyncio.run(_run())


def test_pc_control_plane_client_streams_output_chunks_before_deferred_reply_completion(tmp_path) -> None:
    async def _run() -> None:
        task_root = tmp_path / "task_root"
        thread_state = _build_existing_thread_state(
            task_root,
            backend_session_id="sdk-session-reply-live-stream-001",
            backend_session_resumable=True,
        )
        fake_mail_client = _FakeMailClient()
        workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
        runner = _DeferredSuccessRunner(task_root)
        client = PcControlPlaneClient(
            relay_url="ws://127.0.0.1:8787/relay",
            transport_token="relay-secret",
            pc_id="pc_home",
            client_version="0.1.0",
            display_name="pc_home",
            config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
            runner=runner,
            mail_client=fake_mail_client,
            workspace_provider=lambda: [workspace_item],
            clock=lambda: "2026-03-30T20:10:00",
        )
        client._output_chunk_poll_interval_seconds = 0.05
        command = parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_cmd_reply_live_stream_001",
                trace_id="trace_cmd_reply_live_stream_001",
                pc_id="pc_home",
                connection_epoch=1,
                sent_at="2026-03-30T20:10:00",
                command_id="cmd_reply_live_stream_001",
                command_type="reply",
                workspace_id=str(workspace_item["workspace_id"]),
                session_id=thread_state.session_id,
                execution_policy={},
                command_payload={
                    "target": {
                        "scope": "current_session",
                        "workspace_id": thread_state.workspace_id,
                        "session_id": thread_state.session_id,
                        "thread_id": thread_state.thread_id,
                    },
                    "reply": {
                        "reply_text": "Please continue with the cleanup.",
                    },
                },
            )
        )

        websocket = _RecordingWebSocket(
            on_send=lambda frame: _auto_ack_reliable_payload(client, frame, connection_epoch=1)
        )
        send_lock = asyncio.Lock()
        client._loop = asyncio.get_running_loop()
        client._websocket = websocket
        client._send_lock = send_lock
        client._current_connection_epoch = 1

        await client._handle_command_dispatch(
            websocket,
            message=command,
            connection_epoch=1,
            send_lock=send_lock,
        )
        await client._flush_pending_client_messages(websocket, send_lock)

        snapshot = runner.snapshots[-1]
        stream_path = runner.workspace.run_file_path(snapshot.thread_id, snapshot.task_id, STREAM_EVENTS_FILENAME)
        append_stream_event(
            stream_path,
            StreamEvent(
                ts="2026-03-30T20:10:01",
                seq=1,
                thread_id=snapshot.thread_id,
                task_id=snapshot.task_id,
                backend=snapshot.backend,
                backend_transport=snapshot.backend_transport,
                kind="assistant.delta",
                delta="Hello",
                status="streaming",
            ),
        )
        await _wait_until(
            lambda: any(
                frame.get("type") == "output_chunk" and dict(frame.get("payload") or {}).get("seq") == 1
                for frame in websocket.sent_frames
            ),
            timeout_seconds=2,
        )
        assert not any(frame.get("type") == "result" for frame in websocket.sent_frames)

        append_stream_event(
            stream_path,
            StreamEvent(
                ts="2026-03-30T20:10:02",
                seq=2,
                thread_id=snapshot.thread_id,
                task_id=snapshot.task_id,
                backend=snapshot.backend,
                backend_transport=snapshot.backend_transport,
                kind="assistant.completed",
                text="Hello world",
                status="completed",
            ),
        )
        await _wait_until(
            lambda: sum(1 for frame in websocket.sent_frames if frame.get("type") == "output_chunk") == 2,
            timeout_seconds=2,
        )

        runner.finish()
        await client._flush_pending_client_messages(websocket, send_lock)
        await _wait_until(
            lambda: any(frame.get("type") == "result" for frame in websocket.sent_frames),
            timeout_seconds=2,
        )

        output_frames = [frame for frame in websocket.sent_frames if frame.get("type") == "output_chunk"]
        assert [(frame["payload"]["seq"], frame["payload"].get("delta"), frame["payload"].get("text")) for frame in output_frames] == [
            (1, "Hello", None),
            (2, None, "Hello world"),
        ]
        first_output_index = next(index for index, frame in enumerate(websocket.sent_frames) if frame.get("type") == "output_chunk")
        result_index = next(index for index, frame in enumerate(websocket.sent_frames) if frame.get("type") == "result")
        assert first_output_index < result_index

    asyncio.run(_run())


def test_pc_control_plane_client_reliable_sender_waits_for_delivery_ack_before_next_payload() -> None:
    async def _run() -> None:
        client = PcControlPlaneClient(
            relay_url="ws://127.0.0.1:8787/relay",
            transport_token="relay-secret",
            pc_id="pc_home",
            client_version="0.1.0",
            display_name="pc_home",
            config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
            clock=lambda: "2026-03-30T16:25:20",
        )
        websocket = _RecordingWebSocket()
        send_lock = asyncio.Lock()
        client._loop = asyncio.get_running_loop()
        client._websocket = websocket
        client._send_lock = send_lock
        client._current_connection_epoch = 7

        projection_payload = {
            "schema_version": "v1",
            "type": "projection_batch",
            "message_id": "msg_projection_001",
            "trace_id": "trace_projection_001",
            "pc_id": "pc_home",
            "connection_epoch": 7,
            "sent_at": "2026-03-30T16:25:20",
            "payload": {
                "batch_id": "projection_batch:001",
            },
        }
        result_payload = {
            "schema_version": "v1",
            "type": "result",
            "message_id": "msg_result_001",
            "trace_id": "trace_result_001",
            "pc_id": "pc_home",
            "connection_epoch": 7,
            "sent_at": "2026-03-30T16:25:20",
            "payload": {
                "result_id": "result:cmd_001",
            },
        }

        client._queue_client_payload(projection_payload)
        client._queue_client_payload(result_payload)
        await asyncio.sleep(0)

        assert [frame["type"] for frame in websocket.sent_frames] == ["projection_batch"]

        first_ack = parse_pc_control_server_message(
            build_delivery_ack(
                message_id="msg_delivery_ack_projection_001",
                trace_id="trace_projection_001",
                pc_id="pc_home",
                connection_epoch=7,
                sent_at="2026-03-30T16:25:21",
                request_id="projection_batch:001",
                message_type="projection_batch",
                delivery_status="committed",
            )
        )
        assert client._resolve_delivery_ack(first_ack) is True
        await _wait_until(lambda: len(websocket.sent_frames) == 2, timeout_seconds=1)

        assert [frame["type"] for frame in websocket.sent_frames] == ["projection_batch", "result"]

        second_ack = parse_pc_control_server_message(
            build_delivery_ack(
                message_id="msg_delivery_ack_result_001",
                trace_id="trace_result_001",
                pc_id="pc_home",
                connection_epoch=7,
                sent_at="2026-03-30T16:25:22",
                request_id="result:cmd_001",
                message_type="result",
                delivery_status="committed",
            )
        )
        assert client._resolve_delivery_ack(second_ack) is True
        await _wait_until(lambda: client._pending_reliable_client_messages == [], timeout_seconds=1)

    asyncio.run(_run())


def test_pc_control_plane_client_finish_output_flush_only_sends_missing_tail(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    runner = _ImmediateSuccessRunner(task_root)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=runner,
        clock=lambda: "2026-03-30T20:12:00",
    )
    snapshot = TaskSnapshot(
        task_id="task_cmd_tail_001",
        thread_id="thread_cmd_tail_001",
        backend="codex",
        profile="strong",
        permission="highest",
        repo_path=str(tmp_path / "repo"),
        workdir=None,
        task_text="Flush only the missing output tail",
        acceptance=[],
        timeout_minutes=30,
        mode="modify",
        attachments=[],
        created_at="2026-03-30T20:12:00",
        updated_at="2026-03-30T20:12:00",
        run_mode="new",
        backend_session_id=None,
        turn_text=None,
        backend_transport="sdk",
    )
    runner.start_background_task(snapshot)
    result = runner.workspace.load_run_result(snapshot.thread_id, f"runs/{snapshot.task_id}/result.json")
    client._command_contexts["cmd_tail_001"] = {
        "trace_id": "trace_cmd_tail_001",
        "connection_epoch": 1,
        "execution_policy": {},
        "snapshot": snapshot,
    }
    client._remember_output_chunk_replay_context(
        "cmd_tail_001",
        trace_id="trace_cmd_tail_001",
        thread_id=snapshot.thread_id,
        task_id=snapshot.task_id,
    )
    client._remember_output_chunk_sent_seq("cmd_tail_001", 1)

    client._emit_output_chunks("cmd_tail_001", result=result)

    output_payloads = [payload for payload in client._pending_client_messages if payload.get("type") == "output_chunk"]
    assert len(output_payloads) == 1
    assert output_payloads[0]["payload"]["seq"] == 2
    assert output_payloads[0]["payload"]["text"] == "Hello world"
    assert client._output_chunk_replay_contexts["cmd_tail_001"]["last_sent_seq"] == 2


def test_pc_control_plane_client_requeues_payload_when_scheduled_send_future_is_cancelled(monkeypatch) -> None:
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        clock=lambda: "2026-03-30T11:46:45",
    )
    payload = {
        "type": "projection_batch",
        "pc_id": "pc_home",
        "connection_epoch": 12,
        "sent_at": "2026-03-30T11:46:45",
    }

    def _fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        return _ImmediateCancelledFuture()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _fake_run_coroutine_threadsafe)

    client._schedule_payload_send(payload, websocket=object(), send_lock=object(), loop=object())

    assert client._pending_client_messages == [payload]


def test_pc_control_plane_client_connect_once_uses_32mb_websocket_limit(monkeypatch) -> None:
    class _FakeWebSocketContext:
        def __init__(self, websocket) -> None:
            self._websocket = websocket

        async def __aenter__(self):
            return self._websocket

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeWebSocket:
        pass

    async def _run() -> None:
        captured: dict[str, object] = {}
        client = PcControlPlaneClient(
            relay_url="ws://127.0.0.1:8787/relay",
            transport_token="relay-secret",
            pc_id="pc_home",
            client_version="0.1.0",
            display_name="pc_home",
            config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        )
        client._stop_event.set()

        def _fake_connect(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = dict(kwargs)
            return _FakeWebSocketContext(_FakeWebSocket())

        async def _fake_perform_hello(websocket, send_lock):
            return 1

        async def _fake_send_workspace_snapshot(websocket, connection_epoch, send_lock):
            return None

        async def _fake_flush_pending_client_messages(websocket, send_lock):
            return None

        async def _fake_replay_output_chunks_after_reconnect(websocket, send_lock):
            return None

        async def _fake_receive_loop(websocket, *, connection_epoch, send_lock):
            await asyncio.Future()

        monkeypatch.setattr(pc_control_plane_client_module.websockets, "connect", _fake_connect)
        monkeypatch.setattr(client, "_perform_hello", _fake_perform_hello)
        monkeypatch.setattr(client, "_send_workspace_snapshot", _fake_send_workspace_snapshot)
        monkeypatch.setattr(client, "_flush_pending_client_messages", _fake_flush_pending_client_messages)
        monkeypatch.setattr(client, "_replay_output_chunks_after_reconnect", _fake_replay_output_chunks_after_reconnect)
        monkeypatch.setattr(client, "_receive_loop", _fake_receive_loop)

        await client._connect_once()

        assert captured["url"] == client._pc_control_url
        assert captured["kwargs"]["max_size"] == pc_control_plane_client_module._PC_CONTROL_WEBSOCKET_MAX_SIZE_BYTES

    asyncio.run(_run())


def test_pc_control_plane_client_reply_command_does_not_require_legacy_recipient_recovery(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(
        task_root,
        canonical_reply_recipient=None,
        save_inbound_mail=True,
        backend_session_id="sdk-session-reply-legacy-001",
        backend_session_resumable=True,
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:06:30",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_reply_legacy_001",
            trace_id="trace_cmd_reply_legacy_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:06:30",
            command_id="cmd_reply_legacy_001",
            command_type="reply",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert projection_batches
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.canonical_reply_recipient is None
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_reply_command_accepts_missing_recipient_binding(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(
        task_root,
        canonical_reply_recipient=None,
        save_inbound_mail=False,
        backend_session_id="sdk-session-reply-missing-binding-001",
        backend_session_resumable=True,
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:06:45",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_reply_missing_recipient_001",
            trace_id="trace_cmd_reply_missing_recipient_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:06:45",
            command_id="cmd_reply_missing_recipient_001",
            command_type="reply",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert projection_batches
    assert result.payload["structured_payload"]["session_action_result"]["canonical_outcome_via"] == "relay_runtime"
    assert result.payload["structured_payload"]["session_action_result"]["execution_status"] == "completed"
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_rejects_reply_when_target_session_is_paused(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(task_root)
    thread_state.status = "paused"
    thread_state.updated_at = "2026-03-25T10:07:00"
    thread_state.last_progress_at = thread_state.updated_at
    save_thread_state(thread_state, task_root)
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:07:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_reply_002",
            trace_id="trace_cmd_reply_002",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:07:00",
            command_id="cmd_reply_002",
            command_type="reply",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "reply": {
                    "reply_text": "Please continue with the cleanup.",
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    assert len(messages) == 1
    assert isinstance(messages[0], PcCommandAckMessage)
    assert messages[0].payload["ack_status"] == "rejected"
    assert messages[0].payload["error_code"] == "validation_failed"
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_pause_command_updates_state_without_mail_bridge(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(task_root, status="done")
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:08:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_pause_001",
            trace_id="trace_cmd_pause_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:08:00",
            command_id="cmd_pause_001",
            command_type="pause",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "pause": {},
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    assert structured_payload["session_action_result"]["action_type"] == "pause"
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.status == "paused"
    assert updated_state.paused_from_status == "done"
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_resume_command_restores_question_state_from_paused_session(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(
        task_root,
        status="paused",
        last_summary="Waiting for answers.",
        paused_from_status="awaiting_user_input",
        pending_questions=[
            QuestionItem(
                question_set_id="qs_branch",
                question_id="q_branch",
                question_type="single_choice",
                question_text="Select the release branch.",
                choices=["main", "dev"],
            )
        ],
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:09:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_resume_001",
            trace_id="trace_cmd_resume_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:09:00",
            command_id="cmd_resume_001",
            command_type="resume",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "resume": {},
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    assert structured_payload["session_action_result"]["action_type"] == "resume"
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.status == "awaiting_user_input"
    assert updated_state.paused_from_status is None
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_kill_command_updates_waiting_session(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(
        task_root,
        status="awaiting_user_input",
        last_summary="Need an answer before continuing.",
        pending_questions=[
            QuestionItem(
                question_set_id="qs_branch",
                question_id="q_branch",
                question_type="single_choice",
                question_text="Select the release branch.",
                choices=["main", "dev"],
            )
        ],
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:10:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_kill_001",
            trace_id="trace_cmd_kill_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:10:00",
            command_id="cmd_kill_001",
            command_type="kill",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "kill": {},
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    assert structured_payload["session_action_result"]["action_type"] == "kill"
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.status == "killed"
    assert updated_state.pending_questions == []
    assert updated_state.last_summary == "Task was cancelled while awaiting user input."
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_end_command_marks_session_ended(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    thread_state = _build_existing_thread_state(task_root, status="done")
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=_ImmediateSuccessRunner(task_root),
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:11:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_end_001",
            trace_id="trace_cmd_end_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:11:00",
            command_id="cmd_end_001",
            command_type="end",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "end": {},
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    assert structured_payload["session_action_result"]["action_type"] == "end"
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.status == "done"
    assert updated_state.lifecycle == "ended"
    assert updated_state.last_summary == "Completed."
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_answers_command_executes_without_mail_bridge(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    pending_questions = [
        QuestionItem(
            question_set_id="qs_release",
            question_id="q_branch",
            question_type="single_choice",
            question_text="Select the release branch.",
            choices=["main", "release"],
        ),
        QuestionItem(
            question_set_id="qs_release",
            question_id="q_env",
            question_type="short_text",
            question_text="Which environment should be targeted?",
        ),
    ]
    thread_state = _build_existing_thread_state(
        task_root,
        status="paused",
        last_summary="Waiting for answers.",
        paused_from_status="awaiting_user_input",
        pending_questions=pending_questions,
        backend_session_id="sdk-session-001",
        backend_session_resumable=True,
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    runner = _ImmediateSuccessRunner(task_root)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=runner,
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:12:00",
    )
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_answers_001",
            trace_id="trace_cmd_answers_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:12:00",
            command_id="cmd_answers_001",
            command_type="answers",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "answers": {
                    "question_answers": [
                        {"question_id": "q_branch", "value": "release"},
                        {"question_id": "q_env", "value": "staging"},
                    ]
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    assert structured_payload["session_action_result"]["action_type"] == "answers"
    assert len(runner.snapshots) == 1
    expected_answers = [
        QuestionAnswer(question_id="q_branch", value="release", raw_value="release"),
        QuestionAnswer(question_id="q_env", value="staging", raw_value="staging"),
    ]
    snapshot = runner.snapshots[0]
    assert snapshot.run_mode == "resume"
    assert snapshot.turn_text == canonical_answer_summary("qs_release", expected_answers)
    assert (
        "Additional context from reply:\n" + canonical_answer_context("qs_release", pending_questions, expected_answers)
    ) in snapshot.task_text
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.status == "done"
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_attachment_continuation_materializes_attachments_into_resume_turn(tmp_path) -> None:
    task_root = tmp_path / "task_root"
    repo_path = str(tmp_path / "repo")
    workdir = "feature/taskmail/internal"
    Path(repo_path, workdir).mkdir(parents=True, exist_ok=True)
    thread_state = _build_existing_thread_state(
        task_root,
        status="done",
        backend_session_id="sdk-session-attachment-001",
        backend_session_resumable=True,
        repo_path=repo_path,
        workdir=workdir,
    )
    fake_mail_client = _FakeMailClient()
    workspace_item = _workspace_item(repo_path=thread_state.repo_path, workdir=thread_state.workdir)
    runner = _ImmediateSuccessRunner(task_root)
    client = PcControlPlaneClient(
        relay_url="ws://127.0.0.1:8787/relay",
        transport_token="relay-secret",
        pc_id="pc_home",
        client_version="0.1.0",
        display_name="pc_home",
        config=AppConfig(from_addr="bot@example.com", codex_profile_models={"strong": "gpt-5-codex"}),
        runner=runner,
        mail_client=fake_mail_client,
        workspace_provider=lambda: [workspace_item],
        clock=lambda: "2026-03-25T10:13:00",
    )
    attachment_bytes = b"attachment:v1"
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_attachment_cont_001",
            trace_id="trace_cmd_attachment_cont_001",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:13:00",
            command_id="cmd_attachment_cont_001",
            command_type="attachment_continuation",
            workspace_id=str(workspace_item["workspace_id"]),
            session_id=thread_state.session_id,
            execution_policy={},
            command_payload={
                "target": {
                    "scope": "current_session",
                    "workspace_id": thread_state.workspace_id,
                    "session_id": thread_state.session_id,
                    "thread_id": thread_state.thread_id,
                },
                "attachment_continuation": {
                    "reply_text": "Please continue after reviewing the attached screenshot.",
                    "attachments": [
                        {
                            "name": "wireframe.png",
                            "content_type": "image/png",
                            "size_bytes": len(attachment_bytes),
                            "content_bytes_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                        }
                    ],
                },
            },
        )
    )

    messages, _websocket = asyncio.run(_dispatch_messages(client, command))

    ack, result, projection_batches = _ack_result_and_projection_batches(messages)
    assert ack.payload["ack_status"] == "accepted"
    assert result.payload["final_status"] == "done"
    assert projection_batches
    structured_payload = result.payload["structured_payload"]
    assert structured_payload["kind"] == "session_action_result"
    assert structured_payload["session_action_result"]["action_type"] == "attachment_continuation"
    assert len(runner.snapshots) == 1
    snapshot = runner.snapshots[0]
    assert snapshot.run_mode == "resume"
    assert len(snapshot.attachments) == 1
    saved_path = Path(snapshot.attachments[0])
    assert saved_path.exists()
    assert saved_path.name.endswith("wireframe.png")
    assert saved_path.read_bytes() == attachment_bytes
    assert snapshot.turn_text is not None
    assert "Please continue after reviewing the attached screenshot." in snapshot.turn_text
    assert "New incoming attachments materialized in workdir:" in snapshot.turn_text
    updated_state = load_thread_state(thread_state.thread_id, task_root)
    assert updated_state.status == "done"
    assert fake_mail_client.sent_messages == []


def test_pc_control_plane_client_registers_and_reports_workspace_snapshot(tmp_path) -> None:
    asyncio.run(_run_pc_control_plane_client_test(tmp_path))


def test_pc_control_plane_client_replays_persisted_output_chunks_after_reconnect(tmp_path) -> None:
    async def _run() -> None:
        task_root = tmp_path / "pc_task_root"
        runner = _ImmediateSuccessRunner(task_root)
        snapshot = TaskSnapshot(
            task_id="task_cmd_001",
            thread_id="thread_cmd_001",
            backend="codex",
            profile="strong",
            permission="highest",
            repo_path=str(tmp_path / "repo"),
            workdir=None,
            task_text="Replay persisted stream evidence",
            acceptance=[],
            timeout_minutes=30,
            mode="modify",
            attachments=[],
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:21",
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport="sdk",
        )
        runner.start_background_task(snapshot)

        app_config = AppConfig(
            relay_url="ws://127.0.0.1:8787/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
            codex_profile_models={"strong": "gpt-5-codex"},
        )
        client = PcControlPlaneClient(
            relay_url=app_config.relay_url,
            transport_token=app_config.relay_transport_token,
            pc_id=app_config.relay_client_id,
            client_version=app_config.relay_client_version,
            display_name="pc_home",
            config=app_config,
            runner=runner,
            clock=lambda: "2026-03-25T11:00:00",
        )
        client._remember_output_chunk_replay_context(
            "cmd_001",
            trace_id="trace_cmd_001",
            thread_id="thread_cmd_001",
            task_id="task_cmd_001",
        )
        client._mark_output_chunk_replay_needed()
        client._current_connection_epoch = 7

        websocket = _RecordingWebSocket()
        await client._replay_output_chunks_after_reconnect(websocket, asyncio.Lock())

        assert len(websocket.sent_frames) == 2
        parsed_messages = [parse_pc_control_client_message(frame) for frame in websocket.sent_frames]
        assert all(isinstance(message, PcOutputChunkMessage) for message in parsed_messages)
        assert [message.connection_epoch for message in parsed_messages] == [7, 7]
        assert [message.trace_id for message in parsed_messages] == ["trace_cmd_001", "trace_cmd_001"]
        assert [message.payload["output_chunk_id"] for message in parsed_messages] == [
            "output:cmd_001:thread_cmd_001:task_cmd_001:1",
            "output:cmd_001:thread_cmd_001:task_cmd_001:2",
        ]
        assert [(message.payload["seq"], message.payload["delta"], message.payload["text"]) for message in parsed_messages] == [
            (1, "Hello", None),
            (2, None, "Hello world"),
        ]
        assert client._output_chunk_replay_contexts["cmd_001"]["needs_replay"] is False

    asyncio.run(_run())


def test_pc_control_plane_client_replays_only_missing_chunks_for_resume_request(tmp_path) -> None:
    async def _run() -> None:
        task_root = tmp_path / "pc_task_root"
        runner = _ImmediateSuccessRunner(task_root)
        snapshot = TaskSnapshot(
            task_id="task_cmd_001",
            thread_id="thread_cmd_001",
            backend="codex",
            profile="strong",
            permission="highest",
            repo_path=str(tmp_path / "repo"),
            workdir=None,
            task_text="Replay missing tail only",
            acceptance=[],
            timeout_minutes=30,
            mode="modify",
            attachments=[],
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:21",
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport="sdk",
        )
        runner.start_background_task(snapshot)

        app_config = AppConfig(
            relay_url="ws://127.0.0.1:8787/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
            codex_profile_models={"strong": "gpt-5-codex"},
        )
        client = PcControlPlaneClient(
            relay_url=app_config.relay_url,
            transport_token=app_config.relay_transport_token,
            pc_id=app_config.relay_client_id,
            client_version=app_config.relay_client_version,
            display_name="pc_home",
            config=app_config,
            runner=runner,
            clock=lambda: "2026-03-25T11:05:00",
        )
        client._remember_output_chunk_replay_context(
            "cmd_001",
            trace_id="trace_cmd_001",
            thread_id="thread_cmd_001",
            task_id="task_cmd_001",
        )
        client._current_connection_epoch = 9

        websocket = _RecordingWebSocket()
        request = parse_pc_control_server_message(
            build_output_resume_request(
                message_id="msg_resume_001",
                trace_id="trace_cmd_001",
                pc_id="pc_home",
                connection_epoch=9,
                sent_at="2026-03-25T11:05:01",
                request_id="output_resume_request:cmd_001:thread_cmd_001:task_cmd_001:1",
                command_id="cmd_001",
                stream_id="thread_cmd_001:task_cmd_001",
                stream_id_source="derived_from_run_identity",
                after_seq=1,
                reason="reconnect_resume",
            )
        )

        await client._handle_output_resume_request(
            websocket,
            message=request,
            connection_epoch=9,
            send_lock=asyncio.Lock(),
        )

        assert len(websocket.sent_frames) == 1
        parsed_messages = [parse_pc_control_client_message(frame) for frame in websocket.sent_frames]
        assert all(isinstance(message, PcOutputChunkMessage) for message in parsed_messages)
        assert [message.connection_epoch for message in parsed_messages] == [9]
        assert [
            (message.payload["seq"], message.payload["delta"], message.payload["text"])
            for message in parsed_messages
        ] == [
            (2, None, "Hello world"),
        ]

    asyncio.run(_run())


def test_pc_control_plane_client_handles_server_driven_resume_over_websocket_roundtrip(tmp_path) -> None:
    asyncio.run(_run_pc_control_plane_output_resume_roundtrip_test(tmp_path))


async def _run_pc_control_plane_client_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    (sync_root / "alpha").mkdir(parents=True)

    relay_config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_name="TaskMail Relay",
        from_addr="bot@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    pc_runtime = build_pc_control_runtime(relay_config)
    relay = build_runtime_relay(
        relay_config,
        session_store=session_store,
        packet_store=packet_store,
    )
    server = await start_relay_server(
        relay_config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
        pc_control_runtime=pc_runtime,
    )
    client = None
    runner = _ImmediateSuccessRunner(tmp_path / "pc_task_root")
    try:
        host, port = server.sockets[0].getsockname()[:2]
        app_config = AppConfig(
            relay_url=f"ws://{host}:{port}/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
            project_sync_roots=[str(sync_root)],
            codex_profile_models={"strong": "gpt-5-codex"},
        )
        client = PcControlPlaneClient(
            relay_url=app_config.relay_url,
            transport_token=app_config.relay_transport_token,
            pc_id=app_config.relay_client_id,
            client_version=app_config.relay_client_version,
            display_name="pc_home",
            config=app_config,
            runner=runner,
            heartbeat_interval_seconds=60,
            snapshot_interval_seconds=60,
        )
        client.start()
        await asyncio.sleep(2.5)

        node = pc_runtime.node_store.get_node("pc_home")
        workspace_items = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")
        assert node is not None
        assert len(workspace_items) == 1
        command = parse_pc_control_server_message(
            build_command_dispatch(
                message_id="msg_cmd_001",
                trace_id="trace_cmd_001",
                pc_id="pc_home",
                connection_epoch=node.current_connection_epoch,
                sent_at="2026-03-25T10:00:20",
                command_id="cmd_001",
                command_type="new_task",
                workspace_id=workspace_items[0].workspace_id,
                execution_policy={
                    "backend": "codex",
                    "profile": "strong",
                    "permission": "highest",
                    "backend_transport": "sdk",
                },
                command_payload={"task_text": "Refactor floor_shear.py"},
            )
        )
        pc_runtime.enqueue_command(command)
        await _wait_until(
            lambda: (
                pc_runtime.command_store.get_command("pc_home", "cmd_001") is not None
                and pc_runtime.command_store.get_command("pc_home", "cmd_001").result is not None
            ),
            timeout_seconds=5,
        )

        node = pc_runtime.node_store.get_node("pc_home")
        workspace_items = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")
        command_record = pc_runtime.command_store.get_command("pc_home", "cmd_001")

        assert node is not None
        assert node.status == "online"
        assert node.current_connection_epoch >= 1
        assert node.workspace_count == 1
        assert len(workspace_items) == 1
        assert workspace_items[0].repo_path == str(sync_root / "alpha")
        assert command_record is not None
        assert command_record.ack_status == "accepted"
        assert [event.event_type for event in command_record.events] == ["accepted", "running", "done"]
        assert command_record.final_status == "done"
        assert command_record.result is not None
        assert command_record.result.summary == SUMMARY_LINE
        assert command_record.result.structured_payload["kind"] == "run_result"
        assert command_record.result.effective_execution["resolved_model"] == "gpt-5-codex"
        assert [(chunk.seq, chunk.delta, chunk.text) for chunk in command_record.output_chunks] == [
            (1, "Hello", None),
            (2, None, "Hello world"),
        ]
        assert command_record.artifact_manifest is not None
        assert command_record.artifact_manifest.artifacts_root == "runs/task_cmd_001/artifacts"
        assert [item["artifact_id"] for item in command_record.artifact_manifest.artifacts] == [
            "artifact-preview",
            "artifact-report",
        ]
        assert command_record.artifact_manifest.artifacts[0]["download_ref"] == {
            "kind": "vps_file",
            "file_id": "file_preview_002",
            "metadata_url": "/v1/files/file_preview_002",
            "content_url": "/v1/files/file_preview_002/content",
        }
        assert command_record.artifact_manifest.artifacts[1]["download_ref"] is None
        assert len(runner.snapshots) == 1
        assert runner.snapshots[0].task_text == "Refactor floor_shear.py"
        assert runner.snapshots[0].backend_transport == "sdk"
    finally:
        if client is not None:
            client.stop()
        server.close()
        await server.wait_closed()


async def _run_pc_control_plane_output_resume_roundtrip_test(tmp_path) -> None:
    state_dir = tmp_path / "relay_state"
    sync_root = tmp_path / "sync_root"
    (sync_root / "alpha").mkdir(parents=True)

    relay_config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(state_dir),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_name="TaskMail Relay",
        from_addr="bot@example.com",
    )
    session_store = PersistentSessionStore(state_dir)
    packet_store = PersistentAcceptedPacketStore(state_dir)
    pc_runtime = build_pc_control_runtime(relay_config)
    relay = build_runtime_relay(
        relay_config,
        session_store=session_store,
        packet_store=packet_store,
    )
    server = await start_relay_server(
        relay_config,
        relay=relay,
        session_store=session_store,
        packet_store=packet_store,
        pc_control_runtime=pc_runtime,
    )
    client = None
    runner = _ImmediateSuccessRunner(tmp_path / "pc_task_root")
    try:
        host, port = server.sockets[0].getsockname()[:2]
        app_config = AppConfig(
            relay_url=f"ws://{host}:{port}/relay",
            relay_transport_token="relay-secret",
            relay_client_id="pc_home",
            relay_client_version="0.1.0",
            project_sync_roots=[str(sync_root)],
            codex_profile_models={"strong": "gpt-5-codex"},
        )
        snapshot = TaskSnapshot(
            task_id="task_cmd_001",
            thread_id="thread_cmd_001",
            backend="codex",
            profile="strong",
            permission="highest",
            repo_path=str(sync_root / "alpha"),
            workdir=None,
            task_text="Replay missing tail after reconnect",
            acceptance=[],
            timeout_minutes=30,
            mode="modify",
            attachments=[],
            created_at="2026-03-25T10:00:21",
            updated_at="2026-03-25T10:00:21",
            run_mode="new",
            backend_session_id=None,
            turn_text=None,
            backend_transport="sdk",
        )
        runner.start_background_task(snapshot)

        client = PcControlPlaneClient(
            relay_url=app_config.relay_url,
            transport_token=app_config.relay_transport_token,
            pc_id=app_config.relay_client_id,
            client_version=app_config.relay_client_version,
            display_name="pc_home",
            config=app_config,
            runner=runner,
            heartbeat_interval_seconds=1,
            snapshot_interval_seconds=1,
        )
        client._remember_output_chunk_replay_context(
            "cmd_001",
            trace_id="trace_cmd_001",
            thread_id="thread_cmd_001",
            task_id="task_cmd_001",
        )
        client.start()

        await _wait_until(
            lambda: pc_runtime.node_store.get_node("pc_home") is not None,
            timeout_seconds=5,
        )
        node = pc_runtime.node_store.get_node("pc_home")
        workspace_items = pc_runtime.workspace_store.list_workspaces(pc_id="pc_home")
        assert node is not None
        assert node.current_connection_id is not None
        assert len(workspace_items) == 1

        pc_runtime.command_store.upsert_dispatch(
            PcCommandRecord(
                pc_id="pc_home",
                workspace_id=workspace_items[0].workspace_id,
                command_id="cmd_001",
                command_type="new_task",
                trace_id="trace_cmd_001",
                dispatch_message_id="msg_cmd_010",
                created_at="2026-03-25T10:00:20",
                execution_policy={
                    "backend": "codex",
                    "profile": "strong",
                    "permission": "highest",
                    "backend_transport": "sdk",
                },
                command_payload={"task_text": "Replay persisted stream evidence"},
            )
        )
        first_chunk = parse_pc_control_client_message(
            build_output_chunk(
                message_id="msg_out_010",
                trace_id="trace_cmd_001",
                pc_id="pc_home",
                connection_epoch=node.current_connection_epoch,
                sent_at="2026-03-25T10:00:21",
                output_chunk_id="output:cmd_001:thread_cmd_001:task_cmd_001:1",
                command_id="cmd_001",
                stream_id="thread_cmd_001:task_cmd_001",
                stream_id_source="derived_from_run_identity",
                seq=1,
                kind="assistant.delta",
                delta="Hello",
                status="streaming",
            )
        )
        assert pc_runtime.handle_output_chunk(first_chunk, connection_id=node.current_connection_id) is None

        client.stop()
        client.start()

        await _wait_until(
            lambda: (
                (pc_runtime.node_store.get_node("pc_home") is not None)
                and pc_runtime.node_store.get_node("pc_home").current_connection_epoch >= 2
                and (
                    pc_runtime.command_store.get_command("pc_home", "cmd_001") is not None
                    and len(pc_runtime.command_store.get_command("pc_home", "cmd_001").output_chunks) == 2
                )
            ),
            timeout_seconds=8,
        )

        reconnected_node = pc_runtime.node_store.get_node("pc_home")
        command_record = pc_runtime.command_store.get_command("pc_home", "cmd_001")
        assert reconnected_node is not None
        assert command_record is not None
        assert reconnected_node.current_connection_epoch >= 2
        assert [(chunk.seq, chunk.delta, chunk.text) for chunk in command_record.output_chunks] == [
            (1, "Hello", None),
            (2, None, "Hello world"),
        ]
        assert command_record.output_chunks[1].connection_epoch == reconnected_node.current_connection_epoch
    finally:
        if client is not None:
            client.stop()
        server.close()
        await server.wait_closed()


async def _wait_until(predicate, *, timeout_seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition was not satisfied before timeout")


def test_pc_control_plane_client_rejects_unresolved_profile_model() -> None:
    app_config = AppConfig(
        relay_url="ws://127.0.0.1:8787/relay",
        relay_transport_token="relay-secret",
        relay_client_id="pc_home",
        relay_client_version="0.1.0",
        codex_profile_models={"strong": ""},
    )
    client = PcControlPlaneClient(
        relay_url=app_config.relay_url,
        transport_token=app_config.relay_transport_token,
        pc_id=app_config.relay_client_id,
        client_version=app_config.relay_client_version,
        display_name="pc_home",
        config=app_config,
        workspace_provider=lambda: [
            {
                "workspace_id": "workspace_001",
                "workspace_norm": "workspace_norm_001",
                "repo_path": "E:\\projects\\repo_a",
                "workdir": None,
                "display_name": "repo_a",
                "source": "project_sync_roots",
                "capabilities": {
                    "streaming": True,
                    "artifact_manifest": True,
                    "workspace_snapshot": True,
                    "supported_backends": ["codex"],
                    "profile_catalogs": {"codex": ["strong"]},
                    "permission_modes": ["default", "highest"],
                    "backend_transport_modes": {"codex": ["cli", "sdk"]},
                },
            }
        ],
    )

    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_003",
            trace_id="trace_cmd_003",
            pc_id="pc_home",
            connection_epoch=1,
            sent_at="2026-03-25T10:00:20",
            command_id="cmd_003",
            command_type="new_task",
            workspace_id="workspace_001",
            execution_policy={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
            },
            command_payload={"task_text": "Refactor floor_shear.py"},
        )
    )

    admission = client._admit_command(command)

    assert admission["ack_status"] == "rejected"
    assert admission["error_code"] == "profile_model_unresolved"
