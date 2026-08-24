from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


MINI_CODE_DIR = Path.home() / ".mini-code"
MINI_CODE_ENV_PATH = MINI_CODE_DIR / ".env"
MINI_CODE_SETTINGS_PATH = MINI_CODE_DIR / "settings.json"
MINI_CODE_HISTORY_PATH = MINI_CODE_DIR / "history.json"
MINI_CODE_PERMISSIONS_PATH = MINI_CODE_DIR / "permissions.json"
MINI_CODE_MCP_PATH = MINI_CODE_DIR / "mcp.json"
MINI_CODE_USER_PROFILE_PATH = MINI_CODE_DIR / "USER.md"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Only these model/runtime variables are accepted from the private user env
# store.  This prevents a typo or an unrelated shell variable in that file
# from silently becoming authority for subprocess behaviour.
USER_MODEL_ENV_KEYS = frozenset(
    {
        "MINI_CODE_MODEL",
        "MINI_CODE_PROVIDER",
        "MINI_CODE_MAX_OUTPUT_TOKENS",
        "MINI_CODE_LANGUAGE",
        "MINI_CODE_VERBOSITY",
        "MINI_CODE_TOOL_PROFILE",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_REFERER",
        "OPENROUTER_TITLE",
        "OPENROUTER_TRANSFORMS",
        "CUSTOM_API_BASE_URL",
        "CUSTOM_API_KEY",
        "CUSTOM_API_EXTRA_HEADERS",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "MINI_CODE_REFLECTION_SYNTHESIZER_MODE",
        "MINI_CODE_REFLECTION_MODEL",
        "MINI_CODE_REFLECTION_LLM_TIMEOUT_SECONDS",
        "MINI_CODE_REFLECTION_LLM_MAX_OUTPUT_TOKENS",
        "MINI_CODE_REFLECTION_LLM_MAX_INPUT_BYTES",
        "MINI_CODE_REFLECTION_LLM_MAX_OUTPUT_BYTES",
        "MINI_CODE_REFLECTION_LLM_MAX_CLAIMS",
        "MINI_CODE_ALLOW_REMOTE_REFLECTION_MODEL",
        "MINI_CODE_REFLECTION_SHADOW_METRICS_ENABLED",
        "MINI_CODE_REFLECTION_SHADOW_METRICS_PATH",
        "MINI_CODE_REFLECTION_SHADOW_SAMPLE_RATE",
        "MINI_CODE_REFLECTION_SHADOW_MAX_RECORDS",
        "MINI_CODE_REFLECTION_SHADOW_MAX_FILE_BYTES",
        "MINI_CODE_REFLECTION_PROMPT_VERSION",
        "MINI_CODE_REFLECTION_LLM_SELECTION_STRATEGY",
        "MINI_CODE_TURN_BUDGET_TOKENS",
        "MINI_CODE_TURN_BUDGET_MODEL_CALLS",
        "MINI_CODE_TURN_BUDGET_COST_USD",
        "MINI_CODE_SUBAGENT_ROUTING_ENABLED",
        "MINI_CODE_SUBAGENT_PROVIDER",
        "MINI_CODE_SUBAGENT_BASE_URL",
        "MINI_CODE_SUBAGENT_API_KEY",
        "MINI_CODE_SUBAGENT_MODEL",
        "MINI_CODE_SUBAGENT_EXPLORE_MODEL",
        "MINI_CODE_SUBAGENT_PLAN_MODEL",
        "MINI_CODE_SUBAGENT_GENERAL_MODEL",
        "MINI_CODE_MEMORY_HYBRID_ENABLED",
        "MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER",
        "MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING",
        "MINI_CODE_MEMORY_HYBRID_MODEL_PATH",
        "MINI_CODE_MEMORY_HYBRID_EVIDENCE_PATH",
        "MINI_CODE_MEMORY_HYBRID_VERIFIER_MODEL",
        "MINICODE_EMBEDDING_API_KEY",
        "MINICODE_EMBEDDING_BASE_URL",
        "MINICODE_EMBEDDING_MODEL",
        "MINICODE_EMBEDDING_TIMEOUT_SECONDS",
        "MINICODE_EMBEDDING_BOOST_THRESHOLD",
        "MINICODE_EMBEDDING_SIGNAL_THRESHOLD",
        "MINICODE_EMBEDDING_SIGNAL_THRESHOLD_UNKNOWN",
    }
)


def project_user_profile_path(cwd: str | Path | None = None) -> Path:
    """Return the project-level USER.md path."""
    return Path(cwd or Path.cwd()) / ".mini-code" / "USER.md"

# 已知的合法模型名称（用于拼写检查提示）
KNOWN_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-haiku-3-20240307",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "o1",
    "o1-mini",
    "o3-mini",
    # OpenRouter popular models
    "openrouter/auto",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-opus-4",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "meta-llama/llama-4-maverick",
    "deepseek/deepseek-r1",
    "deepseek/deepseek-chat",
    "deepseek-chat",
    "qwen/qwen3-235b-a22b",
    "minimax/minimax-m1",
]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _suggest_model_name(typed: str) -> str:
    """根据输入建议最接近的合法模型名称"""
    if not typed:
        return ""
    
    # 简单的前缀匹配
    for model in KNOWN_MODELS:
        if model.startswith(typed.lower()):
            return model
    
    # 模糊匹配：包含输入字符的模型
    for model in KNOWN_MODELS:
        if typed.lower() in model:
            return model
    
    return ""


