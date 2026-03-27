import pytest

from mail_runner.pc_control_operator_read import (
    build_pc_control_commands_query_params,
    build_pc_control_ingress_query_params,
    build_pc_control_terminal_outcome_query_params,
    derive_pc_control_operator_commands_url,
    derive_pc_control_operator_ingress_url,
    derive_pc_control_operator_lease_url,
    derive_pc_control_operator_nodes_url,
    derive_pc_control_operator_terminal_outcome_url,
    derive_pc_control_operator_workspaces_url,
)


def test_derive_pc_control_operator_read_urls_from_relay_url() -> None:
    assert derive_pc_control_operator_nodes_url("ws://127.0.0.1:8787/relay") == "http://127.0.0.1:8787/debug/pc-control/nodes"
    assert (
        derive_pc_control_operator_workspaces_url("wss://relay.example.com/relay")
        == "https://relay.example.com/debug/pc-control/workspaces"
    )
    assert (
        derive_pc_control_operator_commands_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/commands"
    )
    assert (
        derive_pc_control_operator_lease_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/lease"
    )
    assert (
        derive_pc_control_operator_ingress_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/ingress"
    )
    assert (
        derive_pc_control_operator_terminal_outcome_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/terminal-outcome"
    )


def test_build_pc_control_commands_query_rejects_command_detail_without_pc_id() -> None:
    with pytest.raises(ValueError, match="pc_id is required"):
        build_pc_control_commands_query_params(command_id="cmd_001")


def test_build_pc_control_ingress_query_accepts_supported_lookup_shapes() -> None:
    assert build_pc_control_ingress_query_params(ingress_id="ingress_001") == {"ingress_id": "ingress_001"}
    assert build_pc_control_ingress_query_params(
        mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
        message_id="<ingress@example.com>",
    ) == {
        "mailbox_key": "imap://bot@example.com@imap.example.com/INBOX",
        "message_id": "<ingress@example.com>",
    }
    assert build_pc_control_ingress_query_params(
        mailbox_key="imap://bot@example.com@imap.example.com/INBOX",
        uid=101,
        uid_validity=777,
        folder="INBOX",
    ) == {
        "mailbox_key": "imap://bot@example.com@imap.example.com/INBOX",
        "uid": "101",
        "uid_validity": "777",
        "folder": "INBOX",
    }


def test_build_pc_control_ingress_query_rejects_missing_selector_or_mailbox_key() -> None:
    with pytest.raises(ValueError, match="ingress lookup requires"):
        build_pc_control_ingress_query_params()
    with pytest.raises(ValueError, match="mailbox_key is required"):
        build_pc_control_ingress_query_params(message_id="<ingress@example.com>")
    with pytest.raises(ValueError, match="mailbox_key is required"):
        build_pc_control_ingress_query_params(uid=101)


def test_build_pc_control_terminal_outcome_query_requires_thread_id() -> None:
    with pytest.raises(ValueError, match="thread_id is required"):
        build_pc_control_terminal_outcome_query_params(thread_id="")
