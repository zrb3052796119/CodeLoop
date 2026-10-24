"""Native review limits use existing admission/deadline authority, without I/O."""

from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget, LimitedBudgetView
from minicode import completion_review_limits as limits_api
from minicode import task_deadline
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline
from minicode.tooling import ToolContext
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    controlled_time = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(limits_api, "time", controlled_time)
    monkeypatch.setattr(task_deadline, "time", controlled_time)
    monkeypatch.delenv("MINI_CODE_COMPLETION_REVIEW_MODE", raising=False)
    return clock


def _context(*, deadline=None, budget=None, token=None, closing_tokens=0):
    host = ToolContext(
        _agent_budget=budget,
        _task_deadline_monotonic=deadline,
        _cancellation_token=token,
    )
    host._completion_review_limits = limits_api.create_review_limits(
        host, closing_tokens=closing_tokens
    )
    return host


def _call(budget, *, tokens=1):
    reservation = budget.reserve_model_call(tokens)
    budget.record_model_call(input_tokens=tokens, reservation=reservation)


@pytest.mark.parametrize(
    "remaining,seconds,reserve",
    [(600, 300, 90), (1000, 500, 150), (100, 40, 60), (61, 1, 60),
     (60, 0, 60), (20, 0, 60), (0, 0, 60)],
)
def test_bounded_chain_uses_at_most_half_and_keeps_parent_time(
    clock, remaining, seconds, reserve
):
    shared = AgentTurnBudget(max_model_calls=40)
    deadline = clock.now + remaining
    host = _context(deadline=deadline, budget=shared)
    limits = host._completion_review_limits
    assert limits["deadline"] == clock.now + seconds
    assert limits["deadline"] <= deadline
    assert limits["closingSeconds"] == reserve
    assert limits["ancestorDeadline"] == deadline
    # Creating limits does not mutate the caller's original authority.
    assert host._agent_budget is shared
    assert host._task_deadline_monotonic == deadline
    assert shared.snapshot().used_model_calls == 0
    assert shared.snapshot().reserved_total_tokens == 0


def test_expired_ancestor_is_never_extended_to_current_time(clock):
    host = _context(deadline=clock.now - 1)
    assert host._completion_review_limits["deadline"] == clock.now - 1
    with pytest.raises(TaskDeadlineExceeded):
        limits_api.select_review_phase(host, "review")
    assert host._task_deadline_monotonic == clock.now - 1


def test_without_finite_ancestor_chain_has_300_seconds_and_one_shared_ledger(clock):
    host = _context()
    limits = host._completion_review_limits
    assert limits["deadline"] == 400
    assert limits["closingSeconds"] == 60
    assert limits["ancestorDeadline"] is None
    assert isinstance(limits["parent"], AgentTurnBudget)
    assert not limits["parent"].has_limits
    limits_api.select_review_phase(host, "review")
    _call(host._agent_budget)
    limits_api.select_review_phase(host, "inspection")
    _call(host._agent_budget)
    assert limits["parent"].snapshot().used_model_calls == 2
    assert limits["budget"].admitted_model_calls == 2


def test_phase_deadlines_are_siblings_and_first_selection_is_permanent(clock):
    host = _context(deadline=700, budget=AgentTurnBudget(max_model_calls=40))
    limits = host._completion_review_limits
    limits_api.select_review_phase(host, "review")
    review_budget = host._agent_budget
    assert host._task_deadline_monotonic == 190
    assert limits_api.review_steps(host) == 8
    _call(review_budget)
    clock.now = 150
    limits_api.select_review_phase(host, "inspection")
    assert host._task_deadline_monotonic == 300  # Not the review's 190.
    assert limits_api.review_steps(host) == 8
    inspection_budget = host._agent_budget
    _call(inspection_budget)
    _call(inspection_budget)
    clock.now = 170
    limits_api.select_review_phase(host, "review")
    assert host._agent_budget is review_budget
    assert host._task_deadline_monotonic == 190
    assert limits_api.review_steps(host) == 7
    review_snapshot = review_budget.snapshot()
    assert review_snapshot.limit_model_calls - review_snapshot.used_model_calls == 7
    limits_api.select_review_phase(host, "inspection")
    assert host._agent_budget is inspection_budget
    assert host._task_deadline_monotonic == 300
    clock.now = 260
    limits_api.select_review_phase(host, "reconciliation")
    assert host._task_deadline_monotonic == 320
    assert limits_api.review_steps(host) == 4
    assert limits["deadline"] == 400
    limits_api.select_review_phase(host, None)
    assert host._agent_budget is limits["budget"]
    assert host._task_deadline_monotonic == 400
    assert limits_api.review_steps(host) is None


