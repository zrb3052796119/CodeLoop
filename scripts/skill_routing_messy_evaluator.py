"""Offline evaluation for irregular, production-shaped Skill-routing turns.

The corpus intentionally lives outside Skill frontmatter and reuses the frozen
real project catalog from ``intent-skill-production-regressions.json``.  This
keeps the prompts independently authored while exercising the production
``parse_intent -> SkillRouter -> prompt projection`` seam with no provider,
embedding model, or candidate reranker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
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
)
from minicode.skill_router import SkillRouter
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)

_OUTCOMES = frozenset({"route", "no_skill", "clarify", "abstain"})
_PROMPT_MODES = frozenset({"routed", "inventory_names", "suppressed"})
_CASE_KEYS = frozenset(
    {
        "id",
        "category",
        "language",
        "dimensions",
        "prompt",
        "context",
        "pairId",
        "expected",
    }
)
_EXPECTED_KEYS = frozenset(
    {
        "operations",
        "contextUsed",
        "outcome",
        "topSkill",
        "allowedSkills",
        "forbiddenSkills",
        "promptMode",
    }
)
_STAGE_ORDER = (
    "operations",
    "context",
    "topSkill",
    "allowedFinal",
    "forbiddenFinal",
    "outcome",
    "promptMode",
)


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("messy Skill-routing fixture must be a JSON object")
    return document


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _string_list(value: object, *, label: str, allow_empty: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
        or (not allow_empty and not value)
    ):
        raise ValueError(f"{label} must be a unique string list")
    return value


def _noop_capability(**_kwargs: object) -> None:
    return None


def _build_registry(capabilities: object) -> CapabilityRegistry:
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("catalog fixture requires coreCapabilities")
    registry = CapabilityRegistry()
    seen: set[str] = set()
    for index, raw in enumerate(capabilities):
        if not isinstance(raw, dict) or set(raw) != {"name", "domain", "scope"}:
            raise ValueError(f"invalid core capability at index {index}")
        name = raw["name"]
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError(f"invalid core capability name at index {index}")
        try:
            metadata = CapabilityMetadata(
                name=name,
                domain=CapabilityDomain(raw["domain"]),
                scope=CapabilityScope(raw["scope"]),
                description=f"Frozen core capability {name}",
                tags=("messy-routing-fixture",),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid core capability at index {index}") from error
        registry.register(metadata, _noop_capability)
        seen.add(name)
    return registry.freeze()


def _load_catalog(
    fixture_path: Path,
    document: dict[str, Any],
) -> tuple[list[dict[str, object]], CapabilityRegistry, str]:
    catalog_name = document.get("catalogFixture")
    if (
        not isinstance(catalog_name, str)
        or not catalog_name
        or Path(catalog_name).name != catalog_name
    ):
        raise ValueError("catalogFixture must be a sibling filename")
    catalog_path = fixture_path.parent / catalog_name
    catalog_document = _read_json(catalog_path)
    snapshot = catalog_document.get("catalogSnapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("catalog fixture is missing catalogSnapshot")
    raw_skills = snapshot.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ValueError("catalog fixture has no Skills")
    catalog: list[dict[str, object]] = []
    names: set[str] = set()
    for index, raw_skill in enumerate(raw_skills):
        if not isinstance(raw_skill, dict):
            raise ValueError(f"invalid catalog Skill at index {index}")
        qualified_name = raw_skill.get("qualified_name")
        if (
            not isinstance(qualified_name, str)
            or not qualified_name
            or qualified_name in names
        ):
            raise ValueError(f"invalid catalog Skill at index {index}")
        skill = dict(raw_skill)
        skill.setdefault("path", f"/messy-routing/{qualified_name}/SKILL.md")
        catalog.append(skill)
        names.add(qualified_name)
    registry = _build_registry(catalog_document.get("coreCapabilities"))
    digest = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    return catalog, registry, digest


def _normalise_operation(raw: object, *, case_id: str, index: int) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"invalid operation {index} for case {case_id}")
    try:
        polarity_text, operation = raw.split(":", 1)
        intent_text, action_text = operation.split(".", 1)
        intent = IntentType(intent_text)
        action = ActionType(action_text)
        polarity = IntentPolarity(polarity_text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid operation {index} for case {case_id}") from error
    return f"{polarity.value}:{intent.value}.{action.value}"


def _normalise_cases(
    document: dict[str, Any],
    *,
    known_skills: set[str],
) -> list[dict[str, Any]]:
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("messy Skill-routing fixture requires cases")
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict) or set(raw_case) != _CASE_KEYS:
            raise ValueError(f"invalid case shape at index {index}")
        case_id = raw_case["id"]
        category = raw_case["category"]
        language = raw_case["language"]
        prompt = raw_case["prompt"]
        pair_id = raw_case["pairId"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen_ids
            or not isinstance(category, str)
            or not category
            or language not in {"zh", "en", "mixed"}
            or not isinstance(prompt, str)
            or not prompt.strip()
            or (pair_id is not None and (not isinstance(pair_id, str) or not pair_id))
        ):
            raise ValueError(f"invalid case identity at index {index}")
        dimensions = _string_list(
            raw_case["dimensions"],
            label=f"dimensions for case {case_id}",
            allow_empty=False,
        )
        raw_context = raw_case["context"]
        messages: list[dict[str, str]] = []
        active_task = ""
        if raw_context is not None:
            if not isinstance(raw_context, dict) or set(raw_context) != {
                "messages",
                "activeTask",
            }:
                raise ValueError(f"invalid context for case {case_id}")
            raw_messages = raw_context["messages"]
            active_task = raw_context["activeTask"]
            if (
                not isinstance(raw_messages, list)
                or not all(
                    isinstance(message, dict)
                    and set(message) == {"role", "content"}
                    and message["role"] in {"user", "assistant"}
                    and isinstance(message["content"], str)
                    and bool(message["content"].strip())
                    for message in raw_messages
                )
                or not isinstance(active_task, str)
            ):
                raise ValueError(f"invalid context for case {case_id}")
            messages = [dict(message) for message in raw_messages]

        raw_expected = raw_case["expected"]
        if not isinstance(raw_expected, dict) or set(raw_expected) != _EXPECTED_KEYS:
            raise ValueError(f"invalid expected contract for case {case_id}")
        raw_operations = raw_expected["operations"]
        if not isinstance(raw_operations, list):
            raise ValueError(f"invalid operations for case {case_id}")
        operations = [
            _normalise_operation(raw, case_id=case_id, index=operation_index)
            for operation_index, raw in enumerate(raw_operations)
        ]
        context_used = raw_expected["contextUsed"]
        outcome = raw_expected["outcome"]
        top_skill = raw_expected["topSkill"]
        prompt_mode = raw_expected["promptMode"]
        allowed = _string_list(
            raw_expected["allowedSkills"],
            label=f"allowedSkills for case {case_id}",
        )
        forbidden = _string_list(
            raw_expected["forbiddenSkills"],
            label=f"forbiddenSkills for case {case_id}",
        )
        if (
            not isinstance(context_used, bool)
            or (context_used and raw_context is None)
            or outcome not in _OUTCOMES
            or prompt_mode not in _PROMPT_MODES
            or (top_skill is not None and not isinstance(top_skill, str))
        ):
            raise ValueError(f"invalid expected outcome for case {case_id}")
        referenced = set(allowed) | set(forbidden)
        if top_skill is not None:
            referenced.add(top_skill)
        if referenced - known_skills:
            raise ValueError(
                f"case {case_id} references unknown Skills: "
                f"{sorted(referenced - known_skills)!r}"
            )
        if set(allowed) & set(forbidden):
            raise ValueError(f"case {case_id} has conflicting Skill labels")
        if outcome == "route":
            if top_skill is None or top_skill not in allowed or prompt_mode != "routed":
                raise ValueError(f"route case {case_id} requires routed Skill gold")
        elif top_skill is not None or allowed:
            raise ValueError(f"non-route case {case_id} cannot allow final Skills")

        seen_ids.add(case_id)
        cases.append(
            {
                "id": case_id,
                "category": category,
                "language": language,
                "dimensions": dimensions,
                "prompt": prompt,
                "pairId": pair_id,
                "messages": messages,
                "activeTask": active_task,
                "hasContext": raw_context is not None,
                "expected": {
                    "operations": operations,
                    "contextUsed": context_used,
                    "outcome": outcome,
                    "topSkill": top_skill,
                    "allowedSkills": allowed,
                    "forbiddenSkills": forbidden,
                    "promptMode": prompt_mode,
                },
            }
        )
    return cases


def _validate_coverage(document: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    coverage = document.get("coverageMinimums")
    if not isinstance(coverage, dict) or set(coverage) != {
        "caseCount",
        "contextCases",
        "compoundCases",
        "negativeCases",
        "nonRouteCases",
        "pairCount",
        "requiredDimensions",
        "requiredLanguages",
    }:
        raise ValueError("invalid coverageMinimums")
    dimensions = Counter(
        dimension for case in cases for dimension in case["dimensions"]
    )
    languages = Counter(case["language"] for case in cases)
    pair_counts = Counter(
        case["pairId"] for case in cases if case["pairId"] is not None
    )
    if any(count != 2 for count in pair_counts.values()):
        raise ValueError("every pairId must identify exactly two cases")
    observed = {
        "caseCount": len(cases),
        "contextCases": sum(case["hasContext"] for case in cases),
        "compoundCases": sum(
            len(case["expected"]["operations"]) > 1 for case in cases
        ),
        "negativeCases": sum(
            any(
                operation.startswith("negated:")
                for operation in case["expected"]["operations"]
            )
            for case in cases
        ),
        "nonRouteCases": sum(
            case["expected"]["outcome"] != "route" for case in cases
        ),
        "pairCount": len(pair_counts),
    }
    for name, actual in observed.items():
        minimum = coverage[name]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ValueError(f"invalid coverage minimum {name}")
        if actual < minimum:
            raise ValueError(f"coverage {name}={actual} is below minimum {minimum}")
    required_dimensions = _string_list(
        coverage["requiredDimensions"],
        label="requiredDimensions",
        allow_empty=False,
    )
    required_languages = _string_list(
        coverage["requiredLanguages"],
        label="requiredLanguages",
        allow_empty=False,
    )
    if set(required_dimensions) - set(dimensions):
        raise ValueError("fixture is missing required dimensions")
    if set(required_languages) - set(languages):
        raise ValueError("fixture is missing required languages")


def _case_context(case: dict[str, Any]) -> IntentContext | None:
    if not case["hasContext"]:
        return None
    return IntentContext.from_messages(
        case["messages"],
        active_task=case["activeTask"],
    )


def _operation_projection(intent: object) -> list[str]:
    return [
        f"{operation.polarity.value}:"
        f"{operation.intent_type.value}.{operation.action_type.value}"
        for operation in getattr(intent, "operations", ())
    ]


def evaluate_messy_skill_routing(path: str | Path) -> dict[str, object]:
    """Evaluate one frozen dirty-input corpus through the production seam."""

    fixture_path = Path(path)
    document = _read_json(fixture_path)
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported messy Skill-routing schema")
    top_k = document.get("topK")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("topK must be a positive integer")
    catalog, registry, catalog_digest = _load_catalog(fixture_path, document)
    known_skills = {
        str(skill["qualified_name"])
        for skill in catalog
    }
    cases = _normalise_cases(document, known_skills=known_skills)
    _validate_coverage(document, cases)

    case_results: list[dict[str, object]] = []
    stage_hits: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    category_passes: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()
    language_passes: Counter[str] = Counter()
    dimension_counts: Counter[str] = Counter()
    dimension_passes: Counter[str] = Counter()
    failed_case_ids: list[str] = []
    results_by_id: dict[str, dict[str, object]] = {}

    for case in cases:
        decision = route_skill_turn(
            SkillRoutingTurnRequest(
                prompt=case["prompt"],
                context=_case_context(case),
                skills=catalog,
                registry=registry,
                top_k=top_k,
            ),
            SkillRoutingTurnAdapters(router=SkillRouter()),
        )
        actual_operations = _operation_projection(decision.intent)
        selected = [
            skill.qualified_name for skill in getattr(decision.routing, "selected", ())
        ]
        actual_top = selected[0] if selected else None
        expected = case["expected"]
        stages = {
            "operations": actual_operations == expected["operations"],
            "context": bool(decision.intent.context_used)
            is expected["contextUsed"],
            "topSkill": actual_top == expected["topSkill"],
            "allowedFinal": set(selected) <= set(expected["allowedSkills"]),
            "forbiddenFinal": not (
                set(selected) & set(expected["forbiddenSkills"])
            ),
            "outcome": decision.outcome == expected["outcome"],
            "promptMode": decision.prompt_mode == expected["promptMode"],
        }
        passed = all(stages.values())
        failed_stages = [name for name in _STAGE_ORDER if not stages[name]]
        if not passed:
            failed_case_ids.append(case["id"])
        for name, hit in stages.items():
            stage_hits[name] += int(hit)
        category_counts[case["category"]] += 1
        category_passes[case["category"]] += int(passed)
        language_counts[case["language"]] += 1
        language_passes[case["language"]] += int(passed)
        for dimension in case["dimensions"]:
            dimension_counts[dimension] += 1
            dimension_passes[dimension] += int(passed)
        result = {
            "id": case["id"],
            "category": case["category"],
            "language": case["language"],
            "dimensions": list(case["dimensions"]),
            "pairId": case["pairId"],
            "passed": passed,
            "failedStages": failed_stages,
            "stagePass": stages,
            "expectedOperations": expected["operations"],
            "actualOperations": actual_operations,
            "expectedContextUsed": expected["contextUsed"],
            "actualContextUsed": bool(decision.intent.context_used),
            "expectedTopSkill": expected["topSkill"],
            "actualSelectedSkills": selected,
            "expectedOutcome": expected["outcome"],
            "actualOutcome": decision.outcome,
            "expectedPromptMode": expected["promptMode"],
            "actualPromptMode": decision.prompt_mode,
        }
        case_results.append(result)
        results_by_id[case["id"]] = result

    pair_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        if case["pairId"] is not None:
            pair_groups[case["pairId"]].append(case)
    pair_hits = 0
    for pair in pair_groups.values():
        first, second = pair
        first_result = results_by_id[first["id"]]
        second_result = results_by_id[second["id"]]
        expected_changed = (
            first["expected"]["topSkill"], first["expected"]["outcome"]
        ) != (second["expected"]["topSkill"], second["expected"]["outcome"])
        actual_changed = (
            first_result["actualSelectedSkills"], first_result["actualOutcome"]
        ) != (second_result["actualSelectedSkills"], second_result["actualOutcome"])
        pair_hits += int(
            bool(first_result["passed"])
            and bool(second_result["passed"])
            and actual_changed is expected_changed
        )

    case_count = len(cases)
    passed_count = case_count - len(failed_case_ids)
    fixture_digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    return {
        "schemaVersion": 1,
        "evaluator": "skill-routing-messy",
        "mode": "offline-real-catalog",
        "suiteId": document.get("suiteId"),
        "fixtureSha256": fixture_digest,
        "catalogSha256": catalog_digest,
        "remoteCallCount": 0,
        "caseCount": case_count,
        "passedCaseCount": passed_count,
        "failedCaseCount": len(failed_case_ids),
        "casePassRate": _ratio(passed_count, case_count),
        "operationExactRate": _ratio(stage_hits["operations"], case_count),
        "contextExactRate": _ratio(stage_hits["context"], case_count),
        "topSkillExactRate": _ratio(stage_hits["topSkill"], case_count),
        "allowedFinalRate": _ratio(stage_hits["allowedFinal"], case_count),
        "forbiddenFinalRate": _ratio(stage_hits["forbiddenFinal"], case_count),
        "outcomeExactRate": _ratio(stage_hits["outcome"], case_count),
        "promptModeExactRate": _ratio(stage_hits["promptMode"], case_count),
        "pairExactRate": _ratio(pair_hits, len(pair_groups)),
        "categoryStats": {
            name: {
                "caseCount": count,
                "passRate": _ratio(category_passes[name], count),
            }
            for name, count in sorted(category_counts.items())
        },
        "languageStats": {
            name: {
                "caseCount": count,
                "passRate": _ratio(language_passes[name], count),
            }
            for name, count in sorted(language_counts.items())
        },
        "dimensionStats": {
            name: {
                "caseCount": count,
                "passRate": _ratio(dimension_passes[name], count),
            }
            for name, count in sorted(dimension_counts.items())
        },
        "failedCaseIds": failed_case_ids,
        "caseResults": case_results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_messy_skill_routing(args.fixture)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(encoded)
    else:
        args.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "evaluate_messy_skill_routing", "main"]
