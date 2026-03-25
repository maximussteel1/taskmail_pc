from mail_runner.pc_control_operator_dispatch import (
    build_operator_dispatch_request_payload,
    derive_pc_control_operator_dispatch_url,
)


def test_derive_pc_control_operator_dispatch_url_from_relay_url() -> None:
    assert (
        derive_pc_control_operator_dispatch_url("ws://127.0.0.1:8787/relay")
        == "http://127.0.0.1:8787/debug/pc-control/dispatch"
    )


def test_build_operator_dispatch_request_payload_keeps_optional_fields_narrow() -> None:
    payload = build_operator_dispatch_request_payload(
        pc_id="pc-home",
        workspace_id="workspace_001",
        command_type="status",
        execution_policy={"backend": "codex"},
        command_payload={"want": "summary"},
    )

    assert payload == {
        "pc_id": "pc-home",
        "workspace_id": "workspace_001",
        "command_type": "status",
        "execution_policy": {"backend": "codex"},
        "payload": {"want": "summary"},
    }
