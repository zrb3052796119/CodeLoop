from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from minicode.memory_hybrid_freeze import HYBRID_RUNTIME_BEHAVIOR_FILES
from scripts.memory_retrieval_production_baseline import (
    ACTIVE_BASELINE_VERSION,
    ACTIVE_PRODUCTION_RETRIEVAL_HASHES,
    BASELINE_V40_ID,
    BASELINE_V41_ID,
    EXPECTED_V41_ADDED_FILES,
    EXPECTED_V41_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    PINNED_RELEASE_JOIN_SHA256,
    PRODUCTION_RETRIEVAL_HASHES_V40,
    PRODUCTION_RETRIEVAL_HASHES_V41,
    RELEASE_JOIN_SCHEMA_VERSION,
    V41_ADDED_FILE_REASONS,
    V41_CHANGED_FILE_REASONS,
    V41_RELEASE_JOIN_COMPONENT_PATHS,
    V41_REQUIRED_HYBRID_CLOSURE_FILES,
    BaselineCertificationError,
    build_v41_candidate,
    build_v41_release_join,
    build_v41_release_join_from_files,
    canonical_manifest_text,
    compare_baselines,
    load_baseline_manifest,
    select_active_production_hashes,
    validate_manifest,
    validate_v41_release_join,
    verify_active_baseline,
    verify_manifest_version,
    verify_v41_release_join_receipt,
    write_v41_manifest,
    write_v41_release_join_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"

EXPECTED_CHANGED = {
    "minicode/memory_injector.py",
    "minicode/memory_lexical_policy.py",
    "minicode/memory_pipeline.py",
    "minicode/memory_retrieval.py",
}
EXPECTED_ADDED = {
    "minicode/agent_budget.py",
    "minicode/api_retry.py",
    "minicode/config.py",
    "minicode/context_manager.py",
    "minicode/cost_tracker.py",
    "minicode/cybernetic_orchestrator.py",
    "minicode/domain_classifier.py",
    "minicode/embeddings.py",
    "minicode/env_file.py",
    "minicode/evaluation_one_shot.py",
    "minicode/memory_hybrid.py",
    "minicode/memory_hybrid_freeze.py",
    "minicode/memory_hybrid_promotion_contract.py",
    "minicode/memory_hybrid_release.py",
    "minicode/memory_hybrid_runtime.py",
    "minicode/memory_path_identity.py",
    "minicode/model_call_control.py",
    "minicode/model_registry.py",
    "minicode/openai_adapter.py",
    "minicode/reflection_evidence.py",
    "minicode/reflection_synthesis.py",
    "minicode/state.py",
    "minicode/tls.py",
    "minicode/types.py",
    "scripts/memory_hybrid_qwen_v2_evaluator.py",
    "scripts/memory_hybrid_resolver_pressure_evaluator.py",
}


def _copy_staged_tree(destination: Path) -> None:
    v40 = load_baseline_manifest("v40")
    for relative in sorted(set(v40["files"]) | EXPECTED_ADDED):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for number in range(1, 41):
        relative = Path(
            f"tests/fixtures/memory_retrieval_production_freeze/v{number}.json"
        )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def _write_release_components(destination: Path) -> None:
    for component, relative in V41_RELEASE_JOIN_COMPONENT_PATHS.items():
        if component == "manifest":
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"sealed-{component}\n".encode())


