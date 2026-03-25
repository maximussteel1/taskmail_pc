from __future__ import annotations

import asyncio
import json

from mail_runner.adapters.mock_adapter import SUMMARY_LINE
from mail_runner.artifact_resolver import write_artifact_index
from mail_runner.config import AppConfig
from mail_runner.file_surface import write_artifact_upload_success_binding
from mail_runner.models import RunArtifact, RunResult, TaskSnapshot, ThreadState
from mail_runner.pc_control_plane_client import PcControlPlaneClient
from mail_runner.relay_server.app import build_runtime_relay, start_relay_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.packet_store import PersistentAcceptedPacketStore
from mail_runner.relay_server.pc_command_store import PcCommandRecord
from mail_runner.relay_server.pc_control_protocol import (
    PcOutputChunkMessage,
    build_command_dispatch,
    build_output_chunk,
    build_output_resume_request,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_control_runtime import build_pc_control_runtime
from mail_runner.relay_server.session_store import PersistentSessionStore
from mail_runner.stream_events import STREAM_EVENTS_FILENAME
from mail_runner.workspace import WorkspaceManager


class _ImmediateSuccessRunner:
    def __init__(self, task_root) -> None:
        self.snapshots: list[TaskSnapshot] = []
        self.workspace = WorkspaceManager(task_root)

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
        return accepted_state


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent_frames: list[dict[str, object]] = []

    async def send(self, payload: str) -> None:
        self.sent_frames.append(json.loads(payload))


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
            heartbeat_interval_seconds=1,
            snapshot_interval_seconds=1,
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
        assert command_record.artifact_manifest.artifacts[0]["download_ref"] == "/v1/files/file_preview_002/content"
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
