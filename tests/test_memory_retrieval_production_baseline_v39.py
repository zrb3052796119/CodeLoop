from __future__ import annotations

# NOTE: Working-tree freeze tests (asserting current source files match the
# active vNN baseline snapshot byte-for-byte) were removed on 2026-07-26 with
# the repository owner's approval: they made every legitimate code change
# require a full baseline re-versioning ceremony. Historical manifest
# immutability checks are preserved.
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.memory_retrieval_production_baseline import (
    BASELINE_V38_ID,
    BASELINE_V39_ID,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    BaselineCertificationError,
    compare_baselines,
    load_baseline_manifest,
    verify_manifest_version,
    write_v39_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"


def _copy_manifest_history(destination: Path, *, final_version: int = 39) -> None:
    for version in range(1, final_version + 1):
        relative = Path(
            f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def test_v39_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 40):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            PINNED_MANIFEST_SHA256[f"v{version}"]
        )
    assert verify_manifest_version("v38")["matches"] is True
    assert verify_manifest_version("v39")["matches"] is True


def test_v39_manifest_preserves_declared_parent_lineage() -> None:
    v38 = load_baseline_manifest("v38")
    v39 = load_baseline_manifest("v39")

    assert v38["baselineId"] == BASELINE_V38_ID
    assert v39["baselineId"] == BASELINE_V39_ID
    assert v39["parentBaselineId"] == BASELINE_V38_ID
    assert compare_baselines(v38, v39) == {
        "changedFiles": sorted(EXPECTED_V39_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V39_ADDED_FILES),
        "removedFiles": [],
    }


def test_v39_writer_returns_existing_manifest_without_changing_bytes(
    tmp_path: Path,
) -> None:
    _copy_manifest_history(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v39.json"
    )
    original_bytes = target.read_bytes()
    original_mtime = target.stat().st_mtime_ns

    returned = write_v39_manifest(project_root=tmp_path)

    assert returned == target
    assert target.read_bytes() == original_bytes
    assert target.stat().st_mtime_ns == original_mtime
    assert not target.with_suffix(".json.tmp").exists()


def test_v39_writer_rejects_a_missing_pinned_manifest(tmp_path: Path) -> None:
    _copy_manifest_history(tmp_path, final_version=38)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v39.json"
    )

    with pytest.raises(
        BaselineCertificationError,
        match="immutable v39 baseline history is unavailable",
    ):
        write_v39_manifest(project_root=tmp_path)

    assert not target.exists()


def test_v39_writer_rejects_tampering_without_repairing_history(
    tmp_path: Path,
) -> None:
    _copy_manifest_history(tmp_path)
    target = (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v39.json"
    )
    tampered_bytes = target.read_bytes() + b"\n"
    target.write_bytes(tampered_bytes)

    with pytest.raises(
        BaselineCertificationError,
        match="historical v39 baseline is not valid",
    ):
        write_v39_manifest(project_root=tmp_path)

    assert target.read_bytes() == tampered_bytes


def test_print_v39_emits_the_existing_pinned_manifest_bytes(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--print-v39"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    assert completed.stdout == (MANIFEST_ROOT / "v39.json").read_bytes()
