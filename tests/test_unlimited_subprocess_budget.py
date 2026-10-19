"""Null lease dimensions preserve accounting and process cancellation."""

from dataclasses import replace
from decimal import Decimal
import os
import sys
import threading

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget, SubprocessBudgetLease, SubprocessBudgetUsage
from minicode.subagent_supervisor import run_subagent_process
from minicode import subagent_worker
from minicode.task_outcome import canonicalize_task_outcome
from minicode.turn_cancellation import TurnCancellationToken
from test_subagent_subprocess import _request, _run_worker_in_process, _RESULT_CHILD


def _unlimited_lease(budget):
    return budget.reserve_subprocess_lease(requested_total_tokens=None,
        requested_model_calls=None, requested_cost_usd=None, preserve_model_calls=0)


def test_null_lease_roundtrip_and_exact_actual_usage_settlement():
    parent = AgentTurnBudget()
    lease = _unlimited_lease(parent)
    assert (lease.max_total_tokens, lease.max_model_calls, lease.max_cost_usd) == (None, None, None)
    assert SubprocessBudgetLease.from_dict(lease.to_dict()) == lease
    child = AgentTurnBudget(max_total_tokens=lease.max_total_tokens,
        max_model_calls=lease.max_model_calls, max_cost_usd=lease.max_cost_usd)
    reservation = child.reserve_model_call(4_000_000)
    child.record_model_call(input_tokens=4_000_000, output_tokens=300,
        cost_usd="6.50", reservation=reservation)
    usage = child.subprocess_usage(lease)
    assert lease.accepts(usage)
    assert not lease.accepts(replace(usage, lease_nonce="b" * 64))
    assert parent.settle_subprocess_lease(lease, usage) == usage
    assert parent.settle_subprocess_lease(lease, usage) is None
    snapshot = parent.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.used_total_tokens == 4_000_300
    assert Decimal(snapshot.used_cost_usd) == Decimal("6.50")
    assert snapshot.reserved_total_tokens == 0
    assert not snapshot.to_dict()["usageUncertain"]


def test_unlimited_request_respects_finite_parent_atomically():
    parent = AgentTurnBudget(max_total_tokens=100, max_model_calls=4, max_cost_usd="1")
    first = parent.reserve_model_call(10)
    parent.record_model_call(input_tokens=10, output_tokens=10, cost_usd="0.25", reservation=first)
    lease = parent.reserve_subprocess_lease(requested_total_tokens=None,
        requested_model_calls=None, requested_cost_usd=None, preserve_model_calls=1)
    assert (lease.max_total_tokens, lease.max_model_calls, lease.max_cost_usd) == (80, 2, Decimal("0.75"))
    before = parent.snapshot()
    with pytest.raises(AgentBudgetExceeded):
        _unlimited_lease(parent)
    assert parent.snapshot() == before
    assert not lease.accepts(SubprocessBudgetUsage(lease.lease_id, lease.lease_nonce, 3, 0, 0, Decimal(0)))
    parent.settle_subprocess_lease(lease, None)
    assert parent.snapshot().used_total_tokens == 100
    assert parent.snapshot().to_dict()["usageUncertain"]


def test_parallel_null_requests_cannot_both_lease_a_finite_parent_balance():
    parent = AgentTurnBudget(max_total_tokens=100, max_model_calls=4, max_cost_usd="1")
    barrier = threading.Barrier(2)
    leases, failures = [], []

    def reserve():
        barrier.wait(timeout=5)
        try:
            leases.append(_unlimited_lease(parent))
        except AgentBudgetExceeded as error:
            failures.append(error)

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(leases) == len(failures) == 1
    assert leases[0].max_total_tokens == 100
    assert leases[0].max_model_calls == 4
    assert parent.snapshot().reserved_total_tokens == 100


def test_active_null_child_lease_cannot_publish_a_premature_exact_receipt():
    parent = AgentTurnBudget()
    lease = _unlimited_lease(parent)
    assert parent.snapshot().reserved_total_tokens == 0
    with pytest.raises(ValueError, match="active reservation"):
        parent.subprocess_usage(lease)


