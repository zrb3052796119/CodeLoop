from __future__ import annotations

# v40 is immutable historical evidence. Assertions that the live source tree
# matches the active baseline belong to the succeeding active-version suite.
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.memory_retrieval_production_baseline import (
    BASELINE_V39_ID,
    BASELINE_V40_ID,
    EXPECTED_V40_ADDED_FILES,
    EXPECTED_V40_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    V40_ADDED_FILE_REASONS,
    V40_CHANGED_FILE_REASONS,
    BaselineCertificationError,
    build_v40_candidate,
    compare_baselines,
    load_baseline_manifest,
    verify_manifest_version,
    write_v40_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"

EXPECTED_CHANGED_BY_REASON = {
    "persistent_memory_lifecycle_authority": {
        "minicode/agent_loop.py",
        "minicode/agent_reflection.py",
        "minicode/conversation.py",
        "minicode/conversation_turn_store.py",
        "minicode/memory.py",
        "minicode/memory_approval.py",
        "minicode/memory_curator_agent.py",
        "minicode/memory_pipeline.py",
        "minicode/memory_store.py",
        "minicode/project_memory_deletion.py",
        "minicode/run_events.py",
        "minicode/run_journal.py",
        "minicode/run_lifecycle.py",
        "minicode/session.py",
        "minicode/storage_health.py",
        "minicode/web/read_model.py",
    },
    "persistent_memory_retrieval_semantics": {
        "minicode/memory_candidate_consolidation.py",
        "minicode/memory_injector.py",
        "minicode/memory_retrieval.py",
    },
    "context_compaction_fidelity_authority": {
        "minicode/context_compactor.py",
    },
    "skill_routing_semantic_authority": {
        "minicode/agent_runtime.py",
        "minicode/main.py",
        "minicode/tui/input_handler.py",
    },
    "multi_agent_workflow_authority": {
        "minicode/headless.py",
        "minicode/permissions.py",
        "minicode/tooling.py",
        "minicode/tools/task.py",
    },
    "client_surface_runtime_integration": {
        "minicode/web/static/assets/app.js",
    },
    "production_source_state_reconciliation": {
        "minicode/deletion_store.py",
        "minicode/gateway.py",
        "minicode/permission_approval.py",
        "minicode/tools/__init__.py",
        "minicode/tools/network_safety.py",
        "minicode/tools/run_command.py",
        "minicode/tools/search_providers.py",
        "minicode/tools/web_search.py",
        "minicode/workspace.py",
        "minicode/web/change_feed.py",
        "minicode/web/chat_http.py",
        "minicode/web/chat_stream.py",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/index.html",
    },
}
EXPECTED_ADDED = {
    "minicode/advisory_lock.py",
    "minicode/curator_schedule.py",
    "minicode/memory_approval_interpretation.py",
    "minicode/memory_audit_contract.py",
    "minicode/memory_authority_io.py",
    "minicode/memory_authority_publisher.py",
    "minicode/memory_blob_store.py",
    "minicode/memory_lexical_policy.py",
    "minicode/memory_provenance.py",
    "minicode/memory_write_contract.py",
    "minicode/project_facts.py",
}


def _copy_certification_tree(destination: Path, *, include_v40: bool = True) -> None:
    manifest = load_baseline_manifest("v40")
    for relative in manifest["files"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    final_version = 40 if include_v40 else 39
    for version in range(1, final_version + 1):
        relative = Path(
            f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def test_v40_candidate_is_the_exact_multi_track_release_delta() -> None:
    v39 = load_baseline_manifest("v39")
    v40 = build_v40_candidate()
    expected_changed = set().union(*EXPECTED_CHANGED_BY_REASON.values())

    assert v40["baselineId"] == BASELINE_V40_ID
    assert v40["parentBaselineId"] == BASELINE_V39_ID
    assert len(expected_changed) == 42
    assert expected_changed == EXPECTED_V40_CHANGED_FILES
    assert EXPECTED_ADDED == EXPECTED_V40_ADDED_FILES
    assert compare_baselines(v39, v40) == {
        "changedFiles": sorted(expected_changed),
        "addedFiles": sorted(EXPECTED_ADDED),
        "removedFiles": [],
    }
    assert v40 == load_baseline_manifest("v40")
    for reason, paths in EXPECTED_CHANGED_BY_REASON.items():
        assert {
            path for path, value in V40_CHANGED_FILE_REASONS.items() if value == reason
        } == paths
    assert V40_ADDED_FILE_REASONS == {
        path: (
            "persistent_memory_retrieval_semantics"
            if path == "minicode/memory_lexical_policy.py"
            else "persistent_memory_lifecycle_authority"
        )
        for path in EXPECTED_ADDED
    }


def test_v40_preserves_every_unrelated_v39_production_hash() -> None:
    v39 = load_baseline_manifest("v39")
    v40 = build_v40_candidate()

    for path, digest in v39["files"].items():
        if path not in EXPECTED_V40_CHANGED_FILES:
            assert v40["files"][path] == digest


def test_v40_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 41):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == (PINNED_MANIFEST_SHA256[f"v{version}"])
        )
    assert verify_manifest_version("v39")["matches"] is True
    assert verify_manifest_version("v40")["matches"] is True


def test_v40_writer_returns_existing_manifest_without_changing_bytes(
    tmp_path: Path,
) -> None:
    _copy_certification_tree(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v40.json"
    )
    original_bytes = target.read_bytes()
    original_mtime = target.stat().st_mtime_ns

    returned = write_v40_manifest(project_root=tmp_path)

    assert returned == target
    assert target.read_bytes() == original_bytes
    assert target.stat().st_mtime_ns == original_mtime
    assert not target.with_suffix(".json.tmp").exists()


def test_v40_writer_rejects_a_missing_pinned_manifest(tmp_path: Path) -> None:
    _copy_certification_tree(tmp_path, include_v40=False)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v40.json"
    )

    with pytest.raises(
        BaselineCertificationError,
        match="immutable v40 baseline history is unavailable",
    ):
        write_v40_manifest(project_root=tmp_path)

    assert not target.exists()


