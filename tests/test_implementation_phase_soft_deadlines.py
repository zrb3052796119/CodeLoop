"""Phase schedules cannot truncate healthy calls or strand repair allowance."""

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.implementation_phases import ImplementationPhase, ImplementationPhaseController
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


# Public host timing from V20, measured from its unchanged 600-second start.
# No model decisions, task solution or private acceptance tests are replayed.
EXECUTOR_STARTED = 87.829
OWNER_DEADLINE = 519.895


def _policy():
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True,
        snapshot=budget.snapshot(), max_steps=28, now=EXECUTOR_STARTED,
        deadline=OWNER_DEADLINE, analysis_complete=True, allow_test_completion=True,
        allow_checked_source_completion=True, require_structured_handoff=True,
    )
    return policy, budget


def _registry():
    return ToolRegistry([
        ToolDefinition(name=name, description=name, input_schema={"type": "object"},
                       validator=lambda value: value,
                       run=lambda _value, _context: ToolResult(ok=True, output="unused"))
        for name in ("read_file", "edit_file", "executor_test", "executor_check", "executor_submit")
    ])


def test_investigation_boundary_changes_the_next_view_without_cutting_the_current_request():
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=28, now=0, deadline=600)
    request = policy.request(budget.snapshot(), estimated_input=1000, now=119)
    assert request.phase is ImplementationPhase.INVESTIGATE
    assert request.deadline == 299  # Normal 180-second request, not the 120-second phase boundary.
    policy.admitted()
    following = policy.request(budget.snapshot(), estimated_input=1000, now=125)
    assert following.phase is ImplementationPhase.IMPLEMENT


def test_v20_healthy_implementation_request_can_finish_across_final_start():
    policy, budget = _policy()
    request = policy.request(budget.snapshot(), estimated_input=24201, now=304.864)
    assert request.phase is ImplementationPhase.IMPLEMENT
    assert policy.final_start == pytest.approx(433.4818)
    assert request.deadline > 450  # A healthy 145-second response may complete.
    assert request.deadline <= 304.864 + 180
    assert request.deadline <= OWNER_DEADLINE
    policy.admitted()
    following = policy.request(budget.snapshot(), estimated_input=25000, now=450)
    assert following.phase is ImplementationPhase.FINALIZE
    assert not following.handoff_only


def test_v20_late_repair_does_not_receive_a_seven_second_phase_deadline():
    policy, budget = _policy()
    original_handoff_start = policy.handoff_start
    policy.observe_verification(failed=True)
    request = policy.request(budget.snapshot(), estimated_input=25978, now=469.275)
    assert request.phase is ImplementationPhase.FINALIZE
    assert not request.handoff_only
    assert request.deadline == pytest.approx(policy.handoff_start)
    assert request.deadline > original_handoff_start
    assert request.deadline <= policy.handoff_start
    assert request.preserve_calls == 1
    assert OWNER_DEADLINE - request.deadline == pytest.approx((OWNER_DEADLINE - 469.275) / 3)


def test_failure_feedback_crossing_old_handoff_floor_keeps_repairs_and_real_submission():
    policy, budget = _policy()
    registry = _registry()
    policy.observe_verification(failed=True)
    calls = []
    for now, name in [(469.275, "edit_file"), (485.0, "executor_test")]:
        request = policy.request(budget.snapshot(), estimated_input=1000, now=now)
        assert not request.handoff_only
        assert name in {tool.name for tool in policy.tool_view(registry).list()}
        assert request.deadline > now
        assert request.deadline < OWNER_DEADLINE
        lease = budget.reserve_model_call(1000, preserve_model_calls=request.preserve_calls,
                                          preserve_total_tokens=request.preserve_tokens)
        policy.admitted()
        budget.record_model_call(input_tokens=1000, output_tokens=100, reservation=lease)
        calls.append(name)
    policy.observe_verification(failed=False)
    final = policy.request(budget.snapshot(), estimated_input=1000, now=497)
    assert final.handoff_only
    assert {tool.name for tool in policy.tool_view(registry).list()} == {"executor_submit"}
    assert final.deadline == OWNER_DEADLINE
    lease = budget.reserve_model_call(1000, preserve_total_tokens=final.preserve_tokens)
    policy.admitted()
    budget.record_model_call(input_tokens=1000, output_tokens=100, reservation=lease)
    calls.append("executor_submit")
    assert calls == ["edit_file", "executor_test", "executor_submit"]
    assert policy.exhausted
    assert budget.snapshot().used_model_calls == 3
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000
    assert policy.deadline == OWNER_DEADLINE


def test_eight_late_failures_cannot_geometrically_consume_the_promised_handoff():
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True,
        snapshot=budget.snapshot(), max_steps=28, now=0, deadline=600,
        analysis_complete=True, allow_test_completion=True,
        allow_checked_source_completion=True, require_structured_handoff=True,
    )
    policy.observe_verification(failed=True)
    now = 590.0
    promised_handoff = (600 - now) / 3
    closing = None
    work_deadlines = []
    for _ in range(8):
        request = policy.request(budget.snapshot(), estimated_input=1000, now=now)
        if request.handoff_only:
            closing = request
            break
        lease = budget.reserve_model_call(1000, preserve_model_calls=request.preserve_calls,
                                          preserve_total_tokens=request.preserve_tokens)
        policy.admitted()
        budget.record_model_call(input_tokens=1000, output_tokens=100, reservation=lease)
        work_deadlines.append(request.deadline)
        now = request.deadline  # Each failed response uses its entire admitted window.
        policy.timeout(now=now)

    assert closing is not None, "late retries kept taking half of the declaration's remaining time"
    assert closing.deadline - now == pytest.approx(promised_handoff)
    assert policy.handoff_start == pytest.approx(600 - promised_handoff)
    assert all(deadline <= policy.handoff_start for deadline in work_deadlines)
    assert policy.deadline == 600
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000
