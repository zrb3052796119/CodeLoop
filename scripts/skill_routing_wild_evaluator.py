"""Evaluate the frozen wild-language Skill-routing holdout offline.

This evaluator is observational.  Its frozen baseline is deliberately low and
must not be interpreted as a promotion threshold.  It exercises the production
``parse_intent -> SkillRouter -> prompt projection`` seam with the repository's
real frozen Skill/Capability catalog, without a model, provider, or network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Keep the CLI entry point runnable as a file as well as with ``python -m``.
# Python otherwise places ``scripts/`` (not the repository root) on sys.path.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from minicode.capability_registry import (  # noqa: E402
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import (  # noqa: E402
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
)
from minicode.skill_router import SkillRouter  # noqa: E402
from minicode.skill_routing_turn import (  # noqa: E402
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)

_DEFAULT_FIXTURE = (
    _REPOSITORY_ROOT
    / "tests/fixtures/agent_quality/skill-routing-wild-holdout.json"
)
_DEFAULT_CATALOG = (
    _REPOSITORY_ROOT
    / "tests/fixtures/agent_quality/intent-skill-production-regressions.json"
)
_PRODUCTION_SOURCES = (
    _REPOSITORY_ROOT / "minicode/intent_parser.py",
    _REPOSITORY_ROOT / "minicode/intent_negation.py",
    _REPOSITORY_ROOT / "minicode/skill_router.py",
    _REPOSITORY_ROOT / "minicode/skill_routing_turn.py",
)
_TOP_LEVEL_KEYS = frozenset({"schemaVersion", "suiteId", "cases"})
_CASE_KEYS = frozenset(
    {
        "id",
        "category",
        "prompt",
        "operations",
        "top",
        "allowed",
        "forbidden",
        "outcome",
        "context",
        "context_used",
        "oracle",
    }
)
_CONTEXT_KEYS = frozenset({"messages", "active"})
_OUTCOMES = frozenset({"route", "no_skill", "clarify", "abstain"})
_ORACLE_CLASSES = frozenset({"solid", "ambiguous"})
_STRICT_STAGES = (
    "operations",
    "context",
    "outcome",
    "top",
    "allowed",
    "forbidden",
)
_LENIENT_STAGES = ("keyIntent", "outcome", "forbidden")
_TOP_K = 5


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON document must be an object: {path}")
    return document


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _digest_files(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(_REPOSITORY_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _string_list(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{label} must be a unique string list")
    return list(value)


def _normalise_operation(value: object, *, case_id: str, index: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"invalid operation {index} for case {case_id}")
    try:
        polarity_text, operation_text = value.split(":", 1)
        intent_text, action_text = operation_text.split(".", 1)
        polarity = IntentPolarity(polarity_text)
        intent_type = IntentType(intent_text)
        action_type = ActionType(action_text)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"invalid operation {index} for case {case_id}"
        ) from error
    return f"{polarity.value}:{intent_type.value}.{action_type.value}"


def _noop_capability(**_kwargs: object) -> None:
    return None


def _build_registry(raw_capabilities: object) -> CapabilityRegistry:
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise ValueError("catalog requires coreCapabilities")
    registry = CapabilityRegistry()
    seen: set[str] = set()
    for index, raw in enumerate(raw_capabilities):
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
                tags=("wild-routing-holdout",),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid core capability at index {index}") from error
        registry.register(metadata, _noop_capability)
        seen.add(name)
    return registry.freeze()


def _load_catalog(
    path: Path,
) -> tuple[list[dict[str, object]], CapabilityRegistry, set[str]]:
    document = _read_json(path)
    snapshot = document.get("catalogSnapshot")
    if not isinstance(snapshot, dict):
        raise TypeError("catalog is missing catalogSnapshot")
    raw_skills = snapshot.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ValueError("catalog has no Skills")
    catalog: list[dict[str, object]] = []
    names: set[str] = set()
    for index, raw_skill in enumerate(raw_skills):
        if not isinstance(raw_skill, dict):
            raise TypeError(f"invalid catalog Skill at index {index}")
        qualified_name = raw_skill.get("qualified_name")
        if (
            not isinstance(qualified_name, str)
            or not qualified_name
            or qualified_name in names
        ):
            raise ValueError(f"invalid catalog Skill at index {index}")
        skill = dict(raw_skill)
        skill.setdefault("path", f"/wild-routing/{qualified_name}/SKILL.md")
        catalog.append(skill)
        names.add(qualified_name)
    return catalog, _build_registry(document.get("coreCapabilities")), names


def _normalise_context(
    value: object,
    *,
    case_id: str,
) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _CONTEXT_KEYS:
        raise ValueError(f"invalid context for case {case_id}")
    messages = value["messages"]
    active = value["active"]
    if (
        not isinstance(messages, list)
        or not messages
        or not all(
            isinstance(message, dict)
            and set(message) == {"role", "content"}
            and message["role"] in {"user", "assistant"}
            and isinstance(message["content"], str)
            and bool(message["content"].strip())
            for message in messages
        )
        or not isinstance(active, str)
    ):
        raise ValueError(f"invalid context for case {case_id}")
    return {
        "messages": [dict(message) for message in messages],
        "active": active,
    }


def _normalise_cases(
    document: dict[str, Any],
    *,
    known_skills: set[str],
) -> list[dict[str, object]]:
    schema_version = document.get("schemaVersion")
    if (
        set(document) != _TOP_LEVEL_KEYS
        or type(schema_version) is not int
        or schema_version != 1
    ):
        raise ValueError("invalid wild holdout document schema")
    suite_id = document.get("suiteId")
    raw_cases = document.get("cases")
    if not isinstance(suite_id, str) or not suite_id:
        raise ValueError("suiteId must be a non-empty string")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("wild holdout requires cases")

    cases: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict) or set(raw_case) != _CASE_KEYS:
            raise ValueError(f"invalid case shape at index {index}")
        case_id = raw_case["id"]
        category = raw_case["category"]
        prompt = raw_case["prompt"]
        oracle = raw_case["oracle"]
        outcome = raw_case["outcome"]
        context_used = raw_case["context_used"]
        top = raw_case["top"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen_ids
            or not isinstance(category, str)
            or not category
            or not isinstance(prompt, str)
            or not prompt.strip()
            or oracle not in _ORACLE_CLASSES
            or outcome not in _OUTCOMES
            or not isinstance(context_used, bool)
            or (top is not None and (not isinstance(top, str) or not top))
        ):
            raise ValueError(f"invalid case identity at index {index}")

        raw_operations = raw_case["operations"]
        if not isinstance(raw_operations, list):
            raise TypeError(f"operations for case {case_id} must be a list")
        operations = [
            _normalise_operation(operation, case_id=case_id, index=op_index)
            for op_index, operation in enumerate(raw_operations)
        ]
        allowed = _string_list(
            raw_case["allowed"], label=f"allowed for case {case_id}"
        )
        forbidden = _string_list(
            raw_case["forbidden"], label=f"forbidden for case {case_id}"
        )
        context = _normalise_context(raw_case["context"], case_id=case_id)
        referenced = set(allowed) | set(forbidden)
        if top is not None:
            referenced.add(top)
        if referenced - known_skills:
            raise ValueError(
                f"case {case_id} references unknown Skills: "
                f"{sorted(referenced - known_skills)!r}"
            )
        if set(allowed) & set(forbidden):
            raise ValueError(f"case {case_id} has conflicting Skill labels")
        if context_used and context is None:
            raise ValueError(f"case {case_id} uses missing context")
        if outcome == "route":
            if top is None or top not in allowed:
                raise ValueError(f"route case {case_id} requires allowed top Skill")
        elif top is not None or allowed:
            raise ValueError(f"non-route case {case_id} cannot allow Skills")

        cases.append(
            {
                "id": case_id,
                "category": category,
                "prompt": prompt,
                "operations": operations,
                "top": top,
                "allowed": allowed,
                "forbidden": forbidden,
                "outcome": outcome,
                "context": context,
                "context_used": context_used,
                "oracle": oracle,
            }
        )
        seen_ids.add(case_id)
    return cases


def _intent_context(case: dict[str, object]) -> IntentContext | None:
    raw_context = case["context"]
    if not isinstance(raw_context, dict):
        return None
    return IntentContext.from_messages(
        raw_context["messages"],
        active_task=str(raw_context["active"]),
    )


def _operation_projection(intent: object) -> list[str]:
    return [
        f"{operation.polarity.value}:"
        f"{operation.intent_type.value}.{operation.action_type.value}"
        for operation in getattr(intent, "operations", ())
    ]


def _stage_stats(hits: Counter[str], total: int, names: tuple[str, ...]) -> dict[str, object]:
    return {
        name: {
            "passed": hits[name],
            "total": total,
            "rate": _ratio(hits[name], total),
        }
        for name in names
    }


def evaluate_wild_skill_routing(
    fixture_path: str | Path = _DEFAULT_FIXTURE,
    *,
    catalog_path: str | Path = _DEFAULT_CATALOG,
) -> dict[str, object]:
    """Run the frozen holdout through the production parse-and-route seam."""

    fixture = Path(fixture_path)
    catalog_file = Path(catalog_path)
    initial_source_digest = _digest_files(_PRODUCTION_SOURCES)
    initial_catalog_digest = hashlib.sha256(catalog_file.read_bytes()).hexdigest()
    document = _read_json(fixture)
    catalog, registry, known_skills = _load_catalog(catalog_file)
    cases = _normalise_cases(document, known_skills=known_skills)

    strict_hits: Counter[str] = Counter()
    lenient_hits: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    category_strict: Counter[str] = Counter()
    category_lenient: Counter[str] = Counter()
    oracle_counts: Counter[str] = Counter()
    oracle_strict: Counter[str] = Counter()
    oracle_lenient: Counter[str] = Counter()
    case_results: list[dict[str, object]] = []
    failed_case_ids: list[str] = []
    strict_pass_count = 0
    lenient_pass_count = 0
    intended_route_count = 0
    intended_route_hits = 0
    intended_top_hits = 0
    intended_allowed_anywhere_hits = 0
    non_route_count = 0
    non_route_hits = 0
    actual_route_count = 0
    actual_route_allowed_hits = 0
    negative_case_count = 0
    negative_case_recall_hits = 0
    negative_case_exact_hits = 0
    negative_operation_count = 0
    negative_operation_hits = 0
    forbidden_case_count = 0
    forbidden_case_hits = 0
    context_case_count = 0
    context_case_hits = 0
    for case in cases:
        decision = route_skill_turn(
            SkillRoutingTurnRequest(
                prompt=str(case["prompt"]),
                context=_intent_context(case),
                skills=catalog,
                registry=registry,
                top_k=_TOP_K,
            ),
            SkillRoutingTurnAdapters(router=SkillRouter()),
        )
        actual_operations = _operation_projection(decision.intent)
        selected = [
            skill.qualified_name
            for skill in getattr(decision.routing, "selected", ())
        ]
        actual_top = selected[0] if selected else None
        expected_operations = list(case["operations"])
        expected_affirmed = [
            operation
            for operation in expected_operations
            if operation.startswith("affirmed:")
        ]
        actual_affirmed = [
            operation
            for operation in actual_operations
            if operation.startswith("affirmed:")
        ]
        expected_negated = Counter(
            operation
            for operation in expected_operations
            if operation.startswith("negated:")
        )
        actual_negated = Counter(
            operation
            for operation in actual_operations
            if operation.startswith("negated:")
        )
        missing_negated = expected_negated - actual_negated
        negated_constraints_hit = not missing_negated
        negated_constraints_exact = actual_negated == expected_negated
        key_intent_hit = (
            expected_affirmed[0] in actual_affirmed
            if expected_affirmed
            else not actual_affirmed
        )
        strict_stages = {
            "operations": actual_operations == expected_operations,
            "context": bool(decision.intent.context_used)
            is bool(case["context_used"]),
            "outcome": decision.outcome == case["outcome"],
            "top": actual_top == case["top"],
            "allowed": set(selected) <= set(case["allowed"]),
            "forbidden": not (set(selected) & set(case["forbidden"])),
        }
        lenient_stages = {
            "keyIntent": key_intent_hit,
            "outcome": strict_stages["outcome"],
            "forbidden": strict_stages["forbidden"],
        }
        strict_passed = all(strict_stages.values())
        lenient_passed = all(lenient_stages.values())
        strict_pass_count += int(strict_passed)
        lenient_pass_count += int(lenient_passed)
        strict_hits.update(
            {name: int(passed) for name, passed in strict_stages.items()}
        )
        lenient_hits.update(
            {name: int(passed) for name, passed in lenient_stages.items()}
        )
        lenient_hits["negatedConstraints"] += int(negated_constraints_hit)

        category = str(case["category"])
        oracle = str(case["oracle"])
        category_counts[category] += 1
        category_strict[category] += int(strict_passed)
        category_lenient[category] += int(lenient_passed)
        oracle_counts[oracle] += 1
        oracle_strict[oracle] += int(strict_passed)
        oracle_lenient[oracle] += int(lenient_passed)

        if case["outcome"] == "route":
            intended_route_count += 1
            intended_route_hits += int(decision.outcome == "route")
            intended_top_hits += int(actual_top == case["top"])
            intended_allowed_anywhere_hits += int(
                bool(set(selected) & set(case["allowed"]))
            )
        else:
            non_route_count += 1
            non_route_hits += int(decision.outcome == case["outcome"])
        if decision.outcome == "route":
            actual_route_count += 1
            actual_route_allowed_hits += int(
                bool(selected) and set(selected) <= set(case["allowed"])
            )
        if expected_negated:
            negative_case_count += 1
            negative_case_recall_hits += int(negated_constraints_hit)
            negative_case_exact_hits += int(negated_constraints_exact)
            negative_operation_count += sum(expected_negated.values())
            negative_operation_hits += (
                sum(expected_negated.values()) - sum(missing_negated.values())
            )
        if case["forbidden"]:
            forbidden_case_count += 1
            forbidden_case_hits += int(strict_stages["forbidden"])
        if case["context"] is not None:
            context_case_count += 1
            context_case_hits += int(strict_stages["context"])

        if not strict_passed:
            failed_case_ids.append(str(case["id"]))
        classification = None
        if not strict_passed:
            classification = (
                "implementation-defect"
                if oracle == "solid"
                else "oracle-ambiguity"
            )
        case_results.append(
            {
                "id": case["id"],
                "category": category,
                "oracle": oracle,
                "classification": classification,
                "prompt": case["prompt"],
                "strictPassed": strict_passed,
                "lenientPassed": lenient_passed,
                "strictStagePass": strict_stages,
                "lenientStagePass": {
                    **lenient_stages,
                    "negatedConstraints": negated_constraints_hit,
                },
                "negatedConstraintsExact": negated_constraints_exact,
                "expectedOperations": expected_operations,
                "actualOperations": actual_operations,
                "expectedContextUsed": case["context_used"],
                "actualContextUsed": bool(decision.intent.context_used),
                "expectedTopSkill": case["top"],
                "actualSelectedSkills": selected,
                "expectedOutcome": case["outcome"],
                "actualOutcome": decision.outcome,
            }
        )

    case_count = len(cases)
    final_source_digest = _digest_files(_PRODUCTION_SOURCES)
    final_catalog_digest = hashlib.sha256(catalog_file.read_bytes()).hexdigest()
    canonical_cases = json.dumps(
        cases,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schemaVersion": 1,
        "evaluator": "skill-routing-wild-holdout",
        "suiteId": document["suiteId"],
        "mode": "offline-real-catalog-no-model",
        "remoteCallCount": 0,
        "measurementDefinitions": {
            "strict": (
                "Exact ordered operations, context flag, outcome, top Skill, "
                "selected-Skill containment, and forbidden-Skill exclusion."
            ),
            "lenient": (
                "First frozen affirmed operation present, outcome exact, and "
                "forbidden-Skill exclusion; negative constraints are reported "
                "separately and are not part of lenient pass."
            ),
            "classification": (
                "A strict failure is implementation-defect only for a corpus "
                "case frozen as solid; predeclared ambiguous cases remain "
                "oracle-ambiguity."
            ),
            "baselinePolicy": (
                "Observational drift snapshot, not a promotion threshold or "
                "an estimate of natural production-traffic accuracy."
            ),
        },
        "fixtureSha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "caseSha256": hashlib.sha256(canonical_cases).hexdigest(),
        "catalogSha256": initial_catalog_digest,
        "productionSourceSha256": initial_source_digest,
        "sourceStableDuringRun": (
            initial_source_digest == final_source_digest
            and initial_catalog_digest == final_catalog_digest
        ),
        "caseCount": case_count,
        "solidCaseCount": oracle_counts["solid"],
        "ambiguousCaseCount": oracle_counts["ambiguous"],
        "strictPassCount": strict_pass_count,
        "strictPassRate": _ratio(strict_pass_count, case_count),
        "lenientPassCount": lenient_pass_count,
        "lenientPassRate": _ratio(lenient_pass_count, case_count),
        "strictStageStats": _stage_stats(
            strict_hits, case_count, _STRICT_STAGES
        ),
        "lenientStageStats": {
            **_stage_stats(lenient_hits, case_count, _LENIENT_STAGES),
            "negatedConstraints": {
                "passed": lenient_hits["negatedConstraints"],
                "total": case_count,
                "rate": _ratio(lenient_hits["negatedConstraints"], case_count),
                "includesVacuousCases": True,
            },
        },
        "negativeConstraintStats": {
            "caseCount": negative_case_count,
            "caseRecallCount": negative_case_recall_hits,
            "caseRecallRate": _ratio(
                negative_case_recall_hits, negative_case_count
            ),
            "caseExactCount": negative_case_exact_hits,
            "caseExactRate": _ratio(
                negative_case_exact_hits, negative_case_count
            ),
            "operationCount": negative_operation_count,
            "operationRecallCount": negative_operation_hits,
            "operationRecallRate": _ratio(
                negative_operation_hits, negative_operation_count
            ),
        },
        "forbiddenSkillStats": {
            "caseCount": forbidden_case_count,
            "passCount": forbidden_case_hits,
            "passRate": _ratio(forbidden_case_hits, forbidden_case_count),
        },
        "contextCaseStats": {
            "caseCount": context_case_count,
            "exactCount": context_case_hits,
            "exactRate": _ratio(context_case_hits, context_case_count),
        },
        "routingStats": {
            "intendedRouteCaseCount": intended_route_count,
            "intendedRouteOutcomeCount": intended_route_hits,
            "intendedRouteOutcomeRecall": _ratio(
                intended_route_hits, intended_route_count
            ),
            "intendedTopSkillCount": intended_top_hits,
            "intendedTopSkillRecall": _ratio(
                intended_top_hits, intended_route_count
            ),
            "acceptableSkillAnywhereCount": intended_allowed_anywhere_hits,
            "acceptableSkillAnywhereRecall": _ratio(
                intended_allowed_anywhere_hits, intended_route_count
            ),
            "nonRouteCaseCount": non_route_count,
            "nonRouteOutcomeExactCount": non_route_hits,
            "nonRouteOutcomeExactRate": _ratio(non_route_hits, non_route_count),
            "actualRoutedCaseCount": actual_route_count,
            "actualRouteAllowedCount": actual_route_allowed_hits,
            "actualRouteAllowedContainmentRate": _ratio(
                actual_route_allowed_hits, actual_route_count
            ),
        },
        "categoryStats": {
            name: {
                "caseCount": count,
                "strictPassCount": category_strict[name],
                "strictPassRate": _ratio(category_strict[name], count),
                "lenientPassCount": category_lenient[name],
                "lenientPassRate": _ratio(category_lenient[name], count),
            }
            for name, count in sorted(category_counts.items())
        },
        "oracleStats": {
            name: {
                "caseCount": count,
                "strictPassCount": oracle_strict[name],
                "strictPassRate": _ratio(oracle_strict[name], count),
                "lenientPassCount": oracle_lenient[name],
                "lenientPassRate": _ratio(oracle_lenient[name], count),
            }
            for name, count in sorted(oracle_counts.items())
        },
        "failedCaseIds": failed_case_ids,
        "caseResults": case_results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", type=Path, default=_DEFAULT_FIXTURE)
    parser.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_wild_skill_routing(
        args.fixture,
        catalog_path=args.catalog,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(encoded)
    else:
        args.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "evaluate_wild_skill_routing", "main"]
