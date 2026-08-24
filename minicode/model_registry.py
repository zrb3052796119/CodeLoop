"""Unified model registry and routing for MiniCode.

Supports multiple LLM providers with a single configuration system:
- Anthropic (Claude) — native Messages API
- OpenAI (GPT) — Chat Completions API
- OpenRouter — unified gateway to 200+ models
- Custom OpenAI-compatible endpoints (vLLM, Ollama, LiteLLM, etc.)

Design inspired by Hermes Agent's provider/model abstraction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse



# ---------------------------------------------------------------------------
# Provider types
# ---------------------------------------------------------------------------

class Provider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"
    MOCK = "mock"


# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Static metadata about a model."""
    name: str                          # Canonical model ID
    provider: Provider                 # Which provider to use
    display_name: str = ""             # Human-readable name
    context_window: int = 128_000      # Token limit
    max_output_tokens: int | None = None
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    pricing_input: float = 3.0        # USD per 1M input tokens
    pricing_output: float = 15.0      # USD per 1M output tokens

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


@dataclass
class ModelSelectionSignal:
    """Observed state for cybernetic model selection."""

    task_complexity: str = "moderate"
    budget_pressure: float = 0.0
    latency_pressure: float = 0.0
    recent_failures: int = 0
    requires_tools: bool = True
    requires_long_context: bool = False
    current_model: str = ""


@dataclass
class ModelSelectionDecision:
    """Controller output for model/routing recommendation."""

    model: str
    provider: Provider
    reasoning_effort: ReasoningEffort
    score: float
    reasons: list[str] = field(default_factory=list)
    fallback_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider.value,
            "reasoning_effort": self.reasoning_effort.value,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
            "fallback_model": self.fallback_model,
        }


class ModelSelectionController:
    """Risk/cost adaptive model recommendation controller."""

    def decide(self, signal: ModelSelectionSignal) -> ModelSelectionDecision:
        candidates = [
            info for info in list_available_models()
            if info.supports_tools or not signal.requires_tools
        ]
        if not candidates:
            info = resolve_model_info(signal.current_model or "claude-sonnet-4-20250514")
            return ModelSelectionDecision(
                model=info.name,
                provider=info.provider,
                reasoning_effort=ReasoningEffort.MEDIUM,
                score=0.0,
                reasons=["no compatible candidates"],
            )

        reasons: list[str] = []
        complexity = signal.task_complexity.lower()
        target_power = {"simple": 0.25, "moderate": 0.55, "complex": 0.85}.get(complexity, 0.55)
        if signal.recent_failures > 0:
            target_power = min(1.0, target_power + 0.10 * signal.recent_failures)
            reasons.append(f"recent failures: {signal.recent_failures}")
        if signal.requires_long_context:
            target_power = min(1.0, target_power + 0.10)
            reasons.append("long context required")
        if signal.budget_pressure >= 0.70:
            target_power = max(0.20, target_power - 0.25)
            reasons.append("high budget pressure")
        elif signal.budget_pressure <= 0.20 and complexity == "complex":
            target_power = min(1.0, target_power + 0.10)
            reasons.append("budget allows stronger model")
        if signal.latency_pressure >= 0.70:
            target_power = max(0.20, target_power - 0.15)
            reasons.append("high latency pressure")

        scored: list[tuple[float, ModelInfo, list[str]]] = []
        for info in candidates:
            power = self._model_power(info)
            cost = self._model_cost(info)
            latency = self._latency_proxy(info)
            context_fit = 1.0 if not signal.requires_long_context else min(1.0, info.context_window / 200_000)

            score = 1.0 - abs(power - target_power)
            score -= signal.budget_pressure * cost * 0.45
            score -= signal.latency_pressure * latency * 0.30
            score += context_fit * 0.15
            if signal.current_model and info.name == resolve_model_info(signal.current_model).name:
                score += 0.05
            if signal.requires_tools and not info.supports_tools:
                score -= 1.0

            candidate_reasons = [
                f"power={power:.2f}",
                f"cost={cost:.2f}",
                f"context={info.context_window // 1000}K",
            ]
            scored.append((score, info, candidate_reasons))

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best, candidate_reasons = scored[0]
        fallback = scored[1][1].name if len(scored) > 1 else None
        effort = self._reasoning_effort(target_power, signal)
        return ModelSelectionDecision(
            model=best.name,
            provider=best.provider,
            reasoning_effort=effort,
            score=max(0.0, best_score),
            reasons=reasons + candidate_reasons,
            fallback_model=fallback,
        )

    def _model_power(self, info: ModelInfo) -> float:
        name = info.name.lower()
        if "opus" in name or name == "o1" or "gemini-2.5-pro" in name:
            return 0.95
        if "sonnet" in name or "gpt-4o" in name or "o3" in name or "r1" in name:
            return 0.75
        if "mini" in name or "haiku" in name or "flash" in name or "deepseek-chat" in name:
            return 0.35
        return 0.55

    def _model_cost(self, info: ModelInfo) -> float:
        blended = (info.pricing_input + info.pricing_output) / 2
        return min(1.0, blended / 45.0)

    def _latency_proxy(self, info: ModelInfo) -> float:
        power = self._model_power(info)
        return min(1.0, 0.25 + power * 0.65)

    def _reasoning_effort(
        self,
        target_power: float,
        signal: ModelSelectionSignal,
    ) -> ReasoningEffort:
        if signal.budget_pressure >= 0.80 or signal.latency_pressure >= 0.85:
            return ReasoningEffort.LOW
        if target_power >= 0.90:
            return ReasoningEffort.XHIGH
        if target_power >= 0.75:
            return ReasoningEffort.HIGH
        if target_power >= 0.45:
            return ReasoningEffort.MEDIUM
        return ReasoningEffort.LOW