@pytest.mark.parametrize("token_limit", [None, 100])
def test_missing_unbounded_usage_is_explicitly_uncertain_not_exact_zero(token_limit):
    parent = AgentTurnBudget(max_total_tokens=token_limit)
    lease = _unlimited_lease(parent)
    parent.settle_subprocess_lease(lease, None)
    snapshot = parent.snapshot()
    assert snapshot.to_dict()["usageUncertain"]
    assert snapshot.used_total_tokens == (token_limit or 0)
    assert snapshot.reserved_total_tokens == 0
    with pytest.raises(ValueError, match="uncertain"):
        parent.subprocess_usage(lease)
    if token_limit is None:
        # Unknown consumption is disclosed; it is not a replacement ceiling.
        parent.reserve_model_call(9_000_000)


@pytest.mark.parametrize("field,invalid", [
    ("maxTotalTokens", 0), ("maxTotalTokens", True), ("maxTotalTokens", -1),
    ("maxModelCalls", 0), ("maxModelCalls", 1.5), ("maxModelCalls", "unlimited"),
    ("maxCostUsd", "0"), ("maxCostUsd", "Infinity"), ("maxCostUsd", False),
])
def test_null_lease_does_not_accept_malformed_limits(field, invalid):
    raw = {"budgetLeaseVersion": 2, "leaseId": 1, "leaseNonce": "a" * 64,
           "maxTotalTokens": None, "maxModelCalls": None, "maxCostUsd": None}
    raw[field] = invalid
    with pytest.raises(ValueError):
        SubprocessBudgetLease.from_dict(raw)


def test_worker_passes_null_steps_deadline_and_limits_to_actual_turn_entry(tmp_path, monkeypatch):
    lease = _unlimited_lease(AgentTurnBudget())
    observed = []

    def turn(**kwargs):
        observed.append(kwargs)
        assert kwargs["max_steps"] is None and kwargs["deadline_monotonic"] is None
        budget = kwargs["agent_budget"]
        assert not budget.has_limits
        reservation = budget.reserve_model_call(4_000_000)
        budget.record_model_call(input_tokens=4_000_000, output_tokens=7,
                                 cost_usd="6", reservation=reservation)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(subagent_worker, "run_agent_turn", turn)
    code, payload = _run_worker_in_process(tmp_path, max_turns=None,
        timeout_seconds=None, budget_lease=lease.to_dict())
    assert code == 0 and payload["status"] == "completed"
    assert len(observed) == 1
    usage = SubprocessBudgetUsage.from_dict(payload["budget_usage"])
    assert lease.accepts(usage)
    assert usage.input_tokens == 4_000_000 and usage.output_tokens == 7


@pytest.mark.parametrize("field,invalid", [
    ("max_turns", 0), ("max_turns", False), ("max_turns", 1.5),
    ("timeout_seconds", 0), ("timeout_seconds", True),
    ("timeout_seconds", float("inf")), ("timeout_seconds", float("nan")),
])
def test_worker_null_option_keeps_malformed_values_invalid(tmp_path, field, invalid):
    request = _request(tmp_path, **{field: invalid})
    with pytest.raises(ValueError, match=field):
        subagent_worker._validate_request(request)


@pytest.mark.parametrize("field", ["max_turns", "timeout_seconds"])
def test_worker_requires_explicit_null_not_missing_field(tmp_path, field):
    request = _request(tmp_path)
    del request[field]
    with pytest.raises(ValueError, match=field):
        subagent_worker._validate_request(request)


def test_supervisor_no_deadline_still_finishes_and_cancellation_kills(tmp_path):
    request = _request(tmp_path, max_turns=None, timeout_seconds=None,
                       budget_lease=_unlimited_lease(AgentTurnBudget()).to_dict())
    completed = run_subagent_process(request, deadline_monotonic=None,
        command=[sys.executable, "-c", _RESULT_CHILD])
    assert completed.status == "completed"
    token = TurnCancellationToken("turn_" + "d" * 32)
    token.request()
    cancelled = run_subagent_process(request, deadline_monotonic=None, cancellation_token=token,
        command=[sys.executable, "-c", "import time; time.sleep(60)"], kill_grace_seconds=0.05)
    assert cancelled.status == "cancelled"
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.killpg(cancelled.pid, 0)


@pytest.mark.parametrize("deadline", [True, float("inf"), float("nan")])
def test_supervisor_rejects_invalid_deadline_before_spawning(tmp_path, deadline):
    with pytest.raises(ValueError, match="deadline_monotonic"):
        run_subagent_process(_request(tmp_path), deadline_monotonic=deadline,
            command=[sys.executable, "-c", "raise AssertionError('must not spawn')"])
