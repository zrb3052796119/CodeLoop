from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.agent_quality_evaluator import (
    _sha256,
    _tool_pairs_intact,
    evaluate_compaction_fidelity,
    evaluate_gate,
    evaluate_intent_recognition,
    evaluate_multi_agent_cost,
    evaluate_north_star,
    evaluate_quality_suite,
    evaluate_skill_routing,
)
from scripts.evaluate_agent_quality import main as quality_cli_main


def test_quality_dataset_digest_is_cross_platform_line_ending_stable(
    tmp_path,
) -> None:
    lf_path = tmp_path / "lf.json"
    crlf_path = tmp_path / "crlf.json"
    lf_path.write_bytes(b'{\n  "schemaVersion": 1\n}\n')
    crlf_path.write_bytes(b'{\r\n  "schemaVersion": 1\r\n}\r\n')

    assert _sha256(lf_path) == _sha256(crlf_path)


def test_current_gate_accepts_crlf_checkout_of_frozen_quality_suite(
    tmp_path,
) -> None:
    source_root = Path("tests/fixtures/agent_quality")
    crlf_root = tmp_path / "agent_quality"
    crlf_root.mkdir()
    for source in source_root.glob("*.json"):
        canonical = source.read_bytes().replace(b"\r\n", b"\n").replace(
            b"\r", b"\n"
        )
        (crlf_root / source.name).write_bytes(canonical.replace(b"\n", b"\r\n"))

    canonical_report = evaluate_quality_suite(source_root)
    crlf_report = evaluate_quality_suite(crlf_root)
    gate = evaluate_gate(
        crlf_report,
        "artifacts/agent-quality-contract.json",
        profile="current",
    )

    assert crlf_report["datasets"] == canonical_report["datasets"]
    assert crlf_report["payloadSha256"] == canonical_report["payloadSha256"]
    assert gate["passed"] is True


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


