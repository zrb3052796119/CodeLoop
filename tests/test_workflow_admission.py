"""Workflow feasibility is checked before paid stages without spending budget."""

import json
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.central_scheduler import DispatchStage
from minicode.context_manager import estimate_tokens
from minicode.multi_agent_contracts import parse_workflow_request
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.workflow_admission import (
    analysis_allocation, reserved_calls_for_snapshot, workflow_preflight_reason,
)
from minicode.workflow_runner import _WorkerCallBudget, run_workflow
from test_workflow_v2_structured_routing import _request


VERIFIED = (DispatchStage.EXECUTOR, DispatchStage.VERIFIER)
ANALYZED = (DispatchStage.ANALYST, *VERIFIED)
REVIEWED = (*ANALYZED, DispatchStage.REVIEWER)
OBJECTIVE = "Analyze first, then update allowed.txt and request independent review."


@pytest.fixture(autouse=True)
def stable_role_limits(monkeypatch):
    # Tests must not inherit a developer's private role cap.
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "0")
    monkeypatch.setenv("MINI_CODE_SUBAGENT_PLAN_MAX_TURNS", "0")


def _reason(budget, *, stages=REVIEWED, seconds=900.0, floor=0, request=None):
    return workflow_preflight_reason(
        request=request or parse_workflow_request(_request(), legacy_task_id="task_admission"),
        objective=OBJECTIVE, stages=stages, snapshot=budget.snapshot(),
        preserve_calls=10 if DispatchStage.REVIEWER in stages else 1,
        preserve_tokens=floor, remaining_seconds=seconds,
    )


@pytest.mark.parametrize("calls,expected", [(9, 1), (18, 3), (19, 3), (30, 6), (60, 6), (None, 6)])
def test_analysis_quote_uses_the_stage_slice_and_keeps_ledger_unchanged(calls, expected):
    budget = AgentTurnBudget(max_model_calls=calls)
    before = budget.snapshot()
    assert analysis_allocation(before, 150.0) == (expected, 30.0)
    assert analysis_allocation(before, None) == (expected, 120.0)
    assert analysis_allocation(before, 900.0) == (expected, 120.0)
    assert budget.snapshot() == before


@pytest.mark.parametrize("stages,calls,passes", [
    (VERIFIED, 6, False), (VERIFIED, 7, True),
    (ANALYZED, 9, False), (ANALYZED, 10, True),
    (REVIEWED, 18, False), (REVIEWED, 19, True), (REVIEWED, 20, True),
])
def test_call_threshold_keeps_execution_after_actual_analysis_and_tail(stages, calls, passes):
    budget = AgentTurnBudget(max_model_calls=calls)
    before = budget.snapshot()
    assert (_reason(budget, stages=stages) is None) is passes
    assert budget.snapshot() == before


@pytest.mark.parametrize("seconds,passes", [(150.0, False), (187.499, False), (187.5, True), (900.0, True)])
def test_reviewed_time_threshold_includes_analysis_before_executor(seconds, passes):
    budget = AgentTurnBudget(max_model_calls=40)
    assert (_reason(budget, seconds=seconds) is None) is passes


@pytest.mark.parametrize("turns,passes", [("1", False), ("4", False), ("5", True), ("0", True)])
def test_explicit_executor_cap_includes_closing_call(monkeypatch, turns, passes):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", turns)
    assert (_reason(AgentTurnBudget()) is None) is passes


def test_token_threshold_keeps_parent_floor_and_existing_reservations():
    request = parse_workflow_request(_request(), legacy_task_id="task_admission")
    first_input = estimate_tokens(OBJECTIVE + json.dumps(request.to_dict(), ensure_ascii=False))
    floor, reservation = 5000, 700
    required = first_input + floor + 2048 + reservation
    for shortfall in (0, 1):
        budget = AgentTurnBudget(max_total_tokens=required - shortfall)
        budget.reserve_model_call(reservation)
        before = budget.snapshot()
        assert (_reason(budget, floor=floor, request=request) is None) is (shortfall == 0)
        assert budget.snapshot() == before


def test_fully_unbounded_workflow_does_not_gain_limits_or_reservations():
    budget = AgentTurnBudget()
    before = budget.snapshot()
    assert _reason(budget, seconds=None) is None
    assert budget.snapshot() == before
    assert not budget.has_limits


def _run_actual_workflow(tmp_path, budget, worker, *, seconds=900.0, floor=0):
    (tmp_path / "allowed.txt").write_text("before\n")
    return run_workflow(
        {"description": "workflow admission", "agent_type": "workflow", "prompt": OBJECTIVE,
         "workflow_request": _request()},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"},
                    _agent_budget=budget, _subagent_mailbox=SubagentMailbox(),
                    _task_deadline_monotonic=time.monotonic() + seconds,
                    _logical_agent_preserve_total_tokens=floor),
        depth=0, subagent_id="sub_" + "a" * 32, run_subagent=worker,
    )


@pytest.mark.parametrize("reason", ["analysis_calls", "analysis_time", "executor_cap", "parent_floor", "tokens"])
def test_actual_workflow_rejects_before_any_worker_or_budget_spend(tmp_path, monkeypatch, reason):
    budget = AgentTurnBudget(max_model_calls=18 if reason == "analysis_calls" else 40,
                             max_total_tokens=100 if reason == "tokens" else 500000)
    if reason == "executor_cap":
        monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "1")
    called = []

    def worker(*_args):
        called.append(True)
        return ToolResult(ok=False, output="must not reach a paid stage")

    before = budget.snapshot()
    result = _run_actual_workflow(
        tmp_path, budget, worker, seconds=150 if reason == "analysis_time" else 900,
        floor=500000 if reason == "parent_floor" else 0,
    )
    assert not result.ok
    assert "error[pre_flight_insufficient]" in result.output
    assert called == []
    assert budget.snapshot() == before
    assert (tmp_path / "allowed.txt").read_text() == "before\n"


