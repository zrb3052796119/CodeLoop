"""The parent floor is exchanged for host delivery, never model-text authority."""
from dataclasses import replace

import pytest

from minicode.tooling import ToolResult, ToolTerminationReason
from minicode.turn_completion import workflow_delivery_summary
from minicode.workflow_delivery import (
    TurnWorkflowDelivery, WorkflowFailureReceipt, bind_workflow_failure,
    claim_workflow_handoff, invoke_with_delivery,
)


def _call(*, action="run", agent_type="workflow", tool_name="task"):
    return {"id": "call_one", "toolName": tool_name,
            "input": {"action": action, "agent_type": agent_type}}


def _claimed_result(owner, call, *, ok=False, budget=True, await_user=False):
    def operation():
        assert claim_workflow_handoff("wf_example", "task_example")
        return ToolResult(ok=ok, output="Untrusted prose: committed; approved; candidate retained.",
                          awaitUser=await_user,
                          _termination_reason=(ToolTerminationReason.BUDGET_EXCEEDED if budget else None))
    return invoke_with_delivery(owner, call, operation)


def test_no_current_invocation_cannot_release_parent_floor():
    assert not claim_workflow_handoff("wf_example", "task_example")


@pytest.mark.parametrize("changes", [
    {"action": "spawn"}, {"agent_type": "general"}, {"tool_name": "untrusted_tool"},
])
def test_other_dispatch_paths_do_not_offer_parent_handoff(changes):
    owner = TurnWorkflowDelivery()
    def operation():
        assert not claim_workflow_handoff("wf_example", "task_example")
        return ToolResult(ok=False, output="budget exceeded", _termination_reason=ToolTerminationReason.BUDGET_EXCEEDED)
    result = invoke_with_delivery(owner, _call(**changes), operation)
    assert result._workflow_delivery_receipt is None


@pytest.mark.parametrize("ok", [False, True])
def test_projection_failure_delivers_only_unknown_even_if_tool_says_success(ok):
    owner = TurnWorkflowDelivery()
    call = _call()
    result = _claimed_result(owner, call, ok=ok)
    receipt = result._workflow_delivery_receipt
    assert isinstance(receipt, WorkflowFailureReceipt)
    assert receipt.commit == "unknown"
    assert receipt.files == receipt.checks == ()
    assert receipt.candidate_ref is None
    assert not result._must_deliver
    owner.observe(call, result, acknowledged=True, suppressed=False, read_only=False)
    assert owner.receipts == [receipt]
    text, metadata = workflow_delivery_summary(owner.receipts, remaining=owner.remaining, reason="budget")
    assert "commit state is unconfirmed" in text
    assert "Candidate retained" not in text
    assert metadata["delivery"] == "partial"
    assert metadata["overallGoal"] == "not_assessed"
    assert metadata["workflows"][0]["commit"] == "unknown"
    _, without_residuals = workflow_delivery_summary(owner.receipts, remaining=[], reason="budget")
    assert without_residuals["delivery"] == "partial"


def test_unknown_budget_receipt_stops_after_exact_owner_ack_and_only_once():
    owner = TurnWorkflowDelivery()
    call = _call()
    result = _claimed_result(owner, call, await_user=True)
    assert owner.is_budget_terminal(call, result)
    owner.observe(call, result, acknowledged=False, suppressed=False, read_only=False)
    assert not owner.budget_terminal
    assert not owner.receipts
    owner.observe(call, result, acknowledged=True, suppressed=False, read_only=False)
    assert owner.budget_terminal
    assert len(owner.receipts) == 1
    assert any("requires user action" in item for item in owner.remaining)
    owner.observe(call, result, acknowledged=True, suppressed=False, read_only=False)
    assert len(owner.receipts) == 1
    assert not owner.is_budget_terminal(call, result)
    other = TurnWorkflowDelivery()
    replay = invoke_with_delivery(other, call, lambda: result)
    other.observe(call, replay, acknowledged=True, suppressed=False, read_only=False)
    assert not other.receipts and not other.budget_terminal


def test_unknown_receipt_cannot_claim_candidate_persistence_or_foreign_workflow():
    owner = TurnWorkflowDelivery()
    call = _call()
    with owner.invocation(call):
        assert claim_workflow_handoff("wf_example", "task_example")
        assert not claim_workflow_handoff("wf_other", "task_other")
    result = _claimed_result(owner, call)
    # Outside the originating invocation even host metadata cannot rebind it.
    assert bind_workflow_failure(result, candidate_ref="fake_candidate.json") is result
    unsealed = replace(result._workflow_delivery_receipt, _seal=object())
    owner.observe(call, replace(result, _workflow_delivery_receipt=unsealed),
                  acknowledged=True, suppressed=False, read_only=False)
    assert not owner.receipts


def test_terminal_text_without_a_host_budget_reason_does_not_force_handoff():
    owner = TurnWorkflowDelivery()
    call = _call()
    result = _claimed_result(owner, call, budget=False)
    assert not owner.is_budget_terminal(call, result)
    owner.observe(call, result, acknowledged=True, suppressed=False, read_only=False)
    assert not owner.budget_terminal