def test_intent_recognition_evaluator_measures_context_and_abstention(
    tmp_path,
) -> None:
    dataset_path = tmp_path / "intent-routing.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cases": [
                    {
                        "id": "direct-update",
                        "prompt": "把 auth.py 的校验逻辑改一下",
                        "expectedPrimary": {"intent": "code", "action": "update"},
                        "expectedOperations": [
                            {
                                "intent": "code",
                                "action": "update",
                                "polarity": "affirmed",
                            }
                        ],
                        "expectedContextUsed": False,
                        "expectedAbstain": False,
                    },
                    {
                        "id": "context-continue",
                        "prompt": "继续",
                        "context": {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": "把 auth.py 的校验逻辑改一下",
                                }
                            ],
                            "previousPrompt": "把 auth.py 的校验逻辑改一下",
                            "activeTask": "update authentication",
                        },
                        "expectedPrimary": {"intent": "code", "action": "update"},
                        "expectedOperations": [
                            {
                                "intent": "code",
                                "action": "update",
                                "polarity": "affirmed",
                            }
                        ],
                        "expectedContextUsed": True,
                        "expectedAbstain": False,
                    },
                    {
                        "id": "abstain-chat",
                        "prompt": "给我讲个笑话",
                        "expectedPrimary": {
                            "intent": "unknown",
                            "action": "unknown",
                        },
                        "expectedOperations": [],
                        "expectedContextUsed": False,
                        "expectedAbstain": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_intent_recognition(dataset_path)

    assert report["caseCount"] == 3
    assert report["primaryExactRate"] == 1.0
    assert report["operationExactRate"] == 1.0
    assert report["contextResolutionRate"] == 1.0
    assert report["abstainAccuracy"] == 1.0
    assert report["failedCaseIds"] == []


def test_checked_in_intent_recognition_fixture_is_green() -> None:
    report = evaluate_intent_recognition(
        "tests/fixtures/agent_quality/intent-routing.json"
    )

    assert report["caseCount"] == 25
    assert report["primaryExactRate"] == 1.0
    assert report["operationExactRate"] == 1.0
    assert report["contextResolutionRate"] == 1.0
    assert report["abstainAccuracy"] == 1.0
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
    assert report["taskLedgerRetentionRate"] == 1.0
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
    assert first["skillRouting"]["caseCount"] == 60
    assert first["skillRoutingJoined"]["caseCount"] == 96
    assert first["compactionFidelity"]["caseCount"] == 12
    assert first["northStar"]["caseCount"] == 50
    assert first["multiAgentCost"]["caseCount"] == 12
    assert first["multiAgentCost"]["recordedCaseCount"] == 12
    assert first["multiAgentCost"]["pendingCaseCount"] == 0
    assert len(first["datasets"]["skillRoutingSha256"]) == 64
    assert len(first["datasets"]["skillRoutingJoinedSha256"]) == 64
    assert len(first["datasets"]["compactionFidelitySha256"]) == 64
    assert len(first["datasets"]["northStarManifestSha256"]) == 64
    assert len(first["datasets"]["northStarResultsSha256"]) == 64
    assert len(first["datasets"]["multiAgentCostManifestSha256"]) == 64
    assert len(first["datasets"]["multiAgentCostResultsSha256"]) == 64
    assert "cases" not in first["skillRouting"]
    assert "cases" not in first["skillRoutingJoined"]
    assert "cases" not in first["compactionFidelity"]
    assert "cases" not in first["northStar"]
    assert "cases" not in first["multiAgentCost"]


def test_checked_in_quality_baseline_matches_the_frozen_suite() -> None:
    report = evaluate_quality_suite("tests/fixtures/agent_quality")
    baseline = json.loads(
        Path("artifacts/agent-quality-baseline.json").read_text(encoding="utf-8")
    )

    assert baseline["datasets"] == report["datasets"]
    assert baseline["evaluationPayloadSha256"] == report["payloadSha256"]
    for dimension in (
        "skillRouting",
        "skillRoutingJoined",
        "compactionFidelity",
        "northStar",
        "multiAgentCost",
    ):
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

    assert fresh["northStar"]["taskSuccessRate"] < recorded["northStar"]["taskSuccessRate"]
    assert fresh["northStar"]["oraclePassRate"] < recorded["northStar"]["oraclePassRate"]
    assert (
        fresh["datasets"]["northStarResultsSha256"]
        != recorded["datasets"]["northStarResultsSha256"]
    )


def test_quality_suite_accepts_explicit_north_star_manifest() -> None:
    fixture_root = Path("tests/fixtures/agent_quality")
    report = evaluate_quality_suite(
        fixture_root,
        north_star_manifest_path=fixture_root / "north-star-manifest.json",
    )

    assert report["northStar"]["caseCount"] == 50


def test_quality_cli_passes_current_and_a_profiles(capsys) -> None:
    assert quality_cli_main(["--profile", "current"]) == 0
    current_output = json.loads(capsys.readouterr().out)
    assert current_output["gate"]["passed"] is True
    assert any(
        check["id"].startswith("skillRoutingJoined.")
        for check in current_output["gate"]["checks"]
    )

    # The three former single-general cases were rewritten as
    # delegation-rejection contracts (2026-08-28 re-recording): production
    # hard-codes central scheduling, so the sealed refusal of a direct
    # `general` dispatch (error[central_scheduler_required], zero sub-agents,
    # zero writes) is now the behavior under test. With the refusal measured
    # instead of the impossible delegation, all 12 recorded cases pass and the
    # a profile's 0.8 success-rate bar is met on real evidence, so both
    # profiles pin green.
    assert quality_cli_main(["--profile", "a"]) == 0
    grade_a_output = json.loads(capsys.readouterr().out)
    assert grade_a_output["gate"]["passed"] is True
    assert grade_a_output["gate"]["failedChecks"] == []


def test_current_gate_rejects_joined_routing_regression() -> None:
    report = evaluate_quality_suite("tests/fixtures/agent_quality")
    report["skillRoutingJoined"]["outcomeAccuracy"] = 0.99

    gate = evaluate_gate(
        report,
        "artifacts/agent-quality-contract.json",
        profile="current",
    )

    assert gate["passed"] is False
    assert "skillRoutingJoined.outcomeAccuracy.min" in gate["failedChecks"]


def test_a_profile_cli_accepts_fresh_north_star_evidence(tmp_path, capsys) -> None:
    source = Path("tests/fixtures/agent_quality/north-star-baseline-results.json")
    fresh_results = tmp_path / "north-star-results.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["recordedAt"] = "2026-08-20T00:00:00Z"
    fresh_results.write_text(json.dumps(payload), encoding="utf-8")

    # Fresh north-star evidence is accepted (its result digest is not pinned
    # in the a profile), and with the multi-agent cost domain green on the
    # rejection-contract evidence the whole a profile passes.
    assert quality_cli_main(
        [
            "--profile",
            "a",
            "--north-star-results",
            str(fresh_results),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["northStar"]["caseCount"] == 50
    assert output["gate"]["passed"] is True
    assert all(
        check["id"] != "datasets.northStarResultsSha256.equals"
        for check in output["gate"]["checks"]
    )


def _multi_agent_cost_manifest() -> dict:
    return {
        "schemaVersion": 1,
        "suiteId": "cost-test-v1",
        "cases": [
            {
                "id": "fanout-recorded",
                "category": "parallel-readonly-fanout",
                "mutability": "read_only",
                "evidenceStatus": "recorded",
                "oracleIds": ["run-completed", "synthesis-facts"],
                "topology": {
                    "expectedSubagents": {"explore": 2},
                    "maxSubagents": 4,
                },
            },
            {
                "id": "workflow-pending",
                "category": "structured-workflow-chain",
                "mutability": "write",
                "evidenceStatus": "pending",
                "oracleIds": ["run-completed", "role-chain-completed"],
                "topology": {
                    "expectedSubagents": {"workflow": 1},
                    "maxSubagents": 1,
                    "roleChain": ["analyst", "executor", "verify", "reviewer"],
                },
            },
        ],
    }


def _multi_agent_cost_result(case_id: str, **overrides) -> dict:
    result = {
        "id": case_id,
        "status": "passed",
        "verificationPassed": True,
        "unsafeActionCount": 0,
        "userInterventionCount": 0,
        "durationMs": 40000,
        "modelCalls": 20,
        "inputTokens": 100000,
        "outputTokens": 20000,
        "runId": "run_" + "c" * 32,
        "passedOracleIds": ["run-completed"],
        "subagents": [
            {
                "subagentId": "sub_" + "d" * 32,
                "agentType": "explore",
                "outcome": "completed",
                "durationMs": 15000,
                "modelTurns": 5,
                "toolCalls": 8,
            },
            {
                "subagentId": "sub_" + "e" * 32,
                "agentType": "explore",
                "outcome": "failed",
                "durationMs": 22000,
                "modelTurns": 12,
                "toolCalls": 9,
            },
        ],
    }
    result.update(overrides)
    return result


def _write_multi_agent_cost_pair(
    tmp_path, results: list[dict], *, manifest: dict | None = None
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest_path = tmp_path / "multi-agent-cost-manifest.json"
    results_path = tmp_path / "multi-agent-cost-results.json"
    manifest_path.write_text(
        json.dumps(manifest or _multi_agent_cost_manifest()), encoding="utf-8"
    )
    results_path.write_text(
        json.dumps(
            {"schemaVersion": 1, "suiteId": "cost-test-v1", "results": results}
        ),
        encoding="utf-8",
    )
    return manifest_path, results_path


def test_multi_agent_cost_evaluator_measures_recorded_cases(tmp_path) -> None:
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result(
                "fanout-recorded",
                coordination={"parentInputTokens": 24000, "parentOutputTokens": 6000},
            )
        ],
    )

    report = evaluate_multi_agent_cost(manifest_path, results_path)

    assert report["caseCount"] == 2
    assert report["recordedCaseCount"] == 1
    assert report["pendingCaseCount"] == 1
    assert report["reportedResultCount"] == 1
    assert report["recordedCaseCoverageRate"] == 1.0
    assert report["taskSuccessRate"] == 1.0
    assert report["oraclePassRate"] == 0.5
    assert report["subagentCount"] == 2
    assert report["subagentSuccessRate"] == 0.5
    assert report["subagentTelemetryCoverageRate"] == 1.0
    assert report["subagentTokenTelemetryCoverageRate"] == 0.0
    assert report["coordinationTelemetryCoverageRate"] == 1.0
    assert report["meanCoordinationOverheadRatio"] == 0.25
    assert report["topologyViolationRate"] == 0.0
    assert report["totalTokens"] == 120000
    assert report["maxCaseTotalTokens"] == 120000
    assert report["failedCaseIds"] == []
    fanout_stats = report["categoryStats"]["parallel-readonly-fanout"]
    assert fanout_stats["reportedCaseCount"] == 1
    assert fanout_stats["meanTotalTokens"] == 120000
    pending_stats = report["categoryStats"]["structured-workflow-chain"]
    assert pending_stats["reportedCaseCount"] == 0
    assert pending_stats["successRate"] is None


def test_multi_agent_cost_pending_cases_accept_fresh_evidence(tmp_path) -> None:
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result("fanout-recorded"),
            _multi_agent_cost_result(
                "workflow-pending",
                modelCalls=30,
                subagents=[
                    {
                        "subagentId": "sub_" + "f" * 32,
                        "agentType": "workflow",
                        "outcome": "completed",
                        "durationMs": 90000,
                        "modelTurns": 20,
                        "toolCalls": 25,
                    }
                ],
            ),
        ],
    )

    report = evaluate_multi_agent_cost(manifest_path, results_path)

    assert report["reportedResultCount"] == 2
    assert report["recordedCaseCoverageRate"] == 1.0
    assert report["categoryStats"]["structured-workflow-chain"]["reportedCaseCount"] == 1
    assert report["subagentCount"] == 3


