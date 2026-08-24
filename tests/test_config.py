import json
import os
from pathlib import Path

import pytest

import minicode.config as config_module
from minicode.config import (
    format_config_diagnostic,
    inspect_memory_hybrid_config,
    load_runtime_config,
    merge_settings,
    validate_provider_runtime,
)
from minicode.memory_hybrid import HybridActivation


_PRIMARY_ENV_NAMES = (
    "MINI_CODE_MODEL",
    "MINI_CODE_PROVIDER",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "CUSTOM_API_KEY",
    "CUSTOM_API_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_API_KEY",
)


def _isolated_config_paths(tmp_path: Path, monkeypatch) -> Path:
    mini_dir = tmp_path / ".mini-code"
    mini_dir.mkdir(mode=0o700)
    monkeypatch.setattr(config_module, "MINI_CODE_DIR", mini_dir)
    monkeypatch.setattr(config_module, "MINI_CODE_ENV_PATH", mini_dir / ".env")
    monkeypatch.setattr(
        config_module,
        "MINI_CODE_SETTINGS_PATH",
        mini_dir / "settings.json",
    )
    monkeypatch.setattr(config_module, "MINI_CODE_MCP_PATH", mini_dir / "mcp.json")
    monkeypatch.setattr(
        config_module,
        "CLAUDE_SETTINGS_PATH",
        tmp_path / ".claude" / "settings.json",
    )
    for name in _PRIMARY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return mini_dir


def _write_private_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def test_global_env_is_complete_primary_model_source(tmp_path, monkeypatch) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    (mini_dir / "settings.json").write_text(
        json.dumps(
            {
                "model": "claude-legacy",
                "env": {"ANTHROPIC_API_KEY": "legacy-anthropic-key"},
            }
        ),
        encoding="utf-8",
    )
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=deepseek-chat\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://api.deepseek.com\n"
        "CUSTOM_API_KEY=user-env-key\n",
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["model"] == "deepseek-chat"
    assert runtime["provider"] == "custom"
    assert runtime["customBaseUrl"] == "https://api.deepseek.com"
    assert runtime["customApiKey"] == "user-env-key"
    assert runtime["configSources"]["model"] == "user_env"
    assert runtime["configSources"]["credential"]["custom"] == "user_env"
    assert "CUSTOM_API_KEY" not in os.environ

    diagnostic = format_config_diagnostic(tmp_path / "workspace")
    assert "Provider: custom" in diagnostic
    assert "Base URL: https://api.deepseek.com" in diagnostic
    assert "user-env-key" not in diagnostic
    assert "Unknown model" not in diagnostic


