"""A valid independent omission must survive an unrelated unclassified failure.

Only semantic model decisions are scripted. The parent, reviewer, adjudicator,
tool delivery, public checks, isolated pytest and continuation receipts are real.
"""
from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import requirement_units
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import COMPLETE, OBJECTIVE, PARTIAL, PROBE, _call, _result
from test_completion_failure_adjudication_resume import packet_with


inventory = chronology.inventory
UNCERTAIN_PROBE = ("from stock import hold\n"
    "def test_uncertain_expectation():\n"
    "    assert hold(10, 3) == 8\n")
UNCERTAIN_RELEASE_PROBE = ("from stock import release\n"
    "def test_uncertain_release_expectation():\n"
    "    assert release(7, 3) == 11\n")


@pytest.mark.parametrize("submission", ["missing", "invalid_missing", "approved", "unknown_only",
    "missing_with_unknown_probe", "unknown_probe_only_missing"])
def test_independent_missing_is_not_erased_by_unrelated_unknown_failed_probe(inventory, submission):
    case = inventory
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    expected_source = COMPLETE if submission == "approved" else PARTIAL
    (case["workspace"] / "stock.py").write_text(expected_source)
    original_public = (case["workspace"] / "test_public.py").read_bytes()
    original_read = None
    stop = KeyboardInterrupt("same Executor received only the independently evidenced release omission")
    calls = {"parent": 0, "attributions": 0, "review": 0, "invalid_missing_submitted": False}
    unknown_id = None
    positive_id = None
    linked_unknown = submission in {"missing_with_unknown_probe", "unknown_probe_only_missing"}
    unknown_code = UNCERTAIN_RELEASE_PROBE if linked_unknown else UNCERTAIN_PROBE
    unknown_criteria = ids[1:] if linked_unknown else ids[:1]
    raw_negative = submission in {"missing", "invalid_missing", "missing_with_unknown_probe", "unknown_probe_only_missing"}

    def delivered(messages, identity):
        return next((row for row in messages if row.get("role") == "tool_result"
                     and row.get("toolUseId") == identity), None)

    class Driver:
        model_id = "local-independent-negative-unknown-probe"

        def next(self, messages, **kwargs):
            nonlocal original_read, unknown_id, positive_id
            names = set(kwargs["tools_override"].list_all())
            assert "completion_coverage_audit_submit" not in names, "No approval can bypass the unknown acceptance failure"
            if "completion_probe_adjudication_submit" in names:
                packet = packet_with(messages, "probe")
                assert packet["probe"]["probeId"] == unknown_id
                assert packet["probe"]["criterionIds"] == unknown_criteria
                assert packet["probe"]["code"] == unknown_code
                assert packet["receipt"]["receipt"]["status"] == "failed"
                source = delivered(messages, "attribution-source")
                if source is None:
                    return _call("attribution-source", "read_file", path="stock.py")
                assert not source["isError"]
                calls["attributions"] += 1
                return _call("uncertain-attribution", "completion_probe_adjudication_submit",
                    verdict="inconclusive", reason="This review has not independently established the expectation's validity.",
                    objectiveEvidence=[{"startLine": 2 if linked_unknown else 1, "endLine": 2 if linked_unknown else 1}],
                    sourceEvidence=[{"path": "stock.py", "startLine": 4 if linked_unknown else 1,
                        "endLine": 5 if linked_unknown else 2}])
            if "completion_review_submit" in names:
                calls["review"] += 1
                assert calls["review"] <= 6, [m for m in messages if m.get("isError")]
                uncertain = delivered(messages, "uncertain-probe")
                if uncertain is None:
                    return _call("uncertain-probe", "review_test", purpose="acceptance",
                        criterionIds=unknown_criteria, scenario="A proposed inventory expectation awaiting independent attribution.",
                        code=unknown_code)
                receipt = json.JSONDecoder().raw_decode(uncertain["content"])[0]
                assert receipt["receipt"]["status"] == "failed"
                unknown_id = receipt["probeId"]
                if submission == "approved":
                    positive = delivered(messages, "positive-probe")
                    if positive is None:
                        return _call("positive-probe", "review_test", purpose="acceptance", criterionIds=ids,
                            scenario="Both real inventory operations with their ordinary quantities.", code=PROBE)
                    passed = json.JSONDecoder().raw_decode(positive["content"])[0]
                    assert passed["receipt"]["status"] == "passed"
                    positive_id = passed["probeId"]
                source = delivered(messages, "review-source")
                if source is None:
                    return _call("review-source", "read_file", path="stock.py")
                assert not source["isError"]
                previous = delivered(messages, "independent-review")
                if previous is not None:
                    assert submission in {"approved", "invalid_missing"} and previous["isError"], previous
                    # Either the native unknown-failure gate or an explicit
                    # rejected approval is valid; neither may authorize writes.
                    return _call("retain-uncertainty", "completion_review_submit", status="inconclusive",
                        requirements=[{"id": identity, "status": "unknown", "sourceEvidence": [], "probeIds": [],
                            "rationale": "An acceptance expectation remains independently unresolved."} for identity in ids],
                        findings=["The unrelated failed expectation has not been attributed."])
                status = {"missing": "changes_required", "invalid_missing": "changes_required",
                    "missing_with_unknown_probe": "changes_required", "unknown_probe_only_missing": "changes_required",
                    "approved": "approved", "unknown_only": "inconclusive"}[submission]
                rows = []
                for index, identity in enumerate(ids):
                    missing = raw_negative and index == 1
                    approved = submission == "approved"
                    rows.append({"id": identity, "status": "covered" if approved else "missing" if missing else "unknown",
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1 if approved else 4, "endLine": 5}]
                            if approved or missing else [],
                        "probeIds": [positive_id] if approved else [],
                        "rationale": "The valid inventory assertions passed." if approved else
                            "release returns the unchanged count instead of adding the amount." if missing else
                            "No positive support or product defect is asserted for hold."})
                if linked_unknown:
                    # Referencing an unresolved failed acceptance cannot turn
                    # that failure into evidence for a native missing row.
                    rows[1]["probeIds"] = [unknown_id]
                    if submission == "unknown_probe_only_missing":
                        rows[1]["sourceEvidence"] = []
                if submission == "invalid_missing":
                    # The raw negative claim has the correct original ID but
                    # cites lines absent from the actual five-line candidate.
                    # Validation must reject/downgrade it before scheduling.
                    rows[1]["sourceEvidence"] = [{"path": "stock.py", "startLine": 999, "endLine": 1000}]
                    assert rows[1]["status"] == "missing" and rows[1]["id"] == ids[1]
                    calls["invalid_missing_submitted"] = True
                return _call("independent-review", "completion_review_submit", status=status, requirements=rows,
                    findings=[] if submission == "approved" else ["release must add the requested amount."
                        if raw_negative else
                        "The unrelated failed expectation has not been attributed."])
            assert {"write_file", "executor_check"} <= names
            calls["parent"] += 1
            if calls["parent"] == 1:
                return _call("original-source", "read_file", path="stock.py")
            if calls["parent"] == 2:
                original_read = deepcopy(_result(messages, "original-source"))
                return AgentStep(type="assistant", kind="final", content="Ready for independent verification.")
            assert submission in {"missing", "missing_with_unknown_probe", "unknown_probe_only_missing"}, (
                "Unknown acceptance evidence does not authorize product changes")
            assert _result(messages, "original-source") == original_read
            raise stop

    try:
        run_agent_turn(model=Driver(), messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    except KeyboardInterrupt as error:
        assert error is stop
    document, messages = case["checkpoints"][-1]
    assert _result(messages, "original-source") == original_read
    assert len({row[0]["executorId"] for row in case["checkpoints"]}) == 1
    state = document["reviewState"]
    assert state["supersessions"] == [] and state["retiredProbeIds"] == []
    assert all(row["verdict"] == "inconclusive" for row in state["failureAdjudications"])
    unknown_receipt = next(row for row in state["receipts"] if row["probeId"] == unknown_id)
    assert unknown_receipt["receipt"]["status"] == "failed"
    assert unknown_receipt["receipt"]["testReport"]["caseReport"]["cases"][0]["failureKind"] == "assertion"
    assert (case["workspace"] / "stock.py").read_text() == expected_source
    assert (case["workspace"] / "test_public.py").read_bytes() == original_public
    assert len(case["commands"]) >= 2 and all(row["cwd"] != case["workspace"] for row in case["commands"])
    decision = next(row["payload"] for row in reversed(case["events"])
                    if row["type"] == "implementation.completion_decided")
    assert decision["status"] != "approved"
    assert decision["repairProbes"] == [], "An unknown probe is never a product repair instruction"
    assert [row["source"]["probeId"] for row in decision["unresolvedProbes"]] == [unknown_id]
    if submission == "invalid_missing":
        assert calls["invalid_missing_submitted"], "The invalid raw missing row must reach native validation"
    if submission not in {"missing", "missing_with_unknown_probe"}:
        assert calls["parent"] == 2
        assert decision["status"] == "inconclusive" and decision["canContinue"] is False
        return
    assert calls["parent"] == 3, ("A validated independent missing row was erased before native validation", decision)
    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"] is True
    missing_rows = [row for row in decision["requirements"] if row["status"] == "missing"]
    assert [row["id"] for row in missing_rows] == ids[1:]
    assert missing_rows[0]["sourceEvidence"][0]["path"] == "stock.py"
    assert missing_rows[0]["sourceEvidence"][0]["startLine"] == 4
    assert missing_rows[0]["probeIds"] == []
    assert "release must add the requested amount." in decision["findings"]
    if submission == "missing_with_unknown_probe":
        diagnostics = decision["coverageDiagnostics"]
        assert any(row.get("id") == ids[1] and row.get("claim", {}).get("probeIds") == [unknown_id]
            for row in diagnostics), "Keep the original unsupported claim as diagnostic evidence"