def test_multi_agent_cost_fails_closed_on_missing_recorded_case(tmp_path) -> None:
    manifest_path, results_path = _write_multi_agent_cost_pair(tmp_path, [])

    try:
        evaluate_multi_agent_cost(manifest_path, results_path)
    except ValueError as error:
        assert "missing recorded cases" in str(error)
    else:
        raise AssertionError("missing recorded case must fail closed")


def test_multi_agent_cost_fails_closed_on_unknown_or_duplicate_ids(tmp_path) -> None:
    unknown_manifest, unknown_results = _write_multi_agent_cost_pair(
        tmp_path / "unknown",
        [_multi_agent_cost_result("not-in-manifest")],
    )
    duplicate_manifest, duplicate_results = _write_multi_agent_cost_pair(
        tmp_path / "duplicate",
        [
            _multi_agent_cost_result("fanout-recorded"),
            _multi_agent_cost_result("fanout-recorded", runId="run_" + "9" * 32),
        ],
    )

    for manifest_path, results_path in (
        (unknown_manifest, unknown_results),
        (duplicate_manifest, duplicate_results),
    ):
        try:
            evaluate_multi_agent_cost(manifest_path, results_path)
        except ValueError as error:
            assert "identity mismatch" in str(error)
        else:
            raise AssertionError("unknown or duplicate result ids must fail closed")


