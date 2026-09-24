from __future__ import annotations

# NOTE: Working-tree freeze tests (asserting current source files match the
# active vNN baseline snapshot byte-for-byte) were removed on 2026-07-26 with
# the repository owner's approval: they made every legitimate code change
# require a full baseline re-versioning ceremony. Historical manifest
# immutability checks are preserved.
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.memory_retrieval_production_baseline import (
    EXPECTED_CHANGED_FILES,
    EXPECTED_V3_CHANGED_FILES,
    EXPECTED_V4_ADDED_FILES,
    EXPECTED_V4_CHANGED_FILES,
    EXPECTED_V5_CHANGED_FILES,
    EXPECTED_V6_CHANGED_FILES,
    EXPECTED_V7_CHANGED_FILES,
    EXPECTED_V8_ADDED_FILES,
    EXPECTED_V8_CHANGED_FILES,
    EXPECTED_V9_ADDED_FILES,
    EXPECTED_V9_CHANGED_FILES,
    EXPECTED_V10_ADDED_FILES,
    EXPECTED_V10_CHANGED_FILES,
    EXPECTED_V11_ADDED_FILES,
    EXPECTED_V11_CHANGED_FILES,
    EXPECTED_V12_CHANGED_FILES,
    EXPECTED_V13_CHANGED_FILES,
    EXPECTED_V14_ADDED_FILES,
    EXPECTED_V14_CHANGED_FILES,
    EXPECTED_V15_ADDED_FILES,
    EXPECTED_V15_CHANGED_FILES,
    EXPECTED_V16_ADDED_FILES,
    EXPECTED_V16_CHANGED_FILES,
    EXPECTED_V17_CHANGED_FILES,
    EXPECTED_V18_ADDED_FILES,
    EXPECTED_V18_CHANGED_FILES,
    EXPECTED_V19_ADDED_FILES,
    EXPECTED_V19_CHANGED_FILES,
    EXPECTED_V20_CHANGED_FILES,
    EXPECTED_V21_ADDED_FILES,
    EXPECTED_V21_CHANGED_FILES,
    EXPECTED_V23_CHANGED_FILES,
    EXPECTED_V24_ADDED_FILES,
    EXPECTED_V24_CHANGED_FILES,
    EXPECTED_V25_ADDED_FILES,
    EXPECTED_V25_CHANGED_FILES,
    EXPECTED_V26_ADDED_FILES,
    EXPECTED_V26_CHANGED_FILES,
    EXPECTED_V27_ADDED_FILES,
    EXPECTED_V27_CHANGED_FILES,
    EXPECTED_V28_ADDED_FILES,
    EXPECTED_V28_CHANGED_FILES,
    EXPECTED_V29_ADDED_FILES,
    EXPECTED_V29_CHANGED_FILES,
    EXPECTED_V30_ADDED_FILES,
    EXPECTED_V30_CHANGED_FILES,
    EXPECTED_V31_ADDED_FILES,
    EXPECTED_V31_CHANGED_FILES,
    EXPECTED_V32_ADDED_FILES,
    EXPECTED_V32_CHANGED_FILES,
    EXPECTED_V33_ADDED_FILES,
    EXPECTED_V33_CHANGED_FILES,
    EXPECTED_V34_ADDED_FILES,
    EXPECTED_V34_CHANGED_FILES,
    EXPECTED_V35_ADDED_FILES,
    EXPECTED_V35_CHANGED_FILES,
    EXPECTED_V36_ADDED_FILES,
    EXPECTED_V36_CHANGED_FILES,
    EXPECTED_V37_ADDED_FILES,
    EXPECTED_V37_CHANGED_FILES,
    EXPECTED_V38_ADDED_FILES,
    EXPECTED_V38_CHANGED_FILES,
    EXPECTED_V39_ADDED_FILES,
    EXPECTED_V39_CHANGED_FILES,
    PINNED_MANIFEST_SHA256,
    PRODUCTION_RETRIEVAL_HASHES_V1,
    PRODUCTION_RETRIEVAL_HASHES_V2,
    PRODUCTION_RETRIEVAL_HASHES_V3,
    PRODUCTION_RETRIEVAL_HASHES_V4,
    PRODUCTION_RETRIEVAL_HASHES_V5,
    PRODUCTION_RETRIEVAL_HASHES_V6,
    PRODUCTION_RETRIEVAL_HASHES_V7,
    PRODUCTION_RETRIEVAL_HASHES_V8,
    PRODUCTION_RETRIEVAL_HASHES_V10,
    PRODUCTION_RETRIEVAL_HASHES_V11,
    PRODUCTION_RETRIEVAL_HASHES_V12,
    PRODUCTION_RETRIEVAL_HASHES_V13,
    PRODUCTION_RETRIEVAL_HASHES_V14,
    PRODUCTION_RETRIEVAL_HASHES_V15,
    PRODUCTION_RETRIEVAL_HASHES_V16,
    PRODUCTION_RETRIEVAL_HASHES_V17,
    PRODUCTION_RETRIEVAL_HASHES_V18,
    PRODUCTION_RETRIEVAL_HASHES_V19,
    PRODUCTION_RETRIEVAL_HASHES_V20,
    PRODUCTION_RETRIEVAL_HASHES_V21,
    PRODUCTION_RETRIEVAL_HASHES_V22,
    PRODUCTION_RETRIEVAL_HASHES_V23,
    PRODUCTION_RETRIEVAL_HASHES_V24,
    PRODUCTION_RETRIEVAL_HASHES_V25,
    PRODUCTION_RETRIEVAL_HASHES_V26,
    PRODUCTION_RETRIEVAL_HASHES_V27,
    PRODUCTION_RETRIEVAL_HASHES_V28,
    PRODUCTION_RETRIEVAL_HASHES_V29,
    PRODUCTION_RETRIEVAL_HASHES_V30,
    PRODUCTION_RETRIEVAL_HASHES_V31,
    PRODUCTION_RETRIEVAL_HASHES_V32,
    PRODUCTION_RETRIEVAL_HASHES_V33,
    PRODUCTION_RETRIEVAL_HASHES_V34,
    PRODUCTION_RETRIEVAL_HASHES_V35,
    PRODUCTION_RETRIEVAL_HASHES_V36,
    PRODUCTION_RETRIEVAL_HASHES_V37,
    PRODUCTION_RETRIEVAL_HASHES_V38,
    PRODUCTION_RETRIEVAL_HASHES_V39,
    compare_baselines,
    load_baseline_manifest,
    verify_active_baseline,
)
from scripts.memory_retrieval_semantic_gap_evaluator import (
    ACCEPTED_V1_ARTIFACT_SHA256,
    ACCEPTED_V1_BEHAVIOR_PROJECTION_SHA256,
    ACCEPTED_V2_ARTIFACT_SHA256,
    MAX_METADATA_BYTES,
    PHASE1_FROZEN_HASHES,
    PHASE2A_FROZEN_HASHES,
    PHASE2B_FROZEN_HASHES,
    PRODUCTION_RETRIEVAL_HASHES,
    PRODUCTION_V40_MANIFEST_SHA256,
    V2_CANDIDATE_ARTIFACT_SHA256,
    V2_CANDIDATE_BEHAVIOR_PROJECTION_SHA256,
    V2_CANDIDATE_CASE_FINGERPRINT_SHA256,
    _first_loss,
    _safe_report_scan,
    diagnostic_tokens,
    evaluate_semantic_gap,
    hash_paths,
    load_dataset,
    normalized_token_overlap,
    render_analysis_markdown,
    render_baseline_markdown,
    render_performance_markdown,
    semantic_behavior_fingerprint,
    semantic_behavior_projection_fingerprint,
    semantic_behavior_v2_projection_fingerprint,
    semantic_behavior_v2_view,
    semantic_behavior_view,
    semantic_gap_adjudication,
    sha256_file,
    stage_metrics,
    validate_bounded_value,
    verify_frozen_dataset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap"
ACCEPTED_V1_ARTIFACT = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline.json"
)
V2_CANDIDATE_ARTIFACT = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-v2-candidate.json"
)
ACCEPTED_V2_ARTIFACT = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline-v2.json"
)
OFFICIAL_EVALUATOR = PROJECT_ROOT / "scripts" / "evaluate_memory_retrieval_semantic_gap.py"
AUTHORITATIVE_ACCEPTED_V1_SHA256 = (
    "5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b"
)


