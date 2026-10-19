"""Native host evidence gaps must not be lost behind an unaudited test edge."""
from copy import deepcopy
from dataclasses import replace
import json

from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import requirement_units
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import COMPLETE, _call, _result
from test_completion_failure_adjudication_resume import packet_with
from test_implementation_completion_review import _native_coverage_step, _inspection_step
from test_semantic_supersession_continuation import BROKEN_FIXTURE, VALID_FIXTURE


inventory = chronology.inventory


def test_host_missing_behavior_evidence_returns_to_same_owner_without_inventing_product_gap(inventory):
    case = inventory
    objective = "1. hold subtracts the amount; release adds the amount.\n"
    spec = replace(case["spec"], objective=objective)
    options = {**case["options"], "implementation_spec": spec}
    (case["workspace"] / "stock.py").write_text(COMPLETE)
    ids = [row["id"] for row in requirement_units(objective)]
    assert len(ids) == 1
    stop = KeyboardInterrupt("same owner receives evidence gap; no product repair performed")
    original_read = None
    calls = {"parent": 0, "review": 0, "attributions": [], "nativeAuditSubmissions": 0}

    class Driver:
        model_id = "local-pending-behavior-gap"
        old_id = None
        total = 0

        def next(self, messages, **kwargs):
            nonlocal original_read
            self.total += 1
            assert self.total < 45, "Unexpected protocol retry instead of a native host evidence gap"
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs["tools_override"].list_all())
            if "completion_probe_adjudication_submit" in names:
                packet = packet_with(messages, "probe")
                assert packet["probe"]["probeId"] == self.old_id
                source = [m for m in messages if m.get("role") == "tool_result"
                          and m.get("toolUseId") == "attribution-source"]
                if not source:
                    return _call("attribution-source", "read_file", path="stock.py")
                assert not source[-1]["isError"]
                calls["attributions"].append("probe_error")
                return _call("fixture-attribution", "completion_probe_adjudication_submit",
                    verdict="probe_error", reason="The fixture invokes an already constructed Backend instance.",
                    objectiveEvidence=[{"startLine": 1, "endLine": 1}],
                    sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}],
                    replacement={"criterionIds": ids, "scenario": packet["probe"]["scenario"], "code": VALID_FIXTURE})
            if "completion_review_submit" in names:
                calls["review"] += 1
                if calls["review"] == 1:
                    return _call("broken-fixture", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="Both real inventory operations.", code=BROKEN_FIXTURE)
                if calls["review"] == 2:
                    result = json.JSONDecoder().raw_decode(_result(messages, "broken-fixture")["content"])[0]
                    assert result["receipt"]["status"] == "failed"
                    self.old_id = result["probeId"]
                    return _call("review-source", "read_file", path="stock.py")
                assert calls["review"] == 3
                return _call("uncertain-review", "completion_review_submit", status="inconclusive",
                    requirements=[{"id": ids[0], "status": "unknown", "probeIds": [],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "The corrected evidence still needs obligation-by-obligation scrutiny."}],
                    findings=["No product gap is established; review the replacement coverage."])
            if "completion_coverage_audit_submit" in names:
                packet = packet_with(messages, "evidenceIndex")
                assert len(packet["behaviorContract"]["obligations"]) == 2
                step = _native_coverage_step(messages, stable_evidence=True)
                if step.calls[0]["toolName"] != "completion_coverage_audit_submit":
                    return step
                calls["nativeAuditSubmissions"] += 1
                assert calls["nativeAuditSubmissions"] == 1, [m for m in messages if m.get("isError")]
                replacement = packet["supersessionIndex"][0]["effectiveProbeId"]
                # No fabricated missingBehaviorEvidence is submitted. The real
                # host detects absent child obligations from this supported row.
                step.calls[0]["input"] = {"status": "changes_required", "findings": [
                    "The remaining original clauses still need explicit executed evidence."],
                    "requirements": [{"id": ids[0], "status": "supported",
                        "reason": "The corrected test asserts the real positive inventory result.",
                        "witnesses": [{"probeId": replacement, "startLine": 9, "endLine": 9}]}],
                    "supersessions": []}
                return step
            assert {"write_file", "executor_check"} <= names
            calls["parent"] += 1
            if calls["parent"] == 1:
                return _call("original-source", "read_file", path="stock.py")
            if calls["parent"] == 2:
                original_read = deepcopy(_result(messages, "original-source"))
                return AgentStep(type="assistant", kind="final", content="Ready for independent coverage review.")
            assert _result(messages, "original-source") == original_read
            raise stop

    try:
        run_agent_turn(model=Driver(), messages=[{"role": "user", "content": objective}], **options)
    except KeyboardInterrupt as error:
        assert error is stop
    assert calls["attributions"] == ["probe_error"] and calls["nativeAuditSubmissions"] == 1
    decision = next(row["payload"] for row in reversed(case["events"])
                    if row["type"] == "implementation.completion_decided")
    audit = decision["coverageAudit"]
    assert audit["status"] == "changes_required"
    assert audit["missingBehaviorEvidence"], "The host must actually detect missing child obligations"
    assert all(row["status"] == "unknown" for row in audit["requirements"])
    assert audit["supersessions"] == []
    assert decision["repairProbes"] == [], "Unknown failed fixture must not become a product repair instruction"
    assert decision["unresolvedProbes"]
    document, saved = case["checkpoints"][-1]
    assert _result(saved, "original-source") == original_read
    assert document["reviewState"]["retiredProbeIds"] == []
    assert len({row[0]["executorId"] for row in case["checkpoints"]}) == 1
    assert (case["workspace"] / "stock.py").read_text() == COMPLETE
    assert calls["parent"] == 3, ("Host-confirmed evidence gap was erased by a pending replacement", decision)
    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"] is True
