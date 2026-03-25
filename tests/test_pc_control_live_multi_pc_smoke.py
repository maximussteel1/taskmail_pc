from __future__ import annotations

from mail_runner.pc_control_live_multi_pc_smoke import (
    build_probe_workspace,
    evaluate_dispatch_route_observation,
    evaluate_workspace_registration_observation,
    extract_remote_workspace_record,
)


def test_build_probe_workspace_overrides_identity_fields() -> None:
    workspace = build_probe_workspace(
        {
            "workspace_id": "workspace_base",
            "workspace_norm": "workspace_base",
            "repo_path": "E:\\projects\\mail_based_task_manager",
            "workdir": ".",
            "display_name": "base",
            "source": "inventory",
            "capabilities": {"supported_backends": ["codex"]},
        },
        workspace_id="workspace_probe_a",
        display_name="probe-a",
    )

    assert workspace["workspace_id"] == "workspace_probe_a"
    assert workspace["workspace_norm"] == "workspace_probe_a"
    assert workspace["display_name"] == "probe-a"
    assert workspace["repo_path"] == "E:\\projects\\mail_based_task_manager"


def test_extract_remote_workspace_record_matches_pc_and_workspace() -> None:
    record = extract_remote_workspace_record(
        {
            "version": 1,
            "workspaces": [
                {"pc_id": "pc-a", "workspace_id": "workspace_001"},
                {"pc_id": "pc-b", "workspace_id": "workspace_target", "repo_path": "E:\\projects\\mail_based_task_manager"},
            ],
        },
        pc_id="pc-b",
        workspace_id="workspace_target",
    )

    assert record is not None
    assert record["repo_path"] == "E:\\projects\\mail_based_task_manager"


def test_evaluate_dispatch_route_observation_requires_target_only_delivery_and_done_record() -> None:
    observation = evaluate_dispatch_route_observation(
        dispatch_message={
            "pc_id": "pc-probe-a",
            "connection_epoch": 1,
            "payload": {
                "command_id": "cmd_a",
                "workspace_id": "workspace_a",
            },
        },
        cross_message=None,
        remote_record={
            "pc_id": "pc-probe-a",
            "workspace_id": "workspace_a",
            "ack_status": "accepted",
            "events": [
                {"event_type": "accepted"},
                {"event_type": "running"},
                {"event_type": "done"},
            ],
            "result": {"final_status": "done"},
        },
        expected_pc_id="pc-probe-a",
        expected_workspace_id="workspace_a",
        expected_command_id="cmd_a",
    )

    assert observation["success"] is True
    assert observation["cross_message_present"] is False


def test_evaluate_workspace_registration_observation_requires_both_pairs() -> None:
    observation = evaluate_workspace_registration_observation(
        {
            "version": 1,
            "workspaces": [
                {"pc_id": "pc-a", "workspace_id": "workspace_a"},
                {"pc_id": "pc-b", "workspace_id": "workspace_b"},
            ],
        },
        expected_pairs=[("pc-a", "workspace_a"), ("pc-b", "workspace_b")],
    )

    assert observation["success"] is True
    assert [item["present"] for item in observation["pairs"]] == [True, True]
