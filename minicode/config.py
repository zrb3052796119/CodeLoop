from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


MINI_CODE_DIR = Path.home() / ".mini-code"
MINI_CODE_SETTINGS_PATH = MINI_CODE_DIR / "settings.json"
MINI_CODE_HISTORY_PATH = MINI_CODE_DIR / "history.json"
MINI_CODE_PERMISSIONS_PATH = MINI_CODE_DIR / "permissions.json"
MINI_CODE_MCP_PATH = MINI_CODE_DIR / "mcp.json"
MINI_CODE_USER_PROFILE_PATH = MINI_CODE_DIR / "USER.md"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


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


def load_runtime_config(cwd: str | Path | None = None) -> dict[str, Any]:
    effective = load_effective_settings(cwd)
    settings_env = dict(effective.get("env", {}))
    env = {**settings_env, **os.environ}
    model = (
        os.environ.get("MINI_CODE_MODEL")
        or effective.get("model")
        or str(env.get("ANTHROPIC_MODEL", "")).strip()
    )

    # --- Provider-specific base URLs ---
    # Anthropic
    base_url = str(env.get("ANTHROPIC_BASE_URL", "")).strip() or "https://api.anthropic.com"
    auth_token = str(env.get("ANTHROPIC_AUTH_TOKEN", "")).strip() or None
    api_key = str(env.get("ANTHROPIC_API_KEY", "")).strip() or None

    # OpenAI
    openai_base_url = (
        str(env.get("OPENAI_BASE_URL", "")).strip()
        or str(env.get("OPENAI_API_BASE", "")).strip()
        or effective.get("openaiBaseUrl", "")
        or "https://api.openai.com"
    )
    openai_api_key = str(env.get("OPENAI_API_KEY", "")).strip() or effective.get("openaiApiKey", "")

    # OpenRouter
    openrouter_base_url = (
        str(env.get("OPENROUTER_BASE_URL", "")).strip()
        or "https://openrouter.ai/api"
    )
    openrouter_api_key = str(env.get("OPENROUTER_API_KEY", "")).strip()

    # Custom endpoint
    custom_base_url = (
        str(env.get("CUSTOM_API_BASE_URL", "")).strip()
        or effective.get("customBaseUrl", "")
    )
    custom_api_key = (
        str(env.get("CUSTOM_API_KEY", "")).strip()
        or effective.get("customApiKey", "")
        or openai_api_key
    )

    raw_max_output_tokens = (
        os.environ.get("MINI_CODE_MAX_OUTPUT_TOKENS")
        or effective.get("maxOutputTokens")
        or env.get("MINI_CODE_MAX_OUTPUT_TOKENS")
    )
    max_output_tokens = None
    if raw_max_output_tokens is not None:
        try:
            parsed = int(raw_max_output_tokens)
            if parsed > 0:
                max_output_tokens = parsed
        except (TypeError, ValueError):
            max_output_tokens = None

    # Validate: at least one auth method must be available
    has_auth = any([
        auth_token, api_key, openai_api_key, openrouter_api_key, custom_api_key,
    ])
    if not model:
        raise RuntimeError("No model configured. Set ~/.mini-code/settings.json or ANTHROPIC_MODEL.")
    if not has_auth:
        raise RuntimeError(
            "No auth configured. Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "OPENROUTER_API_KEY, or CUSTOM_API_KEY."
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

    reflection_values = dict(effective)
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
    budget_settings = effective.get("agentTurnBudget", {})
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
    subagent_settings = effective.get("subagentRouting", {})
    if not isinstance(subagent_settings, dict):
        subagent_settings = {}
    # A workspace .env may configure the child route only when it owns the
    # dedicated child credential too. This source binding prevents an
    # untrusted workspace from overriding only the endpoint while borrowing a
    # key from global settings. Process environment overrides remain trusted.
    from minicode.env_file import read_env_files

    workspace_subagent_env = (
        read_env_files([Path(cwd) / ".env"])
        if cwd is not None
        else {}
    )
    workspace_owns_subagent_key = bool(
        str(workspace_subagent_env.get("MINI_CODE_SUBAGENT_API_KEY") or "").strip()
    )
    bound_workspace_env = (
        workspace_subagent_env if workspace_owns_subagent_key else {}
    )

    def subagent_env_value(name: str) -> Any:
        return (
            os.environ.get(name)
            or bound_workspace_env.get(name)
            or settings_env.get(name)
        )

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

    hybrid_settings = effective.get("memoryHybrid", {})
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

    return {
        "model": model,
        "baseUrl": base_url,
        "authToken": auth_token,
        "apiKey": api_key,
        "openaiBaseUrl": openai_base_url,
        "openaiApiKey": openai_api_key,
        "openrouterBaseUrl": openrouter_base_url,
        "openrouterApiKey": openrouter_api_key,
        "customBaseUrl": custom_base_url,
        "customApiKey": custom_api_key,
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
            os.environ.get("MINI_CODE_TOOL_PROFILE")
            or effective.get("toolProfile", "")
            or "core"
        ).strip().lower(),
        "sourceSummary": f"config: {MINI_CODE_SETTINGS_PATH} > {CLAUDE_SETTINGS_PATH} > process.env",
    }


