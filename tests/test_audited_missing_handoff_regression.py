"""Regression coverage for durable, same-owner requirement repair feedback."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import minicode
from minicode.agent_loop import run_agent_turn
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import requirement_units
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_coverage_audit import _inspection_step
from test_completion_feedback_chronology import OBJECTIVE, PARTIAL, _call, _result
from test_completion_failure_adjudication_resume import packet_with


SOURCE_ROOT = Path(minicode.__file__).resolve().parent.parent
inventory = chronology.inventory
WEAK_PROBE = "from stock import hold\ndef test_hold_quantity():\n    assert hold(10, 3) == 7\n"
UNKNOWN_PROBE = "from stock import hold\ndef test_uncertain_quantity():\n    assert hold(10, 3) == 8\n"


def test_accepted_native_audit_missing_reaches_same_executor_before_unrelated_attribution(inventory):
    case = inventory
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    source_names = ["minicode/implementation_completion_review.py", "minicode/completion_coverage_audit.py",
        "minicode/coverage_audit_state.py", "minicode/implementation_continuation.py", "minicode/agent_loop.py"]
    before = {name: hashlib.sha256((SOURCE_ROOT / name).read_bytes()).hexdigest() for name in source_names}
    candidate_before = (case["workspace"] / "stock.py").read_bytes()
    public_before = (case["workspace"] / "test_public.py").read_bytes()
    observed = {"parentCalls": 0, "reviewCalls": 0, "auditCalls": 0, "attributionCalls": 0,
        "repairDispatched": False, "originalReadRetained": False, "inspectionCalls": 0}
    original_read = None
    wrong_order = KeyboardInterrupt("unrelated attribution intercepted a native accepted missing")
    delivered = KeyboardInterrupt("the same original Executor received native missing before unrelated attribution")

    class Driver:
        model_id = "artifact-native-audit-handoff-regression"

        def next(self, messages, **kwargs):
            nonlocal original_read
            inspection = _inspection_step(messages, kwargs["tools_override"])
            if inspection is not None:
                observed["inspectionCalls"] += 1
                return inspection
            names = set(kwargs["tools_override"].list_all())
            if "completion_probe_adjudication_submit" in names:
                observed["attributionCalls"] += 1
                packet = packet_with(messages, "probe")
                assert packet["probe"]["probeId"] == observed["unknownProbeId"]
                assert packet["probe"]["code"] == UNKNOWN_PROBE
                assert packet["receipt"]["receipt"]["status"] == "failed"
                raise wrong_order
            if "completion_review_submit" in names:
                observed["reviewCalls"] += 1
                if observed["reviewCalls"] == 1:
                    return _call("passed-weak-probe", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="A narrow inventory quantity assertion proposed for both requirements.", code=WEAK_PROBE)
                assert observed["reviewCalls"] == 2
                receipt = json.JSONDecoder().raw_decode(_result(messages, "passed-weak-probe")["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                return _call("ordinary-positive-proposal", "completion_review_submit", status="approved", findings=[],
                    requirements=[{"id": identity, "status": "covered", "probeIds": [receipt["probeId"]],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "The author proposes these passing inventory assertions."} for identity in ids])
            if "completion_coverage_audit_submit" in names:
                observed["auditCalls"] += 1
                packet = packet_with(messages, "evidenceIndex")
                assert packet["proposal"]["status"] == "approved"
                if observed["auditCalls"] == 1:
                    return _call("unrelated-audit-challenge", "review_test", purpose="acceptance", criterionIds=ids[:1],
                        scenario="An unrelated hold expectation remains independently unresolved.", code=UNKNOWN_PROBE)
                assert observed["auditCalls"] == 2
                receipt = json.JSONDecoder().raw_decode(_result(messages, "unrelated-audit-challenge")["content"])[0]
                assert receipt["receipt"]["status"] == "failed"
                observed["unknownProbeId"] = receipt["probeId"]
                return _call("native-negative-audit", "completion_coverage_audit_submit", status="changes_required",
                    requirements=[{"id": ids[1], "status": "missing", "witnesses": [],
                        "reason": "The release implementation returns unchanged stock, and the proposed test never exercises release."}],
                    findings=["Implement release addition and test its returned stock quantity."])
            assert {"write_file", "executor_check"} <= names
            observed["parentCalls"] += 1
            if observed["parentCalls"] == 1:
                return _call("original-inventory-read", "read_file", path="stock.py")
            if observed["parentCalls"] == 2:
                original_read = deepcopy(_result(messages, "original-inventory-read"))
                assert not original_read["isError"]
                return AgentStep(type="assistant", kind="final", content="Ready for independent verification.")
            assert observed["parentCalls"] == 3
            observed["repairDispatched"] = True
            observed["originalReadRetained"] = _result(messages, "original-inventory-read") == original_read
            raise delivered

    try:
        run_agent_turn(model=Driver(), messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    except KeyboardInterrupt as error:
        assert error is delivered or error is wrong_order
    document, messages = deepcopy(case["checkpoints"][-1])
    state = document["reviewState"]
    evidence = Path(state["evidenceDirectory"])
    accepted = [json.loads(path.read_text()) for path in evidence.glob("*-coverage-audit-decision.json")]
    assert len(accepted) == 1 and accepted[0]["status"] == "changes_required"
    assert accepted[0]["candidateDigest"] == document["candidateDigest"]
    assert [row["id"] for row in accepted[0]["requirements"] if row["status"] == "missing"] == ids[1:]
    finished = [json.loads(path.read_text()) for path in evidence.glob("*-coverage-audit-tool-evidence.json")]
    finished = [row for row in finished if row["toolName"] == "completion_coverage_audit_submit" and row["phase"] == "finished"]
    assert len(finished) == 1 and finished[0]["isError"] is False
    assert (case["workspace"] / "stock.py").read_bytes() == candidate_before == PARTIAL.encode()
    assert (case["workspace"] / "test_public.py").read_bytes() == public_before
    assert _result(messages, "original-inventory-read") == original_read
    assert len({saved[0]["executorId"] for saved in case["checkpoints"]}) == 1
    assert len(case["commands"]) == 3 and all(row["cwd"] != case["workspace"] for row in case["commands"])
    after = {name: hashlib.sha256((SOURCE_ROOT / name).read_bytes()).hexdigest() for name in source_names}
    assert after == before
    decisions = [row["payload"] for row in case["events"] if row["type"] == "implementation.completion_decided"]
    assert observed["repairDispatched"], "Accepted native missing was blocked behind unrelated failure attribution"
    assert observed["inspectionCalls"] == 2
    assert observed["originalReadRetained"] and observed["attributionCalls"] == 0
    repair = state.get("acceptedRepairDecision")
    assert isinstance(repair, dict), "Accepted native negative must survive as host-owned schedulable state"
    assert record_document(repair)["candidateDigest"] == document["candidateDigest"]
    decision = decisions[-1]
    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"] is True
    assert decision["executorId"] == document["executorId"]
    assert decision["repairProbes"] == [], "Unknown hold expectation is not product repair authority"
    assert observed["unknownProbeId"] in [row["source"]["probeId"] for row in decision["unresolvedProbes"]]
    assert state["retiredProbeIds"] == [] and state["failureAdjudications"] == []
