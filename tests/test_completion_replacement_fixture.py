"""Pending migration can repair independent fixtures without changing product evidence."""

from copy import deepcopy
import json

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.types import AgentStep
from test_completion_probe_purposes import _resolution, _seed_legacy
from test_implementation_completion_review import _fixture, _probe, _rows, _submit, PROBE, SOURCE


BAD = (
    "from stock import hold, release\n"
    "def test_inventory(tmp_path):\n"
    "    folder = tmp_path / 'inventory-fixture'\n"
    "    folder.mkdir(exist_ok=False)\n"
    "    folder.mkdir(exist_ok=False)\n"
    "    assert hold(5, 2) == 3\n"
    "    assert release(3, 2) == 5\n"
)
GOOD = BAD.replace("    folder.mkdir(exist_ok=False)\n    folder.mkdir(exist_ok=False)",
                   "    folder.mkdir(exist_ok=False)\n    folder.mkdir(exist_ok=True)")


def fixture_adjudicator(calls):
    def driver(tools, child, packet):
        calls.append(packet["probe"]["code"])
        fixture_error = packet["probe"]["code"] == BAD
        request = {
            "verdict": "probe_error" if fixture_error else "product_gap",
            "reason": (
                "The fixture creates the same temporary directory twice without allowing an existing directory; "
                "the second creation should be idempotent, preserving both inventory assertions."
                if fixture_error else
                "The release contract requires addition but the implementation returns the original quantity."
            ),
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
        }
        if fixture_error:
            request["replacement"] = {"criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"], "code": GOOD}
        submitted = tools.execute("completion_probe_adjudication_submit", request, child)
        assert submitted.ok, submitted.output
        return AgentStep(type="assistant", kind="final", content="Fixture or product attribution recorded")
    return driver


def pending(tmp_path, monkeypatch, *, product=SOURCE, replacement=BAD, adjudication_calls=None):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy),
        adjudication_driver=fixture_adjudicator(adjudication_calls if adjudication_calls is not None else []))
    (tmp_path / "stock.py").write_text(product)
    review.queue_legacy_resolution(_resolution(review, tmp_path, replacement=replacement))
    return review, assess, commands, cap


def correct(review, tools, child, packet, *, code=GOOD, criteria=None):
    old = next(probe for probe in review.state["probes"] if probe["code"] == BAD)
    return _probe(tools, child, packet, code, scenario=old["scenario"],
        criterionIds=old["criterionIds"] if criteria is None else criteria,
        correctsProbeId=old["probeId"],
        correctionReason="The independent fixture creates its temporary directory twice; product assertions stay unchanged.",
        sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])


def test_pending_replacement_fixture_can_be_corrected_then_approved_without_product_edit(tmp_path, monkeypatch):
    review, assess, commands, cap = pending(tmp_path, monkeypatch)
    entered = []

    def driver(tools, child, packet):
        entered.append(True)
        assert review.has_legacy_probes, "Migration remains pending until independent replacement coverage is accepted."
        assert review.state["corrections"] == [] and len(review.state["supersessions"]) == 1
        assert len(review.state["failureAdjudications"]) == 1
        assert review.state["failureAdjudications"][0]["verdict"] == "probe_error"
        assert all(probe["status"] == "passed" for probe in packet["retainedProbes"])
        assert _submit(tools, child, packet, ids=[probe["probeId"] for probe in packet["retainedProbes"]]).ok

    try:
        result = assess(driver)
        assert result["status"] == "approved" and entered == [True]
        assert len(commands) == 4  # public, old diagnostic, bad fixture, corrected acceptance
        assert (tmp_path / "stock.py").read_text() == SOURCE
        state = review.state
        old = next(probe for probe in state["probes"] if probe["code"] == BAD)
        new = next(probe for probe in state["probes"] if probe["code"] == GOOD)
        correction = state["corrections"][0]
        assert state["retiredProbeIds"] == [old["probeId"]]
        assert correction["oldProbeId"] == old["probeId"] and correction["newProbeId"] == new["probeId"]
        activated = state["legacyResolutions"][-1]
        assert activated["status"] == "activated"
        assert activated["replacementProbeIds"] == [new["probeId"]]
        assert activated["replacementChains"] == [{"originalProbeId": old["probeId"],
            "activeProbeId": new["probeId"], "correctionRefs": [correction["correctionRef"]]}]
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert not restored.has_legacy_probes and not restored._cached
        for field in ("receipts", "probes", "corrections", "legacyResolutions", "retiredProbeIds"):
            assert restored.state[field] == state[field]
    finally:
        cap.close()


