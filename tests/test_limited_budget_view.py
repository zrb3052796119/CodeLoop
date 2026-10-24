"""Nested admission limits share accounting and preserve the parent's tail once."""

from decimal import Decimal

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget, LimitedBudgetView


def _settled_call(view, *, input_tokens=3, output_tokens=2):
    reservation = view.reserve_model_call(input_tokens)
    view.record_model_call(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd="0.01",
        reservation=reservation,
    )


def test_workflow_private_name_remains_the_same_class():
    from minicode.workflow_runner import _WorkerCallBudget

    assert _WorkerCallBudget is LimitedBudgetView


def test_eight_eight_four_stages_leave_two_parent_calls_without_double_charging():
    shared = AgentTurnBudget(max_model_calls=22, max_total_tokens=1000)
    chain = LimitedBudgetView(shared, "review-chain", max_calls=None, preserve_calls=2)
    before = shared.snapshot()
    stages = []
    admitted = 0

    for index, allowance in enumerate((8, 8, 4)):
        stage = LimitedBudgetView(
            chain, f"stage-{index}", max_calls=allowance, preserve_calls=0
        )
        stages.append(stage)
        assert stage.snapshot().limit_model_calls == admitted + allowance
        assert shared.snapshot().used_model_calls == admitted
        for _ in range(allowance):
            _settled_call(stage)
        admitted += allowance
        with pytest.raises(AgentBudgetExceeded) as error:
            stage.reserve_model_call()
        assert error.value.reason == "worker model call budget exhausted"
        assert stage.admitted_model_calls == allowance
        assert stage.attributed_tokens() == (allowance * 3, allowance * 2)

    assert before.used_model_calls == 0
    assert before.reserved_total_tokens == 0
    assert chain.snapshot().limit_model_calls == 20
    assert shared.snapshot().limit_model_calls == 22
    assert shared.snapshot().used_model_calls == chain.admitted_model_calls == 20
    assert shared.snapshot().used_total_tokens == 100
    assert shared.snapshot().reserved_total_tokens == 0
    assert Decimal(shared.snapshot().used_cost_usd) == Decimal("0.20")
    assert chain.attributed_tokens() == (60, 40)

    with pytest.raises(AgentBudgetExceeded) as error:
        chain.reserve_model_call()
    assert error.value.reason == "model call budget exhausted"
    assert chain.admitted_model_calls == 20
    for _ in range(2):
        _settled_call(shared)
    assert shared.snapshot().used_model_calls == 22
    assert shared.snapshot().used_total_tokens == 110
    assert chain.attributed_tokens() == (60, 40)
    assert [stage.admitted_model_calls for stage in stages] == [8, 8, 4]


@pytest.mark.parametrize("charge_estimate", [False, True])
def test_failed_attempt_counts_in_both_limits_and_settles_only_once(charge_estimate):
    shared = AgentTurnBudget(max_model_calls=4, max_total_tokens=100)
    chain = LimitedBudgetView(shared, "chain", max_calls=None, preserve_calls=2)
    stage = LimitedBudgetView(chain, "stage", max_calls=2, preserve_calls=0)
    failed = stage.reserve_model_call(17)

    assert stage.fail_model_call(failed, charge_estimate=charge_estimate)
    assert not stage.fail_model_call(failed, charge_estimate=charge_estimate)
    assert shared.snapshot().used_model_calls == 1
    assert stage.admitted_model_calls == chain.admitted_model_calls == 1
    assert shared.snapshot().used_total_tokens == (17 if charge_estimate else 0)
    assert shared.snapshot().reserved_total_tokens == 0
    assert shared.snapshot().usage_uncertain

    _settled_call(stage)
    before_rejection = shared.snapshot()
    with pytest.raises(AgentBudgetExceeded) as error:
        stage.reserve_model_call()
    assert error.value.reason == "worker model call budget exhausted"
    assert shared.snapshot() == before_rejection
    assert stage.admitted_model_calls == chain.admitted_model_calls == 2
    assert shared.snapshot().used_total_tokens == 5 + (17 if charge_estimate else 0)


