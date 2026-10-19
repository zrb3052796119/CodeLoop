"""Regression coverage for durable, same-owner requirement repair feedback."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import minicode
import pytest

from minicode.agent_loop import run_agent_turn
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_coverage_audit import _inspection_step
from test_completion_feedback_chronology import OBJECTIVE, PARTIAL, _call, _result


SOURCE_ROOT = Path(minicode.__file__).resolve().parent.parent
inventory = chronology.inventory
WEAK_PROBE = "from stock import hold\ndef test_hold_quantity():\n    assert hold(10, 3) == 7\n"


def test_failed_native_negative_commit_retains_audit_and_can_resume_same_owner(inventory, monkeypatch):
    assert hasattr(ImplementationCompletionReview, "_retain_repair_decision")
    case = inventory
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    names = ["minicode/completion_repair_decision.py", "minicode/implementation_completion_review.py",
        "minicode/completion_coverage_audit.py", "minicode/agent_loop.py"]
    before = {name: hashlib.sha256((SOURCE_ROOT / name).read_bytes()).hexdigest() for name in names}
    initial = (case["workspace"] / "stock.py").read_bytes()
    observed = {"parentCalls": 0, "reviewCalls": 0, "auditCalls": 0, "injectedFailures": 0,
        "resumeAuditCalls": 0, "resumeParentCalls": 0, "inspectionCalls": 0}
    captured = {}

    def negative(identity):
        return _call(identity, "completion_coverage_audit_submit", status="changes_required",
            requirements=[{"id": ids[1], "status": "missing", "witnesses": [],
                "reason": "release returns unchanged stock and the proposed test omits release."}],
            findings=["Implement and test the requested release addition."])

    def fail_commit(self, decision, *, origin, authority=None):
        assert origin == "validated_coverage_audit" and decision["status"] == "changes_required"
        assert [row["id"] for row in decision["requirements"] if row["status"] == "missing"] == ids[1:]
        assert authority is not None
        observed["injectedFailures"] += 1
        raise ValueError("simulated durable repair store failure")

    class Seed:
        def next(self, messages, **kwargs):
            inspection = _inspection_step(messages, kwargs["tools_override"])
            if inspection is not None:
                observed["inspectionCalls"] += 1
                return inspection
            tools = set(kwargs["tools_override"].list_all())
            assert "completion_probe_adjudication_submit" not in tools
            if "completion_review_submit" in tools:
                observed["reviewCalls"] += 1
                if observed["reviewCalls"] == 1:
                    return _call("passing-narrow-probe", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="A narrow inventory result proposed as quantity evidence.", code=WEAK_PROBE)
                assert observed["reviewCalls"] == 2
                receipt = json.JSONDecoder().raw_decode(_result(messages, "passing-narrow-probe")["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                return _call("positive-proposal", "completion_review_submit", status="approved", findings=[],
                    requirements=[{"id": identity, "status": "covered", "probeIds": [receipt["probeId"]],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "A passed proposed inventory assertion."} for identity in ids])
            if "completion_coverage_audit_submit" in tools:
                observed["auditCalls"] += 1
                if observed["auditCalls"] == 1:
                    return negative("native-negative-failed-commit")
                assert observed["auditCalls"] == 2
                result = _result(messages, "native-negative-failed-commit")
                assert result["isError"] is True
                output = json.JSONDecoder().raw_decode(result["content"])[0]
                assert output["errorCode"] == "coverage_audit_invalid"
                assert "simulated durable repair store failure" in output["message"]
                captured["failedResult"] = deepcopy(result)
                captured["auditMessages"] = deepcopy(messages)
                raise KeyboardInterrupt("retain rejected repair commit and pending audit")
            assert {"write_file", "executor_check"} <= tools
            observed["parentCalls"] += 1
            if observed["parentCalls"] == 1:
                return _call("original-source-read", "read_file", path="stock.py")
            assert observed["parentCalls"] == 2, "A failed native commit must not deliver a parent verdict"
            return AgentStep(type="assistant", kind="final", content="Ready for independent verification.")

    with monkeypatch.context() as fault:
        fault.setattr(ImplementationCompletionReview, "_retain_repair_decision", fail_commit)
        with pytest.raises(KeyboardInterrupt, match="pending audit"):
            run_agent_turn(model=Seed(), messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    document, messages = deepcopy(case["checkpoints"][-1])
    state = document["reviewState"]
    pending = state["pendingCoverageAudit"]
    assert pending is not None and pending["candidateDigest"] == document["candidateDigest"]
    assert record_document(pending["messagesRecord"])["messages"] == captured["auditMessages"]
    assert state.get("acceptedRepairDecision") is None and state["reviews"] == []
    assert document["completionFeedback"] is None
    assert not [row for row in case["events"] if row["type"] == "implementation.completion_decided"]
    assert observed["injectedFailures"] == 1
    assert observed["inspectionCalls"] == 2
    original_read = deepcopy(_result(messages, "original-source-read"))
    continuation = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=messages)

    class Resume:
        def next(self, actual, **kwargs):
            tools = set(kwargs["tools_override"].list_all())
            assert "completion_review_submit" not in tools and "completion_probe_adjudication_submit" not in tools
            if "completion_coverage_audit_submit" in tools:
                observed["resumeAuditCalls"] += 1
                assert observed["resumeAuditCalls"] == 1
                assert actual[:len(captured["auditMessages"])] == captured["auditMessages"]
                assert _result(actual, "native-negative-failed-commit") == captured["failedResult"]
                return negative("native-negative-successful-retry")
            assert {"write_file", "executor_check"} <= tools
            observed["resumeParentCalls"] += 1
            assert _result(actual, "original-source-read") == original_read
            raise KeyboardInterrupt("same owner received the successfully persisted native retry")

    with pytest.raises(KeyboardInterrupt, match="persisted native retry"):
        run_agent_turn(model=Resume(), messages=messages, implementation_continuation=continuation, **case["options"])
    final, final_messages = deepcopy(case["checkpoints"][-1])
    assert observed["resumeAuditCalls"] == observed["resumeParentCalls"] == 1
    assert observed["inspectionCalls"] == 2
    assert final["executorId"] == document["executorId"] and final["candidateDigest"] == document["candidateDigest"]
    assert final["reviewState"]["pendingCoverageAudit"] is None
    repair = record_document(final["reviewState"]["acceptedRepairDecision"])
    assert repair["origin"] == "validated_coverage_audit" and repair["decision"]["status"] == "changes_required"
    decisions = [row["payload"] for row in case["events"] if row["type"] == "implementation.completion_decided"]
    assert len(decisions) == 1 and decisions[0]["status"] == "changes_required"
    assert decisions[0]["continuesSameExecutor"] is True and decisions[0]["executorId"] == document["executorId"]
    assert _result(final_messages, "original-source-read") == original_read
    assert (case["workspace"] / "stock.py").read_bytes() == initial == PARTIAL.encode()
    after = {name: hashlib.sha256((SOURCE_ROOT / name).read_bytes()).hexdigest() for name in names}
    assert before == after
