import threading
from pathlib import Path

import pytest

from mail_runner.artifact_contract_smoke import run_artifact_contract_smoke
from mail_runner.relay_server.app import build_http_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.session_store import InMemorySessionStore


def test_artifact_contract_smoke_live_relay_mode_uses_file_surface_owner_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_config = RelayServerConfig(
        host="127.0.0.1",
        port=0,
        transport_token="relay-secret",
        state_dir=str(tmp_path / "relay_state"),
    )
    server = build_http_server(relay_config, session_store=InMemorySessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        config_path = tmp_path / "mail_config.live.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "outbound_transport: relay",
                    f"relay_url: ws://{host}:{port}/relay",
                    "relay_transport_token: relay-secret",
                    "relay_timeout_seconds: 5",
                    "external_delivery_threshold_mb: 20",
                    "external_delivery_backend_preference: auto",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "mail_runner.external_delivery._load_local_cos_config",
            lambda: {
                "region": "ap-shanghai",
                "bucket": "mailbot-compat",
                "secret_id": "secret-id",
                "secret_key": "secret-key",
                "object_prefix": "mail-runner",
            },
        )
        monkeypatch.setattr(
            "mail_runner.external_delivery._build_cos_client",
            lambda settings: (_ for _ in ()).throw(AssertionError("live smoke should not route to COS")),
        )

        result = run_artifact_contract_smoke(
            output_dir=tmp_path,
            run_name="artifact-contract-smoke-live",
            config_path=config_path,
        )

        assert result["success"] is True
        assert result["smoke_mode"] == "live_relay_file_surface"
        assert result["cleanup"]["required"] is False

        live_info = result["live_relay_file_surface"]
        assert live_info["mode"] == "live_relay_host"
        assert live_info["relay_url"] == f"ws://{host}:{port}/relay"
        assert live_info["metadata_status"] == 200
        assert live_info["download_status"] == 200
        assert live_info["download_verified"] is True

        assert len(result["external_deliveries"]) == 1
        assert result["external_deliveries"][0]["provider"] == "file_surface"
        preview_item = next(
            item for item in result["candidate_artifact_manifest"] if item["artifact_id"] == "artifact-preview"
        )
        assert preview_item["download_ref_source"] == "external_delivery_index.file_surface"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_artifact_contract_smoke_live_relay_mode_requires_relay_transport(tmp_path: Path) -> None:
    config_path = tmp_path / "mail_config.invalid.yaml"
    config_path.write_text("outbound_transport: email\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outbound_transport=relay"):
        run_artifact_contract_smoke(
            output_dir=tmp_path,
            run_name="artifact-contract-smoke-invalid",
            config_path=config_path,
        )
