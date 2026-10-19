"""Independent expression corrections must execute before completion approval."""

import json

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.types import AgentStep
from test_implementation_completion_review import _fixture, _probe, _submit


ACCESSOR_BAD = (
    "from types import SimpleNamespace\n"
    "from stock import hold, release\n"
    "def test_inventory():\n"
    "    batch = SimpleNamespace(rows=[(5, 2, 3), (8, 3, 5)])\n"
    "    for quantity, amount, expected in batch.read_rows():\n"
    "        assert hold(quantity, amount) == expected\n"
    "        assert release(expected, amount) == quantity\n"
)
ACCESSOR_GOOD = ACCESSOR_BAD.replace("batch.read_rows()", "batch.rows")
INLINE_BAD = (
    "from stock import hold, release\n"
    "def test_inventory():\n"
    "    assert hold(5, 1) == 3\n"
    "    assert release(3, 2) == 5\n"
)
INLINE_GOOD = INLINE_BAD.replace("hold(5, 1)", "hold(5, 2)")
CASES = [
    pytest.param(ACCESSOR_BAD, ACCESSOR_GOOD, "fixture_accessor", "exception",
        "The fixture stores rows as a collection property and has no read_rows method; "
        "reading that collection preserves both inventory assertions and their loop.", id="collection-accessor"),
    pytest.param(INLINE_BAD, INLINE_GOOD, "fixture_inline_inputs", "assertion",
        "The objective requires subtraction: subtracting two from five yields three. "
        "The probe used one as input while keeping the correct expected result for two.", id="inline-input"),
]


def _seed(tools, child, packet, code, failure_kind):
    result, evidence = _probe(tools, child, packet, code)
    assert not result.ok
    receipt = evidence["receipt"]
    assert receipt["status"] == "failed" and receipt["testReport"]["failed"] == 1
    assert receipt["testReport"]["caseReport"]["cases"][0]["failureKind"] == failure_kind
    return AgentStep(type="assistant", kind="final", content="The probe failed and needs independent attribution")


def _approve(tools, child, packet):
    assert len(packet["retainedProbes"]) == 1
    probe = packet["retainedProbes"][0]
    assert probe["status"] == "passed"
    result = _submit(tools, child, packet, ids=[probe["probeId"]])
    assert result.ok and result._implementation_submitted, result.output


@pytest.mark.parametrize("bad,good,mode,failure_kind,reason", CASES)
def test_independent_expression_correction_executes_approves_and_restores(
        tmp_path, monkeypatch, bad, good, mode, failure_kind, reason):
    judgments = []

    def adjudicate(tools, child, packet):
        assert set(tools.list_all()) == {"read_file", "completion_probe_adjudication_submit"}
        assert packet["probe"]["code"] == bad
        judgments.append(packet["receipt"]["receiptRef"])
        result = tools.execute("completion_probe_adjudication_submit", {
            "verdict": "probe_error", "reason": reason,
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
            "replacement": {"criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"], "code": good},
        }, child)
        assert result.ok and result._implementation_submitted, result.output
        document = json.loads(result.output)
        assert document["verdict"] == "probe_error" and document["correctionMode"] == mode
        assert "approved" not in document.values(), "An attributed correction still needs actual execution."
        return AgentStep(type="assistant", kind="final", content="Independent probe correction submitted")

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch, adjudication_driver=adjudicate)
    original = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}
    try:
        initial = assess(lambda tools, child, packet: _seed(tools, child, packet, bad, failure_kind))
        assert initial["status"] == "inconclusive" and initial["repairProbes"] == []
        failed = review.state["receipts"][0]
        assert len(commands) == 2 and judgments == []
        approved = assess(_approve)
        assert approved["status"] == "approved" and approved["repairProbes"] == []
        assert len(commands) == 3, "Public check, old failure and corrected probe must execute."
        assert judgments == [failed["receiptRef"]]
        state = review.state
        assert state["receipts"][0] == failed
        assert [row["receipt"]["status"] for row in state["receipts"]] == ["failed", "passed"]
        correction = state["corrections"][0]
        assert correction["correctionMode"] == mode
        assert correction["originalReceipt"] == failed["receiptRef"]
        assert correction["replacementReceipt"] == state["receipts"][-1]["receiptRef"]
        assert correction["adjudicationRef"] == state["failureAdjudications"][0]["adjudicationRef"]
        assert state["retiredProbeIds"] == [failed["probeId"]]
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        cap.close()

    restored, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, state),
        adjudication_driver=lambda *_: pytest.fail("Restore must reexecute the successor without reclassifying it"))
    try:
        again = assess(_approve)
        assert again["status"] == "approved" and again["patchDigest"] == approved["patchDigest"]
        assert len(commands) == 2, "Restore must run the current public check and corrected acceptance probe."
        assert restored.state["corrections"] == state["corrections"]
        assert restored.state["failureAdjudications"] == state["failureAdjudications"]
        assert restored.state["receipts"][:2] == state["receipts"]
        assert restored.state["receipts"][-1]["receipt"]["status"] == "passed"
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        cap.close()


@pytest.mark.parametrize("bad,good,mode,failure_kind,reason", CASES)
def test_ordinary_reviewer_cannot_make_expression_correction_without_authority(
        tmp_path, monkeypatch, bad, good, mode, failure_kind, reason):
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=lambda *_: pytest.fail("Ordinary reviewer has no independent authority"))
    original = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}

    def driver(tools, child, packet):
        _seed(tools, child, packet, bad, failure_kind)
        old = review.state["probes"][0]
        result, _ = _probe(tools, child, packet, good, scenario=old["scenario"],
            correctsProbeId=old["probeId"], correctionReason=reason,
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert not result.ok, f"Ordinary reviewer must not activate {mode} without attribution."
        assert review.state["corrections"] == [] and review.state["failureAdjudications"] == []
        assert [probe["code"] for probe in review.state["probes"]] == [bad]

    try:
        result = assess(driver)
        assert result["status"] == "inconclusive" and result["repairProbes"] == []
        assert len(commands) == 2, "An unauthorized replacement must not execute."
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        cap.close()