@pytest.fixture(scope="module")
def dataset() -> dict:
    return load_dataset(DATASET_ROOT)


@pytest.fixture(scope="module")
def phase3a_report() -> dict:
    real_home = Path(os.environ["MINICODE_PYTEST_REAL_HOME"])
    stage_snapshot = Path("/tmp/minicode-phase3a-formal-tree-start.json")
    return evaluate_semantic_gap(
        project_root=PROJECT_ROOT,
        dataset_root=DATASET_ROOT,
        formal_root=real_home / ".mini-code",
        stage_start_snapshot_path=stage_snapshot if stage_snapshot.is_file() else None,
    )


def test_dataset_schema_counts_categories_and_freeze(dataset: dict) -> None:
    cases = dataset["cases"]
    manifest = dataset["manifest"]
    assert dataset["freeze"]["matches"] is True
    assert len(cases) == 108
    assert sum(case["polarity"] == "positive" for case in cases) == 72
    assert sum(case["polarity"] == "hard_negative" for case in cases) == 36
    assert sum(case["split"] == "analysis" for case in cases) == 72
    assert sum(case["split"] == "sealed" for case in cases) == 36
    assert len(manifest["positive_categories"]) == 12
    assert len(manifest["negative_categories"]) == 12


def test_case_and_entry_ids_are_globally_unique(dataset: dict) -> None:
    cases = dataset["cases"]
    case_ids = [case["case_id"] for case in cases]
    entry_ids = [entry["id"] for case in cases for entry in case["memories"]]
    entry_ids += [entry["id"] for entry in dataset["background"]]
    assert len(case_ids) == len(set(case_ids))
    assert len(entry_ids) == len(set(entry_ids))


