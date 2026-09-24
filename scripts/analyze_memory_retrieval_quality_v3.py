#!/usr/bin/env python3
"""Create a content-free analysis bundle for Memory retrieval evaluations.

The analyzer consumes evaluator *reports*, never their source holdouts.  It
normalizes only case identifiers, grouping identifiers, polarity, and binary
outcomes; query and memory text are neither projected nor written.
"""

from __future__ import annotations

import argparse
import html
import importlib
import json
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "artifacts/memory-retrieval-semantic-gap-baseline-v2.json"
DEFAULT_OUTPUT_DIR = ROOT / ".repair-work/memory-retrieval-quality-2026-09-03"
BOOTSTRAP_SEED = 20260903
BOOTSTRAP_SAMPLES = 10_000
ALPHA = 0.05
_CASE_TEXT_KEYS = frozenset(
    {"query", "memory_text", "content", "prompt", "task_description", "metadata"}
)
MAX_REPORT_BYTES = 20_000_000


def _contains_forbidden_text_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_CASE_TEXT_KEYS & set(value)) or any(
            _contains_forbidden_text_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_text_key(item) for item in value)
    return False


@dataclass(frozen=True)
class CaseOutcome:
    """Content-free case projection used by all statistical routines."""

    case_id: str
    polarity: str
    group_id: str
    candidate_hit: bool | None
    target_rendered: bool
    decision_correct: bool
    dense_top20_hit: bool | None = None
    admitted_hit: bool | None = None


@dataclass(frozen=True)
class ReportProjection:
    label: str
    scope: str
    source: str
    schema_version: str
    synthetic: bool
    one_shot: bool
    embedding_identity: str
    verifier_identity: str
    challenger_identity: str
    acceptance_passed: bool | None
    usage: Mapping[str, int]
    cases: tuple[CaseOutcome, ...]
    reported_rendered_precision: float | None
    suite_id: str = "unknown-suite"
    formal_authority: bool = False
    stage_counts: Mapping[str, int] | None = None
    dense_top_k: int | None = None
    canonical_candidate_limit: int | None = None


@dataclass(frozen=True)
class PressureProjection:
    """Validated, content-free resolver-pressure authority projection."""

    label: str
    scope: str
    source: str
    schema_version: str
    synthetic: bool
    one_shot: bool
    acceptance_passed: bool
    report_fingerprint: str
    case_count: int
    raw_relevant_count: int
    approved_count: int
    protocol_failure_cases: int
    usage: Mapping[str, int]


@dataclass(frozen=True)
class AttemptRecord:
    """Content-free provenance for a spent or authoritative attempt."""

    attempt_id: str
    suite: str
    generation: str
    status: str
    stage: str
    reason_code: str
    authority: bool
    note: str


FIXED_EXPERIMENT_PROVENANCE = (
    AttemptRecord(
        attempt_id="resolver-pressure-v2-original-one-shot",
        suite="resolver-pressure",
        generation="v2",
        status="failed",
        stage="model-output-validation",
        reason_code="redundant_contested_reason_echo_mismatch",
        authority=False,
        note="Original spent one-shot; not a rerun and not final authority.",
    ),
    AttemptRecord(
        attempt_id="resolver-pressure-spent-v1-development",
        suite="resolver-pressure-development",
        generation="spent-v1",
        status="passed-development-60-of-60",
        stage="post-fix-development",
        reason_code="development_confirmation_only",
        authority=False,
        note="All 60 development cases passed; spent data cannot authorize promotion.",
    ),
    AttemptRecord(
        attempt_id="resolver-pressure-v3-confirmation",
        suite="resolver-pressure",
        generation="v3",
        status="passed-confirmation-60-of-60",
        stage="one-shot-confirmation",
        reason_code="later_invalidated_by_freeze_change",
        authority=False,
        note="Passed before the claim-order source fix; it is not current authority.",
    ),
    AttemptRecord(
        attempt_id="resolver-pressure-v3-freeze-invalidation",
        suite="resolver-pressure",
        generation="v3",
        status="invalidated",
        stage="implementation-freeze",
        reason_code="claim_order_source_fix_changed_freeze",
        authority=False,
        note="The later source repair changed the freeze, so v3 is not represented as a rerun.",
    ),
    AttemptRecord(
        attempt_id="independent-v1-original-one-shot",
        suite="independent-promotion",
        generation="v1",
        status="failed-before-api",
        stage="top-level-packaging-validation",
        reason_code="top_level_packaging_schema_mismatch",
        authority=False,
        note="Original spent one-shot failed before any API call; it is not a v2 rerun.",
    ),
)


@dataclass(frozen=True)
class FigureBackend:
    """A plotting backend selected without making either library mandatory."""

    name: str
    api: Mapping[str, Any]


