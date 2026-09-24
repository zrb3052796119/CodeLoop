"""Production-shaped, deterministic Intent-to-Skill routing evaluation.

The joined benchmark deliberately crosses the same small parse-and-route seam
as production while injecting no semantic provider, embedding matcher, or
candidate reranker.  Its report preserves stage boundaries so a failed final
route can be attributed to Intent parsing, context resolution, candidate
recall, final selection, or authority handling.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
    ParsedIntent,
)
from minicode.skill_router import SkillRouter, required_skill_names_for_routing
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnDecision,
    SkillRoutingTurnRequest,
    route_skill_turn,
)

_CATEGORIES = frozenset(
    {
        "same-topic-intent",
        "context-continuation-switch",
        "compound-partial-semantic",
        "authority-negation-correction",
        "no-skill-clarify",
    }
)
_OUTCOMES = frozenset({"route", "no_skill", "abstain", "clarify"})
_PROMPT_MODES = frozenset({"routed", "inventory_names", "suppressed"})
_EXPECTED_KEYS = frozenset(
    {
        "operations",
        "contextUsed",
        "expectedCandidateSkills",
        "operationSkillMap",
        "requiredSkills",
        "allowedInferredSkills",
        "forbiddenSkills",
        "outcome",
        "promptMode",
    }
)
_STAGE_ORDER = (
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
)
_ROUTING_DECISION_STAGES = (
    "candidateExact",
    "operationCandidateExact",
    "finalSkills",
    "explicitAuthority",
    "forbiddenCandidates",
    "forbiddenFinal",
    "outcome",
    "promptProjection",
)


@dataclass(frozen=True, slots=True)
class _ExpectedOperation:
    intent: str
    action: str
    polarity: str

    def to_dict(self) -> dict[str, str]:
        return {
            "intent": self.intent,
            "action": self.action,
            "polarity": self.polarity,
        }


@dataclass(frozen=True, slots=True)
class _OperationSkillExpectation:
    operation_index: int
    skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ExpectedRoute:
    operations: tuple[_ExpectedOperation, ...]
    context_used: bool
    candidate_skills: tuple[str, ...]
    operation_skill_map: tuple[_OperationSkillExpectation, ...]
    required_skills: tuple[str, ...]
    allowed_inferred_skills: tuple[str, ...]
    forbidden_skills: tuple[str, ...]
    outcome: str
    prompt_mode: str


@dataclass(frozen=True, slots=True)
class _JoinedCase:
    case_id: str
    category: str
    tags: tuple[str, ...]
    pair_id: str | None
    prompt: str
    messages: tuple[dict[str, str], ...]
    active_task: str
    has_context: bool
    expected: _ExpectedRoute


@dataclass(frozen=True, slots=True)
class _CaseRun:
    decision: SkillRoutingTurnDecision
    operations: tuple[dict[str, str], ...]
    candidate_rows: tuple[dict[str, object], ...]
    candidate_names_at_k: tuple[str, ...]
    selected_names: tuple[str, ...]
    required_names: tuple[str, ...]
    prompt_skill_names: tuple[str, ...]
    catalog_skill_names: tuple[str, ...]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _read_document(path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("joined Skill-routing dataset must be a JSON object")
    return document


def _string_list(
    value: object,
    *,
    field: str,
    case_id: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
        or (not allow_empty and not value)
    ):
        raise ValueError(f"invalid {field} for joined Skill case {case_id}")
    return tuple(value)


def _normalise_catalog(document: dict[str, Any]) -> tuple[list[dict[str, object]], set[str]]:
    raw_catalog = document.get("catalog")
    if not isinstance(raw_catalog, list) or not raw_catalog:
        raise ValueError("joined Skill-routing dataset requires a non-empty catalog")
    catalog: list[dict[str, object]] = []
    qualified_names: set[str] = set()
    for index, raw_skill in enumerate(raw_catalog):
        if not isinstance(raw_skill, dict):
            raise TypeError(f"invalid joined Skill catalog entry at index {index}")
        name = raw_skill.get("name")
        qualified_name = raw_skill.get("qualified_name", name)
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(qualified_name, str)
            or not qualified_name
            or qualified_name in qualified_names
        ):
            raise ValueError(f"invalid joined Skill catalog entry at index {index}")
        skill = dict(raw_skill)
        skill["qualified_name"] = qualified_name
        skill.setdefault("description", "")
        skill.setdefault("source", "project")
        skill.setdefault("path", f"/joined-quality/{qualified_name}/SKILL.md")
        raw_tools = skill.get("tools", [])
        if (
            not isinstance(raw_tools, list)
            or not all(isinstance(item, str) and item for item in raw_tools)
            or len(set(raw_tools)) != len(raw_tools)
        ):
            raise ValueError(f"invalid tools for joined Skill {qualified_name}")
        qualified_names.add(qualified_name)
        catalog.append(skill)
    return catalog, qualified_names


def _noop_capability(**_kwargs: object) -> None:
    return None


def _build_registry(
    document: dict[str, Any],
    catalog: Sequence[dict[str, object]],
) -> CapabilityRegistry:
    raw_capabilities = document.get("capabilities")
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise ValueError(
            "joined Skill-routing dataset requires non-empty capabilities"
        )
    registry = CapabilityRegistry()
    known_capabilities: set[str] = set()
    for index, raw_capability in enumerate(raw_capabilities):
        if not isinstance(raw_capability, dict):
            raise TypeError(f"invalid joined capability at index {index}")
        name = raw_capability.get("name")
        domain = raw_capability.get("domain")
        scope = raw_capability.get("scope")
        description = raw_capability.get("description")
        tags = raw_capability.get("tags", [])
        if (
            not isinstance(name, str)
            or not name
            or name in known_capabilities
            or not isinstance(description, str)
            or not isinstance(tags, list)
            or not all(isinstance(tag, str) and tag for tag in tags)
            or len(set(tags)) != len(tags)
        ):
            raise ValueError(f"invalid joined capability at index {index}")
        try:
            metadata = CapabilityMetadata(
                name=name,
                domain=CapabilityDomain(domain),
                scope=CapabilityScope(scope),
                description=description,
                tags=list(tags),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid joined capability at index {index}") from error
        registry.register(metadata, _noop_capability)
        known_capabilities.add(name)

    for skill in catalog:
        unknown_tools = set(skill.get("tools", [])) - known_capabilities
        if unknown_tools:
            raise ValueError(
                f"joined Skill {skill['qualified_name']} references unknown "
                f"capabilities: {sorted(unknown_tools)}"
            )
    return registry.freeze()


def _normalise_expected(
    raw_expected: object,
    *,
    case_id: str,
    known_skills: set[str],
) -> _ExpectedRoute:
    if not isinstance(raw_expected, dict) or set(raw_expected) != _EXPECTED_KEYS:
        raise ValueError(f"invalid expected contract for joined Skill case {case_id}")

    raw_operations = raw_expected["operations"]
    if not isinstance(raw_operations, list):
        raise ValueError(f"invalid operations for joined Skill case {case_id}")
    operations: list[_ExpectedOperation] = []
    for index, raw_operation in enumerate(raw_operations):
        if not isinstance(raw_operation, dict) or set(raw_operation) != {
            "intent",
            "action",
            "polarity",
        }:
            raise ValueError(
                f"invalid operation {index} for joined Skill case {case_id}"
            )
        intent = raw_operation["intent"]
        action = raw_operation["action"]
        polarity = raw_operation["polarity"]
        try:
            IntentType(intent)
            ActionType(action)
            IntentPolarity(polarity)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"invalid operation {index} for joined Skill case {case_id}"
            ) from error
        operations.append(_ExpectedOperation(intent, action, polarity))

    context_used = raw_expected["contextUsed"]
    if not isinstance(context_used, bool):
        raise ValueError(f"invalid contextUsed for joined Skill case {case_id}")
    candidate_skills = _string_list(
        raw_expected["expectedCandidateSkills"],
        field="expectedCandidateSkills",
        case_id=case_id,
    )
    required_skills = _string_list(
        raw_expected["requiredSkills"],
        field="requiredSkills",
        case_id=case_id,
    )
    allowed_skills = _string_list(
        raw_expected["allowedInferredSkills"],
        field="allowedInferredSkills",
        case_id=case_id,
    )
    forbidden_skills = _string_list(
        raw_expected["forbiddenSkills"],
        field="forbiddenSkills",
        case_id=case_id,
    )

    referenced = (
        set(candidate_skills)
        | set(required_skills)
        | set(allowed_skills)
        | set(forbidden_skills)
    )
    unknown_skills = referenced - known_skills
    if unknown_skills:
        raise ValueError(
            f"joined Skill case {case_id} references unknown Skills: "
            f"{sorted(unknown_skills)}"
        )
    positive_final = set(required_skills) | set(allowed_skills)
    if set(required_skills) & set(allowed_skills):
        raise ValueError(
            f"joined Skill case {case_id} repeats required Skills as allowed"
        )
    if positive_final & set(forbidden_skills) or set(candidate_skills) & set(
        forbidden_skills
    ):
        raise ValueError(f"conflicting Skill expectations for joined case {case_id}")

    raw_operation_map = raw_expected["operationSkillMap"]
    if not isinstance(raw_operation_map, list):
        raise ValueError(f"invalid operationSkillMap for joined case {case_id}")
    operation_map: list[_OperationSkillExpectation] = []
    seen_operation_indexes: set[int] = set()
    mapped_skills: set[str] = set()
    for map_index, raw_mapping in enumerate(raw_operation_map):
        if not isinstance(raw_mapping, dict) or set(raw_mapping) != {
            "operationIndex",
            "skills",
        }:
            raise ValueError(
                f"invalid operationSkillMap row {map_index} for joined case {case_id}"
            )
        operation_index = raw_mapping["operationIndex"]
        if (
            isinstance(operation_index, bool)
            or not isinstance(operation_index, int)
            or operation_index < 0
            or operation_index >= len(operations)
            or operation_index in seen_operation_indexes
        ):
            raise ValueError(
                f"invalid operation index for joined Skill case {case_id}"
            )
        skills = _string_list(
            raw_mapping["skills"],
            field=f"operationSkillMap[{map_index}].skills",
            case_id=case_id,
        )
        unknown_mapped = set(skills) - known_skills
        if unknown_mapped:
            raise ValueError(
                f"joined Skill case {case_id} maps unknown Skills: "
                f"{sorted(unknown_mapped)}"
            )
        seen_operation_indexes.add(operation_index)
        mapped_skills.update(skills)
        operation_map.append(_OperationSkillExpectation(operation_index, skills))
    if len(operation_map) != len(operations):
        raise ValueError(
            f"joined Skill case {case_id} must map every expected operation"
        )
    if mapped_skills - set(candidate_skills):
        raise ValueError(
            f"joined Skill case {case_id} maps Skills absent from candidate gold"
        )

    outcome = raw_expected["outcome"]
    prompt_mode = raw_expected["promptMode"]
    if outcome not in _OUTCOMES or prompt_mode not in _PROMPT_MODES:
        raise ValueError(f"invalid outcome projection for joined Skill case {case_id}")
    if outcome == "route":
        if not positive_final or prompt_mode != "routed":
            raise ValueError(f"route case {case_id} requires final Skill gold")
    elif positive_final or prompt_mode not in {"inventory_names", "suppressed"}:
        raise ValueError(f"non-route case {case_id} cannot require final Skills")
    if prompt_mode == "suppressed" and outcome != "no_skill":
        raise ValueError(f"suppressed case {case_id} must be no_skill")

    return _ExpectedRoute(
        operations=tuple(operations),
        context_used=context_used,
        candidate_skills=candidate_skills,
        operation_skill_map=tuple(operation_map),
        required_skills=required_skills,
        allowed_inferred_skills=allowed_skills,
        forbidden_skills=forbidden_skills,
        outcome=outcome,
        prompt_mode=prompt_mode,
    )


def _normalise_cases(
    document: dict[str, Any],
    *,
    known_skills: set[str],
) -> list[_JoinedCase]:
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("joined Skill-routing dataset requires non-empty cases")
    cases: list[_JoinedCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise TypeError(f"invalid joined Skill case at index {index}")
        case_id = raw_case.get("id")
        category = raw_case.get("category")
        prompt = raw_case.get("prompt")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen_ids
            or category not in _CATEGORIES
            or not isinstance(prompt, str)
            or not prompt.strip()
        ):
            raise ValueError(f"invalid joined Skill case identity at index {index}")
        tags = _string_list(
            raw_case.get("tags"),
            field="tags",
            case_id=case_id,
            allow_empty=False,
        )
        pair_id = raw_case.get("pairId")
        if pair_id is not None and (not isinstance(pair_id, str) or not pair_id):
            raise ValueError(f"invalid pairId for joined Skill case {case_id}")

        messages: tuple[dict[str, str], ...] = ()
        active_task = ""
        raw_context = raw_case.get("context")
        has_context = raw_context is not None
        if has_context:
            if not isinstance(raw_context, dict) or not set(raw_context).issubset(
                {"messages", "activeTask"}
            ):
                raise ValueError(f"invalid context for joined Skill case {case_id}")
            raw_messages = raw_context.get("messages", [])
            active_task = raw_context.get("activeTask", "")
            if (
                not isinstance(raw_messages, list)
                or not all(
                    isinstance(message, dict)
                    and set(message) == {"role", "content"}
                    and message.get("role") in {"user", "assistant"}
                    and isinstance(message.get("content"), str)
                    and bool(message["content"].strip())
                    for message in raw_messages
                )
                or not isinstance(active_task, str)
            ):
                raise ValueError(f"invalid context for joined Skill case {case_id}")
            messages = tuple(
                {"role": message["role"], "content": message["content"]}
                for message in raw_messages
            )

        expected = _normalise_expected(
            raw_case.get("expected"),
            case_id=case_id,
            known_skills=known_skills,
        )
        if expected.context_used and not has_context:
            raise ValueError(
                f"joined Skill case {case_id} expects context without providing it"
            )
        seen_ids.add(case_id)
        cases.append(
            _JoinedCase(
                case_id=case_id,
                category=category,
                tags=tags,
                pair_id=pair_id,
                prompt=prompt,
                messages=messages,
                active_task=active_task,
                has_context=has_context,
                expected=expected,
            )
        )

    category_counts = Counter(case.category for case in cases)
    if set(category_counts) != _CATEGORIES:
        raise ValueError(
            "joined Skill-routing dataset must cover every benchmark category"
        )
    pair_groups: dict[str, list[_JoinedCase]] = defaultdict(list)
    for case in cases:
        if case.pair_id is not None:
            pair_groups[case.pair_id].append(case)
    if not pair_groups or any(len(group) != 2 for group in pair_groups.values()):
        raise ValueError("joined Skill-routing pairId groups must contain exactly two cases")

    contextual = [case for case in cases if case.has_context and case.expected.context_used]
    compound = [
        case
        for case in cases
        if sum(operation.polarity == "affirmed" for operation in case.expected.operations)
        > 1
    ]
    intent_sensitive = [case for case in cases if "intent_sensitive" in case.tags]
    outcome_counts = Counter(case.expected.outcome for case in cases)
    if (
        not contextual
        or not compound
        or not intent_sensitive
        or not outcome_counts["no_skill"]
        or not outcome_counts["clarify"]
        or not outcome_counts["abstain"]
    ):
        raise ValueError(
            "joined Skill-routing dataset is missing a required causal or outcome subset"
        )
    return cases


def _intent_context(case: _JoinedCase, *, enabled: bool = True) -> IntentContext | None:
    if not enabled or not case.has_context:
        return None
    return IntentContext.from_messages(case.messages, active_task=case.active_task)


def _qualified_name(candidate: object) -> str:
    if isinstance(candidate, Mapping):
        qualified = candidate.get("qualified_name") or candidate.get(
            "qualifiedName"
        )
        name = candidate.get("name")
    else:
        qualified = getattr(candidate, "qualified_name", None)
        name = getattr(candidate, "name", None)
    return str(qualified or name or "")


def _candidate_projection(
    decision: SkillRoutingTurnDecision,
    *,
    top_k: int,
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    # Explicitly requested Skills bypass the advisory reranker by design, but
    # are still admitted routing candidates and therefore belong in recall.
    admitted: list[object] = [
        candidate
        for candidate in getattr(decision.routing, "selected", ())
        if bool(getattr(candidate, "explicitly_requested", False))
    ]
    admitted.extend(getattr(decision.routing, "inferred_candidates", ()))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for candidate in admitted:
        name = _qualified_name(candidate)
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            {
                "skill": name,
                "operationIndexes": list(
                    getattr(candidate, "operation_indexes", ()) or ()
                ),
                "explicitlyRequested": bool(
                    getattr(candidate, "explicitly_requested", False)
                ),
            }
        )
    return tuple(rows), tuple(row["skill"] for row in rows[:top_k])


def _run_case(
    case: _JoinedCase,
    *,
    catalog: Sequence[dict[str, object]],
    registry: CapabilityRegistry,
    top_k: int,
    context_enabled: bool = True,
    parsed_intent: ParsedIntent | None = None,
) -> _CaseRun:
    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=case.prompt,
            context=_intent_context(case, enabled=context_enabled),
            parsed_intent=parsed_intent,
            skills=catalog,
            registry=registry,
            top_k=top_k,
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
    )
    operations = tuple(
        {
            "intent": operation.intent_type.value,
            "action": operation.action_type.value,
            "polarity": operation.polarity.value,
        }
        for operation in decision.intent.operations
    )
    candidate_rows, candidate_names_at_k = _candidate_projection(
        decision,
        top_k=top_k,
    )
    selected_names = tuple(
        _qualified_name(skill) for skill in getattr(decision.routing, "selected", ())
    )
    return _CaseRun(
        decision=decision,
        operations=operations,
        candidate_rows=candidate_rows,
        candidate_names_at_k=candidate_names_at_k,
        selected_names=selected_names,
        required_names=tuple(required_skill_names_for_routing(decision.routing)),
        prompt_skill_names=tuple(
            _qualified_name(skill) for skill in decision.prompt_skills
        ),
        catalog_skill_names=tuple(_qualified_name(skill) for skill in catalog),
    )


def _ordered_subsequence(required: Sequence[str], actual: Sequence[str]) -> bool:
    iterator = iter(actual)
    return all(any(item == expected for item in iterator) for expected in required)


def _operation_recall(
    expectations: Sequence[_OperationSkillExpectation],
    candidate_rows: Sequence[dict[str, object]],
) -> tuple[int, int]:
    by_name = {str(row["skill"]): row for row in candidate_rows}
    hits = 0
    total = 0
    for expectation in expectations:
        for skill in expectation.skills:
            total += 1
            row = by_name.get(skill)
            if row is not None and expectation.operation_index in row[
                "operationIndexes"
            ]:
                hits += 1
    return hits, total


def _operation_mapping_exact(
    expectations: Sequence[_OperationSkillExpectation],
    candidate_rows: Sequence[dict[str, object]],
) -> bool:
    """Require the complete top-k ``(Skill, operation)`` edge set to match."""
    expected_edges: set[tuple[str, int]] = set()
    for expectation in expectations:
        for skill in expectation.skills:
            expected_edges.add((skill, expectation.operation_index))
    actual_edges = {
        (str(row["skill"]), int(operation_index))
        for row in candidate_rows
        for operation_index in row["operationIndexes"]
    }
    return actual_edges == expected_edges


def _actual_operation_edge_count(
    candidate_rows: Sequence[dict[str, object]],
) -> int:
    return len(
        {
            (str(row["skill"]), int(operation_index))
            for row in candidate_rows
            for operation_index in row["operationIndexes"]
        }
    )


def _expected_prompt_skill_names(
    case: _JoinedCase,
    run: _CaseRun,
) -> tuple[str, ...]:
    if case.expected.prompt_mode == "routed":
        # ``allowedInferredSkills`` are optional.  Final correctness belongs
        # to ``finalSkills``; this stage verifies that the chosen routing
        # decision crosses the prompt boundary without omission or leakage.
        return run.selected_names
    if case.expected.prompt_mode == "inventory_names":
        return run.catalog_skill_names
    return ()


def _stage_results(case: _JoinedCase, run: _CaseRun) -> dict[str, bool]:
    expected = case.expected
    expected_operations = tuple(operation.to_dict() for operation in expected.operations)
    expected_candidates = set(expected.candidate_skills)
    actual_candidates = set(run.candidate_names_at_k)
    operation_hits, operation_total = _operation_recall(
        expected.operation_skill_map,
        run.candidate_rows[: len(run.candidate_names_at_k)],
    )
    operation_mapping_exact = _operation_mapping_exact(
        expected.operation_skill_map,
        run.candidate_rows[: len(run.candidate_names_at_k)],
    )
    allowed_final = set(expected.required_skills) | set(
        expected.allowed_inferred_skills
    )
    if expected.outcome == "route":
        final_matches = bool(run.selected_names) and _ordered_subsequence(
            expected.required_skills,
            run.selected_names,
        ) and set(run.selected_names).issubset(allowed_final)
    else:
        final_matches = not run.selected_names

    explicit_matches = True
    if "explicit_authority" in case.tags:
        explicit_matches = set(expected.required_skills) == set(run.required_names)
    expected_prompt_skills = _expected_prompt_skill_names(case, run)
    return {
        "operations": run.operations == expected_operations,
        "context": run.decision.intent.context_used is expected.context_used,
        "candidateRecall": expected_candidates.issubset(actual_candidates),
        "candidateExact": expected_candidates == actual_candidates,
        "operationCandidateRecall": operation_hits == operation_total,
        "operationCandidateExact": operation_mapping_exact,
        "finalSkills": final_matches,
        "explicitAuthority": explicit_matches,
        "forbiddenCandidates": not (
            set(expected.forbidden_skills) & actual_candidates
        ),
        "forbiddenFinal": not (
            set(expected.forbidden_skills) & set(run.selected_names)
        ),
        "outcome": run.decision.outcome == expected.outcome,
        "promptProjection": (
            run.decision.prompt_mode == expected.prompt_mode
            and run.prompt_skill_names == expected_prompt_skills
        ),
    }


def _permuted_intent(intent: ParsedIntent) -> ParsedIntent:
    operations = tuple(
        replace(
            operation,
            intent_type=IntentType.QUESTION,
            action_type=ActionType.READ,
        )
        if operation.polarity is IntentPolarity.AFFIRMED
        else operation
        for operation in intent.operations
    )
    return replace(
        intent,
        intent_type=IntentType.QUESTION,
        action_type=ActionType.READ,
        operations=operations,
    )


def _quality_score(case: _JoinedCase, run: _CaseRun) -> int:
    stages = _stage_results(case, run)
    # Context itself is removed during the ablation, so scoring that field
    # would manufacture degradation. These are the downstream causal stages.
    return sum(
        int(stages[name])
        for name in (
            "operations",
            "candidateRecall",
            "operationCandidateRecall",
            "finalSkills",
            "outcome",
        )
    )


def evaluate_joined_skill_routing(dataset_path: str | Path) -> dict[str, object]:
    """Evaluate raw prompt + context through final Skill prompt projection."""

    document = _read_document(dataset_path)
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported joined Skill-routing dataset schema")
    top_k = document.get("topK")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("joined Skill-routing topK must be a positive integer")
    catalog, known_skills = _normalise_catalog(document)
    registry = _build_registry(document, catalog)
    cases = _normalise_cases(document, known_skills=known_skills)

    operation_exact = 0
    route_operation_exact = 0
    routing_decision_passes = 0
    operation_correct_case_count = 0
    routing_passes_given_correct_operations = 0
    context_exact = 0
    context_case_count = 0
    candidate_hits = 0
    candidate_total = 0
    candidate_exact = 0
    operation_candidate_hits = 0
    operation_candidate_total = 0
    operation_candidate_actual_total = 0
    operation_candidate_exact = 0
    required_hits = 0
    required_total = 0
    final_exact = 0
    top1_hits = 0
    route_case_count = 0
    explicit_hits = 0
    explicit_case_count = 0
    forbidden_candidate_violations = 0
    forbidden_final_violations = 0
    outcome_hits = 0
    projection_hits = 0
    no_skill_false_positives = 0
    no_skill_case_count = 0
    clarify_hits = 0
    clarify_case_count = 0
    abstain_hits = 0
    abstain_case_count = 0
    compound_hits = 0
    compound_case_count = 0
    compound_exact_hits = 0
    context_degradations = 0
    context_ablation_count = 0
    intent_sensitivity_hits = 0
    intent_sensitivity_count = 0
    failed_case_ids: list[str] = []
    case_results: list[dict[str, object]] = []
    runs_by_id: dict[str, _CaseRun] = {}

    category_totals: Counter[str] = Counter()
    category_passes: Counter[str] = Counter()
    outcome_totals: Counter[str] = Counter()

    for case in cases:
        run = _run_case(
            case,
            catalog=catalog,
            registry=registry,
            top_k=top_k,
        )
        stages = _stage_results(case, run)
        runs_by_id[case.case_id] = run
        passed = all(stages.values())
        routing_decision_passed = all(
            stages[name] for name in _ROUTING_DECISION_STAGES
        )
        routing_decision_passes += int(routing_decision_passed)
        if stages["operations"]:
            operation_correct_case_count += 1
            routing_passes_given_correct_operations += int(
                routing_decision_passed
            )
        first_failure = next(
            (name for name in _STAGE_ORDER if not stages[name]),
            None,
        )
        if not passed:
            failed_case_ids.append(case.case_id)

        operation_exact += int(stages["operations"])
        if case.has_context:
            context_case_count += 1
            context_exact += int(stages["context"])
        expected_candidates = set(case.expected.candidate_skills)
        actual_candidates = set(run.candidate_names_at_k)
        candidate_hits += len(expected_candidates & actual_candidates)
        candidate_total += len(expected_candidates)
        candidate_exact += int(stages["candidateExact"])
        operation_hits, operation_total = _operation_recall(
            case.expected.operation_skill_map,
            run.candidate_rows[: len(run.candidate_names_at_k)],
        )
        operation_candidate_hits += operation_hits
        operation_candidate_total += operation_total
        operation_candidate_actual_total += _actual_operation_edge_count(
            run.candidate_rows[: len(run.candidate_names_at_k)]
        )
        operation_candidate_exact += int(stages["operationCandidateExact"])
        required_hits += len(set(case.expected.required_skills) & set(run.selected_names))
        required_total += len(case.expected.required_skills)
        final_exact += int(stages["finalSkills"])
        outcome_hits += int(stages["outcome"])
        projection_hits += int(stages["promptProjection"])
        forbidden_candidate_violations += int(not stages["forbiddenCandidates"])
        forbidden_final_violations += int(not stages["forbiddenFinal"])

        if case.expected.outcome == "route":
            route_case_count += 1
            route_operation_exact += int(stages["operations"])
            top1_gold = set(case.expected.required_skills) | set(
                case.expected.allowed_inferred_skills
            )
            top1_hits += int(bool(run.selected_names) and run.selected_names[0] in top1_gold)
        if "explicit_authority" in case.tags:
            explicit_case_count += 1
            explicit_hits += int(stages["explicitAuthority"])
        if case.expected.outcome == "no_skill":
            no_skill_case_count += 1
            no_skill_false_positives += int(run.decision.outcome == "route")
        if case.expected.outcome == "clarify":
            clarify_case_count += 1
            clarify_hits += int(stages["outcome"])
        if case.expected.outcome == "abstain":
            abstain_case_count += 1
            abstain_hits += int(stages["outcome"])

        affirmed_operation_count = sum(
            operation.polarity == "affirmed"
            for operation in case.expected.operations
        )
        if affirmed_operation_count > 1:
            compound_case_count += 1
            compound_hits += int(stages["operationCandidateRecall"])
            compound_exact_hits += int(stages["operationCandidateExact"])

        if case.has_context and case.expected.context_used:
            context_ablation_count += 1
            ablated = _run_case(
                case,
                catalog=catalog,
                registry=registry,
                top_k=top_k,
                context_enabled=False,
            )
            context_degradations += int(
                _quality_score(case, ablated) < _quality_score(case, run)
            )

        if "intent_sensitive" in case.tags:
            intent_sensitivity_count += 1
            counterfactual = _run_case(
                case,
                catalog=catalog,
                registry=registry,
                top_k=top_k,
                parsed_intent=_permuted_intent(run.decision.intent),
            )
            intent_sensitivity_hits += int(
                (
                    counterfactual.selected_names,
                    counterfactual.candidate_names_at_k,
                )
                != (run.selected_names, run.candidate_names_at_k)
            )

        category_totals[case.category] += 1
        category_passes[case.category] += int(passed)
        outcome_totals[case.expected.outcome] += 1
        case_results.append(
            {
                "id": case.case_id,
                "category": case.category,
                "tags": list(case.tags),
                "pairId": case.pair_id,
                "passed": passed,
                "routingDecisionPassed": routing_decision_passed,
                "firstFailureStage": first_failure,
                "stagePass": stages,
                "expectedOperations": [
                    operation.to_dict() for operation in case.expected.operations
                ],
                "actualOperations": list(run.operations),
                "expectedContextUsed": case.expected.context_used,
                "actualContextUsed": run.decision.intent.context_used,
                "expectedCandidateSkills": list(case.expected.candidate_skills),
                "actualCandidatesAtK": list(run.candidate_names_at_k),
                "candidateTrace": list(run.candidate_rows),
                "expectedRequiredSkills": list(case.expected.required_skills),
                "allowedInferredSkills": list(
                    case.expected.allowed_inferred_skills
                ),
                "actualSelectedSkills": list(run.selected_names),
                "actualExplicitRequiredSkills": list(run.required_names),
                "forbiddenSkills": list(case.expected.forbidden_skills),
                "expectedOutcome": case.expected.outcome,
                "actualOutcome": run.decision.outcome,
                "expectedPromptMode": case.expected.prompt_mode,
                "actualPromptMode": run.decision.prompt_mode,
                "expectedPromptSkills": list(
                    _expected_prompt_skill_names(case, run)
                ),
                "actualPromptSkills": list(run.prompt_skill_names),
            }
        )

    pair_groups: dict[str, list[_JoinedCase]] = defaultdict(list)
    for case in cases:
        if case.pair_id is not None:
            pair_groups[case.pair_id].append(case)
    pair_behavior_hits = 0
    contrast_pair_count = 0
    pair_flip_hits = 0
    context_pair_flip_hits = 0
    context_contrast_pair_count = 0
    context_pair_count = 0
    context_pair_behavior_hits = 0
    for pair in pair_groups.values():
        first, second = pair
        expected_first = (
            first.expected.required_skills,
            first.expected.allowed_inferred_skills,
            first.expected.outcome,
        )
        expected_second = (
            second.expected.required_skills,
            second.expected.allowed_inferred_skills,
            second.expected.outcome,
        )
        expected_changed = expected_first != expected_second
        actual_first = (
            runs_by_id[first.case_id].selected_names,
            runs_by_id[first.case_id].decision.outcome,
        )
        actual_second = (
            runs_by_id[second.case_id].selected_names,
            runs_by_id[second.case_id].decision.outcome,
        )
        actual_changed = actual_first != actual_second
        pair_behavior_hits += int(actual_changed is expected_changed)
        if expected_changed:
            contrast_pair_count += 1
            pair_flip_hits += int(actual_changed)
        if first.prompt == second.prompt:
            context_pair_count += 1
            context_pair_behavior_hits += int(actual_changed is expected_changed)
            if expected_changed:
                context_contrast_pair_count += 1
                context_pair_flip_hits += int(actual_changed)

    category_stats = {
        category: {
            "caseCount": category_totals[category],
            "passRate": _ratio(category_passes[category], category_totals[category]),
        }
        for category in sorted(_CATEGORIES)
    }
    total_cases = len(cases)
    return {
        "schemaVersion": 1,
        "evaluator": "skill-routing-joined",
        "mode": "offline-production-shaped",
        "remoteCallCount": 0,
        "caseCount": total_cases,
        "topK": top_k,
        "catalogSkillCount": len(catalog),
        "capabilityCount": len(registry.list_all()),
        "routeCaseCount": route_case_count,
        "contextCaseCount": context_case_count,
        "contextAblationCaseCount": context_ablation_count,
        "intentSensitivityCaseCount": intent_sensitivity_count,
        "compoundCaseCount": compound_case_count,
        "minimalPairCount": len(pair_groups),
        "contrastPairCount": contrast_pair_count,
        "samePromptContextPairCount": context_pair_count,
        "samePromptContextContrastPairCount": context_contrast_pair_count,
        "noSkillCaseCount": no_skill_case_count,
        "clarifyCaseCount": clarify_case_count,
        "abstainCaseCount": abstain_case_count,
        "casePassRate": _ratio(total_cases - len(failed_case_ids), total_cases),
        "routingDecisionPassRate": _ratio(
            routing_decision_passes,
            total_cases,
        ),
        "operationCorrectCaseCount": operation_correct_case_count,
        "skillDecisionPassGivenCorrectOperations": _ratio(
            routing_passes_given_correct_operations,
            operation_correct_case_count,
        ),
        "operationExactRate": _ratio(operation_exact, total_cases),
        "routeOperationExactRate": _ratio(
            route_operation_exact,
            route_case_count,
        ),
        "contextResolutionRate": _ratio(context_exact, context_case_count),
        "candidateRecallAtK": _ratio(candidate_hits, candidate_total),
        "candidateExactRate": _ratio(candidate_exact, total_cases),
        "operationCandidateRecallAtK": _ratio(
            operation_candidate_hits,
            operation_candidate_total,
        ),
        "operationCandidatePrecisionAtK": _ratio(
            operation_candidate_hits,
            operation_candidate_actual_total,
        ),
        "operationCandidateExactRate": _ratio(
            operation_candidate_exact,
            total_cases,
        ),
        "requiredSkillRecall": _ratio(required_hits, required_total),
        "finalSkillExactRate": _ratio(final_exact, total_cases),
        "top1Accuracy": _ratio(top1_hits, route_case_count),
        "explicitAuthorityPreservationRate": _ratio(
            explicit_hits,
            explicit_case_count,
        ),
        "forbiddenCandidateViolationRate": _ratio(
            forbidden_candidate_violations,
            total_cases,
        ),
        "forbiddenFinalViolationRate": _ratio(
            forbidden_final_violations,
            total_cases,
        ),
        "outcomeAccuracy": _ratio(outcome_hits, total_cases),
        "promptProjectionAccuracy": _ratio(projection_hits, total_cases),
        "noSkillFalsePositiveRate": _ratio(
            no_skill_false_positives,
            no_skill_case_count,
        ),
        "clarifyAccuracy": _ratio(clarify_hits, clarify_case_count),
        "abstainAccuracy": _ratio(abstain_hits, abstain_case_count),
        "compoundOperationCoverageRate": _ratio(
            compound_hits,
            compound_case_count,
        ),
        "compoundOperationCandidateExactRate": _ratio(
            compound_exact_hits,
            compound_case_count,
        ),
        "minimalPairBehaviorAccuracy": _ratio(
            pair_behavior_hits,
            len(pair_groups),
        ),
        "minimalPairRouteFlipRate": _ratio(
            pair_flip_hits,
            contrast_pair_count,
        ),
        "samePromptContextBehaviorAccuracy": _ratio(
            context_pair_behavior_hits,
            context_pair_count,
        ),
        "samePromptContextFlipRate": _ratio(
            context_pair_flip_hits,
            context_contrast_pair_count,
        ),
        "contextAblationDegradationRate": _ratio(
            context_degradations,
            context_ablation_count,
        ),
        "intentPermutationSensitivityRate": _ratio(
            intent_sensitivity_hits,
            intent_sensitivity_count,
        ),
        "categoryStats": category_stats,
        "expectedOutcomeCounts": {
            outcome: outcome_totals[outcome] for outcome in sorted(_OUTCOMES)
        },
        "failedCaseIds": failed_case_ids,
        "cases": case_results,
    }


__all__ = ["evaluate_joined_skill_routing"]
