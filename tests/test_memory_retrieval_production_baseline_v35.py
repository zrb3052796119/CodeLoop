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
    BASELINE_V34_ID,
    BASELINE_V35_ID,
    BASELINE_V39_ID,
    EXPECTED_V35_ADDED_FILES,
    EXPECTED_V35_CHANGED_FILES,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    build_v35_candidate,
    compare_baselines,
    load_baseline_manifest,
    verify_active_baseline,
    verify_manifest_version,
    write_v35_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_memory_retrieval_production_baseline.py"
MANIFEST_ROOT = ROOT / "tests/fixtures/memory_retrieval_production_freeze"


def test_v35_candidate_is_the_exact_agent_observatory_core_page_delta() -> None:
    v34 = load_baseline_manifest("v34")
    v35 = build_v35_candidate()

    assert v35["baselineId"] == BASELINE_V35_ID
    assert v35["parentBaselineId"] == BASELINE_V34_ID
    assert EXPECTED_V35_CHANGED_FILES == {
        "minicode/web/static/index.html",
        "minicode/web/static/assets/styles.css",
        "minicode/web/static/assets/app.js",
    }
    assert EXPECTED_V35_ADDED_FILES == set()
    assert set(v35["allowedChangesFromParent"]) == EXPECTED_V35_CHANGED_FILES
    assert v35["addedFiles"] == {}
    assert all(
        item == {"reasonCode": "dashboard_agent_observatory_core_pages"}
        for item in v35["allowedChangesFromParent"].values()
    )
    assert compare_baselines(v34, v35) == {
        "changedFiles": sorted(EXPECTED_V35_CHANGED_FILES),
        "addedFiles": [],
        "removedFiles": [],
    }


def test_v35_preserves_cost_format_and_all_non_core_page_hashes() -> None:
    v34 = load_baseline_manifest("v34")
    v35 = build_v35_candidate()

    for path, digest in v34["files"].items():
        if path not in EXPECTED_V35_CHANGED_FILES:
            assert v35["files"][path] == digest
    assert (
        hashlib.sha256(
            (ROOT / "minicode/web/static/assets/cost-format.js").read_bytes()
        ).hexdigest()
        == "194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916"
    )


def test_v35_manifest_is_pinned_and_all_history_remains_immutable() -> None:
    for version in range(1, 36):
        path = MANIFEST_ROOT / f"v{version}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == PINNED_MANIFEST_SHA256[
            f"v{version}"
        ]
    assert verify_manifest_version("v34")["matches"] is True
    assert verify_manifest_version("v35")["matches"] is True


def test_v35_candidate_is_stable_across_cwd_home_and_hash_seed(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index, seed in enumerate(("35", "3501")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--print-v35"],
            cwd=cwd,
            env={"HOME": str(home), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == PINNED_MANIFEST_SHA256["v35"]
    assert b"/Users/" not in outputs[0]
    assert b"generatedAt" not in outputs[0]


def test_v35_writer_returns_the_fixed_immutable_target(tmp_path: Path) -> None:
    v35 = load_baseline_manifest("v35")
    protected = set(v35["files"]) | {
        f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        for version in range(1, 36)
    }
    for relative in protected:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    historical = {
        version: (MANIFEST_ROOT / f"v{version}.json").read_bytes()
        for version in range(1, 36)
    }

    target = write_v35_manifest(project_root=tmp_path)

    assert target == (
        tmp_path / "tests/fixtures/memory_retrieval_production_freeze/v35.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == build_v35_candidate(
        project_root=tmp_path
    )
    assert historical == {
        version: (
            tmp_path
            / f"tests/fixtures/memory_retrieval_production_freeze/v{version}.json"
        ).read_bytes()
        for version in range(1, 36)
    }