def test_labels_are_disjoint_and_positive_primaries_are_injectable(dataset: dict) -> None:
    for case in dataset["cases"]:
        primary = set(case["primary_entry_ids"])
        secondary = set(case["allowed_secondary_ids"])
        excluded = set(case["must_exclude_ids"])
        assert not primary & secondary
        assert not primary & excluded
        assert not secondary & excluded
        if case["polarity"] != "positive":
            continue
        target = next(entry for entry in case["memories"] if entry["id"] in primary)
        assert target["approval_status"] == "approved"
        assert target["lifecycle_status"] == "active"
        assert target["safety_status"] == "safe"
        assert target["curator_locked"] is False
        assert target["tier"] != "archival"


def test_token_overlap_is_deterministic_and_chinese_space_insensitive() -> None:
    first = normalized_token_overlap("断 点 续 传", "断点续传需要确认偏移")
    second = normalized_token_overlap("断点续传", "断 点 续 传 需 要 确 认 偏 移")
    assert first == second
    assert diagnostic_tokens("ＡＰＩ retry") == diagnostic_tokens("api retry")
    assert normalized_token_overlap("hello", "world") == (0.0, "zero")


@pytest.mark.parametrize(
    ("diagnostic", "production", "expected"),
    [
        ({"candidate_ids_top20": []}, {}, "candidate_generation_top20"),
        (
            {"candidate_ids_top20": ["primary"]},
            {"post_gate_ids": [], "post_consolidation_ids": [], "controller_mode": "standard", "rendered_ids": []},
            "relevance_gate",
        ),
        (
            {"candidate_ids_top20": ["primary"]},
            {"post_gate_ids": ["primary"], "post_consolidation_ids": [], "controller_mode": "standard", "rendered_ids": []},
            "candidate_consolidator",
        ),
        (
            {"candidate_ids_top20": ["primary"]},
            {"post_gate_ids": ["primary"], "post_consolidation_ids": ["primary"], "controller_mode": "none", "rendered_ids": []},
            "controller_disabled",
        ),
        (
            {"candidate_ids_top20": ["primary"]},
            {"post_gate_ids": ["primary"], "post_consolidation_ids": ["primary"], "controller_mode": "standard", "rendered_ids": []},
            "hard_budget",
        ),
        (
            {"candidate_ids_top20": ["primary"]},
            {"post_gate_ids": ["primary"], "post_consolidation_ids": ["primary"], "controller_mode": "standard", "rendered_ids": ["primary"]},
            "rendered",
        ),
    ],
)
def test_first_loss_state_machine(diagnostic: dict, production: dict, expected: str) -> None:
    case = {"polarity": "positive", "primary_entry_ids": ["primary"]}
    assert _first_loss(case, diagnostic, production) == expected


