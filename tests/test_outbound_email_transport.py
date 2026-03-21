from __future__ import annotations

from mail_runner.models import OutgoingAttachment
from mail_runner.outbound.contract import OutboundDispatchRequest, TaskRunPacket
from mail_runner.outbound.email_transport import EmailTransport


class FakeMailClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_mail(self, **kwargs):
        self.calls.append(kwargs)
        return "<email-transport@example.com>"


def test_email_transport_maps_dispatch_request_to_mail_client_kwargs() -> None:
    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:00:00",
        message_kind="status_update",
        content_format="text/plain+text/html",
        html="<html><body>Done.</body></html>",
        text_fallback="Status: DONE\n",
        attachments=[
            OutgoingAttachment(
                path="D:\\repo\\runs\\task_001\\report.txt",
                name="report.txt",
                content_type="text/plain",
            )
        ],
        state_patch={"thread_id": "thread_001"},
        client_trace_id="task_001",
    )
    request = OutboundDispatchRequest(
        packet=packet,
        to_addr="user@example.com",
        subject="[DONE][S:session_001] Demo task",
        in_reply_to="<done@example.com>",
        references=["<root@example.com>", "<done@example.com>"],
        headers={"X-Mail-Runner": "1"},
    )
    client = FakeMailClient()

    receipt = EmailTransport(client).send(request)

    assert receipt.success is True
    assert receipt.transport_name == "email"
    assert receipt.transport_message_id == "<email-transport@example.com>"
    assert receipt.error_message is None
    assert client.calls == [
        {
            "to_addr": "user@example.com",
            "subject": "[DONE][S:session_001] Demo task",
            "body": "Status: DONE\n",
            "attachments": packet.attachments,
            "in_reply_to": "<done@example.com>",
            "references": ["<root@example.com>", "<done@example.com>"],
            "headers": {"X-Mail-Runner": "1"},
            "html_body": "<html><body>Done.</body></html>",
        }
    ]


def test_email_transport_returns_failed_receipt_when_mail_client_raises() -> None:
    class FailingMailClient:
        def send_mail(self, **kwargs):
            raise RuntimeError("smtp down")

    packet = TaskRunPacket(
        packet_id="packet:task_001:test",
        task_id="task_001",
        created_at="2026-03-20T11:00:00",
        message_kind="status_update",
        content_format="text/plain+text/html",
        html="<html><body>Done.</body></html>",
        text_fallback="Status: DONE\n",
        state_patch={"thread_id": "thread_001"},
        client_trace_id="task_001",
    )
    request = OutboundDispatchRequest(
        packet=packet,
        to_addr="user@example.com",
        subject="[DONE][S:session_001] Demo task",
    )

    receipt = EmailTransport(FailingMailClient()).send(request)

    assert receipt.success is False
    assert receipt.transport_name == "email"
    assert receipt.transport_message_id is None
    assert receipt.error_message == "RuntimeError: smtp down"
