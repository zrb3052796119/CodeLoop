from __future__ import annotations

from collections import Counter

from scripts.build_persistent_memory_repair_acceptance_manifest import (
    NON_PATH_SOURCE,
    PATH_SOURCE,
    SELECTIONS,
    build_manifest,
)
from scripts.run_north_star_live import _validate_manifest


def test_repair_acceptance_manifest_has_two_cases_per_lesson_family() -> None:
    manifest = build_manifest()
    counts = Counter(
        case["study"]["acceptanceFamily"] for case in manifest["cases"]
    )

    assert counts == {family: 2 for family in SELECTIONS}
    assert manifest["caseCount"] == 10
    assert manifest["familyCount"] == 5


def test_repair_acceptance_cases_are_synthetic_warm_and_unique() -> None:
    cases = build_manifest()["cases"]

    assert len({case["id"] for case in cases}) == 10
    assert all(case["study"]["condition"] == "warm" for case in cases)
    assert all("synthetic" in case["fixtureId"] for case in cases)
    assert all(case["study"]["block"] == 1 for case in cases)


def test_repair_acceptance_manifest_satisfies_real_runner_contract() -> None:
    cases = _validate_manifest(build_manifest())

    assert len(cases) == 10
    assert {case["mutability"] for case in cases} == {"read_only", "write"}
    assert PATH_SOURCE.is_file()
    assert NON_PATH_SOURCE.is_file()
