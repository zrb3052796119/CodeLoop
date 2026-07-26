"""Deterministic evaluation helpers for ReflectionEngine golden traces."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Set
from pathlib import Path
from typing import Any


class DatasetValidationError(ValueError):
    """A golden case failed validation with source-local context."""


_CASE_REQUIRED_KEYS = {
    "schema_version",
    "case_id",
    "category",
    "description",
    "task_description",
    "trace",
    "expected_evidence",
    "expected_value",
    "expected_claims",
    "forbidden_claims",
    "notes",
}
_EVIDENCE_FIELDS = {
    "files_read",
    "files_changed",
    "tools",
    "libraries",
    "errors",
    "recoveries",
    "decisions",
    "verification",
    "outcome",
}
_VALID_CATEGORIES = {
    "path_extraction",
    "library_detection",
    "error_deduplication",
    "recovery_and_verification",
    "low_value_tasks",
    "decisions_and_constraints",
    "security_and_redaction",
    "multilingual_and_edge_cases",
}
_VALID_LIBRARY_STATUSES = {"confirmed", "weak_mention", "not_dependency"}
_VALID_CLAIM_TYPES = {
    "constraint",
    "dependency",
    "error_pattern",
    "root_cause",
    "recovery",
    "decision",
    "correction",
    "verification_rule",
    "warning",
}
_CLAIM_REQUIRED_KEYS = {
    "claim_id",
    "claim_type",
    "semantic_key",
    "evidence_ids",
    "epistemic_status",
    "required_terms",
    "forbidden_terms",
}
_LEGACY_CAPABILITY_GAPS = [
    "file_access_role",
    "dependency_certainty",
    "error_call_id_association",
    "verification_evidence",
    "claim_evidence_references",
    "epistemic_status",
]
_TASK_EVIDENCE_CAPABILITY_GAPS: list[str] = []

_CLAIM_TYPE_ALIASES = {
    "failure": {"error_pattern", "warning"},
    "fix": {"recovery"},
    "recovery_result": {"recovery", "error_pattern", "warning"},
    "security_policy": {"decision", "constraint"},
}


def precision_recall_f1(expected: Set[Any], actual: Set[Any]) -> dict[str, int | float]:
    """Return exact-set precision, recall, and F1 without NaN values."""
    true_positives = len(expected & actual)
    false_positives = len(actual - expected)
    false_negatives = len(expected - actual)

    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    precision = true_positives / precision_denominator if precision_denominator else 1.0
    recall = true_positives / recall_denominator if recall_denominator else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _fail(source: Path, message: str) -> None:
    raise DatasetValidationError(f"{source}: {message}")


def _contains_unredacted_secret(value: Any) -> bool:
    """Scan arbitrary fixture data iteratively so deep inputs cannot recurse."""
    stack = [value]
    seen: set[int] = set()
    visited = 0
    while stack:
        current = stack.pop()
        visited += 1
        if visited > 200_000:
            return True
        if isinstance(current, str):
            if (
                _OPENAI_STYLE_KEY_RE.search(current)
                or _SECRET_ASSIGNMENT_RE.search(current)
                or _BEARER_RE.search(current)
            ):
                return True
            continue
        if isinstance(current, (dict, list)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return False


def validate_case(case: Any, source: Path) -> None:
    """Validate one manually labelled golden case."""
    if not isinstance(case, dict):
        _fail(source, "case must be an object")
    missing = sorted(_CASE_REQUIRED_KEYS - set(case))
    if missing:
        _fail(source, f"case {case.get('case_id', '<unknown>')} missing keys: {missing}")
    if case["schema_version"] != 1:
        _fail(source, f"case {case['case_id']} has unsupported schema_version")
    if not isinstance(case["case_id"], str) or not case["case_id"].strip():
        _fail(source, "case_id must be a non-empty string")
    for field in ("category", "description", "task_description", "notes"):
        if not isinstance(case[field], str):
            _fail(source, f"case {case['case_id']} {field} must be a string")
    if case["category"] not in _VALID_CATEGORIES:
        _fail(source, f"case {case['case_id']} has unknown category {case['category']}")
    if _contains_unredacted_secret(case):
        _fail(source, f"case {case['case_id']} contains an unredacted secret")
    if not isinstance(case["trace"], list):
        _fail(source, f"case {case['case_id']} trace must be a list")

    event_ids: list[str] = []
    for index, event in enumerate(case["trace"]):
        if not isinstance(event, dict):
            _fail(source, f"case {case['case_id']} trace[{index}] must be an object")
        event_id = event.get("event_id")
        if event_id is None:
            event_id = f"legacy-event-{index + 1:06d}"
        elif not isinstance(event_id, str) or not event_id:
            _fail(source, f"case {case['case_id']} trace[{index}] has invalid event_id")
        if not isinstance(event.get("type"), str) or not event.get("type"):
            _fail(source, f"case {case['case_id']} trace[{index}] missing type")
        event_ids.append(event_id)
    if len(event_ids) != len(set(event_ids)):
        _fail(source, f"case {case['case_id']} contains duplicate event_id")

    evidence = case["expected_evidence"]
    if not isinstance(evidence, dict):
        _fail(source, f"case {case['case_id']} expected_evidence must be an object")
    missing_evidence = sorted(_EVIDENCE_FIELDS - set(evidence))
    if missing_evidence:
        _fail(source, f"case {case['case_id']} missing evidence fields: {missing_evidence}")
    for field in _EVIDENCE_FIELDS - {"outcome"}:
        if not isinstance(evidence[field], list):
            _fail(source, f"case {case['case_id']} evidence.{field} must be a list")
    if evidence["outcome"] not in {"success", "failed", "unknown"}:
        _fail(source, f"case {case['case_id']} has invalid outcome")

    for library in evidence["libraries"]:
        if not isinstance(library, dict) or not isinstance(library.get("name"), str):
            _fail(source, f"case {case['case_id']} library evidence requires name")
        if library.get("status") not in _VALID_LIBRARY_STATUSES:
            _fail(source, f"case {case['case_id']} has invalid library status")

    if not isinstance(case["expected_claims"], list):
        _fail(source, f"case {case['case_id']} expected_claims must be a list")
    for claim in case["expected_claims"]:
        if not isinstance(claim, dict):
            _fail(source, f"case {case['case_id']} claim must be an object")
        missing_claim_keys = sorted(_CLAIM_REQUIRED_KEYS - set(claim))
        if missing_claim_keys:
            _fail(
                source,
                f"case {case['case_id']} claim missing keys: {missing_claim_keys}",
            )
    if not isinstance(case["forbidden_claims"], list):
        _fail(source, f"case {case['case_id']} forbidden_claims must be a list")
    probe_claims = case.get("validation_probe_claims", [])
    if not isinstance(probe_claims, list):
        _fail(source, f"case {case['case_id']} validation_probe_claims must be a list")
    for probe in probe_claims:
        if not isinstance(probe, dict):
            _fail(source, f"case {case['case_id']} validation probe must be an object")
        required_probe = {
            "claim_id",
            "claim_type",
            "semantic_key",
            "statement",
            "evidence_ids",
            "epistemic_status",
        }
        missing_probe = sorted(required_probe - set(probe))
        if missing_probe:
            _fail(source, f"case {case['case_id']} validation probe missing keys: {missing_probe}")
        if probe["claim_type"] not in _VALID_CLAIM_TYPES:
            _fail(source, f"case {case['case_id']} validation probe has invalid claim_type")

    expected_value = case["expected_value"]
    if not isinstance(expected_value, dict) or not isinstance(
        expected_value.get("should_write_memory"), bool
    ):
        _fail(source, f"case {case['case_id']} expected_value is invalid")
    reasons = expected_value.get("reasons")
    if (
        not isinstance(reasons, list)
        or not reasons
        or not all(isinstance(reason, str) and reason for reason in reasons)
    ):
        _fail(source, f"case {case['case_id']} expected_value requires reasons")

    known_event_ids = set(event_ids)
    events_by_id = {
        event.get("event_id", f"legacy-event-{index + 1:06d}"): event
        for index, event in enumerate(case["trace"])
    }
    referenced_items = list(case["expected_claims"]) + list(evidence["libraries"])
    for field in ("errors", "recoveries", "decisions", "verification"):
        value = evidence[field]
        if not all(isinstance(item, dict) for item in value):
            _fail(source, f"case {case['case_id']} evidence.{field} items must be objects")
        referenced_items.extend(value)
    for item in referenced_items:
        references = item.get("evidence_ids", item.get("source_event_ids", []))
        if not isinstance(references, list):
            _fail(source, f"case {case['case_id']} has non-list evidence references")
        missing_references = sorted(set(references) - known_event_ids)
        if missing_references:
            _fail(
                source,
                f"case {case['case_id']} references missing event ids: {missing_references}",
            )

    for error in evidence["errors"]:
        if not isinstance(error, dict) or not error.get("error_type"):
            continue
        source_ids = error.get("source_event_ids", error.get("evidence_ids", []))
        source_text = _normalize_text(
            json.dumps(
                [events_by_id[event_id] for event_id in source_ids if event_id in events_by_id],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        error_type = _normalize_text(error["error_type"])
        if error_type not in source_text:
            _fail(
                source,
                f"case {case['case_id']} error_type {error['error_type']} is not grounded in its source events",
            )


def load_dataset(dataset_root: str | Path) -> list[dict[str, Any]]:
    """Load and validate all golden cases in deterministic case-id order."""
    root = Path(dataset_root)
    cases_dir = root / "cases"
    if not cases_dir.is_dir():
        _fail(root, "missing cases directory")

    loaded: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for path in sorted(cases_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(path, f"cannot parse JSON: {exc}")
        cases = document.get("cases") if isinstance(document, dict) else None
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            _fail(path, "document has unsupported schema_version")
        if not isinstance(cases, list):
            _fail(path, "document must contain a cases list")
        for case in cases:
            validate_case(case, path)
            case_id = case["case_id"]
            if case_id in seen_case_ids:
                _fail(path, f"duplicate case_id: {case_id}")
            seen_case_ids.add(case_id)
            loaded.append(case)
    return sorted(loaded, key=lambda case: case["case_id"])


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())


def _normalize_path(value: Any) -> str:
    return _normalize_text(value).replace("\\", "/")


def _count_metrics(
    true_positives: int,
    false_positives: int,
    false_negatives: int,
) -> dict[str, int | float]:
    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    precision = true_positives / precision_denominator if precision_denominator else 1.0
    recall = true_positives / recall_denominator if recall_denominator else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _expected_library_names(items: list[Any]) -> set[str]:
    names: set[str] = set()
    for item in items:
        if isinstance(item, str):
            names.add(_normalize_text(item))
        elif isinstance(item, dict) and item.get("status") == "confirmed":
            names.add(_normalize_text(item.get("name", "")))
    return {name for name in names if name}


def _required_terms(item: Any) -> list[str]:
    if isinstance(item, str):
        return [_normalize_text(item)]
    if not isinstance(item, dict):
        return []
    terms = item.get("required_terms", [])
    return [_normalize_text(term) for term in terms if _normalize_text(term)]


def _semantic_matches(
    expected_items: list[Any],
    actual_items: list[str],
) -> tuple[dict[str, int | float], set[int], set[int]]:
    matched_expected: set[int] = set()
    matched_actual: set[int] = set()
    normalized_actual = [_normalize_text(item) for item in actual_items]
    for expected_index, expected in enumerate(expected_items):
        terms = _required_terms(expected)
        if not terms:
            continue
        for actual_index, actual in enumerate(normalized_actual):
            if actual_index in matched_actual:
                continue
            if all(term in actual for term in terms):
                matched_expected.add(expected_index)
                matched_actual.add(actual_index)
                break
    true_positives = len(matched_expected)
    metrics = _count_metrics(
        true_positives,
        len(actual_items) - len(matched_actual),
        len(expected_items) - len(matched_expected),
    )
    return metrics, matched_expected, matched_actual


def _canonical_error(value: str) -> str:
    normalized = _normalize_text(value)
    if ": " in normalized:
        normalized = normalized.split(": ", 1)[1]
    normalized = re.sub(r"\bline\s+\d+\b", "line <n>", normalized)
    return normalized


def _forbidden_claim_count(forbidden: list[Any], actual_claims: list[str]) -> int:
    count = 0
    normalized_claims = [_normalize_text(claim) for claim in actual_claims]
    for item in forbidden:
        terms = _required_terms(item)
        if terms and any(
            all(term in claim for term in terms)
            and not any(
                re.search(
                    rf"\b(?:do not|don't|must not|never)\s+{re.escape(term)}\b",
                    claim,
                )
                for term in terms
            )
            for claim in normalized_claims
        ):
            count += 1
    return count


def _claim_type_matches(expected_type: str, actual_type: str) -> bool:
    return actual_type == expected_type or actual_type in _CLAIM_TYPE_ALIASES.get(
        expected_type, set()
    )


def _structured_claim_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    statement = _normalize_text(actual.get("statement", ""))
    if not _claim_type_matches(
        _normalize_text(expected.get("claim_type", "")),
        _normalize_text(actual.get("claim_type", "")),
    ):
        return False
    if not all(term in statement for term in _required_terms(expected)):
        return False
    if any(
        _normalize_text(term) in statement
        for term in expected.get("forbidden_terms", [])
        if _normalize_text(term)
    ):
        return False
    expected_status = expected.get("epistemic_status")
    if expected_status and actual.get("epistemic_status") != expected_status:
        return False
    expected_ids = set(expected.get("evidence_ids", []))
    if not expected_ids <= set(actual.get("evidence_ids", [])):
        return False
    expected_verification = set(expected.get("verification_ids", []))
    if not expected_verification <= set(actual.get("verification_ids", [])):
        return False
    if expected.get("requires_applies_when") and not actual.get("applies_when"):
        return False
    if expected.get("requires_limitations") and not actual.get("limitations"):
        return False
    return True


def _match_structured_claims(
    expected_items: list[dict[str, Any]],
    actual_items: list[dict[str, Any]],
) -> tuple[dict[str, int | float], set[int], set[int], int]:
    matched_expected: set[int] = set()
    matched_actual: set[int] = set()
    semantic_key_mismatches = 0
    for expected_index, expected in enumerate(expected_items):
        for actual_index, actual in enumerate(actual_items):
            if actual_index in matched_actual:
                continue
            if not _structured_claim_matches(expected, actual):
                continue
            matched_expected.add(expected_index)
            matched_actual.add(actual_index)
            if expected.get("semantic_key") != actual.get("semantic_key"):
                semantic_key_mismatches += 1
            break
    metrics = _count_metrics(
        len(matched_expected),
        len(actual_items) - len(matched_actual),
        len(expected_items) - len(matched_expected),
    )
    return metrics, matched_expected, matched_actual, semantic_key_mismatches


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Evaluate ReflectionEngine, preferring structured TaskEvidence when present."""
    from minicode.agent_reflection import ReflectionEngine

    engine = ReflectionEngine(memory_manager=None, persist_reflections=False)
    try:
        reflection = engine.reflect(case["task_description"], case["trace"])
    except Exception as exc:  # The baseline records engine failures instead of aborting.
        return {
            "case_id": case["case_id"],
            "category": case["category"],
            "event_count": len(case["trace"]),
            "engine_error": f"{type(exc).__name__}: {exc}",
            "capability_gaps": list(_LEGACY_CAPABILITY_GAPS),
        }

    context = reflection.task_context
    task_evidence = getattr(reflection, "task_evidence", None)
    if task_evidence is not None:
        actual_errors = [
            {
                "error_id": item.error_id,
                "call_id": item.call_id,
                "tool_name": item.tool_name,
                "error_type": item.error_type,
                "message": item.message,
                "source_event_ids": list(item.source_event_ids),
                "epistemic_status": item.epistemic_status,
            }
            for item in task_evidence.errors
        ]
        actual_recoveries = [
            {
                "recovery_id": item.recovery_id,
                "related_error_ids": list(item.related_error_ids),
                "action": item.action,
                "evidence_ids": list(item.event_ids),
                "epistemic_status": item.epistemic_status,
            }
            for item in task_evidence.recoveries
        ]
        actual_decisions = [
            {
                "decision_id": item.decision_id,
                "statement": item.statement,
                "rationale": item.rationale,
                "evidence_ids": list(item.event_ids),
                "epistemic_status": item.epistemic_status,
            }
            for item in task_evidence.decisions
        ]
        actual_verification = [
            {
                "verification_id": item.verification_id,
                "tool_name": item.tool_name,
                "call_id": item.call_id,
                "command_kind": item.command_kind,
                "scope": item.scope,
                "result": item.result,
                "summary": item.summary,
                "evidence_ids": list(item.event_ids),
            }
            for item in task_evidence.verification
        ]
        actual_evidence = {
            "files_read": sorted({_normalize_path(item.path) for item in task_evidence.files_read}),
            "files_changed": sorted({_normalize_path(item.path) for item in task_evidence.files_changed}),
            "referenced_files": sorted({_normalize_path(item.path) for item in task_evidence.referenced_files}),
            "tools": sorted({_normalize_text(item.tool_name) for item in task_evidence.tool_calls}),
            "libraries": sorted({
                _normalize_text(item.name)
                for item in task_evidence.libraries
                if item.status == "confirmed"
            }),
            "errors": actual_errors,
            "recoveries": actual_recoveries,
            "recovery_suggestions": [
                {
                    "suggestion_id": item.suggestion_id,
                    "suggestion": item.suggestion,
                    "evidence_ids": list(item.event_ids),
                }
                for item in task_evidence.recovery_suggestions
            ],
            "decisions": actual_decisions,
            "verification": actual_verification,
            "outcome": task_evidence.outcome,
            "had_errors": task_evidence.had_errors,
            "errors_recovered": task_evidence.errors_recovered,
        }
        capability_gaps = list(_TASK_EVIDENCE_CAPABILITY_GAPS)
    else:
        actual_errors = [
            {
                "error_id": f"legacy-error-{index}",
                "call_id": None,
                "tool_name": None,
                "error_type": None,
                "message": message,
                "source_event_ids": [],
                "epistemic_status": "unknown",
            }
            for index, message in enumerate(reflection.errors_encountered, start=1)
        ]
        actual_recoveries = [
            {"action": action, "evidence_ids": []}
            for action in context.get("recoveries", [])
        ]
        actual_decisions = [
            {"statement": statement, "evidence_ids": []}
            for statement in reflection.key_decisions
        ]
        actual_verification: list[dict[str, Any]] = []
        actual_evidence = {
            "files_read": sorted({_normalize_path(path) for path in context.get("files", [])}),
            "files_changed": [],
            "referenced_files": [],
            "tools": sorted({_normalize_text(tool) for tool in context.get("tools", [])}),
            "libraries": sorted({_normalize_text(lib) for lib in context.get("libraries", [])}),
            "errors": actual_errors,
            "recoveries": actual_recoveries,
            "recovery_suggestions": [],
            "decisions": actual_decisions,
            "verification": actual_verification,
            "outcome": "success" if reflection.success else "failed",
            "had_errors": bool(actual_errors),
            "errors_recovered": bool(actual_recoveries),
        }
        capability_gaps = list(_LEGACY_CAPABILITY_GAPS)

    expected = case["expected_evidence"]
    evidence_metrics: dict[str, dict[str, int | float]] = {}
    evidence_metrics["files_read"] = precision_recall_f1(
        {_normalize_path(path) for path in expected["files_read"]},
        set(actual_evidence["files_read"]),
    )
    evidence_metrics["files_changed"] = precision_recall_f1(
        {_normalize_path(path) for path in expected["files_changed"]},
        set(actual_evidence["files_changed"]),
    )
    evidence_metrics["tools"] = precision_recall_f1(
        {_normalize_text(tool) for tool in expected["tools"]},
        set(actual_evidence["tools"]),
    )
    evidence_metrics["libraries"] = precision_recall_f1(
        _expected_library_names(expected["libraries"]),
        set(actual_evidence["libraries"]),
    )

    semantic_actual = {
        "errors": [
            " ".join(
                str(value)
                for value in (
                    item.get("tool_name"),
                    item.get("error_type"),
                    item.get("message"),
                )
                if value
            )
            for item in actual_errors
        ],
        "recoveries": [str(item.get("action", "")) for item in actual_recoveries],
        "decisions": [
            " ".join(
                str(value)
                for value in (item.get("statement"), item.get("rationale"))
                if value
            )
            for item in actual_decisions
        ],
        "verification": [
            " ".join(
                str(value)
                for value in (
                    item.get("command_kind"),
                    item.get("scope"),
                    item.get("result"),
                    item.get("summary"),
                )
                if value
            )
            for item in actual_verification
        ],
    }
    semantic_matches: dict[str, tuple[set[int], set[int]]] = {}
    for field in ("errors", "recoveries", "decisions", "verification"):
        metrics, matched_expected, matched_actual = _semantic_matches(
            expected[field], semantic_actual[field]
        )
        evidence_metrics[field] = metrics
        semantic_matches[field] = (matched_expected, matched_actual)

    expected_errors = expected["errors"]
    canonical_errors = {
        (error.get("call_id"), _canonical_error(str(error.get("message", ""))))
        for error in actual_errors
    }
    error_true_positives = int(evidence_metrics["errors"]["true_positives"])
    error_denominator = max(len(expected_errors), len(actual_errors))
    error_deduplication = {
        "expected_logical_errors": len(expected_errors),
        "actual_error_records": len(actual_errors),
        "actual_unique_errors": len(canonical_errors),
        "duplicate_error_records": max(0, len(actual_errors) - len(canonical_errors)),
        "merge_accuracy": error_true_positives / error_denominator if error_denominator else 1.0,
        "call_id_association_errors": sum(
            1
            for error in expected_errors
            if isinstance(error, dict)
            and error.get("call_id")
            and not any(
                actual.get("call_id") == error.get("call_id")
                and all(
                    term in _normalize_text(
                        " ".join(
                            str(value)
                            for value in (
                                actual.get("tool_name"),
                                actual.get("error_type"),
                                actual.get("message"),
                            )
                            if value
                        )
                    )
                    for term in _required_terms(error)
                )
                for actual in actual_errors
            )
        ),
    }

    evidence_reference_errors = sum(
        1
        for items in (actual_errors, actual_recoveries, actual_decisions, actual_verification)
        for item in items
        if not (item.get("source_event_ids") or item.get("evidence_ids"))
    )

    candidate = getattr(reflection, "reflection_candidate", None)
    validation = getattr(reflection, "claim_validation", None)
    value_decision = getattr(reflection, "value_decision", None)
    structured_claims = getattr(reflection, "structured_claims", None)
    probe_claims = case.get("validation_probe_claims")
    if isinstance(probe_claims, list) and task_evidence is not None:
        from minicode.reflection_synthesis import (
            ReflectionCandidate,
            ReflectionClaim,
            ReflectionClaimValidator,
            ReflectionValueGate,
        )

        candidate = ReflectionCandidate(
            task_summary=case["task_description"][:200],
            outcome=task_evidence.outcome,
            claims=[
                ReflectionClaim(
                    claim_id=str(item.get("claim_id", f"probe-{index:06d}")),
                    claim_type=item.get("claim_type", "warning"),
                    semantic_key=str(item.get("semantic_key", f"probe_{index:06d}")),
                    statement=str(item.get("statement", "")),
                    evidence_ids=list(item.get("evidence_ids", [])),
                    epistemic_status=item.get("epistemic_status", "unknown"),
                    applies_when=str(item.get("applies_when", "")),
                    limitations=list(item.get("limitations", [])),
                    verification_ids=list(item.get("verification_ids", [])),
                    related_error_ids=list(item.get("related_error_ids", [])),
                    related_recovery_ids=list(item.get("related_recovery_ids", [])),
                )
                for index, item in enumerate(probe_claims, start=1)
                if isinstance(item, dict)
            ],
        )
        validation = ReflectionClaimValidator().validate(candidate, task_evidence)
        value_decision = ReflectionValueGate().evaluate(
            candidate, validation, task_evidence
        )
        accepted_ids = set(value_decision.accepted_claim_ids)
        structured_claims = [
            claim
            for claim in validation.valid_claims
            if value_decision.accepted and claim.claim_id in accepted_ids
        ]
    if candidate is not None and validation is not None and structured_claims is not None:
        generated_claim_records = [claim.to_dict() for claim in candidate.claims]
        valid_claim_records = [claim.to_dict() for claim in validation.valid_claims]
        rejected_claim_records = [claim.to_dict() for claim in validation.rejected_claims]
        persistable_claim_records = [claim.to_dict() for claim in structured_claims]
        validation_issues = [issue.to_dict() for issue in validation.issues]
        capability_gaps = []
    else:
        legacy_claims = list(reflection.key_decisions) + list(reflection.lessons_learned)
        generated_claim_records = [
            {
                "claim_id": f"legacy-claim-{index:06d}",
                "claim_type": "warning",
                "semantic_key": f"legacy_claim_{index:06d}",
                "statement": statement,
                "evidence_ids": [],
                "epistemic_status": "unknown",
                "applies_when": "",
                "limitations": [],
                "verification_ids": [],
                "related_error_ids": [],
                "related_recovery_ids": [],
            }
            for index, statement in enumerate(legacy_claims, start=1)
        ]
        valid_claim_records = []
        rejected_claim_records = generated_claim_records
        persistable_claim_records = []
        validation_issues = []
        capability_gaps = list(_LEGACY_CAPABILITY_GAPS)

    (
        claim_metrics,
        matched_expected_claims,
        _matched_actual_claims,
        semantic_key_mismatches,
    ) = _match_structured_claims(case["expected_claims"], persistable_claim_records)
    persistable_statements = [
        str(claim.get("statement", "")) for claim in persistable_claim_records
    ]
    forbidden_claims = _forbidden_claim_count(
        case["forbidden_claims"], persistable_statements
    )
    persistable_ids = {
        str(claim.get("claim_id", "")) for claim in persistable_claim_records
    }
    error_issue_codes = {
        "invalid_evidence_reference",
        "invalid_verification_reference",
        "invalid_error_reference",
        "invalid_recovery_reference",
    }
    invalid_reference_claim_ids = {
        str(issue.get("claim_id", ""))
        for issue in validation_issues
        if issue.get("code") in error_issue_codes
    }
    epistemic_issue_ids = {
        str(issue.get("claim_id", ""))
        for issue in validation_issues
        if issue.get("code") == "epistemic_status_overclaim"
    }
    claims_without_reference = sum(
        1 for claim in persistable_claim_records if not claim.get("evidence_ids")
    )
    invalid_references = len(persistable_ids & invalid_reference_claim_ids)
    epistemic_mismatches = len(persistable_ids & epistemic_issue_ids)
    missing_applies_when = sum(
        1
        for claim in persistable_claim_records
        if claim.get("claim_type")
        in {
            "error_pattern",
            "root_cause",
            "recovery",
            "decision",
            "verification_rule",
            "warning",
        }
        and not claim.get("applies_when")
    )
    missing_limitations = sum(
        1
        for claim in persistable_claim_records
        if claim.get("epistemic_status") == "inferred" and not claim.get("limitations")
    )
    semantic_keys = [
        str(claim.get("semantic_key", "")) for claim in persistable_claim_records
    ]
    duplicate_semantic_keys = len(semantic_keys) - len(set(semantic_keys))
    generic_success_tool_claims = sum(
        1
        for statement in persistable_statements
        if any(
            marker in _normalize_text(statement)
            for marker in (
                "task completed successfully",
                "used unique tool",
                "used tools",
                "errors occurred with tool",
            )
        )
    )
    confirmed_recovery_without_verification = sum(
        1
        for claim in persistable_claim_records
        if claim.get("claim_type") == "recovery"
        and claim.get("epistemic_status") == "confirmed"
        and not claim.get("verification_ids")
    )
    confirmed_root_cause_without_full_chain = sum(
        1
        for claim in persistable_claim_records
        if claim.get("claim_type") == "root_cause"
        and claim.get("epistemic_status") == "confirmed"
        and not (
            claim.get("verification_ids")
            and claim.get("related_error_ids")
            and claim.get("related_recovery_ids")
        )
    )
    unsupported_accepted = sum(
        1
        for claim in persistable_claim_records
        if claim.get("claim_id")
        not in {valid.get("claim_id") for valid in valid_claim_records}
    )
    claims = {
        "generated_claims": len(generated_claim_records),
        "valid_claims": len(valid_claim_records),
        "rejected_claims": len(rejected_claim_records),
        "persistable_claims": len(persistable_claim_records),
        "supported_accepted_claims": len(persistable_claim_records) - unsupported_accepted,
        "unsupported_accepted_claims": unsupported_accepted,
        "matched_expected_claims": len(matched_expected_claims),
        "missing_required_claims": len(case["expected_claims"]) - len(matched_expected_claims),
        "forbidden_accepted_claims": forbidden_claims,
        "claims_without_evidence_reference": claims_without_reference,
        "invalid_evidence_references": invalid_references,
        "epistemic_status_mismatches": epistemic_mismatches,
        "missing_applies_when": missing_applies_when,
        "missing_limitations": missing_limitations,
        "duplicate_semantic_keys": duplicate_semantic_keys,
        "semantic_key_mismatches": semantic_key_mismatches,
        "confirmed_recovery_without_verification": confirmed_recovery_without_verification,
        "confirmed_root_cause_without_full_chain": confirmed_root_cause_without_full_chain,
        "generic_success_tool_count_claims": generic_success_tool_claims,
        "supported_claims": len(matched_expected_claims),
        "unsupported_claims": unsupported_accepted,
        "forbidden_claims": forbidden_claims,
        "metrics": claim_metrics,
        "generated": generated_claim_records,
        "valid": valid_claim_records,
        "rejected": rejected_claim_records,
        "actual_claims": persistable_statements,
        "validation_issues": validation_issues,
    }

    outcome_correct = actual_evidence["outcome"] == _normalize_text(expected["outcome"])
    fact_error_count = sum(
        int(metrics["false_positives"]) + int(metrics["false_negatives"])
        for metrics in evidence_metrics.values()
    ) + (0 if outcome_correct else 1)
    predicted_write = bool(value_decision and value_decision.accepted)
    expected_write = bool(case["expected_value"]["should_write_memory"])

    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "event_count": len(case["trace"]),
        "confidence": reflection.confidence,
        "predicted_write": predicted_write,
        "expected_write": expected_write,
        "value_reason_codes": list(
            getattr(value_decision, "reason_codes", ["legacy_result_default_denied"])
        ),
        "durable_signals": list(getattr(value_decision, "durable_signals", [])),
        "low_value_false_write": predicted_write and not expected_write,
        "actual_evidence": actual_evidence,
        "evidence_metrics": evidence_metrics,
        "outcome_correct": outcome_correct,
        "error_deduplication": error_deduplication,
        "evidence_reference_errors": evidence_reference_errors,
        "claims": claims,
        "fact_error_count": fact_error_count,
        "conclusion_correct": (
            fact_error_count == 0
            and claims["unsupported_claims"] == 0
            and claims["missing_required_claims"] == 0
            and forbidden_claims == 0
        ),
        "capability_gaps": capability_gaps,
        "engine_error": None,
    }


