"""Regression coverage for durable, same-owner requirement repair feedback."""
from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import OBJECTIVE, PARTIAL, _call, _result
from test_completion_failure_adjudication_resume import packet_with


inventory = chronology.inventory
UNKNOWN = "from stock import hold\ndef test_unknown_hold_expectation():\n    assert hold(10, 3) == 8\n"


def _exercise(case, *, interrupt_attribution):
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    order = []
    stop_attribution = KeyboardInterrupt("adjudicator remains pending")
    stop_owner = KeyboardInterrupt("original owner received independent omission")
    unknown_id = None
    first_read = None
    parent_calls = 0

    def delivered(messages, identity):
        return next((m for m in messages if m.get("role") == "tool_result"
            and m.get("toolUseId") == identity), None)

    class Driver:
        model_id = "local-order-audit-no-provider"

        def next(self, messages, **kwargs):
            nonlocal unknown_id, first_read, parent_calls
            names = set(kwargs["tools_override"].list_all())
            assert "completion_coverage_audit_submit" not in names
            if "completion_probe_adjudication_submit" in names:
                packet = packet_with(messages, "probe")
                assert packet["probe"]["probeId"] == unknown_id
                assert packet["probe"]["criterionIds"] == ids[:1]
                assert packet["receipt"]["receipt"]["status"] == "failed"
                order.append("unrelated_adjudicator_model_request")
                if interrupt_attribution:
                    raise stop_attribution
                if delivered(messages, "adjudication-read") is None:
                    return _call("adjudication-read", "read_file", path="stock.py")
                order.append("unrelated_adjudicator_native_inconclusive")
                return _call("adjudication", "completion_probe_adjudication_submit",
                    verdict="inconclusive", reason="The hold expectation has not been independently established.",
                    objectiveEvidence=[{"startLine": 1, "endLine": 1}],
                    sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 2}])
            if "completion_review_submit" in names:
                failed = delivered(messages, "uncertain-hold")
                if failed is None:
                    order.append("review_runs_unrelated_probe")
                    return _call("uncertain-hold", "review_test", purpose="acceptance",
                        criterionIds=ids[:1], scenario="An unclassified hold expectation.", code=UNKNOWN)
                receipt = json.JSONDecoder().raw_decode(failed["content"])[0]
                assert receipt["receipt"]["status"] == "failed"
                unknown_id = receipt["probeId"]
                if delivered(messages, "review-read") is None:
                    return _call("review-read", "read_file", path="stock.py")
                assert not _result(messages, "review-read")["isError"]
                order.append("review_submits_independent_source_backed_missing")
                return _call("missing-release", "completion_review_submit", status="changes_required",
                    requirements=[
                        {"id": ids[0], "status": "unknown", "sourceEvidence": [], "probeIds": [],
                         "rationale": "No product defect is asserted for hold."},
                        {"id": ids[1], "status": "missing",
                         "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 5}],
                         "probeIds": [], "rationale": "release returns n rather than n plus amount."}],
                    findings=["release must add the requested amount."])
            assert {"write_file", "executor_check"} <= names
            parent_calls += 1
            if parent_calls == 1:
                return _call("owner-read", "read_file", path="stock.py")
            if parent_calls == 2:
                first_read = deepcopy(_result(messages, "owner-read"))
                order.append("owner_requests_completion")
                return AgentStep(type="assistant", kind="final", content="Ready for completion review.")
            assert _result(messages, "owner-read") == first_read
            order.append("same_owner_receives_independent_missing")
            raise stop_owner

    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(model=Driver(), messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    assert caught.value in (stop_attribution, stop_owner)
    assert (case["workspace"] / "stock.py").read_text() == PARTIAL
    assert len(case["commands"]) >= 2
    assert all(row["cwd"] != case["workspace"] for row in case["commands"])
    document, messages = case["checkpoints"][-1]
    assert len({row[0]["executorId"] for row in case["checkpoints"]}) == 1
    result = {"order": order, "parentCalls": parent_calls,
        "executorId": document["executorId"], "candidateDigest": document["candidateDigest"],
        "unknownProbeId": unknown_id,
        "nativeCompletionDecisions": [e["payload"] for e in case["events"]
            if e["type"] == "implementation.completion_decided"]}
    return result, document, messages




def test_independent_missing_submission_should_not_wait_for_unrelated_attribution(inventory):
    result, _document, _messages = _exercise(inventory, interrupt_attribution=True)
    assert "review_submits_independent_source_backed_missing" in result["order"]
    assert result["parentCalls"] == 3, (
        "Independent source-backed missing is still blocked inside submit's synchronous unrelated adjudication",
        result["order"])


def test_source_backed_missing_reaches_owner_with_unknown_probe_retained(inventory):
    result, document, _messages = _exercise(inventory, interrupt_attribution=False)
    decision = result["nativeCompletionDecisions"][-1]
    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"] is True
    assert decision["repairProbes"] == []
    missing = [r for r in decision["requirements"] if r["status"] == "missing"]
    assert len(missing) == 1 and missing[0]["probeIds"] == []
    assert missing[0]["sourceEvidence"][0]["startLine"] == 4
    assert [p["source"]["probeId"] for p in decision["unresolvedProbes"]] == [result["unknownProbeId"]]
    assert all(row["verdict"] == "inconclusive" for row in document["reviewState"]["failureAdjudications"])
    assert "same_owner_receives_independent_missing" in result["order"]


def test_resume_validated_same_candidate_missing_should_not_wait_for_unknown_probe(inventory):
    case = inventory
    seed, document, saved = _exercise(case, interrupt_attribution=False)
    decision = seed["nativeCompletionDecisions"][-1]
    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"] is True
    assert any(row["status"] == "missing" and row["sourceEvidence"]
        for row in document["reviewState"]["reviews"][-1]["requirements"])
    assert decision["patchDigest"] == document["candidateDigest"]
    restored = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=saved)
    requests = []
    stop = KeyboardInterrupt("first native request on genuine restored owner")

    class FirstResumeRequest:
        model_id = "local-order-audit-no-provider"

        def next(self, messages, **kwargs):
            requests.append(sorted(kwargs["tools_override"].list_all()))
            raise stop

    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(model=FirstResumeRequest(), messages=saved,
            implementation_continuation=restored, **case["options"])
    assert caught.value is stop and len(requests) == 1
    resumed_document, _ = case["checkpoints"][-1]
    assert resumed_document["executorId"] == document["executorId"]
    assert resumed_document["candidateDigest"] == document["candidateDigest"]
    assert "write_file" in requests[0], (
        "Restored host-validated same-candidate independent missing is blocked by unrelated attribution again",
        requests[0])
