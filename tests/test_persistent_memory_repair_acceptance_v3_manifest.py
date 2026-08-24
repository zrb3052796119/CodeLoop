from __future__ import annotations

from collections import Counter

from scripts.build_persistent_memory_repair_acceptance_v3_manifest import (
    ATTRIBUTION_BY_CASE,
    SUITE_ID,
    build_manifest,
)
from scripts.run_north_star_live import _validate_manifest


def test_v3_manifest_has_two_strongly_attributed_cases_per_lesson_family() -> None:
    manifest = build_manifest()

    assert manifest["suiteId"] == SUITE_ID
    assert manifest["caseCount"] == 10
    assert Counter(
        case["study"]["acceptanceFamily"] for case in manifest["cases"]
    ) == {
        "path_resource_recovery": 2,
        "command_recovery": 2,
        "code_fix_recovery": 2,
        "stable_verification_rule": 2,
        "project_constraint_decision": 2,
    }
    assert len(_validate_manifest(manifest)) == 10

    for case in manifest["cases"]:
        attribution = [
            oracle
            for oracle in case["oracles"]
            if oracle["kind"] == "memory_attributed"
        ]
        assert attribution == [
            {
                "id": "lesson-attributed",
                "kind": "memory_attributed",
                **ATTRIBUTION_BY_CASE[case["id"]],
            }
        ]
        assert case["oracleIds"].count("lesson-attributed") == 1


def test_v3_keeps_the_v2_behavioral_oracle_correction() -> None:
    manifest = build_manifest()
    case = next(
        item
        for item in manifest["cases"]
        if item["id"] == "npmem-b1-token-normalization-repair-warm"
    )
    oracle = next(item for item in case["oracles"] if item["id"] == "target-content")

    assert oracle == {
        "id": "target-content",
        "kind": "file_not_contains",
        "path": "parser/normalizer_secondary.py",
        "text": "normalized = value.strip()\n",
    }