def test_v41_candidate_closes_the_exact_hybrid_runtime_delta() -> None:
    v40 = load_baseline_manifest("v40")
    candidate = build_v41_candidate()

    assert EXPECTED_V41_CHANGED_FILES == {
        *EXPECTED_CHANGED,
        "minicode/agent_loop.py",
        "minicode/agent_reflection.py",
        "minicode/agent_runtime.py",
        "minicode/context_compactor.py",
        "minicode/conversation.py",
        "minicode/file_review.py",
        "minicode/main.py",
        "minicode/mcp.py",
        "minicode/memory.py",
        "minicode/memory_curator_agent.py",
        "minicode/permissions.py",
        "minicode/run_events.py",
        "minicode/run_journal.py",
        "minicode/session.py",
        "minicode/tooling.py",
        "minicode/tools/__init__.py",
        "minicode/tools/http_utils.py",
        "minicode/tools/network_safety.py",
        "minicode/tools/run_command.py",
        "minicode/tools/task.py",
        "minicode/tui/input_handler.py",
    }
    assert EXPECTED_V41_ADDED_FILES == EXPECTED_ADDED
    assert len(EXPECTED_V41_CHANGED_FILES) == 25
    assert len(EXPECTED_ADDED) == 26
    assert V41_REQUIRED_HYBRID_CLOSURE_FILES == (
        EXPECTED_V41_CHANGED_FILES | EXPECTED_V41_ADDED_FILES
    )
    assert candidate["baselineId"] == BASELINE_V41_ID
    assert candidate["parentBaselineId"] == BASELINE_V40_ID
    assert compare_baselines(v40, candidate) == {
        "changedFiles": sorted(EXPECTED_V41_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V41_ADDED_FILES),
        "removedFiles": [],
    }
    validate_manifest(candidate, version="v41")
    assert set(HYBRID_RUNTIME_BEHAVIOR_FILES) <= set(candidate["files"])


def test_v41_reasons_separate_behavior_transport_and_evaluator() -> None:
    assert set(V41_CHANGED_FILE_REASONS) == EXPECTED_V41_CHANGED_FILES
    assert (
        V41_CHANGED_FILE_REASONS["minicode/mcp.py"]
        == "skill_routing_semantic_authority"
    )
    assert (
        V41_CHANGED_FILE_REASONS["minicode/memory_curator_agent.py"]
        == "persistent_memory_lifecycle_authority"
    )
    assert (
        V41_CHANGED_FILE_REASONS["minicode/run_events.py"]
        == "persistent_memory_lifecycle_authority"
    )
    assert set(V41_CHANGED_FILE_REASONS.values()) == {
        "hybrid_memory_behavior_closure",
        "multi_agent_workflow_authority",
        "persistent_memory_lifecycle_authority",
        "production_source_state_reconciliation",
        "skill_routing_semantic_authority",
    }
    assert (
        V41_ADDED_FILE_REASONS["scripts/memory_hybrid_qwen_v2_evaluator.py"]
        == "hybrid_memory_promotion_evaluator"
    )
    assert (
        V41_ADDED_FILE_REASONS[
            "scripts/memory_hybrid_resolver_pressure_evaluator.py"
        ]
        == "hybrid_memory_promotion_evaluator"
    )
    assert {
        path
        for path, reason in V41_ADDED_FILE_REASONS.items()
        if reason == "hybrid_memory_behavior_closure"
    } == {
        "minicode/domain_classifier.py",
        "minicode/embeddings.py",
        "minicode/memory_hybrid.py",
        "minicode/memory_hybrid_runtime.py",
        "minicode/memory_path_identity.py",
        "minicode/reflection_evidence.py",
        "minicode/reflection_synthesis.py",
    }
    assert {
        path
        for path, reason in V41_ADDED_FILE_REASONS.items()
        if reason == "hybrid_memory_release_trust_anchor"
    } == {
        "minicode/memory_hybrid_freeze.py",
        "minicode/memory_hybrid_promotion_contract.py",
        "minicode/memory_hybrid_release.py",
    }
    promotion_files = {
        "minicode/evaluation_one_shot.py",
        "scripts/memory_hybrid_qwen_v2_evaluator.py",
        "scripts/memory_hybrid_resolver_pressure_evaluator.py",
    }
    assert {
        path
        for path, reason in V41_ADDED_FILE_REASONS.items()
        if reason == "hybrid_memory_promotion_evaluator"
    } == promotion_files
    assert {
        path
        for path, reason in V41_ADDED_FILE_REASONS.items()
        if reason == "hybrid_memory_runtime_transport_closure"
    } == EXPECTED_ADDED - {
        "minicode/domain_classifier.py",
        "minicode/embeddings.py",
        "minicode/memory_hybrid.py",
        "minicode/memory_hybrid_runtime.py",
        "minicode/memory_path_identity.py",
        "minicode/reflection_evidence.py",
        "minicode/reflection_synthesis.py",
        "minicode/memory_hybrid_freeze.py",
        "minicode/memory_hybrid_promotion_contract.py",
        "minicode/memory_hybrid_release.py",
        *promotion_files,
    }