def test_real_product_assertion_failure_still_returns_to_executor_before_review(tmp_path, monkeypatch):
    product = SOURCE.replace("return n + amount", "return n")
    review, assess, _commands, cap = pending(tmp_path, monkeypatch, product=product, replacement=PROBE)
    try:
        result = assess(lambda *_args: pytest.fail("a genuine product assertion needs its original Executor"))
        assert result["status"] == "changes_required" and result["reason"] == "legacy_acceptance_product_gap"
        assert not review.has_legacy_probes and review.state["corrections"] == []
        assert result["repairProbes"][0]["code"] == PROBE
    finally:
        cap.close()


def test_fixture_repair_exposes_product_failure_without_activating_or_changing_assertions(tmp_path, monkeypatch):
    product = SOURCE.replace("return n + amount", "return n")
    calls = []
    review, assess, _commands, cap = pending(tmp_path, monkeypatch, product=product, adjudication_calls=calls)

    def proposal(tools, child, packet):
        assert review.state["corrections"] == []
        assert _submit(tools, child, packet, status="changes_required", rows=_rows(packet, status="missing"),
            findings=["The corrected fixture exposes the actual missing release behavior."]).ok

    try:
        result = assess(proposal)
        assert calls == [BAD, GOOD] and result["status"] == "changes_required"
        assert not review.has_legacy_probes and len(review.state["corrections"]) == 1
        assert [probe["code"] for probe in result["repairProbes"]] == [GOOD]
        assert (tmp_path / "stock.py").read_text() == product
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert not restored.has_legacy_probes
    finally:
        cap.close()


@pytest.mark.parametrize("tamper", ["assertion", "criteria"])
def test_ordinary_fixture_correction_cannot_weaken_the_acceptance_contract(tmp_path, monkeypatch, tamper):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=lambda *_: pytest.fail("The same review corrects its fixture before completion submission"))
    entered = []

    def driver(tools, child, packet):
        entered.append(True)
        failed, evidence = _probe(tools, child, packet, BAD)
        assert not failed.ok and evidence["receipt"]["status"] == "failed"
        arguments = {"code": GOOD.replace("== 5", "== 999")} if tamper == "assertion" else {
            "criteria": [packet["requirements"][0]["id"]]}
        rejected, _ = correct(review, tools, child, packet, **arguments)
        assert not rejected.ok and review.state["corrections"] == []
        passed, receipt = correct(review, tools, child, packet)
        assert passed.ok and _submit(tools, child, packet, ids=[receipt["probeId"]]).ok

    try:
        assert assess(driver)["status"] == "approved" and entered == [True]
    finally:
        cap.close()


def test_interrupted_fixture_correction_restores_successor_and_rechecks_before_delivery(tmp_path, monkeypatch):
    review, assess, _commands, cap = pending(tmp_path, monkeypatch)

    def interrupted(tools, child, packet):
        assert review.state["corrections"] == [] and len(review.state["supersessions"]) == 1
        assert review.state["failureAdjudications"][0]["verdict"] == "probe_error"
        assert packet["retainedProbes"][0]["status"] == "passed"
        raise KeyboardInterrupt("interrupted after durable correction")

    try:
        with pytest.raises(KeyboardInterrupt, match="durable correction"):
            assess(interrupted)
        state = review.state
    finally:
        cap.close()
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, state))

    def final_review(tools, child, packet):
        assert review.has_legacy_probes and review.state["corrections"] == []
        assert len(packet["retainedProbes"]) == 1 and packet["retainedProbes"][0]["status"] == "passed"
        assert _submit(tools, child, packet, ids=[packet["retainedProbes"][0]["probeId"]]).ok

    try:
        assert assess(final_review)["status"] == "approved"
        assert len(commands) == 4, "Pending recovery rechecks public, legacy, old fixture and successor before audit."
        assert len(review.state["corrections"]) == 1
        corrupted = deepcopy(review.state)
        activated = corrupted["legacyResolutions"][-1]
        activated["replacementChains"][0]["correctionRefs"] = []
        with pytest.raises(ValueError):
            ImplementationCompletionReview.from_state(review.spec, corrupted)
        assert "FileExistsError" in json.dumps(review.state["receipts"])
    finally:
        cap.close()


