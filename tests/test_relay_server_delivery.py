from __future__ import annotations

import base64

from mail_runner.relay_server.config import RelayServerConfig
from mail_runner.relay_server.delivery import RelayPacketDeliverer, build_dispatch_request_from_packet
from mail_runner.relay_server.packet_store import AcceptedRelayPacket


class FakeMailClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_mail(self, **kwargs):
        self.calls.append(kwargs)
        return "<relay-vps-sent@example.com>"


def _packet() -> AcceptedRelayPacket:
    return AcceptedRelayPacket(
        packet_id="packet:relay:001",
        receipt_id="receipt:relay:001",
        connection_id="conn:pc-001",
        client_id="pc-001",
        client_trace_id="task_001",
        received_at="2026-03-20T16:00:00",
        task_run_packet={
            "packet_id": "packet:relay:001",
            "task_id": "task_001",
            "created_at": "2026-03-20T16:00:00",
            "message_kind": "status_update",
            "content_format": "text/plain+text/html",
            "html": "<html><body>Done.</body></html>",
            "text_fallback": "Status: DONE\n",
            "attachments": [
                {
                    "path": "E:/missing/result.txt",
                    "name": "result.txt",
                    "content_type": "text/plain",
                    "attach": True,
                    "inline": False,
                    "content_bytes_b64": base64.b64encode(b"relay artifact").decode("ascii"),
                }
            ],
            "state_patch": {"thread_id": "thread_001"},
            "client_trace_id": "task_001",
        },
        dispatch_metadata={
            "to_addr": "user@example.com",
            "subject": "[DONE][S:thread_001] Demo task",
            "in_reply_to": "<prev@example.com>",
            "references": ["<root@example.com>", "<prev@example.com>"],
            "headers": {"X-Mail-Runner": "1"},
        },
    )


def test_build_dispatch_request_from_packet_materializes_embedded_attachments(tmp_path) -> None:
    request = build_dispatch_request_from_packet(_packet(), state_dir=tmp_path)

    assert request.to_addr == "user@example.com"
    assert request.subject == "[DONE][S:thread_001] Demo task"
    assert len(request.packet.attachments) == 1
    attachment_path = request.packet.attachments[0].path
    assert attachment_path.endswith("result.txt")
    assert (tmp_path / "materialized_attachments").exists()
    assert request.packet.attachments[0].attach is True


def test_relay_packet_deliverer_reuses_email_transport_contract(tmp_path) -> None:
    fake_client = FakeMailClient()
    deliverer = RelayPacketDeliverer(
        RelayServerConfig(
            host="127.0.0.1",
            port=8787,
            transport_token="relay-secret",
            state_dir=str(tmp_path),
            smtp_host="smtp.example.com",
            smtp_user="bot@example.com",
            smtp_password="secret",
            from_addr="bot@example.com",
        ),
        mail_client=fake_client,
    )

    receipt = deliverer.deliver(_packet())

    assert receipt.success is True
    assert receipt.transport_message_id == "<relay-vps-sent@example.com>"
    assert fake_client.calls[0]["subject"] == "[DONE][S:thread_001] Demo task"
    assert fake_client.calls[0]["in_reply_to"] == "<prev@example.com>"
    assert len(fake_client.calls[0]["attachments"]) == 1
