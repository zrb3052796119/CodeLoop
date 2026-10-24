"""Real failed probes remain obligations until a separate audit accepts replacement."""

from copy import deepcopy
import json

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.probe_supersession import admit_replacement
from minicode.reviewer_verification import ReviewerProbe
from minicode.review_probe_semantic_correction import validate_adjudicated_probe_correction
from minicode.types import AgentStep
from test_implementation_completion_review import (
    _coverage_approval, _coverage_probe_ids, _fixture, _native_obligation_step, _probe, _submit,
)


BAD = """from stock import hold, release
class Inventory:
    def reserve(self):
        return hold(5, 2)
    def restore(self):
        return release(3, 2)
def inventory():
    return Inventory()
def test_inventory():
    subject = inventory()()
    assert subject.reserve() == 3
    assert subject.restore() == 5
"""
GOOD = BAD.replace("subject = inventory()()", "subject = inventory()")


def _adjudicate(tools, child, packet):
    result = tools.execute("completion_probe_adjudication_submit", {
        "verdict": "probe_error", "reason": "The fixture calls an already constructed, noncallable inventory instance.",
        "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
        "replacement": {"criterionIds": packet["probe"]["criterionIds"],
            "scenario": packet["probe"]["scenario"], "code": GOOD}}, child)
    assert result.ok, result.output
    assert json.loads(result.output)["correctionMode"] == "semantic_replacement"
    return AgentStep(type="assistant", kind="final", content="Invalid experiment independently identified.")


def _seed(tools, child, packet):
    failed, result = _probe(tools, child, packet, BAD)
    assert not failed.ok and result["receipt"]["status"] == "failed"
    assert result["receipt"]["testReport"]["caseReport"]["cases"][0]["failureKind"] == "exception"


def _propose(tools, child, packet):
    ids = [row["probeId"] for row in packet["retainedProbes"]]
    assert len(ids) == 1
    result = _submit(tools, child, packet, ids=ids)
    assert result.ok, result.output


def _read(tools, child, packet):
    sources = {}
    for pid in _coverage_probe_ids(packet):
        pages, offset = [], 0
        while True:
            result = tools.execute("review_probe_read", {"probeId": pid, "receiptView": "current",
                "offset": offset, "limit": 16000}, child)
            assert result.ok, result.output
            page = json.loads(result.output)
            pages.append(page["text"])
            if page["nextOffset"] is None:
                break
            offset = page["nextOffset"]
        sources[pid] = json.loads("".join(pages))["source"]
    return sources


def test_structurally_different_replacement_is_proposed_not_ast_approved():
    old = ReviewerProbe.create(criterion_ids=["inventory"], scenario="Real consumer", code=BAD).to_dict()
    new = ReviewerProbe.create(criterion_ids=["inventory"], scenario="Real consumer", code=GOOD).to_dict()
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(old, new)
    assert admit_replacement(old, new) == {"mode": "semantic_replacement"}
    other = ReviewerProbe.create(criterion_ids=["other"], scenario="Real consumer", code=GOOD).to_dict()
    with pytest.raises(ValueError, match="original criteria"):
        admit_replacement(old, other)


@pytest.mark.parametrize("failure", ["missing_edge", "unread_evidence", "omitted_requirement"])
def test_approval_cannot_skip_pending_replacement_authority(tmp_path, monkeypatch, failure):
    def audit(tools, child, packet):
        assert len(review.state["supersessions"]) == 1
        assert review.state["retiredProbeIds"] == []
        assert review.state["corrections"] == []
        sources = {p["probeId"]: p for p in review.state["probes"]}
        if failure != "unread_evidence":
            sources = _read(tools, child, packet)
            # Give the other counterexamples their complete real authority
            # documents, so omitted edges/requirements are not masked by the
            # independent unread-evidence refusal.
            while (step := _native_obligation_step(tools.messages, packet)) is not None:
                call = step.calls[0]
                read = tools.execute(call["toolName"], call["input"], child)
                assert read.ok, read.output
        value = _coverage_approval(packet, sources)
        if failure == "missing_edge":
            value.pop("supersessions")
        elif failure == "omitted_requirement":
            value["supersessions"][0]["requirements"].pop()
        refused = tools.execute("completion_coverage_audit_submit", value, child)
        assert not refused.ok
        assert review.state["retiredProbeIds"] == []
        return AgentStep(type="assistant", kind="final", content="No valid independent supersession decision.")

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=_adjudicate, coverage_driver=audit)
    try:
        assess(_seed)
        result = assess(_propose)
        assert result["status"] == "inconclusive"
        # A rejected submission leaves the same independent audit resumable;
        # it is not a completed inconclusive adjudication or approval.
        assert result["reason"] == "coverage_audit_interrupted"
        assert result["canContinue"] is False and result["auditPending"] and result["resumable"]
        assert result["continuationTarget"] == "coverage_auditor"
        assert result["repairProbes"] == []
        assert len(commands) == 3
        assert review.state["retiredProbeIds"] == []
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert restored.needs_failure_adjudication(result["patchDigest"])
        assert restored.state["supersessions"] == review.state["supersessions"]
        assert restored.state["pendingCoverageAudit"] == review.state["pendingCoverageAudit"]
        assert restored.has_pending_coverage_audit(result["patchDigest"])
    finally:
        cap.close()