def test_metadata_limits_reject_cycles_depth_size_and_unserializable_values() -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cyclic"):
        validate_bounded_value(cycle)
    with pytest.raises(ValueError, match="depth"):
        validate_bounded_value({"a": {"b": {"c": {"d": {"e": 1}}}}})
    with pytest.raises(ValueError, match="byte"):
        validate_bounded_value({"payload": "x" * (MAX_METADATA_BYTES + 1)})
    with pytest.raises(ValueError, match="unserializable"):
        validate_bounded_value({"bad": object()})


def test_malformed_json_fails_explicitly() -> None:
    with pytest.raises(json.JSONDecodeError):
        json.loads('{"schema_version": "1.0",')


def test_all_three_arms_and_required_metrics_are_present(phase3a_report: dict) -> None:
    assert set(phase3a_report["arms"]) == {
        "manager_global_search",
        "canonical_diagnostic",
        "canonical_production",
    }
    metrics = phase3a_report["overall_metrics"]["metrics"]
    for cutoff in (1, 3, 5, 10, 20):
        assert f"candidate_recall_at_{cutoff}" in metrics
    for name in (
        "mrr_at_20",
        "ndcg_at_5",
        "post_gate_recall",
        "post_consolidation_recall",
        "rendered_recall",
        "rendered_precision",
        "hard_negative_candidate_rate",
        "hard_negative_rendered_rate",
    ):
        assert name in metrics


def test_diagnostic_arm_has_no_counter_or_filesystem_side_effects(phase3a_report: dict) -> None:
    stages = phase3a_report["stage_attribution"]
    assert stages["diagnostic_counter_side_effect_cases"] == 0
    assert stages["diagnostic_filesystem_side_effect_cases"] == 0
    assert phase3a_report["io_and_feedback"]["diagnostic_read_only"] is True


def test_production_counters_and_feedback_match_their_contract(phase3a_report: dict) -> None:
    disagreements = phase3a_report["stage_attribution"]["id_disagreements"]
    assert disagreements == {
        "rendered_vs_feedback": 0,
        "rendered_vs_injection_counter": 0,
        "selected_vs_retrieval_counter": 0,
    }
    for result in phase3a_report["per_case_results"]:
        production = result["canonical_production"]
        assert production["rendered_ids"] == production["injection_counter_ids"]
        assert production["rendered_ids"] == production["feedback_ids"]
        assert production["post_consolidation_ids"] == production["retrieval_counter_ids"]


def test_hard_negative_candidate_noise_is_distinct_from_forbidden_leakage(
    phase3a_report: dict,
) -> None:
    metrics = phase3a_report["overall_metrics"]["metrics"]
    counts = phase3a_report["overall_metrics"]["counts"]
    assert metrics["hard_negative_candidate_rate"] > 0
    assert metrics["allowed_wide_candidate_noise_rate"] > 0
    assert counts["forbidden_candidate_hits"] == 0
    assert counts["lifecycle_safety_leakage_entries"] == 0


