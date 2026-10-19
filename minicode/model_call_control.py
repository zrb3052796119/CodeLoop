"""Shared deadline and cancellation controls for provider model calls."""

from __future__ import annotations

import inspect
import json
import math
import time
from typing import Any, Mapping


class ModelCallDeadlineExceeded(TimeoutError):
    """The absolute Agent deadline elapsed before a provider call completed."""


def checkpoint_model_call(
    *,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> None:
    """Raise at one content-free provider control checkpoint."""
    checker = getattr(cancellation_token, "raise_if_requested", None)
    if callable(checker):
        checker()
    if deadline_monotonic is None:
        return
    if not isinstance(deadline_monotonic, (int, float)) or not math.isfinite(
        float(deadline_monotonic)
    ):
        raise ModelCallDeadlineExceeded("model call deadline is invalid")
    if time.monotonic() >= float(deadline_monotonic):
        raise ModelCallDeadlineExceeded("model call deadline exceeded")


def bounded_request_timeout(
    configured_timeout: float,
    *,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> float:
    """Return a positive socket timeout that cannot extend a caller deadline."""
    checkpoint_model_call(
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
    )
    try:
        configured = float(configured_timeout)
    except (TypeError, ValueError) as error:
        raise ValueError("model request timeout must be numeric") from error
    if not math.isfinite(configured) or configured <= 0:
        raise ValueError("model request timeout must be positive and finite")
    if deadline_monotonic is None:
        return configured
    remaining = float(deadline_monotonic) - time.monotonic()
    if remaining <= 0:
        raise ModelCallDeadlineExceeded("model call deadline exceeded")
    return min(configured, remaining)


def controlled_retry_sleep(
    seconds: float,
    *,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> None:
    """Sleep in short checkpoints so retry backoff remains cancellable."""
    try:
        remaining_sleep = max(0.0, float(seconds))
    except (TypeError, ValueError):
        remaining_sleep = 0.0
    while remaining_sleep > 0:
        checkpoint_model_call(
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        interval = min(0.05, remaining_sleep)
        if deadline_monotonic is not None:
            deadline_remaining = float(deadline_monotonic) - time.monotonic()
            if deadline_remaining <= 0:
                raise ModelCallDeadlineExceeded("model call deadline exceeded")
            interval = min(interval, deadline_remaining)
        time.sleep(max(0.0, interval))
        remaining_sleep -= interval
    checkpoint_model_call(
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
    )


def _accepted_model_kwargs(model: Any, supplied: Mapping[str, Any]) -> dict[str, Any]:
    """Use the same option projection for admission and the eventual call."""
    accepted: dict[str, Any] = {}
    try:
        signature = inspect.signature(model.next)
        parameters = signature.parameters
        has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        for name, value in supplied.items():
            if has_kwargs or name in parameters:
                accepted[name] = value
    except (TypeError, ValueError):
        for name, value in supplied.items():
            if name not in {"cancellation_token", "deadline_monotonic"}:
                accepted[name] = value
    return accepted


def estimate_budgeted_request_input(
    model: Any,
    messages: list[dict[str, Any]],
    *,
    agent_budget: Any,
    estimated_input: int,
    optional_kwargs: Mapping[str, Any] | None = None,
    default_tools: Any = None,
) -> int:
    """Keep calibration, but never omit the selected request's tool schemas.

    Only finite token admission needs this estimate. Provider usage is still
    settled unchanged. Model-side schemas already present in calibration are
    not added twice; host-only tool metadata is never serialized here.
    """
    if agent_budget is None or agent_budget.snapshot().limit_total_tokens is None:
        return estimated_input
    from minicode.context_manager import estimate_messages_tokens, estimate_tokens

    accepted = _accepted_model_kwargs(model, optional_kwargs or {})
    schema_tokens = 0
    projection = getattr(model, "request_tool_schemas", None)
    if callable(projection):
        # Adapters own late runtime overrides and structured-output clearing.
        schema_options = {name: accepted[name] for name in (
            "tools_override", "disable_tools", "json_object_override", "response_schema_override"
        ) if name in accepted}
        schemas = projection(**schema_options)
    elif accepted.get("disable_tools", False):
        schemas = []
    else:
        override = accepted.get("tools_override")
        selected = override if override is not None else getattr(model, "tools", default_tools)
        if selected is None or not selected.list():
            schemas = []
        else:
            serializer = getattr(model, "_get_serialized_tools", None)
            if callable(serializer):
                schemas = (serializer(tools_override=override) if override is not None
                           else serializer())
            else:
                from minicode.openai_adapter import serialize_function_tools

                schemas = serialize_function_tools(selected.list())
    if schemas:
        schema_tokens = estimate_tokens(json.dumps(schemas))
    return max(estimated_input, estimate_messages_tokens(messages) + schema_tokens)


def call_model_next(
    model: Any,
    messages: list[dict[str, Any]],
    *,
    optional_kwargs: Mapping[str, Any] | None = None,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> Any:
    """Invoke ``model.next`` with controls when the adapter accepts them."""
    checkpoint_model_call(
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
    )
    supplied = dict(optional_kwargs or {})
    supplied.update(
        {
            "cancellation_token": cancellation_token,
            "deadline_monotonic": deadline_monotonic,
        }
    )
    accepted = _accepted_model_kwargs(model, supplied)
    result = model.next(messages, **accepted)
    checkpoint_model_call(
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
    )
    return result


__all__ = [
    "ModelCallDeadlineExceeded",
    "bounded_request_timeout",
    "call_model_next",
    "estimate_budgeted_request_input",
    "checkpoint_model_call",
    "controlled_retry_sleep",
]
