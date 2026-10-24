"""Objective-bound probe correction retains real failed and replacement evidence."""

from copy import deepcopy

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import implementation_candidate_digest
from minicode.probe_supersession import read_audit_record
from minicode.tooling import ToolContext
from minicode.types import AgentStep
from test_completion_probe_purposes import _resolution, _seed_legacy
from test_implementation_completion_review import _fixture, _rows, _submit, SOURCE


WRONG = ("from stock import hold, release\ndef test_inventory():\n"
         "    assert hold(5, 2) == 3\n    quantity = release(3, 2)\n"
         "    quantity = release(quantity, 4)\n    assert quantity == 5\n")
RIGHT = WRONG.replace("quantity == 5", "quantity == 9")


def judge(verdict="probe_error", *, calls=None, code=RIGHT, reason=None):
    def driver(tools, child, packet):
        if calls is not None:
            calls.append(packet)
        request = {"verdict": verdict,
            "reason": reason or "Each release adds its amount; applying both releases requires the sum of both amounts, independently of the observed output.",
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}]}
        if verdict == "probe_error":
            request["replacement"] = {"criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"], "code": code}
        result = tools.execute("completion_probe_adjudication_submit", request, child)
        assert result.ok, result.output
        return AgentStep(type="assistant", kind="final", content="Independent attribution submitted")
    return driver


def pending(tmp_path, monkeypatch, *, adjudication_driver, product=SOURCE):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy),
        adjudication_driver=adjudication_driver)
    (tmp_path / "stock.py").write_text(product)
    review.queue_legacy_resolution(_resolution(review, tmp_path, replacement=WRONG))
    return review, assess, commands, cap


def approve(tools, child, packet):
    ids = [p["probeId"] for p in packet["retainedProbes"] if p["status"] == "passed"]
    result = _submit(tools, child, packet, ids=ids)
    assert result.ok, result.output


def assert_independently_audited(review, correction):
    assert correction["correctionKind"] == "audited_semantic_supersession"
    request = next(row for row in review.state["supersessions"]
        if row["supersessionId"] == correction["supersessionId"])
    audit, authority = read_audit_record(correction["coverageAudit"])
    edge = next(row for row in audit["supersessions"] if row["id"] == request["supersessionId"])
    assert edge["status"] == "accepted"
    assert {row["id"] for row in edge["requirements"]} == set(request["criterionIds"])
    assert authority["originalObjective"] == review.spec.objective
    assert request["oldProbeId"] == correction["oldProbeId"]
    assert request["newProbeId"] == correction["newProbeId"]
    assert request["adjudicationRef"] == correction["adjudicationRef"]


def test_wrong_expected_value_is_independently_corrected_reexecuted_and_restorable(tmp_path, monkeypatch):
    calls = []
    review, assess, commands, cap = pending(tmp_path, monkeypatch, adjudication_driver=judge(calls=calls))
    original = (tmp_path / "stock.py").read_bytes()
    try:
        result = assess(approve)
        assert result["status"] == "approved" and not review.has_legacy_probes
        assert len(calls) == 1 and len(commands) == 4
        assert (tmp_path / "stock.py").read_bytes() == original
        state = review.state
        correction = state["corrections"][0]
        assert_independently_audited(review, correction)
        assert correction["correctionMode"] == "expected_literal"
        authority = state["failureAdjudications"][0]
        assert correction["adjudicationRef"] == authority["adjudicationRef"]
        assert correction["originalReceipt"] == authority["receiptRef"]
        old = next(p for p in state["probes"] if p["code"] == WRONG)
        new = next(p for p in state["probes"] if p["code"] == RIGHT)
        assert state["retiredProbeIds"] == [old["probeId"]]
        assert [(r["probeId"], r["receipt"]["status"]) for r in state["receipts"]][-2:] == [
            (old["probeId"], "failed"), (new["probeId"], "passed")]
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored.state["failureAdjudications"] == state["failureAdjudications"]
        assert restored.state["corrections"] == state["corrections"]
        assert not restored._cached
        assert not restored.needs_failure_adjudication(result["patchDigest"])
    finally:
        cap.close()


def test_true_product_gap_is_the_only_assertion_feedback_sent_to_executor(tmp_path, monkeypatch):
    product = SOURCE.replace("return n + amount", "return n")
    review, assess, _, cap = pending(tmp_path, monkeypatch,
        adjudication_driver=judge("product_gap"), product=product)
    try:
        result = assess(lambda *_: pytest.fail("confirmed product gap returns to existing owner"))
        assert result["status"] == "changes_required"
        assert [p["code"] for p in result["repairProbes"]] == [WRONG]
        assert not review.state["corrections"]
        assert not review.needs_failure_adjudication(result["patchDigest"])
        assert (tmp_path / "stock.py").read_text() == product
    finally:
        cap.close()