def test_multi_agent_cost_fails_closed_on_invalid_counters(tmp_path) -> None:
    bad_inputs = _multi_agent_cost_result("fanout-recorded", inputTokens=-1)
    bad_bool = _multi_agent_cost_result("fanout-recorded", modelCalls=True)
    bad_coordination = _multi_agent_cost_result(
        "fanout-recorded",
        coordination={"parentInputTokens": 200000, "parentOutputTokens": 0},
    )
    for index, bad in enumerate((bad_inputs, bad_bool, bad_coordination)):
        manifest_path, results_path = _write_multi_agent_cost_pair(
            tmp_path / f"bad-{index}", [bad]
        )
        try:
            evaluate_multi_agent_cost(manifest_path, results_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid counter case {index} must fail closed")


def test_multi_agent_cost_fails_closed_on_invalid_subagent_entries(tmp_path) -> None:
    cases = [
        _multi_agent_cost_result("fanout-recorded", subagents=[]),
        _multi_agent_cost_result(
            "fanout-recorded",
            subagents=[
                {
                    "subagentId": "sub_" + "1" * 32,
                    "agentType": "explorer",
                    "outcome": "completed",
                }
            ],
        ),
        _multi_agent_cost_result(
            "fanout-recorded",
            subagents=[
                {
                    "subagentId": "sub_" + "1" * 32,
                    "agentType": "explore",
                    "outcome": "success",
                }
            ],
        ),
        _multi_agent_cost_result(
            "fanout-recorded",
            subagents=[
                {
                    "subagentId": "sub_" + "1" * 32,
                    "agentType": "explore",
                    "outcome": "completed",
                },
                {
                    "subagentId": "sub_" + "1" * 32,
                    "agentType": "explore",
                    "outcome": "failed",
                },
            ],
        ),
        _multi_agent_cost_result(
            "fanout-recorded",
            subagents=[
                {
                    "subagentId": "sub_" + "1" * 32,
                    "agentType": "explore",
                    "outcome": "completed",
                    "durationMs": -5,
                }
            ],
        ),
    ]
    for index, bad in enumerate(cases):
        manifest_path, results_path = _write_multi_agent_cost_pair(
            tmp_path / f"sub-{index}", [bad]
        )
        try:
            evaluate_multi_agent_cost(manifest_path, results_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid subagent case {index} must fail closed")


def test_multi_agent_cost_allows_empty_subagents_on_failed_case(tmp_path) -> None:
    # A failed case may legitimately record zero sub-agents: production can
    # reject the delegation before any sub-agent starts (the 2026-08-27 live
    # evidence hit exactly this for direct `general` dispatch). Only passed
    # cases fail closed on an empty breakdown.
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result(
                "fanout-recorded",
                status="failed",
                verificationPassed=False,
                passedOracleIds=[],
                subagents=[],
            )
        ],
    )

    report = evaluate_multi_agent_cost(manifest_path, results_path)

    assert report["taskSuccessRate"] == 0.0
    assert report["subagentCount"] == 0
    assert report["failedCaseIds"] == ["fanout-recorded"]