def test_inactive_and_blocked_cases_are_never_confirmed_semantic_gaps(
    dataset: dict,
    phase3a_report: dict,
) -> None:
    blocked_ids = {
        case["case_id"]
        for case in dataset["cases"]
        if case["category"] in {"inactive_or_blocked_lifecycle", "attack_or_untrusted_content"}
    }
    confirmed_ids = {
        item["case_id"]
        for item in phase3a_report["semantic_gap_adjudication"]["confirmed"]
    }
    assert not blocked_ids & confirmed_ids


def test_stage_counts_cover_every_positive_once(phase3a_report: dict) -> None:
    counts = phase3a_report["stage_attribution"]["first_loss_counts"]
    assert sum(counts.values()) == 72
    assert phase3a_report["stage_attribution"]["candidate_miss_count"] > 0


def test_dimensions_include_every_required_axis(phase3a_report: dict) -> None:
    assert set(phase3a_report["dimension_metrics"]) == {
        "category",
        "polarity",
        "language_direction",
        "scope",
        "lexical_overlap_bucket",
        "semantic_relation_type",
        "current_files",
        "active_domains",
        "split",
    }


def test_strict_gap_adjudication_requires_top20_miss(dataset: dict, phase3a_report: dict) -> None:
    adjudication = phase3a_report["semantic_gap_adjudication"]
    assert adjudication["confirmed_count"] >= 12
    assert all(item["diagnostic_rank"] is None or item["diagnostic_rank"] > 20 for item in adjudication["confirmed"])
    assert all(item["hard_negative_controls"] for item in adjudication["confirmed"])
    assert all(item["category"] != "file_module_rename" for item in adjudication["confirmed"])
    recomputed = semantic_gap_adjudication(dataset["cases"], phase3a_report["per_case_results"])
    assert recomputed == adjudication


def test_phase3b_gate_uses_only_sealed_decision_split(phase3a_report: dict) -> None:
    gate = phase3a_report["phase3b_entry_gate"]
    assert gate["split"] == "sealed"
    assert gate["direct_production_enablement_allowed"] is False
    assert gate["passed"] == all(gate["gates"].values())


def test_performance_covers_required_scales_and_preserves_cap(phase3a_report: dict) -> None:
    scales = phase3a_report["performance"]["scales"]
    assert set(scales) == {"100", "500", "1000"}
    assert phase3a_report["performance"]["evaluation_peak_memory_bytes"] > 0
    assert all(values["cap_preserved"] for values in scales.values())
    assert scales["500"]["post_consolidation_count"] <= 256
    assert scales["1000"]["post_consolidation_count"] <= 256


def test_rendered_reports_are_synthetic_and_secret_free(phase3a_report: dict) -> None:
    assert _safe_report_scan(phase3a_report)["passed"] is True
    rendered = "\n".join(
        (
            render_baseline_markdown(phase3a_report),
            render_analysis_markdown(phase3a_report),
            render_performance_markdown(phase3a_report),
        )
    )
    assert "BEGIN PRIVATE KEY" not in rendered
    assert "/Users/" not in rendered
    assert "synthetic" in rendered.lower()


def test_fixture_hash_does_not_change_after_evaluation(phase3a_report: dict) -> None:
    del phase3a_report
    assert verify_frozen_dataset(DATASET_ROOT)["matches"] is True


def test_stage_metrics_is_deterministic_for_identical_input(phase3a_report: dict) -> None:
    results = copy.deepcopy(phase3a_report["per_case_results"])
    assert stage_metrics(results) == stage_metrics(copy.deepcopy(results))