@pytest.mark.parametrize(
    "limits,estimate,cost,reason",
    [
        ({"max_model_calls": 3}, 0, None, "model call budget exhausted"),
        ({"max_total_tokens": 10}, 11, None, "token budget exhausted"),
        ({"max_cost_usd": "0.10"}, 0, "0.10", "cost budget exhausted"),
    ],
)
def test_upstream_ledger_rejection_does_not_count_as_local_admission(
    limits, estimate, cost, reason
):
    shared = AgentTurnBudget(**limits)
    initial = shared.reserve_model_call()
    shared.record_model_call(cost_usd=cost, reservation=initial)
    chain = LimitedBudgetView(shared, "chain", max_calls=None, preserve_calls=2)
    stage = LimitedBudgetView(chain, "stage", max_calls=8, preserve_calls=0)
    before = shared.snapshot()

    with pytest.raises(AgentBudgetExceeded) as error:
        stage.reserve_model_call(estimate)

    assert error.value.reason == reason
    assert shared.snapshot() == before
    assert stage.admitted_model_calls == chain.admitted_model_calls == 0


def test_upstream_local_cap_rejection_does_not_increment_descendant_count():
    shared = AgentTurnBudget(max_model_calls=40)
    chain = LimitedBudgetView(shared, "chain", max_calls=1, preserve_calls=2)
    stage = LimitedBudgetView(chain, "stage", max_calls=8, preserve_calls=0)
    _settled_call(stage)
    before = shared.snapshot()

    with pytest.raises(AgentBudgetExceeded) as error:
        stage.reserve_model_call()

    assert error.value.reason == "worker model call budget exhausted"
    assert stage.admitted_model_calls == chain.admitted_model_calls == 1
    assert shared.snapshot() == before


def test_nested_readonly_consumer_shares_stage_cap_and_preserves_attribution():
    shared = AgentTurnBudget(max_model_calls=22)
    chain = LimitedBudgetView(shared, "chain", max_calls=None, preserve_calls=2)
    stage = LimitedBudgetView(chain, "stage", max_calls=8, preserve_calls=0)
    inspection = LimitedBudgetView(stage, "inspection", max_calls=8, preserve_calls=0)
    leaf = inspection.attribution_view("leaf")

    for _ in range(3):
        _settled_call(stage)
    for _ in range(5):
        _settled_call(leaf)
    before = shared.snapshot()
    with pytest.raises(AgentBudgetExceeded) as error:
        leaf.reserve_model_call()

    assert error.value.reason == "worker model call budget exhausted"
    assert shared.snapshot() == before
    assert stage.admitted_model_calls == chain.admitted_model_calls == 8
    assert inspection.admitted_model_calls == 5
    assert shared.snapshot().used_model_calls == 8
    assert shared.snapshot().used_total_tokens == 40
    assert chain.attributed_tokens() == stage.attributed_tokens() == (24, 16)
    assert inspection.attributed_tokens() == leaf.attributed_tokens() == (15, 10)


def test_snapshot_excludes_token_floor_without_reserving_or_charging_it_twice():
    shared = AgentTurnBudget(max_model_calls=22, max_total_tokens=1000)
    chain = LimitedBudgetView(
        shared, "chain", max_calls=None, preserve_calls=2, preserve_tokens=200
    )
    stage = LimitedBudgetView(chain, "stage", max_calls=8, preserve_calls=0)
    assert chain.snapshot().limit_total_tokens == stage.snapshot().limit_total_tokens == 800
    assert shared.snapshot().limit_total_tokens == 1000
    assert shared.snapshot().used_total_tokens == 0
    assert shared.snapshot().reserved_total_tokens == 0

    lease = stage.reserve_model_call(800)
    assert shared.snapshot().reserved_total_tokens == 800
    with pytest.raises(AgentBudgetExceeded) as error:
        stage.reserve_model_call(1)
    assert error.value.reason == "token budget exhausted"
    assert stage.admitted_model_calls == chain.admitted_model_calls == 1
    stage.record_model_call(input_tokens=750, output_tokens=50, reservation=lease)
    assert shared.snapshot().used_total_tokens == 800
    assert shared.snapshot().reserved_total_tokens == 0
    closing = shared.reserve_model_call(200)
    shared.record_model_call(input_tokens=190, output_tokens=10, reservation=closing)
    assert shared.snapshot().used_total_tokens == 1000
    assert shared.snapshot().used_model_calls == 2
    assert chain.attributed_tokens() == stage.attributed_tokens() == (750, 50)


