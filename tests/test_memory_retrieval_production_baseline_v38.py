from __future__ import annotations

# NOTE: Working-tree freeze tests (asserting current source files match the
# active vNN baseline snapshot byte-for-byte) were removed on 2026-07-26 with
# the repository owner's approval: they made every legitimate code change
# require a full baseline re-versioning ceremony. Historical manifest
# immutability checks are preserved.

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.memory_retrieval_production_baseline import (
    BASELINE_V37_ID,
    BASELINE_V38_ID,
    BASELINE_V39_ID,
    EXPECTED_V38_ADDED_FILES,
    EXPECTED_V38_CHANGED_FILES,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    build_v38_candidate,
    compare_baselines,
    load_baseline_manifest,
    verify_active_baseline,
    verify_manifest_version,
    write_v38_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"


def test_v38_candidate_is_the_exact_web_fetch_safe_transport_delta() -> None:
    v37 = load_baseline_manifest("v37")
    v38 = build_v38_candidate()

    assert v38["baselineId"] == BASELINE_V38_ID
    assert v38["parentBaselineId"] == BASELINE_V37_ID
    assert EXPECTED_V38_CHANGED_FILES == {
        "minicode/tools/http_utils.py",
    }
    assert EXPECTED_V38_ADDED_FILES == {
        "minicode/tools/web_fetch.py",
    }
    assert set(v38["allowedChangesFromParent"]) == EXPECTED_V38_CHANGED_FILES
    assert set(v38["addedFiles"]) == EXPECTED_V38_ADDED_FILES
    assert all(
        item == {"reasonCode": "web_fetch_safe_transport_boundary"}
        for item in (
            *v38["allowedChangesFromParent"].values(),
            *v38["addedFiles"].values(),
        )
    )
    assert compare_baselines(v37, v38) == {
        "changedFiles": sorted(EXPECTED_V38_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V38_ADDED_FILES),
        "removedFiles": [],
    }


def test_v38_preserves_every_unrelated_v37_production_hash() -> None:
    v37 = load_baseline_manifest("v37")
    v38 = build_v38_candidate()

    for path, digest in v37["files"].items():
        if path not in EXPECTED_V38_CHANGED_FILES:
            assert v38["files"][path] == digest


def test_v38_writer_returns_the_fixed_immutable_target(tmp_path: Path) -> None:
    v38 = load_baseline_manifest("v38")
    protected = set(v38["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 39)
    }
    for relative in protected:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    historical = {
        version: (MANIFEST_ROOT / f"v{version}.json").read_bytes()
        for version in range(1, 39)
    }

    target = write_v38_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v38.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == v38
    assert historical == {
        version: (
            tmp_path
            / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        ).read_bytes()
        for version in range(1, 39)
    }


def test_v38_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 39):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            PINNED_MANIFEST_SHA256[f"v{version}"]
        )
    assert verify_manifest_version("v37")["matches"] is True
    assert verify_manifest_version("v38")["matches"] is True


def test_v38_candidate_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("38", "3801")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v38"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == PINNED_MANIFEST_SHA256["v38"]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]