# ---------------------------------------------------------------------------
# Built-in model catalog
# ---------------------------------------------------------------------------

BUILTIN_MODELS: dict[str, ModelInfo] = {}

def _register(info: ModelInfo) -> None:
    BUILTIN_MODELS[info.name] = info
    # Also register under common aliases
    for alias in _aliases(info.name):
        if alias not in BUILTIN_MODELS:
            BUILTIN_MODELS[alias] = info


def _aliases(name: str) -> list[str]:
    """Generate common aliases for a model name."""
    result: list[str] = []
    # e.g. "claude-sonnet-4-20250514" -> "claude-sonnet-4", "sonnet-4"
    parts = name.split("-")
    if "claude" in parts:
        idx = parts.index("claude")
        family = "-".join(parts[idx:idx + 2])  # claude-sonnet-4
        if family != name:
            result.append(family)
    if "gpt" in parts:
        idx = parts.index("gpt")
        family = "-".join(parts[idx:idx + 2])  # gpt-4o
        if family != name:
            result.append(family)
    return result


# --- Anthropic models ---
_register(ModelInfo("claude-sonnet-4-20250514", Provider.ANTHROPIC,
    context_window=200_000, max_output_tokens=16_384,
    pricing_input=3.0, pricing_output=15.0))
_register(ModelInfo("claude-opus-4-20250514", Provider.ANTHROPIC,
    context_window=200_000, max_output_tokens=16_384,
    pricing_input=15.0, pricing_output=75.0))
_register(ModelInfo("claude-haiku-3-20240307", Provider.ANTHROPIC,
    context_window=100_000, max_output_tokens=4_096,
    pricing_input=0.25, pricing_output=1.25))

# --- OpenAI models ---
_register(ModelInfo("gpt-4o", Provider.OPENAI,
    context_window=128_000, max_output_tokens=16_384,
    pricing_input=2.50, pricing_output=10.0))
_register(ModelInfo("gpt-4o-mini", Provider.OPENAI,
    context_window=128_000, max_output_tokens=16_384,
    pricing_input=0.15, pricing_output=0.60))