def project_mcp_path(cwd: str | Path | None = None) -> Path:
    return Path(cwd or Path.cwd()) / ".mcp.json"


def _read_json_file(file_path: Path) -> dict[str, Any]:
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def read_settings_file(file_path: Path) -> dict[str, Any]:
    return _read_json_file(file_path)


def read_mcp_config_file(file_path: Path) -> dict[str, Any]:
    parsed = _read_json_file(file_path)
    if not isinstance(parsed, dict):
        return {}
    mcp_servers = parsed.get("mcpServers", {})
    return mcp_servers if isinstance(mcp_servers, dict) else {}


def merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged_mcp = dict(base.get("mcpServers", {}))
    for name, server in override.get("mcpServers", {}).items():
        current = dict(merged_mcp.get(name, {}))
        next_server = dict(server)
        current.update(next_server)
        current["env"] = {
            **dict(merged_mcp.get(name, {}).get("env", {})),
            **dict(next_server.get("env", {})),
        }
        merged_mcp[name] = current

    return {
        **base,
        **override,
        "env": {
            **dict(base.get("env", {})),
            **dict(override.get("env", {})),
        },
        "mcpServers": merged_mcp,
    }


def load_effective_settings(cwd: str | Path | None = None) -> dict[str, Any]:
    claude_settings = read_settings_file(CLAUDE_SETTINGS_PATH)
    global_mcp = read_mcp_config_file(MINI_CODE_MCP_PATH)
    project_mcp = read_mcp_config_file(project_mcp_path(cwd))
    mini_code_settings = read_settings_file(MINI_CODE_SETTINGS_PATH)

    return merge_settings(
        merge_settings(
            merge_settings(claude_settings, {"mcpServers": global_mcp}),
            {"mcpServers": project_mcp},
        ),
        mini_code_settings,
    )