def test_inconclusive_attribution_never_turns_test_claim_into_product_repair(tmp_path, monkeypatch):
    calls = []
    review, assess, _, cap = pending(tmp_path, monkeypatch,
        adjudication_driver=judge("inconclusive", calls=calls))
    try:
        for _ in range(2):
            result = assess(lambda *_: pytest.fail("unknown attribution cannot demand product repair"))
            assert result["status"] == "inconclusive" and result["repairProbes"] == []
            assert review.needs_failure_adjudication(result["patchDigest"])
        assert len(calls) == 2 and not review._cached
        assert not review.state["corrections"]
    finally:
        cap.close()


def test_classified_gap_feedback_survives_repeat_restore_and_budget_closure(tmp_path, monkeypatch):
    product = SOURCE.replace("return n + amount", "return n")
    review, assess, commands, cap = pending(tmp_path, monkeypatch,
        adjudication_driver=judge("product_gap"), product=product)
    try:
        native_before = deepcopy(review.state["acceptedRepairDecision"])
        first = assess(lambda *_: pytest.fail("confirmed gap needs no new reviewer"))
        assert [p["code"] for p in first["repairProbes"]] == [WRONG]
        count = len(commands)
        second = assess(lambda *_: pytest.fail("repeat must use confirmed feedback"))
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        context = ToolContext(cwd=str(tmp_path))
        resumed = restored.assess(model=None, tools=None, context=context,
            agent_budget=None, event_sink=None, cancellation_token=None,
            deadline_monotonic=None,
            checks_receipt={"status": "passed", "patchDigest": first["patchDigest"]})
        bounded = restored.review_budget_exhausted_result(first["patchDigest"], context, "stage exhausted")
        for result in (second, resumed, bounded):
            assert result["status"] == "changes_required"
            assert [p["code"] for p in result["repairProbes"]] == [WRONG]
        assert len(commands) == count
        assert review.state["acceptedRepairDecision"] == native_before
        assert restored.state["acceptedRepairDecision"] == native_before
    finally:
        cap.close()


@pytest.mark.parametrize("field", ["candidateDigest", "receiptRef", "sourceDigest", "replacement", "adjudicationRef"])
def test_restore_rejects_semantic_correction_authority_tampering(tmp_path, monkeypatch, field):
    review, assess, _, cap = pending(tmp_path, monkeypatch, adjudication_driver=judge())
    try:
        assert assess(approve)["status"] == "approved"
        state = deepcopy(review.state)
        if field == "adjudicationRef":
            state["corrections"][0][field] = "unrelated-authority.json"
        elif field == "replacement":
            state["failureAdjudications"][0][field]["code"] = RIGHT.replace("== 9", "== 5")
        else:
            state["failureAdjudications"][0][field] = "sha256:" + "0" * 64
        with pytest.raises(ValueError):
            ImplementationCompletionReview.from_state(review.spec, state)
    finally:
        cap.close()


def test_new_candidate_requires_new_failed_receipt_authority(tmp_path, monkeypatch):
    review, assess, _, cap = pending(tmp_path, monkeypatch, adjudication_driver=judge("product_gap"))
    try:
        first = assess(lambda *_: None)
        assert first["status"] == "changes_required"
        state = review.state
        (tmp_path / "stock.py").write_text(SOURCE + "\n# New implementation candidate\n")
        digest = implementation_candidate_digest(review.spec, ToolContext(cwd=str(tmp_path)))
        assert digest != first["patchDigest"]
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored._failure_authority(next(p["probeId"] for p in state["probes"] if p["code"] == WRONG), digest) is None
    finally:
        cap.close()


def test_classified_product_gap_cannot_restore_without_each_exact_independent_authority(tmp_path, monkeypatch):
    review, assess, _, cap = pending(tmp_path, monkeypatch, adjudication_driver=judge("product_gap"))
    try:
        result = assess(lambda *_: None)
        assert result["status"] == "changes_required"
        state = review.state
        record = state["legacyResolutions"][-1]
        assert record["status"] == "classified" and record["productGapAdjudicationRefs"]
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert not restored.has_legacy_probes and restored.state["pendingLegacyResolution"] is None
        for refs in ([], ["unrelated-review.json"], record["productGapAdjudicationRefs"] * 2):
            altered = deepcopy(state)
            altered["legacyResolutions"][-1]["productGapAdjudicationRefs"] = refs
            with pytest.raises(ValueError, match="independent authority"):
                ImplementationCompletionReview.from_state(review.spec, altered)
        altered = deepcopy(state)
        altered["legacyResolutions"][-1]["status"] = "activated"
        with pytest.raises(ValueError, match="passed independent acceptance"):
            ImplementationCompletionReview.from_state(review.spec, altered)
    finally:
        cap.close()


