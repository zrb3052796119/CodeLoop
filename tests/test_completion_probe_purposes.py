"""Diagnostic intent, host migration, and resumed evidence on real pytest."""

import copy
from pathlib import Path
import shutil

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import implementation_candidate_digest
from minicode.tooling import ToolContext
from test_implementation_completion_review import _fixture, _probe, _rows, _submit, PROBE, SOURCE


DIAGNOSIS = "from stock import release\ndef test_defect():\n    assert release(3, 2) == 3\n"


def _legacy(state):
    # This explicitly constructs the pre-purpose v1 host checkpoint format.
    # Production never derives this downgrade from a reviewer tool request.
    value = copy.deepcopy(state)
    for field in ("completionReviewStateVersion", "probePurposes", "legacyResolutions", "pendingLegacyResolution"):
        value.pop(field, None)
    return value


def _resolution(review, tmp_path, *, replacement=PROBE, purpose="diagnostic"):
    return {"resolutionVersion": 1, "objectiveDigest": review.objective_digest,
        "candidateDigest": implementation_candidate_digest(review.spec, ToolContext(cwd=str(tmp_path))),
        "reviewEvidenceRef": "host-independent-semantic-review.json", "decisions": [
            {"probeId": probe["probeId"], "sourceDigest": probe["sourceDigest"], "purpose": purpose,
             "reason": "The exact original objective requires release to add; this v1 assertion observes its absence.",
             "acceptanceProbes": [] if purpose == "acceptance" else [{
                 "criterionIds": probe["criterionIds"], "scenario": "Independent original inventory acceptance", "code": replacement}]}
            for probe in review.state["probes"] if probe["probeId"] not in review.state["retiredProbeIds"]
            and probe["probeId"] not in review.state["probePurposes"]]}


