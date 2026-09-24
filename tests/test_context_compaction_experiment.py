from __future__ import annotations

from collections import Counter

from scripts.analyze_context_compaction_experiment import (
    _analysis,
    _bootstrap_mean_ci,
    _exact_sign_permutation_p,
    _rank_biserial,
    _sustained_crossover,
)
from scripts.build_context_compaction_experiment_manifest import (
    CANONICAL_WINDOW,
    CONTROL_WINDOW,
    build_manifest,
    build_pilot_manifest,
)
from scripts.run_context_compaction_stress import build_report as build_stress_report
from scripts.run_north_star_live import _validate_manifest


def test_context_compaction_manifest_freezes_matched_live_matrix() -> None:
    document = build_manifest()
    cases = _validate_manifest(document)

    assert document["caseCount"] == 24
    assert document["taskCount"] == 120
    assert document["pairCount"] == 12
    assert len(cases) == 24
    assert Counter(
        case["experiment"]["condition"] for case in cases
    ) == {"canonical": 12, "high-window-control": 12}
    assert Counter(case["experiment"]["family"] for case in cases) == {
        "port-normalization": 6,
        "retry-delay": 6,
        "record-parser": 6,
        "cache-key": 6,
    }
    assert all(len(case["turns"]) == 5 for case in cases)
    assert all(case["mutability"] == "write" for case in cases)
    assert all(len(case["authorizedPaths"]) == 1 for case in cases)
    assert document["runtimeProfileContract"]["model"] == "deepseek-v4-pro"
    assert len(document["runtimeProfileContract"]["profileSha256"]) == 64
    assert all(
        {
            "goal-recalled",
            "constraint-recalled",
            "rejected-approach-recalled",
        }
        <= set(case["oracleIds"])
        for case in cases
    )


def test_context_compaction_pairs_differ_only_in_pressure_contract() -> None:
    cases = build_manifest()["cases"]
    grouped: dict[str, dict[str, dict]] = {}
    for case in cases:
        experiment = case["experiment"]
        grouped.setdefault(experiment["pairId"], {})[
            experiment["condition"]
        ] = case

    assert len(grouped) == 12
    for conditions in grouped.values():
        canonical = conditions["canonical"]
        control = conditions["high-window-control"]
        assert canonical["files"] == control["files"]
        assert canonical["authorizedPaths"] == control["authorizedPaths"]
        assert [turn["prompt"] for turn in canonical["turns"]] == [
            turn["prompt"] for turn in control["turns"]
        ]
        assert canonical["turns"][0]["initialHistory"] == control["turns"][0][
            "initialHistory"
        ]
        assert {
            turn["contextWindow"] for turn in canonical["turns"]
        } == {CANONICAL_WINDOW}
        assert {turn["contextWindow"] for turn in control["turns"]} == {
            CONTROL_WINDOW
        }
        assert canonical["oracleIds"][:-1] == control["oracleIds"]
        assert canonical["oracleIds"][-1] == "compaction-observed"


def test_anchor_repair_pilot_is_independent_from_confirmatory_replicates() -> None:
    document = build_pilot_manifest()
    cases = _validate_manifest(document)

    assert document["caseCount"] == 4
    assert document["taskCount"] == 20
    assert document["pairCount"] == 2
    assert {case["experiment"]["replicate"] for case in cases} == {0}
    assert {case["experiment"]["family"] for case in cases} == {
        "port-normalization",
        "retry-delay",
    }


def test_exact_paired_statistics_are_deterministic_and_directional() -> None:
    assert _exact_sign_permutation_p([1.0, 1.0, 1.0]) == 0.25
    assert _exact_sign_permutation_p([0.0, 0.0]) == 1.0
    assert _rank_biserial([3.0, 2.0, 1.0]) == 1.0
    assert _rank_biserial([-3.0, -2.0, -1.0]) == -1.0
    assert _bootstrap_mean_ci([1.0, 2.0, 3.0]) == _bootstrap_mean_ci(
        [1.0, 2.0, 3.0]
    )
    assert _sustained_crossover(
        [12, 19, 25, 30, 34],
        [10, 20, 30, 40, 50],
    ) == 2


def test_analysis_keeps_pair_as_inference_unit() -> None:
    cases = []
    for replicate in range(1, 4):
        for condition, tokens in (
            ("canonical", 100 * replicate),
            ("high-window-control", 150 * replicate),
        ):
            cases.append(
                {
                    "id": f"case-{replicate}-{condition}",
                    "pairId": f"pair-{replicate}",
                    "family": "fixture",
                    "replicate": replicate,
                    "condition": condition,
                    "status": "passed",
                    "inputTokens": tokens,
                    "compactions": 1 if condition == "canonical" else 0,
                    "failedCompactions": 0,
                    "cumulativeInputTokensByTurn": [
                        tokens // 5 * turn for turn in range(1, 6)
                    ],
                }
            )

    report = _analysis(cases)

    assert report["pairCount"] == 3
    assert report["completeUsagePairCount"] == 3
    assert report["canonicalPassed"] == 3
    assert report["controlPassed"] == 3
    assert report["inputTokens"]["pairedPercentReduction"]["median"] == (
        100 / 3
    )
    assert len(report["pairs"]) == 3


def test_deterministic_stress_matrix_covers_scale_round_and_layout() -> None:
    report = build_stress_report()
    stress = report["stressMatrix"]

    assert stress["caseCount"] == 60
    assert stress["passedCases"] == 60
    assert stress["scales"] == [12, 24, 48, 96]
    assert stress["rounds"] == [1, 2, 3, 4, 5]
    assert stress["layouts"] == ["head", "middle", "boundary"]
    assert stress["markerRecall"] == 1.0
    assert stress["summaryChainRetentionRate"] == 1.0
    assert stress["toolPairIntegrityRate"] == 1.0