def test_actual_workflow_at_call_boundary_reaches_first_stage_without_reserving(tmp_path):
    budget = AgentTurnBudget(max_model_calls=19, max_total_tokens=500000)
    called = []

    def worker(_input, context):
        called.append(context._logical_agent_role)
        raise TurnCancellationRequested("turn_" + "b" * 32)

    before = budget.snapshot()
    with pytest.raises(TurnCancellationRequested):
        _run_actual_workflow(tmp_path, budget, worker)
    assert called == ["analyst"]
    assert budget.snapshot() == before


def test_existing_subprocess_lease_rejects_before_any_workflow_worker(tmp_path):
    budget = AgentTurnBudget(max_model_calls=30)
    lease = budget.reserve_subprocess_lease(
        requested_total_tokens=None, requested_model_calls=25,
        requested_cost_usd=None, preserve_model_calls=1,
    )
    before = budget.snapshot()
    called = []
    result = _run_actual_workflow(tmp_path, budget, lambda *_args: called.append(True))
    assert not result.ok and "pre_flight_insufficient" in result.output
    assert called == []
    assert budget.snapshot() == before
    assert budget.reserved_model_calls == lease.max_model_calls == 25
    assert "reservedModelCalls" not in before.to_dict()
    assert (tmp_path / "allowed.txt").read_text() == "before\n"


@pytest.mark.parametrize("lease_calls,expected_occupancy,passes", [(5, 0, True), (25, 5, False)])
def test_lease_quote_retains_local_cap_without_double_subtracting(
    lease_calls, expected_occupancy, passes,
):
    budget = AgentTurnBudget(max_model_calls=30)
    budget.reserve_subprocess_lease(requested_total_tokens=None, requested_model_calls=lease_calls,
                                    requested_cost_usd=None, preserve_model_calls=1)
    local = _WorkerCallBudget(budget.attribution_view("owner"), "local", max_calls=10, preserve_calls=0)
    view = local.attribution_view("workflow")
    before = view.snapshot()
    occupancy = reserved_calls_for_snapshot(view, before)
    assert view.reserved_model_calls == lease_calls
    assert before.limit_model_calls == 10 and occupancy == expected_occupancy
    reason = workflow_preflight_reason(
        request=parse_workflow_request(_request(), legacy_task_id="task_lease_quote"),
        objective="Change allowed.txt.", stages=VERIFIED, snapshot=before,
        preserve_calls=1, preserve_tokens=0, remaining_seconds=900,
        reserved_model_calls=occupancy,
    )
    assert (reason is None) is passes
    assert view.snapshot() == before


def test_lease_quote_also_preserves_nested_downstream_floors():
    budget = AgentTurnBudget(max_model_calls=30)
    budget.reserve_subprocess_lease(requested_total_tokens=None, requested_model_calls=5,
                                    requested_cost_usd=None, preserve_model_calls=1)
    outer = _WorkerCallBudget(budget, "outer", max_calls=None, preserve_calls=4)
    inner = _WorkerCallBudget(outer, "inner", max_calls=None, preserve_calls=6)
    view = inner.attribution_view("workflow")
    snapshot = view.snapshot()
    assert snapshot.limit_model_calls == 20
    assert reserved_calls_for_snapshot(view, snapshot) == 5
    assert snapshot.limit_model_calls - snapshot.used_model_calls - 5 == 15
    assert budget.reserved_model_calls == 5


@pytest.mark.parametrize("stages,calls,passes", [
    (VERIFIED, 12, False), (VERIFIED, 13, True),
    (ANALYZED, 15, False), (ANALYZED, 16, True),
    (REVIEWED, 24, False), (REVIEWED, 25, True),
])
def test_parallel_exploration_quotes_two_calls_per_facet_before_the_role_slice(stages, calls, passes):
    budget = AgentTurnBudget(max_model_calls=calls)
    before = budget.snapshot()
    reason = workflow_preflight_reason(
        request=parse_workflow_request(_request(), legacy_task_id="task_exploration_quote"),
        objective=OBJECTIVE, stages=stages, snapshot=before,
        preserve_calls=10 if DispatchStage.REVIEWER in stages else 1,
        preserve_tokens=0, remaining_seconds=900, exploration_fanout=3,
    )
    assert (reason is None) is passes
    assert budget.snapshot() == before


@pytest.mark.parametrize("fanout", [1, 3])
@pytest.mark.parametrize("seconds,passes", [(217.499, False), (217.5, True)])
def test_parallel_exploration_time_quote_is_one_shared_thirty_second_window(fanout, seconds, passes):
    reason = workflow_preflight_reason(
        request=parse_workflow_request(_request(), legacy_task_id="task_exploration_time"),
        objective=OBJECTIVE, stages=REVIEWED, snapshot=AgentTurnBudget().snapshot(),
        preserve_calls=10, preserve_tokens=0, remaining_seconds=seconds,
        exploration_fanout=fanout,
    )
    assert (reason is None) is passes