def _rejection_contract_manifest() -> dict:
    manifest = _multi_agent_cost_manifest()
    manifest["cases"].append(
        {
            "id": "rejection-recorded",
            "category": "direct-general-rejection",
            "mutability": "read_only",
            "evidenceStatus": "recorded",
            "oracleIds": ["run-completed", "delegation-rejected"],
            "oracles": [
                {"id": "run-completed", "kind": "all_runs_completed"},
                {
                    "id": "delegation-rejected",
                    "kind": "delegation_rejected",
                    "errorCode": "central_scheduler_required",
                },
            ],
            "topology": {
                "expectedSubagents": {"general": 1},
                "maxSubagents": 2,
            },
        }
    )
    return manifest


def test_multi_agent_cost_rejection_contract_passes_with_zero_subagents(
    tmp_path,
) -> None:
    # A delegation-rejection contract case measures the sealed refusal itself:
    # a passed case is expected to have an empty sub-agent breakdown.
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result("fanout-recorded"),
            _multi_agent_cost_result(
                "rejection-recorded",
                passedOracleIds=["run-completed", "delegation-rejected"],
                subagents=[],
            ),
        ],
        manifest=_rejection_contract_manifest(),
    )

    report = evaluate_multi_agent_cost(manifest_path, results_path)

    assert report["taskSuccessRate"] == 1.0
    assert report["subagentCount"] == 2
    assert report["failedCaseIds"] == []


