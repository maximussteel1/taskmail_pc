from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_control_protocol import (
    PcCommandDispatchMessage,
    PcErrorMessage,
    PcHelloAckMessage,
    build_artifact_manifest,
    build_command_ack,
    build_command_dispatch,
    build_command_event,
    build_command_result,
    build_ingress_candidate,
    build_mailbox_lease,
    build_output_chunk,
    build_heartbeat,
    build_pc_hello,
    build_terminal_outcome,
    build_thread_binding,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)
from mail_runner.relay_server.pc_command_store import InMemoryPcCommandStore
from mail_runner.relay_server.pc_control_runtime import PcControlRuntime
from mail_runner.relay_server.pc_control_runtime import PcCommandDispatchValidationError
from mail_runner.relay_server.pc_credential_registry import InMemoryPcCredentialRegistry
from mail_runner.relay_server.pc_node_store import InMemoryPcNodeStore
from mail_runner.relay_server.workspace_inventory_store import InMemoryWorkspaceInventoryStore
from mail_runner.thread_store import build_workspace_id


def _capabilities() -> dict[str, object]:
    return {
        "streaming": True,
        "artifact_manifest": True,
        "workspace_snapshot": True,
        "supported_backends": ["codex", "opencode"],
        "profile_catalogs": {
            "codex": ["fast", "strong"],
            "opencode": ["fast", "strong"],
        },
        "permission_modes": ["default", "highest"],
        "backend_transport_modes": {
            "codex": ["cli", "sdk"],
            "opencode": ["cli", "sdk"],
        },
    }


def _runtime() -> PcControlRuntime:
    return PcControlRuntime(
        credential_registry=InMemoryPcCredentialRegistry(default_transport_token="relay-secret"),
        node_store=InMemoryPcNodeStore(),
        workspace_store=InMemoryWorkspaceInventoryStore(),
        command_store=InMemoryPcCommandStore(),
        keepalive_seconds=15,
        clock=lambda: "2026-03-25T10:00:00",
    )


def _register_online_pc(runtime: PcControlRuntime) -> tuple[str, int]:
    hello_message = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, connection_id, connection_epoch = runtime.handle_hello(hello_message, provided_token="relay-secret")
    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_ws_001",
            trace_id="trace_ws_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:06",
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "workspace_norm_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                    "source": "project_sync_roots",
                    "capabilities": _capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None
    return connection_id, connection_epoch


def test_pc_control_runtime_accepts_hello_heartbeat_and_workspace_snapshot() -> None:
    runtime = _runtime()
    hello_message = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )

    response, connection_id, connection_epoch = runtime.handle_hello(hello_message, provided_token="relay-secret")
    parsed_response = parse_pc_control_server_message(response)

    assert isinstance(parsed_response, PcHelloAckMessage)
    assert connection_epoch == 1

    heartbeat = parse_pc_control_client_message(
        build_heartbeat(
            message_id="msg_hb_001",
            trace_id="trace_hb_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:05",
            active_run_count=2,
            workspace_count=1,
            load_hint="busy",
        )
    )
    assert runtime.handle_heartbeat(heartbeat, connection_id=connection_id) is None

    snapshot = parse_pc_control_client_message(
        build_workspace_snapshot(
            message_id="msg_ws_001",
            trace_id="trace_ws_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:06",
            snapshot_id="snapshot_001",
            workspaces=[
                {
                    "workspace_id": "workspace_001",
                    "workspace_norm": "workspace_norm_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                    "source": "project_sync_roots",
                    "capabilities": _capabilities(),
                }
            ],
        )
    )
    assert runtime.handle_workspace_snapshot(snapshot, connection_id=connection_id) is None

    node = runtime.node_store.get_node("pc_home")
    workspace = runtime.workspace_store.get_workspace("pc_home", "workspace_001")

    assert node is not None
    assert node.active_run_count == 2
    assert node.workspace_count == 1
    assert workspace is not None
    assert workspace.repo_path == "E:\\projects\\repo_a"