def test_new_phase_near_chain_end_cannot_extend_chain_deadline(clock):
    host = _context(deadline=700)
    clock.now = 390
    limits_api.select_review_phase(host, "inspection")
    assert host._task_deadline_monotonic == 400
    clock.now = 399
    limits_api.select_review_phase(host, "reconciliation")
    assert host._task_deadline_monotonic == 400
    clock.now = 400
    with pytest.raises(TaskDeadlineExceeded):
        limits_api.check_review_allowance(host)


def test_expired_phase_cannot_replenish_on_reentry(clock):
    host = _context(deadline=700)
    limits_api.select_review_phase(host, "review")
    original = host._agent_budget
    clock.now = 190
    with pytest.raises(TaskDeadlineExceeded) as error:
        limits_api.select_review_phase(host, "review")
    assert host._agent_budget is original
    assert host._task_deadline_monotonic == 190
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) == "wall_clock"


def test_eight_eight_four_share_one_parent_call_and_token_floor(clock):
    shared = AgentTurnBudget(max_model_calls=22, max_total_tokens=30)
    host = _context(deadline=700, budget=shared, closing_tokens=10)
    limits = host._completion_review_limits
    for phase, calls in (("review", 8), ("inspection", 8), ("reconciliation", 4)):
        limits_api.select_review_phase(host, phase)
        assert isinstance(host._agent_budget, LimitedBudgetView)
        assert host._agent_budget.snapshot().limit_total_tokens == 20
        for remaining in range(calls, 0, -1):
            assert limits_api.review_steps(host) == remaining
            _call(host._agent_budget)
        with pytest.raises(AgentBudgetExceeded) as error:
            limits_api.check_review_allowance(host)
        assert limits_api.review_exhaustion(limits, error.value) == "model_calls"
    assert shared.snapshot().used_model_calls == limits["budget"].admitted_model_calls == 20
    assert shared.snapshot().used_total_tokens == 20
    assert shared.snapshot().reserved_total_tokens == 0
    assert limits["budget"].snapshot().limit_model_calls == 20
    assert limits["budget"].snapshot().limit_total_tokens == 20
    _call(shared, tokens=5)
    _call(shared, tokens=5)
    assert shared.snapshot().used_model_calls == 22
    assert shared.snapshot().used_total_tokens == 30


def test_failed_attempt_is_not_refunded_by_reselecting_phase(clock):
    shared = AgentTurnBudget(max_model_calls=10)
    host = _context(deadline=700, budget=shared)
    limits_api.select_review_phase(host, "review")
    original = host._agent_budget
    failed = original.reserve_model_call(5)
    original.fail_model_call(failed)
    limits_api.select_review_phase(host, None)
    limits_api.select_review_phase(host, "review")
    assert host._agent_budget is original
    assert limits_api.review_steps(host) == 7
    assert shared.snapshot().used_model_calls == 1
    assert shared.snapshot().reserved_total_tokens == 0


@pytest.mark.parametrize("kind", ["calls", "tokens"])
def test_preserved_parent_floor_is_local_exhaustion_not_global_spend(clock, kind):
    shared = AgentTurnBudget(
        max_model_calls=2 if kind == "calls" else 40,
        max_total_tokens=10,
    )
    host = _context(deadline=700, budget=shared, closing_tokens=10)
    limits_api.select_review_phase(host, "review")
    before = shared.snapshot()
    with pytest.raises(AgentBudgetExceeded) as error:
        host._agent_budget.reserve_model_call(1)
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) == "closing_reserve"
    assert shared.snapshot() == before
    assert host._agent_budget.admitted_model_calls == 0


@pytest.mark.parametrize("kind", ["calls", "tokens", "cost"])
def test_spent_ancestor_budget_is_not_reclassified_as_local(clock, kind):
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=100, max_cost_usd="1")
    host = _context(deadline=700, budget=shared)
    limits_api.select_review_phase(host, "review")
    if kind == "calls":
        for _ in range(40):
            _call(shared)
    elif kind == "tokens":
        _call(shared, tokens=100)
    else:
        reservation = shared.reserve_model_call()
        shared.record_model_call(cost_usd="1", reservation=reservation)
    with pytest.raises(AgentBudgetExceeded) as error:
        host._agent_budget.reserve_model_call(1)
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) is None