def test_ever_passed_acceptance_remains_a_product_regression_not_a_retirable_probe(tmp_path, monkeypatch):
    from test_implementation_completion_review import _probe, PROBE
    review, assess, _, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=lambda *_: pytest.fail("a once-passed acceptance cannot be retired by adjudication"))
    try:
        def first(tools, child, packet):
            ok, receipt = _probe(tools, child, packet, PROBE)
            assert ok.ok
            assert _submit(tools, child, packet, ids=[receipt["probeId"]]).ok
        assert assess(first)["status"] == "approved"
        (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

        def second(tools, child, packet):
            result = _submit(tools, child, packet, status="changes_required",
                rows=[{"id": r["id"], "status": "missing", "probeIds": [], "sourceEvidence": [],
                       "rationale": "The retained acceptance is now a regression."} for r in packet["requirements"]],
                findings=["release stopped adding the requested amount"])
            assert result.ok
        result = assess(second)
        assert result["status"] == "changes_required" and result["repairProbes"][0]["code"] == PROBE
        assert review.state["failureAdjudications"] == [] and review.state["retiredProbeIds"] == []
    finally:
        cap.close()


def test_corrected_expectation_exposes_real_gap_and_returns_it_in_same_assessment(tmp_path, monkeypatch):
    tail = "    assert release(3, 2.5) == 5.5\n"
    wrong, right = WRONG + tail, RIGHT + tail
    calls = []

    def attribution(tools, child, packet):
        calls.append(packet["probe"]["code"])
        return judge("probe_error" if len(calls) == 1 else "product_gap", code=right)(tools, child, packet)

    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy),
        adjudication_driver=attribution)
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n + int(amount)"))
    review.queue_legacy_resolution(_resolution(review, tmp_path, replacement=wrong))
    def proposal(tools, child, packet):
        assert review.state["corrections"] == []
        result = _submit(tools, child, packet, status="changes_required", rows=_rows(packet, status="missing"),
            findings=["The corrected expectation exposes the actual missing fractional-quantity behavior."])
        assert result.ok, result.output

    try:
        result = assess(proposal)
        assert result["status"] == "changes_required" and calls == [wrong, right]
        assert [p["code"] for p in result["repairProbes"]] == [right]
        assert review.state["pendingLegacyResolution"] is None
        assert review.state["legacyResolutions"][-1]["status"] == "classified"
        assert [a["verdict"] for a in review.state["failureAdjudications"]] == ["probe_error", "product_gap"]
        old_history = review.state
        before = len(commands)
        (tmp_path / "stock.py").write_text(SOURCE)
        accepted = assess(approve)
        assert accepted["status"] == "approved" and calls == [wrong, right]
        assert len(commands) == before + 2
        assert review.state["legacyResolutions"] == old_history["legacyResolutions"]
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert not restored.has_legacy_probes
    finally:
        cap.close()


ALIASED_FIXTURE = (
    "from stock import hold, release\n"
    "def entry(records, key):\n    return records.setdefault(key, {'quantity': 5})\n"
    "def test_inventory():\n"
    "    records = {}\n"
    "    used = entry(records, 'warehouse')\n"
    "    used['quantity'] = hold(used['quantity'], 2)\n"
    "    fresh = entry(records, 'warehouse')\n"
    "    restored = release(fresh['quantity'], 4)\n"
    "    assert used['quantity'] == 3\n"
    "    assert restored == 9\n"
)
SEPARATE_FIXTURE = ALIASED_FIXTURE.replace("fresh = entry(records, 'warehouse')", "fresh = entry(records, 'reserve')")


def test_independent_fixture_alias_correction_keeps_all_assertions_and_product_bytes(tmp_path, monkeypatch):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    calls = []
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy),
        adjudication_driver=judge(calls=calls, code=SEPARATE_FIXTURE,
            reason="The fixture aliases its used and fresh records through the same setdefault key. Give the fresh record its own key; retain both original hold and release assertions."))
    review.queue_legacy_resolution(_resolution(review, tmp_path, replacement=ALIASED_FIXTURE))
    before = (tmp_path / "stock.py").read_bytes()
    try:
        result = assess(approve)
        assert result["status"] == "approved" and len(calls) == 1 and len(commands) == 4
        assert (tmp_path / "stock.py").read_bytes() == before
        state = review.state
        correction = state["corrections"][0]
        assert_independently_audited(review, correction)
        assert correction["correctionMode"] == "fixture_inputs"
        assert [row["receipt"]["status"] for row in state["receipts"]][-2:] == ["failed", "passed"]
        assert next(p["code"] for p in state["probes"] if p["probeId"] == correction["oldProbeId"]) == ALIASED_FIXTURE
        assert next(p["code"] for p in state["probes"] if p["probeId"] == correction["newProbeId"]) == SEPARATE_FIXTURE
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored.state["corrections"] == state["corrections"] and not restored.has_legacy_probes
        changed = deepcopy(state)
        changed["corrections"][0]["correctionMode"] = "expected_literal"
        with pytest.raises(ValueError, match="independent pending request"):
            ImplementationCompletionReview.from_state(review.spec, changed)
    finally:
        cap.close()