def test_pc_control_runtime_rejects_stale_epoch_after_reconnect() -> None:
    runtime = _runtime()
    first_hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            sent_at="2026-03-25T10:00:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, first_connection_id, first_epoch = runtime.handle_hello(first_hello, provided_token="relay-secret")
    second_hello = parse_pc_control_client_message(
        build_pc_hello(
            message_id="msg_hello_002",
            trace_id="trace_hello_002",
            pc_id="pc_home",
            sent_at="2026-03-25T10:01:00",
            display_name="Home PC",
            client_version="0.1.0",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            capabilities=_capabilities(),
        )
    )
    _, _second_connection_id, second_epoch = runtime.handle_hello(second_hello, provided_token="relay-secret")

    stale_heartbeat = parse_pc_control_client_message(
        build_heartbeat(
            message_id="msg_hb_001",
            trace_id="trace_hb_001",
            pc_id="pc_home",
            connection_epoch=first_epoch,
            sent_at="2026-03-25T10:01:05",
            active_run_count=0,
            workspace_count=0,
            load_hint="normal",
        )
    )
    response = runtime.handle_heartbeat(stale_heartbeat, connection_id=first_connection_id)
    parsed = parse_pc_control_server_message(response)

    assert second_epoch == 2
    assert isinstance(parsed, PcErrorMessage)
    assert parsed.payload["code"] == "stale_connection_epoch"


def test_pc_control_runtime_dispatches_pending_command_and_records_ack() -> None:
    runtime = _runtime()
    connection_id, connection_epoch = _register_online_pc(runtime)

    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:10",
            command_id="cmd_001",
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
    assert isinstance(command, PcCommandDispatchMessage)

    runtime.enqueue_command(command)
    pending = runtime.collect_pending_dispatches(
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
    )

    assert len(pending) == 1
    assert pending[0]["payload"]["command_id"] == "cmd_001"

    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:11",
            command_id="cmd_001",
            ack_status="accepted",
        )
    )

    assert runtime.handle_command_ack(ack, connection_id=connection_id) is None
    record = runtime.command_store.get_command("pc_home", "cmd_001")

    assert record is not None
    assert record.ack_status == "accepted"


def test_pc_control_runtime_rejects_unsupported_backend_before_dispatch() -> None:
    runtime = _runtime()
    _connection_id, connection_epoch = _register_online_pc(runtime)

    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_002",
            trace_id="trace_cmd_002",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:12",
            command_id="cmd_002",
            command_type="new_task",
            workspace_id="workspace_001",
            execution_policy={
                "backend": "claude",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
            },
            command_payload={"task_text": "Refactor floor_shear.py"},
        )
    )

    with pytest.raises(PcCommandDispatchValidationError, match="backend is not supported"):
        runtime.enqueue_command(command)


def test_pc_control_runtime_records_canonical_event_and_result() -> None:
    runtime = _runtime()
    connection_id, connection_epoch = _register_online_pc(runtime)
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_010",
            trace_id="trace_cmd_010",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:10",
            command_id="cmd_010",
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
    runtime.enqueue_command(command)
    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_010",
            trace_id="trace_cmd_010",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:11",
            command_id="cmd_010",
            ack_status="accepted",
        )
    )
    assert runtime.handle_command_ack(ack, connection_id=connection_id) is None

    running_event = parse_pc_control_client_message(
        build_command_event(
            message_id="msg_evt_010",
            trace_id="trace_cmd_010",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:12",
            event_id="event:cmd_010:running",
            command_id="cmd_010",
            event_type="running",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            event_payload={"thread_id": "thread_cmd_010", "task_id": "task_cmd_010"},
        )
    )
    duplicate_running_event = parse_pc_control_client_message(
        build_command_event(
            message_id="msg_evt_011",
            trace_id="trace_cmd_010",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:13",
            event_id="event:cmd_010:running",
            command_id="cmd_010",
            event_type="running",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            event_payload={"thread_id": "thread_cmd_010", "task_id": "task_cmd_010"},
        )
    )
    result = parse_pc_control_client_message(
        build_command_result(
            message_id="msg_res_010",
            trace_id="trace_cmd_010",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:20",
            result_id="result:cmd_010",
            command_id="cmd_010",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={
                "kind": "run_result",
                "thread_id": "thread_cmd_010",
                "task_id": "task_cmd_010",
            },
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        )
    )
    duplicate_result = parse_pc_control_client_message(
        build_command_result(
            message_id="msg_res_011",
            trace_id="trace_cmd_010",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:21",
            result_id="result:cmd_010",
            command_id="cmd_010",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={
                "kind": "run_result",
                "thread_id": "thread_cmd_010",
                "task_id": "task_cmd_010",
            },
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
        )
    )

    assert runtime.handle_event(running_event, connection_id=connection_id) is None
    assert runtime.handle_event(duplicate_running_event, connection_id=connection_id) is None
    assert runtime.handle_result(result, connection_id=connection_id) is None
    assert runtime.handle_result(duplicate_result, connection_id=connection_id) is None

    record = runtime.command_store.get_command("pc_home", "cmd_010")

    assert record is not None
    assert [item.event_type for item in record.events] == ["running"]
    assert record.final_status == "done"
    assert record.latest_event_type == "done"
    assert record.result is not None
    assert record.result.summary == "Mock run completed successfully."
    assert record.result.effective_execution["resolved_model"] == "gpt-5-codex"


