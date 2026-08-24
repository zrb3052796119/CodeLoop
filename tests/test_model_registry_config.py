from __future__ import annotations

import pytest

from minicode.model_registry import (
    Provider,
    build_provider_config,
    create_model_adapter,
    detect_provider,
    format_model_status,
)
from minicode.openai_adapter import OpenAIModelAdapter


def test_explicit_provider_is_not_changed_by_unrelated_process_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "unrelated-process-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://attacker.invalid/api")
    runtime = {
        "provider": "custom",
        "customBaseUrl": "https://api.deepseek.com",
        "customApiKey": "custom-key",
    }

    config = build_provider_config("deepseek-chat", runtime)

    assert config.provider == Provider.CUSTOM
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "custom-key"


def test_openrouter_provider_uses_frozen_runtime_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "conflicting-process-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://conflict.invalid/api")
    runtime = {
        "provider": "openrouter",
        "openrouterBaseUrl": "https://openrouter.ai/api",
        "openrouterApiKey": "runtime-key",
        "openrouterTitle": "Runtime title",
    }

    config = build_provider_config("openrouter/auto", runtime)

    assert config.base_url == "https://openrouter.ai/api"
    assert config.api_key == "runtime-key"
    assert config.extra_headers["X-Title"] == "Runtime title"


def test_provider_config_repr_does_not_contain_secret() -> None:
    config = build_provider_config(
        "gpt-4o",
        {
            "provider": "openai",
            "openaiBaseUrl": "https://api.openai.com",
            "openaiApiKey": "canary-private-key",
        },
    )

    assert "canary-private-key" not in repr(config)


def test_custom_provider_never_borrows_openai_key() -> None:
    config = build_provider_config(
        "deepseek-chat",
        {
            "provider": "custom",
            "customBaseUrl": "https://api.deepseek.com",
            "customApiKey": "",
            "openaiApiKey": "must-not-cross-provider-boundary",
        },
    )

    assert config.api_key == ""


def test_created_openai_adapter_ignores_later_process_env_changes(monkeypatch) -> None:
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    runtime = {
        "model": "gpt-4o",
        "provider": "openai",
        "openaiBaseUrl": "https://api.openai.com",
        "openaiApiKey": "frozen-runtime-key",
    }
    adapter = create_model_adapter("gpt-4o", tools=None, runtime=runtime)
    monkeypatch.setenv("OPENAI_API_KEY", "later-process-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://later.invalid/v1")

    assert isinstance(adapter, OpenAIModelAdapter)
    assert adapter.runtime["_isolatedOpenAIConfig"] is True
    assert adapter.runtime["openaiApiKey"] == "frozen-runtime-key"
    assert adapter.runtime["openaiBaseUrl"] == "https://api.openai.com"


def test_model_status_never_prints_key_suffix() -> None:
    status = format_model_status(
        "gpt-4o",
        {
            "provider": "openai",
            "openaiBaseUrl": "https://api.openai.com",
            "openaiApiKey": "canary-private-key-1234",
        },
    )

    assert "canary-private-key-1234" not in status
    assert "1234" not in status
    assert "API Key:  configured" in status


def test_invalid_explicit_provider_fails_closed() -> None:
    try:
        detect_provider("some-model", {"provider": "not-a-provider"})
    except RuntimeError as error:
        assert "Unsupported MINI_CODE_PROVIDER" in str(error)
    else:
        raise AssertionError("invalid provider must fail closed")


def test_mock_provider_is_not_a_user_configurable_transport() -> None:
    with pytest.raises(RuntimeError, match="Unsupported MINI_CODE_PROVIDER"):
        detect_provider("test-model", {"provider": "mock"})


@pytest.mark.parametrize(
    "base_url",
    [
        "http://remote.invalid/v1",
        "https://user:password@remote.invalid/v1",
        "https://remote.invalid/v1?token=secret",
        "https://remote.invalid/v1#fragment",
    ],
)
def test_provider_builder_rejects_unsafe_credential_destinations(base_url) -> None:
    with pytest.raises(RuntimeError, match="custom_base_url_unsafe"):
        build_provider_config(
            "custom-model",
            {
                "provider": "custom",
                "customBaseUrl": base_url,
                "customApiKey": "test-key",
            },
        )


def test_provider_builder_allows_loopback_http() -> None:
    config = build_provider_config(
        "local-model",
        {
            "provider": "custom",
            "customBaseUrl": "http://127.0.0.1:11434/v1",
            "customApiKey": "test-key",
        },
    )

    assert config.base_url == "http://127.0.0.1:11434/v1"
