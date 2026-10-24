"""Explicit host migration survives real owner restoration and budget closure."""

from copy import deepcopy
from dataclasses import replace
import json
import socket
import time

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.completion_repair_decision import delivered_repair_matches
from minicode.completion_review_limits import create_review_limits, check_review_allowance
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.probe_failure_adjudication import ProbeFailureAdjudicator
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import OBJECTIVE, PARTIAL, PROBE, _call, _result
from test_completion_failure_adjudication import pending, WRONG
from test_completion_failure_adjudication_resume import packet_with
from test_completion_probe_purposes import _legacy, _resolution
from test_implementation_completion_review import SOURCE


inventory = chronology.inventory


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("Migration feedback regressions must not contact a provider")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _old_negative_checkpoint(case):
    """Deliver a real source-backed negative before simulating its v1 archive."""
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    stop = KeyboardInterrupt("old native feedback reached its owner")

    class Seed:
        model_id = "local-migration-feedback-seed"
        parent_calls = 0
        review_calls = 0

        def next(self, messages, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            if "completion_review_submit" in names:
                self.review_calls += 1
                if self.review_calls == 1:
                    return _call("old-diagnostic", "review_test", purpose="diagnostic",
                        criterionIds=ids, scenario="Observe the existing release no-op.",
                        code="from stock import release\ndef test_observation():\n    assert release(7, 3) == 7\n")
                if self.review_calls == 2:
                    receipt = json.JSONDecoder().raw_decode(_result(messages, "old-diagnostic")["content"])[0]
                    assert receipt["receipt"]["status"] == "passed"
                    return _call("old-review-read", "read_file", path="stock.py")
                assert self.review_calls == 3
                assert not _result(messages, "old-review-read")["isError"]
                return _call("old-negative", "completion_review_submit", status="changes_required",
                    requirements=[{"id": identity, "status": "missing", "probeIds": [],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 5}],
                        "rationale": "release returns the unchanged quantity rather than adding the amount."}
                        for identity in ids], findings=["The requested release operation is missing."])
            assert {"write_file", "executor_check"} <= names
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call("original-owner-read", "read_file", path="stock.py")
            if self.parent_calls == 2:
                return AgentStep(type="assistant", kind="final", content="Ready for completion review.")
            assert self.parent_calls == 3
            assert any(message.get("_implementation_completion_feedback") for message in messages)
            raise stop

    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(model=Seed(), messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    assert caught.value is stop
    return case["checkpoints"][-1]


def test_parent_resume_finishes_explicit_migration_before_returning_to_writer(inventory, monkeypatch):
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "full")
    case = inventory
    document, saved = _old_negative_checkpoint(case)
    original_read = deepcopy(_result(saved, "original-owner-read"))
    # Only reconstruct the historical pre-purpose host format. Candidate bytes,
    # native negative, receipts and actually delivered parent messages stay real.
    review = ImplementationCompletionReview.from_state(case["spec"], _legacy(document["reviewState"]))
    context = ToolContext(cwd=str(case["workspace"]))
    native_before = deepcopy(review.state["acceptedRepairDecision"])
    old_negative = review._accepted_repair_for_candidate(document["candidateDigest"], context)
    assert old_negative["repairProbes"] == []
    assert delivered_repair_matches(document["completionFeedback"], old_negative,
        executor_id=document["executorId"], candidate_digest=document["candidateDigest"], objective=OBJECTIVE)
    review.queue_legacy_resolution(_resolution(review, case["workspace"], replacement=PROBE))
    resumed_document = {**deepcopy(document), "reviewState": review.state}
    continuation = HostImplementationContinuation.from_document(resumed_document, spec=case["spec"], messages=saved)
    stop = KeyboardInterrupt("same owner received migrated product counterexample")
    requests, feedback = [], []

    class Resume:
        model_id = "local-migration-feedback-resume"
        adjudication_calls = 0

        def next(self, messages, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            requests.append(names)
            if "completion_probe_adjudication_submit" in names:
                self.adjudication_calls += 1
                packet = packet_with(messages, "probe")
                assert packet["probe"]["code"] == PROBE
                assert packet["receipt"]["receipt"]["status"] == "failed"
                if self.adjudication_calls == 1:
                    return _call("migration-source", "read_file", path="stock.py")
                assert self.adjudication_calls == 2
                assert not _result(messages, "migration-source")["isError"]
                return _call("migration-product-gap", "completion_probe_adjudication_submit",
                    verdict="product_gap", reason="The objective requires addition; release returns the original count.",
                    objectiveEvidence=[{"startLine": 2, "endLine": 2}],
                    sourceEvidence=[{"path": "stock.py", "startLine": 4, "endLine": 5}])
            assert {"write_file", "executor_check"} <= names, names
            assert self.adjudication_calls == 2, "Old feedback dedup must not bypass explicit host migration."
            assert _result(messages, "original-owner-read") == original_read
            active = [message for message in messages if message.get("_implementation_completion_feedback")]
            assert len(active) == 1
            receipt = json.loads(active[0]["content"].rpartition("\nCompletion evidence:\n")[2])
            assert receipt["executorId"] == document["executorId"]
            assert receipt["patchDigest"] == document["candidateDigest"]
            assert receipt["status"] == "changes_required" and receipt["continuesSameExecutor"]
            assert [probe["code"] for probe in receipt["repairProbes"]] == [PROBE]
            feedback.append(receipt)
            raise stop

    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(model=Resume(), messages=saved, implementation_continuation=continuation, **case["options"])
    assert caught.value is stop and len(feedback) == 1
    assert "completion_probe_adjudication_submit" in requests[0]
    final, _ = case["checkpoints"][-1]
    assert final["executorId"] == document["executorId"]
    assert final["candidateDigest"] == document["candidateDigest"]
    state = final["reviewState"]
    assert state["pendingLegacyResolution"] is None
    assert state["legacyResolutions"][-1]["status"] == "classified"
    assert state["acceptedRepairDecision"] == native_before
    assert not state["retiredProbeIds"] and not state["corrections"]
    assert (case["workspace"] / "stock.py").read_text() == PARTIAL


def test_pending_migration_budget_stop_keeps_native_product_gap_before_classification(tmp_path, monkeypatch):
    import minicode.agent_loop as loop
    import minicode.completion_review_limits as limits

    real_loop = loop.run_agent_turn
    review, _assess, commands, capability = pending(tmp_path, monkeypatch,
        adjudication_driver=None, product=SOURCE.replace("return n + amount", "return n"))
    monkeypatch.setattr(loop, "run_agent_turn", real_loop)
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "bounded")
    # Two actual local model requests (read, submit) exhaust this phase. Check
    # that real allowance at the post-submit/pre-classification boundary.
    monkeypatch.setitem(limits.PHASE_LIMITS, "review", (2, 90.0))
    parent = ToolContext(cwd=str(tmp_path), _agent_budget=AgentTurnBudget(),
        _task_deadline_monotonic=time.monotonic() + 600)
    context = replace(parent, _completion_review_limits=create_review_limits(parent))
    original_review = ProbeFailureAdjudicator._review
    native_before = deepcopy(review.state["acceptedRepairDecision"])
    boundary = []

    def stop_after_native_submit(adjudicator, **values):
        result = original_review(adjudicator, **values)
        assert result["verdict"] == "product_gap"
        state = review.state
        assert state["pendingLegacyResolution"] is not None
        assert len(state["failureAdjudications"]) == 1
        assert state["failureAdjudications"][0]["verdict"] == "product_gap"
        assert not any(row["status"] == "classified" for row in state["legacyResolutions"])
        boundary.append(deepcopy(state))
        check_review_allowance(values["host"])
        pytest.fail("The two real requests must exhaust the two-call phase allowance")

    monkeypatch.setattr(ProbeFailureAdjudicator, "_review", stop_after_native_submit)

    class Attribution:
        model_id = "local-pending-migration-budget"
        calls = 0

        def next(self, messages, **kwargs):
            assert "completion_probe_adjudication_submit" in kwargs["tools_override"].list_all()
            self.calls += 1
            packet = packet_with(messages, "probe")
            assert packet["probe"]["code"] == WRONG
            assert packet["receipt"]["receipt"]["status"] == "failed"
            if self.calls == 1:
                return _call("budget-gap-source", "read_file", path="stock.py")
            assert self.calls == 2
            assert not _result(messages, "budget-gap-source")["isError"]
            return _call("budget-gap-submit", "completion_probe_adjudication_submit",
                verdict="product_gap", reason="The original objective requires release to add the requested amount.",
                objectiveEvidence=[{"startLine": 4, "endLine": 4}],
                sourceEvidence=[{"path": "stock.py", "startLine": 4, "endLine": 5}])

    try:
        checks = capability.ensure_completion_checks(parent)
        model = Attribution()
        with pytest.raises(AgentBudgetExceeded, match="phase call allowance exhausted"):
            review.assess(model=model, tools=ToolRegistry([read_file_tool]), context=context,
                agent_budget=parent._agent_budget, event_sink=None, cancellation_token=None,
                deadline_monotonic=parent._task_deadline_monotonic, checks_receipt=checks)
        assert model.calls == 2 and len(boundary) == 1
        state, count = deepcopy(review.state), len(commands)
        result = review.review_budget_exhausted_result(checks["patchDigest"], parent, "model_calls")
        assert result["status"] == "changes_required" and result["reason"] == "confirmed_product_gap"
        assert [probe["code"] for probe in result["repairProbes"]] == [WRONG]
        assert result["failureAdjudications"] == state["failureAdjudications"]
        assert review.state == state and len(commands) == count
        assert state["pendingLegacyResolution"] == boundary[0]["pendingLegacyResolution"]
        assert state["acceptedRepairDecision"] == native_before
        assert state["retiredProbeIds"] == state["corrections"] == []
        assert not any(row["status"] in {"activated", "classified"} for row in state["legacyResolutions"])
    finally:
        capability.close()