def test_staged_v41_candidate_is_read_only_and_v40_stays_active(
    tmp_path: Path,
) -> None:
    _copy_staged_tree(tmp_path)

    candidate = build_v41_candidate(project_root=tmp_path)

    assert candidate["baselineId"] == BASELINE_V41_ID
    assert not (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v41.json"
    ).exists()
    assert "v41" not in PINNED_MANIFEST_SHA256
    assert "v41" not in PINNED_RELEASE_JOIN_SHA256
    assert ACTIVE_BASELINE_VERSION == "v40"
    assert ACTIVE_PRODUCTION_RETRIEVAL_HASHES == PRODUCTION_RETRIEVAL_HASHES_V40
    assert PRODUCTION_RETRIEVAL_HASHES_V41 == {}
    assert verify_active_baseline()["activeBaselineId"] == BASELINE_V40_ID
    # A complete staged candidate is not a promoted source baseline.
    assert verify_active_baseline(project_root=tmp_path)["matches"] is False
    assert select_active_production_hashes("v41", project_root=tmp_path) == {}
    assert verify_manifest_version("v41", project_root=tmp_path) == {
        "version": "v41",
        "baselineId": BASELINE_V41_ID,
        "manifestIntegrity": False,
        "lineageMatches": False,
        "matches": False,
    }


@pytest.mark.parametrize(
    "relative",
    ["minicode/conversation_turn_store.py", "minicode/memory_store.py"],
)
def test_v41_candidate_rejects_an_unlisted_v40_change(
    tmp_path: Path, relative: str,
) -> None:
    _copy_staged_tree(tmp_path)
    target = tmp_path / relative
    target.write_bytes(target.read_bytes() + b"\n# unexpected v41 change\n")

    with pytest.raises(
        BaselineCertificationError,
        match="unexpected v40 source differences",
    ):
        build_v41_candidate(project_root=tmp_path)


def test_v41_candidate_rejects_a_missing_runtime_dependency(tmp_path: Path) -> None:
    _copy_staged_tree(tmp_path)
    (tmp_path / "minicode/memory_hybrid_runtime.py").unlink()

    with pytest.raises(
        BaselineCertificationError,
        match="required v41 source files missing",
    ):
        build_v41_candidate(project_root=tmp_path)


def test_v41_print_is_deterministic_without_machine_metadata(tmp_path: Path) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("41", "4101")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v41"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert outputs[0] == canonical_manifest_text(build_v41_candidate()).encode()
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]


def test_v41_writer_exclusively_creates_one_unpinned_candidate(
    tmp_path: Path,
) -> None:
    _copy_staged_tree(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v41.json"
    )

    returned = write_v41_manifest(project_root=tmp_path)
    original_bytes = target.read_bytes()

    assert returned == target
    assert original_bytes == canonical_manifest_text(
        build_v41_candidate(project_root=tmp_path)
    ).encode("utf-8")
    with pytest.raises(
        BaselineCertificationError,
        match="existing v41 manifest is unpinned",
    ):
        write_v41_manifest(project_root=tmp_path)
    assert target.read_bytes() == original_bytes


def test_v41_writer_returns_an_existing_candidate_only_after_full_pin_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_staged_tree(tmp_path)
    target = write_v41_manifest(project_root=tmp_path)
    original_bytes = target.read_bytes()
    original_mtime = target.stat().st_mtime_ns
    monkeypatch.setitem(
        PINNED_MANIFEST_SHA256,
        "v41",
        hashlib.sha256(original_bytes).hexdigest(),
    )

    returned = write_v41_manifest(project_root=tmp_path)

    assert returned == target
    assert verify_manifest_version("v41", project_root=tmp_path)["matches"] is True
    assert target.read_bytes() == original_bytes
    assert target.stat().st_mtime_ns == original_mtime


def test_v41_writer_never_repairs_a_tampered_pinned_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_staged_tree(tmp_path)
    target = write_v41_manifest(project_root=tmp_path)
    pinned_digest = hashlib.sha256(target.read_bytes()).hexdigest()
    monkeypatch.setitem(PINNED_MANIFEST_SHA256, "v41", pinned_digest)
    tampered = target.read_bytes() + b"\n"
    target.write_bytes(tampered)

    with pytest.raises(
        BaselineCertificationError,
        match="historical v41 baseline is not valid",
    ):
        write_v41_manifest(project_root=tmp_path)

    assert target.read_bytes() == tampered


