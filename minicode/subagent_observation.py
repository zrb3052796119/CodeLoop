"""Versioned, content-free sub-agent completion event contract.

A sub-agent runs a full nested agent loop, but its internal events must not
be written into the parent Run's event stream: readers such as
``skill_evidence`` require exactly one ``task.outcome`` and one
``skill.routed`` per Run, so forwarding the nested loop's own lifecycle
events would silently disqualify every Run that used a sub-agent.

Instead the parent Run records one bounded summary event per sub-agent
invocation. It carries counts and a closed outcome enum only — never the
sub-agent's prompt, findings, tool arguments, or file paths.
"""

from __future__ import annotations

from collections.abc import Mapping


SUBAGENT_EVENT_TYPE = "subagent.completed"

_AGENT_TYPES = frozenset({"explore", "plan", "general"})
_OUTCOMES = frozenset({"completed", "failed", "depth_rejected"})
_MAX_COUNT = 100_000
_MAX_DURATION_MS = 86_400_000
_FIELDS = frozenset(
    {
        "subagentVersion",
        "agentType",
        "outcome",
        "modelTurns",
        "toolCalls",
        "durationMs",
        "maxTurns",
        "resultTruncated",
    }
)


def normalize_subagent_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return the strict v1 whitelist or ``None`` for non-canonical input.

    An unexpected field is rejected outright rather than quietly dropped: a
    payload carrying anything beyond this contract means the caller is not
    the contract's owner, and silently accepting it would let a future
    change smuggle content into a stream that is required to stay bounded
    and content-free.
    """
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return None
    if payload.get("subagentVersion") != 1:
        return None
    agent_type = payload.get("agentType")
    outcome = payload.get("outcome")
    model_turns = payload.get("modelTurns")
    tool_calls = payload.get("toolCalls")
    duration_ms = payload.get("durationMs")
    max_turns = payload.get("maxTurns")
    result_truncated = payload.get("resultTruncated")
    counts = (model_turns, tool_calls, duration_ms, max_turns)
    if (
        agent_type not in _AGENT_TYPES
        or outcome not in _OUTCOMES
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts
        )
        or any(value > _MAX_COUNT for value in (model_turns, tool_calls, max_turns))
        or duration_ms > _MAX_DURATION_MS
        or not isinstance(result_truncated, bool)
    ):
        return None
    return {
        "subagentVersion": 1,
        "agentType": agent_type,
        "outcome": outcome,
        "modelTurns": model_turns,
        "toolCalls": tool_calls,
        "durationMs": duration_ms,
        "maxTurns": max_turns,
        "resultTruncated": result_truncated,
    }


def project_subagent_event(
    *,
    agent_type: str,
    outcome: str,
    model_turns: int,
    tool_calls: int,
    duration_ms: int,
    max_turns: int,
    result_truncated: bool,
) -> dict[str, object] | None:
    """Build the strict v1 payload, or ``None`` if it would be invalid."""
    return normalize_subagent_payload(
        {
            "subagentVersion": 1,
            "agentType": agent_type,
            "outcome": outcome,
            "modelTurns": model_turns,
            "toolCalls": tool_calls,
            "durationMs": duration_ms,
            "maxTurns": max_turns,
            "resultTruncated": result_truncated,
        }
    )


__all__ = [
    "SUBAGENT_EVENT_TYPE",
    "normalize_subagent_payload",
    "project_subagent_event",
]