_register(ModelInfo("gpt-4-turbo", Provider.OPENAI,
    context_window=128_000, max_output_tokens=4_096,
    pricing_input=10.0, pricing_output=30.0))
_register(ModelInfo("o1", Provider.OPENAI,
    context_window=200_000, max_output_tokens=100_000,
    pricing_input=15.0, pricing_output=60.0, supports_tools=False))
_register(ModelInfo("o1-mini", Provider.OPENAI,
    context_window=128_000, max_output_tokens=65_536,
    pricing_input=3.0, pricing_output=12.0, supports_tools=False))
_register(ModelInfo("o3-mini", Provider.OPENAI,
    context_window=200_000, max_output_tokens=100_000,
    pricing_input=1.10, pricing_output=4.40))

# --- OpenRouter popular models ---
_register(ModelInfo("openrouter/auto", Provider.OPENROUTER,
    display_name="OpenRouter Auto", context_window=200_000,
    pricing_input=3.0, pricing_output=15.0))
_register(ModelInfo("anthropic/claude-sonnet-4", Provider.OPENROUTER,
    context_window=200_000, max_output_tokens=16_384,
    pricing_input=3.0, pricing_output=15.0))
_register(ModelInfo("anthropic/claude-opus-4", Provider.OPENROUTER,
    context_window=200_000, max_output_tokens=16_384,
    pricing_input=15.0, pricing_output=75.0))
_register(ModelInfo("openai/gpt-4o", Provider.OPENROUTER,
    context_window=128_000, max_output_tokens=16_384,
    pricing_input=2.50, pricing_output=10.0))
_register(ModelInfo("openai/gpt-4o-mini", Provider.OPENROUTER,
    context_window=128_000, max_output_tokens=16_384,
    pricing_input=0.15, pricing_output=0.60))
_register(ModelInfo("google/gemini-2.5-pro", Provider.OPENROUTER,
    context_window=1_000_000, max_output_tokens=8_192,
    pricing_input=1.25, pricing_output=10.0, supports_vision=True))
_register(ModelInfo("google/gemini-2.5-flash", Provider.OPENROUTER,
    context_window=1_000_000, max_output_tokens=8_192,
    pricing_input=0.15, pricing_output=0.60, supports_vision=True))
_register(ModelInfo("meta-llama/llama-4-maverick", Provider.OPENROUTER,
    context_window=1_000_000, max_output_tokens=8_192,
    pricing_input=0.20, pricing_output=0.60))
_register(ModelInfo("deepseek/deepseek-r1", Provider.OPENROUTER,
    context_window=128_000, max_output_tokens=8_192,
    pricing_input=0.55, pricing_output=2.19))
_register(ModelInfo("deepseek/deepseek-chat", Provider.OPENROUTER,
    context_window=128_000, max_output_tokens=8_192,
    pricing_input=0.14, pricing_output=0.28))
_register(ModelInfo("deepseek-v4-pro[1m]", Provider.ANTHROPIC,
    display_name="DeepSeek V4 Pro",
    context_window=128_000, max_output_tokens=8_192,
    pricing_input=0.10, pricing_output=0.40))
_register(ModelInfo("qwen/qwen3-235b-a22b", Provider.OPENROUTER,
    context_window=128_000, max_output_tokens=8_192,
    pricing_input=0.22, pricing_output=0.88))
