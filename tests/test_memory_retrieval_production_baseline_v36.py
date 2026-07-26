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
    BASELINE_V35_ID,
    BASELINE_V36_ID,
    BASELINE_V39_ID,
    EXPECTED_V36_ADDED_FILES,
    EXPECTED_V36_CHANGED_FILES,
    EXPECTED_V37_ADDED_FILES,
    EXPECTED_V37_CHANGED_FILES,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    build_v36_candidate,
    compare_baselines,
    load_baseline_manifest,
    verify_active_baseline,
    verify_manifest_version,
    write_v36_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"


def test_v36_candidate_is_the_exact_http_request_safety_delta() -> None:
    v35 = load_baseline_manifest("v35")
    v36 = build_v36_candidate()

    assert v36["baselineId"] == BASELINE_V36_ID
    assert v36["parentBaselineId"] == BASELINE_V35_ID
    assert EXPECTED_V36_CHANGED_FILES == {
        "minicode/permissions.py",
        "minicode/permission_approval.py",
        "minicode/permission_event_contract.py",
        "minicode/tooling.py",
        "minicode/web/static/assets/app.js",
    }
    assert EXPECTED_V36_ADDED_FILES == {
        "minicode/tools/http_utils.py",
        "minicode/tools/network_safety.py",
    }
    assert set(v36["allowedChangesFromParent"]) == EXPECTED_V36_CHANGED_FILES
    assert set(v36["addedFiles"]) == EXPECTED_V36_ADDED_FILES
    assert all(
        item == {"reasonCode": "http_request_network_safety"}
        for item in (
            *v36["allowedChangesFromParent"].values(),
            *v36["addedFiles"].values(),
        )
    )
    assert compare_baselines(v35, v36) == {
        "changedFiles": sorted(EXPECTED_V36_CHANGED_FILES),
        "addedFiles": sorted(EXPECTED_V36_ADDED_FILES),
        "removedFiles": [],
    }


def test_v36_preserves_every_unrelated_v35_production_hash() -> None:
    v35 = load_baseline_manifest("v35")
    v36 = build_v36_candidate()

    for path, digest in v35["files"].items():
        if path not in EXPECTED_V36_CHANGED_FILES:
            assert v36["files"][path] == digest


def test_v36_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 37):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            PINNED_MANIFEST_SHA256[f"v{version}"]
        )
    assert verify_manifest_version("v35")["matches"] is True
    assert verify_manifest_version("v36")["matches"] is True


def test_v36_candidate_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("36", "3601")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v36"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == PINNED_MANIFEST_SHA256["v36"]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]


def test_v36_writer_returns_the_fixed_immutable_target(tmp_path: Path) -> None:
    v35 = load_baseline_manifest("v35")
    protected = set(v35["files"]) | set(EXPECTED_V36_ADDED_FILES) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 37)
    }
    for relative in protected:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    historical = {
        version: (MANIFEST_ROOT / f"v{version}.json").read_bytes()
        for version in range(1, 37)
    }

    target = write_v36_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v36.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v36_candidate(
        project_root=tmp_path
    )
    assert historical == {
        version: (
            tmp_path
            / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        ).read_bytes()
        for version in range(1, 37)
    }
