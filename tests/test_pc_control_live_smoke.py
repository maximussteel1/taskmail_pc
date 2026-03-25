from mail_runner.pc_control_live_smoke import evaluate_stale_epoch_error, evaluate_workspace_snapshot_observation
from mail_runner.relay_server.pc_control_protocol import PcErrorMessage


def test_evaluate_workspace_snapshot_observation_prefers_remote_state() -> None:
    observation = evaluate_workspace_snapshot_observation(
        health_before_payload={"pc_control": {"node_count": 1, "workspace_count": 23, "command_count": 0}},
        health_payload={"pc_control": {"node_count": 1, "workspace_count": 23, "command_count": 0}},
        remote_nodes_payload={
            "version": 1,
            "nodes": [
                {
                    "pc_id": "pc-home",
                    "workspace_count": 2,
                    "updated_at": "2026-03-25T23:10:00",
                    "current_connection_epoch": 7,
                }
            ],
        },
        remote_workspaces_payload={
            "version": 1,
            "workspaces": [
                {"pc_id": "pc-home", "workspace_id": "workspace_a", "updated_at": "2026-03-25T23:10:00"},
                {"pc_id": "pc-home", "workspace_id": "workspace_b", "updated_at": "2026-03-25T23:10:00"},
            ],
        },
        pc_id="pc-home",
        expected_workspace_count=2,
        snapshot_sent_at="2026-03-25T23:10:00",
        expected_connection_epoch=7,
    )

    assert observation["success"] is True
    assert observation["workspace_count_matches"] is True
    assert observation["remote_node_updated_at_matches"] is True
    assert observation["remote_workspace_updated_at_matches"] is True


def test_evaluate_workspace_snapshot_observation_falls_back_to_health_only() -> None:
    observation = evaluate_workspace_snapshot_observation(
        health_before_payload={"pc_control": {"node_count": 1, "workspace_count": 0, "command_count": 0}},
        health_payload={"pc_control": {"node_count": 1, "workspace_count": 3, "command_count": 0}},
        remote_nodes_payload=None,
        remote_workspaces_payload=None,
        pc_id="pc-home",
        expected_workspace_count=3,
        snapshot_sent_at="2026-03-25T23:10:00",
        expected_connection_epoch=7,
    )

    assert observation["success"] is True
    assert observation["observed_workspace_count"] == 3
    assert observation["remote_node_present"] is False
    assert observation["health_workspace_delta"] == 3


def test_evaluate_stale_epoch_error_requires_stale_connection_code() -> None:
    error = PcErrorMessage(
        schema_version="v1",
        type="error",
        message_id="err_001",
        trace_id="trace_001",
        pc_id="pc-home",
        connection_epoch=3,
        sent_at="2026-03-25T23:10:00",
        payload={"code": "stale_connection_epoch", "message": "stale connection_epoch for pc-home"},
    )

    observation = evaluate_stale_epoch_error(error)

    assert observation["success"] is True
    assert observation["code"] == "stale_connection_epoch"