_register(ModelInfo("minimax/minimax-m1", Provider.OPENROUTER,
    context_window=1_000_000, max_output_tokens=8_192,
    pricing_input=0.20, pricing_output=0.80))


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def infer_model_provider(model: str) -> Provider | None:
    """Infer a provider only when the model identifier is unambiguous.

    Unknown bare identifiers intentionally return ``None`` so callers that
    manage a custom endpoint can retain their explicit provider profile.
    """
    normalized = str(model).strip()
    model_lower = normalized.lower()

    registered = BUILTIN_MODELS.get(normalized)
    if registered is None:
        registered = next(
            (
                info
                for name, info in BUILTIN_MODELS.items()
                if name.lower() == model_lower
            ),
            None,
        )
    if registered is not None:
        return registered.provider

    if model_lower.startswith("openrouter/"):
        return Provider.OPENROUTER
    for prefix in (
        "anthropic/",
        "openai/",
        "google/",
        "meta-llama/",
        "deepseek/",
        "qwen/",
        "minimax/",
        "mistralai/",
    ):
        if model_lower.startswith(prefix):
            return Provider.OPENROUTER

    openai_prefixes = ("gpt-4", "gpt-3.5", "o1-", "o3-", "chatgpt-")
    openai_exact = {
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "o1",
        "o1-mini",
        "o3-mini",
    }
    if model_lower in openai_exact or any(
        model_lower.startswith(prefix) for prefix in openai_prefixes
    ):
        return Provider.OPENAI
    if model_lower.startswith("claude-"):
        return Provider.ANTHROPIC
    return None


def detect_provider(model: str, runtime: dict | None = None) -> Provider:
    """Resolve a provider without using credential presence as a router.

    ``load_runtime_config`` is the authority for process/file precedence.  The
    registry consumes that frozen result so adding an unrelated provider key
    cannot silently change where a request is sent.
    """
    runtime = runtime or {}

    explicit = str(runtime.get("provider") or "").strip().lower()
    if explicit:
        if explicit == Provider.MOCK.value:
            allowed = ", ".join(
                provider.value for provider in Provider if provider != Provider.MOCK
            )
            raise RuntimeError(
                f"Unsupported MINI_CODE_PROVIDER '{explicit}'. Expected one of: {allowed}."
            )
        try:
            return Provider(explicit)
        except ValueError as error:
            allowed = ", ".join(
                provider.value for provider in Provider if provider != Provider.MOCK
            )
            raise RuntimeError(
                f"Unsupported MINI_CODE_PROVIDER '{explicit}'. Expected one of: {allowed}."
            ) from error

    inferred = infer_model_provider(model)
    if inferred is not None:
        return inferred

    # A custom endpoint is an explicit routing signal.  This also preserves
    # legacy DeepSeek setups whose model name is simply ``deepseek-chat``.
    if str(runtime.get("customBaseUrl") or "").strip():
        return Provider.CUSTOM

    # Anthropic remains the compatibility default for unknown bare model IDs.
    return Provider.ANTHROPIC


def resolve_model_info(model: str, provider: Provider | None = None) -> ModelInfo:
    """Resolve a model name to ModelInfo, with fallback for unknown models."""
    # Check built-in catalog first
    if model in BUILTIN_MODELS:
        return BUILTIN_MODELS[model]

    # Try case-insensitive lookup
    for key, info in BUILTIN_MODELS.items():
        if key.lower() == model.lower():
            return info

    # Unknown model: generate a best-effort ModelInfo
    resolved_provider = provider or detect_provider(model)
    return ModelInfo(
        name=model,
        provider=resolved_provider,
        context_window=128_000,
        pricing_input=3.0,
        pricing_output=15.0,
    )


# ---------------------------------------------------------------------------
# Provider configuration builder
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """Resolved provider configuration for a model."""
    provider: Provider
    model: str
    base_url: str
    api_key: str = field(repr=False)
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_openai_compatible(self) -> bool:
        """Whether this provider uses OpenAI Chat Completions API format."""
        return self.provider in (Provider.OPENAI, Provider.OPENROUTER, Provider.CUSTOM)


def _validated_provider_base_url(value: str, provider: Provider) -> str:
    normalized = str(value).strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{provider.value}_base_url_unsafe")
    if parsed.scheme == "https":
        return normalized
    if parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return normalized
    raise RuntimeError(f"{provider.value}_base_url_unsafe")


