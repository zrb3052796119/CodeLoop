from __future__ import annotations

import json

import pytest

import minicode.config as config_module
from minicode.config import load_runtime_config
from minicode.env_file import update_private_env_file
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.subagent_model_routing import (
    SubagentModelRoutingError,
    create_subagent_model_adapter,
    resolve_subagent_model_route,
)


def test_runtime_config_enables_qwen_subagents_when_dedicated_key_is_present(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "parent-key"},
        },
    )
    monkeypatch.setenv("MINI_CODE_SUBAGENT_API_KEY", "child-key")

    runtime = load_runtime_config()

    assert runtime["subagentRoutingEnabled"] is True
    assert runtime["subagentProvider"] == "openai-compatible"
    assert runtime["subagentBaseUrl"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert runtime["subagentApiKey"] == "child-key"
    assert runtime["subagentModels"] == {
        "default": "qwen3.6-flash",
        "explore": "qwen3.6-flash",
        "plan": "qwen3.6-flash",
        "general": "qwen3.6-flash",
    }


def test_runtime_config_supports_per_agent_model_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {
                "ANTHROPIC_API_KEY": "parent-key",
                "MINI_CODE_SUBAGENT_API_KEY": "child-key",
                "MINI_CODE_SUBAGENT_PLAN_MODEL": "qwen-plan-env",
            },
            "subagentRouting": {
                "models": {
                    "explore": "qwen-explore-settings",
                    "plan": "qwen-plan-settings",
                }
            },
        },
    )

    runtime = load_runtime_config()

    assert runtime["subagentModels"]["explore"] == "qwen-explore-settings"
    assert runtime["subagentModels"]["plan"] == "qwen-plan-env"
    assert runtime["subagentModels"]["general"] == "qwen3.6-flash"


def test_runtime_config_reads_child_route_from_private_user_env(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "parent-key"},
        },
    )
    monkeypatch.delenv("MINI_CODE_SUBAGENT_API_KEY", raising=False)
    user_env = tmp_path / ".mini-code" / ".env"
    monkeypatch.setattr(config_module, "MINI_CODE_ENV_PATH", user_env)
    update_private_env_file(
        user_env,
        {
            "MINI_CODE_SUBAGENT_API_KEY": "user-child-key",
            "MINI_CODE_SUBAGENT_BASE_URL": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "MINI_CODE_SUBAGENT_PLAN_MODEL": "qwen-plan-user",
        },
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["subagentRoutingEnabled"] is True
    assert runtime["subagentApiKey"] == "user-child-key"
    assert runtime["subagentModels"]["plan"] == "qwen-plan-user"


def test_runtime_config_ignores_dedicated_child_route_from_workspace_env(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "parent-key"},
        },
    )
    monkeypatch.delenv("MINI_CODE_SUBAGENT_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "MINI_CODE_SUBAGENT_API_KEY=workspace-child-key\n"
        "MINI_CODE_SUBAGENT_PLAN_MODEL=qwen-plan-from-file\n",
        encoding="utf-8",
    )

    runtime = load_runtime_config(tmp_path)

    assert runtime["subagentRoutingEnabled"] is False
    assert runtime["subagentApiKey"] == ""
    assert runtime["subagentModels"]["plan"] == "qwen3.6-flash"


def test_workspace_cannot_redirect_a_globally_owned_subagent_key(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {
                "ANTHROPIC_API_KEY": "parent-key",
                "MINI_CODE_SUBAGENT_API_KEY": "global-child-key",
            },
        },
    )
    monkeypatch.delenv("MINI_CODE_SUBAGENT_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "MINI_CODE_SUBAGENT_BASE_URL=https://attacker.invalid/v1\n",
        encoding="utf-8",
    )

    runtime = load_runtime_config(tmp_path)

    assert runtime["subagentApiKey"] == "global-child-key"
    assert runtime["subagentBaseUrl"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


def test_resolver_selects_the_model_for_each_agent_type_without_exposing_key() -> None:
    route = resolve_subagent_model_route(
        {
            "model": "parent-model",
            "subagentRoutingEnabled": True,
            "subagentProvider": "openai-compatible",
            "subagentBaseUrl": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "subagentApiKey": "private-child-key",
            "subagentModels": {
                "default": "qwen3.6-flash",
                "explore": "qwen3.6-flash",
                "plan": "qwen-plan-override",
            },
        },
        "plan",
    )

    assert route.enabled is True
    assert route.provider == "openai-compatible"
    assert route.model == "qwen-plan-override"
    assert route.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert route.api_key == "private-child-key"
    assert "private-child-key" not in repr(route)


def test_enabled_qwen_route_builds_an_isolated_openai_compatible_adapter() -> None:
    adapter = create_subagent_model_adapter(
        {
            "model": "parent-model",
            "openaiBaseUrl": "https://parent.invalid",
            "openaiApiKey": "parent-key",
            "subagentRoutingEnabled": True,
            "subagentProvider": "openai-compatible",
            "subagentBaseUrl": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "subagentApiKey": "child-key",
            "subagentModels": {"default": "qwen3.6-flash"},
        },
        "explore",
        tools=None,
    )

    assert isinstance(adapter, OpenAIModelAdapter)
    assert adapter.runtime["model"] == "qwen3.6-flash"
    assert adapter.runtime["openaiBaseUrl"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert adapter.runtime["openaiApiKey"] == "child-key"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://dashscope.invalid/v1",
        "https://user:password@dashscope.invalid/v1",
        "https://dashscope.invalid/v1?key=secret",
        "https://dashscope.invalid/v1#fragment",
    ],
)
def test_child_route_rejects_unsafe_credential_destinations(base_url) -> None:
    with pytest.raises(SubagentModelRoutingError, match="subagent_base_url_unsafe"):
        resolve_subagent_model_route(
            {
                "model": "parent-model",
                "subagentRoutingEnabled": True,
                "subagentProvider": "openai-compatible",
                "subagentBaseUrl": base_url,
                "subagentApiKey": "child-key",
                "subagentModels": {"default": "qwen3.6-flash"},
            },
            "explore",
        )


def test_qwen_adapter_uses_versioned_dashscope_url_despite_parent_openai_env(
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "done"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout=60, context=None):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        return Response()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://parent.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "parent-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = create_subagent_model_adapter(
        {
            "model": "parent-model",
            "subagentRoutingEnabled": True,
            "subagentProvider": "openai-compatible",
            "subagentBaseUrl": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "subagentApiKey": "child-key",
            "subagentModels": {"default": "qwen3.6-flash"},
            "modelMaxRetries": 0,
        },
        "explore",
        tools=None,
    )

    adapter.next([{"role": "user", "content": "inspect auth"}])

    assert captured["url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert captured["authorization"] == "Bearer child-key"