def test_v41_writer_does_not_create_a_missing_manifest_after_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_staged_tree(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v41.json"
    )
    monkeypatch.setitem(PINNED_MANIFEST_SHA256, "v41", "1" * 64)

    with pytest.raises(
        BaselineCertificationError,
        match="historical v41 baseline is not valid",
    ):
        write_v41_manifest(project_root=tmp_path)

    assert not target.exists()


def test_v41_writer_removes_final_target_when_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.memory_retrieval_production_baseline as baseline

    _copy_staged_tree(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v41.json"
    )

    def fail_directory_fsync(_directory: Path) -> None:
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(baseline, "_fsync_directory", fail_directory_fsync)

    with pytest.raises(BaselineCertificationError, match="publication failed"):
        write_v41_manifest(project_root=tmp_path)

    assert not target.exists()
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_v41_cli_write_action_is_wired_without_touching_the_real_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.memory_retrieval_production_baseline as baseline

    called = False

    def fake_write_v41_manifest() -> Path:
        nonlocal called
        called = True
        return Path("unused-v41.json")

    monkeypatch.setattr(baseline, "write_v41_manifest", fake_write_v41_manifest)

    assert baseline.main(["--write-v41"]) == 0
    assert called is True


def test_v41_release_join_binds_only_the_four_required_digests() -> None:
    payload = build_v41_release_join(
        manifest_sha256="1" * 64,
        production_evidence_sha256="2" * 64,
        promotion_report_sha256="3" * 64,
        holdout_sha256="4" * 64,
    )

    assert payload == {
        "schemaVersion": RELEASE_JOIN_SCHEMA_VERSION,
        "baselineId": BASELINE_V41_ID,
        "artifacts": {
            "manifest": {
                "path": V41_RELEASE_JOIN_COMPONENT_PATHS["manifest"].as_posix(),
                "sha256": "1" * 64,
            },
            "productionEvidence": {
                "path": V41_RELEASE_JOIN_COMPONENT_PATHS[
                    "productionEvidence"
                ].as_posix(),
                "sha256": "2" * 64,
            },
            "promotionReport": {
                "path": V41_RELEASE_JOIN_COMPONENT_PATHS[
                    "promotionReport"
                ].as_posix(),
                "sha256": "3" * 64,
            },
            "holdout": {
                "path": V41_RELEASE_JOIN_COMPONENT_PATHS["holdout"].as_posix(),
                "sha256": "4" * 64,
            },
        },
    }
    validate_v41_release_join(json.loads(json.dumps(payload)))


@pytest.mark.parametrize(
    "mutation",
    [
        {"artifacts": {}},
        {"baselineId": BASELINE_V40_ID},
        {"extra": "not-closed"},
    ],
)
def test_v41_release_join_rejects_malformed_or_open_payloads(
    mutation: dict[str, str],
) -> None:
    payload = build_v41_release_join(
        manifest_sha256="1" * 64,
        production_evidence_sha256="2" * 64,
        promotion_report_sha256="3" * 64,
        holdout_sha256="4" * 64,
    )
    payload.update(mutation)

    with pytest.raises(BaselineCertificationError):
        validate_v41_release_join(payload)


def test_v41_release_join_rejects_a_retargeted_or_malformed_binding() -> None:
    payload = build_v41_release_join(
        manifest_sha256="1" * 64,
        production_evidence_sha256="2" * 64,
        promotion_report_sha256="3" * 64,
        holdout_sha256="4" * 64,
    )
    payload["artifacts"]["holdout"]["path"] = "artifacts/decoy.json"
    with pytest.raises(BaselineCertificationError, match="path is invalid"):
        validate_v41_release_join(payload)

    payload = build_v41_release_join(
        manifest_sha256="1" * 64,
        production_evidence_sha256="2" * 64,
        promotion_report_sha256="3" * 64,
        holdout_sha256="4" * 64,
    )
    payload["artifacts"]["promotionReport"]["sha256"] = "short"
    with pytest.raises(BaselineCertificationError, match="lowercase SHA-256"):
        validate_v41_release_join(payload)