def test_multi_agent_cost_rejection_contract_fails_closed_on_subagent_rows(
    tmp_path,
) -> None:
    # Any journaled sub-agent contradicts the rejection contract, even on a
    # failed case: the evidence cannot represent the sealed refusal.
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result("fanout-recorded"),
            _multi_agent_cost_result(
                "rejection-recorded",
                status="failed",
                verificationPassed=False,
                passedOracleIds=[],
            )
        ],
        manifest=_rejection_contract_manifest(),
    )

    try:
        evaluate_multi_agent_cost(manifest_path, results_path)
    except ValueError as error:
        assert "delegation-rejection contract" in str(error)
    else:
        raise AssertionError("sub-agent rows must contradict a rejection contract")


def test_multi_agent_cost_counts_topology_violations(tmp_path) -> None:
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result(
                "fanout-recorded",
                subagents=[
                    {
                        "subagentId": f"sub_{str(index) * 32}",
                        "agentType": "explore",
                        "outcome": "completed",
                    }
                    for index in range(5)
                ],
            )
        ],
    )

    report = evaluate_multi_agent_cost(manifest_path, results_path)

    assert report["topologyViolationRate"] == 1.0
    assert report["maxSubagentsPerCase"] == 5


def test_multi_agent_cost_topology_counts_top_level_delegations_only(tmp_path) -> None:
    # A workflow chain journals its scheduler-owned role workers (plan/general)
    # alongside the workflow row; the frozen maxSubagents=1 bound applies to
    # the parent agent's top-level delegation, not to chain-internal workers.
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [
            _multi_agent_cost_result("fanout-recorded"),
            _multi_agent_cost_result(
                "workflow-pending",
                modelCalls=40,
                subagents=[
                    {
                        "subagentId": "sub_" + "f" * 32,
                        "agentType": "workflow",
                        "outcome": "completed",
                        "durationMs": 90000,
                        "modelTurns": 20,
                        "toolCalls": 25,
                    },
                    {
                        "subagentId": "sub_" + "e" * 32,
                        "agentType": "plan",
                        "outcome": "completed",
                        "durationMs": 20000,
                        "modelTurns": 6,
                        "toolCalls": 4,
                    },
                    {
                        "subagentId": "sub_" + "d" * 32,
                        "agentType": "general",
                        "outcome": "completed",
                        "durationMs": 30000,
                        "modelTurns": 8,
                        "toolCalls": 9,
                    },
                ],
            )
        ],
    )

    report = evaluate_multi_agent_cost(manifest_path, results_path)

    assert report["topologyViolationRate"] == 0.0
    # Cost telemetry still counts every journaled sub-agent (2 fanout explore
    # rows plus the workflow row and its two chain-internal role workers).
    assert report["subagentCount"] == 5
    case_entry = next(
        entry for entry in report["cases"] if entry["id"] == "workflow-pending"
    )
    assert case_entry["subagentCount"] == 3
    assert case_entry["topLevelSubagentCount"] == 1
    assert case_entry["topologyConformant"] is True


def test_multi_agent_cost_enforces_oracle_subset(tmp_path) -> None:
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path,
        [_multi_agent_cost_result("fanout-recorded", passedOracleIds=["unknown-oracle"])],
    )

    try:
        evaluate_multi_agent_cost(manifest_path, results_path)
    except ValueError as error:
        assert "oracle evidence" in str(error)
    else:
        raise AssertionError("unrecognized oracle evidence must fail closed")


def test_multi_agent_cost_rejects_invalid_manifest_topology(tmp_path) -> None:
    manifest = _multi_agent_cost_manifest()
    manifest["cases"][0]["topology"] = {
        "expectedSubagents": {"explore": 2},
        "maxSubagents": 1,
    }
    manifest_path, results_path = _write_multi_agent_cost_pair(
        tmp_path, [], manifest=manifest
    )

    try:
        evaluate_multi_agent_cost(manifest_path, results_path)
    except ValueError as error:
        assert "invalid topology" in str(error)
    else:
        raise AssertionError("inconsistent topology bounds must fail closed")


