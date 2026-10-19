"""Real probe execution keeps derived commands bound through workflow replay.

Only model choices are scripted. Candidate edits, pytest, probe correction,
publication, repair, promotion and durable audit all use production code.
"""

import copy
import hashlib
import json

import pytest

from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.scheduler_audit import SchedulerAuditValidationError
from test_review_coverage_audit import _replay
from test_review_requirement_challenge import COMPLETE, REPAIRED_TEST, _exercise_blind_approval


@pytest.fixture(scope="module")
def bound_workflow(tmp_path_factory):
    path = tmp_path_factory.mktemp("derived-probe-workflow")
    with pytest.MonkeyPatch.context() as monkeypatch:
        result, trace, audit, *_ = _exercise_blind_approval(
            path, monkeypatch, independent_challenge=True, harness_correction=True,
        )
    assert result.ok and trace["committed"], result.output
    documents = {artifact.artifact_id: json.loads(artifact.content) for artifact in audit.artifacts}
    return path, audit, documents


def test_derived_probe_binding_survives_publish_repair_correction_and_audit(bound_workflow):
    path, audit, documents = bound_workflow
    assert (path / "cache.py").read_text() == COMPLETE
    assert (path / "test_regression.py").read_text() == REPAIRED_TEST
    assert not (path / "__minicode_review_probe__.py").exists()
    executions = [doc for doc in documents.values() if doc.get("resultType") == "ReviewProbeExecution.v1"]
    corrections = [doc for doc in documents.values() if doc.get("resultType") == "ReviewProbeCorrection.v1"]
    assert len(executions) == 9 and len(corrections) == 1
    assert {doc["status"] for doc in executions} == {"passed", "failed"}
    for receipt in executions:
        binding = receipt["executionBinding"]
        assert binding["bindingVersion"] == 1
        assert binding["patchDigest"] == receipt["patchDigest"]
        assert binding["originalTarget"] == "test_regression.py"
        assert binding["probeTarget"] == "__minicode_review_probe__.py"
        assert binding["baseCheck"]["argv"] == ["python", "-m", "pytest", "-q", "test_regression.py"]
        assert binding["derivedCheck"]["argv"] == receipt["evidence"]["argv"] == [
            "python", "-m", "pytest", "-q", "__minicode_review_probe__.py",
        ]
        assert binding["probeSourceDigest"] == documents[receipt["sourceRef"]]["sourceDigest"]
    assert _replay(audit, documents) == audit


@pytest.mark.parametrize("change", ["remove", "patch", "source", "target", "base_timeout", "argv"])
@pytest.mark.parametrize("selection", ["historical", "approval"])
def test_real_archive_rejects_unbound_or_tampered_probe_commands(bound_workflow, change, selection):
    _path, audit, original = bound_workflow
    documents = copy.deepcopy(original)
    if selection == "historical":
        # Correction must not erase the original failed command's
        # authorization obligation from replay.
        correction = next(doc for doc in documents.values() if doc.get("resultType") == "ReviewProbeCorrection.v1")
        receipt = documents[correction["oldReceiptRefs"][0]]
    else:
        receipt = max((doc for doc in documents.values() if doc.get("resultType") == "ReviewProbeExecution.v1"),
                      key=lambda doc: doc["sequence"])
    binding = receipt["executionBinding"]
    if change == "remove":
        del receipt["executionBinding"]
    elif change == "patch":
        binding["patchDigest"] = "sha256:" + "f" * 64
    elif change == "source":
        binding["probeSourceDigest"] = "sha256:" + "f" * 64
    elif change == "target":
        binding["probeTarget"] = "arbitrary.py"
    elif change == "base_timeout":
        # An internally consistent derivation still cannot replace the host's
        # locked base check with a new timeout policy.
        binding["baseCheck"]["timeoutSeconds"] += 1
        binding["derivedCheck"]["timeoutSeconds"] += 1
    else:
        receipt["evidence"]["argv"].append("--unapproved-option")
        receipt["evidence"]["commandDigest"] = "sha256:" + hashlib.sha256(
            "\0".join(receipt["evidence"]["argv"]).encode(),
        ).hexdigest()
    with pytest.raises(SchedulerAuditValidationError):
        _replay(audit, documents)


@pytest.mark.parametrize("change", ["patch", "source", "argv", "inconclusive"])
def test_ledger_restore_validates_binding_against_exact_probe(bound_workflow, change):
    _path, _audit, documents = bound_workflow
    receipt = copy.deepcopy(next(doc for doc in documents.values()
                                 if doc.get("resultType") == "ReviewProbeExecution.v1"))
    source = documents[receipt["sourceRef"]]
    probe = ReviewerProbe.create(criterion_ids=source["criterionIds"], scenario=source["scenario"], code=source["code"])
    accepted = ReviewerProbeLedger().record(probe, receipt["patchDigest"], receipt)
    assert accepted["executionBinding"] == receipt["executionBinding"]
    if change in {"patch", "inconclusive"}:
        receipt["executionBinding"]["patchDigest"] = "sha256:" + "f" * 64
        if change == "inconclusive":
            receipt["status"] = "inconclusive"
    elif change == "source":
        receipt["executionBinding"]["probeSourceDigest"] = "sha256:" + "f" * 64
    else:
        receipt["executionBinding"]["derivedCheck"]["argv"].append("--unapproved-option")
    with pytest.raises(ValueError):
        ReviewerProbeLedger().record(probe, receipt["patchDigest"], receipt)