def test_accepted_edge_restores_and_rejects_tampered_authority(tmp_path, monkeypatch):
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch, adjudication_driver=_adjudicate)
    original = (tmp_path / "stock.py").read_bytes()
    try:
        assess(_seed)
        approved = assess(_propose)
        assert approved["status"] == "approved"
        state = review.state
        assert [r["receipt"]["status"] for r in state["receipts"]] == ["failed", "passed"]
        assert state["corrections"][0]["correctionKind"] == "audited_semantic_supersession"
        assert state["retiredProbeIds"] == [state["supersessions"][0]["oldProbeId"]]
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored.state["corrections"] == state["corrections"]
        assert (tmp_path / "stock.py").read_bytes() == original
        assert len(commands) == 3
        for field, value in (("oldSourceDigest", "sha256:" + "0" * 64),
                             ("criterionIds", []), ("adjudicationRef", "/missing-authority")):
            corrupt = deepcopy(state)
            corrupt["supersessions"][0][field] = value
            with pytest.raises(ValueError):
                ImplementationCompletionReview.from_state(review.spec, corrupt)
        corrupt = deepcopy(state)
        corrupt["corrections"][0]["coverageAudit"]["evidenceDigest"] = "sha256:" + "0" * 64
        with pytest.raises(ValueError, match="audit evidence changed"):
            ImplementationCompletionReview.from_state(review.spec, corrupt)
    finally:
        cap.close()


@pytest.mark.parametrize("code", [
    "from stock import hold, release\ndef test_inventory():\n    assert hold(5, 0) == 5\n    assert release(3, 0) == 3\n",
    "def test_inventory():\n    def hold(n, amount): return n - amount\n    def release(n, amount): return n + amount\n    assert hold(5, 2) == 3\n    assert release(3, 2) == 5\n",
], ids=["trivial-input", "self-authored-substitute"])
def test_passing_but_weakened_replacement_is_rejected_by_independent_audit(tmp_path, monkeypatch, code):
    def attribution(tools, child, packet):
        value = {"verdict": "probe_error", "reason": "The original generated fixture calls an instance twice.",
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
            "replacement": {"criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"], "code": code}}
        result = tools.execute("completion_probe_adjudication_submit", value, child)
        assert result.ok, result.output
        assert json.loads(result.output)["correctionMode"] == "semantic_replacement"

    def rejecting_audit(tools, child, packet):
        sources = _read(tools, child, packet)
        assert sources[packet["supersessionIndex"][0]["newProbeId"]]["code"] == code
        assert review.state["receipts"][-1]["receipt"]["status"] == "passed"
        assert review.state["retiredProbeIds"] == []
        request = {"status": "changes_required", "requirements": [],
            "findings": ["The proposed replacement no longer establishes nonzero changes through the real inventory implementation."],
            "supersessions": [{"id": packet["supersessionIndex"][0]["id"], "status": "rejected",
                "invalidityReason": "The old fixture is invalid, but its proposed successor weakens the original experiment.",
                "requirements": []}]}
        result = tools.execute("completion_coverage_audit_submit", request, child)
        assert result.ok, result.output

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=attribution, coverage_driver=rejecting_audit)
    try:
        assess(_seed)
        result = assess(_propose)
        assert result["status"] == "inconclusive" and result["repairProbes"] == []
        assert result["reason"] == "failure_adjudication_inconclusive"
        assert review.state["retiredProbeIds"] == [] and review.state["corrections"] == []
        assert [row["receipt"]["status"] for row in review.state["receipts"]] == ["failed", "passed"]
        assert len(commands) == 3
    finally:
        cap.close()


def test_previously_passed_probe_remains_regression_after_candidate_changes(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=lambda *_: pytest.fail("Previously passed tests cannot enter invalid-test retirement"))

    def initial(tools, child, packet):
        result, receipt = _probe(tools, child, packet, GOOD)
        assert result.ok
        assert _submit(tools, child, packet, ids=[receipt["probeId"]]).ok

    def preserve(tools, child, packet):
        assert packet["retainedProbes"][0]["status"] == "failed"
        old = review.state["probes"][0]
        result, _ = _probe(tools, child, packet, GOOD.replace("subject.restore() == 5", "subject.restore() == 3"),
            correctsProbeId=old["probeId"], correctionReason="Follow observed output",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert not result.ok
        request = _submit(tools, child, packet, status="changes_required", rows=[{
            "id": row["id"], "status": "missing", "sourceEvidence": [], "probeIds": [],
            "rationale": "The previously passing real inventory regression now fails."} for row in packet["requirements"]],
            findings=["Restore the original inventory contract."])
        assert request.ok

    try:
        assert assess(initial)["status"] == "approved"
        stock = tmp_path / "stock.py"
        stock.write_text(stock.read_text().replace("return n + amount", "return n"))
        result = assess(preserve)
        assert result["status"] == "changes_required"
        assert review.state["supersessions"] == [] and review.state["retiredProbeIds"] == []
        assert [row["receipt"]["status"] for row in review.state["receipts"]] == ["passed", "failed"]
    finally:
        cap.close()
