"""Only independently attributed fixture keywords can amend a failed experiment."""

import json

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.types import AgentStep
from test_implementation_completion_review import _fixture, _probe, _submit


BAD = (
    "from stock import hold, release\n"
    "def prepare(folder):\n"
    "    folder.mkdir()\n"
    "    return folder\n"
    "def test_inventory(tmp_path):\n"
    "    folder = tmp_path / 'stock'\n"
    "    folder.mkdir()\n"
    "    prepared = prepare(folder)\n"
    "    assert prepared == folder\n"
    "    assert hold(5, 2) == 3\n"
    "    assert release(3, 2) == 5\n"
)
GOOD = BAD.replace("    folder.mkdir()", "    folder.mkdir(exist_ok=True)", 1)


def _seed(tools, child, packet):
    failed, evidence = _probe(tools, child, packet, BAD)
    assert not failed.ok
    receipt = evidence["receipt"]
    assert receipt["status"] == "failed" and receipt["testReport"]["failed"] == 1
    assert receipt["testReport"]["caseReport"]["cases"][0]["failureKind"] == "exception"
    assert "FileExistsError" in json.dumps(receipt)
    return AgentStep(type="assistant", kind="final", content="The fixture raised before the inventory assertions")


def _approve(tools, child, packet):
    assert len(packet["retainedProbes"]) == 1
    assert packet["retainedProbes"][0]["status"] == "passed"
    result = _submit(tools, child, packet, ids=[packet["retainedProbes"][0]["probeId"]])
    assert result.ok, result.output


def test_independent_fixture_keyword_addition_reexecutes_then_approves_and_restores(tmp_path, monkeypatch):
    judgments = []

    def adjudicate(tools, child, packet):
        assert set(tools.list_all()) == {"read_file", "completion_probe_adjudication_submit"}
        assert packet["probe"]["code"] == BAD
        judgments.append(packet["receipt"]["receiptRef"])
        request = {
            "verdict": "probe_error",
            "reason": "The test creates its temporary directory before prepare creates the same directory again; "
                      "allowing the directory to exist preserves the experiment and every inventory assertion.",
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
            "replacement": {"criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"], "code": GOOD},
        }
        result = tools.execute("completion_probe_adjudication_submit", request, child)
        assert result.ok, result.output
        assert result._implementation_submitted
        document = json.loads(result.output)
        assert document["verdict"] == "probe_error" and document["correctionMode"] == "fixture_keywords"
        assert "approved" not in document.values(), "Attribution alone is not completion approval."
        return AgentStep(type="assistant", kind="final", content="Fixture keyword correction independently submitted")

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch, adjudication_driver=adjudicate)
    original = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}
    try:
        initial = assess(_seed)
        assert initial["status"] == "inconclusive" and initial["repairProbes"] == []
        failed = review.state["receipts"][0]
        assert len(commands) == 2 and judgments == []
        approved = assess(_approve)
        assert approved["status"] == "approved" and approved["repairProbes"] == []
        assert len(commands) == 3, "Public check, failed probe and corrected probe must all execute."
        assert judgments == [failed["receiptRef"]]
        state = review.state
        assert state["receipts"][0] == failed
        assert [receipt["receipt"]["status"] for receipt in state["receipts"]] == ["failed", "passed"]
        correction = state["corrections"][0]
        assert correction["correctionMode"] == "fixture_keywords"
        assert correction["originalReceipt"] == failed["receiptRef"]
        assert correction["replacementReceipt"] == state["receipts"][-1]["receiptRef"]
        assert correction["adjudicationRef"] == state["failureAdjudications"][0]["adjudicationRef"]
        assert state["retiredProbeIds"] == [failed["probeId"]]
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        cap.close()

    restored, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, state),
        adjudication_driver=lambda *_: pytest.fail("An already attributed passing successor is reexecuted, not reclassified"))
    try:
        again = assess(_approve)
        assert again["status"] == "approved" and again["patchDigest"] == approved["patchDigest"]
        assert len(commands) == 2, "Restore must execute the current public check and active corrected probe."
        assert restored.state["corrections"] == state["corrections"]
        assert restored.state["failureAdjudications"] == state["failureAdjudications"]
        assert restored.state["receipts"][:2] == state["receipts"]
        assert restored.state["receipts"][-1]["receipt"]["status"] == "passed"
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        cap.close()


def test_ordinary_reviewer_cannot_add_fixture_keyword_without_independent_authority(tmp_path, monkeypatch):
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=lambda *_: pytest.fail("Ordinary fixture submission is not independent attribution"))
    original = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}

    def driver(tools, child, packet):
        _seed(tools, child, packet)
        old = review.state["probes"][0]
        correction, _ = _probe(tools, child, packet, GOOD, scenario=old["scenario"],
            correctsProbeId=old["probeId"], correctionReason="Allow the temporary directory to exist.",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert not correction.ok
        assert review.state["corrections"] == [] and review.state["failureAdjudications"] == []
        assert [probe["code"] for probe in review.state["probes"]] == [BAD]

    try:
        result = assess(driver)
        assert result["status"] == "inconclusive" and result["repairProbes"] == []
        assert len(commands) == 2, "Unauthorized correction must not execute a replacement."
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        cap.close()
