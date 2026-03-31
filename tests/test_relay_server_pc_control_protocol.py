from __future__ import annotations

import pytest

from mail_runner.relay_server.pc_control_protocol import (
    PcArtifactManifestMessage,
    PcCommandAckMessage,
    PcCommandDispatchMessage,
    PcDeliveryAckMessage,
    PcCommandEventMessage,
    PcCommandResultMessage,
    PcControlProtocolError,
    PcErrorMessage,
    PcHelloAckMessage,
    PcHelloMessage,
    PcIngressCandidateMessage,
    PcIngressDecisionMessage,
    PcMailboxLeaseAckMessage,
    PcMailboxLeaseMessage,
    PcOutputResumeRequestMessage,
    PcOutputChunkMessage,
    PcTerminalOutcomeAckMessage,
    PcTerminalOutcomeMessage,
    PcThreadBindingAckMessage,
    PcThreadBindingMessage,
    PcWorkspaceSnapshotMessage,
    build_artifact_manifest,
    build_command_ack,
    build_command_dispatch,
    build_command_event,
    build_command_result,
    build_delivery_ack,
    build_ingress_candidate,
    build_ingress_decision,
    build_mailbox_lease,
    build_mailbox_lease_ack,
    build_output_chunk,
    build_output_resume_request,
    build_pc_error,
    build_pc_hello,
    build_pc_hello_ack,
    build_terminal_outcome,
    build_terminal_outcome_ack,
    build_thread_binding,
    build_thread_binding_ack,
    build_workspace_snapshot,
    parse_pc_control_client_message,
    parse_pc_control_server_message,
)


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


def test_pc_hello_roundtrip_parses_capabilities() -> None:
    payload = build_pc_hello(
        message_id="msg_hello_001",
        trace_id="trace_hello_001",
        pc_id="pc_home",
        sent_at="2026-03-25T10:00:00",
        display_name="Home PC",
        client_version="0.1.0",
        host_fingerprint="host_123",
        runtime_fingerprint="runtime_456",
        capabilities=_capabilities(),
    )

    parsed = parse_pc_control_client_message(payload)

    assert isinstance(parsed, PcHelloMessage)
    assert parsed.connection_epoch == 0
    assert parsed.payload["display_name"] == "Home PC"
    assert parsed.payload["capabilities"]["supported_backends"] == ["codex", "opencode"]


def test_workspace_snapshot_requires_workspace_capabilities() -> None:
    payload = {
        "schema_version": "v1",
        "type": "workspace_snapshot",
        "message_id": "msg_ws_001",
        "trace_id": "trace_ws_001",
        "pc_id": "pc_home",
        "connection_epoch": 1,
        "sent_at": "2026-03-25T10:00:10",
        "payload": {
            "snapshot_id": "snapshot_001",
            "workspaces": [
                {
                    "workspace_id": "workspace_001",
                    "repo_path": "E:\\projects\\repo_a",
                    "workdir": None,
                    "display_name": "repo_a",
                }
            ],
        },
    }

    with pytest.raises(PcControlProtocolError, match="payload.workspaces\\[0\\]\\.capabilities must be a dict"):
        parse_pc_control_client_message(payload)


def test_workspace_snapshot_roundtrip_parses_workspace_entries() -> None:
    payload = build_workspace_snapshot(
        message_id="msg_ws_001",
        trace_id="trace_ws_001",
        pc_id="pc_home",
        connection_epoch=3,
        sent_at="2026-03-25T10:00:10",
        snapshot_id="snapshot_001",
        workspaces=[
            {
                "workspace_id": "workspace_001",
                "workspace_norm": "e:/projects/repo_a",
                "repo_path": "E:\\projects\\repo_a",
                "workdir": None,
                "display_name": "repo_a",
                "source": "project_sync_roots",
                "capabilities": _capabilities(),
            }
        ],
    )

    parsed = parse_pc_control_client_message(payload)

    assert isinstance(parsed, PcWorkspaceSnapshotMessage)
    assert parsed.payload["snapshot_id"] == "snapshot_001"
    assert parsed.payload["workspaces"][0]["workspace_id"] == "workspace_001"
    assert parsed.payload["workspaces"][0]["capabilities"]["permission_modes"] == ["default", "highest"]


def test_pc_control_server_messages_roundtrip() -> None:
    hello_ack = parse_pc_control_server_message(
        build_pc_hello_ack(
            message_id="msg_hello_ack_001",
            trace_id="trace_hello_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:01",
            keepalive_seconds=15,
        )
    )
    error = parse_pc_control_server_message(
        build_pc_error(
            message_id="msg_error_001",
            trace_id="trace_error_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:02",
            code="stale_connection_epoch",
            message="stale connection",
        )
    )

    assert isinstance(hello_ack, PcHelloAckMessage)
    assert hello_ack.connection_epoch == 7
    assert isinstance(error, PcErrorMessage)
    assert error.payload["code"] == "stale_connection_epoch"


