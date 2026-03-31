"""OpenCode SDK common helper tests."""

from __future__ import annotations

from types import SimpleNamespace

from mail_runner.opencode_sdk_common import resolve_profile_provider_model


def _providers_payload(*, defaults: dict[str, str], providers: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(default=defaults, providers=providers)


def _provider(provider_id: str, name: str, models: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(id=provider_id, name=name, models=models)


def test_resolve_profile_provider_model_prefers_coding_plan_default_when_unconfigured() -> None:
    payload = _providers_payload(
        defaults={
            "alibaba-cn": "tongyi-intent-detect-v3",
            "alibaba-coding-plan-cn": "qwen3.5-plus",
        },
        providers=[
            _provider("alibaba-cn", "Alibaba Cloud", {"tongyi-intent-detect-v3": object()}),
            _provider("alibaba-coding-plan-cn", "Alibaba Coding Plan (China)", {"qwen3.5-plus": object()}),
        ],
    )

    provider_id, model_id = resolve_profile_provider_model(payload, None)

    assert provider_id == "alibaba-coding-plan-cn"
    assert model_id == "qwen3.5-plus"


def test_resolve_profile_provider_model_keeps_plain_default_when_no_coding_plan_exists() -> None:
    payload = _providers_payload(
        defaults={"provider-alpha": "model-alpha"},
        providers=[_provider("provider-alpha", "Provider Alpha", {"model-alpha": object()})],
    )

    provider_id, model_id = resolve_profile_provider_model(payload, None)

    assert provider_id == "provider-alpha"
    assert model_id == "model-alpha"
