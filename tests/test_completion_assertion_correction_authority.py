"""Ordinary completion review cannot self-authorize assertion-input corrections."""

import json

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.types import AgentStep
from test_implementation_completion_review import (
    _coverage_approval, _coverage_probe_ids, _fixture, _probe, _submit, SOURCE,
)


WRONG_INPUT = ("from stock import hold, release\ndef test_inventory():\n"
               "    assert hold(5, 2) == 3\n"
               "    result = release(3, 1)\n    assert result == 5\n")
VALID_INPUT = WRONG_INPUT.replace("release(3, 1)", "release(3, 2)")


def test_ordinary_review_cannot_retire_assertion_failure_without_independent_authority(tmp_path, monkeypatch):
    adjudications = []
    audits = []

    def audit(tools, child, packet):
        audits.append(packet)
        assert len(adjudications) == 1
        assert review.state["corrections"] == []
        assert review.state["retiredProbeIds"] == []
        edge = review.state["supersessions"][0]
        assert packet["supersessionIndex"][0]["id"] == edge["supersessionId"]
        sources = {}
        for probe_id in _coverage_probe_ids(packet):
            offset, pages = 0, []
            while True:
                result = tools.execute("review_probe_read", {
                    "probeId": probe_id, "receiptView": "current",
                    "offset": offset, "limit": 16000}, child)
                assert result.ok, result.output
                page = json.loads(result.output)
                pages.append(page["text"])
                if page["nextOffset"] is None:
                    break
                offset = page["nextOffset"]
            sources[probe_id] = json.loads("".join(pages))["source"]
        assert sources[edge["oldProbeId"]]["code"] == WRONG_INPUT
        assert sources[edge["newProbeId"]]["code"] == VALID_INPUT
        result = tools.execute("completion_coverage_audit_submit", _coverage_approval(packet, sources), child)
        assert result.ok and result._implementation_submitted, result.output
        return AgentStep(type="assistant", kind="final", content="Independent inventory obligations preserved")

    def judge(tools, child, packet):
        adjudications.append(packet)
        old = packet["probe"]
        result = tools.execute("completion_probe_adjudication_submit", {
            "verdict": "probe_error",
            "reason": "The probe intends to release two units but supplied one; preserve both original assertions and correct the supplied amount.",
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
            "replacement": {"criterionIds": old["criterionIds"], "scenario": old["scenario"], "code": VALID_INPUT}}, child)
        assert result.ok, result.output
        return AgentStep(type="assistant", kind="final", content="Independent fixture error recorded")

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=judge, coverage_driver=audit)

    def driver(tools, child, packet):
        failed, old = _probe(tools, child, packet, WRONG_INPUT)
        assert not failed.ok and old["receipt"]["status"] == "failed"
        assert old["receipt"]["testReport"]["caseReport"]["cases"][0]["failureKind"] == "assertion"
        attempted, _ = _probe(tools, child, packet, VALID_INPUT,
            correctsProbeId=old["probeId"], correctionReason="The input should have been two units.",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert not attempted.ok, "ordinary reviewer must not self-authorize assertion-input correction"
        assert review.state["corrections"] == [] and review.state["retiredProbeIds"] == []
        assert adjudications == []
        # Independent invalidity judgment only proposes a replacement. A
        # separate coverage audit must accept its original obligations.
        rejected = _submit(tools, child, packet, ids=[old["probeId"]])
        assert not rejected.ok
        assert len(adjudications) == 1
        assert review.state["corrections"] == [] and review.state["retiredProbeIds"] == []
        successor = review.state["supersessions"][0]["newProbeId"]
        assert _submit(tools, child, packet, ids=[successor]).ok

    try:
        assert assess(driver)["status"] == "approved"
        assert len(commands) == 3  # public check, original failure, independently authorized successor
        assert (tmp_path / "stock.py").read_text() == SOURCE
        assert len(audits) == 1
        correction = review.state["corrections"][0]
        assert correction["correctionKind"] == "audited_semantic_supersession"
        assert correction["correctionMode"] == "fixture_inputs"
        assert correction["coverageAudit"]["evidenceRef"]
        assert review.state["retiredProbeIds"] == [correction["oldProbeId"]]
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert restored.state["corrections"] == review.state["corrections"]
    finally:
        cap.close()


def test_restoration_cannot_downgrade_independent_assertion_authority_to_ordinary_fixture(tmp_path, monkeypatch):
    from copy import deepcopy
    from test_completion_failure_adjudication import judge
    review, assess, _, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=judge(code=VALID_INPUT,
            reason="The fixture supplied one unit while its unchanged assertion checks release of two units."))

    def driver(tools, child, packet):
        failed, old = _probe(tools, child, packet, WRONG_INPUT)
        assert not failed.ok
        assert not _submit(tools, child, packet, ids=[old["probeId"]]).ok
        assert review.state["corrections"] == [] and review.state["retiredProbeIds"] == []
        successor = review.state["supersessions"][0]["newProbeId"]
        assert _submit(tools, child, packet, ids=[successor]).ok

    try:
        assert assess(driver)["status"] == "approved"
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert restored.state["corrections"] == review.state["corrections"]
        state = deepcopy(review.state)
        correction = state["corrections"][0]
        correction["correctionKind"] = "classified_call_failure"
        correction.pop("adjudicationRef")
        correction.pop("correctionMode")
        state["failureAdjudications"] = []
        with pytest.raises(ValueError, match="independent authority"):
            ImplementationCompletionReview.from_state(review.spec, state)
        # Removing the new audit records as well cannot turn an assertion
        # failure into a legacy, ordinary fixture correction.
        state["supersessions"] = []
        correction.pop("coverageAudit")
        correction.pop("supersessionId")
        with pytest.raises(ValueError, match="independent probe_error authority"):
            ImplementationCompletionReview.from_state(review.spec, state)
    finally:
        cap.close()