def test_command_dispatch_and_ack_roundtrip() -> None:
    dispatch = parse_pc_control_server_message(
        build_command_dispatch(
            message_id="msg_cmd_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:20",
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
    ack = parse_pc_control_client_message(
        build_command_ack(
            message_id="msg_ack_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:21",
            command_id="cmd_001",
            ack_status="accepted_but_queued",
            queue_position=1,
            reason="command accepted into the local runner queue",
            error_code=None,
        )
    )

    assert isinstance(dispatch, PcCommandDispatchMessage)
    assert dispatch.payload["execution_policy"]["backend"] == "codex"
    assert isinstance(ack, PcCommandAckMessage)
    assert ack.payload["ack_status"] == "accepted_but_queued"
    assert ack.payload["queue_position"] == 1


def test_command_event_and_result_roundtrip() -> None:
    event = parse_pc_control_client_message(
        build_command_event(
            message_id="msg_evt_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:22",
            event_id="event:cmd_001:running",
            command_id="cmd_001",
            event_type="running",
            summary="command is running on the local runner",
            effective_execution={
                "backend": "codex",
                "profile": "strong",
                "permission": "highest",
                "backend_transport": "sdk",
                "resolved_model": "gpt-5-codex",
            },
            event_payload={"thread_id": "thread_001", "task_id": "task_001"},
        )
    )
    result = parse_pc_control_client_message(
        build_command_result(
            message_id="msg_res_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:23",
            result_id="result:cmd_001",
            command_id="cmd_001",
            final_status="done",
            summary="Mock run completed successfully.",
            structured_payload={
                "kind": "run_result",
                "thread_id": "thread_001",
                "task_id": "task_001",
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

    assert isinstance(event, PcCommandEventMessage)
    assert event.payload["event_type"] == "running"
    assert event.payload["effective_execution"]["resolved_model"] == "gpt-5-codex"
    assert isinstance(result, PcCommandResultMessage)
    assert result.payload["final_status"] == "done"
    assert result.payload["structured_payload"]["kind"] == "run_result"


def test_output_chunk_and_artifact_manifest_roundtrip() -> None:
    output_chunk = parse_pc_control_client_message(
        build_output_chunk(
            message_id="msg_out_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:24",
            output_chunk_id="output:cmd_001:thread_001:task_001:1",
            command_id="cmd_001",
            stream_id="thread_001:task_001",
            stream_id_source="derived_from_run_identity",
            seq=1,
            kind="assistant.delta",
            delta="Hello",
            status="streaming",
        )
    )
    artifact_manifest = parse_pc_control_client_message(
        build_artifact_manifest(
            message_id="msg_art_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:25",
            manifest_id="artifact_manifest:cmd_001",
            command_id="cmd_001",
            artifacts_root="runs/task_001/artifacts",
            source="manifest",
            artifacts=[
                {
                    "artifact_id": "artifact-preview",
                    "kind": "image",
                    "name": "preview.png",
                    "content_type": "image/png",
                    "size": 8,
                    "download_ref": {
                        "kind": "vps_file",
                        "file_id": "file_preview_001",
                        "metadata_url": "/v1/files/file_preview_001",
                        "content_url": "/v1/files/file_preview_001/content",
                    },
                    "download_ref_source": "artifact_file_binding_index",
                }
            ],
        )
    )

    assert isinstance(output_chunk, PcOutputChunkMessage)
    assert output_chunk.payload["stream_id"] == "thread_001:task_001"
    assert output_chunk.payload["seq"] == 1
    assert isinstance(artifact_manifest, PcArtifactManifestMessage)
    assert artifact_manifest.payload["artifacts_root"] == "runs/task_001/artifacts"
    assert artifact_manifest.payload["artifacts"][0]["artifact_id"] == "artifact-preview"


def test_output_chunk_roundtrip_preserves_multiline_delta() -> None:
    output_chunk = parse_pc_control_client_message(
        build_output_chunk(
            message_id="msg_out_multiline_001",
            trace_id="trace_cmd_multiline_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:26",
            output_chunk_id="output:cmd_001:thread_001:task_001:2",
            command_id="cmd_001",
            stream_id="thread_001:task_001",
            stream_id_source="derived_from_run_identity",
            seq=2,
            kind="assistant.delta",
            delta="Line 1\n\nLine 2",
            status="streaming",
        )
    )

    assert isinstance(output_chunk, PcOutputChunkMessage)
    assert output_chunk.payload["delta"] == "Line 1\n\nLine 2"


def test_output_resume_request_roundtrip() -> None:
    request = parse_pc_control_server_message(
        build_output_resume_request(
            message_id="msg_resume_001",
            trace_id="trace_cmd_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:26",
            request_id="output_resume_request:cmd_001:thread_001:task_001:1",
            command_id="cmd_001",
            stream_id="thread_001:task_001",
            stream_id_source="derived_from_run_identity",
            after_seq=1,
            reason="reconnect_resume",
        )
    )

    assert isinstance(request, PcOutputResumeRequestMessage)
    assert request.payload["command_id"] == "cmd_001"
    assert request.payload["stream_id"] == "thread_001:task_001"
    assert request.payload["after_seq"] == 1


def test_mailbox_lease_and_ingress_roundtrip() -> None:
    lease = parse_pc_control_client_message(
        build_mailbox_lease(
            message_id="msg_lease_001",
            trace_id="trace_lease_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:27",
            request_id="request_lease_001",
            operation="acquire",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_ttl_seconds=45,
            config_fingerprint="cfg_001",
            host_fingerprint="host_001",
            runtime_fingerprint="runtime_001",
            degraded_mode=False,
        )
    )
    lease_ack = parse_pc_control_server_message(
        build_mailbox_lease_ack(
            message_id="msg_lease_ack_001",
            trace_id="trace_lease_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:28",
            request_id="request_lease_001",
            operation="acquire",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_status="active",
            lease_holder_id="runner:pc_home:abc123",
            lease_pc_id="pc_home",
            lease_epoch=3,
            expires_at="2026-03-25T10:01:13",
        )
    )
    ingress = parse_pc_control_client_message(
        build_ingress_candidate(
            message_id="msg_ingress_001",
            trace_id="trace_ingress_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:29",
            request_id="request_ingress_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=3,
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
    ingress_decision = parse_pc_control_server_message(
        build_ingress_decision(
            message_id="msg_ingress_decision_001",
            trace_id="trace_ingress_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:30",
            request_id="request_ingress_001",
            ingress_id="ingress_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            decision="accepted",
            classification="new_task",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=3,
        )
    )

    assert isinstance(lease, PcMailboxLeaseMessage)
    assert lease.payload["operation"] == "acquire"
    assert isinstance(lease_ack, PcMailboxLeaseAckMessage)
    assert lease_ack.payload["lease_epoch"] == 3
    assert isinstance(ingress, PcIngressCandidateMessage)
    assert ingress.payload["uid"] == 101
    assert isinstance(ingress_decision, PcIngressDecisionMessage)
    assert ingress_decision.payload["decision"] == "accepted"


def test_thread_binding_and_terminal_outcome_roundtrip() -> None:
    binding = parse_pc_control_client_message(
        build_thread_binding(
            message_id="msg_binding_001",
            trace_id="trace_binding_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:31",
            request_id="request_binding_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=3,
            ingress_id="ingress_001",
            root_message_id="<ingress@example.com>",
            thread_id="thread_001",
            session_id="thread_001",
            repo_path="E:\\projects\\repo_a",
            workdir=None,
            subject_norm="demo",
        )
    )
    binding_ack = parse_pc_control_server_message(
        build_thread_binding_ack(
            message_id="msg_binding_ack_001",
            trace_id="trace_binding_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:32",
            request_id="request_binding_001",
            ingress_id="ingress_001",
            binding_status="committed",
            thread_id="thread_001",
            session_id="thread_001",
        )
    )
    outcome = parse_pc_control_client_message(
        build_terminal_outcome(
            message_id="msg_outcome_001",
            trace_id="trace_outcome_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:33",
            request_id="request_outcome_001",
            mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
            lease_holder_id="runner:pc_home:abc123",
            lease_epoch=3,
            thread_id="thread_001",
            task_id="task_001",
            run_status="success",
            generated_at="2026-03-25T10:00:33",
            last_summary="done",
            terminal_mail_message_id="<done@example.com>",
            terminal_mail_subject="[DONE][S:thread_001] Demo",
            source_ingress_id="ingress_001",
        )
    )
    outcome_ack = parse_pc_control_server_message(
        build_terminal_outcome_ack(
            message_id="msg_outcome_ack_001",
            trace_id="trace_outcome_001",
            pc_id="pc_home",
            connection_epoch=7,
            sent_at="2026-03-25T10:00:34",
            request_id="request_outcome_001",
            thread_id="thread_001",
            task_id="task_001",
            outcome_status="committed",
            source_ingress_id="ingress_001",
        )
    )

    assert isinstance(binding, PcThreadBindingMessage)
    assert binding.payload["ingress_id"] == "ingress_001"
    assert isinstance(binding_ack, PcThreadBindingAckMessage)
    assert binding_ack.payload["binding_status"] == "committed"
    assert isinstance(outcome, PcTerminalOutcomeMessage)
    assert outcome.payload["terminal_mail_message_id"] == "<done@example.com>"
    assert isinstance(outcome_ack, PcTerminalOutcomeAckMessage)
    assert outcome_ack.payload["outcome_status"] == "committed"


def test_delivery_ack_roundtrip_parses_committed_message() -> None:
    payload = build_delivery_ack(
        message_id="msg_delivery_ack_001",
        trace_id="trace_delivery_ack_001",
        pc_id="pc_home",
        connection_epoch=7,
        sent_at="2026-03-25T10:00:34",
        request_id="projection_batch:001",
        message_type="projection_batch",
        delivery_status="committed",
    )

    parsed = parse_pc_control_server_message(payload)

    assert isinstance(parsed, PcDeliveryAckMessage)
    assert parsed.payload["request_id"] == "projection_batch:001"
    assert parsed.payload["message_type"] == "projection_batch"
    assert parsed.payload["delivery_status"] == "committed"
