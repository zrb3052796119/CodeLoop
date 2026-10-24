"""One feasibility check before a workflow spends its first model request.

This quotes existing limits; it never reserves or subtracts resources.
Actual worker requests continue to use the shared budget ledger.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from minicode.agent_budget import AgentBudgetSnapshot, AgentTurnBudget, AttributedBudgetView
from minicode.central_scheduler import DispatchStage
from minicode.context_manager import estimate_tokens
from minicode.multi_agent_contracts import WorkflowRequest
from minicode.subagent_types import configured_max_turns

MIN_EXECUTOR_CALLS = 6
MIN_EXECUTOR_SECONDS = 30.0
MIN_EXPLORATION_CALLS_PER_FACET = 2
MIN_PARALLEL_EXPLORATION_SECONDS = 30.0


def reserved_calls_for_snapshot(
    budget: AgentTurnBudget | AttributedBudgetView, snapshot: AgentBudgetSnapshot,
) -> int:
    """Project physical lease occupancy without subtracting a local cap twice.

    Worker views may narrow snapshot limits and protect downstream calls.
    Apply those floors to physical headroom, then retain the local cap.
    """
    root = budget
    preserved_calls = 0
    while isinstance(root, AttributedBudgetView):
        preserved_calls += getattr(root, "_preserve_calls", 0)
        root = root._budget
    if snapshot.limit_model_calls is None or root.max_model_calls is None:
        return 0
    local_remaining = max(0, snapshot.limit_model_calls - snapshot.used_model_calls)
    shared_remaining = max(0, root.max_model_calls - snapshot.used_model_calls
                           - root.reserved_model_calls - preserved_calls)
    return max(0, local_remaining - shared_remaining)


def analysis_allocation(
    snapshot: AgentBudgetSnapshot, remaining_seconds: float | None, *,
    reserved_model_calls: int = 0,
) -> tuple[int, float]:
    """Quote the existing bounded Analyst slice without reserving resources.

    The stage runner uses this same calculation when it issues the slice.
    With no call limit, retain the existing six-call Analyst allowance;
    a completely unbounded workflow does not apply this bounded slice.
    """
    remaining_calls = (max(0, snapshot.limit_model_calls - snapshot.used_model_calls - reserved_model_calls)
                       if snapshot.limit_model_calls is not None else 30)
    calls = min(6, remaining_calls // 5)
    seconds = (min(120.0, max(0.0, remaining_seconds) * 0.2)
               if remaining_seconds is not None else 120.0)
    return calls, seconds


def reviewer_window(remaining_seconds: float) -> float:
    """Time protected from the first Executor, within the owner deadline."""
    return min(remaining_seconds, max(120.0, remaining_seconds * 0.5))


def workflow_preflight_reason(
    *, request: WorkflowRequest, objective: str, stages: Sequence[DispatchStage],
    snapshot: AgentBudgetSnapshot, preserve_calls: int,
    preserve_tokens: int, remaining_seconds: float | None,
    reserved_model_calls: int = 0, exploration_fanout: int = 0,
) -> str | None:
    if not request.structured or request.kind != "change":
        return None
    if (type(reserved_model_calls) is not int or reserved_model_calls < 0
            or type(exploration_fanout) is not int or exploration_fanout < 0):
        raise ValueError("workflow occupancy and exploration fanout must be non-negative integers")
    executor_turns = configured_max_turns("general")
    if executor_turns is not None and executor_turns + 1 < MIN_EXECUTOR_CALLS:
        return (f"configured Executor limit allows {executor_turns + 1} model requests; "
                f"need at least {MIN_EXECUTOR_CALLS}")
    # One investigation request plus its handoff per facet is only a
    # feasibility lower bound. Parallel time is quoted once, not per facet.
    exploration_calls = exploration_fanout * MIN_EXPLORATION_CALLS_PER_FACET
    exploration_seconds = MIN_PARALLEL_EXPLORATION_SECONDS if exploration_fanout else 0.0
    analysis_calls, analysis_seconds = (
        analysis_allocation(
            replace(snapshot, used_model_calls=snapshot.used_model_calls + exploration_calls),
            max(0.0, remaining_seconds - exploration_seconds) if remaining_seconds is not None else None,
            reserved_model_calls=reserved_model_calls,
        )
        if DispatchStage.ANALYST in stages else (0, 0.0)
    )
    if DispatchStage.ANALYST in stages and analysis_calls < 2:
        return "remaining model requests cannot fit the existing Analyst allowance"
    required_calls = MIN_EXECUTOR_CALLS + preserve_calls + analysis_calls + exploration_calls
    if (snapshot.limit_model_calls is not None
            and snapshot.limit_model_calls - snapshot.used_model_calls - reserved_model_calls < required_calls):
        return f"need at least {required_calls} remaining model requests for this route"
    # A lower bound on the first input and its result, not an assurance that
    # the task fits. No speculative six-call reservation is introduced.
    first_input = estimate_tokens(objective + json.dumps(request.to_dict(), ensure_ascii=False))
    if (snapshot.limit_total_tokens is not None
            and snapshot.limit_total_tokens - snapshot.used_total_tokens
            - snapshot.reserved_total_tokens < first_input + preserve_tokens + 2048):
        return "remaining tokens cannot fit the initial task input and existing handoff allowance"
    if remaining_seconds is not None:
        tail = (reviewer_window(remaining_seconds)
                if DispatchStage.REVIEWER in stages else min(30.0, remaining_seconds * 0.1))
        if remaining_seconds - tail - analysis_seconds - exploration_seconds < MIN_EXECUTOR_SECONDS:
            return "remaining time cannot fit analysis, initial execution and the existing delivery window"
    return None