def _is_valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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
            errors.append("OpenAI base URL must be an http(s) URL.")
    elif provider == Provider.OPENROUTER:
        if not runtime.get("openrouterApiKey"):
            errors.append(
                "Provider is openrouter for this model, but OPENROUTER_API_KEY is not configured."
            )
        if not _is_valid_http_url(runtime.get("openrouterBaseUrl")):
            errors.append("OpenRouter base URL must be an http(s) URL.")
    elif provider == Provider.CUSTOM:
        if not runtime.get("customBaseUrl"):
            errors.append("Provider is custom, but CUSTOM_API_BASE_URL/customBaseUrl is not configured.")
        elif not _is_valid_http_url(runtime.get("customBaseUrl")):
            errors.append("Custom base URL must be an http(s) URL.")
        if not runtime.get("customApiKey"):
            errors.append("Provider is custom, but CUSTOM_API_KEY/customApiKey is not configured.")
    elif provider == Provider.ANTHROPIC:
        if not (runtime.get("apiKey") or runtime.get("authToken")):
            errors.append(
                "Provider is anthropic for this model, but ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN is not configured."
            )
        if not _is_valid_http_url(runtime.get("baseUrl")):
            errors.append("Anthropic base URL must be an http(s) URL.")

    return errors


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
                "  1. Set model name: export ANTHROPIC_MODEL=claude-sonnet-4-20250514\n"
                "  2. Or edit ~/.mini-code/settings.json:\n"
                f'     {{"model": "claude-sonnet-4-20250514"}}\n'
            )
            if suggestion:
                help_msg += f"\n  Did you mean: {suggestion}?\n"
            help_msg += f"\n  Known models: {', '.join(KNOWN_MODELS[:3])}..."
            errors.append(help_msg)
            
        elif "No auth configured" in error_msg:
            help_msg = (
                f"Error: {error_msg}\n\n"
                "How to fix:\n"
                "  1. Anthropic:  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  2. OpenAI:     export OPENAI_API_KEY=sk-...\n"
                "  3. OpenRouter: export OPENROUTER_API_KEY=sk-or-...\n"
                "  4. Custom:     export CUSTOM_API_KEY=... + CUSTOM_API_BASE_URL=...\n"
                "  5. Or edit ~/.mini-code/settings.json:\n"
                '     {"env": {"ANTHROPIC_API_KEY": "sk-ant-..."}}\n'
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
        from minicode.model_registry import detect_provider, Provider
        provider = detect_provider(model_name, config)
        lines.append(f"  Provider: {provider.value}")

        lines.append(f"  Base URL: {config.get('baseUrl', 'not set')}")
        if config.get('openaiBaseUrl') and provider in (Provider.OPENAI, Provider.OPENROUTER, Provider.CUSTOM):
            lines.append(f"  OpenAI Base URL: {config.get('openaiBaseUrl')}")
        if config.get('openrouterApiKey'):
            lines.append("  OpenRouter: configured")
        if config.get('customBaseUrl'):
            lines.append(f"  Custom Base URL: {config.get('customBaseUrl')}")

        auth_methods = []
        if config.get("authToken"):
            auth_methods.append("ANTHROPIC_AUTH_TOKEN")
        if config.get("apiKey"):
            auth_methods.append("ANTHROPIC_API_KEY")
        if config.get("openaiApiKey"):
            auth_methods.append("OPENAI_API_KEY")
        if config.get("openrouterApiKey"):
            auth_methods.append("OPENROUTER_API_KEY")
        if config.get("customApiKey"):
            auth_methods.append("CUSTOM_API_KEY")
        lines.append(f"  Auth: {', '.join(auth_methods) or 'none'}")
        lines.append(f"  MCP Servers: {len(config.get('mcpServers', {}))}")
        lines.append(f"  Tool Profile: {config.get('toolProfile', 'core')}")

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
