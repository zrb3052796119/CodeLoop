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
    BASELINE_V33_ID,
    BASELINE_V34_ID,
    BASELINE_V39_ID,
    EXPECTED_V34_ADDED_FILES,
    EXPECTED_V34_CHANGED_FILES,
    EXPECTED_V35_ADDED_FILES,
    EXPECTED_V35_CHANGED_FILES,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    build_v34_candidate,
    compare_baselines,
    load_baseline_manifest,
    verify_active_baseline,
    verify_manifest_version,
    write_v34_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"


def test_v34_candidate_is_the_exact_visual_shell_delta() -> None:
    v33 = load_baseline_manifest("v33")
    v34 = build_v34_candidate()

    assert v34["baselineId"] == BASELINE_V34_ID
    assert v34["parentBaselineId"] == BASELINE_V33_ID
    assert EXPECTED_V34_CHANGED_FILES == {
        "minicode/web/static/index.html",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/assets/app.js",
    }
    assert EXPECTED_V34_ADDED_FILES == set()
    assert set(v34["allowedChangesFromParent"]) == EXPECTED_V34_CHANGED_FILES
    assert v34["addedFiles"] == {}
    assert all(
        item == {"reasonCode": "dashboard_waku_visual_shell"}
        for item in v34["allowedChangesFromParent"].values()
    )
    assert compare_baselines(v33, v34) == {
        "changedFiles": sorted(EXPECTED_V34_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }


def test_v34_preserves_cost_format_and_all_non_shell_production_hashes() -> None:
    v33 = load_baseline_manifest("v33")
    v34 = build_v34_candidate()

    for path, digest in v33["files"].items():
        if path not in EXPECTED_V34_CHANGED_FILES:
            assert v34["files"][path] == digest
    formatter = ROOT / "minicode/web/static/assets/cost-format.js"
    assert hashlib.sha256(formatter.read_bytes()).hexdigest() == (
        "194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916"
    )
    assert "minicode/web/static/assets/cost-format.js" not in EXPECTED_V34_CHANGED_FILES


def test_v34_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 35):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == PINNED_MANIFEST_SHA256[
            f"v{version}"
        ]
    assert verify_manifest_version("v33")["matches"] is True
    assert verify_manifest_version("v34")["matches"] is True


def test_v34_candidate_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("34", "3401")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v34"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == PINNED_MANIFEST_SHA256["v34"]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]


def test_v34_writer_returns_the_fixed_immutable_target(tmp_path: Path) -> None:
    v34 = load_baseline_manifest("v34")
    protected = set(v34["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 35)
    }
    for relative in protected:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    historical = {
        version: (MANIFEST_ROOT / f"v{version}.json").read_bytes()
        for version in range(1, 35)
    }

    target = write_v34_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v34.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v34_candidate(
        project_root=tmp_path
    )
    assert historical == {
        version: (
            tmp_path
            / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        ).read_bytes()
        for version in range(1, 35)
    }
