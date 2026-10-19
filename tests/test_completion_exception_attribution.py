"""Real pytest exceptions need independent attribution before writer feedback."""

from copy import deepcopy

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.types import AgentStep
from test_implementation_completion_review import (
    PROBE,
    SOURCE,
    _fixture,
    _probe,
    _rows,
    _submit,
)


UNBOUND_PROBE = (
    "from stock import hold, release\n"
    "def test_inventory():\n"
    "    _remaining = hold(5, 2)\n"
    "    assert remaining == 3\n"
    "    assert release(3, 2) == 5\n"
)
ALIASED_PROBE = UNBOUND_PROBE.replace(
    "    assert remaining", "    remaining = _remaining\n    assert remaining"
)
REBOUND_PROBE = UNBOUND_PROBE.replace("_remaining =", "remaining =")
BROKEN_PRODUCT = SOURCE.replace("return n + amount", "raise ValueError('release failed')")


def _adjudicator(verdict, calls, *, replacement=None):
    def driver(tools, child, packet):
        calls.append(deepcopy(packet))
        assert set(tools.list_all()) == {"read_file", "completion_probe_adjudication_submit"}
        request = {
            "verdict": verdict,
            "reason": (
                "The test binds the held quantity to _remaining but reads the unbound name "
                "remaining; preserve the actual hold result and both inventory assertions."
                if verdict == "probe_error" else
                "The public release contract requires returning the increased quantity; "
                "the implementation raises before returning."
                if verdict == "product_gap" else
                "The exception alone does not establish whether the probe or the product is wrong."
            ),
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
        }
        if replacement is not None:
            request["replacement"] = {
                "criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"],
                "code": replacement,
            }
        submitted = tools.execute("completion_probe_adjudication_submit", request, child)
        assert submitted.ok, submitted.output
        assert submitted._implementation_submitted
        return AgentStep(type="assistant", kind="final", content="Independent attribution recorded")

    return driver


def _seed_exception(assess, code):
    captured = []

    def driver(tools, child, packet):
        result, evidence = _probe(tools, child, packet, code)
        assert not result.ok
        receipt = evidence["receipt"]
        assert receipt["status"] == "failed"
        assert receipt["testReport"]["failed"] == 1
        assert receipt["testReport"]["errors"] == 0
        assert [case["failureKind"] for case in receipt["testReport"]["caseReport"]["cases"]
                if case["outcome"] == "failed"] == ["exception"]
        captured.append(evidence)
        # Normal model completion without a native submission is not attribution.
        return AgentStep(type="assistant", kind="final", content="The probe raised an exception")

    result = assess(driver)
    assert result["status"] == "inconclusive"
    return result, captured[0]


def _approve(tools, child, packet):
    assert packet["retainedProbes"]
    assert all(probe["status"] == "passed" for probe in packet["retainedProbes"])
    result = _submit(tools, child, packet, ids=[probe["probeId"] for probe in packet["retainedProbes"]])
    assert result.ok, result.output


@pytest.mark.parametrize("product_exception", [False, True], ids=["probe-name-error", "product-value-error"])
def test_unattributed_real_exception_is_not_a_writer_repair_instruction(tmp_path, monkeypatch, product_exception):
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=lambda *_: pytest.fail("No completion submission has requested attribution yet"))
    if product_exception:
        (tmp_path / "stock.py").write_text(BROKEN_PRODUCT)
    try:
        result, evidence = _seed_exception(assess, PROBE if product_exception else UNBOUND_PROBE)
        assert result["repairProbes"] == [], "An unverified exception is evidence, not permission to change the product."
        assert review.state["failureAdjudications"] == []
        assert review.needs_failure_adjudication(evidence["candidateDigest"])
        assert len(commands) == 2, "The public check and independent probe both ran through real pytest."
    finally:
        cap.close()