@pytest.mark.parametrize("kind", ["calls", "tokens", "cost"])
@pytest.mark.parametrize("also_expired", [False, True])
def test_ancestor_spend_takes_precedence_over_simultaneous_local_exhaustion(
    clock, kind, also_expired
):
    shared = AgentTurnBudget(max_model_calls=10, max_total_tokens=100, max_cost_usd="1")
    host = _context(deadline=700, budget=shared)
    limits_api.select_review_phase(host, "review")
    for _ in range(8):
        _call(host._agent_budget)
    if kind == "calls":
        _call(shared)
        _call(shared)
    elif kind == "tokens":
        _call(shared, tokens=92)
    else:
        reservation = shared.reserve_model_call()
        shared.record_model_call(cost_usd="1", reservation=reservation)
    if also_expired:
        clock.now = 190
    with pytest.raises((AgentBudgetExceeded, TaskDeadlineExceeded)) as error:
        limits_api.check_review_allowance(host)
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) is None


def test_reserved_ancestor_cost_failure_is_not_hidden_by_local_deadline(clock):
    shared = AgentTurnBudget(max_model_calls=40, max_cost_usd="1")
    host = _context(deadline=700, budget=shared)
    limits_api.select_review_phase(host, "review")
    shared.reserve_subprocess_lease(
        requested_total_tokens=1, requested_model_calls=1,
        requested_cost_usd="1", preserve_model_calls=0,
    )
    with pytest.raises(AgentBudgetExceeded) as error:
        host._agent_budget.reserve_model_call()
    assert error.value.reason == "cost budget exhausted"
    assert shared.snapshot().used_cost_usd == "0.0"
    clock.now = 190
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) is None


def test_ancestor_deadline_takes_precedence_over_local_exhaustion(clock):
    host = _context(deadline=700)
    limits_api.select_review_phase(host, "review")
    clock.now = 700
    with pytest.raises(TaskDeadlineExceeded) as error:
        limits_api.check_review_allowance(host)
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) is None


def test_cancellation_is_preserved_even_when_local_deadline_has_also_elapsed(clock):
    token = TurnCancellationToken("turn_" + "1" * 32)
    host = _context(deadline=700, token=token)
    limits_api.select_review_phase(host, "review")
    clock.now = 200
    token.request()
    with pytest.raises(TurnCancellationRequested) as error:
        limits_api.check_review_allowance(host)
    assert host._cancellation_token is token
    assert limits_api.review_exhaustion(host._completion_review_limits, error.value) is None


@pytest.mark.parametrize("now", [100, 200, 450])
def test_non_budget_error_is_never_masked_by_elapsed_time(clock, now):
    host = _context(deadline=700)
    limits_api.select_review_phase(host, "review")
    clock.now = now
    assert limits_api.review_exhaustion(host._completion_review_limits, ValueError("bad evidence")) is None


def test_full_mode_helpers_do_not_add_limits_or_deadline_checkpoints(clock, monkeypatch):
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "full")
    shared = AgentTurnBudget(max_model_calls=40)
    token = TurnCancellationToken("turn_" + "2" * 32)
    token.request()
    host = _context(deadline=99, budget=shared, token=token, closing_tokens=100)
    assert host._completion_review_limits is None
    limits_api.select_review_phase(host, "review")
    assert limits_api.review_steps(host) is None
    limits_api.check_review_allowance(host)
    assert host._agent_budget is shared
    assert host._task_deadline_monotonic == 99
    assert host._cancellation_token is token
    assert shared.snapshot().used_model_calls == 0
    assert limits_api.review_exhaustion(None, TaskDeadlineExceeded(1)) is None
    # Existing checkpoints still enforce cancellation; full did not disable it.
    with pytest.raises(TurnCancellationRequested):
        raise_task_deadline(host)


def test_legacy_duck_context_without_new_field_retains_unlimited_helpers(clock):
    shared = AgentTurnBudget(max_model_calls=40)
    host = SimpleNamespace(_agent_budget=shared, _task_deadline_monotonic=99)
    limits_api.select_review_phase(host, "review")
    assert limits_api.review_steps(host) is None
    limits_api.check_review_allowance(host)
    assert host._agent_budget is shared
    assert host._task_deadline_monotonic == 99


def test_invalid_mode_is_rejected_without_charging_parent(clock, monkeypatch):
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "anything")
    shared = AgentTurnBudget(max_model_calls=40)
    with pytest.raises(ValueError, match="must be bounded or full"):
        _context(budget=shared)
    assert shared.snapshot().used_model_calls == 0