def test_pending_resolution_follows_setup_then_exception_corrections_and_preserves_original_authority(tmp_path, monkeypatch):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy))
    original = ("from stock import hold, release\nsetup_amount = int('setup-invalid')\n"
                "def test_inventory():\n"
                "    amount = int('call-invalid')\n    assert hold(5, amount) == 3\n"
                "    assert release(3, amount) == 5\n")
    replacement_id = []

    def prepare(tools, child, packet):
        failed, receipt = _probe(tools, child, packet, original)
        assert not failed.ok
        assert receipt["receipt"]["status"] == "inconclusive"
        # Both ordinary corrections retain every assertion. The first fixes
        # collection, exposing an exception; the second fixes that exception.
        collected = original.replace("setup-invalid", "2")
        for source, expected in ((collected, "failed"), (collected.replace("call-invalid", "2"), "passed")):
            execution, receipt = _probe(tools, child, packet, source,
                correctsProbeId=receipt["probeId"], correctionReason="Correct the quantity fixture, retaining both assertions.",
                sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
            assert receipt["receipt"]["status"] == expected
            assert execution.ok is (expected == "passed")
        replacement_id.append(receipt["probeId"])
        assert _submit(tools, child, packet, status="changes_required", rows=_rows(packet, status="missing"),
            findings=["Legacy diagnostic purpose still needs its independent host authority."]).ok

    try:
        assert assess(prepare)["status"] == "changes_required"
        resolution = _resolution(review, tmp_path, replacement=original)
        # The semantic authority identifies the exact original source/scenario;
        # corrections remain separate evidence, not an edited resolution.
        resolution["decisions"][0]["acceptanceProbes"][0]["scenario"] = "Both inventory operations preserve quantity semantics"
        review.queue_legacy_resolution(resolution)

        def approve(tools, child, packet):
            assert not review.has_legacy_probes
            assert _submit(tools, child, packet, ids=replacement_id).ok

        assert assess(approve)["status"] == "approved"
        state = review.state
        activation = state["legacyResolutions"][-1]
        assert activation["resolution"] == resolution
        assert activation["replacementProbeIds"] == replacement_id
        assert activation["replacementChains"][0]["correctionRefs"] == [row["correctionRef"] for row in state["corrections"]]
        assert len(state["retiredProbeIds"]) == 2
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        assert restored.state["corrections"] == state["corrections"]
        assert restored.state["receipts"] == state["receipts"]
    finally:
        cap.close()


def test_new_candidate_cannot_reuse_pending_semantic_authority_or_fixture_evidence(tmp_path, monkeypatch):
    review, assess, _commands, cap = pending(tmp_path, monkeypatch)
    (tmp_path / "stock.py").write_text(SOURCE + "\n# independently changed candidate\n")
    try:
        result = assess(lambda *_args: pytest.fail("stale binding cannot enter fixture correction or final review"))
        assert result["status"] == "inconclusive" and result["reason"] == "legacy_resolution_stale"
        assert review.has_legacy_probes and review.state["corrections"] == []
        assert not review.state["legacyResolutions"]
    finally:
        cap.close()


def test_executor_product_repair_rechecks_classified_acceptance_without_reclassifying(tmp_path, monkeypatch):
    product = SOURCE.replace("return n + amount", "return n")
    calls = []
    review, assess, commands, cap = pending(tmp_path, monkeypatch, product=product, adjudication_calls=calls)

    def proposal(tools, child, packet):
        assert review.state["corrections"] == []
        assert _submit(tools, child, packet, status="changes_required", rows=_rows(packet, status="missing"),
            findings=["The corrected acceptance test confirms release has not implemented addition."]).ok

    try:
        rejected = assess(proposal)
        assert rejected["status"] == "changes_required"
        assert calls == [BAD, GOOD]
        old_candidate = rejected["patchDigest"]
        old_state = review.state
        assert old_state["pendingLegacyResolution"] is None and not review.has_legacy_probes
        assert old_state["legacyResolutions"][-1]["status"] == "classified"
        active = next(probe for probe in old_state["probes"] if probe["code"] == GOOD)
        (tmp_path / "stock.py").write_text(SOURCE)
        before = len(commands)

        def approve(tools, child, packet):
            assert not review.has_legacy_probes
            assert packet["candidateDigest"] != old_candidate
            assert _submit(tools, child, packet, ids=[active["probeId"]]).ok

        accepted = assess(approve)
        assert accepted["status"] == "approved"
        assert calls == [BAD, GOOD], "Repaired current acceptance is executed, not semantically classified again."
        assert len(commands) == before + 2  # Current public and acceptance both really execute.
        assert review.state["legacyResolutions"] == old_state["legacyResolutions"]
        current = [r for r in review.state["receipts"] if r["probeId"] == active["probeId"]]
        assert [r["receipt"]["status"] for r in current] == ["failed", "passed"]
        assert current[-1]["candidateDigest"] == accepted["patchDigest"]
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert not restored.has_legacy_probes
        assert restored.state["corrections"] == old_state["corrections"]
        assert all(receipt in restored.state["receipts"] for receipt in old_state["receipts"])
    finally:
        cap.close()