def test_v40_writer_rejects_tampering_without_repairing_history(
    tmp_path: Path,
) -> None:
    _copy_certification_tree(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v40.json"
    )
    tampered_bytes = target.read_bytes() + b"\n"
    target.write_bytes(tampered_bytes)

    with pytest.raises(
        BaselineCertificationError,
        match="historical v40 baseline is not valid",
    ):
        write_v40_manifest(project_root=tmp_path)

    assert target.read_bytes() == tampered_bytes


def test_v40_candidate_rejects_an_unlisted_parent_change(tmp_path: Path) -> None:
    _copy_certification_tree(tmp_path)
    relative = "minicode/mcp_current_state.py"
    target = tmp_path / relative
    target.write_bytes(target.read_bytes() + b"\n# unexpected parent change\n")

    with pytest.raises(
        BaselineCertificationError,
        match="unexpected v39 source differences",
    ):
        build_v40_candidate(project_root=tmp_path)


def test_v40_candidate_rejects_a_tampered_projection_authority(
    tmp_path: Path,
) -> None:
    _copy_certification_tree(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v40.json"
    )
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(
        BaselineCertificationError,
        match="historical baseline integrity changed",
    ):
        build_v40_candidate(project_root=tmp_path)


def test_v40_candidate_rejects_a_missing_added_authority_dependency(
    tmp_path: Path,
) -> None:
    _copy_certification_tree(tmp_path)
    (tmp_path / "minicode/memory_authority_publisher.py").unlink()

    with pytest.raises(
        BaselineCertificationError,
        match="required v40 source files missing",
    ):
        build_v40_candidate(project_root=tmp_path)


def test_print_v40_emits_existing_manifest_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("40", "4001")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v40"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert outputs[0] == (MANIFEST_ROOT / "v40.json").read_bytes()
    assert hashlib.sha256(outputs[0]).hexdigest() == PINNED_MANIFEST_SHA256["v40"]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]
    assert b"timestamp" not in outputs[0]
