"""Credential-safe per-agent model routing.

The parent runtime supplies one small configuration interface. This module
owns agent-type selection, validation, secret-safe diagnostics, and adapter
construction so the task tool never handles provider details directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


_AGENT_TYPES = frozenset({"explore", "plan", "general"})
_OPENAI_COMPATIBLE = "openai-compatible"


class SubagentModelRoutingError(RuntimeError):
    """Raised when an explicitly enabled child route is unsafe or incomplete."""


@dataclass(frozen=True)
class SubagentModelRoute:
    enabled: bool
    provider: str
    model: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)


def resolve_subagent_model_route(
    runtime: dict[str, Any],
    agent_type: str,
) -> SubagentModelRoute:
    """Resolve one child route without inheriting a credential implicitly."""
    normalized_type = str(agent_type).strip().lower()
    if normalized_type not in _AGENT_TYPES:
        raise SubagentModelRoutingError("subagent_type_unsupported")

    if not bool(runtime.get("subagentRoutingEnabled", False)):
        parent_model = str(runtime.get("model") or "").strip()
        if not parent_model:
            raise SubagentModelRoutingError("parent_model_missing")
        return SubagentModelRoute(False, "inherit", parent_model)

    provider = str(runtime.get("subagentProvider") or "").strip().lower()
    if provider != _OPENAI_COMPATIBLE:
        raise SubagentModelRoutingError("subagent_provider_unsupported")
    models = runtime.get("subagentModels")
    if not isinstance(models, dict):
        raise SubagentModelRoutingError("subagent_models_invalid")
    model = str(models.get(normalized_type) or models.get("default") or "").strip()
    if not model:
        raise SubagentModelRoutingError("subagent_model_missing")
    base_url = str(runtime.get("subagentBaseUrl") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SubagentModelRoutingError("subagent_base_url_unsafe")
    api_key = str(runtime.get("subagentApiKey") or "").strip()
    if not api_key:
        raise SubagentModelRoutingError("subagent_api_key_missing")
    return SubagentModelRoute(True, provider, model, base_url, api_key)


def create_subagent_model_adapter(
    runtime: dict[str, Any],
    agent_type: str,
    *,
    tools: Any,
) -> Any:
    """Build the selected child adapter while isolating its provider config."""
    route = resolve_subagent_model_route(runtime, agent_type)
    if not route.enabled:
        from minicode.model_registry import create_model_adapter

        # Preserve the registry's keyword-call seam. Several embedders and
        # tests replace this factory with keyword-only wrappers, and routing
        # must remain transparent while the dedicated child route is off.
        return create_model_adapter(
            model=route.model,
            tools=tools,
            runtime=runtime,
        )

    from minicode.openai_adapter import OpenAIModelAdapter

    child_runtime = dict(runtime)
    child_runtime.update(
        {
            "model": route.model,
            "openaiBaseUrl": route.base_url,
            "openaiApiKey": route.api_key,
            "_isolatedOpenAIConfig": True,
        }
    )
    child_runtime.pop("_openrouter_headers", None)
    child_runtime.pop("_openrouter_params", None)
    child_runtime.pop("_custom_headers", None)
    return OpenAIModelAdapter(child_runtime, tools)


__all__ = [
    "SubagentModelRoute",
    "SubagentModelRoutingError",
    "create_subagent_model_adapter",
    "resolve_subagent_model_route",
]