def save_mini_code_settings(updates: dict[str, Any]) -> None:
    import tempfile

    MINI_CODE_DIR.mkdir(parents=True, exist_ok=True)
    existing = read_settings_file(MINI_CODE_SETTINGS_PATH)
    next_settings = merge_settings(existing, updates)
    # The settings file may contain API keys (env section): write atomically
    # and owner-only instead of a default-0644 write_text.
    # mkstemp creates the file 0600, which os.replace preserves.
    fd, tmp_path = tempfile.mkstemp(dir=MINI_CODE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(next_settings, file, indent=2)
            file.write("\n")
        os.replace(tmp_path, MINI_CODE_SETTINGS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _resolve_env_value(
    names: tuple[str, ...],
    *,
    process_env: Mapping[str, Any],
    user_env: Mapping[str, Any],
    legacy_env: Mapping[str, Any],
) -> tuple[str, str]:
    """Resolve aliases by source, preserving explicit empty tombstones."""
    for source_name, source in (
        ("process_env", process_env),
        ("user_env", user_env),
        ("legacy_settings", legacy_env),
    ):
        for name in names:
            if name in source:
                return str(source[name]).strip(), source_name
    return "", "default"


def _resolve_anthropic_credential(
    *,
    process_env: Mapping[str, Any],
    user_env: Mapping[str, Any],
    legacy_env: Mapping[str, Any],
    effective: Mapping[str, Any],
    legacy_fallback_enabled: bool,
) -> tuple[str | None, str | None, str]:
    """Select exactly one Anthropic credential by source precedence.

    API keys and auth tokens are alternative authentication methods, so they
    must compete at the source level rather than being resolved independently.
    Within one source an API key wins when both are populated. An explicitly
    empty credential source remains a tombstone for all lower-priority sources.
    """
    for source_name, source in (
        ("process_env", process_env),
        ("user_env", user_env),
        ("legacy_settings", legacy_env),
    ):
        key_present = "ANTHROPIC_API_KEY" in source
        token_present = "ANTHROPIC_AUTH_TOKEN" in source
        if not (key_present or token_present):
            continue
        api_key = str(source.get("ANTHROPIC_API_KEY") or "").strip()
        auth_token = str(source.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        if api_key:
            return api_key, None, source_name
        if auth_token:
            return None, auth_token, source_name
        return None, None, source_name

    if legacy_fallback_enabled:
        key_present = "apiKey" in effective
        token_present = "authToken" in effective
        if key_present or token_present:
            api_key = str(effective.get("apiKey") or "").strip()
            auth_token = str(effective.get("authToken") or "").strip()
            if api_key:
                return api_key, None, "legacy_settings"
            if auth_token:
                return None, auth_token, "legacy_settings"
            return None, None, "legacy_settings"
    return None, None, "default"


def load_runtime_config(cwd: str | Path | None = None) -> dict[str, Any]:
    effective = load_effective_settings(cwd)
    mini_code_settings = read_settings_file(MINI_CODE_SETTINGS_PATH)
    try:
        model_env_migration_version = int(
            mini_code_settings.get("modelEnvMigrationVersion", 0) or 0
        )
    except (TypeError, ValueError):
        model_env_migration_version = 0
    legacy_model_fallback_enabled = model_env_migration_version < 1
    raw_settings_env = effective.get("env", {})
    settings_env = (
        dict(raw_settings_env)
        if legacy_model_fallback_enabled and isinstance(raw_settings_env, Mapping)
        else {}
    )
    from minicode.env_file import read_private_env_file

    user_env = read_private_env_file(
        MINI_CODE_ENV_PATH,
        allowed_keys=USER_MODEL_ENV_KEYS,
    )
    process_env: Mapping[str, Any] = os.environ
    env = {**settings_env, **user_env, **process_env}

    def resolved(
        *names: str,
        legacy_field: str | None = None,
        default: Any = "",
    ) -> tuple[Any, str]:
        value, source = _resolve_env_value(
            tuple(names),
            process_env=process_env,
            user_env=user_env,
            legacy_env=settings_env,
        )
        if source != "default":
            return value, source
        if (
            legacy_model_fallback_enabled
            and legacy_field is not None
            and legacy_field in effective
        ):
            return effective.get(legacy_field), "legacy_settings"
        return default, "default"

    model, model_source = resolved(
        "MINI_CODE_MODEL",
        "ANTHROPIC_MODEL",
        legacy_field="model",
    )
    model = str(model or "").strip()
    provider, provider_source = resolved(
        "MINI_CODE_PROVIDER",
        legacy_field="provider",
    )
    provider = str(provider or "").strip().lower()

    # --- Provider-specific base URLs ---
    # Anthropic
    base_url, _ = resolved(
        "ANTHROPIC_BASE_URL",
        legacy_field="baseUrl",
        default="https://api.anthropic.com",
    )
    base_url = str(base_url or "https://api.anthropic.com").strip()
    api_key, auth_token, anthropic_credential_source = (
        _resolve_anthropic_credential(
            process_env=process_env,
            user_env=user_env,
            legacy_env=settings_env,
            effective=effective,
            legacy_fallback_enabled=legacy_model_fallback_enabled,
        )
    )

    # OpenAI
    openai_base_url, _ = resolved(
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        legacy_field="openaiBaseUrl",
        default="https://api.openai.com",
    )
    openai_base_url = str(openai_base_url or "https://api.openai.com").strip()
    openai_api_key, openai_key_source = resolved(
        "OPENAI_API_KEY",
        legacy_field="openaiApiKey",
    )
    openai_api_key = str(openai_api_key).strip()

    # OpenRouter
    openrouter_base_url, _ = resolved(
        "OPENROUTER_BASE_URL",
        legacy_field="openrouterBaseUrl",
        default="https://openrouter.ai/api",
    )
    openrouter_base_url = str(
        openrouter_base_url or "https://openrouter.ai/api"
    ).strip()
    openrouter_api_key, openrouter_key_source = resolved(
        "OPENROUTER_API_KEY",
        legacy_field="openrouterApiKey",
    )
    openrouter_api_key = str(openrouter_api_key).strip()
    openrouter_referer, _ = resolved(
        "OPENROUTER_REFERER",
        legacy_field="openrouterReferer",
        default="https://github.com/minicode-py",
    )
    openrouter_title, _ = resolved(
        "OPENROUTER_TITLE",
        legacy_field="openrouterTitle",
        default="MiniCode Python",
    )
    openrouter_transforms, _ = resolved(
        "OPENROUTER_TRANSFORMS",
        legacy_field="openrouterTransforms",
    )

    # Custom endpoint
    custom_base_url, _ = resolved(
        "CUSTOM_API_BASE_URL",
        legacy_field="customBaseUrl",
    )
    custom_base_url = str(custom_base_url).strip()
    custom_api_key, custom_key_source = resolved(
        "CUSTOM_API_KEY",
        legacy_field="customApiKey",
    )
    custom_api_key = str(custom_api_key).strip()
    custom_extra_headers, _ = resolved(
        "CUSTOM_API_EXTRA_HEADERS",
        legacy_field="customApiExtraHeaders",
    )

    # Compatibility for direct DeepSeek configuration is deliberately bound
    # to DeepSeek's own endpoint; it is never lent to an arbitrary custom URL.
    deepseek_base_url, _ = resolved(
        "DEEPSEEK_BASE_URL",
        default="https://api.deepseek.com",
    )
    deepseek_base_url = str(
        deepseek_base_url or "https://api.deepseek.com"
    ).strip().rstrip("/")
    deepseek_api_key, deepseek_key_source = resolved("DEEPSEEK_API_KEY")
    deepseek_api_key = str(deepseek_api_key).strip()
    if (
        not deepseek_api_key
        and "deepseek" in model.lower()
        and provider == "custom"
        and custom_base_url.rstrip("/")
        in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
        and custom_api_key
    ):
        # A primary DeepSeek route may seed the isolated verifier route only
        # after both its model and official endpoint have been proven.  A main
        # Qwen/custom credential never crosses this boundary.
        deepseek_base_url = custom_base_url.rstrip("/")
        deepseek_api_key = custom_api_key
        deepseek_key_source = custom_key_source
    if (
        not custom_api_key
        and deepseek_api_key
        and "deepseek" in model.lower()
        and custom_base_url.rstrip("/") in {"", "https://api.deepseek.com", "https://api.deepseek.com/v1"}
    ):
        custom_base_url = custom_base_url or "https://api.deepseek.com"
        custom_api_key = deepseek_api_key
        custom_key_source = deepseek_key_source

    raw_max_output_tokens, _ = resolved(
        "MINI_CODE_MAX_OUTPUT_TOKENS",
        legacy_field="maxOutputTokens",
    )
    max_output_tokens = None
    if raw_max_output_tokens is not None:
        try:
            parsed = int(raw_max_output_tokens)
            if parsed > 0:
                max_output_tokens = parsed
        except (TypeError, ValueError):
            max_output_tokens = None

    if not model:
        raise RuntimeError(
            "No model configured. Set MINI_CODE_MODEL in ~/.mini-code/.env."
        )

    # --- User profile paths ---
    global_user_profile = MINI_CODE_USER_PROFILE_PATH
    proj_user_profile = project_user_profile_path(cwd)

    # --- User preferences from settings (lightweight, not from USER.md) ---
    user_preferences = effective.get("userPreferences", {})
    response_language = (
        str(env.get("MINI_CODE_LANGUAGE", "")).strip()
        or user_preferences.get("language", "")
    )
    response_verbosity = (
        str(env.get("MINI_CODE_VERBOSITY", "")).strip()
        or user_preferences.get("verbosity", "")
    )

    reflection_values = dict(effective) if legacy_model_fallback_enabled else {}
    reflection_env_fields = {
        "reflectionSynthesizerMode": "MINI_CODE_REFLECTION_SYNTHESIZER_MODE",
        "reflectionModel": "MINI_CODE_REFLECTION_MODEL",
        "reflectionLLMTimeoutSeconds": "MINI_CODE_REFLECTION_LLM_TIMEOUT_SECONDS",
        "reflectionLLMMaxOutputTokens": "MINI_CODE_REFLECTION_LLM_MAX_OUTPUT_TOKENS",
        "reflectionLLMMaxInputBytes": "MINI_CODE_REFLECTION_LLM_MAX_INPUT_BYTES",
        "reflectionLLMMaxOutputBytes": "MINI_CODE_REFLECTION_LLM_MAX_OUTPUT_BYTES",
        "reflectionLLMMaxClaims": "MINI_CODE_REFLECTION_LLM_MAX_CLAIMS",
        "allowRemoteReflectionModel": "MINI_CODE_ALLOW_REMOTE_REFLECTION_MODEL",
        "reflectionShadowMetricsEnabled": "MINI_CODE_REFLECTION_SHADOW_METRICS_ENABLED",
        "reflectionShadowMetricsPath": "MINI_CODE_REFLECTION_SHADOW_METRICS_PATH",
        "reflectionShadowSampleRate": "MINI_CODE_REFLECTION_SHADOW_SAMPLE_RATE",
        "reflectionShadowMaxRecords": "MINI_CODE_REFLECTION_SHADOW_MAX_RECORDS",
        "reflectionShadowMaxFileBytes": "MINI_CODE_REFLECTION_SHADOW_MAX_FILE_BYTES",
        "reflectionPromptVersion": "MINI_CODE_REFLECTION_PROMPT_VERSION",
        "reflectionLLMSelectionStrategy": "MINI_CODE_REFLECTION_LLM_SELECTION_STRATEGY",
    }
    for field_name, env_name in reflection_env_fields.items():
        if env.get(env_name) not in (None, ""):
            reflection_values[field_name] = env[env_name]
    from minicode.reflection_llm import ReflectionLLMConfig

    reflection_config = ReflectionLLMConfig.from_runtime(reflection_values)

    # Shared Agent Turn budget, inherited by every nested `task` sub-agent.
    # Values are optional; a default model-call ceiling is applied by
    # AgentTurnBudget.from_runtime even when token/cost limits are unset.
    budget_settings = (
        effective.get("agentTurnBudget", {})
        if legacy_model_fallback_enabled
        else {}
    )
    if not isinstance(budget_settings, dict):
        budget_settings = {}
    agent_turn_budget = {
        "maxTokens": env.get("MINI_CODE_TURN_BUDGET_TOKENS")
        or budget_settings.get("maxTokens"),
        "maxModelCalls": env.get("MINI_CODE_TURN_BUDGET_MODEL_CALLS")
        or budget_settings.get("maxModelCalls"),
        "maxCostUsd": env.get("MINI_CODE_TURN_BUDGET_COST_USD")
        or budget_settings.get("maxCostUsd"),
    }

    # Child-agent model routing is independent from the parent provider. The
    # dedicated key is never inferred from the parent credential: adding it is
    # the explicit act that enables the default remote Qwen route.
    subagent_settings = (
        effective.get("subagentRouting", {})
        if legacy_model_fallback_enabled
        else {}
    )
    if not isinstance(subagent_settings, dict):
        subagent_settings = {}
    def subagent_env_value(name: str) -> Any:
        # A repository is data processed by the agent, not a credential
        # authority.  Child routes therefore use the same trusted sources as
        # the parent: process env, private user env, then legacy settings.
        value, _ = resolved(name)
        return value

    configured_subagent_models = subagent_settings.get("models", {})
    if not isinstance(configured_subagent_models, dict):
        configured_subagent_models = {}
    subagent_api_key = str(
        subagent_env_value("MINI_CODE_SUBAGENT_API_KEY")
        or subagent_settings.get("apiKey")
        or ""
    ).strip()
    subagent_enabled_value = (
        subagent_env_value("MINI_CODE_SUBAGENT_ROUTING_ENABLED")
        if subagent_env_value("MINI_CODE_SUBAGENT_ROUTING_ENABLED")
        not in (None, "")
        else subagent_settings.get("enabled", bool(subagent_api_key))
    )
    default_subagent_model = str(
        subagent_env_value("MINI_CODE_SUBAGENT_MODEL")
        or subagent_settings.get("defaultModel")
        or configured_subagent_models.get("default")
        or "qwen3.6-flash"
    ).strip()
    subagent_models = {"default": default_subagent_model}
    # A workflow is an orchestrator, not a model-bearing agent. Its actual
    # explore/plan/general phases resolve their own role-specific models.
    for agent_type in ("explore", "plan", "general"):
        env_name = f"MINI_CODE_SUBAGENT_{agent_type.upper()}_MODEL"
        subagent_models[agent_type] = str(
            subagent_env_value(env_name)
            or configured_subagent_models.get(agent_type)
            or default_subagent_model
        ).strip()

    hybrid_settings = (
        effective.get("memoryHybrid", {})
        if legacy_model_fallback_enabled
        else {}
    )
    if not isinstance(hybrid_settings, dict):
        hybrid_settings = {}
    enabled_value = (
        env["MINI_CODE_MEMORY_HYBRID_ENABLED"]
        if env.get("MINI_CODE_MEMORY_HYBRID_ENABLED") not in (None, "")
        else hybrid_settings.get("enabled", False)
    )
    memory_hybrid_embedding_provider = str(
        env.get("MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER")
        or hybrid_settings.get("embeddingProvider")
        or "local-e5"
    ).strip().lower()
    allow_remote_memory_embedding_value = (
        env["MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING"]
        if env.get("MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING") not in (None, "")
        else hybrid_settings.get("allowRemoteEmbedding", False)
    )
    default_hybrid_evidence_name = (
        "memory-retrieval-hybrid-qwen-v1-production-evidence.json"
        if memory_hybrid_embedding_provider == "qwen"
        else "memory-retrieval-hybrid-v4-production-evidence.json"
    )
    default_hybrid_evidence = (
        Path(cwd or Path.cwd()) / "artifacts" / default_hybrid_evidence_name
    )
    memory_hybrid_model_path = str(
        env.get("MINI_CODE_MEMORY_HYBRID_MODEL_PATH")
        or hybrid_settings.get("modelPath")
        or ""
    ).strip()
    memory_hybrid_evidence_path = str(
        env.get("MINI_CODE_MEMORY_HYBRID_EVIDENCE_PATH")
        or hybrid_settings.get("evidencePath")
        or (default_hybrid_evidence if default_hybrid_evidence.is_file() else "")
    ).strip()
    memory_hybrid_verifier_model = str(
        env.get("MINI_CODE_MEMORY_HYBRID_VERIFIER_MODEL")
        or hybrid_settings.get("verifierModel")
        or model
    ).strip()

    credential_sources = {
        "anthropic": anthropic_credential_source,
        "openai": openai_key_source,
        "openrouter": openrouter_key_source,
        "custom": custom_key_source,
        "deepseek": deepseek_key_source,
    }
    runtime = {
        "model": model,
        "provider": provider,
        "baseUrl": base_url,
        "authToken": auth_token,
        "apiKey": api_key,
        "openaiBaseUrl": openai_base_url,
        "openaiApiKey": openai_api_key,
        "openrouterBaseUrl": openrouter_base_url,
        "openrouterApiKey": openrouter_api_key,
        "openrouterReferer": str(openrouter_referer or ""),
        "openrouterTitle": str(openrouter_title or ""),
        "openrouterTransforms": str(openrouter_transforms or ""),
        "customBaseUrl": custom_base_url,
        "customApiKey": custom_api_key,
        "customApiExtraHeaders": str(custom_extra_headers or ""),
        "deepseekBaseUrl": deepseek_base_url,
        "deepseekApiKey": deepseek_api_key,
        "maxOutputTokens": max_output_tokens,
        "mcpServers": effective.get("mcpServers", {}),
        "globalUserProfilePath": str(global_user_profile),
        "projectUserProfilePath": str(proj_user_profile),
        "responseLanguage": response_language,
        "responseVerbosity": response_verbosity,
        "reflectionSynthesizerMode": reflection_config.mode,
        "reflectionModel": reflection_config.model,
        "reflectionLLMTimeoutSeconds": reflection_config.timeout_seconds,
        "reflectionLLMMaxOutputTokens": reflection_config.max_output_tokens,
        "reflectionLLMMaxInputBytes": reflection_config.max_input_bytes,
        "reflectionLLMMaxOutputBytes": reflection_config.max_output_bytes,
        "reflectionLLMMaxClaims": reflection_config.max_claims,
        "allowRemoteReflectionModel": reflection_config.allow_remote_model,
        "reflectionShadowMetricsEnabled": reflection_config.shadow_metrics_enabled,
        "reflectionShadowMetricsPath": reflection_config.shadow_metrics_path,
        "reflectionShadowSampleRate": reflection_config.shadow_sample_rate,
        "reflectionShadowMaxRecords": reflection_config.shadow_max_records,
        "reflectionShadowMaxFileBytes": reflection_config.shadow_max_file_bytes,
        "reflectionPromptVersion": reflection_config.prompt_version,
        "reflectionLLMSelectionStrategy": reflection_config.selection_strategy,
        "agentTurnBudget": agent_turn_budget,
        "subagentRoutingEnabled": _as_bool(subagent_enabled_value),
        "subagentProvider": str(
            subagent_env_value("MINI_CODE_SUBAGENT_PROVIDER")
            or subagent_settings.get("provider")
            or "openai-compatible"
        ).strip().lower(),
        "subagentBaseUrl": str(
            subagent_env_value("MINI_CODE_SUBAGENT_BASE_URL")
            or subagent_settings.get("baseUrl")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip().rstrip("/"),
        "subagentApiKey": subagent_api_key,
        "subagentModels": subagent_models,
        "memoryHybridEnabled": _as_bool(enabled_value),
        "memoryHybridEmbeddingProvider": memory_hybrid_embedding_provider,
        "allowRemoteMemoryEmbedding": _as_bool(
            allow_remote_memory_embedding_value
        ),
        "memoryHybridModelPath": memory_hybrid_model_path,
        "memoryHybridEvidencePath": memory_hybrid_evidence_path,
        "memoryHybridVerifierModel": memory_hybrid_verifier_model,
        "toolProfile": str(
            env.get("MINI_CODE_TOOL_PROFILE")
            or effective.get("toolProfile", "")
            or "core"
        ).strip().lower(),
        "configSources": {
            "model": model_source,
            "provider": provider_source,
            "credential": credential_sources,
            "legacyModelFallbackEnabled": legacy_model_fallback_enabled,
        },
        "sourceSummary": (
            (
                f"model config: process.env > {MINI_CODE_ENV_PATH} > "
                f"{MINI_CODE_SETTINGS_PATH} > {CLAUDE_SETTINGS_PATH}"
            )
            if legacy_model_fallback_enabled
            else (
                f"model config: process.env > {MINI_CODE_ENV_PATH} "
                "(legacy model fallback disabled after migration)"
            )
        ),
    }
    provider_errors = validate_provider_runtime(runtime)
    if provider_errors:
        raise RuntimeError(" ".join(provider_errors))
    return runtime


def _is_valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value))
    if not parsed.netloc or parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def safe_runtime_summary(runtime: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicit, credential-free runtime diagnostic projection."""
    from minicode.agent_budget import AgentTurnBudget

    values = runtime if isinstance(runtime, Mapping) else {}
    budget = AgentTurnBudget.from_runtime(values).snapshot()
    subagent_models = values.get("subagentModels")
    safe_models: dict[str, str] = {}
    if isinstance(subagent_models, Mapping):
        for key in ("default", "explore", "plan", "general", "workflow"):
            value = subagent_models.get(key)
            if isinstance(value, str) and value.strip():
                safe_models[key] = value.strip()[:160]
    return {
        "model": str(values.get("model") or "")[:160],
        "provider": str(values.get("provider") or "")[:40],
        "configSources": dict(values.get("configSources") or {}),
        "toolProfile": str(values.get("toolProfile") or "core")[:80],
        "subagentRoutingEnabled": bool(values.get("subagentRoutingEnabled")),
        "subagentProvider": str(values.get("subagentProvider") or "")[:80],
        "subagentModels": safe_models,
        "memoryHybridEnabled": bool(values.get("memoryHybridEnabled")),
        "memoryHybridEmbeddingProvider": str(
            values.get("memoryHybridEmbeddingProvider") or ""
        )[:80],
        "reflectionSynthesizerMode": str(
            values.get("reflectionSynthesizerMode") or ""
        )[:80],
        "credentials": {
            "primaryConfigured": any(
                bool(values.get(key))
                for key in (
                    "apiKey",
                    "authToken",
                    "openaiApiKey",
                    "openrouterApiKey",
                    "customApiKey",
                )
            ),
            "subagentConfigured": bool(values.get("subagentApiKey")),
        },
        "effectiveTurnBudget": {
            "maxTokens": budget.limit_total_tokens,
            "maxModelCalls": budget.limit_model_calls,
            "maxCostUsd": budget.limit_cost_usd,
        },
    }


def validate_provider_runtime(runtime: dict[str, Any]) -> list[str]:
    """Validate the auth/base-url required by the detected provider.

    A generic API key is not enough: if the selected model routes to OpenAI,
    OpenAI-compatible credentials must be present; likewise for Anthropic,
    OpenRouter, and custom endpoints.
    """
    from minicode.model_registry import Provider, detect_provider

    model = str(runtime.get("model", "")).strip()
    provider = detect_provider(model, runtime)
    errors: list[str] = []

    if provider == Provider.OPENAI:
        if not runtime.get("openaiApiKey"):
            errors.append(
                "Provider is openai for this model, but OPENAI_API_KEY/openaiApiKey is not configured."
            )
        if not _is_valid_http_url(runtime.get("openaiBaseUrl")):
            errors.append(
                "OpenAI base URL must use HTTPS (HTTP is allowed only for loopback)."
            )
    elif provider == Provider.OPENROUTER:
        if not runtime.get("openrouterApiKey"):
            errors.append(
                "Provider is openrouter for this model, but OPENROUTER_API_KEY is not configured."
            )
        if not _is_valid_http_url(runtime.get("openrouterBaseUrl")):
            errors.append(
                "OpenRouter base URL must use HTTPS (HTTP is allowed only for loopback)."
            )
    elif provider == Provider.CUSTOM:
        if not runtime.get("customBaseUrl"):
            errors.append("Provider is custom, but CUSTOM_API_BASE_URL/customBaseUrl is not configured.")
        elif not _is_valid_http_url(runtime.get("customBaseUrl")):
            errors.append(
                "Custom base URL must use HTTPS (HTTP is allowed only for loopback)."
            )
        if not runtime.get("customApiKey"):
            errors.append("Provider is custom, but CUSTOM_API_KEY/customApiKey is not configured.")
    elif provider == Provider.ANTHROPIC:
        if not (runtime.get("apiKey") or runtime.get("authToken")):
            errors.append(
                "Provider is anthropic for this model, but ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN is not configured."
            )
        if not _is_valid_http_url(runtime.get("baseUrl")):
            errors.append(
                "Anthropic base URL must use HTTPS (HTTP is allowed only for loopback)."
            )

    return errors


def inspect_memory_hybrid_config(
    runtime: Mapping[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Statically validate evidence-gated Hybrid Memory configuration.

    The accepted promotion evidence, not an editable model setting, is the
    authority for the verifier identity.  This check performs no network I/O
    and never returns endpoints, credentials, evidence content, or paths.
    """
    if not bool(runtime.get("memoryHybridEnabled", False)):
        return [], [], {
            "requested": False,
            "active": False,
            "reason": "not_requested",
            "verifierBinding": "not_applicable",
        }
    from minicode.memory_hybrid import assess_hybrid_activation

    activation = assess_hybrid_activation(
        requested=True,
        evidence_path=runtime.get("memoryHybridEvidencePath") or None,
        model_path=runtime.get("memoryHybridModelPath") or None,
        embedding_provider=str(
            runtime.get("memoryHybridEmbeddingProvider") or "local-e5"
        ),
        allow_remote_embedding=bool(
            runtime.get("allowRemoteMemoryEmbedding", False)
        ),
    )
    status = {
        "requested": True,
        "active": bool(activation.active),
        "reason": str(activation.reason)[:96],
        "verifierBinding": "activation_unavailable",
    }
    if not activation.active or not isinstance(activation.evidence, dict):
        return (
            [
                "Hybrid Memory was requested but promotion activation failed "
                f"({activation.reason}). Canonical lexical retrieval will be used."
            ],
            [],
            status,
        )
    verifier = activation.evidence.get("verifier", {})
    expected_model = (
        str(verifier.get("model_id") or "").strip()
        if isinstance(verifier, dict)
        else ""
    )
    configured_model = str(
        runtime.get("memoryHybridVerifierModel") or ""
    ).strip()
    warnings: list[str] = []
    binding = "configured_match"
    if configured_model and configured_model != expected_model:
        binding = "evidence_bound_override"
        warnings.append(
            "Hybrid verifier setting differs from accepted promotion evidence "
            f"(configured={configured_model}, evidence={expected_model}); "
            "runtime will use the evidence-bound verifier."
        )
    elif not configured_model:
        binding = "evidence_bound_dedicated"
    status["verifierBinding"] = binding
    errors: list[str] = []
    try:
        from minicode.model_registry import (
            build_dedicated_model_runtime,
            build_provider_config,
        )

        verifier_runtime = build_dedicated_model_runtime(
            expected_model,
            dict(runtime),
        )
        provider = build_provider_config(expected_model, verifier_runtime)
        if not provider.api_key:
            errors.append(
                "Hybrid Memory evidence-bound verifier credential is unavailable."
            )
    except Exception:
        errors.append(
            "Hybrid Memory evidence-bound verifier route is invalid or unavailable."
        )
    return errors, warnings, status


def get_mcp_config_path(scope: str, cwd: str | Path | None = None) -> Path:
    return project_mcp_path(cwd) if scope == "project" else MINI_CODE_MCP_PATH


def load_scoped_mcp_servers(scope: str, cwd: str | Path | None = None) -> dict[str, Any]:
    return read_mcp_config_file(get_mcp_config_path(scope, cwd))


def save_scoped_mcp_servers(scope: str, servers: dict[str, Any], cwd: str | Path | None = None) -> None:
    target = get_mcp_config_path(scope, cwd)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")


def validate_config(cwd: str | Path | None = None) -> tuple[bool, list[str]]:
    """验证配置完整性，返回 (是否有效，错误列表)
    
    检查项：
    1. 模型名称是否配置
    2. API key 是否配置
    3. 模型名称拼写是否正确
    4. MCP 配置文件是否合法
    """
    errors: list[str] = []
    warnings: list[str] = []
    
    try:
        config = load_runtime_config(cwd)
        errors.extend(validate_provider_runtime(config))
        hybrid_errors, hybrid_warnings, _hybrid_status = (
            inspect_memory_hybrid_config(config)
        )
        errors.extend(hybrid_errors)
        warnings.extend(hybrid_warnings)
        
        # 检查模型名称拼写
        model = config.get("model", "")
        if model and not any(model.lower() == km.lower() for km in KNOWN_MODELS):
            suggestion = _suggest_model_name(model)
            if suggestion:
                warnings.append(
                    f"Unknown model '{model}'. Did you mean '{suggestion}'?"
                )
            else:
                warnings.append(
                    f"Unknown model '{model}'. Known models: {', '.join(KNOWN_MODELS[:3])}..."
                )
        
        # 检查 MCP 配置
        mcp_servers = config.get("mcpServers", {})
        for name, server in mcp_servers.items():
            if not server.get("command"):
                errors.append(f"MCP server '{name}' has no command configured")
        
        return len(errors) == 0, errors + warnings
        
    except RuntimeError as e:
        error_msg = str(e)
        
        # 提供友好的错误消息
        if "No model configured" in error_msg:
            suggestion = _suggest_model_name(os.environ.get("MINI_CODE_MODEL", ""))
            help_msg = (
                f"Error: {error_msg}\n\n"
                "How to fix:\n"
                "  1. Edit ~/.mini-code/.env\n"
                "  2. Set MINI_CODE_MODEL=claude-sonnet-4-20250514\n"
                "  3. Set MINI_CODE_PROVIDER=anthropic\n"
            )
            if suggestion:
                help_msg += f"\n  Did you mean: {suggestion}?\n"
            help_msg += f"\n  Known models: {', '.join(KNOWN_MODELS[:3])}..."
            errors.append(help_msg)
            
        elif "API_KEY" in error_msg or "AUTH_TOKEN" in error_msg:
            help_msg = (
                f"Error: {error_msg}\n\n"
                "How to fix:\n"
                "  Edit ~/.mini-code/.env and configure the key matching "
                "MINI_CODE_PROVIDER.\n"
                "  Examples: ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "OPENROUTER_API_KEY, or CUSTOM_API_KEY.\n"
            )
            errors.append(help_msg)
        else:
            errors.append(str(e))
        
        return False, errors
    except Exception as e:
        return False, [f"Unexpected error: {e}"]


def format_config_diagnostic(cwd: str | Path | None = None) -> str:
    """格式化配置诊断信息"""
    is_valid, messages = validate_config(cwd)
    
    lines = ["Configuration Diagnostics", "=" * 40, ""]
    
    if is_valid:
        lines.append("Status: OK")
        if messages:
            lines.append("")
            lines.append("Warnings:")
            for msg in messages:
                lines.append(f"  [WARN] {msg}")
    else:
        lines.append("Status: ERRORS")
        lines.append("")
        lines.append("Errors:")
        for msg in messages:
            lines.append(f"  [ERROR] {msg}")
    
    # 显示当前配置摘要
    try:
        config = load_runtime_config(cwd)
        model_name = config.get('model', 'not set')
        lines.append("")
        lines.append("Current Configuration")
        lines.append("-" * 40)
        lines.append(f"  Model: {model_name}")

        # Show provider info
        from minicode.model_registry import build_provider_config, detect_provider
        provider = detect_provider(model_name, config)
        lines.append(f"  Provider: {provider.value}")

        provider_config = build_provider_config(model_name, config)
        lines.append(f"  Base URL: {provider_config.base_url}")
        lines.append(
            f"  Auth: {'configured' if provider_config.api_key else 'not configured'}"
        )
        lines.append(f"  Sources: {config.get('sourceSummary', 'unknown')}")
        lines.append(f"  MCP Servers: {len(config.get('mcpServers', {}))}")
        lines.append(f"  Tool Profile: {config.get('toolProfile', 'core')}")
        _hybrid_errors, _hybrid_warnings, hybrid_status = (
            inspect_memory_hybrid_config(config)
        )
        lines.append(
            "  Hybrid Memory: "
            f"requested={str(hybrid_status['requested']).lower()} "
            f"active={str(hybrid_status['active']).lower()} "
            f"reason={hybrid_status['reason']} "
            f"verifierBinding={hybrid_status['verifierBinding']}"
        )

        # User profile info
        global_profile_path = config.get('globalUserProfilePath', '')
        project_profile_path = config.get('projectUserProfilePath', '')
        if global_profile_path:
            gp_exists = Path(global_profile_path).exists()
            lines.append(f"  Global Profile: {global_profile_path} ({'exists' if gp_exists else 'not found'})")
        if project_profile_path:
            pp_exists = Path(project_profile_path).exists()
            lines.append(f"  Project Profile: {project_profile_path} ({'exists' if pp_exists else 'not found'})")
        if config.get('responseLanguage'):
            lines.append(f"  Response Language: {config.get('responseLanguage')}")
        if config.get('responseVerbosity'):
            lines.append(f"  Response Verbosity: {config.get('responseVerbosity')}")
    except Exception:
        pass
    
    return "\n".join(lines)