def test_pc_control_runtime_records_output_chunk_and_artifact_manifest() -> None:
    runtime = _runtime()
    connection_id, connection_epoch = _register_online_pc(runtime)
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_020",
            trace_id="trace_cmd_020",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:10",
            command_id="cmd_020",
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
    runtime.enqueue_command(command)
    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_020",
            trace_id="trace_cmd_020",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:11",
            command_id="cmd_020",
            ack_status="accepted",
        )
    )
    assert runtime.handle_command_ack(ack, connection_id=connection_id) is None

    output_chunk = parse_pc_control_client_message(
        build_output_chunk(
            message_id="msg_out_020",
            trace_id="trace_cmd_020",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:12",
            output_chunk_id="output:cmd_020:thread_cmd_020:1",
            command_id="cmd_020",
            stream_id="thread_cmd_020:task_cmd_020",
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        )
    )
    artifact_manifest = parse_pc_control_client_message(
        build_artifact_manifest(
            message_id="msg_art_020",
            trace_id="trace_cmd_020",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:20",
            manifest_id="artifact_manifest:cmd_020",
            command_id="cmd_020",
            artifacts_root="runs/task_cmd_020/artifacts",
            source="manifest",
            artifacts=[
                {
                    "artifact_id": "artifact-preview",
                    "kind": "image",
                    "name": "preview.png",
                    "content_type": "image/png",
                    "size": 8,
                    "download_ref": "/v1/files/file_preview_001/content",
                    "download_ref_source": "artifact_file_binding_index",
                }
            ],
        )
    )

    assert runtime.handle_output_chunk(output_chunk, connection_id=connection_id) is None
    assert runtime.handle_artifact_manifest(artifact_manifest, connection_id=connection_id) is None

    record = runtime.command_store.get_command("pc_home", "cmd_020")

    assert record is not None
    assert len(record.output_chunks) == 1
    assert record.output_chunks[0].stream_id == "thread_cmd_020:task_cmd_020"
    assert record.artifact_manifest is not None
    assert record.artifact_manifest.artifacts[0]["artifact_id"] == "artifact-preview"


def test_pc_control_runtime_collects_output_resume_requests_from_cursor() -> None:
    runtime = _runtime()
    connection_id, connection_epoch = _register_online_pc(runtime)
    command = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_030",
            trace_id="trace_cmd_030",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:10",
            command_id="cmd_030",
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
    runtime.enqueue_command(command)
    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_030",
            trace_id="trace_cmd_030",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:11",
            command_id="cmd_030",
            ack_status="accepted",
        )
    )
    output_chunk = parse_pc_control_client_message(
        build_output_chunk(
            message_id="msg_out_030",
            trace_id="trace_cmd_030",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:12",
            output_chunk_id="output:cmd_030:thread_cmd_030:task_cmd_030:1",
            command_id="cmd_030",
            stream_id="thread_cmd_030:task_cmd_030",
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        )
    )

    assert runtime.handle_command_ack(ack, connection_id=connection_id) is None
    assert runtime.handle_output_chunk(output_chunk, connection_id=connection_id) is None

    requests = runtime.collect_output_resume_requests(
        pc_id="pc_home",
        connection_id=connection_id,
        connection_epoch=connection_epoch,
    )

    assert len(requests) == 1
    parsed = parse_pc_control_server_message(requests[0])
    assert parsed.type == "output_resume_request"
    assert parsed.payload["command_id"] == "cmd_030"
    assert parsed.payload["stream_id"] == "thread_cmd_030:task_cmd_030"
    assert parsed.payload["after_seq"] == 1