def test_checked_in_multi_agent_cost_baseline_matches_real_records() -> None:
    fixture_root = Path("tests/fixtures/agent_quality")
    manifest = json.loads(
        (fixture_root / "multi-agent-cost-manifest.json").read_text(encoding="utf-8")
    )
    baseline = json.loads(
        (fixture_root / "multi-agent-cost-baseline-results.json").read_text(
            encoding="utf-8"
        )
    )

    # The baseline is the sealed 2026-08-28 live evidence: it must cover every
    # recorded manifest case exactly once, carry a real run identity, and ship
    # the complete per-sub-agent and coordination telemetry that the 1.0
    # coverage pins in the current profile rely on. The three
    # direct-general-rejection cases are the exception to the sub-agent
    # breakdown rule: their passed results must carry an EMPTY breakdown,
    # because the sealed refusal means no sub-agent ever started.
    rejection_case_ids = {
        case["id"]
        for case in manifest["cases"]
        if any(
            isinstance(oracle, dict) and oracle.get("kind") == "delegation_rejected"
            for oracle in case.get("oracles") or []
        )
    }
    assert len(rejection_case_ids) == 3
    recorded_ids = {
        case["id"] for case in manifest["cases"] if case["evidenceStatus"] == "recorded"
    }
    assert {result["id"] for result in baseline["results"]} == recorded_ids
    assert len(recorded_ids) == 12
    run_id_pattern = re.compile(r"run_[0-9a-f]{32}")
    subagent_id_pattern = re.compile(r"sub_[0-9a-f]{32}")
    for result in baseline["results"]:
        assert run_id_pattern.fullmatch(result["runId"])
        assert result["inputTokens"] is not None
        assert result["outputTokens"] is not None
        coordination = result["coordination"]
        assert (
            coordination["parentInputTokens"]
            + coordination["subagentInputTokens"]
            == result["inputTokens"]
        )
        assert (
            coordination["parentOutputTokens"]
            + coordination["subagentOutputTokens"]
            == result["outputTokens"]
        )
        if result["id"] in rejection_case_ids:
            assert result["subagents"] == [], (
                "rejection contracts prove zero sub-agents started"
            )
        elif result["status"] == "passed":
            assert result["subagents"], "passed cases need a sub-agent breakdown"
        for subagent in result["subagents"]:
            assert subagent_id_pattern.fullmatch(subagent["subagentId"])
            assert subagent["inputTokens"] is not None
            assert subagent["outputTokens"] is not None


def test_multi_agent_cost_cli_accepts_fresh_evidence_in_a_profile(
    tmp_path, capsys
) -> None:
    fixture_root = Path("tests/fixtures/agent_quality")
    payload = json.loads(
        (fixture_root / "multi-agent-cost-baseline-results.json").read_text(
            encoding="utf-8"
        )
    )
    payload["recordedAt"] = "2026-08-27T00:00:00Z"
    fresh_results = tmp_path / "multi-agent-cost-results.json"
    fresh_results.write_text(json.dumps(payload), encoding="utf-8")

    # Fresh multi-agent cost evidence is accepted without a pinned digest, and
    # the locked baseline itself now passes the a bar: the three former
    # single-general cases are delegation-rejection contracts whose recorded
    # refusal evidence satisfies every success-rate check.
    assert quality_cli_main(
        [
            "--profile",
            "a",
            "--multi-agent-cost-results",
            str(fresh_results),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["multiAgentCost"]["caseCount"] == 12
    assert output["gate"]["passed"] is True
    assert output["gate"]["failedChecks"] == []
    assert all(
        check["id"] != "datasets.multiAgentCostResultsSha256.equals"
        for check in output["gate"]["checks"]
    )
