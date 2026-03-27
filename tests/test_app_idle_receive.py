from __future__ import annotations

from pathlib import Path

from mail_runner.app import _process_batch, _sleep_with_runtime_control
from mail_runner.config import AppConfig


class _FakeRunner:
    def __init__(self) -> None:
        self.collect_calls = 0
        self.dispatch_calls = 0

    def collect_finished(self) -> None:
        self.collect_calls += 1

    def dispatch_ready(self) -> None:
        self.dispatch_calls += 1


class _FakeMailClient:
    def __init__(self, responses: list[bool]) -> None:
        self._responses = list(responses)
        self.timeouts: list[float] = []
        self.fetch_calls = 0

    def wait_for_new_messages(self, timeout_seconds: float) -> bool:
        self.timeouts.append(timeout_seconds)
        if self._responses:
            return self._responses.pop(0)
        return False

    def fetch_unseen_messages(self):
        self.fetch_calls += 1
        return []


class _FakeLeaseClient:
    def __init__(self, *, can_consume: bool) -> None:
        self._can_consume = can_consume

    def can_consume_mailbox(self) -> bool:
        return self._can_consume


def test_sleep_with_runtime_control_returns_early_when_mail_arrives(monkeypatch, tmp_path: Path) -> None:
    runner = _FakeRunner()
    client = _FakeMailClient([True])
    control_calls: list[Path | None] = []

    monkeypatch.setattr(
        "mail_runner.app._process_runtime_thread_kill_requests",
        lambda _runner, runtime_dir=None: control_calls.append(runtime_dir) or {"seen": 0, "accepted": 0, "ignored": 0, "invalid": 0},
    )

    woke_for_mail = _sleep_with_runtime_control(
        5.0,
        runner=runner,
        runtime_dir=tmp_path,
        mail_client=client,
    )

    assert woke_for_mail is True
    assert client.timeouts == [1.0]
    assert runner.collect_calls == 1
    assert runner.dispatch_calls == 1
    assert control_calls == [tmp_path]


def test_sleep_with_runtime_control_times_out_without_mail(monkeypatch, tmp_path: Path) -> None:
    runner = _FakeRunner()
    client = _FakeMailClient([False, False, False])
    control_calls: list[Path | None] = []

    monkeypatch.setattr(
        "mail_runner.app._process_runtime_thread_kill_requests",
        lambda _runner, runtime_dir=None: control_calls.append(runtime_dir) or {"seen": 0, "accepted": 0, "ignored": 0, "invalid": 0},
    )

    woke_for_mail = _sleep_with_runtime_control(
        2.5,
        runner=runner,
        runtime_dir=tmp_path,
        mail_client=client,
    )

    assert woke_for_mail is False
    assert client.timeouts == [1.0, 1.0, 0.5]
    assert runner.collect_calls == 3
    assert runner.dispatch_calls == 3
    assert control_calls == [tmp_path, tmp_path, tmp_path]


def test_process_batch_skips_mailbox_fetch_without_active_lease(tmp_path: Path) -> None:
    runner = _FakeRunner()
    client = _FakeMailClient([])

    stats = _process_batch(
        AppConfig(),
        tmp_path / "tasks",
        client,
        runner,
        background=False,
        pc_control_client=_FakeLeaseClient(can_consume=False),
    )

    assert stats == {"fetched": 0, "processed": 0, "skipped": 0, "failed": 0}
    assert client.fetch_calls == 0


def test_sleep_with_runtime_control_skips_mailbox_wait_when_mail_ingress_is_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = _FakeRunner()
    client = _FakeMailClient([True])
    control_calls: list[Path | None] = []

    monkeypatch.setattr(
        "mail_runner.app._process_runtime_thread_kill_requests",
        lambda _runner, runtime_dir=None: control_calls.append(runtime_dir) or {"seen": 0, "accepted": 0, "ignored": 0, "invalid": 0},
    )

    woke_for_mail = _sleep_with_runtime_control(
        1.5,
        runner=runner,
        runtime_dir=tmp_path,
        mail_client=client,
        mail_ingress_enabled=False,
    )

    assert woke_for_mail is False
    assert client.timeouts == []
    assert runner.collect_calls == 2
    assert runner.dispatch_calls == 2
    assert control_calls == [tmp_path, tmp_path]


def test_process_batch_skips_mailbox_fetch_in_vps_only_mode(tmp_path: Path) -> None:
    runner = _FakeRunner()
    client = _FakeMailClient([])

    stats = _process_batch(
        AppConfig(control_plane_mode="vps_only"),
        tmp_path / "tasks",
        client,
        runner,
        background=False,
        pc_control_client=None,
    )

    assert stats == {"fetched": 0, "processed": 0, "skipped": 0, "failed": 0}
    assert client.fetch_calls == 0