def _seed_legacy(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def diagnose(tools, child, packet):
        execution, _ = _probe(tools, child, packet, DIAGNOSIS, purpose="diagnostic")
        assert execution.ok
        assert _submit(tools, child, packet, rows=_rows(packet, status="missing"),
                       status="changes_required", findings=["release remains a no-op"]).ok

    assert assess(diagnose)["status"] == "changes_required"
    state = _legacy(review.state)
    cap.close()
    return state


def test_diagnostic_failure_after_repair_does_not_block_real_acceptance(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def diagnose(tools, child, packet):
        execution, evidence = _probe(tools, child, packet, DIAGNOSIS, purpose="diagnostic")
        assert execution.ok
        rejected = _submit(tools, child, packet, ids=[evidence["probeId"]])
        assert not rejected.ok, "even a passing diagnostic can never certify covered"
        assert _submit(tools, child, packet, rows=_rows(packet, status="missing"),
                       status="changes_required", findings=["release remains a no-op"]).ok

    try:
        assert assess(diagnose)["status"] == "changes_required"
        (tmp_path / "stock.py").write_text(SOURCE)

        def accept(tools, child, packet):
            assert packet["retainedProbes"] == [], "diagnostics are not mandatory regressions"
            execution, _ = _probe(tools, child, packet, DIAGNOSIS, purpose="diagnostic")
            assert not execution.ok
            passed, evidence = _probe(tools, child, packet)
            assert passed.ok
            assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

        decision = assess(accept)
        assert decision["status"] == "approved" and decision["repairProbes"] == []
        assert [r["receipt"]["status"] for r in decision["diagnosticObservations"][0]["receipts"]] == ["passed", "failed"]
    finally:
        cap.close()


def test_purpose_is_mandatory_and_acceptance_cannot_be_relabelled(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        definition = next(tool for tool in tools.list() if tool.name == "review_test")
        assert "purpose" in definition.input_schema["required"]
        missing = tools.execute("review_test", {"criterionIds": [r["id"] for r in packet["requirements"]],
            "scenario": "An unclassified probe is not a new acceptance obligation", "code": PROBE}, child)
        assert not missing.ok and review.state["probes"] == []
        passed, evidence = _probe(tools, child, packet)
        assert passed.ok
        relabelled, _ = _probe(tools, child, packet, purpose="diagnostic")
        assert not relabelled.ok
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    try:
        assert assess(driver)["status"] == "approved"
        with pytest.raises(ValueError, match="active legacy"):
            review.queue_legacy_resolution(_resolution(review, tmp_path))
    finally:
        cap.close()


def test_new_diagnostic_cannot_neutralize_a_real_acceptance_regression(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)

    def approve(tools, child, packet):
        passed, evidence = _probe(tools, child, packet)
        assert passed.ok
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    try:
        assert assess(approve)["status"] == "approved"
        (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

        def regressed(tools, child, packet):
            assert packet["retainedProbes"][0]["status"] == "failed"
            diagnostic, _ = _probe(tools, child, packet, DIAGNOSIS, purpose="diagnostic")
            assert diagnostic.ok
            incomplete, receipt = _probe(tools, child, packet, PROBE.replace("    assert release(3, 2) == 5\n", ""))
            assert incomplete.ok
            assert not _submit(tools, child, packet, ids=[receipt["probeId"]]).ok
            assert _submit(tools, child, packet, rows=_rows(packet, status="missing"),
                           status="changes_required", findings=["The original acceptance regression still fails."]).ok

        assert assess(regressed)["status"] == "changes_required"
        assert len(review.state["retiredProbeIds"]) == 0
    finally:
        cap.close()


def test_legacy_diagnosis_migrates_only_after_real_acceptance_and_survives_restore(tmp_path, monkeypatch):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy))
    assert review.has_legacy_probes
    review.queue_legacy_resolution(_resolution(review, tmp_path))

    def driver(tools, child, packet):
        assert not review.has_legacy_probes
        ids = [probe["probeId"] for probe in packet["retainedProbes"]]
        assert len(ids) == 1 and packet["retainedProbes"][0]["status"] == "passed"
        assert _submit(tools, child, packet, ids=ids).ok

    try:
        assert assess(driver)["status"] == "approved"
        saved = review.state
        assert [r["receipt"]["status"] for r in saved["receipts"]] == ["passed", "failed", "passed"]
        restored = ImplementationCompletionReview.from_state(review.spec, saved)
        assert restored.state["receipts"] == saved["receipts"]
        assert restored.state["legacyResolutions"] == saved["legacyResolutions"]
        assert not restored.has_legacy_probes
        assert not restored._cached
    finally:
        cap.close()


def test_confirmed_product_gap_preserves_acceptance_and_diagnostic_history(tmp_path, monkeypatch):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy))
    # Integer release is repaired, so the defect-detection probe now fails;
    # fractional release still violates the same original addition objective.
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n + int(amount)"))
    acceptance = PROBE + "    assert release(3, 2.5) == 5.5\n"
    review.queue_legacy_resolution(_resolution(review, tmp_path, replacement=acceptance))
    try:
        result = assess(lambda *_args: pytest.fail("failed migration cannot enter final approval review"))
        assert result["status"] == "changes_required"
        assert result["reason"] == "legacy_acceptance_product_gap"
        assert any(source["code"] == acceptance for source in result["repairProbes"])
        assert all(source["code"] != DIAGNOSIS for source in result["repairProbes"])
        assert result["diagnosticObservations"][0]["source"]["code"] == DIAGNOSIS
        assert result["diagnosticObservations"][0]["receipts"][-1]["receipt"]["status"] == "failed"
        assert not review.has_legacy_probes and review.state["pendingLegacyResolution"] is None
        restored = ImplementationCompletionReview.from_state(review.spec, review.state)
        assert not restored.has_legacy_probes and restored.state["receipts"] == review.state["receipts"]
    finally:
        cap.close()


