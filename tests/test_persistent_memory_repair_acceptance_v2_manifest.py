from __future__ import annotations

from collections import Counter

from scripts.build_persistent_memory_repair_acceptance_v2_manifest import (
    SUITE_ID,
    V1_MANIFEST_SHA256,
    build_manifest,
)
from scripts.run_north_star_live import _validate_manifest


def test_v2_manifest_retains_two_cases_per_lesson_family() -> None:
    document = build_manifest()
    cases = _validate_manifest(document)

    assert document["suiteId"] == SUITE_ID
    assert document["supersedes"]["sha256"] == V1_MANIFEST_SHA256
    assert len(cases) == 10
    assert Counter(
        case["study"]["acceptanceFamily"] for case in cases
    ) == {
        "path_resource_recovery": 2,
        "command_recovery": 2,
        "code_fix_recovery": 2,
        "stable_verification_rule": 2,
        "project_constraint_decision": 2,
    }


def test_v2_normalization_oracle_accepts_lower_or_casefold_implementations() -> None:
    document = build_manifest()
    case = next(
        item
        for item in document["cases"]
        if item["id"] == "npmem-b1-token-normalization-repair-warm"
    )
    oracle = next(item for item in case["oracles"] if item["id"] == "target-content")

    assert oracle == {
        "id": "target-content",
        "kind": "file_not_contains",
        "path": "parser/normalizer_secondary.py",
        "text": "normalized = value.strip()\n",
    }