def test_runtime_keeps_dedicated_deepseek_route_when_main_provider_is_qwen(
    tmp_path, monkeypatch
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=qwen3.6-flash\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://qwen.synthetic.invalid/v1\n"
        "CUSTOM_API_KEY=synthetic-qwen-key\n"
        "DEEPSEEK_API_KEY=synthetic-deepseek-key\n",
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["customBaseUrl"] == "https://qwen.synthetic.invalid/v1"
    assert runtime["customApiKey"] == "synthetic-qwen-key"
    assert runtime["deepseekBaseUrl"] == "https://api.deepseek.com"
    assert runtime["deepseekApiKey"] == "synthetic-deepseek-key"
    assert runtime["configSources"]["credential"]["deepseek"] == "user_env"


def test_official_deepseek_main_route_can_seed_the_dedicated_verifier_route(
    tmp_path, monkeypatch
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=deepseek-chat\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://api.deepseek.com\n"
        "CUSTOM_API_KEY=synthetic-deepseek-key\n",
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["deepseekBaseUrl"] == "https://api.deepseek.com"
    assert runtime["deepseekApiKey"] == "synthetic-deepseek-key"
    assert runtime["configSources"]["credential"]["deepseek"] == "user_env"


def test_official_deepseek_family_route_seeds_dedicated_chat_verifier(
    tmp_path, monkeypatch
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=deepseek-v4-pro\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://api.deepseek.com\n"
        "CUSTOM_API_KEY=synthetic-deepseek-family-key\n",
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["deepseekBaseUrl"] == "https://api.deepseek.com"
    assert runtime["deepseekApiKey"] == "synthetic-deepseek-family-key"
    assert runtime["configSources"]["credential"]["deepseek"] == "user_env"


@pytest.mark.parametrize(
    ("model", "endpoint"),
    [
        ("qwen3.6-flash", "https://api.deepseek.com"),
        ("deepseek-v4-pro", "https://qwen.synthetic.invalid/v1"),
    ],
)
def test_non_deepseek_or_nonofficial_route_never_seeds_dedicated_credential(
    tmp_path, monkeypatch, model: str, endpoint: str
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        f"MINI_CODE_MODEL={model}\n"
        "MINI_CODE_PROVIDER=custom\n"
        f"CUSTOM_API_BASE_URL={endpoint}\n"
        "CUSTOM_API_KEY=synthetic-main-key\n",
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["deepseekApiKey"] == ""
    assert runtime["configSources"]["credential"]["deepseek"] == "default"


def test_deepseek_family_real_config_hybrid_preflight_is_safe_and_green(
    tmp_path, monkeypatch
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    private_key = "synthetic-deepseek-preflight-key"
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=deepseek-v4-pro\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://api.deepseek.com\n"
        f"CUSTOM_API_KEY={private_key}\n"
        "MINI_CODE_MEMORY_HYBRID_ENABLED=true\n"
        "MINI_CODE_MEMORY_HYBRID_VERIFIER_MODEL=deepseek-chat\n",
    )
    runtime = load_runtime_config(tmp_path / "workspace")
    monkeypatch.setattr(
        "minicode.memory_hybrid.assess_hybrid_activation",
        lambda **_kwargs: HybridActivation(
            requested=True,
            active=True,
            reason="activated",
            evidence={"verifier": {"model_id": "deepseek-chat"}},
            embedding_provider="qwen",
        ),
    )

    errors, warnings, status = inspect_memory_hybrid_config(runtime)

    assert errors == []
    assert status["active"] is True
    assert private_key not in str((errors, warnings, status))


def test_process_environment_overrides_global_env_bundle(tmp_path, monkeypatch) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=deepseek-chat\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://api.deepseek.com\n"
        "CUSTOM_API_KEY=user-env-key\n",
    )
    monkeypatch.setenv("MINI_CODE_MODEL", "gpt-4o")
    monkeypatch.setenv("MINI_CODE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "process-openai-key")

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["model"] == "gpt-4o"
    assert runtime["provider"] == "openai"
    assert runtime["openaiBaseUrl"] == "https://api.openai.example/v1"
    assert runtime["openaiApiKey"] == "process-openai-key"
    assert runtime["configSources"]["model"] == "process_env"


def test_process_anthropic_token_beats_user_api_key_and_clears_loser(
    tmp_path,
    monkeypatch,
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=claude-sonnet-4-20250514\n"
        "MINI_CODE_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=user-env-key\n",
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "process-auth-token")

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["apiKey"] is None
    assert runtime["authToken"] == "process-auth-token"
    assert runtime["configSources"]["credential"]["anthropic"] == "process_env"


def test_workspace_env_cannot_redirect_primary_credentials(tmp_path, monkeypatch) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=gpt-4o\n"
        "MINI_CODE_PROVIDER=openai\n"
        "OPENAI_BASE_URL=https://api.openai.com\n"
        "OPENAI_API_KEY=user-openai-key\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text(
        "MINI_CODE_MODEL=attacker-model\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://attacker.invalid/v1\n"
        "CUSTOM_API_KEY=attacker-key\n",
        encoding="utf-8",
    )

    runtime = load_runtime_config(workspace)

    assert runtime["model"] == "gpt-4o"
    assert runtime["provider"] == "openai"
    assert runtime["openaiBaseUrl"] == "https://api.openai.com"
    assert runtime["openaiApiKey"] == "user-openai-key"


def test_legacy_settings_remain_a_primary_fallback(tmp_path, monkeypatch) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    (mini_dir / "settings.json").write_text(
        json.dumps(
            {
                "model": "deepseek-chat",
                "provider": "custom",
                "env": {
                    "CUSTOM_API_BASE_URL": "https://api.deepseek.com",
                    "CUSTOM_API_KEY": "legacy-custom-key",
                },
            }
        ),
        encoding="utf-8",
    )

    runtime = load_runtime_config(tmp_path / "workspace")

    assert runtime["model"] == "deepseek-chat"
    assert runtime["customApiKey"] == "legacy-custom-key"
    assert runtime["configSources"]["model"] == "legacy_settings"


def test_global_empty_custom_key_does_not_revive_legacy_secret(
    tmp_path,
    monkeypatch,
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    (mini_dir / "settings.json").write_text(
        json.dumps(
            {
                "model": "deepseek-chat",
                "provider": "custom",
                "env": {"CUSTOM_API_KEY": "legacy-key"},
            }
        ),
        encoding="utf-8",
    )
    _write_private_env(
        mini_dir / ".env",
        "MINI_CODE_MODEL=deepseek-chat\n"
        "MINI_CODE_PROVIDER=custom\n"
        "CUSTOM_API_BASE_URL=https://api.deepseek.com\n"
        "CUSTOM_API_KEY=\n",
    )

    with pytest.raises(RuntimeError, match="CUSTOM_API_KEY"):
        load_runtime_config(tmp_path / "workspace")


def test_migration_marker_prevents_claude_or_legacy_model_secret_revival(
    tmp_path,
    monkeypatch,
) -> None:
    mini_dir = _isolated_config_paths(tmp_path, monkeypatch)
    (mini_dir / "settings.json").write_text(
        json.dumps({"modelEnvMigrationVersion": 1}),
        encoding="utf-8",
    )
    claude_path = tmp_path / ".claude" / "settings.json"
    claude_path.parent.mkdir()
    claude_path.write_text(
        json.dumps(
            {
                "model": "claude-should-not-revive",
                "env": {"ANTHROPIC_API_KEY": "retired-secret"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="No model configured"):
        load_runtime_config(tmp_path / "workspace")


def test_merge_settings_merges_env_and_mcp_servers() -> None:
    merged = merge_settings(
        {
            "env": {"A": "1"},
            "mcpServers": {
                "fs": {"command": "npx", "args": ["a"], "env": {"X": "1"}}
            },
        },
        {
            "env": {"B": "2"},
            "mcpServers": {
                "fs": {"command": "uvx", "env": {"Y": "2"}},
                "search": {"command": "python"},
            },
        },
    )

    assert merged["env"] == {"A": "1", "B": "2"}
    assert merged["mcpServers"]["fs"]["command"] == "uvx"
    assert merged["mcpServers"]["fs"]["args"] == ["a"]
    assert merged["mcpServers"]["fs"]["env"] == {"X": "1", "Y": "2"}
    assert merged["mcpServers"]["search"]["command"] == "python"


def test_validate_provider_runtime_rejects_mismatched_provider_key() -> None:
    errors = validate_provider_runtime(
        {
            "model": "gpt-4o",
            "openaiApiKey": "",
            "apiKey": "anthropic-key-does-not-unlock-openai",
            "openaiBaseUrl": "https://api.openai.com",
        }
    )

    assert any("OPENAI_API_KEY" in error for error in errors)


def test_validate_provider_runtime_accepts_openrouter_prefixed_model() -> None:
    errors = validate_provider_runtime(
        {
            "model": "anthropic/claude-sonnet-4",
            "openrouterApiKey": "sk-or-test",
            "openrouterBaseUrl": "https://openrouter.ai/api",
        }
    )

    assert errors == []


def test_runtime_config_defaults_reflection_synthesis_to_shadow(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {"model": "claude-test", "env": {"ANTHROPIC_API_KEY": "test"}},
    )
    monkeypatch.delenv("MINI_CODE_REFLECTION_SYNTHESIZER_MODE", raising=False)

    runtime = load_runtime_config()

    # Shadow is the default: rule output stays durable while the LLM runs
    # alongside for comparison. Explicit rule stays honoured via env/config.
    assert runtime["reflectionSynthesizerMode"] == "llm_shadow"
    assert runtime["reflectionModel"] is None
    assert runtime["allowRemoteReflectionModel"] is False
    assert runtime["reflectionLLMTimeoutSeconds"] == 15.0
    assert runtime["reflectionLLMMaxOutputTokens"] == 1200
    assert runtime["reflectionLLMMaxInputBytes"] == 24_576
    assert runtime["reflectionLLMMaxClaims"] == 8
    assert runtime["reflectionShadowMetricsEnabled"] is False
    assert runtime["reflectionShadowMetricsPath"] is None
    assert runtime["reflectionShadowSampleRate"] == 1.0
    assert runtime["reflectionShadowMaxRecords"] == 5_000
    assert runtime["reflectionShadowMaxFileBytes"] == 5 * 1024 * 1024
    assert runtime["reflectionPromptVersion"] == "calibrated_compact"
    assert runtime["reflectionLLMSelectionStrategy"] == "gap_fill"


def test_runtime_config_reads_explicit_reflection_limits(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "test"},
            "reflectionSynthesizerMode": "llm_shadow",
            "reflectionModel": "local-reflector",
            "reflectionLLMTimeoutSeconds": 9,
            "reflectionLLMMaxOutputTokens": 700,
            "reflectionLLMMaxInputBytes": 12000,
            "reflectionLLMMaxOutputBytes": 18000,
            "reflectionLLMMaxClaims": 5,
            "reflectionShadowMetricsEnabled": True,
            "reflectionShadowMetricsPath": "/tmp/reflection-shadow.jsonl",
            "reflectionShadowSampleRate": 0.2,
            "reflectionShadowMaxRecords": 321,
            "reflectionShadowMaxFileBytes": 65536,
            "reflectionPromptVersion": "baseline",
            "reflectionLLMSelectionStrategy": "replace",
            "allowRemoteReflectionModel": True,
        },
    )

    runtime = load_runtime_config()

    assert runtime["reflectionSynthesizerMode"] == "llm_shadow"
    assert runtime["reflectionModel"] == "local-reflector"
    assert runtime["reflectionLLMTimeoutSeconds"] == 9.0
    assert runtime["reflectionLLMMaxOutputTokens"] == 700
    assert runtime["reflectionLLMMaxInputBytes"] == 12000
    assert runtime["reflectionLLMMaxOutputBytes"] == 18000
    assert runtime["reflectionLLMMaxClaims"] == 5
    assert runtime["reflectionShadowMetricsEnabled"] is True
    assert runtime["reflectionShadowMetricsPath"] == "/tmp/reflection-shadow.jsonl"
    assert runtime["reflectionShadowSampleRate"] == 0.2
    assert runtime["reflectionShadowMaxRecords"] == 321
    assert runtime["reflectionShadowMaxFileBytes"] == 65536
    assert runtime["reflectionPromptVersion"] == "baseline"
    assert runtime["reflectionLLMSelectionStrategy"] == "replace"
    assert runtime["allowRemoteReflectionModel"] is True


def test_runtime_config_reads_reflection_prompt_version_from_env(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {
                "ANTHROPIC_API_KEY": "test",
                "MINI_CODE_REFLECTION_PROMPT_VERSION": "baseline",
            },
            "reflectionPromptVersion": "calibrated",
        },
    )

    runtime = load_runtime_config()

    assert runtime["reflectionPromptVersion"] == "baseline"


def test_runtime_config_reads_shared_agent_turn_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "test"},
            "agentTurnBudget": {
                "maxTokens": 120000,
                "maxModelCalls": 12,
                "maxCostUsd": "2.50",
            },
        },
    )

    runtime = load_runtime_config()

    assert runtime["agentTurnBudget"]["maxTokens"] == 120000
    assert runtime["agentTurnBudget"]["maxModelCalls"] == 12
    assert runtime["agentTurnBudget"]["maxCostUsd"] == "2.50"


def test_runtime_config_reads_evidence_gated_memory_hybrid_settings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "test"},
            "memoryHybrid": {
                "enabled": True,
                "embeddingProvider": "qwen",
                "allowRemoteEmbedding": True,
                "modelPath": "/models/e5",
                "evidencePath": "/evidence/promotion.json",
                "verifierModel": "deepseek-chat",
            },
        },
    )

    runtime = load_runtime_config()

    assert runtime["memoryHybridEnabled"] is True
    assert runtime["memoryHybridEmbeddingProvider"] == "qwen"
    assert runtime["allowRemoteMemoryEmbedding"] is True
    assert runtime["memoryHybridModelPath"] == "/models/e5"
    assert runtime["memoryHybridEvidencePath"] == "/evidence/promotion.json"
    assert runtime["memoryHybridVerifierModel"] == "deepseek-chat"