@pytest.mark.parametrize("bad", ["source", "candidate", "criteria", "same_source"])
def test_host_migration_binding_errors_never_reclassify_legacy(tmp_path, monkeypatch, bad):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy))
    resolution = _resolution(review, tmp_path)
    if bad == "source":
        resolution["decisions"][0]["sourceDigest"] = "sha256:" + "0" * 64
    elif bad == "criteria":
        resolution["decisions"][0]["acceptanceProbes"][0]["criterionIds"].pop()
    elif bad == "same_source":
        resolution["decisions"][0]["acceptanceProbes"][0]["code"] = DIAGNOSIS
    else:
        resolution["candidateDigest"] = "sha256:" + "0" * 64
    try:
        if bad == "candidate":
            review.queue_legacy_resolution(resolution)
            result = assess(lambda *_args: pytest.fail("stale authority cannot enter reviewer"))
            assert result["status"] == "inconclusive" and result["reason"] == "legacy_resolution_stale"
        else:
            with pytest.raises(ValueError):
                review.queue_legacy_resolution(resolution)
        assert review.has_legacy_probes
    finally:
        cap.close()


def test_archive_restores_original_evidence_and_rechecks_an_approved_candidate(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)

    def approve(tools, child, packet):
        passed, evidence = _probe(tools, child, packet)
        assert passed.ok
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    assert assess(approve)["status"] == "approved"
    saved = review.state
    archive = Path(saved["evidenceDirectory"])
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    restored = ImplementationCompletionReview.from_archive(review.spec, archive)
    assert restored.state["receipts"] == saved["receipts"]
    assert restored.state["probes"] == saved["probes"]
    assert not restored._cached and not restored.has_legacy_probes
    cap.close()
    review2, assess2, _commands2, cap2 = _fixture(tmp_path, monkeypatch, review_factory=lambda _spec: restored)
    try:
        assert assess2(approve)["status"] == "approved"
        assert len(review2.state["receipts"]) == len(saved["receipts"]) + 1
        assert {p.name: p.read_bytes() for p in archive.iterdir()} == before
    finally:
        cap2.close()


def test_archived_source_without_receipt_is_retained_and_reexecuted(tmp_path, tmp_path_factory, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)

    def approve(tools, child, packet):
        passed, evidence = _probe(tools, child, packet)
        assert passed.ok
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    assert assess(approve)["status"] == "approved"
    archive = tmp_path_factory.mktemp("interrupted-review-archive")
    for path in Path(review.state["evidenceDirectory"]).iterdir():
        if path.name.endswith(("-probe-source.json", "-probe-purpose.json")):
            shutil.copy2(path, archive / path.name)
    restored = ImplementationCompletionReview.from_archive(review.spec, archive)
    assert len(restored.state["probes"]) == 1 and restored.state["receipts"] == []
    assert not restored._cached
    cap.close()
    _review2, assess2, _commands2, cap2 = _fixture(tmp_path, monkeypatch, review_factory=lambda _spec: restored)
    try:
        assert assess2(approve)["status"] == "approved"
        assert len(restored.state["receipts"]) == 1
    finally:
        cap2.close()


@pytest.mark.parametrize("bad", ["source", "receipt", "retirement", "purpose"])
def test_restore_rejects_corrupt_evidence_instead_of_guessing(tmp_path, monkeypatch, bad):
    legacy = _seed_legacy(tmp_path, monkeypatch)
    spec_review, _assess, _commands, cap = _fixture(tmp_path, monkeypatch)
    if bad == "source":
        legacy["probes"][0]["code"] = PROBE
    elif bad == "receipt":
        legacy["receipts"][0]["receipt"]["sourceDigest"] = "sha256:" + "0" * 64
    elif bad == "retirement":
        legacy["retiredProbeIds"] = [legacy["probes"][0]["probeId"]]
    else:
        legacy["completionReviewStateVersion"] = 2
    try:
        with pytest.raises(ValueError):
            ImplementationCompletionReview.from_state(spec_review.spec, legacy)
    finally:
        cap.close()