def test_pc_control_runtime_manages_mailbox_lease_and_ingress_truth() -> None:
    runtime = _runtime()
    connection_id, connection_epoch = _register_online_pc(runtime)

    lease = parse_pc_control_client_message(
        build_mailbox_lease(
            message_id="msg_lease_001",
            trace_id="trace_lease_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:07",
            request_id="request_lease_001",
            operation="acquire",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_ttl_seconds=45,
            config_fingerprint="cfg_001",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
        )
    )
    lease_response = parse_pc_control_server_message(runtime.handle_mailbox_lease(lease, connection_id=connection_id))

    assert lease_response.type == "mailbox_lease_ack"
    assert lease_response.payload["lease_status"] == "active"
    assert lease_response.payload["lease_epoch"] == 1

    ingress = parse_pc_control_client_message(
        build_ingress_candidate(
            message_id="msg_ingress_001",
            trace_id="trace_ingress_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:08",
            request_id="request_ingress_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=1,
            folder="INBOX",
            uid_validity=777,
            uid=101,
            ingress_message_id="<ingress@example.com>",
            in_reply_to=None,
            references_hash="refs_hash_001",
            from_addr="user@example.com",
            subject="[OC] Demo",
            subject_norm="demo",
            raw_date="Wed, 25 Mar 2026 10:00:00 +0800",
            classification="new_task",
            candidate_status="ready",
        )
    )
    ingress_response = parse_pc_control_server_message(
        runtime.handle_ingress_candidate(ingress, connection_id=connection_id)
    )

    assert ingress_response.type == "ingress_decision"
    assert ingress_response.payload["decision"] == "accepted"

    binding = parse_pc_control_client_message(
        build_thread_binding(
            message_id="msg_binding_001",
            trace_id="trace_binding_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:09",
            request_id="request_binding_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=1,
            ingress_id=ingress_response.payload["ingress_id"],
            root_message_id="<ingress@example.com>",
            thread_id="thread_001",
            session_id="thread_001",
            repo_path="E:\\projects\\repo_a",
            workdir=None,
            subject_norm="demo",
        )
    )
    binding_response = parse_pc_control_server_message(runtime.handle_thread_binding(binding, connection_id=connection_id))

    assert binding_response.type == "thread_binding_ack"
    assert binding_response.payload["binding_status"] == "committed"
    assert runtime.list_thread_bindings() == [
        {
            "ingress_id": ingress_response.payload["ingress_id"],
            "mailbox_key": "imap://bot@example.com@imap.example.com/INBOX",
            "root_message_id": "<ingress@example.com>",
            "thread_id": "thread_001",
            "session_id": "thread_001",
            "repo_path": "E:\\projects\\repo_a",
            "workdir": None,
            "workspace_id": build_workspace_id("E:\\projects\\repo_a", None),
            "workspace_norm": "e:/projects/repo_a",
            "subject_norm": "demo",
            "binding_created_at": "2026-03-25T10:00:09",
            "lease_holder_id": "runner:pc_home:abc123",
            "pc_id": "pc_home",
            "lease_epoch": 1,
            "degraded_mode": False,
        }
    ]

    outcome = parse_pc_control_client_message(
        build_terminal_outcome(
            message_id="msg_outcome_001",
            trace_id="trace_outcome_001",
            pc_id="pc_home",
            connection_epoch=connection_epoch,
            sent_at="2026-03-25T10:00:10",
            request_id="request_outcome_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=1,
            thread_id="thread_001",
            task_id="task_001",
            run_status="success",
            generated_at="2026-03-25T10:00:10",
            last_summary="done",
            terminal_mail_message_id="<done@example.com>",
            terminal_mail_subject="[DONE][S:thread_001] Demo",
        )
    )
    outcome_response = parse_pc_control_server_message(
        runtime.handle_terminal_outcome(outcome, connection_id=connection_id)
    )

    assert outcome_response.type == "terminal_outcome_ack"
    assert outcome_response.payload["outcome_status"] == "committed"
    assert runtime.find_terminal_outcome(thread_id="thread_001")["source_ingress_id"] == ingress_response.payload["ingress_id"]
