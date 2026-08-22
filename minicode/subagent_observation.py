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

import re
from collections.abc import Mapping


SUBAGENT_EVENT_TYPE = "subagent.completed"

_AGENT_TYPES = frozenset({"explore", "plan", "general", "workflow"})
_OUTCOMES = frozenset({"completed", "failed", "depth_rejected", "budget_exceeded"})
_MAX_COUNT = 100_000
_MAX_DURATION_MS = 86_400_000
_SUBAGENT_ID_RE = re.compile(r"^sub_[0-9a-f]{32}$")
_RESULT_CONTRACT_STATUSES = frozenset(
    {"reported", "fallback", "derived", "unavailable"}
)
_V1_FIELDS = frozenset(
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
_V2_FIELDS = frozenset(
    {
        "subagentVersion",
        "agentType",
        "outcome",
        "modelTurns",
        "toolCalls",
        "durationMs",
        "modelTurnLimit",
        "phaseCount",
        "maxPhases",
        "resultTruncated",
    }
)
_V3_FIELDS = frozenset(
    {
        "subagentVersion",
        "subagentId",
        "agentType",
        "outcome",
        "modelTurns",
        "toolCalls",
        "durationMs",
        "modelTurnLimit",
        "phaseCount",
        "maxPhases",
        "resultTruncated",
        "resultContractStatus",
    }
)


def normalize_subagent_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return the strict v1/v2 whitelist or ``None`` for invalid input.

    An unexpected field is rejected outright rather than quietly dropped: a
    payload carrying anything beyond this contract means the caller is not
    the contract's owner, and silently accepting it would let a future
    change smuggle content into a stream that is required to stay bounded
    and content-free.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("subagentVersion") == 1:
        return _normalize_v1(payload)
    if payload.get("subagentVersion") == 2:
        return _normalize_v2(payload)
    if payload.get("subagentVersion") == 3:
        return _normalize_v3(payload)
    return None


def _normalize_v1(payload: Mapping[str, object]) -> dict[str, object] | None:
    if set(payload) != _V1_FIELDS:
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


def _normalize_v2(payload: Mapping[str, object]) -> dict[str, object] | None:
    """Workflow-only schema separating model-call and orchestration limits."""
    if set(payload) != _V2_FIELDS:
        return None
    agent_type = payload.get("agentType")
    outcome = payload.get("outcome")
    model_turns = payload.get("modelTurns")
    tool_calls = payload.get("toolCalls")
    duration_ms = payload.get("durationMs")
    model_turn_limit = payload.get("modelTurnLimit")
    phase_count = payload.get("phaseCount")
    max_phases = payload.get("maxPhases")
    result_truncated = payload.get("resultTruncated")
    required_counts = (
        model_turns,
        tool_calls,
        duration_ms,
        phase_count,
        max_phases,
    )
    if (
        agent_type != "workflow"
        or outcome not in _OUTCOMES
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in required_counts
        )
        or isinstance(model_turn_limit, bool)
        or (
            model_turn_limit is not None
            and (
                not isinstance(model_turn_limit, int)
                or model_turn_limit < 0
                or model_turn_limit > _MAX_COUNT
            )
        )
        or any(
            value > _MAX_COUNT
            for value in (model_turns, tool_calls, phase_count, max_phases)
        )
        or duration_ms > _MAX_DURATION_MS
        or max_phases < 1
        or phase_count > max_phases
        or (
            model_turn_limit is not None
            and model_turns > model_turn_limit
        )
        or not isinstance(result_truncated, bool)
    ):
        return None
    return {
        "subagentVersion": 2,
        "agentType": agent_type,
        "outcome": outcome,
        "modelTurns": model_turns,
        "toolCalls": tool_calls,
        "durationMs": duration_ms,
        "modelTurnLimit": model_turn_limit,
        "phaseCount": phase_count,
        "maxPhases": max_phases,
        "resultTruncated": result_truncated,
    }


