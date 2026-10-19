"""A corrected fixture may expose another real exception before acceptance."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.review_probe_correction import validate_probe_correction_execution
from minicode.types import AgentStep
from test_completion_exception_attribution import _approve, _seed_exception
from test_implementation_completion_review import SOURCE, _fixture, _rows, _submit


BROKEN_FIXTURE = (
    "from stock import hold, release\n"
    "def test_inventory(tmp_path):\n"
    "    folder = tmp_path / 'stock'\n"
    "    folder.mkdir(exist_ok=False)\n"
    "    folder.mkdir(exist_ok=False)\n"
    "    _remaining = hold(5, 2)\n"
    "    assert remaining == 3\n"
    "    assert release(3, 2) == 5\n"
)
INTERMEDIATE = BROKEN_FIXTURE.replace(
    "    folder.mkdir(exist_ok=False)\n    folder.mkdir(exist_ok=False)",
    "    folder.mkdir(exist_ok=False)\n    folder.mkdir(exist_ok=True)",
)
CORRECT = INTERMEDIATE.replace("_remaining =", "remaining =")


@pytest.mark.parametrize("product_gap", [False, True])
def test_independent_correction_retains_exception_successor_and_continues_attribution(
    tmp_path, monkeypatch, product_gap,
):
    first = BROKEN_FIXTURE.replace("_remaining =", "remaining =") if product_gap else BROKEN_FIXTURE
    second = INTERMEDIATE.replace("_remaining =", "remaining =") if product_gap else INTERMEDIATE
    seen = []

    def adjudicate(tools, child, packet):
        code = packet["probe"]["code"]
        seen.append(code)
        fixture = code == first
        request = {
            "verdict": "probe_error" if fixture or not product_gap else "product_gap",
            "reason": (
                "The fixture creates the same directory twice; preserve the experiment and allow reuse."
                if fixture else "The test reads an unbound variable instead of its retained hold result."
                if not product_gap else "The release operation raises instead of adding the quantity."
            ),
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
        }
        if request["verdict"] == "probe_error":
            request["replacement"] = {
                "criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"],
                "code": second if fixture else CORRECT,
            }
        result = tools.execute("completion_probe_adjudication_submit", request, child)
        assert result.ok, result.output
        return AgentStep(type="assistant", kind="final", content="Attribution recorded")

    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch, adjudication_driver=adjudicate)
    if product_gap:
        (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "raise ValueError('broken release')"))
    original = (tmp_path / "stock.py").read_bytes()
    try:
        _seed_exception(assess, first)

        def review_gap(tools, child, packet):
            assert seen == [first, second]
            assert review.state["corrections"] == [] and review.state["retiredProbeIds"] == []
            result = _submit(tools, child, packet, status="changes_required",
                rows=_rows(packet, status="missing"), findings=["release must add the quantity"])
            assert result.ok, result.output

        def review_replacement_chain(tools, child, packet):
            assert len(review.state["supersessions"]) == 2
            assert review.state["corrections"] == [] and review.state["retiredProbeIds"] == []
            return _approve(tools, child, packet)

        result = assess(review_gap if product_gap else review_replacement_chain)
        assert seen == [first, second], "Each newly exposed exception needs its own independent attribution."
        assert result["status"] == ("changes_required" if product_gap else "approved")
        assert [row["code"] for row in result["repairProbes"]] == ([second] if product_gap else [])
        assert (tmp_path / "stock.py").read_bytes() == original
        state = review.state
        assert len(state["corrections"]) == (1 if product_gap else 2)
        assert len(state["failureAdjudications"]) == 2
        terminal = next(row["probeId"] for row in state["probes"]
                        if row["code"] == (second if product_gap else CORRECT))
        for correction in state["corrections"]:
            assert correction["correctionKind"] == "audited_semantic_supersession"
            audit = json.loads(Path(correction["coverageAudit"]["evidenceRef"]).read_text())
            edge_decision = next(row for row in audit["supersessions"]
                                 if row["id"] == correction["supersessionId"])
            assert edge_decision["status"] == "accepted"
            assert {w["probeId"] for row in edge_decision["requirements"] for w in row["witnesses"]} == {terminal}
        edge = state["corrections"][0]
        before = next(row["receipt"] for row in state["receipts"] if row["receiptRef"] == edge["originalReceipt"])
        after = next(row["receipt"] for row in state["receipts"] if row["receiptRef"] == edge["replacementReceipt"])
        assert before["status"] == after["status"] == "failed"
        assert after["testReport"]["caseReport"]["cases"][0]["failureKind"] == "exception"
        # This route cannot be borrowed by ordinary author corrections.
        with pytest.raises(ValueError):
            validate_probe_correction_execution([before], after, edge["frozenPatchDigest"])
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored.state["corrections"] == state["corrections"]
        assert restored.state["receipts"] == state["receipts"]
        forged = deepcopy(state)
        forged["corrections"][0].pop("adjudicationRef")
        with pytest.raises(ValueError, match="independent"):
            ImplementationCompletionReview.from_state(review.spec, forged)
        if product_gap:
            (tmp_path / "stock.py").write_text(SOURCE)
            assert assess(_approve)["status"] == "approved"
            assert seen == [first, second]
    finally:
        cap.close()
