from __future__ import annotations

from mail_runner.pc_control_live_roundtrip_smoke import (
    evaluate_live_roundtrip_observation,
    extract_remote_command_record,
    select_probe_workspace,
)


def test_select_probe_workspace_prefers_matching_repo_path() -> None:
    selected = select_probe_workspace(
        [
            {"workspace_id": "workspace_other", "repo_path": "E:\\projects\\other_repo"},
            {"workspace_id": "workspace_target", "repo_path": "E:\\projects\\mail_based_task_manager"},
        ],
        preferred_repo_path="E:\\projects\\mail_based_task_manager",
    )

    assert selected["workspace_id"] == "workspace_target"


def test_extract_remote_command_record_matches_pc_and_command_id() -> None:
    record = extract_remote_command_record(
        {
            "version": 1,
            "commands": [
                {"pc_id": "pc-a", "command_id": "cmd_other"},
                {"pc_id": "pc-home", "command_id": "cmd_target", "ack_status": "accepted"},
            ],
        },
        pc_id="pc-home",
        command_id="cmd_target",
    )

    assert record is not None
    assert record["ack_status"] == "accepted"


def test_evaluate_live_roundtrip_observation_requires_resume_chunks_and_artifact_manifest() -> None:
    observation = evaluate_live_roundtrip_observation(
        {
            "ack_status": "accepted",
            "events": [
                {"event_type": "accepted"},
                {"event_type": "running"},
                {"event_type": "done"},
            ],
            "result": {"final_status": "done"},
            "output_chunks": [
                {"seq": 1, "stream_id": "thread_x:task_y"},
                {"seq": 2, "stream_id": "thread_x:task_y"},
                {"seq": 3, "stream_id": "thread_x:task_y"},
            ],
            "artifact_manifest": {
                "artifacts": [
                    {
                        "artifact_id": "artifact-live-probe-report",
                        "download_ref_source": "external_delivery_index.file_surface",
                    }
                ]
            },
        },
        expected_stream_id="thread_x:task_y",
        expected_artifact_ids=["artifact-live-probe-report"],
        expected_after_seq=1,
    )

    assert observation["success"] is True
    assert observation["resume_after_seq_matches"] is True
    assert observation["artifact_manifest_present"] is True
    assert observation["artifact_download_ref_sources"] == ["external_delivery_index.file_surface"]
