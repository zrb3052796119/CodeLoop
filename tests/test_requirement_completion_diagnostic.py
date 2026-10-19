"""Offline protocol checks; synthetic task/oracle payloads only."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "requirement_completion_diagnostic", ROOT / "scripts/run_requirement_completion_diagnostic.py"
)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def source_fixture(tmp_path):
    source = tmp_path / "source"
    document = {"schemaVersion": 1, "shared_files": {}, "cases": [
        {"id": identity, "category": "synthetic", "prompt": "Implement the original public requirement.",
         "files": {"minicode/example.py": "VALUE = 1\n", "tests/test_public.py": "def test_baseline(): assert True\n"},
         "authorized_paths": ["minicode/example.py", "tests/test_eval_regression.py"],
         "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
         "oracle_source": "OPAQUE SYNTHETIC PAYLOAD — never inspected or prompted"}
        for identity in diagnostic.previous.CASES]}
    diagnostic.shared.write_json(source / "original/private/tasks.json", document)
    for case in document["cases"]:
        case["external_verification_argv"] = list(case["verification_argv"])
        case["verification_excluded_node_ids"] = ["tests/test_public.py::test_environment_requires_fork"]
        case["external_verification_node_ids"] = ["tests/test_public.py::test_baseline", *case["verification_excluded_node_ids"]]
        case["verification_argv"] += ["-k", "not test_environment_requires_fork"]
    diagnostic.shared.write_json(source / "private/tasks.json", document)
    diagnostic.shared.write_json(source / "manifest.json", {"fixture": True})
    diagnostic.shared.write_json(source / "public-cases.json", {"schemaVersion": 1, "cases": [{"id": c["id"]} for c in document["cases"]]})
    return source, document


def test_exactly_one_original_explicit_case_no_budget_or_prompt_change():
    library = diagnostic._library()
    contract = library.static_contract()
    assert contract["schedule"] == diagnostic.schedule()
    assert contract["cases"] == [diagnostic.CASE]
    assert contract["arms"] == ["explicit"]
    assert contract["model"] == "glm-5.3-flash"
    assert contract["reasoningEffortPolicy"]["everyPhysicalRequest"] == "high"
    assert contract["limits"] == diagnostic.previous.LIMITS
    assert all(contract["limits"][key] is None for key in ("maxTokens", "maxModelCalls", "maxCostUsd", "wallSeconds", "requestTimeoutSeconds"))
    assert library.prompt_for_arm("PUBLIC ORIGINAL", "explicit") == diagnostic.previous.prompt_for_arm("PUBLIC ORIGINAL", "explicit")
    assert "OPAQUE SYNTHETIC" not in json.dumps(contract)


def test_preparation_selects_one_case_and_preserves_every_task_field(tmp_path):
    source, document = source_fixture(tmp_path)
    output = tmp_path / "output"
    before = {p: p.read_bytes() for p in source.rglob("*.json")}
    diagnostic.prepare(output, source, source / "original")
    selected, provenance = diagnostic.prepared_document(output)
    expected = {**document, "cases": [c for c in document["cases"] if c["id"] == diagnostic.CASE]}
    assert selected == expected
    assert provenance["selectedCases"] == [diagnostic.CASE]
    assert set(provenance["baselineFilesSha256"]) == {diagnostic.CASE}
    assert set(provenance["validationLayers"]) == {diagnostic.CASE}
    assert "OPAQUE SYNTHETIC" not in json.dumps(provenance)
    assert all(p.read_bytes() == contents for p, contents in before.items())
    with pytest.raises(ValueError, match="empty"):
        diagnostic.prepare(output, source, source / "original")


def test_duplicate_case_identity_rejected_without_writing(tmp_path):
    source, document = source_fixture(tmp_path)
    document["cases"].append(deepcopy(next(c for c in document["cases"] if c["id"] == diagnostic.CASE)))
    diagnostic.shared.write_json(source / "private/tasks.json", document)
    with pytest.raises(ValueError, match="exactly one"):
        diagnostic.prepare(tmp_path / "output", source, source / "original")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("path", ["private/tasks.json", "public-cases.json", "manifest.json"])
def test_source_drift_blocks_paid_contract(tmp_path, path):
    source, _ = source_fixture(tmp_path)
    output = tmp_path / "output"
    diagnostic.prepare(output, source, source / "original")
    (source / path).write_text("{}")
    with pytest.raises(ValueError, match="source"):
        diagnostic.prepared_document(output)


@pytest.mark.parametrize("path", ["tasks.json", "public-cases.json", "analysis-plan.md"])
def test_prepared_drift_is_rejected(tmp_path, path):
    source, _ = source_fixture(tmp_path)
    output = tmp_path / "output"
    diagnostic.prepare(output, source, source / "original")
    (output / path).write_text("{}")
    with pytest.raises(ValueError):
        diagnostic.prepared_document(output)


def test_old_runner_module_globals_are_unchanged(tmp_path):
    old = diagnostic.previous
    before = (old.ENTRY, old.CASES, old.ARMS, old.ANALYSIS_PLAN, old.schedule(), old.HARNESS_FILES)
    library = diagnostic._library()
    library.runner(tmp_path)
    assert (old.ENTRY, old.CASES, old.ARMS, old.ANALYSIS_PLAN, old.schedule(), old.HARNESS_FILES) == before
    assert library.ENTRY == diagnostic.ENTRY
    assert library.HARNESS_FILES[-1] == diagnostic.ENTRY.name


def test_missing_production_capability_cannot_start_model(monkeypatch):
    from minicode.executor_verification import ExecutorCheckCapability
    monkeypatch.setattr(ExecutorCheckCapability, "ensure_completion_checks", None)
    with pytest.raises(RuntimeError, match="capability is missing"):
        diagnostic.require_completion_integration()


def test_unintegrated_switch_still_blocks_paid_execution(monkeypatch):
    monkeypatch.setattr(diagnostic, "PRODUCTION_COMPLETION_INTEGRATED", False)
    with pytest.raises(RuntimeError, match="integration pending"):
        diagnostic.require_completion_integration()


def test_actual_production_mechanism_contract_binds_both_entrypoints_and_source_hashes():
    contract = diagnostic.completion_mechanism_contract()
    assert contract["version"] == diagnostic.COMPLETION_MECHANISM_VERSION
    assert contract["finalEntrypoints"] == 2
    assert contract["entrypointSha256"] == diagnostic.shared.sha(diagnostic.ENTRY.read_bytes())
    assert set(contract["sourceSha256"]) == set(diagnostic.MECHANISM_FILES)
    assert all(contract["sourceSha256"][name] == diagnostic.shared.sha((ROOT / name).read_bytes())
               for name in diagnostic.MECHANISM_FILES)


def test_archive_copies_real_review_persistence_without_interpreting_or_changing_it(tmp_path):
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.implementation_execution import ImplementationSpec
    review = ImplementationCompletionReview(ImplementationSpec(("app.py",), (), "Original public objective"))
    original_method = ImplementationCompletionReview._persist
    with diagnostic.CompletionEvidenceArchive(tmp_path / "trial"):
        source = Path(review._persist("review", {"opaque": "PRIVATE_SOURCE_BODY", "status": "approved"}))
        original_bytes = source.read_bytes()
        # Persistence is observation only; the real production return path and
        # original bytes remain unchanged, and no new model call is made.
        assert source.exists()
    assert ImplementationCompletionReview._persist is original_method
    archive = diagnostic.shared.read_json(tmp_path / "trial/completion-review-archive.json")
    assert archive["archiveComplete"] is True
    assert archive["reviewDirectories"] == 1 and len(archive["files"]) == 1
    row = archive["files"][0]
    assert (tmp_path / "trial" / row["file"]).read_bytes() == original_bytes
    assert row["sha256"] == diagnostic.shared.sha(original_bytes)
    assert "PRIVATE_SOURCE_BODY" not in json.dumps(archive)
    assert source.read_bytes() == original_bytes


def test_archival_failure_is_recorded_without_replacing_production_persist(tmp_path, monkeypatch):
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.implementation_execution import ImplementationSpec
    review = ImplementationCompletionReview(ImplementationSpec(("app.py",), (), "Public requirement"))
    monkeypatch.setattr(diagnostic.shutil, "copyfile", lambda *_a, **_k: (_ for _ in ()).throw(OSError("test")))
    with diagnostic.CompletionEvidenceArchive(tmp_path / "trial"):
        source = Path(review._persist("review", {"original": True}))
    assert source.is_file()
    archive = diagnostic.shared.read_json(tmp_path / "trial/completion-review-archive.json")
    assert archive["archiveComplete"] is False and archive["errors"] == ["OSError"]


def test_completion_summary_selects_safe_same_owner_repair_events_only(tmp_path):
    trial = diagnostic.schedule()[0]["id"]
    root = tmp_path / "private" / trial
    events_path = root / "journal/dashboard/workspaces/ws_fixture/runs/run_fixture/events.ndjson"
    events_path.parent.mkdir(parents=True)
    owner = "implementation_" + "a" * 32
    rows = [
        {"type": "model.completed", "payload": {"content": "SECRET_MODEL_PROSE"}},
        {"type": "implementation.completion_decided", "sequence": 2, "step": 1,
         "payload": {"executorId": owner, "attempt": 1, "status": "changes_required",
                     "continuesSameExecutor": True, "patchDigest": "sha256:" + "a" * 64,
                     "findings": ["SECRET_CODE_DETAIL"],
                     "requirementCount": {"arbitrary": "SECRET_MALFORMED_METADATA"}}},
        {"type": "tool.finished", "sequence": 3, "step": 2,
         "payload": {"toolName": "edit_file", "outcome": "success", "toolOutput": "PRIVATE_EDIT"}},
        {"type": "implementation.completion_decided", "sequence": 4, "step": 3,
         "payload": {"executorId": owner, "attempt": 2, "status": "approved",
                     "continuesSameExecutor": False, "patchDigest": "sha256:" + "b" * 64}},
    ]
    events_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    summary = diagnostic.completion_summary(tmp_path, trial)
    assert summary["decisionCount"] == 2 and summary["executorCount"] == 1
    assert summary["approvedFinalObserved"] is True
    assert summary["sameOwnerRepairPairs"] == [{"executorId": owner, "rejectedAttempt": 1,
        "acceptedAttempt": 2, "successfulWriteToolEvents": 1, "candidateDigestChanged": True}]
    assert "SECRET_" not in json.dumps(summary) and "PRIVATE_EDIT" not in json.dumps(summary)
    assert summary["decisions"][0]["requirementCount"] is None


@pytest.mark.parametrize("name", ["completion-review-archive.json", "completion-summary.json",
                                  "interrupted-transcript.json", "execution-evidence.json"])
def test_prior_completion_archive_cannot_be_overwritten_by_child_reentry(tmp_path, name):
    trial = diagnostic.schedule()[0]["id"]
    root = tmp_path / "private" / trial
    root.mkdir(parents=True)
    archive = root / name
    original = b'{"immutable":true}\n'
    archive.write_bytes(original)
    module = diagnostic._library().runner(tmp_path)
    with pytest.raises(ValueError, match="prior completion evidence"):
        module.execute_trial(tmp_path, trial)
    assert archive.read_bytes() == original
