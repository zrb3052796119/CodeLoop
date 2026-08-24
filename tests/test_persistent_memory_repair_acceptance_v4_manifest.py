from __future__ import annotations

import json

from scripts.build_persistent_memory_repair_acceptance_v4_manifest import (
    SUITE_ID,
    V3_MANIFEST,
    build_manifest,
)
from scripts.run_north_star_live import _validate_manifest


def test_v4_changes_no_case_fixture_or_oracle_from_frozen_v3() -> None:
    v3 = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))
    v4 = build_manifest()

    assert v4["suiteId"] == SUITE_ID
    assert v4["cases"] == v3["cases"]
    assert len(_validate_manifest(v4)) == 10
    assert v4["supersedes"]["retainedAsFailureEvidence"] is True
    assert v4["supersedes"]["firstAttemptPassedCases"] == 7


def test_v4_still_has_one_exact_attribution_oracle_per_case() -> None:
    manifest = build_manifest()

    for case in manifest["cases"]:
        attribution = [
            oracle
            for oracle in case["oracles"]
            if oracle["kind"] == "memory_attributed"
        ]
        assert len(attribution) == 1
        assert attribution[0]["id"] == "lesson-attributed"