def _normalize_v3(payload: Mapping[str, object]) -> dict[str, object] | None:
    """Unified, correlatable schema for direct agents and workflows."""
    if set(payload) != _V3_FIELDS:
        return None
    subagent_id = payload.get("subagentId")
    agent_type = payload.get("agentType")
    outcome = payload.get("outcome")
    model_turns = payload.get("modelTurns")
    tool_calls = payload.get("toolCalls")
    duration_ms = payload.get("durationMs")
    model_turn_limit = payload.get("modelTurnLimit")
    phase_count = payload.get("phaseCount")
    max_phases = payload.get("maxPhases")
    result_truncated = payload.get("resultTruncated")
    result_contract_status = payload.get("resultContractStatus")
    required_counts = (
        model_turns,
        tool_calls,
        duration_ms,
        phase_count,
        max_phases,
    )
    if (
        not isinstance(subagent_id, str)
        or _SUBAGENT_ID_RE.fullmatch(subagent_id) is None
        or agent_type not in _AGENT_TYPES
        or outcome not in _OUTCOMES
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in required_counts
        )
        or isinstance(model_turn_limit, bool)
        or (
            model_turn_limit is not None
            and (
                not isinstance(model_turn_limit, int)
                or model_turn_limit < 0
                or model_turn_limit > _MAX_COUNT
            )
        )
        or any(
            value > _MAX_COUNT
            for value in (model_turns, tool_calls, phase_count, max_phases)
        )
        or duration_ms > _MAX_DURATION_MS
        or max_phases < 1
        or phase_count > max_phases
        or not isinstance(result_truncated, bool)
        or result_contract_status not in _RESULT_CONTRACT_STATUSES
        or (
            outcome == "completed"
            and result_contract_status == "unavailable"
        )
    ):
        return None
    return {
        "subagentVersion": 3,
        "subagentId": subagent_id,
        "agentType": agent_type,
        "outcome": outcome,
        "modelTurns": model_turns,
        "toolCalls": tool_calls,
        "durationMs": duration_ms,
        "modelTurnLimit": model_turn_limit,
        "phaseCount": phase_count,
        "maxPhases": max_phases,
        "resultTruncated": result_truncated,
        "resultContractStatus": result_contract_status,
    }


def project_subagent_event(
    *,
    agent_type: str,
    outcome: str,
    model_turns: int,
    tool_calls: int,
    duration_ms: int,
    max_turns: int | None = None,
    model_turn_limit: int | None = None,
    phase_count: int | None = None,
    max_phases: int | None = None,
    result_truncated: bool,
    subagent_id: str | None = None,
    result_contract_status: str | None = None,
) -> dict[str, object] | None:
    """Build a strict payload, preserving v1/v2 reader compatibility.

    Producers that provide parent-owned correlation fields get the unified v3
    schema. Omitting both fields retains the legacy v1/v2 projector for stored
    journal replay and compatibility tests.
    """
    if subagent_id is not None or result_contract_status is not None:
        if subagent_id is None or result_contract_status is None:
            return None
        is_workflow = phase_count is not None or max_phases is not None
        if is_workflow:
            if phase_count is None or max_phases is None or max_turns is not None:
                return None
            projected_phase_count = phase_count
            projected_max_phases = max_phases
            projected_model_limit = model_turn_limit
        else:
            if max_turns is None or model_turn_limit is not None:
                return None
            projected_phase_count = 1 if outcome == "completed" else 0
            projected_max_phases = 1
            projected_model_limit = max_turns
        return normalize_subagent_payload(
            {
                "subagentVersion": 3,
                "subagentId": subagent_id,
                "agentType": agent_type,
                "outcome": outcome,
                "modelTurns": model_turns,
                "toolCalls": tool_calls,
                "durationMs": duration_ms,
                "modelTurnLimit": projected_model_limit,
                "phaseCount": projected_phase_count,
                "maxPhases": projected_max_phases,
                "resultTruncated": result_truncated,
                "resultContractStatus": result_contract_status,
            }
        )
    if phase_count is not None or max_phases is not None:
        if phase_count is None or max_phases is None or max_turns is not None:
            return None
        return normalize_subagent_payload(
            {
                "subagentVersion": 2,
                "agentType": agent_type,
                "outcome": outcome,
                "modelTurns": model_turns,
                "toolCalls": tool_calls,
                "durationMs": duration_ms,
                "modelTurnLimit": model_turn_limit,
                "phaseCount": phase_count,
                "maxPhases": max_phases,
                "resultTruncated": result_truncated,
            }
        )
    if max_turns is None or model_turn_limit is not None:
        return None
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