def test_local_token_allowance_is_relative_to_existing_usage_and_nested_snapshot():
    shared = AgentTurnBudget(max_model_calls=22, max_total_tokens=1000)
    _settled_call(shared, input_tokens=90, output_tokens=10)
    chain = LimitedBudgetView(
        shared, "chain", max_calls=None, preserve_calls=2, preserve_tokens=200
    )
    stage = LimitedBudgetView(
        chain, "stage", max_calls=8, preserve_calls=0, max_tokens=300
    )
    assert stage.snapshot().limit_total_tokens == 400
    assert stage.snapshot().used_total_tokens == 100
    pending = stage.reserve_model_call(300)
    before = shared.snapshot()
    with pytest.raises(AgentBudgetExceeded) as error:
        stage.reserve_model_call(1)
    assert error.value.reason == "worker token allowance exhausted"
    assert shared.snapshot() == before
    assert stage.admitted_model_calls == chain.admitted_model_calls == 1
    stage.record_model_call(input_tokens=280, output_tokens=20, reservation=pending)
    assert shared.snapshot().used_total_tokens == 400
    assert stage.attributed_tokens() == chain.attributed_tokens() == (280, 20)


def test_local_cap_still_applies_when_shared_budget_is_unlimited():
    shared = AgentTurnBudget()
    chain = LimitedBudgetView(shared, "chain", max_calls=None, preserve_calls=2)
    stage = LimitedBudgetView(chain, "stage", max_calls=4, preserve_calls=0)
    assert chain.snapshot().limit_model_calls is None
    assert stage.snapshot().limit_model_calls == 4
    assert stage.snapshot().limit_total_tokens is None
    for _ in range(4):
        _settled_call(stage)
    with pytest.raises(AgentBudgetExceeded) as error:
        stage.reserve_model_call()
    assert error.value.reason == "worker model call budget exhausted"
    assert shared.snapshot().limit_model_calls is None
    assert shared.snapshot().used_model_calls == chain.admitted_model_calls == 4


@pytest.mark.parametrize("inspection_calls", [2, 8])
def test_interleaved_sibling_calls_do_not_consume_local_snapshot_allowance(inspection_calls):
    shared = AgentTurnBudget(max_model_calls=40)
    chain = LimitedBudgetView(shared, "chain", max_calls=None, preserve_calls=2)
    review = LimitedBudgetView(chain, "review", max_calls=8, preserve_calls=0)
    for _ in range(3):
        _settled_call(review)
    inspection = LimitedBudgetView(chain, "inspection", max_calls=8, preserve_calls=0)
    for _ in range(inspection_calls):
        _settled_call(inspection)

    snapshot = review.snapshot()
    assert snapshot.used_model_calls == 3 + inspection_calls
    assert snapshot.limit_model_calls - snapshot.used_model_calls == 5
    assert review.admitted_model_calls == 3
    for _ in range(5):
        _settled_call(review)
    assert review.snapshot().limit_model_calls == review.snapshot().used_model_calls
    with pytest.raises(AgentBudgetExceeded) as error:
        review.reserve_model_call()
    assert error.value.reason == "worker model call budget exhausted"


def test_interleaved_snapshot_never_expands_ancestor_limit_or_parent_floor():
    shared = AgentTurnBudget(max_model_calls=12)
    chain = LimitedBudgetView(shared, "chain", max_calls=None, preserve_calls=2)
    review = LimitedBudgetView(chain, "review", max_calls=8, preserve_calls=0)
    for _ in range(3):
        _settled_call(review)
    inspection = LimitedBudgetView(chain, "inspection", max_calls=8, preserve_calls=0)
    for _ in range(4):
        _settled_call(inspection)

    snapshot = review.snapshot()
    assert snapshot.limit_model_calls == chain.snapshot().limit_model_calls == 10
    assert snapshot.limit_model_calls - snapshot.used_model_calls == 3
    assert shared.snapshot().limit_model_calls == 12
    assert review.admitted_model_calls == 3
    for _ in range(3):
        _settled_call(review)
    assert review.admitted_model_calls == 6
    assert review.snapshot().limit_model_calls == review.snapshot().used_model_calls == 10
    with pytest.raises(AgentBudgetExceeded) as error:
        review.reserve_model_call()
    assert error.value.reason == "model call budget exhausted"
    assert review.admitted_model_calls == 6
    _settled_call(shared)
    _settled_call(shared)
    assert shared.snapshot().used_model_calls == 12
