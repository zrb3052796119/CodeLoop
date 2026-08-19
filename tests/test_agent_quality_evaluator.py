from __future__ import annotations

import json
from pathlib import Path

from scripts.agent_quality_evaluator import (
    _tool_pairs_intact,
    evaluate_compaction_fidelity,
    evaluate_gate,
    evaluate_north_star,
    evaluate_quality_suite,
    evaluate_skill_routing,
)
from scripts.evaluate_agent_quality import main as quality_cli_main


def test_skill_routing_evaluator_measures_positive_and_abstain_cases(tmp_path) -> None:
    dataset_path = tmp_path / "skill-routing.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "catalog": [
                    {
                        "name": "pytest-debugging",
                        "qualified_name": "pytest-debugging",
                        "description": "Debug pytest failures and runtime errors",
                        "keywords": ["pytest", "debug"],
                        "source": "project",
                    },
                    {
                        "name": "documentation-writing",
                        "qualified_name": "documentation-writing",
                        "description": "Write README and API documentation",
                        "keywords": ["docs", "readme"],
                        "source": "project",
                    },
                ],
                "cases": [
                    {
                        "id": "pytest-positive",
                        "prompt": "debug this pytest failure",
                        "expectedTop1": "pytest-debugging",
                        "expectedRequired": [],
                        "forbidden": ["documentation-writing"],
                    },
                    {
                        "id": "chat-abstain",
                        "prompt": "给我讲个笑话",
                        "expectedTop1": None,
                        "expectedRequired": [],
                        "forbidden": ["pytest-debugging", "documentation-writing"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_skill_routing(dataset_path)

    assert report["caseCount"] == 2
    assert report["positiveCount"] == 1
    assert report["abstainCount"] == 1
    assert report["top1Accuracy"] == 1.0
    assert report["abstainAccuracy"] == 1.0
    assert report["requiredExactMatchRate"] == 1.0
    assert report["forbiddenSelectionRate"] == 0.0
    assert report["failedCaseIds"] == []


def test_quality_gate_distinguishes_current_floor_from_a_target(tmp_path) -> None:
    contract_path = tmp_path / "quality-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "profiles": {
                    "current": {
                        "skillRouting": {
                            "caseCount": {"min": 2},
                            "top1Accuracy": {"min": 0.5},
                            "forbiddenSelectionRate": {"max": 0.5},
                        }
                    },
                    "a": {
                        "skillRouting": {
                            "caseCount": {"min": 50},
                            "top1Accuracy": {"min": 0.9},
                            "forbiddenSelectionRate": {"max": 0.05},
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    report = {
        "skillRouting": {
            "caseCount": 2,
            "top1Accuracy": 1.0,
            "forbiddenSelectionRate": 0.0,
        }
    }

    current = evaluate_gate(report, contract_path, profile="current")
    grade_a = evaluate_gate(report, contract_path, profile="a")

    assert current["passed"] is True
    assert current["failedChecks"] == []
    assert grade_a["passed"] is False
    assert grade_a["failedChecks"] == ["skillRouting.caseCount.min"]


def test_quality_gate_can_pin_a_frozen_dataset_digest(tmp_path) -> None:
    contract_path = tmp_path / "quality-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "profiles": {
                    "current": {
                        "datasets": {
                            "skillRoutingSha256": {"equals": "a" * 64}
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    matching = evaluate_gate(
        {"datasets": {"skillRoutingSha256": "a" * 64}},
        contract_path,
        profile="current",
    )
    changed = evaluate_gate(
        {"datasets": {"skillRoutingSha256": "b" * 64}},
        contract_path,
        profile="current",
    )

    assert matching["passed"] is True
    assert changed["passed"] is False
    assert changed["failedChecks"] == ["datasets.skillRoutingSha256.equals"]


def test_compaction_evaluator_checks_cross_round_task_fidelity(tmp_path) -> None:
    dataset_path = tmp_path / "compaction-fidelity.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cases": [
                    {
                        "id": "two-round-task-state",
                        "rounds": 2,
                        "historyMarkers": [
                            "QG_GOAL_KEEP_API_STABLE",
                            "QG_FACT_TESTS_PASSED",
                            "QG_REJECTED_GLOBAL_CACHE",
                        ],
                        "loadedSkillMarker": "QG_SKILL_REQUIRES_RUFF",
                        "latestUserMarker": "QG_LATEST_DO_NOT_EDIT_TESTS",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_compaction_fidelity(dataset_path)

    assert report["caseCount"] == 1
    assert report["markerRecall"] == 1.0
    assert report["latestUserRetentionRate"] == 1.0
    assert report["loadedSkillRetentionRate"] == 1.0
    assert report["summaryChainRate"] == 1.0
    assert report["toolPairIntegrityRate"] == 1.0
    assert report["nonNegativeSavingsRate"] == 1.0
    assert report["failedCaseIds"] == []


def test_compaction_tool_integrity_rejects_orphans_in_either_direction() -> None:
    paired = [
        {"role": "assistant_tool_call", "toolUseId": "tool-1"},
        {"role": "tool_result", "toolUseId": "tool-1"},
    ]

    assert _tool_pairs_intact(paired) is True
    assert _tool_pairs_intact(paired[:1]) is False
    assert _tool_pairs_intact(paired[1:]) is False


def test_north_star_evaluator_strictly_joins_tasks_and_results(tmp_path) -> None:
    manifest_path = tmp_path / "north-star-manifest.json"
    results_path = tmp_path / "north-star-results.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "suiteId": "quality-test-v1",
                "cases": [
                    {
                        "id": "read-architecture",
                        "category": "code-understanding",
                        "mutability": "read_only",
                        "oracleIds": ["call-flow"],
                    },
                    {
                        "id": "fix-bug",
                        "category": "bug-fix",
                        "mutability": "write",
                        "oracleIds": ["tests-pass", "minimal-diff"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    results_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "suiteId": "quality-test-v1",
                "results": [
                    {
                        "id": "read-architecture",
                        "status": "passed",
                        "verificationPassed": True,
                        "unsafeActionCount": 0,
                        "userInterventionCount": 0,
                        "durationMs": 1200,
                        "modelCalls": 2,
                        "inputTokens": 100,
                        "outputTokens": 40,
                        "runId": "run_" + "a" * 32,
                        "passedOracleIds": ["call-flow"],
                    },
                    {
                        "id": "fix-bug",
                        "status": "failed",
                        "verificationPassed": False,
                        "unsafeActionCount": 0,
                        "userInterventionCount": 1,
                        "durationMs": 2400,
                        "modelCalls": 4,
                        "inputTokens": 300,
                        "outputTokens": 80,
                        "runId": "run_" + "b" * 32,
                        "passedOracleIds": ["minimal-diff"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_north_star(manifest_path, results_path)

    assert report["caseCount"] == 2
    assert report["categoryCount"] == 2
    assert report["taskSuccessRate"] == 0.5
    assert report["verifiedSuccessRate"] == 0.5
    assert report["oraclePassRate"] == 0.666667
    assert report["unsafeActionRate"] == 0.0
    assert report["interventionRate"] == 0.5
    assert report["evidenceCompleteRate"] == 1.0
    assert report["durationTelemetryCoverageRate"] == 1.0
    assert report["tokenTelemetryCoverageRate"] == 1.0
    assert report["failedCaseIds"] == ["fix-bug"]


def test_quality_suite_is_a_deterministic_content_addressed_report() -> None:
    fixture_root = "tests/fixtures/agent_quality"

    first = evaluate_quality_suite(fixture_root)
    second = evaluate_quality_suite(fixture_root)

    assert first == second
    assert first["mode"] == "offline-deterministic"
    assert first["remoteCallCount"] == 0
    assert first["skillRouting"]["caseCount"] == 36
    assert first["compactionFidelity"]["caseCount"] == 8
    assert first["northStar"]["caseCount"] == 4
    assert len(first["datasets"]["skillRoutingSha256"]) == 64
    assert len(first["datasets"]["compactionFidelitySha256"]) == 64
    assert len(first["datasets"]["northStarManifestSha256"]) == 64
    assert len(first["datasets"]["northStarResultsSha256"]) == 64
    assert "cases" not in first["skillRouting"]
    assert "cases" not in first["compactionFidelity"]
    assert "cases" not in first["northStar"]


def test_checked_in_quality_baseline_matches_the_frozen_suite() -> None:
    report = evaluate_quality_suite("tests/fixtures/agent_quality")
    baseline = json.loads(
        Path("artifacts/agent-quality-baseline.json").read_text(encoding="utf-8")
    )

    assert baseline["datasets"] == report["datasets"]
    assert baseline["evaluationPayloadSha256"] == report["payloadSha256"]
    for dimension in ("skillRouting", "compactionFidelity", "northStar"):
        for metric, expected in baseline[dimension].items():
            assert report[dimension][metric] == expected


def test_quality_suite_can_evaluate_fresh_north_star_results(tmp_path) -> None:
    fixture_root = Path("tests/fixtures/agent_quality")
    baseline_results = json.loads(
        (fixture_root / "north-star-baseline-results.json").read_text(encoding="utf-8")
    )
    baseline_results["results"][0]["status"] = "failed"
    baseline_results["results"][0]["verificationPassed"] = False
    baseline_results["results"][0]["passedOracleIds"] = []
    fresh_results_path = tmp_path / "fresh-results.json"
    fresh_results_path.write_text(json.dumps(baseline_results), encoding="utf-8")

    recorded = evaluate_quality_suite(fixture_root)
    fresh = evaluate_quality_suite(
        fixture_root,
        north_star_results_path=fresh_results_path,
    )

    assert fresh["northStar"]["taskSuccessRate"] == 0.75
    assert fresh["northStar"]["oraclePassRate"] == 0.764706
    assert (
        fresh["datasets"]["northStarResultsSha256"]
        != recorded["datasets"]["northStarResultsSha256"]
    )


def test_quality_cli_passes_current_and_rejects_unmet_a_profile(capsys) -> None:
    assert quality_cli_main(["--profile", "current"]) == 0
    current_output = json.loads(capsys.readouterr().out)
    assert current_output["gate"]["passed"] is True

    assert quality_cli_main(["--profile", "a"]) == 1
    grade_a_output = json.loads(capsys.readouterr().out)
    assert grade_a_output["gate"]["passed"] is False
    assert "skillRouting.caseCount.min" in grade_a_output["gate"]["failedChecks"]


def test_a_profile_cli_accepts_fresh_north_star_evidence(tmp_path, capsys) -> None:
    source = Path("tests/fixtures/agent_quality/north-star-baseline-results.json")
    fresh_results = tmp_path / "north-star-results.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["recordedAt"] = "2026-08-20T00:00:00Z"
    fresh_results.write_text(json.dumps(payload), encoding="utf-8")

    assert quality_cli_main(
        [
            "--profile",
            "a",
            "--north-star-results",
            str(fresh_results),
        ]
    ) == 1
    output = json.loads(capsys.readouterr().out)

    assert output["northStar"]["caseCount"] == 4
    assert all(
        check["id"] != "datasets.northStarResultsSha256.equals"
        for check in output["gate"]["checks"]
    )