def build_provider_config(model: str, runtime: dict | None = None) -> ProviderConfig:
    """Build provider configuration from model name and runtime config.

    This centralizes all the provider-specific URL/key/header logic that was
    previously scattered across main.py, headless.py, gateway.py, etc.
    """
    runtime = runtime or {}
    provider = detect_provider(model, runtime)
    resolve_model_info(model, provider)

    if provider == Provider.OPENROUTER:
        raw_transforms = str(runtime.get("openrouterTransforms") or "").strip()
        return ProviderConfig(
            provider=Provider.OPENROUTER,
            model=model,
            base_url=_validated_provider_base_url(
                str(runtime.get("openrouterBaseUrl") or "https://openrouter.ai/api"),
                Provider.OPENROUTER,
            ),
            api_key=str(runtime.get("openrouterApiKey") or ""),
            extra_headers={
                "HTTP-Referer": str(
                    runtime.get("openrouterReferer") or "https://github.com/minicode-py"
                ),
                "X-Title": str(runtime.get("openrouterTitle") or "MiniCode Python"),
            },
            extra_params={
                # OpenRouter supports provider-specific routing
                "transforms": raw_transforms.split(",") if raw_transforms else None,
            },
        )

    if provider == Provider.OPENAI:
        base_url = _validated_provider_base_url(
            str(runtime.get("openaiBaseUrl") or "https://api.openai.com"),
            Provider.OPENAI,
        )
        api_key = str(runtime.get("openaiApiKey") or "")
        return ProviderConfig(
            provider=Provider.OPENAI,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )

    if provider == Provider.CUSTOM:
        base_url = _validated_provider_base_url(
            str(runtime.get("customBaseUrl") or ""),
            Provider.CUSTOM,
        )
        api_key = str(runtime.get("customApiKey") or "")
        return ProviderConfig(
            provider=Provider.CUSTOM,
            model=model,
            base_url=base_url,
            api_key=api_key,
            extra_headers=_parse_extra_headers(
                str(runtime.get("customApiExtraHeaders") or "")
            ),
        )

    # Default: Anthropic
    base_url = _validated_provider_base_url(
        str(runtime.get("baseUrl") or "https://api.anthropic.com"),
        Provider.ANTHROPIC,
    )
    api_key = str(runtime.get("apiKey") or "")
    auth_token = str(runtime.get("authToken") or "")
    # Anthropic uses x-api-key header, but we keep it in api_key for simplicity
    # The adapter will handle the difference
    return ProviderConfig(
        provider=Provider.ANTHROPIC,
        model=model,
        base_url=base_url,
        api_key=api_key or auth_token,
        extra_params={"auth_token": auth_token} if auth_token else {},
    )


def _parse_extra_headers(raw: str) -> dict[str, str]:
    """Parse ``Key1:Val1,Key2:Val2`` from a resolved runtime value."""
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


# ---------------------------------------------------------------------------
# Model adapter factory (centralized replacement for scattered if/elif)
# ---------------------------------------------------------------------------