def _load_report(path: Path) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"report path is missing or unsafe: {candidate}")
    resolved = candidate.resolve(strict=True)
    if resolved.name.casefold() == "holdout.json":
        raise ValueError("the analyzer accepts evaluator reports, never holdout.json")
    raw = resolved.read_bytes()
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError(f"report exceeds {MAX_REPORT_BYTES} bytes: {resolved}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"report contains duplicate JSON key: {key!r}")
            result[key] = item
        return result

    value = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    if not isinstance(value, dict):
        raise TypeError(f"report root must be an object: {resolved}")
    if _contains_forbidden_text_key(value):
        raise ValueError(f"report contains forbidden sealed-text fields: {resolved}")
    return value


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _polarity(value: Any, expected_relevant: Any = None) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    if normalized in {"positive", "relevant"} or expected_relevant is True:
        return "positive"
    if (
        normalized in {"hard_negative", "negative", "irrelevant"}
        or expected_relevant is False
    ):
        return "hard_negative"
    raise ValueError(f"unsupported case polarity: {value!r}")


def _validate_case_rows(rows: Sequence[Any], *, source: str) -> None:
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError(f"{source} case row must be an object")
        leaked = _CASE_TEXT_KEYS & set(row)
        if leaked:
            raise ValueError(
                f"{source} evaluator report contains forbidden case text fields: "
                f"{sorted(leaked)}"
            )
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{source} case row is missing case_id")
        if case_id in seen:
            raise ValueError(f"{source} contains duplicate case_id {case_id!r}")
        seen.add(case_id)


def _identity(mapping: Any, *, provider_fallback: Any = None) -> str:
    if not isinstance(mapping, dict):
        return str(provider_fallback or "not reported")
    provider = str(mapping.get("provider") or provider_fallback or "").strip()
    model = str(mapping.get("model_id") or mapping.get("model") or "").strip()
    dimension = mapping.get("dimension")
    parts = [part for part in (provider, model) if part]
    if isinstance(dimension, int) and not isinstance(dimension, bool):
        parts.append(f"{dimension}d")
    return " / ".join(parts) or "not reported"


def _suite_id(report: Mapping[str, Any]) -> str:
    """Return a content-free suite identity used to prohibit cross-suite tests."""

    holdout = report.get("holdout")
    dataset = report.get("dataset")
    candidates = (
        holdout.get("version") if isinstance(holdout, dict) else None,
        holdout.get("dataset_id") if isinstance(holdout, dict) else None,
        report.get("holdout_version"),
        dataset.get("dataset_id") if isinstance(dataset, dict) else None,
        report.get("suite_id"),
        report.get("schema_version"),
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown-suite"


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_usage(
    report: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    direct = report.get("usage")
    projected: dict[str, int] = {}

    def project_numeric_leaves(value: Any, prefix: str = "") -> None:
        if not isinstance(value, dict):
            return
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold().replace("-", "_")
            if not key or not all(
                character.isalnum() or character == "_" for character in key
            ):
                continue
            qualified = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict) and qualified.count(".") < 2:
                project_numeric_leaves(child, qualified)
            elif (
                isinstance(child, int)
                and not isinstance(child, bool)
                and child >= 0
                and any(
                    token in key
                    for token in ("call", "token", "failure", "failed", "unavailable")
                )
            ):
                projected[qualified] = child

    if isinstance(direct, dict):
        project_numeric_leaves(direct)
    if projected:
        return projected

    for row in cases:
        diagnostics = row.get("hybrid_diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = row.get("hybrid")
        provider = (
            diagnostics.get("provider") if isinstance(diagnostics, dict) else None
        )
        if not isinstance(provider, dict):
            provider = diagnostics if isinstance(diagnostics, dict) else {}
        for key in ("model_call_count", "input_tokens", "output_tokens"):
            value = provider.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                projected[key] = projected.get(key, 0) + value
    remote = report.get("remote_call_count")
    if isinstance(remote, int) and not isinstance(remote, bool) and remote >= 0:
        projected["remote_call_count"] = remote
    return projected


def _baseline_projection(
    report: Mapping[str, Any], *, label: str, source: str
) -> ReportProjection:
    raw_rows = report.get("per_case_results")
    if not isinstance(raw_rows, list):
        raise TypeError("semantic-v2 report has no per_case_results")
    _validate_case_rows(raw_rows, source=source)
    outcomes: list[CaseOutcome] = []
    for row in raw_rows:
        production = row.get("canonical_production")
        if not isinstance(production, dict):
            raise TypeError(
                f"baseline case {row['case_id']} lacks canonical_production"
            )
        polarity = _polarity(row.get("polarity"))
        targets = (
            _string_list(row.get("primary_entry_ids"))
            if polarity == "positive"
            else _string_list(row.get("must_exclude_ids"))
        )
        if not targets:
            raise ValueError(f"baseline case {row['case_id']} has no target IDs")
        candidates = set(_string_list(production.get("candidate_ids_top20")))
        rendered = set(_string_list(production.get("rendered_ids")))
        admitted = set(_string_list(production.get("post_gate_ids")))
        target_rendered = bool(rendered.intersection(targets))
        target_admitted = bool(admitted.intersection(targets))
        outcomes.append(
            CaseOutcome(
                case_id=str(row["case_id"]),
                polarity=polarity,
                group_id=str(row.get("category") or row["case_id"]),
                candidate_hit=(
                    bool(candidates.intersection(targets))
                    if polarity == "positive"
                    else None
                ),
                target_rendered=target_rendered,
                decision_correct=(
                    target_rendered if polarity == "positive" else not target_rendered
                ),
                dense_top20_hit=None,
                admitted_hit=target_admitted,
            )
        )
    overall = report.get("overall_metrics")
    metrics = overall.get("metrics") if isinstance(overall, dict) else None
    precision = metrics.get("rendered_precision") if isinstance(metrics, dict) else None
    overall_counts = overall.get("counts") if isinstance(overall, dict) else None
    relevant_rendered = (
        overall_counts.get("relevant_rendered_entries")
        if isinstance(overall_counts, dict)
        else None
    )
    total_rendered = (
        overall_counts.get("total_rendered_entries")
        if isinstance(overall_counts, dict)
        else None
    )
    total_admitted = sum(
        len(_string_list(row["canonical_production"].get("post_gate_ids")))
        for row in raw_rows
    )
    admitted_positive_hits = sum(
        case.admitted_hit is True for case in outcomes if case.polarity == "positive"
    )
    stage_counts: dict[str, int] = {
        "false_positive_admissions": max(0, total_admitted - admitted_positive_hits)
    }
    if (
        isinstance(relevant_rendered, int)
        and not isinstance(relevant_rendered, bool)
        and isinstance(total_rendered, int)
        and not isinstance(total_rendered, bool)
        and 0 <= relevant_rendered <= total_rendered
    ):
        stage_counts["false_positive_renders"] = total_rendered - relevant_rendered
    return ReportProjection(
        label=label,
        scope="synthetic development (spent)",
        source=source,
        schema_version=str(report.get("schema_version") or "unknown"),
        synthetic=report.get("synthetic_data") is True,
        one_shot=False,
        embedding_identity="none (lexical canonical baseline)",
        verifier_identity="deterministic lexical gate",
        challenger_identity="none",
        acceptance_passed=(
            report.get("evaluation_passed")
            if isinstance(report.get("evaluation_passed"), bool)
            else None
        ),
        usage=_safe_usage(report, raw_rows),
        cases=tuple(outcomes),
        reported_rendered_precision=(
            float(precision) if isinstance(precision, (int, float)) else None
        ),
        suite_id=_suite_id(report),
        formal_authority=False,
        stage_counts=stage_counts,
        dense_top_k=None,
        canonical_candidate_limit=20,
    )


def _hybrid_projection(
    report: Mapping[str, Any],
    *,
    label: str,
    source: str,
    scope: str,
    formal_authority: bool = False,
) -> ReportProjection:
    canonical = report.get("canonical")
    if not isinstance(canonical, dict) or not isinstance(canonical.get("cases"), list):
        raise TypeError("hybrid report has no canonical.cases")
    raw_rows: list[dict[str, Any]] = canonical["cases"]
    _validate_case_rows(raw_rows, source=source)
    dense_container = report.get("global_dense")
    if not isinstance(dense_container, dict):
        dense_container = report.get("dense")
    dense_rows = (
        dense_container.get("cases") if isinstance(dense_container, dict) else []
    )
    if not isinstance(dense_rows, list):
        dense_rows = []
    _validate_case_rows(dense_rows, source=f"{source}:dense")
    dense_by_id = {str(row["case_id"]): row for row in dense_rows}

    outcomes: list[CaseOutcome] = []
    for row in raw_rows:
        case_id = str(row["case_id"])
        polarity = _polarity(row.get("polarity"), row.get("expected_relevant"))
        target_id = row.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError(f"hybrid case {case_id} has no target_id")
        rendered_ids = set(_string_list(row.get("rendered_ids")))
        explicit_rendered = row.get("target_rendered")
        target_rendered = (
            explicit_rendered
            if isinstance(explicit_rendered, bool)
            else target_id in rendered_ids
        )
        dense = dense_by_id.get(case_id, {})
        dense_top20_hit = _optional_bool(dense.get("target_in_top_20"))
        explicit_candidate = row.get("target_candidate")
        candidate_hit = _optional_bool(explicit_candidate)
        if candidate_hit is None and isinstance(explicit_candidate, dict):
            candidate_hit = True
        explicit_admitted = _optional_bool(row.get("target_admitted"))
        if explicit_admitted is None:
            if isinstance(row.get("admitted_ids"), list):
                explicit_admitted = target_id in _string_list(row.get("admitted_ids"))
            elif isinstance(row.get("selected_ids"), list):
                explicit_admitted = target_id in _string_list(row.get("selected_ids"))
        group_value = (
            row.get("group_id")
            or row.get("family_id")
            or row.get("category")
            or row.get("relation_sha256")
            or dense.get("category_sha256")
            or dense.get("relation_sha256")
            or case_id
        )
        outcomes.append(
            CaseOutcome(
                case_id=case_id,
                polarity=polarity,
                group_id=str(group_value),
                candidate_hit=candidate_hit,
                target_rendered=bool(target_rendered),
                decision_correct=(
                    bool(target_rendered)
                    if polarity == "positive"
                    else not bool(target_rendered)
                ),
                dense_top20_hit=dense_top20_hit,
                admitted_hit=explicit_admitted,
            )
        )

    metrics = canonical.get("metrics")
    precision = metrics.get("rendered_precision") if isinstance(metrics, dict) else None
    gate = report.get("acceptance_gate")
    gate_passed = gate.get("passed") if isinstance(gate, dict) else None
    raw_counts = canonical.get("counts")
    stage_counts = (
        {
            str(key): int(value)
            for key, value in raw_counts.items()
            if isinstance(key, str)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if isinstance(raw_counts, dict)
        else None
    )
    if stage_counts is None:
        has_admitted_lists = any(
            isinstance(row.get("admitted_ids"), list)
            or isinstance(row.get("selected_ids"), list)
            for row in raw_rows
        )
        derived_false_positive_admissions = 0
        derived_false_positive_renders = 0
        for row in raw_rows:
            positive = (
                _polarity(row.get("polarity"), row.get("expected_relevant"))
                == "positive"
            )
            target_id = str(row["target_id"])
            admitted_ids = _string_list(row.get("admitted_ids"))
            if not admitted_ids:
                admitted_ids = _string_list(row.get("selected_ids"))
            rendered_ids = _string_list(row.get("rendered_ids"))
            derived_false_positive_admissions += (
                sum(entry_id != target_id for entry_id in admitted_ids)
                if positive
                else len(admitted_ids)
            )
            derived_false_positive_renders += (
                sum(entry_id != target_id for entry_id in rendered_ids)
                if positive
                else len(rendered_ids)
            )
        stage_counts = {
            "false_positive_renders": derived_false_positive_renders,
        }
        if has_admitted_lists:
            stage_counts["false_positive_admissions"] = (
                derived_false_positive_admissions
            )
    return ReportProjection(
        label=label,
        scope=scope,
        source=source,
        schema_version=str(report.get("schema_version") or "unknown"),
        synthetic=report.get("synthetic_data") is True,
        one_shot=report.get("one_shot") is True,
        embedding_identity=_identity(
            report.get("model"), provider_fallback=report.get("embedding_provider")
        ),
        verifier_identity=_identity(report.get("verifier")),
        challenger_identity=_identity(report.get("challenger")),
        acceptance_passed=gate_passed if isinstance(gate_passed, bool) else None,
        usage=_safe_usage(report, raw_rows),
        cases=tuple(outcomes),
        reported_rendered_precision=(
            float(precision) if isinstance(precision, (int, float)) else None
        ),
        suite_id=_suite_id(report),
        formal_authority=formal_authority,
        stage_counts=stage_counts,
        dense_top_k=(
            int(report["dense_top_k"])
            if isinstance(report.get("dense_top_k"), int)
            and not isinstance(report.get("dense_top_k"), bool)
            else 20
            if dense_rows
            else None
        ),
        canonical_candidate_limit=(
            int(report["max_union_candidates"])
            if isinstance(report.get("max_union_candidates"), int)
            and not isinstance(report.get("max_union_candidates"), bool)
            else None
        ),
    )


def _validate_formal_independent_report(report: dict[str, Any]) -> dict[str, Any]:
    """Apply the production evaluator and release authorities, fail closed."""

    from minicode.memory_hybrid import evidence_fingerprint
    from minicode.memory_hybrid_release import (
        HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT,
    )
    from scripts.memory_hybrid_qwen_v2_evaluator import (
        build_qwen_v2_production_evidence,
        validate_promotion_report,
    )

    validated = validate_promotion_report(report)
    if validated.get("report_fingerprint") != evidence_fingerprint(validated):
        raise ValueError("independent report fingerprint is not authoritative")
    production_evidence = build_qwen_v2_production_evidence(validated)
    evidence_digest = evidence_fingerprint(production_evidence)
    if production_evidence.get("report_fingerprint") != evidence_digest:
        raise ValueError("independent production evidence fingerprint mismatch")
    if (
        not HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT
        or evidence_digest != HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT
    ):
        raise ValueError("independent production evidence is not release-allowlisted")
    return validated


def project_resolver_pressure_report(path: Path) -> PressureProjection:
    """Validate and project the formal pressure report without opening a holdout."""

    from minicode.memory_hybrid import (
        HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        evidence_fingerprint,
    )
    from scripts.memory_hybrid_qwen_v2_evaluator import (
        build_resolver_pressure_receipt,
        load_resolver_pressure_report,
        require_resolver_pressure_release_anchor,
        validate_resolver_pressure_report,
    )

    official_receipt = load_resolver_pressure_report(path)
    raw = _load_report(path)
    validated = validate_resolver_pressure_report(raw)
    if validated.get("report_fingerprint") != evidence_fingerprint(validated):
        raise ValueError("resolver-pressure report fingerprint is not authoritative")
    receipt = build_resolver_pressure_receipt(
        validated,
        artifact=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    )
    if require_resolver_pressure_release_anchor(receipt) != official_receipt:
        raise ValueError("resolver-pressure loader and validator receipts disagree")
    pressure = validated["resolver_pressure"]
    counts = pressure["counts"]
    return PressureProjection(
        label="resolver-pressure-one-shot",
        scope="synthetic independent one-shot safety pressure",
        source=str(path),
        schema_version=str(validated["schema_version"]),
        synthetic=True,
        one_shot=True,
        acceptance_passed=validated["acceptance_gate"]["passed"] is True,
        report_fingerprint=str(validated["report_fingerprint"]),
        case_count=int(counts["case_count"]),
        raw_relevant_count=int(counts["raw_relevant_count"]),
        approved_count=int(counts["approved_count"]),
        protocol_failure_cases=int(counts["protocol_failure_cases"]),
        usage={str(key): int(value) for key, value in pressure["usage"].items()},
    )


def project_report(
    path: Path, *, label: str, kind: str, scope: str | None = None
) -> ReportProjection:
    report = _load_report(path)
    if kind == "baseline":
        return _baseline_projection(report, label=label, source=str(path))
    if kind != "hybrid":
        raise ValueError(f"unknown report kind: {kind}")
    resolved_scope = scope or "synthetic development (spent)"
    is_independent = "independent" in resolved_scope
    if is_independent:
        report = _validate_formal_independent_report(report)
    projected = _hybrid_projection(
        report,
        label=label,
        source=str(path),
        scope=resolved_scope,
        formal_authority=is_independent,
    )
    if is_independent and (
        projected.one_shot is not True
        or projected.synthetic is not True
        or projected.acceptance_passed is not True
    ):
        raise ValueError(
            "an independent projection requires a passing, synthetic, one-shot evaluator report"
        )
    return projected


def one_sided_exact_binomial_upper(
    successes: int, trials: int, *, alpha: float = ALPHA
) -> float:
    """Clopper-Pearson one-sided upper confidence bound without SciPy."""
    if (
        isinstance(successes, bool)
        or isinstance(trials, bool)
        or not isinstance(successes, int)
        or not isinstance(trials, int)
        or trials <= 0
        or not 0 <= successes <= trials
        or not 0.0 < alpha < 1.0
    ):
        raise ValueError("invalid exact-binomial inputs")
    if successes == trials:
        return 1.0
    if successes == 0:
        return 1.0 - alpha ** (1.0 / trials)

    def cdf(probability: float) -> float:
        return sum(
            math.comb(trials, index)
            * probability**index
            * (1.0 - probability) ** (trials - index)
            for index in range(successes + 1)
        )

    low = successes / trials
    high = 1.0
    for _ in range(100):
        middle = (low + high) / 2.0
        if cdf(middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def wilson_interval(
    successes: int,
    trials: int,
    *,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binary rate (95% by default)."""

    if (
        isinstance(successes, bool)
        or isinstance(trials, bool)
        or not isinstance(successes, int)
        or not isinstance(trials, int)
        or trials <= 0
        or not 0 <= successes <= trials
        or not math.isfinite(z)
        or z <= 0
    ):
        raise ValueError("invalid Wilson interval inputs")
    proportion = successes / trials
    z_squared = z * z
    denominator = 1.0 + z_squared / trials
    center = (proportion + z_squared / (2.0 * trials)) / denominator
    radius = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z_squared / (4.0 * trials * trials)
        )
        / denominator
    )
    low = 0.0 if successes == 0 else max(0.0, center - radius)
    high = 1.0 if successes == trials else min(1.0, center + radius)
    return low, high


def _rate_summary(
    successes: int,
    trials: int,
    *,
    exact_upper: bool = False,
) -> dict[str, Any] | None:
    if trials <= 0:
        return None
    low, high = wilson_interval(successes, trials)
    result: dict[str, Any] = {
        "successes": successes,
        "trials": trials,
        "rate": successes / trials,
        "wilson95": [low, high],
    }
    if exact_upper:
        result["one_sided_exact_95_upper"] = one_sided_exact_binomial_upper(
            successes,
            trials,
        )
    return result


def exact_mcnemar(before_only: int, after_only: int) -> float | None:
    """Two-sided exact McNemar probability over discordant pairs."""
    if min(before_only, after_only) < 0:
        raise ValueError("transition counts cannot be negative")
    discordant = before_only + after_only
    if discordant == 0:
        return None
    smaller = min(before_only, after_only)
    tail = sum(math.comb(discordant, index) for index in range(smaller + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile requires observations")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def grouped_paired_bootstrap(
    pairs: Sequence[tuple[str, float, float]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Percentile cluster bootstrap that keeps each before/after case paired."""
    if not pairs or samples <= 0:
        raise ValueError("bootstrap requires pairs and a positive sample count")
    groups: dict[str, list[tuple[float, float]]] = {}
    for group_id, before, after in pairs:
        groups.setdefault(str(group_id), []).append((float(before), float(after)))
    group_ids = sorted(groups)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(samples):
        sampled_pairs: list[tuple[float, float]] = []
        for _index in range(len(group_ids)):
            sampled_pairs.extend(groups[group_ids[rng.randrange(len(group_ids))]])
        draws.append(
            statistics.fmean(after - before for before, after in sampled_pairs)
        )
    estimate = statistics.fmean(after - before for _group, before, after in pairs)
    return {
        "method": "paired group-cluster percentile bootstrap",
        "inference_unit": "matched evaluator case",
        "resampling_unit": "group_id (category/relation when reported; otherwise case_id)",
        "group_count": len(group_ids),
        "pair_count": len(pairs),
        "seed": seed,
        "samples": samples,
        "estimate": estimate,
        "ci95": [_quantile(draws, 0.025), _quantile(draws, 0.975)],
    }


def summarize_report(report: ReportProjection) -> dict[str, Any]:
    positives = [case for case in report.cases if case.polarity == "positive"]
    negatives = [case for case in report.cases if case.polarity == "hard_negative"]
    dense_positive_known = [
        case for case in positives if case.dense_top20_hit is not None
    ]
    dense_negative_known = [
        case for case in negatives if case.dense_top20_hit is not None
    ]
    candidate_positive_known = [
        case for case in positives if case.candidate_hit is not None
    ]
    candidate_negative_known = [
        case for case in negatives if case.candidate_hit is not None
    ]
    admitted_positive_known = [
        case for case in positives if case.admitted_hit is not None
    ]
    admitted_negative_known = [
        case for case in negatives if case.admitted_hit is not None
    ]
    positive_rendered = sum(case.target_rendered for case in positives)
    hard_negative_rendered = sum(case.target_rendered for case in negatives)
    stage_counts = dict(report.stage_counts or {})
    admitted_positive_hits = sum(
        bool(case.admitted_hit) for case in admitted_positive_known
    )
    candidate_positive_hits = sum(
        bool(case.candidate_hit) for case in candidate_positive_known
    )
    dense_positive_hits = sum(
        bool(case.dense_top20_hit) for case in dense_positive_known
    )
    hard_negative_candidate_hits = sum(
        bool(case.candidate_hit) for case in candidate_negative_known
    )
    hard_negative_admissions = sum(
        bool(case.admitted_hit) for case in admitted_negative_known
    )
    admitted_false_positives = stage_counts.get("false_positive_admissions")
    rendered_false_positives = stage_counts.get("false_positive_renders")
    admitted_precision = None
    admitted_precision_rate = None
    if isinstance(admitted_false_positives, int):
        admitted_precision = _rate_summary(
            admitted_positive_hits,
            admitted_positive_hits + admitted_false_positives,
        )
        admitted_precision_rate = (
            admitted_precision["rate"] if admitted_precision is not None else None
        )
    rendered_precision = None
    if isinstance(rendered_false_positives, int):
        rendered_precision = _rate_summary(
            positive_rendered,
            positive_rendered + rendered_false_positives,
        )
    elif report.reported_rendered_precision is not None:
        rendered_precision = {
            "successes": None,
            "trials": None,
            "rate": report.reported_rendered_precision,
            "wilson95": None,
        }
    canonical_candidate_recall = _rate_summary(
        candidate_positive_hits,
        len(candidate_positive_known),
    )
    canonical_candidate_hn_rate = _rate_summary(
        hard_negative_candidate_hits,
        len(candidate_negative_known),
        exact_upper=True,
    )
    binary_rates = {
        "global_dense_positive_recall_at_20": _rate_summary(
            dense_positive_hits,
            len(dense_positive_known),
        ),
        "global_dense_hard_negative_target_rate_at_20": _rate_summary(
            sum(bool(case.dense_top20_hit) for case in dense_negative_known),
            len(dense_negative_known),
            exact_upper=True,
        ),
        "canonical_candidate_positive_recall": canonical_candidate_recall,
        "canonical_candidate_hard_negative_target_rate": canonical_candidate_hn_rate,
        # Compatibility aliases. The formal report fixes this cap at 32; legacy
        # projections can have a different cap recorded on ReportProjection.
        "canonical_candidate_positive_recall_at_most_32": canonical_candidate_recall,
        "canonical_candidate_hard_negative_target_rate_at_most_32": canonical_candidate_hn_rate,
        "admitted_positive_recall": _rate_summary(
            admitted_positive_hits,
            len(admitted_positive_known),
        ),
        "admitted_hard_negative_target_rate": _rate_summary(
            hard_negative_admissions,
            len(admitted_negative_known),
            exact_upper=True,
        ),
        "rendered_positive_recall": _rate_summary(
            positive_rendered,
            len(positives),
        ),
        "rendered_hard_negative_target_rate": _rate_summary(
            hard_negative_rendered,
            len(negatives),
            exact_upper=True,
        ),
        "admitted_precision": admitted_precision,
        "rendered_precision": rendered_precision,
        "decision_accuracy": _rate_summary(
            sum(case.decision_correct for case in report.cases),
            len(report.cases),
        ),
    }
    candidate_rate = binary_rates["canonical_candidate_positive_recall"]
    return {
        "case_count": len(report.cases),
        "positive_count": len(positives),
        "hard_negative_count": len(negatives),
        "dense_known_count": len(dense_positive_known),
        "dense_positive_hits": dense_positive_hits,
        "candidate_known_count": len(candidate_positive_known),
        "candidate_positive_hits": candidate_positive_hits,
        "candidate_recall_at_20": (
            candidate_rate["rate"] if candidate_rate is not None else None
        ),
        "global_dense_recall_at_20": (
            binary_rates["global_dense_positive_recall_at_20"]["rate"]
            if binary_rates["global_dense_positive_recall_at_20"] is not None
            else None
        ),
        "canonical_candidate_positive_recall": (
            candidate_rate["rate"] if candidate_rate is not None else None
        ),
        "admitted_positive_hits": admitted_positive_hits,
        "admitted_positive_recall": (
            binary_rates["admitted_positive_recall"]["rate"]
            if binary_rates["admitted_positive_recall"] is not None
            else None
        ),
        "admitted_precision": admitted_precision_rate,
        "rendered_positive_hits": positive_rendered,
        "rendered_positive_recall": (
            positive_rendered / len(positives) if positives else None
        ),
        "hard_negative_target_renders": hard_negative_rendered,
        "hard_negative_target_render_rate": (
            hard_negative_rendered / len(negatives) if negatives else None
        ),
        "hard_negative_safety_rate": (
            1.0 - hard_negative_rendered / len(negatives) if negatives else None
        ),
        "hard_negative_exact_95_upper": (
            one_sided_exact_binomial_upper(hard_negative_rendered, len(negatives))
            if negatives
            else None
        ),
        "decision_correct": sum(case.decision_correct for case in report.cases),
        "decision_accuracy": (
            sum(case.decision_correct for case in report.cases) / len(report.cases)
            if report.cases
            else None
        ),
        "rendered_precision": (
            rendered_precision["rate"] if rendered_precision is not None else None
        ),
        "rates": binary_rates,
    }


def summarize_pressure(report: PressureProjection) -> dict[str, Any]:
    return {
        "raw_relevant": _rate_summary(
            report.raw_relevant_count,
            report.case_count,
            exact_upper=True,
        ),
        "approved": _rate_summary(
            report.approved_count,
            report.case_count,
            exact_upper=True,
        ),
        "protocol_failures": _rate_summary(
            report.protocol_failure_cases,
            report.case_count,
            exact_upper=True,
        ),
    }


def compare_reports(
    before: ReportProjection, after: ReportProjection
) -> dict[str, Any] | None:
    if (
        "development" not in before.scope.casefold()
        or "development" not in after.scope.casefold()
        or before.suite_id != after.suite_id
    ):
        return None
    before_by_id = {case.case_id: case for case in before.cases}
    after_by_id = {case.case_id: case for case in after.cases}
    joined_ids = sorted(before_by_id.keys() & after_by_id.keys())
    if not joined_ids:
        return None
    transition = {"both_correct": 0, "before_only": 0, "after_only": 0, "both_wrong": 0}
    pairs: list[tuple[str, float, float]] = []
    candidate_transition = {
        "both_hit": 0,
        "before_only": 0,
        "after_only": 0,
        "both_miss": 0,
    }
    candidate_pairs: list[tuple[str, float, float]] = []
    dense_transition = {
        "both_hit": 0,
        "before_only": 0,
        "after_only": 0,
        "both_miss": 0,
    }
    dense_pairs: list[tuple[str, float, float]] = []
    admitted_transition = {
        "both_hit": 0,
        "before_only": 0,
        "after_only": 0,
        "both_miss": 0,
    }
    admitted_pairs: list[tuple[str, float, float]] = []
    for case_id in joined_ids:
        left = before_by_id[case_id]
        right = after_by_id[case_id]
        if left.polarity != right.polarity:
            raise ValueError(f"polarity changed for matched case {case_id}")
        if left.group_id != right.group_id:
            raise ValueError(f"group changed for matched case {case_id}")
        before_ok = left.decision_correct
        after_ok = right.decision_correct
        key = (
            "both_correct"
            if before_ok and after_ok
            else "before_only"
            if before_ok
            else "after_only"
            if after_ok
            else "both_wrong"
        )
        transition[key] += 1
        pairs.append(
            (right.group_id or left.group_id, float(before_ok), float(after_ok))
        )
        if (
            left.polarity == "positive"
            and left.dense_top20_hit is not None
            and right.dense_top20_hit is not None
        ):
            left_hit = bool(left.dense_top20_hit)
            right_hit = bool(right.dense_top20_hit)
            dense_key = (
                "both_hit"
                if left_hit and right_hit
                else "before_only"
                if left_hit
                else "after_only"
                if right_hit
                else "both_miss"
            )
            dense_transition[dense_key] += 1
            dense_pairs.append((right.group_id, float(left_hit), float(right_hit)))
        if (
            left.polarity == "positive"
            and left.candidate_hit is not None
            and right.candidate_hit is not None
        ):
            left_hit = bool(left.candidate_hit)
            right_hit = bool(right.candidate_hit)
            candidate_key = (
                "both_hit"
                if left_hit and right_hit
                else "before_only"
                if left_hit
                else "after_only"
                if right_hit
                else "both_miss"
            )
            candidate_transition[candidate_key] += 1
            candidate_pairs.append(
                (right.group_id or left.group_id, float(left_hit), float(right_hit))
            )
        if (
            left.polarity == "positive"
            and left.admitted_hit is not None
            and right.admitted_hit is not None
        ):
            left_hit = bool(left.admitted_hit)
            right_hit = bool(right.admitted_hit)
            admitted_key = (
                "both_hit"
                if left_hit and right_hit
                else "before_only"
                if left_hit
                else "after_only"
                if right_hit
                else "both_miss"
            )
            admitted_transition[admitted_key] += 1
            admitted_pairs.append(
                (right.group_id or left.group_id, float(left_hit), float(right_hit))
            )

    decision_bootstrap = grouped_paired_bootstrap(
        pairs,
        samples=BOOTSTRAP_SAMPLES,
        seed=BOOTSTRAP_SEED,
    )
    return {
        "before": before.label,
        "after": after.label,
        "suite_id": before.suite_id,
        "matched_case_count": len(joined_ids),
        "before_unmatched_count": len(before_by_id.keys() - after_by_id.keys()),
        "after_unmatched_count": len(after_by_id.keys() - before_by_id.keys()),
        "decision_transitions": transition,
        "decision_exact_mcnemar_p": exact_mcnemar(
            transition["before_only"], transition["after_only"]
        ),
        "decision_bootstrap": decision_bootstrap,
        "paired_risk_difference": decision_bootstrap,
        "dense_matched_positive_count": len(dense_pairs),
        "dense_transitions": dense_transition,
        "dense_exact_mcnemar_p": (
            exact_mcnemar(
                dense_transition["before_only"], dense_transition["after_only"]
            )
            if dense_pairs
            else None
        ),
        "dense_bootstrap": (
            grouped_paired_bootstrap(
                dense_pairs,
                samples=BOOTSTRAP_SAMPLES,
                seed=BOOTSTRAP_SEED,
            )
            if dense_pairs
            else None
        ),
        "candidate_matched_positive_count": len(candidate_pairs),
        "candidate_transitions": candidate_transition,
        "candidate_exact_mcnemar_p": (
            exact_mcnemar(
                candidate_transition["before_only"], candidate_transition["after_only"]
            )
            if candidate_pairs
            else None
        ),
        "candidate_bootstrap": (
            grouped_paired_bootstrap(
                candidate_pairs,
                samples=BOOTSTRAP_SAMPLES,
                seed=BOOTSTRAP_SEED,
            )
            if candidate_pairs
            else None
        ),
        "admitted_matched_positive_count": len(admitted_pairs),
        "admitted_transitions": admitted_transition,
        "admitted_exact_mcnemar_p": (
            exact_mcnemar(
                admitted_transition["before_only"], admitted_transition["after_only"]
            )
            if admitted_pairs
            else None
        ),
        "admitted_bootstrap": (
            grouped_paired_bootstrap(
                admitted_pairs,
                samples=BOOTSTRAP_SAMPLES,
                seed=BOOTSTRAP_SEED,
            )
            if admitted_pairs
            else None
        ),
    }


def load_attempt_history(path: Path | None) -> tuple[AttemptRecord, ...]:
    """Load optional, closed-schema provenance without accepting task text."""

    if path is None:
        return ()
    value = _load_report(path)
    if (
        set(value) != {"schema_version", "attempts"}
        or value.get("schema_version") != "memory-retrieval-attempt-history-v1"
    ):
        raise ValueError("attempt history schema mismatch")
    rows = value.get("attempts")
    if not isinstance(rows, list):
        raise TypeError("attempt history rows are missing")
    allowed_fields = {
        "attempt_id",
        "suite",
        "generation",
        "status",
        "stage",
        "reason_code",
        "authority",
        "note",
    }
    records: list[AttemptRecord] = []
    seen: set[str] = set()
    reserved = {record.attempt_id for record in FIXED_EXPERIMENT_PROVENANCE}
    for row in rows:
        if not isinstance(row, dict) or set(row) != allowed_fields:
            raise ValueError("attempt history row schema mismatch")
        if _CASE_TEXT_KEYS & set(row):
            raise ValueError("attempt history contains forbidden text fields")
        textual = {field: row[field] for field in allowed_fields - {"authority"}}
        if any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > (500 if field == "note" else 100)
            or "|" in item
            or "\n" in item
            or "\r" in item
            for field, item in textual.items()
        ):
            raise ValueError("attempt history row text is invalid")
        if not isinstance(row["authority"], bool):
            raise TypeError("attempt history authority must be boolean")
        attempt_id = str(row["attempt_id"])
        if attempt_id in seen or attempt_id in reserved:
            raise ValueError("attempt history contains duplicate attempt IDs")
        seen.add(attempt_id)
        records.append(
            AttemptRecord(
                attempt_id=attempt_id,
                suite=str(row["suite"]),
                generation=str(row["generation"]),
                status=str(row["status"]),
                stage=str(row["stage"]),
                reason_code=str(row["reason_code"]),
                authority=bool(row["authority"]),
                note=str(row["note"]),
            )
        )
    return tuple(records)


def build_analysis(
    reports: Sequence[ReportProjection],
    *,
    resolver_pressure: PressureProjection | None = None,
    attempt_history: Sequence[AttemptRecord] = (),
) -> dict[str, Any]:
    if not reports:
        raise ValueError("at least one report is required")
    if len({report.label for report in reports}) != len(reports):
        raise ValueError("report labels must be unique")
    summaries = {report.label: summarize_report(report) for report in reports}
    comparisons: list[dict[str, Any]] = []
    for left_index, before in enumerate(reports):
        for after in reports[left_index + 1 :]:
            comparison = compare_reports(before, after)
            if comparison is not None:
                comparisons.append(comparison)
    return {
        "analysis_version": 3,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "reports": reports,
        "summaries": summaries,
        "comparisons": comparisons,
        "resolver_pressure": resolver_pressure,
        "resolver_pressure_summary": (
            summarize_pressure(resolver_pressure)
            if resolver_pressure is not None
            else None
        ),
        "attempt_history": tuple(attempt_history),
    }


def require_final_bundle_authority(analysis: Mapping[str, Any]) -> None:
    """Refuse polished artifacts unless both formal authorities are present."""

    reports = analysis.get("reports")
    if not isinstance(reports, Sequence):
        raise TypeError("analysis reports are missing")
    independent = [
        report
        for report in reports
        if isinstance(report, ReportProjection) and report.formal_authority
    ]
    pressure = analysis.get("resolver_pressure")
    if (
        len(independent) != 1
        or independent[0].one_shot is not True
        or independent[0].synthetic is not True
        or independent[0].acceptance_passed is not True
    ):
        raise ValueError(
            "final bundle requires exactly one validated, release-allowlisted independent report"
        )
    if (
        not isinstance(pressure, PressureProjection)
        or pressure.one_shot is not True
        or pressure.synthetic is not True
        or pressure.acceptance_passed is not True
    ):
        raise ValueError(
            "final bundle requires a validated, release-allowlisted resolver-pressure report"
        )


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * float(value):.1f}%"


def _fmt_probability(value: Any) -> str:
    return "N/A (no discordant pairs)" if value is None else f"{float(value):.6g}"


def _fmt_rate_summary(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "—"
    rate = value.get("rate")
    interval = value.get("wilson95")
    if rate is None:
        return "—"
    if (
        isinstance(interval, Sequence)
        and not isinstance(interval, (str, bytes))
        and len(interval) == 2
    ):
        return (
            f"{100 * float(rate):.1f}% "
            f"[{100 * float(interval[0]):.1f}%, {100 * float(interval[1]):.1f}%]"
        )
    return _fmt_rate(rate)


def render_analysis_report(analysis: Mapping[str, Any]) -> str:
    reports: Sequence[ReportProjection] = analysis["reports"]
    summaries: Mapping[str, Mapping[str, Any]] = analysis["summaries"]
    independent = next((report for report in reports if report.formal_authority), None)
    pressure: PressureProjection | None = analysis.get("resolver_pressure")
    pressure_summary = analysis.get("resolver_pressure_summary")
    if independent is None:
        lead = "尚无独立 one-shot 报告；现有结果只能用于开发诊断，不能据此宣称泛化或生产晋级。"
    else:
        status = "通过" if independent.acceptance_passed is True else "未通过"
        metric = summaries[independent.label]
        lead = (
            f"独立 one-shot 门禁{status}：global Dense Top20 正例召回 "
            f"{_fmt_rate_summary(metric['rates']['global_dense_positive_recall_at_20'])}，"
            "canonical ≤32 候选正例召回 "
            f"{_fmt_rate_summary(metric['rates']['canonical_candidate_positive_recall_at_most_32'])}，"
            "admitted 正例召回 "
            f"{_fmt_rate_summary(metric['rates']['admitted_positive_recall'])}，"
            "渲染正例召回 "
            f"{_fmt_rate_summary(metric['rates']['rendered_positive_recall'])}，"
            "hard-negative 误渲染 "
            f"{metric['hard_negative_target_renders']}/{metric['hard_negative_count']}。"
        )
    lines = [
        "# Memory retrieval quality v3 — analysis report",
        "",
        "## Analysis question",
        "",
        "正式问题是：冻结实现是否在全新合成 independent one-shot 上同时保持候选召回、admitted/rendered 相关性门控，并在独立 resolver-pressure one-shot 上满足预注册安全门；开发报告只回答同 suite 的诊断性变化。",
        "",
        "## 结论",
        "",
        lead,
        "",
        "所有数据均为合成任务。开发/已使用数据与独立 one-shot 数据分栏报告；以下差异是描述性关联，不是因果效应，也不外推到生产任务总体。",
        "",
        "## 汇总指标",
        "",
        "Wilson 95% CI 均写在点估计后的方括号内。global Dense Top20 与 canonical 候选是不同检索阶段，绝不合并口径；当前正式 independent 的 canonical 上限为 32，历史报告按各自 cap 展示。",
        "",
        "| 报告 | 证据范围 | N（正/HN） | Dense Top20 recall | Canonical candidate recall | Admitted recall | Admitted precision | Rendered recall | Rendered precision | HN admitted | HN rendered | 门禁 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for report in reports:
        summary = summaries[report.label]
        gate = (
            "通过"
            if report.acceptance_passed is True
            else "未通过"
            if report.acceptance_passed is False
            else "未报告"
        )
        lines.append(
            f"| {report.label} | {report.scope} | {summary['case_count']} "
            f"({summary['positive_count']}/{summary['hard_negative_count']}) | "
            f"{_fmt_rate_summary(summary['rates']['global_dense_positive_recall_at_20'])} | "
            f"{_fmt_rate_summary(summary['rates']['canonical_candidate_positive_recall'])} | "
            f"{_fmt_rate_summary(summary['rates']['admitted_positive_recall'])} | "
            f"{_fmt_rate_summary(summary['rates']['admitted_precision'])} | "
            f"{_fmt_rate_summary(summary['rates']['rendered_positive_recall'])} | "
            f"{_fmt_rate_summary(summary['rates']['rendered_precision'])} | "
            f"{_fmt_rate_summary(summary['rates']['admitted_hard_negative_target_rate'])} | "
            f"{_fmt_rate_summary(summary['rates']['rendered_hard_negative_target_rate'])} | "
            f"{gate} |"
        )
    lines.extend(["", "## Resolver-pressure authority", ""])
    if pressure is None or not isinstance(pressure_summary, Mapping):
        lines.append(
            "未提供经过正式 validator 与 release allowlist 验证的 pressure 报告。"
        )
    else:
        approved = pressure_summary["approved"]
        lines.extend(
            [
                f"- Authority: `{pressure.schema_version}`; N={pressure.case_count}; gate={'passed' if pressure.acceptance_passed else 'failed'}.",
                f"- Raw relevant: {_fmt_rate_summary(pressure_summary['raw_relevant'])}.",
                f"- Approved: {_fmt_rate_summary(approved)}; one-sided exact 95% upper={_fmt_rate(approved['one_sided_exact_95_upper'])}.",
                f"- Protocol failures: {_fmt_rate_summary(pressure_summary['protocol_failures'])}.",
            ]
        )
    lines.extend(["", "## 同 ID 配对结果", ""])
    comparisons = analysis["comparisons"]
    if not comparisons:
        lines.append("没有同 suite 的开发报告共享 case ID，因此不计算配对差异。")
    else:
        lines.extend(
            [
                "| 比较 | 配对 N | 正确性：退步/改善 | McNemar exact p | 平均正确性差（95% grouped bootstrap CI） |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for comparison in comparisons:
            transition = comparison["decision_transitions"]
            boot = comparison["decision_bootstrap"]
            lines.append(
                f"| {comparison['before']} → {comparison['after']} | "
                f"{comparison['matched_case_count']} | {transition['before_only']}/"
                f"{transition['after_only']} | "
                f"{_fmt_probability(comparison['decision_exact_mcnemar_p'])} | "
                f"{boot['estimate']:+.3f} [{boot['ci95'][0]:+.3f}, "
                f"{boot['ci95'][1]:+.3f}] |"
            )
    lines.extend(["", "## 实验链 provenance", ""])
    lines.append(
        "历史失败/失效记录来自实验操作者提供并固定在 analyzer 中；当前 pressure/independent authority 则由输入工件的正式校验链自动确认。"
    )
    lines.append("")
    supplied_attempts: Sequence[AttemptRecord] = analysis.get("attempt_history", ())
    dynamic_authorities: list[AttemptRecord] = []
    if pressure is not None:
        dynamic_authorities.append(
            AttemptRecord(
                attempt_id="resolver-pressure-current-authority",
                suite="resolver-pressure",
                generation=pressure.schema_version,
                status="accepted-current-authority",
                stage="release-allowlist",
                reason_code="validated_fingerprint_one_shot_release",
                authority=True,
                note="A distinct current authority; not represented as a rerun of v2 or v3.",
            )
        )
    if independent is not None:
        dynamic_authorities.append(
            AttemptRecord(
                attempt_id="independent-current-authority",
                suite="independent-promotion",
                generation=independent.suite_id,
                status="accepted-current-authority",
                stage="release-allowlist",
                reason_code="validated_fingerprint_one_shot_release",
                authority=True,
                note="A distinct current authority; not represented as a rerun of independent v1.",
            )
        )
    attempts_by_id = {
        attempt.attempt_id: attempt
        for attempt in (
            *FIXED_EXPERIMENT_PROVENANCE,
            *supplied_attempts,
            *dynamic_authorities,
        )
    }
    lines.extend(
        [
            "| Attempt | Suite | Generation | Status | Stage | Reason | Authority | Note |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for attempt in attempts_by_id.values():
        lines.append(
            f"| {attempt.attempt_id} | {attempt.suite} | {attempt.generation} | "
            f"{attempt.status} | {attempt.stage} | `{attempt.reason_code}` | "
            f"{'yes' if attempt.authority else 'no'} | {attempt.note} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 统计单位是 evaluator case；bootstrap 保持同一 case 的 before/after 配对。",
            "- 分组重采样优先使用 category/relation；报告未提供分组时退化为 case_id，详见统计附录。",
            "- 历史 semantic-v2 与旧 Qwen 报告已经参与开发，均标记为 spent；只能用于回归描述。",
            "- 精确 McNemar 仅在相同 case ID 上计算；不同测试集之间只并列展示，绝不伪配对。",
            f"- 配对 risk difference 使用固定 seed={BOOTSTRAP_SEED}、B={BOOTSTRAP_SAMPLES:,} 的 grouped paired bootstrap。",
            "- 单次模型运行可能有随机性；本报告不把观察差异归因于某一代码改动。",
            "",
            "## Claim Candidates",
            "",
            "- Claim:",
            "  - Source evidence: 经过正式 validator、fingerprint、one-shot receipt 与 release allowlist 校验的 independent 与 resolver-pressure 报告。",
            "  - Allowed wording: 在各自预注册的合成 one-shot suite 上，按表中点估计、Wilson 95% CI 与 exact upper 描述召回和误接纳结果。",
            "  - Forbidden stronger wording: 不得写成生产总体已证明、真实项目必然提升、或某一实现改动造成了观察差异。",
            "  - Uncertainty: 合成 suite、单次模型运行、case 内相关性以及 pressure 与 independent 并非同一比较族。",
            "  - Next check: 在新冻结、外部登记的真实项目任务集上做重复运行。",
            "  - Decision: keep",
            "",
            "- Claim:",
            "  - Source evidence: 同 suite、同 case ID 的开发配对结果（若上表存在）。",
            "  - Allowed wording: 仅将 exact McNemar 和 paired risk difference 作为开发诊断。",
            "  - Forbidden stronger wording: 不得把 spent development p 值称为独立确认，也不得跨 suite 比较。",
            "  - Uncertainty: 数据已参与开发决策。",
            "  - Next check: 只用新的独立 suite 复核方向。",
            "  - Decision: weaken",
            "",
        ]
    )
    return "\n".join(lines)


def render_stats_appendix(analysis: Mapping[str, Any]) -> str:
    reports: Sequence[ReportProjection] = analysis["reports"]
    summaries: Mapping[str, Mapping[str, Any]] = analysis["summaries"]
    lines = [
        "# Memory retrieval quality v3 — statistical appendix",
        "",
        "## 方法",
        "",
        f"- 固定 seed：{analysis['bootstrap_seed']}（所有开发配对比较均使用此值）。",
        f"- 重采样次数：{analysis['bootstrap_samples']:,}。",
        "- 配对检验：discordant pairs 上的双侧 exact McNemar；无 discordant pair 时 p 不定义。",
        "- 配对效应：after − before 的 paired risk difference；按 group_id 聚类并保持 case 内配对的 percentile bootstrap 95% CI。",
        "- 所有二元率：Wilson score 95% CI。",
        "- HN 与 pressure 风险：另报 one-sided exact Clopper–Pearson 95% upper；尤其 0/N 时为 1 − 0.05^(1/N)。",
        "- 比较族：只比较 suite_id 完全一致的 development/spent 报告；independent、pressure 或不同 suite 之间不做检验。",
        "- 多重比较：McNemar p 仅作 spent-development 诊断，不据此给出显著性或胜者结论，因此不进行事后 multiplicity-adjusted 宣称；若未来预注册多个 confirmatory contrasts，必须先指定校正方法。",
        "",
        "## 数据与模型身份",
        "",
    ]
    for report in reports:
        summary = summaries[report.label]
        usage = (
            ", ".join(f"{key}={value}" for key, value in sorted(report.usage.items()))
            or "not reported"
        )
        lines.extend(
            [
                f"### {report.label}",
                "",
                f"- Source report: `{report.source}`",
                f"- Schema: `{report.schema_version}`; scope: {report.scope}; synthetic={str(report.synthetic).lower()}; one_shot={str(report.one_shot).lower()}.",
                f"- Sample: N={summary['case_count']}; positives={summary['positive_count']}; hard negatives={summary['hard_negative_count']}; candidate-known positives={summary['candidate_known_count']}.",
                f"- Stage limits: global dense top_k={report.dense_top_k if report.dense_top_k is not None else 'not reported'}; canonical candidate cap={report.canonical_candidate_limit if report.canonical_candidate_limit is not None else 'not reported'}.",
                f"- Embedding: {report.embedding_identity}; verifier: {report.verifier_identity}; challenger: {report.challenger_identity}.",
                f"- Provider usage: {usage}.",
                f"- Global Dense Top20 recall: {_fmt_rate_summary(summary['rates']['global_dense_positive_recall_at_20'])}.",
                f"- Canonical candidate recall (cap={report.canonical_candidate_limit if report.canonical_candidate_limit is not None else 'not reported'}): {_fmt_rate_summary(summary['rates']['canonical_candidate_positive_recall'])}.",
                f"- Admitted recall / precision: {_fmt_rate_summary(summary['rates']['admitted_positive_recall'])} / {_fmt_rate_summary(summary['rates']['admitted_precision'])}.",
                f"- Rendered recall / precision: {_fmt_rate_summary(summary['rates']['rendered_positive_recall'])} / {_fmt_rate_summary(summary['rates']['rendered_precision'])}.",
                f"- HN admitted: {_fmt_rate_summary(summary['rates']['admitted_hard_negative_target_rate'])}; exact upper={_fmt_rate(summary['rates']['admitted_hard_negative_target_rate'].get('one_sided_exact_95_upper') if summary['rates']['admitted_hard_negative_target_rate'] else None)}.",
                f"- HN rendered: {_fmt_rate_summary(summary['rates']['rendered_hard_negative_target_rate'])}; exact upper={_fmt_rate(summary['rates']['rendered_hard_negative_target_rate'].get('one_sided_exact_95_upper') if summary['rates']['rendered_hard_negative_target_rate'] else None)}.",
                "",
            ]
        )
    pressure: PressureProjection | None = analysis.get("resolver_pressure")
    pressure_summary = analysis.get("resolver_pressure_summary")
    if pressure is not None and isinstance(pressure_summary, Mapping):
        lines.extend(
            [
                "### resolver-pressure-one-shot",
                "",
                f"- Source report: `{pressure.source}`; schema=`{pressure.schema_version}`; N={pressure.case_count}.",
                f"- Raw relevant: {_fmt_rate_summary(pressure_summary['raw_relevant'])}; exact upper={_fmt_rate(pressure_summary['raw_relevant']['one_sided_exact_95_upper'])}.",
                f"- Approved: {_fmt_rate_summary(pressure_summary['approved'])}; exact upper={_fmt_rate(pressure_summary['approved']['one_sided_exact_95_upper'])}.",
                f"- Protocol failures: {_fmt_rate_summary(pressure_summary['protocol_failures'])}; exact upper={_fmt_rate(pressure_summary['protocol_failures']['one_sided_exact_95_upper'])}.",
                f"- Provider usage: {', '.join(f'{key}={value}' for key, value in sorted(pressure.usage.items()))}.",
                "",
            ]
        )
    lines.extend(["## 配对统计明细", ""])
    if not analysis["comparisons"]:
        lines.append("No joinable case IDs.")
    for comparison in analysis["comparisons"]:
        lines.extend(
            [
                f"### {comparison['before']} → {comparison['after']}",
                "",
                f"- Matched cases: {comparison['matched_case_count']}.",
                f"- Same suite: `{comparison['suite_id']}`; unmatched before/after={comparison['before_unmatched_count']}/{comparison['after_unmatched_count']}.",
                f"- Decision transitions: `{json.dumps(comparison['decision_transitions'], sort_keys=True)}`.",
                f"- Exact McNemar p: {_fmt_probability(comparison['decision_exact_mcnemar_p'])}.",
                f"- Paired risk difference: `{json.dumps(comparison['paired_risk_difference'], sort_keys=True)}`.",
                f"- Dense Top20 matched positives: {comparison['dense_matched_positive_count']}; transitions: `{json.dumps(comparison['dense_transitions'], sort_keys=True)}`.",
                f"- Dense exact McNemar p: {_fmt_probability(comparison['dense_exact_mcnemar_p'])}.",
                f"- Dense bootstrap: `{json.dumps(comparison['dense_bootstrap'], sort_keys=True)}`.",
                f"- Candidate matched positives: {comparison['candidate_matched_positive_count']}; transitions: `{json.dumps(comparison['candidate_transitions'], sort_keys=True)}`.",
                f"- Candidate exact McNemar p: {_fmt_probability(comparison['candidate_exact_mcnemar_p'])}.",
                f"- Candidate bootstrap: `{json.dumps(comparison['candidate_bootstrap'], sort_keys=True)}`.",
                f"- Admitted matched positives: {comparison['admitted_matched_positive_count']}; transitions: `{json.dumps(comparison['admitted_transitions'], sort_keys=True)}`.",
                f"- Admitted exact McNemar p: {_fmt_probability(comparison['admitted_exact_mcnemar_p'])}.",
                f"- Admitted bootstrap: `{json.dumps(comparison['admitted_bootstrap'], sort_keys=True)}`.",
                "",
            ]
        )
    lines.extend(
        [
            "## 限制",
            "",
            "Wilson 与 exact 区间以 evaluator case/事件为二项口径；同 category/relation 的相关性不由 Wilson 建模。grouped bootstrap 只用于同 suite 开发配对。所有区间量化当前合成 fixture 内的不确定性，不是生产总体置信区间；开发数据上的 p 值仅作描述，不能恢复已经因调参而失去的独立性。",
            "",
        ]
    )
    return "\n".join(lines)


def _load_figure_backend(importer: Any = importlib.import_module) -> FigureBackend:
    """Return the dependency-free SVG backend.

    ``importer`` remains accepted for API compatibility with older callers, but
    no optional plotting package is imported or required.
    """

    del importer
    return FigureBackend(name="svg-stdlib", api={})


def _svg_text(
    x: float,
    y: float,
    value: str,
    *,
    size: int = 14,
    anchor: str = "middle",
    weight: str = "normal",
    fill: str = "#1f2937",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
        f'font-family="system-ui, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{html.escape(value)}</text>'
    )


def _write_svg(path: Path, *, width: int, height: int, elements: Sequence[str]) -> None:
    body = "\n  ".join(elements)
    path.write_text(
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">\n'
            f"  {body}\n</svg>\n"
        ),
        encoding="utf-8",
    )


def _render_svg_stage_rates(analysis: Mapping[str, Any], figures_dir: Path) -> None:
    reports: Sequence[ReportProjection] = analysis["reports"]
    summaries: Mapping[str, Mapping[str, Any]] = analysis["summaries"]
    stages = (
        ("global_dense_positive_recall_at_20", "Dense Top20", "#2563eb"),
        (
            "canonical_candidate_positive_recall",
            "Canonical candidate",
            "#7c3aed",
        ),
        ("admitted_positive_recall", "Admitted", "#ea580c"),
        ("rendered_positive_recall", "Rendered", "#059669"),
    )
    width = max(980, 250 * len(reports) + 180)
    height = 650
    left, right, top, bottom = 90, width - 40, 100, 500
    elements = [
        "<title>Positive-stage recall with Wilson 95% confidence intervals</title>",
        "<desc>Global Dense Top20, canonical candidate, admitted, and rendered recall are shown as separate stages.</desc>",
        _svg_text(
            width / 2,
            42,
            "Positive-stage recall (Wilson 95% CI)",
            size=24,
            weight="700",
        ),
    ]
    for index in range(5):
        rate = index / 4
        y = bottom - rate * (bottom - top)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#d1d5db" />'
        )
        elements.append(
            _svg_text(left - 12, y + 5, f"{rate:.0%}", anchor="end", size=12)
        )
    group_width = (right - left) / len(reports)
    bar_width = min(42.0, group_width / 5.5)
    for report_index, report in enumerate(reports):
        center = left + group_width * (report_index + 0.5)
        for stage_index, (key, _label, color) in enumerate(stages):
            rate = summaries[report.label]["rates"][key]
            x = center + (stage_index - 1.5) * (bar_width + 5)
            if rate is None:
                elements.append(
                    _svg_text(x, bottom - 8, "N/A", size=10, fill="#6b7280")
                )
                continue
            value = float(rate["rate"])
            y = bottom - value * (bottom - top)
            elements.append(
                f'<rect x="{x - bar_width / 2:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                f'height="{bottom - y:.1f}" fill="{color}" />'
            )
            interval = rate["wilson95"]
            ci_low_y = bottom - float(interval[0]) * (bottom - top)
            ci_high_y = bottom - float(interval[1]) * (bottom - top)
            elements.extend(
                [
                    f'<line x1="{x:.1f}" y1="{ci_high_y:.1f}" x2="{x:.1f}" y2="{ci_low_y:.1f}" stroke="#111827" stroke-width="2" />',
                    f'<line x1="{x - 5:.1f}" y1="{ci_high_y:.1f}" x2="{x + 5:.1f}" y2="{ci_high_y:.1f}" stroke="#111827" stroke-width="2" />',
                    f'<line x1="{x - 5:.1f}" y1="{ci_low_y:.1f}" x2="{x + 5:.1f}" y2="{ci_low_y:.1f}" stroke="#111827" stroke-width="2" />',
                    _svg_text(x, max(86, y - 10), f"{value:.0%}", size=10),
                ]
            )
        elements.append(
            _svg_text(
                center, bottom + 28, _short_label(report.label, limit=28), size=12
            )
        )
    legend_y = 565
    legend_x = left
    for _key, label, color in stages:
        elements.append(
            f'<rect x="{legend_x}" y="{legend_y - 13}" width="16" height="16" fill="{color}" />'
        )
        elements.append(
            _svg_text(legend_x + 23, legend_y, label, anchor="start", size=12)
        )
        legend_x += 190
    elements.append(
        _svg_text(
            left,
            625,
            "Bars from different suites are descriptive only; error bars are Wilson 95% intervals.",
            anchor="start",
            size=12,
            fill="#4b5563",
        )
    )
    _write_svg(
        figures_dir / "figure-01-stage-recall.svg",
        width=width,
        height=height,
        elements=elements,
    )


def _render_svg_gate_safety(analysis: Mapping[str, Any], figures_dir: Path) -> None:
    reports: Sequence[ReportProjection] = analysis["reports"]
    summaries: Mapping[str, Mapping[str, Any]] = analysis["summaries"]
    observations: list[tuple[str, Mapping[str, Any], str]] = []
    formal_reports = [report for report in reports if report.formal_authority]
    for report in formal_reports or list(reports):
        for key, suffix, color in (
            ("admitted_hard_negative_target_rate", "admitted HN", "#f97316"),
            ("rendered_hard_negative_target_rate", "rendered HN", "#dc2626"),
        ):
            rate = summaries[report.label]["rates"][key]
            if isinstance(rate, Mapping):
                observations.append((f"{report.label}\n{suffix}", rate, color))
    pressure = analysis.get("resolver_pressure_summary")
    if isinstance(pressure, Mapping) and isinstance(pressure.get("approved"), Mapping):
        observations.append(
            ("resolver pressure\napproved", pressure["approved"], "#7c3aed")
        )
    width = max(980, 150 * len(observations) + 170)
    height = 650
    left, right, top, bottom = 90, width - 40, 100, 490
    elements = [
        "<title>Hard-negative gate event rates and exact upper bounds</title>",
        "<desc>Each bar is an observed hard-negative or pressure event rate; black markers are one-sided exact 95 percent upper bounds.</desc>",
        _svg_text(width / 2, 42, "Relevance-gate safety events", size=24, weight="700"),
    ]
    maximum_upper = max(
        [
            float(item[1].get("one_sided_exact_95_upper", item[1]["rate"]))
            for item in observations
        ]
        or [1.0]
    )
    axis_max = min(1.0, max(0.1, math.ceil(maximum_upper * 10.0) / 10.0))
    for index in range(5):
        value = axis_max * index / 4
        y = bottom - index / 4 * (bottom - top)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#d1d5db" />'
        )
        elements.append(
            _svg_text(left - 12, y + 5, f"{value:.1%}", anchor="end", size=12)
        )
    spacing = (right - left) / max(1, len(observations))
    for index, (label, rate, color) in enumerate(observations):
        x = left + spacing * (index + 0.5)
        value = float(rate["rate"])
        upper = float(rate["one_sided_exact_95_upper"])
        y = bottom - (value / axis_max) * (bottom - top)
        upper_y = bottom - (upper / axis_max) * (bottom - top)
        elements.extend(
            [
                f'<rect x="{x - 28:.1f}" y="{y:.1f}" width="56" height="{bottom - y:.1f}" fill="{color}" />',
                f'<line x1="{x - 24:.1f}" y1="{upper_y:.1f}" x2="{x + 24:.1f}" y2="{upper_y:.1f}" stroke="#111827" stroke-width="3" />',
                _svg_text(x, max(86, y - 10), f"{value:.1%}", size=11),
            ]
        )
        for line_index, line in enumerate(label.split("\n")):
            elements.append(_svg_text(x, bottom + 27 + 18 * line_index, line, size=11))
    elements.extend(
        [
            '<line x1="90" y1="570" x2="125" y2="570" stroke="#111827" stroke-width="3" />',
            _svg_text(135, 575, "one-sided exact 95% upper", anchor="start", size=12),
            _svg_text(
                left,
                625,
                "Pressure and independent suites are not compared; they are separate safety estimates.",
                anchor="start",
                size=12,
                fill="#4b5563",
            ),
        ]
    )
    _write_svg(
        figures_dir / "figure-02-gate-safety.svg",
        width=width,
        height=height,
        elements=elements,
    )


def _figure_catalog(
    backend: FigureBackend, *, comparisons: bool
) -> list[dict[str, str]]:
    catalog = [
        {
            "id": "Figure 1",
            "filename": "figures/figure-01-stage-recall.svg",
            "purpose": "Separate retrieval recall at global Dense Top20, canonical candidate (current formal cap ≤32), admitted, and rendered stages.",
            "data_source": "validated content-free case projections from supplied evaluator reports",
            "caption_requirements": "Define every stage, state numerator/denominator, identify Wilson 95% intervals, and label each suite as synthetic.",
            "key_observation": "Stage-specific bars expose where positive targets are lost; missing stages are shown as N/A rather than imputed.",
            "interpretation_checklist": "Check suite identity; compare stages within a report; do not interpret cross-suite bar differences as paired effects.",
            "known_caveats": "Different suites can have different cases; Wilson intervals treat evaluator cases as binary observations.",
            "allowed_claim_candidate": "On the named synthetic suite, report each observed stage recall with its Wilson 95% interval.",
            "forbidden_claim_candidate": "The hybrid system universally improves recall or a stage difference is causal.",
            "backend": backend.name,
        },
        {
            "id": "Figure 2",
            "filename": "figures/figure-02-gate-safety.svg",
            "purpose": "Show hard-negative admission/render events and resolver-pressure approvals with exact one-sided risk bounds.",
            "data_source": "validated content-free independent and resolver-pressure aggregate outcomes",
            "caption_requirements": "State event definitions, N, observed rates, and that black markers are one-sided exact Clopper–Pearson 95% upper bounds.",
            "key_observation": "Zero observed events still imply a non-zero finite-sample upper risk bound.",
            "interpretation_checklist": "Read the exact upper marker; keep pressure and independent suites separate; distinguish admission from rendering.",
            "known_caveats": "Synthetic one-shot evidence does not estimate production prevalence or prove zero real-world risk.",
            "allowed_claim_candidate": "On each named synthetic suite, no more than the reported number of events was observed; quote the exact upper bound.",
            "forbidden_claim_candidate": "The relevance gate has zero false-positive risk in production.",
            "backend": backend.name,
        },
    ]
    if comparisons:
        catalog.append(
            {
                "id": "Supporting paired table",
                "filename": "analysis-report.md#同-id-配对结果",
                "purpose": "Record exact matched-case development transitions without implying independent confirmation.",
                "data_source": "same-suite intersection of exact case IDs",
                "caption_requirements": "Report discordances, exact McNemar p, paired risk difference, seed, B, and unmatched counts.",
                "key_observation": "Only off-diagonal exact-ID pairs contribute to McNemar evidence.",
                "interpretation_checklist": "Verify suite IDs match and treat spent-development inference as diagnostic only.",
                "known_caveats": "Development data participated in tuning and cannot serve as an independent confirmation.",
                "allowed_claim_candidate": "Describe the observed same-suite development transition and uncertainty.",
                "forbidden_claim_candidate": "A significant development comparison proves generalization.",
                "backend": backend.name,
            }
        )
    return catalog


def _render_matplotlib_figures(
    analysis: Mapping[str, Any], figures_dir: Path, backend: FigureBackend
) -> None:
    plt = backend.api["pyplot"]
    reports: Sequence[ReportProjection] = analysis["reports"]
    summaries: Mapping[str, Mapping[str, Any]] = analysis["summaries"]
    labels = [report.label for report in reports]
    metric_specs = (
        ("candidate_recall_at_20", "Candidate R@20"),
        ("rendered_positive_recall", "Positive render recall"),
        ("hard_negative_safety_rate", "HN safety"),
    )
    x = list(range(len(labels)))
    width = 0.24
    fig, axis = plt.subplots(figsize=(max(7.5, len(labels) * 1.7), 4.8))
    colors = ("#0072B2", "#E69F00", "#009E73")
    for metric_index, (metric, metric_label) in enumerate(metric_specs):
        values = [summaries[label][metric] for label in labels]
        offsets = [value + (metric_index - 1) * width for value in x]
        bars = axis.bar(
            offsets,
            [float(value) if value is not None else 0.0 for value in values],
            width,
            label=metric_label,
            color=colors[metric_index],
        )
        for bar, value in zip(bars, values):
            if value is not None:
                axis.text(
                    bar.get_x() + bar.get_width() / 2,
                    min(1.04, float(value) + 0.025),
                    f"{100 * float(value):.0f}%",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
    axis.set_ylim(0, 1.13)
    axis.set_ylabel("Rate")
    axis.set_xticks(x, labels, rotation=15, ha="right")
    axis.set_title("Retrieval and relevance-gate outcomes by evaluator report")
    axis.legend(loc="lower right", fontsize=8)
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(figures_dir / f"figure-01-outcome-rates.{suffix}", dpi=180)
    plt.close(fig)

    comparisons = analysis["comparisons"]
    if comparisons:
        columns = min(3, len(comparisons))
        rows = math.ceil(len(comparisons) / columns)
        fig, axes = plt.subplots(
            rows, columns, figsize=(4.2 * columns, 3.8 * rows), squeeze=False
        )
        for axis, comparison in zip(
            (item for row in axes for item in row), comparisons
        ):
            transition = comparison["decision_transitions"]
            matrix = [
                [transition["both_wrong"], transition["after_only"]],
                [transition["before_only"], transition["both_correct"]],
            ]
            image = axis.imshow(matrix, cmap="Blues", vmin=0)
            for row_index in range(2):
                for column_index in range(2):
                    axis.text(
                        column_index,
                        row_index,
                        str(matrix[row_index][column_index]),
                        ha="center",
                        va="center",
                        color="black",
                        fontsize=12,
                    )
            axis.set_xticks((0, 1), ("After wrong", "After correct"))
            axis.set_yticks((0, 1), ("Before wrong", "Before correct"))
            axis.set_title(
                f"{comparison['before']} → {comparison['after']}\nN={comparison['matched_case_count']}"
            )
            image.set_clim(0, max(1, max(max(row) for row in matrix)))
        for axis in [item for row in axes for item in row][len(comparisons) :]:
            axis.axis("off")
        fig.suptitle("Exact matched-case decision transitions")
        fig.tight_layout()
        for suffix in ("png", "pdf"):
            fig.savefig(figures_dir / f"figure-02-paired-transitions.{suffix}", dpi=180)
        plt.close(fig)


def _pillow_font(backend: FigureBackend, size: int, *, bold: bool = False) -> Any:
    image_font = backend.api["ImageFont"]
    family = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return image_font.truetype(family, size=size)
    except (OSError, TypeError):
        try:
            return image_font.load_default(size=size)
        except TypeError:
            return image_font.load_default()


def _draw_centered(
    draw: Any,
    xy: tuple[float, float],
    text: str,
    *,
    font: Any,
    fill: str | tuple[int, int, int] = "#1F2937",
) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((xy[0] - width / 2, xy[1] - height / 2), text, font=font, fill=fill)


def _save_pillow_figure(image: Any, base_path: Path) -> None:
    """Persist the same data-bearing RGB canvas as PNG and PDF."""
    image.save(base_path.with_suffix(".png"), format="PNG", optimize=True)
    image.save(base_path.with_suffix(".pdf"), format="PDF", resolution=144.0)


def _short_label(value: str, *, limit: int = 24) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _render_pillow_outcome_rates(
    analysis: Mapping[str, Any], figures_dir: Path, backend: FigureBackend
) -> None:
    image_api = backend.api["Image"]
    draw_api = backend.api["ImageDraw"]
    reports: Sequence[ReportProjection] = analysis["reports"]
    summaries: Mapping[str, Mapping[str, Any]] = analysis["summaries"]
    labels = [report.label for report in reports]
    metric_specs = (
        ("candidate_recall_at_20", "Candidate R@20", "#0072B2"),
        ("rendered_positive_recall", "Positive render recall", "#E69F00"),
        ("hard_negative_safety_rate", "HN safety", "#009E73"),
    )
    width = max(1280, 260 * len(labels) + 220)
    height = 820
    image = image_api.new("RGB", (width, height), "white")
    draw = draw_api.Draw(image)
    title_font = _pillow_font(backend, 30, bold=True)
    label_font = _pillow_font(backend, 19)
    small_font = _pillow_font(backend, 16)
    value_font = _pillow_font(backend, 17, bold=True)
    left, right, top, bottom = 110, width - 50, 125, 620
    draw.text(
        (left, 35),
        "Retrieval and relevance-gate outcomes by evaluator report",
        font=title_font,
        fill="#111827",
    )
    for index in range(5):
        rate = index / 4
        y = bottom - rate * (bottom - top)
        draw.line((left, y, right, y), fill="#D1D5DB", width=2)
        text = f"{rate:.0%}"
        bounds = draw.textbbox((0, 0), text, font=small_font)
        draw.text(
            (left - (bounds[2] - bounds[0]) - 14, y - 9),
            text,
            font=small_font,
            fill="#4B5563",
        )
    draw.line((left, top, left, bottom), fill="#374151", width=3)
    draw.line((left, bottom, right, bottom), fill="#374151", width=3)
    group_width = (right - left) / max(1, len(labels))
    bar_width = min(56, group_width / 4.3)
    for report_index, label in enumerate(labels):
        group_center = left + group_width * (report_index + 0.5)
        for metric_index, (metric, _metric_label, color) in enumerate(metric_specs):
            raw = summaries[label][metric]
            x_center = group_center + (metric_index - 1) * (bar_width + 8)
            if raw is None:
                draw.rectangle(
                    (
                        x_center - bar_width / 2,
                        bottom - 4,
                        x_center + bar_width / 2,
                        bottom,
                    ),
                    fill="#9CA3AF",
                )
                _draw_centered(
                    draw,
                    (x_center, bottom - 22),
                    "N/A",
                    font=small_font,
                    fill="#6B7280",
                )
                continue
            value = min(1.0, max(0.0, float(raw)))
            y = bottom - value * (bottom - top)
            draw.rectangle(
                (x_center - bar_width / 2, y, x_center + bar_width / 2, bottom),
                fill=color,
            )
            _draw_centered(
                draw,
                (x_center, max(top - 14, y - 20)),
                f"{100 * value:.0f}%",
                font=value_font,
            )
        _draw_centered(
            draw,
            (group_center, bottom + 34),
            _short_label(label),
            font=label_font,
        )
    legend_y = 704
    legend_width = (right - left) / len(metric_specs)
    for index, (_metric, metric_label, color) in enumerate(metric_specs):
        x = left + legend_width * index
        draw.rectangle((x, legend_y, x + 24, legend_y + 24), fill=color)
        draw.text((x + 34, legend_y), metric_label, font=label_font, fill="#1F2937")
    draw.text(
        (left, 770),
        "Descriptive bars use each report's own evaluator sample; cross-suite bars are not paired effects.",
        font=small_font,
        fill="#4B5563",
    )
    _save_pillow_figure(image, figures_dir / "figure-01-outcome-rates")


def _heat_color(value: int, maximum: int) -> tuple[int, int, int]:
    intensity = value / max(1, maximum)
    return (
        round(239 - 174 * intensity),
        round(246 - 101 * intensity),
        round(255 - 62 * intensity),
    )


def _render_pillow_transitions(
    analysis: Mapping[str, Any], figures_dir: Path, backend: FigureBackend
) -> None:
    image_api = backend.api["Image"]
    draw_api = backend.api["ImageDraw"]
    comparisons: Sequence[Mapping[str, Any]] = analysis["comparisons"]
    columns = min(3, len(comparisons))
    rows = math.ceil(len(comparisons) / columns)
    panel_width, panel_height = 470, 430
    width, height = columns * panel_width + 80, rows * panel_height + 100
    image = image_api.new("RGB", (width, height), "white")
    draw = draw_api.Draw(image)
    title_font = _pillow_font(backend, 29, bold=True)
    panel_font = _pillow_font(backend, 20, bold=True)
    label_font = _pillow_font(backend, 16)
    value_font = _pillow_font(backend, 30, bold=True)
    draw.text(
        (40, 25),
        "Exact matched-case decision transitions",
        font=title_font,
        fill="#111827",
    )
    for index, comparison in enumerate(comparisons):
        panel_x = 40 + (index % columns) * panel_width
        panel_y = 90 + (index // columns) * panel_height
        transition = comparison["decision_transitions"]
        matrix = (
            (transition["both_wrong"], transition["after_only"]),
            (transition["before_only"], transition["both_correct"]),
        )
        maximum = max(1, *(value for row in matrix for value in row))
        _draw_centered(
            draw,
            (panel_x + 250, panel_y + 18),
            _short_label(f"{comparison['before']} -> {comparison['after']}", limit=38),
            font=panel_font,
        )
        _draw_centered(
            draw,
            (panel_x + 250, panel_y + 48),
            f"matched N={comparison['matched_case_count']}",
            font=label_font,
            fill="#4B5563",
        )
        grid_x, grid_y, cell = panel_x + 150, panel_y + 105, 125
        for row_index, row in enumerate(matrix):
            for column_index, value in enumerate(row):
                x0 = grid_x + column_index * cell
                y0 = grid_y + row_index * cell
                draw.rectangle(
                    (x0, y0, x0 + cell, y0 + cell),
                    fill=_heat_color(value, maximum),
                    outline="#374151",
                    width=2,
                )
                _draw_centered(
                    draw,
                    (x0 + cell / 2, y0 + cell / 2),
                    str(value),
                    font=value_font,
                    fill="#111827",
                )
        for column_index, text in enumerate(("After wrong", "After correct")):
            _draw_centered(
                draw,
                (grid_x + column_index * cell + cell / 2, grid_y - 20),
                text,
                font=label_font,
            )
        for row_index, text in enumerate(("Before wrong", "Before correct")):
            bounds = draw.textbbox((0, 0), text, font=label_font)
            draw.text(
                (
                    grid_x - (bounds[2] - bounds[0]) - 12,
                    grid_y + row_index * cell + cell / 2 - 9,
                ),
                text,
                font=label_font,
                fill="#1F2937",
            )
        _draw_centered(
            draw,
            (panel_x + 250, grid_y + 2 * cell + 30),
            "off-diagonal = McNemar discordance",
            font=label_font,
            fill="#4B5563",
        )
    _save_pillow_figure(image, figures_dir / "figure-02-paired-transitions")


def _render_pillow_figures(
    analysis: Mapping[str, Any], figures_dir: Path, backend: FigureBackend
) -> None:
    _render_pillow_outcome_rates(analysis, figures_dir, backend)
    if analysis["comparisons"]:
        _render_pillow_transitions(analysis, figures_dir, backend)


def render_figures(
    analysis: Mapping[str, Any],
    figures_dir: Path,
    *,
    backend: FigureBackend | None = None,
) -> list[dict[str, str]]:
    selected = backend or _load_figure_backend()
    figures_dir.mkdir(parents=True, exist_ok=True)
    _render_svg_stage_rates(analysis, figures_dir)
    _render_svg_gate_safety(analysis, figures_dir)
    if selected.name == "matplotlib":
        _render_matplotlib_figures(analysis, figures_dir, selected)
    elif selected.name == "pillow":
        _render_pillow_figures(analysis, figures_dir, selected)
    elif selected.name != "svg-stdlib":
        raise ValueError(f"unsupported figure backend: {selected.name}")
    return _figure_catalog(selected, comparisons=bool(analysis["comparisons"]))


def render_figure_catalog(catalog: Sequence[Mapping[str, str]]) -> str:
    lines = [
        "# Memory retrieval quality v3 — figure catalog",
        "",
        "Figures contain aggregate outcomes only; no query or memory text is embedded.",
        "",
    ]
    for item in catalog:
        lines.extend(
            [
                f"## {item['id']}",
                "",
                f"- Filename: `{item['filename']}`",
                f"- Purpose: {item['purpose']}",
                f"- Data source: {item['data_source']}",
                f"- Caption requirements: {item['caption_requirements']}",
                f"- Key observation: {item['key_observation']}",
                f"- Interpretation checklist: {item['interpretation_checklist']}",
                f"- Known caveats: {item['known_caveats']}",
                f"- Allowed Claim Candidate: {item['allowed_claim_candidate']}",
                f"- Forbidden Claim Candidate: {item['forbidden_claim_candidate']}",
                f"- Rendering backend: {item['backend']}",
                "",
            ]
        )
    return "\n".join(lines)


def write_bundle(analysis: Mapping[str, Any], output_dir: Path) -> tuple[Path, ...]:
    # Validate before creating a directory so missing authority leaves no polished bundle.
    require_final_bundle_authority(analysis)
    backend = _load_figure_backend()
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = render_figures(analysis, output_dir / "figures", backend=backend)
    outputs = (
        output_dir / "analysis-report.md",
        output_dir / "stats-appendix.md",
        output_dir / "figure-catalog.md",
    )
    outputs[0].write_text(render_analysis_report(analysis), encoding="utf-8")
    outputs[1].write_text(render_stats_appendix(analysis), encoding="utf-8")
    outputs[2].write_text(render_figure_catalog(catalog), encoding="utf-8")
    return outputs


def _optional_projection(
    path: Path | None, *, label: str, scope: str
) -> ReportProjection | None:
    if path is None:
        return None
    return project_report(path, label=label, kind="hybrid", scope=scope)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--development-pre", type=Path)
    parser.add_argument("--development-post", type=Path)
    parser.add_argument(
        "--resolver-pressure-report",
        type=Path,
        required=True,
        help="formal content-free pressure report; its release anchor must be current",
    )
    parser.add_argument(
        "--independent-report",
        type=Path,
        required=True,
        help="formal content-free promotion report; its production fingerprint must be allowlisted",
    )
    parser.add_argument(
        "--attempt-history",
        type=Path,
        help="optional closed-schema, content-free experiment-attempt provenance JSON",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    reports: list[ReportProjection] = [
        project_report(args.baseline, label="semantic-v2", kind="baseline")
    ]
    for projected in (
        _optional_projection(
            args.development_pre,
            label="development-pre",
            scope="synthetic development (spent)",
        ),
        _optional_projection(
            args.development_post,
            label="development-post",
            scope="synthetic development (spent)",
        ),
        _optional_projection(
            args.independent_report,
            label="independent-one-shot",
            scope="synthetic independent one-shot",
        ),
    ):
        if projected is not None:
            reports.append(projected)
    pressure = project_resolver_pressure_report(args.resolver_pressure_report)
    attempts = load_attempt_history(args.attempt_history)
    outputs = write_bundle(
        build_analysis(
            reports,
            resolver_pressure=pressure,
            attempt_history=attempts,
        ),
        args.output_dir,
    )
    print(json.dumps({"outputs": [str(path) for path in outputs]}, indent=2))


if __name__ == "__main__":
    main()