def test_hybrid_config_uses_evidence_bound_verifier_and_surfaces_mismatch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.memory_hybrid.assess_hybrid_activation",
        lambda **_kwargs: HybridActivation(
            requested=True,
            active=True,
            reason="activated",
            evidence={"verifier": {"model_id": "deepseek-chat"}},
            embedding_provider="qwen",
        ),
    )

    errors, warnings, status = inspect_memory_hybrid_config(
        {
            "model": "deepseek-v4-pro",
            "memoryHybridEnabled": True,
            "memoryHybridVerifierModel": "deepseek-v4-pro",
            "provider": "custom",
            "customBaseUrl": "https://api.deepseek.com",
            "customApiKey": "configured-key",
            "deepseekApiKey": "configured-key",
        }
    )

    assert errors == []
    assert len(warnings) == 1
    assert "configured=deepseek-v4-pro" in warnings[0]
    assert "evidence=deepseek-chat" in warnings[0]
    assert "configured-key" not in str((errors, warnings, status))
    assert status["active"] is True
    assert status["verifierBinding"] == "evidence_bound_override"


def test_hybrid_config_rejects_main_qwen_credential_as_deepseek_verifier_route(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.memory_hybrid.assess_hybrid_activation",
        lambda **_kwargs: HybridActivation(
            requested=True,
            active=True,
            reason="activated",
            evidence={"verifier": {"model_id": "deepseek-chat"}},
            embedding_provider="qwen",
        ),
    )

    errors, _warnings, _status = inspect_memory_hybrid_config(
        {
            "model": "qwen3.6-flash",
            "memoryHybridEnabled": True,
            "provider": "custom",
            "customBaseUrl": "https://qwen.synthetic.invalid/v1",
            "customApiKey": "synthetic-qwen-key",
            "deepseekApiKey": "",
        }
    )

    assert errors == [
        "Hybrid Memory evidence-bound verifier credential is unavailable."
    ]