def create_model_adapter(
    model: str,
    tools: Any,
    runtime: dict | None = None,
    force_mock: bool = False,
) -> Any:
    """Create the appropriate ModelAdapter for the given model.

    This replaces the duplicated model-selection logic in main.py,
    headless.py, gateway.py, etc. with a single call.

    Args:
        model: Model name (e.g., "claude-sonnet-4-20250514", "openai/gpt-4o")
        tools: Tool registry instance
        runtime: Runtime configuration dict
        force_mock: Force mock mode (for testing or no API key)

    Returns:
        A ModelAdapter instance (AnthropicModelAdapter, OpenAIModelAdapter, or MockModelAdapter)
    """
    if force_mock or os.environ.get("MINI_CODE_MODEL_MODE") == "mock":
        from minicode.mock_model import MockModelAdapter
        return MockModelAdapter()

    provider_config = build_provider_config(model, runtime)

    # OpenRouter / Custom / OpenAI all use OpenAI-compatible API
    if provider_config.is_openai_compatible:
        from minicode.openai_adapter import OpenAIModelAdapter
        # Inject provider config into runtime so the adapter can use it
        enriched_runtime = dict(runtime or {})
        enriched_runtime["model"] = provider_config.model
        enriched_runtime["_isolatedOpenAIConfig"] = True
        if provider_config.provider == Provider.OPENROUTER:
            enriched_runtime["openaiBaseUrl"] = provider_config.base_url
            enriched_runtime["openaiApiKey"] = provider_config.api_key
            enriched_runtime["_openrouter_headers"] = provider_config.extra_headers
            enriched_runtime["_openrouter_params"] = provider_config.extra_params
        elif provider_config.provider == Provider.CUSTOM:
            enriched_runtime["openaiBaseUrl"] = provider_config.base_url
            enriched_runtime["openaiApiKey"] = provider_config.api_key
            enriched_runtime["_custom_headers"] = provider_config.extra_headers
        elif provider_config.provider == Provider.OPENAI:
            enriched_runtime["openaiBaseUrl"] = provider_config.base_url
            enriched_runtime["openaiApiKey"] = provider_config.api_key
        return OpenAIModelAdapter(enriched_runtime, tools)

    # Anthropic
    from minicode.anthropic_adapter import AnthropicModelAdapter
    enriched = dict(runtime or {})
    if "model" not in enriched:
        enriched["model"] = model
    enriched["baseUrl"] = provider_config.base_url
    if provider_config.extra_params.get("auth_token"):
        enriched["authToken"] = provider_config.extra_params["auth_token"]
    else:
        enriched["apiKey"] = provider_config.api_key
    # Disable extended thinking for non-standard Anthropic endpoints (DeepSeek etc.)
    if "api.anthropic.com" not in enriched.get("baseUrl", ""):
        enriched["disableThinking"] = True
    return AnthropicModelAdapter(enriched, tools)


