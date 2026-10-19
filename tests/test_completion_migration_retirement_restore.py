"""Historical migration scope survives later independently authorized retirement."""

from copy import deepcopy

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.probe_supersession import read_audit_record
from test_completion_failure_adjudication import WRONG, judge, approve
from test_completion_probe_purposes import _legacy, _resolution
from test_implementation_completion_review import _fixture, _probe, _rows, _submit


def _migrated_then_corrected(tmp_path, monkeypatch, *, earlier_retired=False):
    seed, assess, _, cap = _fixture(tmp_path, monkeypatch)

    def observe(tools, child, packet):
        if earlier_retired:
            bad_setup = ("from stock import hold, release\namount = int('invalid')\n"
                         "def test_original_inventory():\n    assert hold(5, amount) == 3\n"
                         "    assert release(3, amount) == 5\n")
            result, original = _probe(tools, child, packet, bad_setup)
            assert not result.ok and original["receipt"]["status"] == "inconclusive"
            fixed, receipt = _probe(tools, child, packet, bad_setup.replace("'invalid'", "'2'"),
                correctsProbeId=original["probeId"], correctionReason="Repair the invalid quantity fixture.",
                sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
            assert fixed.ok and receipt["receipt"]["status"] == "passed"
        result, receipt = _probe(tools, child, packet, WRONG, purpose="diagnostic")
        assert not result.ok and receipt["receipt"]["status"] == "failed"
        assert _submit(tools, child, packet, status="changes_required",
            rows=_rows(packet, status="missing"), findings=["Inventory acceptance is unverified."]).ok

    try:
        assert assess(observe)["status"] == "changes_required"
        legacy = _legacy(seed.state)
    finally:
        cap.close()

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy),
        adjudication_driver=judge())
    review.queue_legacy_resolution(_resolution(review, tmp_path, purpose="acceptance"))
    try:
        original_bytes = (tmp_path / "stock.py").read_bytes()
        assert assess(approve)["status"] == "approved"
        state = review.state
        old_id = next(p["probeId"] for p in legacy["probes"] if p["code"] == WRONG)
        assert state["legacyResolutions"][-1]["status"] == "activated"
        assert state["probePurposes"][old_id]["origin"] == "legacy_migration"
        assert set(state["retiredProbeIds"]) == {old_id, *legacy["retiredProbeIds"]}
        assert state["corrections"][-1]["oldProbeId"] == old_id
        correction = state["corrections"][-1]
        assert correction["correctionKind"] == "audited_semantic_supersession"
        request = next(row for row in state["supersessions"]
            if row["supersessionId"] == correction["supersessionId"])
        assert request["oldProbeId"] == old_id
        assert request["newProbeId"] == correction["newProbeId"]
        assert request["adjudicationRef"] == correction["adjudicationRef"]
        audit, authority = read_audit_record(correction["coverageAudit"])
        accepted = next(row for row in audit["supersessions"] if row["id"] == request["supersessionId"])
        assert accepted["status"] == "accepted"
        assert {row["id"] for row in accepted["requirements"]} == set(request["criterionIds"])
        assert authority["originalObjective"] == review.spec.objective
        assert all(pid not in state["probePurposes"] for pid in legacy["retiredProbeIds"])
        assert state["failureAdjudications"][0]["verdict"] == "probe_error"
        assert (tmp_path / "stock.py").read_bytes() == original_bytes
        assert len(commands) == 3 + int(earlier_retired)
        return review.spec, state
    finally:
        cap.close()


@pytest.mark.parametrize("earlier_retired", [False, True])
def test_native_restore_replays_migration_before_later_acceptance_retirement(tmp_path, monkeypatch, earlier_retired):
    spec, state = _migrated_then_corrected(tmp_path, monkeypatch, earlier_retired=earlier_retired)
    restored = ImplementationCompletionReview.from_state(spec, state)
    for field in ("probes", "receipts", "corrections", "retiredProbeIds", "probePurposes",
                  "legacyResolutions", "failureAdjudications", "supersessions"):
        assert restored.state[field] == state[field]
    assert not restored.has_legacy_probes and not restored._cached
    assert restored.state["pendingLegacyResolution"] is None

    resumed, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda _: restored,
        adjudication_driver=lambda *_: pytest.fail("a validated correction must not be adjudicated again"))
    try:
        before = len(resumed.state["receipts"])
        assert assess(approve)["status"] == "approved"
        assert len(commands) == 2 + int(earlier_retired)
        assert len(resumed.state["receipts"]) == before + 1 + int(earlier_retired)
        assert resumed.state["receipts"][-1]["probeId"] == state["corrections"][-1]["newProbeId"]
        assert resumed.state["legacyResolutions"] == state["legacyResolutions"]
    finally:
        cap.close()


@pytest.mark.parametrize("tamper", ["omit", "duplicate", "source"])
def test_historical_migration_keeps_exact_cover_and_source_binding(tmp_path, monkeypatch, tamper):
    spec, state = _migrated_then_corrected(tmp_path, monkeypatch)
    altered = deepcopy(state)
    rows = altered["legacyResolutions"][0]["resolution"]["decisions"]
    if tamper == "omit":
        rows.clear()
    elif tamper == "duplicate":
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]["sourceDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        ImplementationCompletionReview.from_state(spec, altered)


def test_live_queue_does_not_reactivate_retired_migrated_acceptance(tmp_path, monkeypatch):
    spec, state = _migrated_then_corrected(tmp_path, monkeypatch)
    restored = ImplementationCompletionReview.from_state(spec, state)
    with pytest.raises(ValueError, match="each active legacy probe exactly once"):
        restored.queue_legacy_resolution(state["legacyResolutions"][0]["resolution"])
    assert restored.state["pendingLegacyResolution"] is None


def test_historical_projection_cannot_omit_an_active_legacy_obligation(tmp_path, monkeypatch):
    spec, state = _migrated_then_corrected(tmp_path, monkeypatch, earlier_retired=True)
    altered = deepcopy(state)
    rows = altered["legacyResolutions"][0]["resolution"]["decisions"]
    assert len(rows) == 2
    rows[:] = [row for row in rows if row["probeId"] in state["retiredProbeIds"]]
    assert len(rows) == 1
    with pytest.raises(ValueError, match="each active legacy probe exactly once"):
        ImplementationCompletionReview.from_state(spec, altered)
