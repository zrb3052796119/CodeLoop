from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json

from scripts.build_persistent_memory_large_study_manifest import (
    BLOCK_SEEDS,
    FAMILIES,
    FAMILIES_V1,
    FAMILIES_V2,
    SUITE_ID,
    SUITE_ID_V1,
    SUITE_ID_V2,
    build_manifest,
)
from scripts.run_north_star_live import _validate_manifest


def test_large_study_manifest_freezes_declared_shape_and_runner_contract() -> None:
    document = build_manifest()
    cases = _validate_manifest(document)

    assert document["suiteId"] == SUITE_ID
    assert document["familyCount"] == 16
    assert document["blockCount"] == 3
    assert document["pairCount"] == 48
    assert document["primaryUnit"] == "family"
    assert document["blockSeeds"] == list(BLOCK_SEEDS)
    assert len(cases) == 96
    assert sum(len(case["turns"]) for case in cases) == 120
    assert len({case["id"] for case in cases}) == 96


def test_large_study_has_complete_warm_cold_pairs_and_balanced_strata() -> None:
    cases = build_manifest()["cases"]
    pairs: dict[tuple[int, str], set[str]] = defaultdict(set)
    families = {family.family_id: family for family in FAMILIES}

    for case in cases:
        study = case["study"]
        pairs[(study["block"], study["familyId"])].add(study["condition"])

    assert len(pairs) == 48
    assert all(conditions == {"warm", "cold"} for conditions in pairs.values())
    assert Counter(family.stratum for family in FAMILIES) == {
        "application-security": 4,
        "operations": 4,
        "data-governance": 4,
        "developer-platform": 4,
    }
    assert Counter(family.lesson_mode for family in families.values()) == {
        "learned": 8,
        "seeded": 8,
    }


def test_large_study_randomization_is_blocked_and_condition_order_balanced() -> None:
    cases = build_manifest()["cases"]

    for block in range(1, 4):
        block_cases = [case for case in cases if case["study"]["block"] == block]
        assert len(block_cases) == 32
        assert [case["study"]["conditionOrder"] for case in block_cases] == [
            value for _ in range(16) for value in (1, 2)
        ]
        first_conditions = [
            case["study"]["condition"]
            for case in block_cases
            if case["study"]["conditionOrder"] == 1
        ]
        assert Counter(first_conditions) == {"warm": 8, "cold": 8}
        assert len({case["study"]["pairOrder"] for case in block_cases}) == 16


def test_large_study_oracles_prove_learning_injection_and_exact_target_success() -> None:
    cases = build_manifest()["cases"]

    for case in cases:
        kinds = Counter(oracle["kind"] for oracle in case["oracles"])
        study = case["study"]
        assert kinds["all_runs_completed"] == 1
        assert kinds["canonical_success"] == 1
        assert kinds["no_source_edits"] == 1
        assert kinds["tool_succeeded"] == 1
        assert kinds["response_contains"] == 1
        source_read = next(
            oracle for oracle in case["oracles"] if oracle["kind"] == "tool_succeeded"
        )
        assert source_read == {
            "id": "source-read",
            "kind": "tool_succeeded",
            "toolName": "read_file",
            "min": 1,
            "everyTurn": True,
        }
        if study["condition"] == "warm":
            assert kinds["memory_injected"] == 1
        else:
            assert kinds["memory_injected"] == 0
        if study["condition"] == "warm" and study["lessonMode"] == "learned":
            assert len(case["turns"]) == 2
            assert kinds["tool_failed"] == 1
            assert kinds["memory_written"] == 1
        else:
            assert len(case["turns"]) == 1
            assert kinds["tool_failed"] == 0
            assert kinds["memory_written"] == 0


def test_large_study_target_prompts_do_not_leak_paths_or_markers() -> None:
    for case in build_manifest()["cases"]:
        study = case["study"]
        target_prompt = case["turns"][study["targetTurnIndex"]]["prompt"]
        assert study["failedPath"] not in target_prompt
        assert study["correctedPath"] not in target_prompt
        assert study["marker"] not in target_prompt
        assert set(case["files"]) == {"README.md", study["correctedPath"]}


def test_large_study_builder_is_byte_stable_in_memory() -> None:
    assert build_manifest() == build_manifest()


def test_large_study_v3_learned_recoveries_preserve_the_failed_path_suffix() -> None:
    learned = [family for family in FAMILIES if family.lesson_mode == "learned"]

    assert len(learned) == 8
    assert all(
        family.corrected_path.endswith(family.failed_path)
        for family in learned
    )


def test_large_study_builder_still_reproduces_the_failed_v1_smoke_authority() -> None:
    v1 = build_manifest(suite_version="v1")
    serialized = json.dumps(v1, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    assert v1["suiteId"] == SUITE_ID_V1
    assert FAMILIES_V1 != FAMILIES_V2
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == (
        "51a985daa8b0f95f38fdefb42ea0c0c2f22f5e6691cb553a7b449766afff9d97"
    )


def test_large_study_builder_still_reproduces_the_failed_v2_smoke_authority() -> None:
    v2 = build_manifest(suite_version="v2")
    serialized = json.dumps(v2, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    assert v2["suiteId"] == SUITE_ID_V2
    assert FAMILIES_V2 != FAMILIES
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == (
        "54c16b804442c88caf86c1a74b7c25f32125e8987fbc0cabab80d13275abf211"
    )