def build_dedicated_model_runtime(
    model: str,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an isolated transport runtime for an auxiliary model.

    A main-agent ``provider=custom`` route is authority only for that main
    model.  It must not be inherited when an evidence contract selects a
    different verifier model, otherwise (for example) ``deepseek-chat`` can be
    sent to the main Qwen endpoint with the main Qwen credential.

    Only provider-neutral inference controls are copied.  Provider transport
    and credentials are selected from the target model's dedicated fields.
    Unknown bare custom models fail closed because their independent route
    cannot be inferred safely.
    """
    source = dict(runtime or {})
    isolated = {
        key: source[key]
        for key in (
            "temperature",
            "maxOutputTokens",
            "modelTimeoutSeconds",
            "modelMaxRetries",
        )
        if key in source
    }
    isolated["model"] = model
    normalized = str(model).strip().lower()
    if normalized == "deepseek-chat":
        isolated.update(
            {
                "provider": Provider.CUSTOM.value,
                "customBaseUrl": str(
                    source.get("deepseekBaseUrl") or "https://api.deepseek.com"
                ).strip(),
                "customApiKey": str(source.get("deepseekApiKey") or "").strip(),
            }
        )
        return isolated

    provider = infer_model_provider(model)
    if provider is None:
        raise RuntimeError("dedicated_model_route_unavailable")
    isolated["provider"] = provider.value
    if provider == Provider.OPENAI:
        isolated["openaiBaseUrl"] = source.get("openaiBaseUrl")
        isolated["openaiApiKey"] = source.get("openaiApiKey")
    elif provider == Provider.OPENROUTER:
        isolated["openrouterBaseUrl"] = source.get("openrouterBaseUrl")
        isolated["openrouterApiKey"] = source.get("openrouterApiKey")
        isolated["openrouterReferer"] = source.get("openrouterReferer")
        isolated["openrouterTitle"] = source.get("openrouterTitle")
        isolated["openrouterTransforms"] = source.get("openrouterTransforms")
    elif provider == Provider.ANTHROPIC:
        isolated["baseUrl"] = source.get("baseUrl")
        isolated["apiKey"] = source.get("apiKey")
        isolated["authToken"] = source.get("authToken")
    else:
        raise RuntimeError("dedicated_model_route_unavailable")
    return isolated


def create_dedicated_model_adapter(
    model: str,
    tools: Any,
    runtime: dict[str, Any] | None = None,
) -> Any:
    """Create an auxiliary adapter without inheriting the main transport."""
    return create_model_adapter(
        model,
        tools,
        build_dedicated_model_runtime(model, runtime),
    )


# ---------------------------------------------------------------------------
# Runtime model switching
# ---------------------------------------------------------------------------

@dataclass
class ModelSwitch:
    """Result of a model switch operation."""
    success: bool
    old_model: str
    new_model: str
    provider: Provider
    message: str


def list_available_models(provider: Provider | None = None) -> list[ModelInfo]:
    """List all available models, optionally filtered by provider."""
    models = list(BUILTIN_MODELS.values())
    # Deduplicate (aliases point to same ModelInfo)
    seen: set[str] = set()
    unique: list[ModelInfo] = []
    for m in models:
        if m.name not in seen:
            seen.add(m.name)
            unique.append(m)
    if provider:
        unique = [m for m in unique if m.provider == provider]
    return sorted(unique, key=lambda m: (m.provider.value, m.pricing_input))


def format_model_list(provider: Provider | None = None) -> str:
    """Format available models as a readable table."""
    models = list_available_models(provider)
    if not models:
        return "No models available."

    lines = ["Available Models", "=" * 70, ""]

    current_provider: Provider | None = None
    for m in models:
        if m.provider != current_provider:
            current_provider = m.provider
            lines.append(f"  [{current_provider.value.upper()}]")
            lines.append(f"  {'-' * 50}")

        pricing = f"${m.pricing_input:.2f}/${m.pricing_output:.2f}"
        ctx = f"{m.context_window // 1000}K"
        tools_flag = "tools" if m.supports_tools else "no-tools"
        lines.append(f"    {m.name:<45} {pricing:<14} {ctx:<8} {tools_flag}")

    lines.append("")
    lines.append("  Pricing: input/output per 1M tokens | Context: token limit")
    lines.append("")
    lines.append("  Usage:")
    lines.append("    /model <name>          — Switch to a specific model")
    lines.append("    /model anthropic       — List Anthropic models")
    lines.append("    /model openrouter      — List OpenRouter models")
    lines.append("    /model status          — Show current model info")
    return "\n".join(lines)


def format_model_status(model: str, runtime: dict | None = None) -> str:
    """Format current model status."""
    provider = detect_provider(model, runtime)
    info = resolve_model_info(model, provider)
    pconfig = build_provider_config(model, runtime)
    recommendation = ModelSelectionController().decide(
        ModelSelectionSignal(
            task_complexity="moderate",
            budget_pressure=float((runtime or {}).get("budgetPressure", 0.0) or 0.0),
            latency_pressure=float((runtime or {}).get("latencyPressure", 0.0) or 0.0),
            recent_failures=int((runtime or {}).get("recentFailures", 0) or 0),
            requires_tools=True,
            current_model=model,
        )
    )

    lines = [
        "Current Model",
        "=" * 50,
        f"  Model:    {info.display_name}",
        f"  Provider: {provider.value}",
        f"  Base URL: {pconfig.base_url}",
        f"  Context:  {info.context_window:,} tokens",
        f"  Pricing:  ${info.pricing_input:.2f} / ${info.pricing_output:.2f} (in/out per 1M)",
        f"  Tools:    {'Yes' if info.supports_tools else 'No'}",
        f"  Vision:   {'Yes' if info.supports_vision else 'No'}",
        f"  API Key:  {'configured' if pconfig.api_key else 'not set'}",
        "",
        "Cybernetic Recommendation",
        f"  Model:    {recommendation.model}",
        f"  Effort:   {recommendation.reasoning_effort.value}",
        f"  Score:    {recommendation.score:.2f}",
        f"  Reasons:  {', '.join(recommendation.reasons[:4])}",
    ]
    return "\n".join(lines)
