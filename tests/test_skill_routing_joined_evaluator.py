from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import scripts.skill_routing_joined_evaluator as joined_evaluator
from minicode.skill_router import RoutedSkill
from scripts.skill_routing_joined_evaluator import evaluate_joined_skill_routing

_FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_quality"
    / "skill-routing-joined.json"
)
_STAGE_NAMES = {
    "operations",
    "context",
    "candidateRecall",
    "candidateExact",
    "operationCandidateRecall",
    "operationCandidateExact",
    "finalSkills",
    "explicitAuthority",
    "forbiddenCandidates",
    "forbiddenFinal",
    "outcome",
    "promptProjection",
}
_ROUTING_DECISION_STAGES = {
    "candidateExact",
    "operationCandidateExact",
    "finalSkills",
    "explicitAuthority",
    "forbiddenCandidates",
    "forbiddenFinal",
    "outcome",
    "promptProjection",
}


def _load_fixture() -> dict[str, Any]:
    document = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write_dataset(
    tmp_path: Path,
    document: dict[str, Any],
    *,
    name: str,
) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _case_result(report: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(case for case in report["cases"] if case["id"] == case_id)


def test_joined_skill_routing_fixture_is_deterministic_offline_and_detailed() -> None:
    fixture = _load_fixture()

    first = evaluate_joined_skill_routing(_FIXTURE_PATH)
    second = evaluate_joined_skill_routing(_FIXTURE_PATH)

    assert first == second
    assert first["schemaVersion"] == 1
    assert first["evaluator"] == "skill-routing-joined"
    assert first["mode"] == "offline-production-shaped"
    assert first["remoteCallCount"] == 0
    assert first["caseCount"] == 96
    assert first["topK"] == 5
    assert first["catalogSkillCount"] == 12
    assert first["capabilityCount"] == 9
    assert first["routeCaseCount"] == 80
    assert first["contextCaseCount"] == 28
    assert first["contextAblationCaseCount"] == 28
    assert first["intentSensitivityCaseCount"] == 10
    assert first["compoundCaseCount"] == 21
    assert first["minimalPairCount"] == 24
    assert first["samePromptContextPairCount"] == 12
    assert first["noSkillCaseCount"] == 12
    assert first["clarifyCaseCount"] == 3
    assert first["abstainCaseCount"] == 1
    assert first["expectedOutcomeCounts"] == {
        "abstain": 1,
        "clarify": 3,
        "no_skill": 12,
        "route": 80,
    }
    assert {
        category: stats["caseCount"]
        for category, stats in first["categoryStats"].items()
    } == {
        "authority-negation-correction": 16,
        "compound-partial-semantic": 20,
        "context-continuation-switch": 24,
        "no-skill-clarify": 12,
        "same-topic-intent": 24,
    }

    expected_ids = [case["id"] for case in fixture["cases"]]
    details = first["cases"]
    assert [case["id"] for case in details] == expected_ids
    assert set(first["failedCaseIds"]).issubset(expected_ids)
    assert all(set(case["stagePass"]) == _STAGE_NAMES for case in details)
    assert all(
        case["firstFailureStage"] is None
        if case["passed"]
        else case["firstFailureStage"] in _STAGE_NAMES
        for case in details
    )
    assert all(
        set(trace_row) == {
            "skill",
            "operationIndexes",
            "explicitlyRequested",
        }
        for case in details
        for trace_row in case["candidateTrace"]
    )
    assert all(isinstance(case["routingDecisionPassed"], bool) for case in details)
    assert all(
        case["routingDecisionPassed"]
        == all(case["stagePass"][stage] for stage in _ROUTING_DECISION_STAGES)
        for case in details
    )
    assert all(
        isinstance(case["expectedPromptSkills"], list)
        and isinstance(case["actualPromptSkills"], list)
        for case in details
    )
    assert first["routingDecisionPassRate"] == _ratio(
        sum(case["routingDecisionPassed"] for case in details),
        first["caseCount"],
    )
    operation_correct_cases = [
        case for case in details if case["stagePass"]["operations"]
    ]
    assert first["operationCorrectCaseCount"] == len(
        operation_correct_cases
    )
    assert first["skillDecisionPassGivenCorrectOperations"] == _ratio(
        sum(case["routingDecisionPassed"] for case in operation_correct_cases),
        len(operation_correct_cases),
    )
    assert first["routeOperationExactRate"] == _ratio(
        sum(
            case["stagePass"]["operations"]
            for case in details
            if case["expectedOutcome"] == "route"
        ),
        first["routeCaseCount"],
    )
    assert first["operationCandidateExactRate"] == _ratio(
        sum(case["stagePass"]["operationCandidateExact"] for case in details),
        first["caseCount"],
    )
    assert first["candidateExactRate"] == _ratio(
        sum(case["stagePass"]["candidateExact"] for case in details),
        first["caseCount"],
    )
    assert first["operationCandidatePrecisionAtK"] == 1.0
    assert first["compoundOperationCandidateExactRate"] == 1.0


def test_operation_mapping_exact_rejects_extra_candidate_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = joined_evaluator.route_skill_turn
    target_prompt = (
        "先定位接口的 Python CPU 热点，同时优化已经确认全表扫描的 orders 查询。"
    )

    def add_spurious_lane(
        request: object,
        adapters: object,
        *,
        controls: object = None,
    ) -> object:
        decision = original(request, adapters, controls=controls)
        if request.prompt == target_prompt:
            for candidate in decision.routing.inferred_candidates:
                if candidate.qualified_name == "performance-profiling":
                    candidate.operation_indexes = (0, 1)
        return decision

    monkeypatch.setattr(
        joined_evaluator,
        "route_skill_turn",
        add_spurious_lane,
    )

    report = evaluate_joined_skill_routing(_FIXTURE_PATH)
    target = _case_result(report, "compound-performance-database")

    assert target["stagePass"]["operationCandidateRecall"] is True
    assert target["stagePass"]["operationCandidateExact"] is False
    assert target["routingDecisionPassed"] is False
    assert report["operationCandidateExactRate"] < 1.0


def test_candidate_exact_rejects_empty_lane_ghost_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = joined_evaluator.route_skill_turn
    target_prompt = "说明 auth.py 从 token 校验到角色判断的调用链。"

    def add_ghost_candidate(
        request: object,
        adapters: object,
        *,
        controls: object = None,
    ) -> object:
        decision = original(request, adapters, controls=controls)
        if request.prompt == target_prompt:
            ghost = RoutedSkill(
                name="ghost-candidate",
                qualified_name="ghost-candidate",
                description="must not enter the top-k trace",
                path="/joined-quality/ghost/SKILL.md",
                source="project",
                score=1.0,
            )
            decision.routing.inferred_candidates = (
                *decision.routing.inferred_candidates,
                ghost,
            )
        return decision

    monkeypatch.setattr(
        joined_evaluator,
        "route_skill_turn",
        add_ghost_candidate,
    )

    report = evaluate_joined_skill_routing(_FIXTURE_PATH)
    target = next(
        case
        for case in report["cases"]
        if case["actualCandidatesAtK"]
        and "ghost-candidate" in case["actualCandidatesAtK"]
    )

    assert target["stagePass"]["candidateRecall"] is True
    assert target["stagePass"]["candidateExact"] is False
    assert target["routingDecisionPassed"] is False
    assert report["candidateExactRate"] < 1.0


def test_explicit_authority_requires_an_exact_required_skill_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = joined_evaluator.required_skill_names_for_routing

    def leak_one_extra_authority(routing: object) -> list[str]:
        names = original(routing)
        if not names:
            return names
        return [*names, "unexpected/authority-leak"]

    monkeypatch.setattr(
        joined_evaluator,
        "required_skill_names_for_routing",
        leak_one_extra_authority,
    )

    report = evaluate_joined_skill_routing(_FIXTURE_PATH)
    explicit_cases = [
        case for case in report["cases"] if "explicit_authority" in case["tags"]
    ]

    assert explicit_cases
    assert report["explicitAuthorityPreservationRate"] == 0.0
    assert all(
        case["stagePass"]["explicitAuthority"] is False
        for case in explicit_cases
    )


@pytest.mark.parametrize(
    "prompt_mode",
    ["routed", "inventory_names", "suppressed"],
)
def test_prompt_projection_checks_actual_skill_content_not_only_mode(
    monkeypatch: pytest.MonkeyPatch,
    prompt_mode: str,
) -> None:
    original = joined_evaluator.route_skill_turn

    def corrupt_prompt_projection(
        request: object,
        adapters: object,
        *,
        controls: object = None,
    ) -> object:
        decision = original(request, adapters, controls=controls)
        if decision.prompt_mode != prompt_mode:
            return decision
        if prompt_mode == "suppressed":
            corrupted = (request.skills[0],)
        else:
            corrupted = decision.prompt_skills[:-1]
        return replace(decision, prompt_skills=tuple(corrupted))

    monkeypatch.setattr(
        joined_evaluator,
        "route_skill_turn",
        corrupt_prompt_projection,
    )

    report = evaluate_joined_skill_routing(_FIXTURE_PATH)
    affected = [
        case
        for case in report["cases"]
        if case["expectedPromptMode"] == prompt_mode
    ]

    assert affected
    assert all(case["actualPromptMode"] == prompt_mode for case in affected)
    assert all(
        case["stagePass"]["promptProjection"] is False for case in affected
    )
    assert report["promptProjectionAccuracy"] < 1.0


def test_routing_decision_rate_ignores_operation_taxonomy_only_mismatch(
    tmp_path: Path,
) -> None:
    baseline = evaluate_joined_skill_routing(_FIXTURE_PATH)
    document = _load_fixture()
    target = next(
        case for case in document["cases"] if case["id"] == "contrast-auth-explain"
    )
    target["expected"]["operations"][0] = {
        "intent": "question",
        "action": "read",
        "polarity": "affirmed",
    }
    path = _write_dataset(tmp_path, document, name="route-taxonomy-mismatch")

    mutated = evaluate_joined_skill_routing(path)
    baseline_case = _case_result(baseline, target["id"])
    mutated_case = _case_result(mutated, target["id"])

    assert baseline_case["passed"] is True
    assert mutated_case["stagePass"]["operations"] is False
    assert mutated_case["routingDecisionPassed"] is True
    assert mutated_case["passed"] is False
    assert mutated["routingDecisionPassRate"] == baseline["routingDecisionPassRate"]
    assert mutated["routeOperationExactRate"] < baseline["routeOperationExactRate"]
    assert mutated["casePassRate"] < baseline["casePassRate"]


def test_route_operation_rate_excludes_non_route_taxonomy_mismatches(
    tmp_path: Path,
) -> None:
    baseline = evaluate_joined_skill_routing(_FIXTURE_PATH)
    document = _load_fixture()
    target = next(
        case
        for case in document["cases"]
        if case["id"] == "no-skill-destructive-code"
    )
    target["expected"]["operations"][0] = {
        "intent": "question",
        "action": "read",
        "polarity": "affirmed",
    }
    path = _write_dataset(tmp_path, document, name="non-route-taxonomy-mismatch")

    mutated = evaluate_joined_skill_routing(path)
    baseline_case = _case_result(baseline, target["id"])
    mutated_case = _case_result(mutated, target["id"])

    assert baseline_case["stagePass"]["operations"] is True
    assert mutated_case["stagePass"]["operations"] is False
    assert mutated["operationExactRate"] < baseline["operationExactRate"]
    assert mutated["routeOperationExactRate"] == baseline["routeOperationExactRate"]
    assert mutated["routingDecisionPassRate"] == baseline["routingDecisionPassRate"]


def test_joined_skill_routing_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    document = _load_fixture()
    document["cases"][1]["id"] = document["cases"][0]["id"]
    path = _write_dataset(tmp_path, document, name="duplicate-case-id")

    with pytest.raises(ValueError, match="invalid joined Skill case identity"):
        evaluate_joined_skill_routing(path)


def test_joined_skill_routing_rejects_unknown_skill_gold(tmp_path: Path) -> None:
    document = _load_fixture()
    document["cases"][0]["expected"]["expectedCandidateSkills"].append(
        "unknown-production-skill"
    )
    path = _write_dataset(tmp_path, document, name="unknown-skill-gold")

    with pytest.raises(ValueError, match="references unknown Skills"):
        evaluate_joined_skill_routing(path)


def test_joined_skill_routing_rejects_conflicting_skill_gold(
    tmp_path: Path,
) -> None:
    document = _load_fixture()
    expected = document["cases"][0]["expected"]
    expected["forbiddenSkills"].append(expected["requiredSkills"][0])
    path = _write_dataset(tmp_path, document, name="conflicting-skill-gold")

    with pytest.raises(ValueError, match="conflicting Skill expectations"):
        evaluate_joined_skill_routing(path)


def test_joined_skill_routing_rejects_out_of_range_operation_index(
    tmp_path: Path,
) -> None:
    document = _load_fixture()
    expected = document["cases"][0]["expected"]
    expected["operationSkillMap"][0]["operationIndex"] = len(
        expected["operations"]
    )
    path = _write_dataset(tmp_path, document, name="invalid-operation-index")

    with pytest.raises(ValueError, match="invalid operation index"):
        evaluate_joined_skill_routing(path)


def _remove_context_causal_subset(document: dict[str, Any]) -> None:
    for case in document["cases"]:
        case["expected"]["contextUsed"] = False


def _remove_compound_causal_subset(document: dict[str, Any]) -> None:
    for case in document["cases"]:
        affirmed_seen = False
        for operation in case["expected"]["operations"]:
            if operation["polarity"] != "affirmed":
                continue
            if affirmed_seen:
                operation["polarity"] = "negated"
            affirmed_seen = True


def _remove_intent_sensitive_subset(document: dict[str, Any]) -> None:
    for case in document["cases"]:
        case["tags"] = [
            tag for tag in case["tags"] if tag != "intent_sensitive"
        ]


def _replace_outcome(document: dict[str, Any], source: str, target: str) -> None:
    for case in document["cases"]:
        if case["expected"]["outcome"] == source:
            case["expected"]["outcome"] = target
            if (
                case["expected"]["promptMode"] == "suppressed"
                and target != "no_skill"
            ):
                case["expected"]["promptMode"] = "inventory_names"


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("missing-context-causal", _remove_context_causal_subset),
        ("missing-compound-causal", _remove_compound_causal_subset),
        ("missing-intent-sensitive", _remove_intent_sensitive_subset),
        (
            "missing-no-skill-outcome",
            lambda document: _replace_outcome(document, "no_skill", "clarify"),
        ),
        (
            "missing-clarify-outcome",
            lambda document: _replace_outcome(document, "clarify", "no_skill"),
        ),
        (
            "missing-abstain-outcome",
            lambda document: _replace_outcome(document, "abstain", "no_skill"),
        ),
    ],
)
def test_joined_skill_routing_requires_critical_causal_and_outcome_subsets(
    tmp_path: Path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    document = _load_fixture()
    mutate(document)
    path = _write_dataset(tmp_path, document, name=name)

    with pytest.raises(
        ValueError,
        match="missing a required causal or outcome subset",
    ):
        evaluate_joined_skill_routing(path)
