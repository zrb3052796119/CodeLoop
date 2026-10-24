"""Confirmed repairs resume their owner without retiring unresolved evidence."""
from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import requirement_units
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import OBJECTIVE, PARTIAL, _call, _result
from test_completion_failure_adjudication_resume import packet_with
from test_semantic_supersession_continuation import BROKEN_FIXTURE, VALID_FIXTURE
from test_completion_coverage_audit import _inspection_step


inventory = chronology.inventory


@pytest.mark.parametrize("evidence", ["product_gap", "independent_missing", "unknown_only"])
def test_pending_replacement_does_not_cancel_independent_product_repair(inventory, evidence):
    case = inventory
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    known = evidence != "unknown_only"
    broken = BROKEN_FIXTURE if evidence == "product_gap" else BROKEN_FIXTURE.replace(
        "    assert backend.restore(7, 3) == 10\n", "")
    valid = broken.replace("make_backend()()", "make_backend()")
    criterion_ids = ids if evidence == "product_gap" else ids[:1]
    stop = KeyboardInterrupt("native parent received confirmed repair; no patch is applied")
    calls = {"parent": 0, "review": 0, "audit": 0, "attributions": []}
    original_read = None

    class Driver:
        model_id = "local-repair-priority-regression"
        old_id = None

        def next(self, messages, **kwargs):
            nonlocal original_read
            names = set(kwargs["tools_override"].list_all())
            inspection = _inspection_step(messages, kwargs["tools_override"])
            if inspection is not None:
                return inspection
            if "completion_probe_adjudication_submit" in names:
                packet = packet_with(messages, "probe")
                source = [m for m in messages if m.get("role") == "tool_result"
                          and m.get("toolUseId") == "attribution-source"]
                if not source:
                    return _call("attribution-source", "read_file", path="stock.py")
                assert not source[-1]["isError"]
                fixture_error = packet["probe"]["probeId"] == self.old_id
                assert packet["probe"]["code"] == (broken if fixture_error else valid)
                verdict = "probe_error" if fixture_error else "product_gap"
                calls["attributions"].append(verdict)
                value = {"verdict": verdict,
                    "reason": "The fixture invokes an already constructed Backend instance." if fixture_error
                        else "release returns 7 instead of the required addition result 10.",
                    "objectiveEvidence": [{"startLine": 1, "endLine": 2}],
                    "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}]}
                if fixture_error:
                    value["replacement"] = {"criterionIds": criterion_ids,
                        "scenario": packet["probe"]["scenario"], "code": valid}
                return _call("attribution-submit", "completion_probe_adjudication_submit", **value)
            if "completion_review_submit" in names:
                calls["review"] += 1
                if calls["review"] == 1:
                    return _call("broken-probe", "review_test", purpose="acceptance",
                        criterionIds=criterion_ids, scenario="Real inventory operations.", code=broken)
                if calls["review"] == 2:
                    result = json.JSONDecoder().raw_decode(_result(messages, "broken-probe")["content"])[0]
                    assert result["receipt"]["status"] == "failed"
                    self.old_id = result["probeId"]
                    return _call("review-source", "read_file", path="stock.py")
                assert calls["review"] == 3
                assert not _result(messages, "review-source")["isError"]
                return _call("review-submit", "completion_review_submit",
                    status="changes_required" if known else "inconclusive",
                    requirements=[{"id": identity, "status": "unknown",
                        "sourceEvidence": [], "probeIds": [],
                        "rationale": "No separate source-backed omission is submitted before attribution."}
                        for identity in ids],
                    findings=["release must add the requested amount." if known
                              else "The replacement equivalence still needs independent scrutiny."])
            if "completion_coverage_audit_submit" in names:
                calls["audit"] += 1
                packet = packet_with(messages, "evidenceIndex")
                assert packet["supersessionIndex"]
                assert calls["audit"] <= 2, [m for m in messages if m.get("isError")]
                if calls["audit"] == 1:
                    return _call("audit-source", "read_file", path="stock.py")
                assert not _result(messages, "audit-source")["isError"]
                return _call("audit-submit", "completion_coverage_audit_submit",
                    status="changes_required" if known else "inconclusive",
                    requirements=[{"id": ids[1], "status": "missing" if known else "unknown",
                        "reason": "Source confirms missing addition." if known else "Replacement equivalence is unresolved.",
                        "witnesses": []}],
                    findings=["Repair missing addition." if known else "No product defect has been established."],
                    supersessions=[])
            assert {"write_file", "executor_check"} <= names
            calls["parent"] += 1
            if calls["parent"] == 1:
                return _call("original-read", "read_file", path="stock.py")
            if calls["parent"] == 2:
                original_read = deepcopy(_result(messages, "original-read"))
                return AgentStep(type="assistant", kind="final", content="Ready for independent review.")
            assert known, "Unknown test evidence must not become a product repair instruction"
            assert _result(messages, "original-read") == original_read
            raise stop

    driver = Driver()
    try:
        run_agent_turn(model=driver, messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    except KeyboardInterrupt as error:
        assert error is stop
    assert calls["attributions"] == (["probe_error", "product_gap"] if evidence == "product_gap" else ["probe_error"])
    assert (case["workspace"] / "stock.py").read_text() == PARTIAL
    document, saved = case["checkpoints"][-1]
    assert _result(saved, "original-read") == original_read
    assert len({item[0]["executorId"] for item in case["checkpoints"]}) == 1
    assert document["reviewState"]["supersessions"]
    assert document["reviewState"]["retiredProbeIds"] == []
    decision = next(row["payload"] for row in reversed(case["events"])
                    if row["type"] == "implementation.completion_decided")
    assert decision["status"] != "approved"
    assert decision["unresolvedProbes"], "Unknown fixture evidence must remain visible"
    if not known:
        assert calls["parent"] == 2
        assert decision["status"] == "inconclusive" and decision["canContinue"] is False
        assert decision["repairProbes"] == []
        return
    assert calls["parent"] == 3, ("Independent product defect was suppressed by a pending test replacement", decision)
    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"] is True
    if evidence == "product_gap":
        assert [p["code"] for p in decision["repairProbes"]] == [VALID_FIXTURE]
    else:
        assert any(row["status"] == "missing" for row in decision["requirements"])