@pytest.mark.parametrize("replacement", [ALIASED_PROBE, REBOUND_PROBE], ids=["local-alias", "assignment-target"])
def test_independent_name_binding_repair_preserves_assertions_then_retests_and_approves(tmp_path, monkeypatch, replacement):
    calls = []
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=_adjudicator("probe_error", calls, replacement=replacement))
    original = (tmp_path / "stock.py").read_bytes()
    try:
        _initial, failed = _seed_exception(assess, UNBOUND_PROBE)
        approved = assess(_approve)
        assert approved["status"] == "approved" and approved["repairProbes"] == []
        assert len(calls) == 1 and calls[0]["probe"]["code"] == UNBOUND_PROBE
        assert calls[0]["receipt"]["receiptRef"] == failed["receiptRef"]
        assert (tmp_path / "stock.py").read_bytes() == original
        assert len(commands) == 3, "The corrected probe must really execute; unchanged public checks may be reused."
        state = review.state
        old = next(probe for probe in state["probes"] if probe["code"] == UNBOUND_PROBE)
        new = next(probe for probe in state["probes"] if probe["code"] == replacement)
        assert state["retiredProbeIds"] == [old["probeId"]]
        assert state["corrections"][0]["newProbeId"] == new["probeId"]
        assert [row["receipt"]["status"] for row in state["receipts"]] == ["failed", "passed"]
        assert state["failureAdjudications"][0]["verdict"] == "probe_error"
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored.state["corrections"] == state["corrections"]
        assert restored.state["receipts"] == state["receipts"]
        assert not restored.needs_failure_adjudication(approved["patchDigest"])
    finally:
        cap.close()


def test_confirmed_product_exception_returns_repair_then_same_review_accepts_repaired_candidate(tmp_path, monkeypatch):
    calls = []
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=_adjudicator("product_gap", calls))
    (tmp_path / "stock.py").write_text(BROKEN_PRODUCT)
    try:
        _initial, failed = _seed_exception(assess, PROBE)

        def review_gap(tools, child, packet):
            assert len(calls) == 1, "Independent attribution must precede any writer feedback."
            submitted = _submit(tools, child, packet, status="changes_required",
                rows=_rows(packet, status="missing"), findings=["release raises instead of adding the amount"])
            assert submitted.ok, submitted.output

        result = assess(review_gap)
        assert result["status"] == "changes_required"
        assert [probe["code"] for probe in result["repairProbes"]] == [PROBE]
        assert review.state["failureAdjudications"][0]["verdict"] == "product_gap"
        assert review.state["retiredProbeIds"] == [] and review.state["corrections"] == []
        assert (tmp_path / "stock.py").read_text() == BROKEN_PRODUCT
        # This is the same review/capability owner receiving the repaired files,
        # with the original failed receipt intact, not a new implementation task.
        (tmp_path / "stock.py").write_text(SOURCE)
        approved = assess(_approve)
        assert approved["status"] == "approved" and len(calls) == 1
        assert approved["patchDigest"] != failed["candidateDigest"]
        assert review.state["receipts"][0]["receiptRef"] == failed["receiptRef"]
        assert [row["receipt"]["status"] for row in review.state["receipts"]] == ["failed", "passed"]
        assert not review.state["corrections"]
    finally:
        cap.close()


def test_inconclusive_exception_attribution_never_carries_bad_probe_to_writer(tmp_path, monkeypatch):
    calls = []
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=_adjudicator("inconclusive", calls))
    original = (tmp_path / "stock.py").read_bytes()
    try:
        _initial, failed = _seed_exception(assess, UNBOUND_PROBE)
        for expected_calls in (1, 2):
            result = assess(lambda *_: pytest.fail("Unknown attribution cannot request implementation repair"))
            assert result["status"] == "inconclusive" and result["repairProbes"] == []
            assert len(calls) == expected_calls
            assert review.needs_failure_adjudication(failed["candidateDigest"])
        assert not review._cached and not review.state["corrections"]
        assert (tmp_path / "stock.py").read_bytes() == original
    finally:
        cap.close()