def test_hybrid_config_rejects_wrong_transport_even_when_model_label_matches(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.memory_hybrid.assess_hybrid_activation",
        lambda **_kwargs: HybridActivation(
            requested=True,
            active=True,
            reason="activated",
            evidence={"verifier": {"model_id": "deepseek-chat"}},
            embedding_provider="qwen",
        ),
    )

    errors, _warnings, _status = inspect_memory_hybrid_config(
        {
            "model": "deepseek-chat",
            "memoryHybridEnabled": True,
            "provider": "custom",
            "customBaseUrl": "https://qwen.synthetic.invalid/v1",
            "customApiKey": "synthetic-qwen-key",
            "deepseekApiKey": "",
        }
    )

    assert errors == [
        "Hybrid Memory evidence-bound verifier credential is unavailable."
    ]


def test_hybrid_config_validation_surfaces_promotion_activation_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.memory_hybrid.assess_hybrid_activation",
        lambda **_kwargs: HybridActivation(
            requested=True,
            active=False,
            reason="evidence_fingerprint_mismatch",
            embedding_provider="qwen",
        ),
    )

    errors, warnings, status = inspect_memory_hybrid_config(
        {"memoryHybridEnabled": True}
    )

    assert warnings == []
    assert errors == [
        "Hybrid Memory was requested but promotion activation failed "
        "(evidence_fingerprint_mismatch). Canonical lexical retrieval will be used."
    ]
    assert status == {
        "requested": True,
        "active": False,
        "reason": "evidence_fingerprint_mismatch",
        "verifierBinding": "activation_unavailable",
    }