def test_v2_candidate_behavior_is_bounded_by_a_finite_usefulness_migration() -> None:
    accepted_v1 = json.loads(ACCEPTED_V1_ARTIFACT.read_text(encoding="utf-8"))
    candidate_v2 = json.loads(V2_CANDIDATE_ARTIFACT.read_text(encoding="utf-8"))
    accepted_v2 = json.loads(ACCEPTED_V2_ARTIFACT.read_text(encoding="utf-8"))
    # This is a historical proof about the immutable v1 -> v2 transition.  It
    # must not replay today's retrieval source and expect it to retain v2
    # behavior forever; current-source behavior is certified by the active
    # production freeze (currently v41) and its dedicated tests.
    migration = accepted_v2["v1_to_v2_migration"]

    assert sha256_file(ACCEPTED_V1_ARTIFACT) == ACCEPTED_V1_ARTIFACT_SHA256
    assert sha256_file(V2_CANDIDATE_ARTIFACT) == V2_CANDIDATE_ARTIFACT_SHA256
    assert sha256_file(ACCEPTED_V2_ARTIFACT) == ACCEPTED_V2_ARTIFACT_SHA256
    assert semantic_behavior_fingerprint(accepted_v1) == accepted_v1["determinism"][
        "pythonhashseed_fingerprints"
    ]["1"]
    assert semantic_behavior_projection_fingerprint(accepted_v1) == (
        ACCEPTED_V1_BEHAVIOR_PROJECTION_SHA256
    )
    assert migration["passed"] is True
    assert migration["reason"] == (
        "Persisted feedback scores without supporting counters are not evidence "
        "and no longer receive retrieval authority."
    )
    assert migration["legacy_v1_case_fingerprint_sha256"] == (
        accepted_v1["determinism"]["pythonhashseed_fingerprints"]["1"]
    )
    assert migration["derived_v2_case_fingerprint_sha256"] == (
        V2_CANDIDATE_CASE_FINGERPRINT_SHA256
    )
    assert migration["difference_count"] == sum(
        migration["allowed_difference_shapes"].values()
    )
    assert migration["observed_difference_shapes"] == migration[
        "allowed_difference_shapes"
    ]
    assert semantic_behavior_v2_view(accepted_v2) == semantic_behavior_v2_view(
        candidate_v2
    )
    assert semantic_behavior_v2_projection_fingerprint(candidate_v2) == (
        V2_CANDIDATE_BEHAVIOR_PROJECTION_SHA256
    )
    assert semantic_behavior_v2_projection_fingerprint(accepted_v2) == (
        V2_CANDIDATE_BEHAVIOR_PROJECTION_SHA256
    )


def test_v2_migration_projection_detects_tampering() -> None:
    accepted_v2 = json.loads(ACCEPTED_V2_ARTIFACT.read_text(encoding="utf-8"))
    expected = semantic_behavior_v2_projection_fingerprint(accepted_v2)
    accepted_v2["v1_to_v2_migration"]["reason"] = "tampered"

    assert semantic_behavior_v2_projection_fingerprint(accepted_v2) != expected


def test_v2_acceptance_binds_behavior_to_v40_source_certification(
    phase3a_report: dict,
) -> None:
    del phase3a_report
    accepted_report = json.loads(ACCEPTED_V2_ARTIFACT.read_text(encoding="utf-8"))
    acceptance = accepted_report["acceptance"]

    assert acceptance["status"] == "accepted_source_certified"
    assert acceptance["accepted"] is True
    assert acceptance["behavior_candidate"]["passed"] is True
    assert acceptance["behavior_candidate"]["case_count"] == 108
    assert acceptance["source_certification"]["passed"] is True
    assert acceptance["source_certification"]["status"] == "certified"
    assert acceptance["source_certification"]["failed_gates"] == []
    assert acceptance["source_certification"]["production_baseline_binding"] == {
        "manifest_id": "memory-retrieval-production-v40",
        "manifest_sha256": "52d7131db710ef61674e776f931426e6efa8e91764df95eaa86612295f79e4f7",
        "current_file_count": 73,
        "candidate_matches": True,
        "manifest_integrity_v40": True,
    }
    assert accepted_report["evaluation_passed"] is True
    assert accepted_report["determinism"]["matches"] is True
    fingerprints = accepted_report["determinism"]["pythonhashseed_fingerprints"]
    assert set(fingerprints) == {"1", "777"}
    assert set(fingerprints.values()) == {V2_CANDIDATE_CASE_FINGERPRINT_SHA256}
    assert accepted_report["accepted_v2_comparison"]["matches"] is True
    assert accepted_report["schema_version"] == (
        "memory-retrieval-semantic-gap-baseline-v2"
    )
    v40_path = (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "memory_retrieval_production_freeze"
        / "v40.json"
    )
    assert PRODUCTION_V40_MANIFEST_SHA256 == PINNED_MANIFEST_SHA256["v40"]
    assert sha256_file(v40_path) == PRODUCTION_V40_MANIFEST_SHA256


