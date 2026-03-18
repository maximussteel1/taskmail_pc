"""Phase 0 import smoke tests."""

from __future__ import annotations

import importlib


MODULES = [
    "mail_runner",
    "mail_runner.app",
    "mail_runner.config",
    "mail_runner.host",
    "mail_runner.host_lock",
    "mail_runner.host_state",
    "mail_runner.monitor_windows",
    "mail_runner.observe",
    "mail_runner.models",
    "mail_runner.mail_io",
    "mail_runner.mail_retention",
    "mail_runner.run_result_capsule",
    "mail_runner.stream_events",
    "mail_runner.mail_attachments",
    "mail_runner.artifact_resolver",
    "mail_runner.external_delivery",
    "mail_runner.health_semantics",
    "mail_runner.parser",
    "mail_runner.thread_store",
    "mail_runner.quote_extractor",
    "mail_runner.state_capsule",
    "mail_runner.context_layer",
    "mail_runner.intent_parser",
    "mail_runner.task_compiler",
    "mail_runner.dispatcher",
    "mail_runner.workspace",
    "mail_runner.reporter",
    "mail_runner.runner",
    "mail_runner.session_semantics",
    "mail_runner.status",
    "mail_runner.adapters",
    "mail_runner.adapters.base",
    "mail_runner.adapters.mock_adapter",
    "mail_runner.adapters.opencode_adapter",
    "mail_runner.adapters.codex_adapter",
    "mail_runner.adapters.codex_routing_adapter",
    "mail_runner.adapters.codex_sdk_adapter",
]


def test_all_modules_are_importable() -> None:
    for module_name in MODULES:
        assert importlib.import_module(module_name) is not None