def test_memory_hybrid_env_can_explicitly_disable_config(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {
                "ANTHROPIC_API_KEY": "test",
                "MINI_CODE_MEMORY_HYBRID_ENABLED": "false",
            },
            "memoryHybrid": {"enabled": True},
        },
    )

    runtime = load_runtime_config()

    assert runtime["memoryHybridEnabled"] is False


def test_qwen_hybrid_uses_qwen_evidence_default(tmp_path, monkeypatch) -> None:
    evidence = (
        tmp_path
        / "artifacts"
        / "memory-retrieval-hybrid-qwen-v1-production-evidence.json"
    )
    evidence.parent.mkdir()
    evidence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {"ANTHROPIC_API_KEY": "test"},
            "memoryHybrid": {
                "enabled": True,
                "embeddingProvider": "qwen",
                "allowRemoteEmbedding": True,
            },
        },
    )

    runtime = load_runtime_config(tmp_path)

    assert runtime["memoryHybridEvidencePath"] == str(evidence)


def test_remote_memory_embedding_env_overrides_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {
            "model": "claude-test",
            "env": {
                "ANTHROPIC_API_KEY": "test",
                "MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER": "qwen",
                "MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING": "true",
            },
            "memoryHybrid": {
                "embeddingProvider": "local-e5",
                "allowRemoteEmbedding": False,
            },
        },
    )

    runtime = load_runtime_config()

    assert runtime["memoryHybridEmbeddingProvider"] == "qwen"
    assert runtime["allowRemoteMemoryEmbedding"] is True


def test_agent_turn_budget_env_overrides_settings(monkeypatch) -> None:
    from minicode.agent_budget import AgentTurnBudget

    monkeypatch.setenv("MINI_CODE_TURN_BUDGET_TOKENS", "999")
    monkeypatch.setenv("MINI_CODE_TURN_BUDGET_MODEL_CALLS", "7")

    budget = AgentTurnBudget.from_runtime(
        {
            "agentTurnBudget": {
                "maxTokens": "120000",
                "maxModelCalls": "12",
            }
        }
    )

    assert budget.max_total_tokens == 999
    assert budget.max_model_calls == 7
