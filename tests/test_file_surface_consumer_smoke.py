import threading
from pathlib import Path

import pytest

from mail_runner.file_surface_consumer_smoke import run_file_surface_consumer_smoke
from mail_runner.relay_server.app import build_http_server
from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.session_store import InMemorySessionStore


def test_file_surface_consumer_smoke_live_relay_mode_validates_authenticated_download_ref(
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
            lambda settings: (_ for _ in ()).throw(AssertionError("consumer smoke should not route to COS")),
        )

        result = run_file_surface_consumer_smoke(
            output_dir=tmp_path,
            run_name="file-surface-consumer-smoke-live",
            config_path=config_path,
        )

        assert result["success"] is True
        assert result["owner_smoke_success"] is True
        assert result["consumer_download_ref_source"] == "external_delivery_index.file_surface"

        authenticated = result["authenticated_fetch"]
        assert authenticated["status_code"] == 200
        assert authenticated["content_verified"] is True

        anonymous = result["anonymous_fetch"]
        assert anonymous["status_code"] == 401
        assert anonymous["json_payload"]["error_code"] == "unauthorized"

        wrong_token = result["wrong_token_fetch"]
        assert wrong_token["status_code"] == 401
        assert wrong_token["json_payload"]["error_code"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_file_surface_consumer_smoke_live_relay_mode_requires_relay_transport(tmp_path: Path) -> None:
    config_path = tmp_path / "mail_config.invalid.yaml"
    config_path.write_text("outbound_transport: email\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outbound_transport=relay"):
        run_file_surface_consumer_smoke(
            output_dir=tmp_path,
            run_name="file-surface-consumer-smoke-invalid",
            config_path=config_path,
        )