def _aggregate_metric_rows(rows: list[dict[str, int | float]]) -> dict[str, int | float]:
    return _count_metrics(
        sum(int(row["true_positives"]) for row in rows),
        sum(int(row["false_positives"]) for row in rows),
        sum(int(row["false_negatives"]) for row in rows),
    )


def _confidence_bin(confidence: float) -> str:
    if confidence < 0.5:
        return "[0.0,0.5)"
    if confidence < 0.7:
        return "[0.5,0.7)"
    if confidence < 0.9:
        return "[0.7,0.9)"
    return "[0.9,1.0]"


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _pearson_event_confidence(results: list[dict[str, Any]]) -> float:
    points = [
        (float(result["event_count"]), float(result["confidence"]))
        for result in results
        if result.get("engine_error") is None
    ]
    if len(points) < 2:
        return 0.0
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator_x = math.sqrt(sum((x - mean_x) ** 2 for x, _ in points))
    denominator_y = math.sqrt(sum((y - mean_y) ** 2 for _, y in points))
    denominator = denominator_x * denominator_y
    return numerator / denominator if denominator else 0.0


def _summarize_result_slice(results: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [result for result in results if result.get("engine_error") is None]
    evidence_fields = [
        "files_read",
        "files_changed",
        "tools",
        "libraries",
        "errors",
        "recoveries",
        "decisions",
        "verification",
    ]
    evidence = {
        field: _aggregate_metric_rows(
            [result["evidence_metrics"][field] for result in successful]
        )
        for field in evidence_fields
    }
    confusion = {
        "should_write_and_accepted": 0,
        "should_write_but_rejected": 0,
        "should_not_write_but_accepted": 0,
        "should_not_write_and_rejected": 0,
    }
    for result in successful:
        expected = result["expected_write"]
        actual = result["predicted_write"]
        if expected and actual:
            confusion["should_write_and_accepted"] += 1
        elif expected:
            confusion["should_write_but_rejected"] += 1
        elif actual:
            confusion["should_not_write_but_accepted"] += 1
        else:
            confusion["should_not_write_and_rejected"] += 1
    true_positive = confusion["should_write_and_accepted"]
    false_positive = confusion["should_not_write_but_accepted"]
    false_negative = confusion["should_write_but_rejected"]
    negatives = false_positive + confusion["should_not_write_and_rejected"]
    claim_keys = (
        "generated_claims",
        "valid_claims",
        "rejected_claims",
        "persistable_claims",
        "supported_accepted_claims",
        "unsupported_accepted_claims",
        "missing_required_claims",
        "forbidden_accepted_claims",
        "claims_without_evidence_reference",
        "invalid_evidence_references",
        "epistemic_status_mismatches",
        "missing_applies_when",
        "missing_limitations",
        "duplicate_semantic_keys",
        "confirmed_recovery_without_verification",
        "confirmed_root_cause_without_full_chain",
        "generic_success_tool_count_claims",
    )
    return {
        "case_count": len(results),
        "evidence_extraction": evidence,
        "outcome_accuracy": {
            "correct": sum(1 for result in successful if result["outcome_correct"]),
            "incorrect": sum(1 for result in successful if not result["outcome_correct"]),
            "accuracy": _safe_ratio(
                sum(1 for result in successful if result["outcome_correct"]),
                len(successful),
            ),
            "mismatch_cases": [
                result["case_id"]
                for result in successful
                if not result["outcome_correct"]
            ],
        },
        "value_selection": {
            "confusion_matrix": confusion,
            "metrics": _count_metrics(true_positive, false_positive, false_negative),
            "low_value_false_write_rate": _safe_ratio(false_positive, negatives),
        },
        "claims": {
            key: sum(int(result["claims"].get(key, 0)) for result in successful)
            for key in claim_keys
        },
    }


def evaluate_dataset(
    dataset_root: str | Path,
    include_case_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Evaluate a complete dataset and aggregate deterministic baseline metrics."""
    cases = load_dataset(dataset_root)
    if include_case_ids is not None:
        cases = [case for case in cases if case["case_id"] in include_case_ids]
    results = [evaluate_case(case) for case in cases]
    successful = [result for result in results if result.get("engine_error") is None]

    evidence_fields = [
        "files_read",
        "files_changed",
        "tools",
        "libraries",
        "errors",
        "recoveries",
        "decisions",
        "verification",
    ]
    evidence_metrics = {
        field: _aggregate_metric_rows(
            [result["evidence_metrics"][field] for result in successful]
        )
        for field in evidence_fields
    }

    confusion = {
        "should_write_and_accepted": 0,
        "should_write_but_rejected": 0,
        "should_not_write_but_accepted": 0,
        "should_not_write_and_rejected": 0,
    }
    for result in successful:
        expected = result["expected_write"]
        actual = result["predicted_write"]
        if expected and actual:
            confusion["should_write_and_accepted"] += 1
        elif expected:
            confusion["should_write_but_rejected"] += 1
        elif actual:
            confusion["should_not_write_but_accepted"] += 1
        else:
            confusion["should_not_write_and_rejected"] += 1
    value_true_positive = confusion["should_write_and_accepted"]
    value_false_positive = confusion["should_not_write_but_accepted"]
    value_false_negative = confusion["should_write_but_rejected"]
    negative_cases = value_false_positive + confusion["should_not_write_and_rejected"]

    error_totals = {
        "expected_logical_errors": sum(
            result["error_deduplication"]["expected_logical_errors"] for result in successful
        ),
        "actual_error_records": sum(
            result["error_deduplication"]["actual_error_records"] for result in successful
        ),
        "actual_unique_errors": sum(
            result["error_deduplication"]["actual_unique_errors"] for result in successful
        ),
        "duplicate_error_records": sum(
            result["error_deduplication"]["duplicate_error_records"] for result in successful
        ),
        "call_id_association_errors": sum(
            result["error_deduplication"]["call_id_association_errors"]
            for result in successful
        ),
    }
    error_denominator = max(
        error_totals["expected_logical_errors"], error_totals["actual_error_records"]
    )
    error_totals["merge_accuracy"] = _safe_ratio(
        evidence_metrics["errors"]["true_positives"], error_denominator
    ) if error_denominator else 1.0
    evidence_reference_errors = sum(
        int(result.get("evidence_reference_errors", 0)) for result in successful
    )
    outcome_accuracy = {
        "correct": sum(1 for result in successful if result["outcome_correct"]),
        "incorrect": sum(1 for result in successful if not result["outcome_correct"]),
        "accuracy": _safe_ratio(
            sum(1 for result in successful if result["outcome_correct"]),
            len(successful),
        ),
        "mismatch_cases": [
            result["case_id"] for result in successful if not result["outcome_correct"]
        ],
    }
    capability_gaps = sorted({
        gap for result in successful for gap in result.get("capability_gaps", [])
    })

    claim_keys = [
        "generated_claims",
        "valid_claims",
        "rejected_claims",
        "persistable_claims",
        "supported_accepted_claims",
        "unsupported_accepted_claims",
        "matched_expected_claims",
        "supported_claims",
        "unsupported_claims",
        "missing_required_claims",
        "forbidden_claims",
        "forbidden_accepted_claims",
        "claims_without_evidence_reference",
        "invalid_evidence_references",
        "epistemic_status_mismatches",
        "missing_applies_when",
        "missing_limitations",
        "duplicate_semantic_keys",
        "semantic_key_mismatches",
        "confirmed_recovery_without_verification",
        "confirmed_root_cause_without_full_chain",
        "generic_success_tool_count_claims",
    ]
    claim_totals = {
        key: sum(result["claims"][key] for result in successful) for key in claim_keys
    }
    reason_code_distribution = dict(
        sorted(
            Counter(
                code
                for result in successful
                for code in result.get("value_reason_codes", [])
            ).items()
        )
    )
    durable_signal_distribution = dict(
        sorted(
            Counter(
                signal
                for result in successful
                for signal in result.get("durable_signals", [])
            ).items()
        )
    )

    confidence_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in successful:
        confidence_groups[_confidence_bin(float(result["confidence"]))].append(result)
    confidence_calibration = {}
    for label in ("[0.0,0.5)", "[0.5,0.7)", "[0.7,0.9)", "[0.9,1.0]"):
        group = confidence_groups.get(label, [])
        total_claims = sum(
            result["claims"]["supported_claims"] + result["claims"]["unsupported_claims"]
            for result in group
        )
        confidence_calibration[label] = {
            "case_count": len(group),
            "conclusion_correct_ratio": _safe_ratio(
                sum(1 for result in group if result["conclusion_correct"]), len(group)
            ),
            "unsupported_claim_ratio": _safe_ratio(
                sum(result["claims"]["unsupported_claims"] for result in group),
                total_claims,
            ),
            "low_value_write_ratio": _safe_ratio(
                sum(1 for result in group if result["low_value_false_write"]), len(group)
            ),
        }

    event_groups: dict[int, list[float]] = defaultdict(list)
    for result in successful:
        event_groups[int(result["event_count"])].append(float(result["confidence"]))
    event_count_relation = [
        {
            "event_count": event_count,
            "case_count": len(confidences),
            "average_confidence": sum(confidences) / len(confidences),
        }
        for event_count, confidences in sorted(event_groups.items())
    ]

    high_confidence_errors = [
        result["case_id"]
        for result in successful
        if result["confidence"] >= 0.9
        and (
            result["fact_error_count"] > 0
            or result["claims"]["unsupported_claims"] > 0
            or result["claims"]["forbidden_claims"] > 0
        )
    ]
    confidence_one_unsupported = [
        result["case_id"]
        for result in successful
        if result["confidence"] == 1.0 and result["claims"]["unsupported_claims"] > 0
    ]

    results_by_id = {result["case_id"]: result for result in successful}
    path_defect = results_by_id.get("path-command-is-not-file-002", {})
    library_defect = results_by_id.get("library-changing-gin-negative-005", {})
    error_defect = results_by_id.get("error-same-call-two-sources-001", {})
    known_defects = {
        "command_interpreted_as_path": {
            "case_id": "path-command-is-not-file-002",
            "reproduced": "pytest tests/test_auth.py -q"
            in path_defect.get("actual_evidence", {}).get("files_read", []),
        },
        "changing_interpreted_as_gin": {
            "case_id": "library-changing-gin-negative-005",
            "reproduced": "gin"
            in library_defect.get("actual_evidence", {}).get("libraries", []),
        },
        "tool_result_error_duplicate": {
            "case_id": "error-same-call-two-sources-001",
            "reproduced": error_defect.get("error_deduplication", {}).get(
                "duplicate_error_records", 0
            )
            > 0,
        },
    }

    original_results = [
        result
        for result in results
        if not result["case_id"].startswith(("trace-v2-", "claim-value-"))
    ]
    task_evidence_results = [
        result for result in results if not result["case_id"].startswith("claim-value-")
    ]
    claim_value_results = [
        result for result in results if result["case_id"].startswith("claim-value-")
    ]
    dataset_slices = {
        "original_shared_40": _summarize_result_slice(original_results),
        "task_evidence_48": _summarize_result_slice(task_evidence_results),
        "claim_value_30": _summarize_result_slice(claim_value_results),
        "full": _summarize_result_slice(results),
    }

    return {
        "report_schema_version": 3,
        "report_label": "reflection_value_gate",
        "dataset_schema_version": 1,
        "case_count": len(cases),
        "category_counts": dict(sorted(Counter(case["category"] for case in cases).items())),
        "dataset_slices": dataset_slices,
        "engine": "minicode.agent_reflection.ReflectionEngine",
        "trace_schema": {
            "event_types": [
                "tool_call",
                "tool_result",
                "error",
                "recovery",
                "recovery_suggestion",
                "assistant_step",
                "task_result",
            ],
            "common_fields": [
                "event_id",
                "call_id",
                "type",
                "tool_name",
                "status",
                "input",
                "files",
                "files_read",
                "files_changed",
                "referenced_files",
                "output_summary",
            ],
            "current_output_fields": [
                "files",
                "libraries",
                "tools",
                "errors",
                "recoveries",
                "project_state",
                "key_decisions",
                "lessons_learned",
                "confidence",
                "task_evidence",
                "reflection_candidate",
                "claim_validation",
                "value_decision",
                "structured_claims",
            ],
        },
        "metric_definitions": {
            "evidence": "Micro-averaged exact or required-term TP/FP/FN by field.",
            "value_selection": "Predicted write is ReflectionValueDecision.accepted; confidence is observational only.",
            "persistable_claim": "A validator-valid claim selected by a durable signal when the value decision is accepted.",
            "unsupported_accepted_claim": "A persistable claim absent from validator-valid claims; expected to remain zero.",
            "conclusion_correct": "No evidence FP/FN, missing/unsupported/forbidden claim, or outcome mismatch.",
            "library_positive": "Only golden confirmed dependencies are positive; weak mentions and non-dependencies are negatives.",
        },
        "evidence_extraction": evidence_metrics,
        "error_deduplication": error_totals,
        "evidence_reference_errors": evidence_reference_errors,
        "outcome_accuracy": outcome_accuracy,
        "value_selection": {
            "confusion_matrix": confusion,
            "metrics": _count_metrics(
                value_true_positive, value_false_positive, value_false_negative
            ),
            "low_value_false_write_rate": _safe_ratio(value_false_positive, negative_cases),
            "reason_code_distribution": reason_code_distribution,
            "durable_signal_distribution": durable_signal_distribution,
        },
        "claims": claim_totals,
        "confidence_calibration": confidence_calibration,
        "high_confidence_error_cases": high_confidence_errors,
        "confidence_one_unsupported_cases": confidence_one_unsupported,
        "event_count_confidence": {
            "pearson_correlation": _pearson_event_confidence(successful),
            "groups": event_count_relation,
        },
        "known_defects": known_defects,
        "capability_gaps": capability_gaps,
        "metric_groups": {
            "evidence_layer": [
                "evidence_extraction",
                "error_deduplication",
                "evidence_reference_errors",
                "outcome_accuracy",
            ],
            "synthesis_claim_layer": ["claims"],
            "value_confidence_layer": [
                "value_selection",
                "confidence_calibration",
                "event_count_confidence",
            ],
        },
        "engine_errors": [
            {"case_id": result["case_id"], "error": result["engine_error"]}
            for result in results
            if result.get("engine_error") is not None
        ],
        "cases": results,
    }


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9]*[_-])*(?:api[_-]?key|authorization|credential|password|token|secret(?:[_-]?key)?))\b"
    r"(\s*[:=]\s*)(?!\[redacted)[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+(?!\[redacted)[a-z0-9._~+/-]+")
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")


def _redact_text(value: str) -> str:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    return _OPENAI_STYLE_KEY_RE.sub("[REDACTED_API_KEY]", value)


def _redact_report(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            str(key): _redact_report(nested, depth + 1)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_report(nested, depth + 1) for nested in value]
    return value


def write_json_report(report: dict[str, Any], output_path: str | Path) -> None:
    """Write a redacted, deterministic machine-readable report."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sanitized = _redact_report(report)
    path.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render the deterministic baseline as a human-readable Markdown report."""
    lines = [
        "# Reflection Accuracy - ReflectionValueGate",
        "",
        "This report measures deterministic evidence extraction, claim validation, and durable-value selection against synthetic, manually labelled execution traces. Confidence remains observational and is not a persistence decision.",
        "",
        "## Dataset",
        "",
        f"- Dataset schema version: `{report['dataset_schema_version']}`",
        f"- Cases: `{report['case_count']}`",
        f"- Engine: `{report['engine']}`",
        "- Source policy: synthetic traces only; no real sessions, memory files, credentials, models, or network services.",
        "",
        "| Category | Cases |",
        "| --- | ---: |",
    ]
    for category, count in report["category_counts"].items():
        lines.append(f"| `{category}` | {count} |")

    lines.extend([
        "",
        "## Dataset Slices",
        "",
        "| Slice | Cases | Value P | Value R | Value F1 | Low-value false write | Unsupported accepted |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for label in ("original_shared_40", "task_evidence_48", "claim_value_30", "full"):
        summary = report["dataset_slices"][label]
        value_metrics = summary["value_selection"]["metrics"]
        lines.append(
            f"| `{label}` | {summary['case_count']} | "
            f"{_percent(value_metrics['precision'])} | {_percent(value_metrics['recall'])} | "
            f"{_percent(value_metrics['f1'])} | "
            f"{_percent(summary['value_selection']['low_value_false_write_rate'])} | "
            f"{summary['claims']['unsupported_accepted_claims']} |"
        )

    lines.extend([
        "",
        "## Current Trace Schema",
        "",
        "Production Trace Contract v2 includes deterministic `event_id`, `call_id`, role-specific files, `recovery_suggestion`, real `recovery`, and terminal outcome fields. Legacy traces receive extraction-local fallback IDs without mutation.",
        "",
        "`TaskEvidence` exposes file roles, verification records, error call IDs, evidence references, dependency strength, and epistemic status. Structured claims are synthesized only from this evidence and validated before value selection.",
        "",
        "## Evidence Extraction",
        "",
        "| Field | Precision | Recall | F1 | FP | FN |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for field, metrics in report["evidence_extraction"].items():
        lines.append(
            f"| `{field}` | {_percent(metrics['precision'])} | {_percent(metrics['recall'])} | "
            f"{_percent(metrics['f1'])} | {metrics['false_positives']} | {metrics['false_negatives']} |"
        )

    errors = report["error_deduplication"]
    value = report["value_selection"]
    confusion = value["confusion_matrix"]
    claims = report["claims"]
    lines.extend([
        "",
        "## Error Deduplication",
        "",
        f"- Expected logical errors: `{errors['expected_logical_errors']}`",
        f"- Actual error records: `{errors['actual_error_records']}`",
        f"- Duplicate error records: `{errors['duplicate_error_records']}`",
        f"- Merge accuracy: `{_percent(errors['merge_accuracy'])}`",
        f"- Missing/incorrect call-ID associations: `{errors['call_id_association_errors']}`",
        f"- Evidence records without source IDs: `{report['evidence_reference_errors']}`",
        f"- Outcome accuracy: `{_percent(report['outcome_accuracy']['accuracy'])}`",
        f"- Outcome mismatch cases: `{', '.join(report['outcome_accuracy']['mismatch_cases']) or 'none'}`",
        "",
        "## Value Selection",
        "",
        f"- Should write and accepted: `{confusion['should_write_and_accepted']}`",
        f"- Should write but rejected: `{confusion['should_write_but_rejected']}`",
        f"- Should not write but accepted: `{confusion['should_not_write_but_accepted']}`",
        f"- Should not write and rejected: `{confusion['should_not_write_and_rejected']}`",
        f"- Value precision: `{_percent(value['metrics']['precision'])}`",
        f"- Value recall: `{_percent(value['metrics']['recall'])}`",
        f"- Value F1: `{_percent(value['metrics']['f1'])}`",
        f"- Low-value false-write rate: `{_percent(value['low_value_false_write_rate'])}`",
        f"- Reason codes: `{json.dumps(value['reason_code_distribution'], ensure_ascii=False, sort_keys=True)}`",
        f"- Durable signals: `{json.dumps(value['durable_signal_distribution'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Claims",
        "",
        f"- Generated claims: `{claims['generated_claims']}`",
        f"- Validator-valid claims: `{claims['valid_claims']}`",
        f"- Validator-rejected claims: `{claims['rejected_claims']}`",
        f"- Persistable claims: `{claims['persistable_claims']}`",
        f"- Supported accepted claims: `{claims['supported_accepted_claims']}`",
        f"- Unsupported accepted claims: `{claims['unsupported_accepted_claims']}`",
        f"- Missing required claims: `{claims['missing_required_claims']}`",
        f"- Forbidden accepted claims: `{claims['forbidden_accepted_claims']}`",
        f"- Claims without evidence references: `{claims['claims_without_evidence_reference']}`",
        f"- Invalid evidence references: `{claims['invalid_evidence_references']}`",
        f"- Epistemic status mismatches: `{claims['epistemic_status_mismatches']}`",
        f"- Missing applies_when: `{claims['missing_applies_when']}`",
        f"- Missing limitations: `{claims['missing_limitations']}`",
        f"- Duplicate semantic keys: `{claims['duplicate_semantic_keys']}`",
        f"- Confirmed recovery without verification: `{claims['confirmed_recovery_without_verification']}`",
        f"- Confirmed root cause without full chain: `{claims['confirmed_root_cause_without_full_chain']}`",
        f"- Generic success/tool-count claims: `{claims['generic_success_tool_count_claims']}`",
        "",
        "## Confidence Calibration",
        "",
        "| Confidence | Cases | Correct conclusions | Unsupported claim ratio | Low-value write ratio |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for label, metrics in report["confidence_calibration"].items():
        lines.append(
            f"| `{label}` | {metrics['case_count']} | {_percent(metrics['conclusion_correct_ratio'])} | "
            f"{_percent(metrics['unsupported_claim_ratio'])} | {_percent(metrics['low_value_write_ratio'])} |"
        )
    lines.extend([
        "",
        f"Event-count/confidence Pearson correlation: `{report['event_count_confidence']['pearson_correlation']:.3f}`.",
        "",
        "High-confidence cases with factual or claim errors:",
        "",
    ])
    if report["high_confidence_error_cases"]:
        lines.extend(f"- `{case_id}`" for case_id in report["high_confidence_error_cases"])
    else:
        lines.append("- None")
    lines.extend(["", "Confidence `1.0` cases with unsupported claims:", ""])
    if report["confidence_one_unsupported_cases"]:
        lines.extend(f"- `{case_id}`" for case_id in report["confidence_one_unsupported_cases"])
    else:
        lines.append("- None")

    defect_labels = {
        "command_interpreted_as_path": "command interpreted as a path",
        "changing_interpreted_as_gin": "changing interpreted as gin",
        "tool_result_error_duplicate": "tool_result/error produce duplicate error records",
    }
    lines.extend(["", "## Known Defects", ""])
    for key, label in defect_labels.items():
        defect = report["known_defects"][key]
        status = "REPRODUCED" if defect["reproduced"] else "NOT REPRODUCED"
        lines.append(f"- **{status}**: {label} (`{defect['case_id']}`).")

    lines.extend(["", "## Capability Gaps", ""])
    if report["capability_gaps"]:
        lines.extend(f"- `{gap}`" for gap in report["capability_gaps"])
    else:
        lines.append("- None")
    lines.extend([
        "",
        "The deterministic claim/value stage reports no capability gap when structured results are available.",
        "",
        "## Known Outcome Semantics",
        "",
        "The legacy labels for `edge-empty-trace-003` and `edge-assistant-only-missing-fields-004` differ from Trace Contract v2. V2 returns `unknown` without terminal or verification evidence; these two differences do not affect value-gate acceptance.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "python scripts/evaluate_reflection.py \\",
        "  --dataset tests/fixtures/reflection_golden \\",
        "  --output artifacts/reflection-accuracy-value-gate.json \\",
        "  --markdown docs/reflection-accuracy-value-gate.md \\",
        "  --baseline artifacts/reflection-accuracy-task-evidence.json \\",
        "  --comparison docs/reflection-value-gate-comparison.md",
        "```",
        "",
    ])
    return "\n".join(lines)


def write_markdown_report(report: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(report), encoding="utf-8")


def render_comparison_report(
    baseline: dict[str, Any],
    current_shared: dict[str, Any],
    current_full: dict[str, Any],
) -> str:
    """Render immutable TaskEvidence versus claim/value-gated results."""
    fields = [
        "files_read",
        "files_changed",
        "tools",
        "libraries",
        "errors",
        "recoveries",
        "decisions",
        "verification",
    ]
    baseline_original = _summarize_result_slice(
        [
            case
            for case in baseline.get("cases", [])
            if not str(case.get("case_id", "")).startswith("trace-v2-")
        ]
    )
    current_slices = current_full.get("dataset_slices", {})
    lines = [
        "# Reflection Value Gate Comparison",
        "",
        "The before/after evidence table uses only case IDs shared with the immutable baseline. Value tables additionally separate the original 40 cases, the existing 48-case TaskEvidence set, the 30 claim/value cases, and the complete dataset.",
        "",
        "## Shared Cases",
        "",
        f"- Baseline cases: `{baseline['case_count']}`",
        f"- Current shared cases: `{current_shared['case_count']}`",
        f"- Current full cases: `{current_full['case_count']}`",
        "",
        "| Evidence | Before P | Before R | Before F1 | After P | After R | After F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for field in fields:
        before = baseline["evidence_extraction"][field]
        after = current_shared["evidence_extraction"][field]
        lines.append(
            f"| `{field}` | {_percent(before['precision'])} | {_percent(before['recall'])} | "
            f"{_percent(before['f1'])} | {_percent(after['precision'])} | "
            f"{_percent(after['recall'])} | {_percent(after['f1'])} |"
        )

    before_errors = baseline["error_deduplication"]
    after_errors = current_shared["error_deduplication"]
    before_outcome_correct = sum(
        1 for case in baseline.get("cases", []) if case.get("outcome_correct")
    )
    before_outcome_total = len(baseline.get("cases", []))
    lines.extend([
        "",
        "## Error Identity",
        "",
        f"- Duplicate records: `{before_errors['duplicate_error_records']}` -> `{after_errors['duplicate_error_records']}`",
        f"- Call-ID association errors: `{before_errors['call_id_association_errors']}` -> `{after_errors['call_id_association_errors']}`",
        f"- Merge accuracy: `{_percent(before_errors['merge_accuracy'])}` -> `{_percent(after_errors['merge_accuracy'])}`",
        f"- Evidence-reference errors after: `{current_shared['evidence_reference_errors']}`",
        f"- Outcome accuracy: `{_percent(_safe_ratio(before_outcome_correct, before_outcome_total))}` -> `{_percent(current_shared['outcome_accuracy']['accuracy'])}`",
        f"- Current outcome mismatch cases: `{', '.join(current_shared['outcome_accuracy']['mismatch_cases']) or 'none'}`",
        "",
        "## Full Dataset Evidence",
        "",
        "| Evidence | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: |",
    ])
    for field in fields:
        metrics = current_full["evidence_extraction"][field]
        lines.append(
            f"| `{field}` | {_percent(metrics['precision'])} | "
            f"{_percent(metrics['recall'])} | {_percent(metrics['f1'])} |"
        )

    before_claims = baseline["claims"]
    after_claims = current_shared["claims"]
    before_value = baseline["value_selection"]
    after_value = current_shared["value_selection"]
    value_rows = [
        ("Before original shared", baseline_original),
        ("After original shared", current_slices.get("original_shared_40", {})),
        (
            "Before TaskEvidence shared",
            {
                "case_count": baseline.get("case_count", 0),
                "value_selection": before_value,
            },
        ),
        (
            "After TaskEvidence shared",
            {
                "case_count": current_shared.get("case_count", 0),
                "value_selection": after_value,
            },
        ),
        ("New claim/value cases", current_slices.get("claim_value_30", {})),
        ("Complete dataset", current_slices.get("full", {})),
    ]
    lines.extend([
        "",
        "## Value Selection",
        "",
        "| Slice | Cases | Precision | Recall | F1 | Low-value false write |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for label, summary in value_rows:
        if not summary or "value_selection" not in summary:
            continue
        value = summary["value_selection"]
        metrics = value["metrics"]
        lines.append(
            f"| {label} | {summary.get('case_count', 0)} | "
            f"{_percent(metrics['precision'])} | {_percent(metrics['recall'])} | "
            f"{_percent(metrics['f1'])} | {_percent(value['low_value_false_write_rate'])} |"
        )
    lines.extend([
        "",
        "## Claim Safety",
        "",
        f"- Unsupported claims before -> unsupported accepted after: `{before_claims['unsupported_claims']}` -> `{after_claims['unsupported_accepted_claims']}`",
        f"- Claims without evidence references: `{before_claims['claims_without_evidence_reference']}` -> `{after_claims['claims_without_evidence_reference']}`",
        f"- Forbidden accepted claims after: `{after_claims['forbidden_accepted_claims']}`",
        f"- Invalid evidence references after: `{after_claims['invalid_evidence_references']}`",
        f"- Epistemic status mismatches after: `{after_claims['epistemic_status_mismatches']}`",
        f"- Confirmed recovery without verification after: `{after_claims['confirmed_recovery_without_verification']}`",
        f"- Confirmed root cause without full chain after: `{after_claims['confirmed_root_cause_without_full_chain']}`",
        f"- Low-value false-write rate: `{_percent(before_value['low_value_false_write_rate'])}` -> `{_percent(after_value['low_value_false_write_rate'])}`",
        "",
        "The two known outcome differences remain `edge-empty-trace-003` and `edge-assistant-only-missing-fields-004`: Trace Contract v2 returns `unknown` without terminal or verification evidence. They are excluded from value-gate success criteria.",
        "",
        "## Known Defects",
        "",
    ])
    for name, defect in current_shared["known_defects"].items():
        status = "REPRODUCED" if defect["reproduced"] else "FIXED"
        lines.append(f"- `{name}`: **{status}** (`{defect['case_id']}`)")
    lines.append("")
    return "\n".join(lines)


def write_comparison_report(
    baseline: dict[str, Any],
    current_shared: dict[str, Any],
    current_full: dict[str, Any],
    output_path: str | Path,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_comparison_report(baseline, current_shared, current_full),
        encoding="utf-8",
    )