def test_v41_release_join_verifies_real_files_and_gates_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_staged_tree(tmp_path)
    manifest = write_v41_manifest(project_root=tmp_path)
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    monkeypatch.setitem(PINNED_MANIFEST_SHA256, "v41", manifest_digest)
    _write_release_components(tmp_path)

    expected = build_v41_release_join_from_files(project_root=tmp_path)
    receipt = write_v41_release_join_receipt(project_root=tmp_path)

    assert json.loads(receipt.read_text(encoding="utf-8")) == expected
    assert verify_v41_release_join_receipt(project_root=tmp_path)["matches"] is False
    assert select_active_production_hashes("v41", project_root=tmp_path) == {}

    receipt_digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    monkeypatch.setitem(PINNED_RELEASE_JOIN_SHA256, "v41", receipt_digest)

    verification = verify_v41_release_join_receipt(project_root=tmp_path)
    assert verification["matches"] is True
    assert all(verification["artifactIntegrity"].values())
    assert select_active_production_hashes(
        "v41", project_root=tmp_path
    ) == load_baseline_manifest("v41", project_root=tmp_path)["files"]


def test_release_join_writer_does_not_create_a_missing_receipt_after_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.memory_retrieval_production_baseline as baseline

    _copy_staged_tree(tmp_path)
    manifest = write_v41_manifest(project_root=tmp_path)
    monkeypatch.setitem(
        PINNED_MANIFEST_SHA256,
        "v41",
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    _write_release_components(tmp_path)
    receipt = tmp_path / baseline.V41_RELEASE_JOIN_RECEIPT_PATH
    monkeypatch.setitem(PINNED_RELEASE_JOIN_SHA256, "v41", "1" * 64)

    with pytest.raises(
        BaselineCertificationError,
        match="pinned v41 release join receipt is unavailable or invalid",
    ):
        write_v41_release_join_receipt(project_root=tmp_path)

    assert not receipt.exists()


def test_active_v40_hash_selection_never_falls_back_after_manifest_tampering(
    tmp_path: Path,
) -> None:
    _copy_staged_tree(tmp_path)
    manifest = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v40.json"
    )
    manifest.write_bytes(manifest.read_bytes() + b"\n")

    assert select_active_production_hashes("v40", project_root=tmp_path) == {}


def test_v41_release_join_fails_closed_after_bound_artifact_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_staged_tree(tmp_path)
    manifest = write_v41_manifest(project_root=tmp_path)
    monkeypatch.setitem(
        PINNED_MANIFEST_SHA256,
        "v41",
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    _write_release_components(tmp_path)
    receipt = write_v41_release_join_receipt(project_root=tmp_path)
    monkeypatch.setitem(
        PINNED_RELEASE_JOIN_SHA256,
        "v41",
        hashlib.sha256(receipt.read_bytes()).hexdigest(),
    )
    evidence = tmp_path / V41_RELEASE_JOIN_COMPONENT_PATHS["productionEvidence"]
    evidence.write_bytes(evidence.read_bytes() + b"tampered")

    verification = verify_v41_release_join_receipt(project_root=tmp_path)

    assert verification["matches"] is False
    assert verification["artifactIntegrity"]["productionEvidence"] is False
    assert select_active_production_hashes("v41", project_root=tmp_path) == {}


def test_verify_active_v41_requires_release_join_even_after_manifest_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.memory_retrieval_production_baseline as baseline

    _copy_staged_tree(tmp_path)
    manifest = write_v41_manifest(project_root=tmp_path)
    monkeypatch.setitem(
        PINNED_MANIFEST_SHA256,
        "v41",
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(baseline, "ACTIVE_BASELINE_VERSION", "v41")

    result = verify_active_baseline(project_root=tmp_path)

    assert result["activeBaselineId"] == BASELINE_V41_ID
    assert result["manifestIntegrity"]["v41"] is True
    assert result["releaseJoin"]["matches"] is False
    assert result["matches"] is False


def test_v1_through_v40_manifest_bytes_remain_pinned() -> None:
    for number in range(1, 41):
        version = f"v{number}"
        assert (
            hashlib.sha256((MANIFEST_ROOT / f"{version}.json").read_bytes()).hexdigest()
            == PINNED_MANIFEST_SHA256[version]
        )
