"""Turn-local native review allowances on the existing shared budget ledger.

These limits never certify a candidate. Each phase keeps its first deadline
and budget view; switching phases only selects those existing allowances.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget, LimitedBudgetView
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline


PHASE_LIMITS = {"review": (8, 90.0), "inspection": (8, 150.0), "reconciliation": (4, 60.0)}


def create_review_limits(context, *, closing_tokens: int = 0):
    mode = os.environ.get("MINI_CODE_COMPLETION_REVIEW_MODE", "bounded").strip().lower()
    if mode not in {"bounded", "full"}:
        raise ValueError("MINI_CODE_COMPLETION_REVIEW_MODE must be bounded or full")
    if mode == "full":
        return None
    now = time.monotonic()
    ancestor = context._task_deadline_monotonic
    remaining = max(0.0, ancestor - now) if ancestor is not None else None
    reserve = max(60.0, remaining * 0.15) if remaining is not None else 60.0
    seconds = max(0.0, min(remaining * 0.5, remaining - reserve)) if remaining is not None else 300.0
    deadline = min(ancestor, now + seconds) if ancestor is not None else now + seconds
    parent = context._agent_budget
    if parent is None:
        # An otherwise unmetered caller still needs one shared ledger for the
        # three local caps. This introduces no independent spend allowance.
        parent = AgentTurnBudget(max_total_tokens=None, max_model_calls=None, max_cost_usd=None)
    return {"parent": parent, "ancestorDeadline": ancestor,
        "deadline": deadline, "closingSeconds": reserve,
        "budget": LimitedBudgetView(parent, "completion_review", max_calls=None,
            preserve_calls=2, preserve_tokens=closing_tokens),
        "stages": {}, "active": None}


def select_review_phase(host, phase: str | None) -> None:
    """Select a sibling phase on this private review context, including tools.

    The host is shared by the review's tool closures, so probes and nested
    adjudicators inherit exactly the currently selected phase allowance.
    """
    limits = getattr(host, "_completion_review_limits", None)
    if limits is None:
        return
    budget, deadline = limits["budget"], limits["deadline"]
    if phase is not None:
        calls, seconds = PHASE_LIMITS[phase]
        if phase not in limits["stages"]:
            limits["stages"][phase] = {
                "budget": LimitedBudgetView(budget, "completion_" + phase,
                    max_calls=calls, preserve_calls=0),
                "deadline": min(deadline, time.monotonic() + seconds), "calls": calls}
        stage = limits["stages"][phase]
        budget, deadline = stage["budget"], stage["deadline"]
    limits["active"] = phase
    host._agent_budget = budget
    host._task_deadline_monotonic = deadline
    raise_task_deadline(host)


def review_steps(host) -> int | None:
    limits = getattr(host, "_completion_review_limits", None)
    if limits is None or limits["active"] is None:
        return None
    stage = limits["stages"][limits["active"]]
    remaining = stage["calls"] - stage["budget"].admitted_model_calls
    if remaining <= 0:
        raise AgentBudgetExceeded("completion review phase call allowance exhausted", stage["budget"].snapshot())
    return remaining


def check_review_allowance(host) -> None:
    if getattr(host, "_completion_review_limits", None) is None:
        return
    raise_task_deadline(host)
    review_steps(host)


def review_exhaustion(limits, error) -> str | None:
    """Identify local exhaustion without masking an expired ancestor deadline.

    Cancellation and non-budget errors are never handled here. A spent shared
    ledger remains the ancestor's error; a preserved closing floor or a phase
    cap is a review-local stop.
    """
    if limits is None or not isinstance(error, (AgentBudgetExceeded, TaskDeadlineExceeded)):
        return None
    now = time.monotonic()
    ancestor = limits["ancestorDeadline"]
    if ancestor is not None and now >= ancestor:
        return None
    parent = limits["parent"]
    shared = parent.snapshot()
    if (shared.limit_model_calls is not None
            and shared.used_model_calls + parent.reserved_model_calls >= shared.limit_model_calls):
        return None
    if (shared.limit_total_tokens is not None
            and shared.used_total_tokens + shared.reserved_total_tokens >= shared.limit_total_tokens):
        return None
    if (shared.limit_cost_usd is not None
            and Decimal(shared.used_cost_usd) >= Decimal(shared.limit_cost_usd)):
        return None
    if isinstance(error, AgentBudgetExceeded) and error.reason == "cost budget exhausted":
        return None
    phase = limits["active"]
    stage = limits["stages"].get(phase)
    if now >= limits["deadline"] or (stage is not None and now >= stage["deadline"]):
        return "wall_clock"
    if not isinstance(error, AgentBudgetExceeded):
        return None
    if stage is not None and stage["budget"].admitted_model_calls >= stage["calls"]:
        return "model_calls"
    return "closing_reserve"
