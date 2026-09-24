"""Atomic parent-side leases for subprocess model-budget accounting."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from minicode.agent_budget import (
    AgentBudgetExceeded,
    AgentTurnBudget,
    SubprocessBudgetLease,
    SubprocessBudgetUsage,
    record_budgeted_model_call,
)

_NONCE = "a" * 64


def test_parallel_subprocess_leases_cannot_each_claim_the_full_balance() -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=6,
        max_cost_usd="1.00",
    )
    barrier = threading.Barrier(2)
    leases: list[SubprocessBudgetLease] = []

    def reserve() -> None:
        barrier.wait(timeout=5)
        leases.append(
            budget.reserve_subprocess_lease(
                requested_total_tokens=80,
                requested_model_calls=4,
                requested_cost_usd="0.75",
                preserve_model_calls=0,
            )
        )

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(leases) == 2
    assert sum(lease.max_total_tokens for lease in leases) == 100
    assert sum(lease.max_model_calls for lease in leases) == 6
    assert sum(lease.max_cost_usd for lease in leases) == Decimal("1.00")
    assert budget.snapshot().reserved_total_tokens == 100
    with pytest.raises(AgentBudgetExceeded):
        budget.reserve_model_call()


def test_subprocess_lease_clamps_after_parent_usage_and_preserves_final_call() -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=8,
        max_cost_usd="1.00",
    )
    reservation = budget.reserve_model_call(10)
    budget.record_model_call(
        input_tokens=20,
        output_tokens=5,
        cost_usd="0.25",
        reservation=reservation,
    )

    lease = budget.reserve_subprocess_lease(
        requested_total_tokens=1_000,
        requested_model_calls=20,
        requested_cost_usd="0.50",
        preserve_model_calls=1,
    )

    assert lease.max_total_tokens == 75
    assert lease.max_model_calls == 6
    assert lease.max_cost_usd == Decimal("0.50")
    # The explicitly preserved parent call remains admissible while the
    # child's finite allowance is active.
    parent_final = budget.reserve_model_call()
    assert parent_final.reservation_id > 0
    with pytest.raises(AgentBudgetExceeded):
        budget.reserve_model_call()


def test_settlement_is_idempotent_and_invalid_usage_charges_the_full_lease() -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=5,
        max_cost_usd="1.00",
    )
    lease = budget.reserve_subprocess_lease(
        requested_total_tokens=80,
        requested_model_calls=4,
        requested_cost_usd="0.75",
        preserve_model_calls=0,
    )
    wrong_lease = SubprocessBudgetLease(
        lease_id=lease.lease_id,
        lease_nonce=lease.lease_nonce,
        max_total_tokens=79,
        max_model_calls=lease.max_model_calls,
        max_cost_usd=lease.max_cost_usd,
    )

    assert budget.settle_subprocess_lease(wrong_lease, None) is None
    charged = budget.settle_subprocess_lease(
        lease,
        SubprocessBudgetUsage(
            lease_id=lease.lease_id + 1,
            lease_nonce=lease.lease_nonce,
            used_model_calls=0,
            input_tokens=0,
            output_tokens=0,
            used_cost_usd=Decimal(0),
        ),
    )

    assert charged == SubprocessBudgetUsage(
        lease_id=lease.lease_id,
        lease_nonce=lease.lease_nonce,
        used_model_calls=4,
        input_tokens=80,
        output_tokens=0,
        used_cost_usd=Decimal("0.75"),
    )
    assert budget.settle_subprocess_lease(lease, None) is None
    snapshot = budget.snapshot()
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_model_calls == 4
    assert snapshot.used_total_tokens == 80
    assert snapshot.used_cost_usd == "0.75"


@pytest.mark.parametrize(
    "value",
    [
        {
            "budgetLeaseVersion": True,
            "leaseId": 1,
            "leaseNonce": _NONCE,
            "maxTotalTokens": 1,
            "maxModelCalls": 1,
            "maxCostUsd": "1",
        },
        {
            "budgetLeaseVersion": 2,
            "leaseId": 1,
            "leaseNonce": _NONCE,
            "maxTotalTokens": 1,
            "maxModelCalls": 1,
            "maxCostUsd": "NaN",
        },
    ],
)
def test_subprocess_lease_protocol_rejects_noncanonical_values(value) -> None:
    with pytest.raises(ValueError):
        SubprocessBudgetLease.from_dict(value)


def test_usage_direct_construction_rejects_negative_or_nonfinite_values() -> None:
    with pytest.raises(ValueError):
        SubprocessBudgetUsage(1, _NONCE, -1, 0, 0, Decimal(0))
    with pytest.raises(ValueError):
        SubprocessBudgetUsage(1, _NONCE, 0, 0, 0, Decimal("Infinity"))


def test_usage_receipt_rejects_pending_model_token_reservation() -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    lease = SubprocessBudgetLease(1, _NONCE, 100, 2, Decimal("1.00"))
    budget.reserve_model_call(80)

    with pytest.raises(ValueError, match="active reservation"):
        budget.subprocess_usage(lease)


@pytest.mark.parametrize(
    ("usage", "cost_payload"),
    [
        (
            {
                "source": "estimated",
                "inputTokens": 10,
                "outputTokens": 2,
                "cacheReadTokens": None,
                "cacheCreationTokens": None,
            },
            {
                "status": "priced",
                "quality": "estimated_usage_catalog_rate",
                "amountNanoUsd": 45_000,
            },
        ),
        (
            {
                "source": "provider",
                "inputTokens": 10,
                "outputTokens": 2,
                "cacheReadTokens": None,
                "cacheCreationTokens": None,
            },
            {"status": "unavailable", "reason": "model_unpriced"},
        ),
    ],
)
def test_usage_receipt_rejects_estimated_usage_or_unpriced_cost(
    usage: dict[str, object],
    cost_payload: dict[str, object],
) -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    lease = SubprocessBudgetLease(1, _NONCE, 100, 2, Decimal("1.00"))
    reservation = budget.reserve_model_call(10)

    record_budgeted_model_call(
        budget,
        model="gpt-4o",
        usage=usage,
        reservation=reservation,
        cost_payload=cost_payload,
    )

    with pytest.raises(ValueError, match="uncertain provider usage"):
        budget.subprocess_usage(lease)


def test_provider_usage_with_catalog_price_can_produce_exact_receipt() -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    lease = SubprocessBudgetLease(1, _NONCE, 100, 2, Decimal("1.00"))
    reservation = budget.reserve_model_call(10)

    record_budgeted_model_call(
        budget,
        model="gpt-4o",
        usage={
            "source": "provider",
            "inputTokens": 10,
            "outputTokens": 2,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        },
        reservation=reservation,
        cost_payload={
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "amountNanoUsd": 45_000,
        },
    )

    usage = budget.subprocess_usage(lease)
    assert usage.used_model_calls == 1
    assert usage.input_tokens == 10
    assert usage.output_tokens == 2
    assert usage.used_cost_usd == Decimal("0.000045")


@pytest.mark.parametrize("charge_estimate", [False, True])
def test_usage_receipt_rejects_zero_token_reservation_and_uncertain_cost(
    charge_estimate: bool,
) -> None:
    lease = SubprocessBudgetLease(1, _NONCE, 100, 2, Decimal("1.00"))
    zero_estimate = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    zero_estimate.reserve_model_call(0)
    with pytest.raises(ValueError, match="active reservation"):
        zero_estimate.subprocess_usage(lease)

    uncertain = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    reservation = uncertain.reserve_model_call(10)
    uncertain.fail_model_call(reservation, charge_estimate=charge_estimate)
    with pytest.raises(ValueError, match="uncertain provider usage"):
        uncertain.subprocess_usage(lease)


def test_usage_receipt_cannot_replay_across_turn_budgets() -> None:
    first = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    first_lease = first.reserve_subprocess_lease(
        requested_total_tokens=50,
        requested_model_calls=1,
        requested_cost_usd="0.50",
        preserve_model_calls=0,
    )
    stale = SubprocessBudgetUsage(
        first_lease.lease_id,
        first_lease.lease_nonce,
        0,
        0,
        0,
        Decimal(0),
    )
    second = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=2,
        max_cost_usd="1.00",
    )
    second_lease = second.reserve_subprocess_lease(
        requested_total_tokens=50,
        requested_model_calls=1,
        requested_cost_usd="0.50",
        preserve_model_calls=0,
    )

    assert first_lease.lease_id == second_lease.lease_id
    assert first_lease.lease_nonce != second_lease.lease_nonce
    charged = second.settle_subprocess_lease(second_lease, stale)
    assert charged is not None
    assert charged.used_model_calls == second_lease.max_model_calls
    assert charged.input_tokens == second_lease.max_total_tokens
