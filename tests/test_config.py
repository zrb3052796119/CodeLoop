from minicode.config import load_runtime_config, merge_settings, validate_provider_runtime


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


def test_runtime_config_defaults_reflection_synthesis_to_rule(monkeypatch) -> None:
    monkeypatch.setattr(
        "minicode.config.load_effective_settings",
        lambda cwd=None: {"model": "claude-test", "env": {"ANTHROPIC_API_KEY": "test"}},
    )

    runtime = load_runtime_config()

    assert runtime["reflectionSynthesizerMode"] == "rule"
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
