"""Candidate binding proof using real parent/review/probe runtime.

Only semantic model responses are scripted. The isolated pytest executions,
native checkpoints, candidate digests, evidence delivery, and gates are real.
All candidate changes are to the unrelated temporary inventory fixture.
"""
from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.implementation_execution import direct_verification_capability, implementation_candidate_digest
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import COMPLETE, OBJECTIVE, PARTIAL, PROBE, _call, _result
from test_implementation_completion_review import _native_coverage_step, _inspection_step

inventory = chronology.inventory


def _pending_positive_audit(case):
    """Persist a genuine, not-yet-approved D1 audit after evidence delivery."""
    (case["workspace"] / "stock.py").write_text(COMPLETE)
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    captured = {}

    class Seed:
        parent_calls = 0
        reviewer_calls = 0

        def next(self, messages, **kwargs):
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs["tools_override"].list_all())
            if "completion_coverage_audit_submit" in names:
                step = _native_coverage_step(messages, stable_evidence=True)
                if step.calls[0]["toolName"] == "completion_coverage_audit_submit":
                    captured["auditMessages"] = deepcopy(messages)
                    captured["validSubmission"] = deepcopy(step.calls[0]["input"])
                    raise KeyboardInterrupt("retain D1 positive proposal before audit approval")
                return step
            if "completion_review_submit" in names:
                self.reviewer_calls += 1
                if self.reviewer_calls == 1:
                    return _call("real-D1-probe", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="Both real inventory functions return the required quantity.", code=PROBE)
                assert self.reviewer_calls == 2
                receipt = json.JSONDecoder().raw_decode(_result(messages, "real-D1-probe")["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                captured["probeId"] = receipt["probeId"]
                return _call("positive-D1-proposal", "completion_review_submit", status="approved", findings=[],
                    requirements=[{"id": identity, "status": "covered", "probeIds": [receipt["probeId"]],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "The actual independent hold and release assertions passed on D1."}
                        for identity in ids])
            assert {"write_file", "executor_check"} <= names
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call("original-stock-read", "read_file", path="stock.py")
            assert self.parent_calls == 2
            return AgentStep(type="assistant", kind="final", content="Both operations are ready for verification.")

    with pytest.raises(KeyboardInterrupt, match="D1 positive proposal"):
        run_agent_turn(model=Seed(), messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    document, messages = deepcopy(case["checkpoints"][-1])
    pending = document["reviewState"]["pendingCoverageAudit"]
    assert pending is not None and pending["candidateDigest"] == document["candidateDigest"]
    assert record_document(pending["messagesRecord"])["messages"] == captured["auditMessages"]
    assert captured["validSubmission"]["status"] == "approved"
    assert document["reviewState"]["reviews"] == []
    assert all(row["cwd"] != case["workspace"] for row in case["commands"])
    return document, messages, captured


def test_stale_audit_checkpoint_rejects_actual_candidate_edit_before_model(inventory):
    case = inventory
    document, messages, captured = _pending_positive_audit(case)
    continuation = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=messages)
    (case["workspace"] / "stock.py").write_text(PARTIAL)
    context = ToolContext(cwd=str(case["workspace"]))
    d2 = implementation_candidate_digest(case["spec"], context)
    assert d2 != document["candidateDigest"]
    observed = []

    class NoModel:
        def next(self, *_args, **_kwargs):
            observed.append("model-invoked")
            pytest.fail("A D1 audit must not be resumed against actual D2 bytes")

    with pytest.raises(ValueError, match="continuation candidate changed"):
        run_agent_turn(model=NoModel(), messages=messages, implementation_continuation=continuation,
            **case["options"])
    assert observed == []
    assert continuation.document == document
    assert record_document(document["reviewState"]["pendingCoverageAudit"]["messagesRecord"])["messages"] == captured["auditMessages"]
    assert not [row for row in case["events"] if row["type"] == "implementation.completion_decided"]


def test_stale_audit_in_live_review_is_discarded_and_current_failure_blocks_approval(inventory):
    case = inventory
    document, _messages, captured = _pending_positive_audit(case)
    old_pending = deepcopy(document["reviewState"]["pendingCoverageAudit"])
    review = ImplementationCompletionReview.from_state(case["spec"], document["reviewState"])
    (case["workspace"] / "stock.py").write_text(PARTIAL)
    context = ToolContext(cwd=str(case["workspace"]), permissions=PermissionManager(str(case["workspace"]), prompt=None),
        _runtime={"model": "local-stale-audit-proof", "reflectionMode": "rule"})
    d2 = implementation_candidate_digest(case["spec"], context)
    assert d2 != document["candidateDigest"]
    checks = direct_verification_capability(case["spec"]).ensure_completion_checks(context)
    assert checks["status"] == "passed" and checks["patchDigest"] == d2
    observed = []
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]

    class CurrentReview:
        def next(self, actual, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            assert not {"completion_coverage_audit_submit", "completion_coverage_inspection_submit"} & names, "The obsolete positive D1 audit was reused"
            assert review.state["pendingCoverageAudit"] is None, "Old read/delivery credit survived D2"
            assert actual[:len(captured["auditMessages"])] != captured["auditMessages"]
            if "completion_probe_adjudication_submit" in names:
                pytest.fail("A previously passing acceptance regression should stay a repair obligation")
            assert "completion_review_submit" in names
            observed.append("fresh-D2-review")
            packet = next(json.loads(m["content"]) for m in actual
                if m.get("role") == "user" and '"retainedProbes"' in m.get("content", ""))
            assert packet["candidateDigest"] == d2
            assert packet["retainedProbes"] == [{"probeId": captured["probeId"], "criterionIds": sorted(ids), "status": "failed"}]
            return _call("D2-product-gap", "completion_review_submit", status="changes_required",
                requirements=[{"id": ids[0], "status": "unknown", "sourceEvidence": [], "probeIds": [],
                    "rationale": "No positive approval is needed to report the concrete release regression."},
                    {"id": ids[1], "status": "missing", "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 5}],
                     "probeIds": [], "rationale": "The current release result omits the requested addition."}],
                findings=["Restore release addition on the actual D2 candidate."])

    result = review.assess(model=CurrentReview(), tools=case["options"]["tools"], context=context, agent_budget=None,
        event_sink=None, cancellation_token=None, deadline_monotonic=None, checks_receipt=checks)
    assert observed == ["fresh-D2-review"]
    assert result["status"] == "changes_required" and result["patchDigest"] == d2
    assert "coverageAudit" not in result
    assert result["repairProbes"] and result["repairProbes"][0]["probeId"] == captured["probeId"]
    latest = next(row for row in result["probeReceipts"] if row["probeId"] == captured["probeId"])
    assert latest["candidateDigest"] == d2 and latest["receipt"]["status"] == "failed"
    assert latest["receipt"]["testReport"]["failed"] > 0
    old_receipt = next(row for row in review.state["receipts"] if row["candidateDigest"] == document["candidateDigest"])
    assert old_receipt["receipt"]["status"] == "passed"
    assert review.state["pendingCoverageAudit"] is None
    assert document["reviewState"]["pendingCoverageAudit"] == old_pending
    assert record_document(old_pending["messagesRecord"])["messages"] == captured["auditMessages"]