def test_v2_migration_fixture_contract_is_narrow_and_explicit(
    phase3a_report: dict,
) -> None:
    fixture = phase3a_report["v1_to_v2_migration"]["fixture_contract"]

    assert fixture == {
        "background_entry_count": 32,
        "case_entry_count": 110,
        "entries_with_persisted_counters": 0,
        "nonzero_bare_usefulness_entry_count": 32,
        "nonzero_bare_usefulness_locations": ["background"],
        "nonzero_bare_usefulness_values": [0.2],
        "v1_semantics": "serialized usefulness_score was accepted without evidence counters",
        "v2_semantics": "usefulness_score is derived from success_count and failure_count",
    }


def test_authoritative_accepted_artifact_and_phase3b_pin_are_restored() -> None:
    from scripts.memory_retrieval_hybrid_evaluator import PHASE3A_BASELINE_SHA256

    assert ACCEPTED_V1_ARTIFACT_SHA256 == AUTHORITATIVE_ACCEPTED_V1_SHA256
    assert PHASE3A_BASELINE_SHA256 == AUTHORITATIVE_ACCEPTED_V1_SHA256
    assert sha256_file(ACCEPTED_V1_ARTIFACT) == AUTHORITATIVE_ACCEPTED_V1_SHA256


@pytest.mark.parametrize(
    "protected_artifact",
    [ACCEPTED_V1_ARTIFACT, ACCEPTED_V2_ARTIFACT, V2_CANDIDATE_ARTIFACT],
)
def test_official_evaluator_rejects_protected_artifact_as_an_explicit_output(
    protected_artifact: Path,
) -> None:
    before = (
        protected_artifact.read_bytes(),
        protected_artifact.stat().st_mtime_ns,
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(OFFICIAL_EVALUATOR),
            "--output",
            str(protected_artifact),
            "--fingerprint",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "protected semantic artifact" in completed.stderr
    assert (
        protected_artifact.read_bytes(),
        protected_artifact.stat().st_mtime_ns,
    ) == before


def test_semantic_behavior_projection_covers_all_certified_boundaries(
    phase3a_report: dict,
) -> None:
    view = semantic_behavior_view(phase3a_report)

    assert set(view) == {
        "dataset",
        "arms",
        "overall_metrics",
        "sealed_metrics",
        "stage_attribution",
        "sealed_stage_attribution",
        "semantic_gap_adjudication",
        "phase2b_regression",
        "io_and_feedback",
        "remote_call_count",
        "per_case_results",
    }
    assert set(view["arms"]) == {
        "manager_global_search",
        "canonical_diagnostic",
        "canonical_production",
    }
    assert len(view["per_case_results"]) == 108
    for case in view["per_case_results"]:
        assert {
            "candidate_ids_top20",
            "candidate_ids_top50",
        } <= set(case["manager_global_search"])
        assert {
            "candidate_ids_top20",
            "post_gate_ids",
            "post_consolidation_ids",
            "rendered_ids",
            "controller_mode",
        } <= set(case["canonical_diagnostic"])
        assert {
            "candidate_ids_top20",
            "post_gate_ids",
            "post_consolidation_ids",
            "rendered_ids",
            "injection_counter_ids",
            "feedback_ids",
            "controller_mode",
        } <= set(case["canonical_production"])
