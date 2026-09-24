"""Model Switcher for dynamic model changes at runtime.

Handles the lifecycle of switching between LLM models during a session,
including adapter recreation, context preservation, and state updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from minicode.logging_config import get_logger
from minicode.model_registry import (
    BUILTIN_MODELS,
    create_model_adapter,
    resolve_model_info,
)

logger = get_logger("model_switcher")


@dataclass
class SwitchResult:
    """Result of a model switch operation."""
    success: bool
    old_model: str
    new_model: str
    old_provider: str
    new_provider: str
    reason: str
    adapter: Any | None = None
    errors: list[str] = field(default_factory=list)

    def to_log(self) -> str:
        status = "OK" if self.success else "FAILED"
        msg = f"Switch [{status}]: {self.old_model} ({self.old_provider}) -> {self.new_model} ({self.new_provider})"
        if self.errors:
            msg += f" Errors: {'; '.join(self.errors)}"
        return msg


class ModelSwitcher:
    """Manages runtime model switching with adapter lifecycle."""

    def __init__(
        self,
        current_model: str,
        current_runtime: dict,
        current_tools: Any,
        available_models: dict[str, Any] | None = None,
    ):
        self._current_model = current_model
        self._runtime = current_runtime
        self._tools = current_tools
        self._available_models = available_models or BUILTIN_MODELS
        self._switch_history: list[SwitchResult] = []
        self._current_adapter: Any = None

    @property
    def current_model(self) -> str:
        return self._current_model

    @property
    def switch_count(self) -> int:
        return len(self._switch_history)

    def switch_to(self, target_model: str, reason: str = "user_request") -> SwitchResult:
        """Switch to a new model."""
        if target_model not in self._available_models:
            return SwitchResult(
                success=False,
                old_model=self._current_model,
                new_model=target_model,
                old_provider=detect_provider_name(self._current_model),
                new_provider="unknown",
                reason=reason,
                errors=[f"Model '{target_model}' not in available models"],
            )

        old_model = self._current_model
        old_provider = detect_provider_name(old_model)
        new_provider = detect_provider_name(target_model)

        try:
            new_adapter = create_model_adapter(
                model=target_model,
                tools=self._tools,
                runtime=self._runtime,
            )

            self._current_model = target_model
            self._current_adapter = new_adapter
            self._runtime["model"] = target_model

            result = SwitchResult(
                success=True,
                old_model=old_model,
                new_model=target_model,
                old_provider=old_provider,
                new_provider=new_provider,
                reason=reason,
                adapter=new_adapter,
            )

            self._switch_history.append(result)
            logger.info(result.to_log())
            return result

        except Exception as e:
            result = SwitchResult(
                success=False,
                old_model=old_model,
                new_model=target_model,
                old_provider=old_provider,
                new_provider=new_provider,
                reason=reason,
                errors=[str(e)],
            )
            self._switch_history.append(result)
            logger.error("Model switch failed: %s", result.to_log())
            return result

    def get_switch_history(self) -> list[dict[str, Any]]:
        """Get human-readable switch history."""
        return [
            {
                "old": s.old_model,
                "new": s.new_model,
                "reason": s.reason,
                "success": s.success,
                "errors": s.errors,
            }
            for s in self._switch_history
        ]

    def get_current_adapter(self) -> Any | None:
        """Get the current model adapter."""
        return self._current_adapter


def detect_provider_name(model: str) -> str:
    """Get provider name string for a model."""
    info = resolve_model_info(model)
    return info.provider.value
