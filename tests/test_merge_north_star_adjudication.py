from __future__ import annotations

import pytest

from scripts.merge_north_star_adjudication import merge_results


def _manifest(suite_id: str, second_value: str) -> dict:
    return {
        "suiteId": suite_id,
        "cases": [
            {"id": "case-one", "oracle": "same"},
            {"id": "case-two", "oracle": second_value},
        ],
    }


def test_merge_reuses_unchanged_and_records_replacement_lineage() -> None:
    merged = merge_results(
        original_manifest=_manifest("v1", "old"),
        revised_manifest=_manifest("v2", "new"),
        original_results={
            "results": [
                {"id": "case-one", "status": "passed", "runId": "run_old_1"},
                {"id": "case-two", "status": "failed", "runId": "run_old_2"},
            ]
        },
        rerun_results={
            "results": [
                {"id": "case-two", "status": "passed", "runId": "run_new_2"}
            ]
        },
        replacements={"case-two": "oracle_revision"},
    )

    assert [item["runId"] for item in merged["results"]] == [
        "run_old_1",
        "run_new_2",
    ]
    assert merged["reusedOriginalEvidenceCount"] == 1
    assert merged["adjudications"][0]["priorRunId"] == "run_old_2"


def test_merge_refuses_changed_case_without_fresh_evidence() -> None:
    with pytest.raises(ValueError, match="revised case"):
        merge_results(
            original_manifest=_manifest("v1", "old"),
            revised_manifest=_manifest("v2", "new"),
            original_results={
                "results": [
                    {"id": "case-one", "status": "passed"},
                    {"id": "case-two", "status": "failed"},
                ]
            },
            rerun_results={"results": []},
            replacements={},
        )


def test_merge_preserves_prior_adjudication_lineage() -> None:
    manifest = _manifest("v2", "new")
    merged = merge_results(
        original_manifest=manifest,
        revised_manifest=manifest,
        original_results={
            "results": [
                {"id": "case-one", "status": "passed", "runId": "run_one"},
                {"id": "case-two", "status": "failed", "runId": "run_two"},
            ],
            "adjudications": [{"id": "case-two", "reason": "first_pass"}],
        },
        rerun_results={
            "results": [
                {"id": "case-two", "status": "passed", "runId": "run_fixed"}
            ]
        },
        replacements={"case-two": "production_fix"},
    )

    assert [item["reason"] for item in merged["adjudications"]] == [
        "first_pass",
        "production_fix",
    ]
