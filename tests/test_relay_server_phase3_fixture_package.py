from __future__ import annotations

from mail_runner.relay_server.phase3_fixture_package import (
    iter_phase3_fixture_units,
    load_phase3_fixture_manifest,
    load_phase3_fixture_unit,
    phase3_fixture_root,
)


def test_phase3_fixture_manifest_matches_fixture_directory() -> None:
    fixture_root = phase3_fixture_root()
    manifest = load_phase3_fixture_manifest(fixture_root)
    listed_files = {entry.file for entry in manifest.fixtures}
    actual_files = {
        path.name
        for path in fixture_root.glob("*.json")
        if path.name != "manifest.json"
    }

    assert listed_files == actual_files
    assert len(manifest.fixtures) == 20


def test_phase3_fixture_loader_validates_all_fixture_units() -> None:
    fixtures = iter_phase3_fixture_units()

    assert len(fixtures) == 20
    assert {fixture.fixture_meta["fixture_id"] for fixture in fixtures} == {
        "sub_workspace_session_queued_snapshot",
        "sub_workspace_thread_running_snapshot",
        "sub_repo_workdir_session_running_snapshot",
        "sub_repo_workdir_thread_running_snapshot",
        "sub_repo_only_reject_workspace_identity_unresolved",
        "sub_workspace_locator_mismatch_reject",
        "snapshot_running_no_reply_preview",
        "snapshot_running_with_reply_preview",
        "snapshot_waiting_single_question",
        "snapshot_waiting_multi_question",
        "snapshot_paused_from_question",
        "snapshot_done_terminal",
        "snapshot_failed_terminal",
        "snapshot_killed_terminal",
        "delta_state_transition_waiting",
        "delta_timeline_append_reply_preview",
        "mail_suppresses_direct_terminal_summary",
        "mail_suppresses_direct_question_prompt",
        "mail_suppresses_direct_paused_hint",
        "gap_resubscribe_fresh_snapshot",
    }


def test_phase3_single_question_fixture_keeps_quick_answer_value_and_label_shape() -> None:
    fixture = load_phase3_fixture_unit("snapshot_waiting_single_question")

    assert fixture.expected_projection["quick_answer_choices"] == [
        {"value": "main", "label": "Main branch"},
        {"value": "release", "label": "Release branch"},
    ]


def test_phase3_gap_fixture_includes_recovery_exchange() -> None:
    fixture = load_phase3_fixture_unit("gap_resubscribe_fresh_snapshot")

    assert fixture.recovery_exchange is not None
    assert fixture.recovery_exchange["request"]["task_run_packet"]["subscription"]["reason"] == "detail_refresh"
