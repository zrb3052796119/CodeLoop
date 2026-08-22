from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.build_non_path_memory_study_manifest import (
    BLOCK_SEEDS,
    FAMILIES,
    FAMILIES_V1,
    SUITE_ID,
    SUITE_ID_V1,
    build_manifest,
)
from scripts.run_north_star_live import _validate_manifest


def test_non_path_manifest_shape_and_runner_contract() -> None:
    document = build_manifest()
    cases = _validate_manifest(document)

    assert document["suiteId"] == SUITE_ID
    assert document["familyCount"] == 12
    assert document["blockCount"] == 3
    assert document["pairCount"] == 36
    assert document["taskCount"] == 96
    assert document["primaryUnit"] == "family"
    assert document["blockSeeds"] == list(BLOCK_SEEDS)
    assert len(cases) == 72
    assert len({case["id"] for case in cases}) == 72


def test_non_path_manifest_balances_strata_modes_and_pairs() -> None:
    assert Counter(family.stratum for family in FAMILIES) == {
        "command-recovery": 3,
        "code-repair": 3,
        "project-constraint": 3,
        "verification-rule": 3,
    }
    assert Counter(family.lesson_mode for family in FAMILIES) == {
        "learned": 8,
        "seeded": 4,
    }

    pairs: dict[tuple[int, str], set[str]] = defaultdict(set)
    for case in build_manifest()["cases"]:
        study = case["study"]
        pairs[(study["block"], study["familyId"])].add(study["condition"])

    assert len(pairs) == 36
    assert all(conditions == {"warm", "cold"} for conditions in pairs.values())


def test_non_path_condition_order_is_balanced_inside_every_block() -> None:
    cases = build_manifest()["cases"]

    for block in range(1, 4):
        block_cases = [case for case in cases if case["study"]["block"] == block]
        first_conditions = [
            case["study"]["condition"]
            for case in block_cases
            if case["study"]["conditionOrder"] == 1
        ]
        assert len(block_cases) == 24
        assert Counter(first_conditions) == {"warm": 6, "cold": 6}


def test_learned_target_state_is_matched_before_the_cold_target() -> None:
    cases = build_manifest()["cases"]
    by_key = {
        (case["study"]["block"], case["study"]["familyId"], case["study"]["condition"]): case
        for case in cases
    }

    for family in FAMILIES:
        if family.lesson_mode != "learned":
            continue
        for block in range(1, 4):
            warm = by_key[(block, family.family_id, "warm")]
            cold = by_key[(block, family.family_id, "cold")]
            assert len(warm["turns"]) == 2
            assert len(cold["turns"]) == 1
            if family.learning_file:
                assert warm["files"][family.learning_file] == family.learning_before
                assert cold["files"][family.learning_file] == family.learning_after
            for path, content in cold["files"].items():
                if path == family.learning_file:
                    continue
                assert warm["files"][path] == content


def test_non_path_oracles_require_action_correctness_and_memory_evidence() -> None:
    for case in build_manifest()["cases"]:
        study = case["study"]
        kinds = Counter(oracle["kind"] for oracle in case["oracles"])
        assert kinds["all_runs_completed"] == 1
        assert kinds["canonical_success"] == 1
        assert kinds["tool_succeeded"] == 1
        assert kinds["command"] == 1
        assert kinds["response_contains"] == 1
        if study["targetFile"]:
            assert kinds["file_contains"] == 1
            assert case["mutability"] == "write"
            assert study["targetFile"] in case["authorizedPaths"]
        else:
            assert kinds["no_source_edits"] == 1
            assert case["mutability"] == "read_only"
        if study["condition"] == "warm":
            assert kinds["memory_injected"] == 1
        else:
            assert kinds["memory_injected"] == 0
        if study["condition"] == "warm" and study["lessonMode"] == "learned":
            assert kinds["memory_written"] == 1
            assert kinds["tool_failed"] == int(study["learningFailureRequired"])


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_every_write_family_has_a_distinct_target_and_valid_verifier_fixture(
    tmp_path: Path,
) -> None:
    for family in FAMILIES:
        if not family.target_file:
            continue
        family_root = tmp_path / family.family_id
        family_root.mkdir()
        common = dict(family.common_files)
        if family.learning_file:
            common[family.learning_file] = family.learning_after
        common[family.target_file] = family.target_before
        _write_files(family_root, common)
        argv = [sys.executable if item == "{python}" else item for item in family.target_verifier]

        before = subprocess.run(
            argv,
            cwd=family_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert family.target_expected not in family.target_before
        if family.stratum == "verification-rule":
            # These cases measure whether the agent remembers to run an already
            # valid project verifier after an unrelated source edit.  The edit
            # itself is checked by the file-content oracle below.
            assert before.returncode == 0, (family.family_id, before.stderr)
        else:
            # Repair and constraint cases measure a state transition from a
            # failing project contract to a passing one.
            assert before.returncode != 0, family.family_id

        (family_root / family.target_file).write_text(
            family.target_after,
            encoding="utf-8",
        )
        after = subprocess.run(
            argv,
            cwd=family_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert after.returncode == 0, (family.family_id, after.stderr)
        assert family.target_expected in family.target_after
        assert family.target_marker in after.stdout


def test_command_families_have_runnable_hidden_markers(tmp_path: Path) -> None:
    for family in FAMILIES:
        if family.stratum != "command-recovery":
            continue
        family_root = tmp_path / family.family_id
        family_root.mkdir()
        _write_files(family_root, dict(family.common_files))
        argv = [sys.executable if item == "{python}" else item for item in family.target_verifier]

        completed = subprocess.run(
            argv,
            cwd=family_root,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, (family.family_id, completed.stderr)
        assert family.target_marker in completed.stdout
        assert family.target_marker not in family.target_prompt
        assert family.target_marker not in family.seed_memory


def test_builder_is_deterministic_in_memory() -> None:
    assert build_manifest() == build_manifest()


def test_builder_reproduces_failed_v1_cache_fixture_authority() -> None:
    v1 = build_manifest(suite_version="v1")
    serialized = json.dumps(v1, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    assert v1["suiteId"] == SUITE_ID_V1
    assert FAMILIES_V1 != FAMILIES
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == (
        "63738ee553772286b634e929fa3bb9ca9a61c1b6d17bd3cff04b3308cb165757"
    )
