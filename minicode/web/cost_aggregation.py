"""Bounded read-only reconciliation for persisted canonical Cost events.

This module consumes historical RunJournal facts.  It deliberately does not
import the Pricing Catalog, model adapters, or legacy CostTracker: a persisted
``model.costed.amountNanoUsd`` is the monetary observation for that call.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal


_MODEL_OPERATION_ID_RE = re.compile(r"^modelop_[0-9a-f]{32}$")
_CATALOG_ID_RE = re.compile(
    r"^minicode-pricing-[a-z0-9][a-z0-9._-]{0,63}$"
)
_MODEL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheCreationTokens",
)
_COMPONENT_FIELDS = (
    "inputNanoUsd",
    "outputNanoUsd",
    "cacheReadNanoUsd",
    "cacheCreationNanoUsd",
)
_QUALITY_BY_USAGE = {
    "provider": "provider_usage_catalog_rate",
    "estimated": "estimated_usage_catalog_rate",
}
_UNAVAILABLE_REASONS = frozenset(
    {
        "usage_unavailable",
        "model_unpriced",
        "pricing_incomplete",
        "token_semantics_unsupported",
        "invalid_usage",
        "pricing_failed",
    }
)
_RUN_SOURCES = frozenset({"tui", "headless", "gateway", "unknown"})
_MAX_TOKEN_COUNT = 1_000_000_000
_MAX_COMPONENT_NANO_USD = 1_000_000_000_000_000_000
_MAX_EVENTS = 1_000
_MAX_DIAGNOSTICS = 20
_MAX_RUN_AMOUNT_NANO_USD = _MAX_EVENTS * _MAX_COMPONENT_NANO_USD
_MAX_AGGREGATES = 100
_MAX_AGGREGATE_AMOUNT_NANO_USD = (
    _MAX_AGGREGATES * _MAX_RUN_AMOUNT_NANO_USD
)
_MAX_CATALOG_BREAKDOWN = 20
_MAX_MODEL_BREAKDOWN = 20

CostStatus = Literal["complete", "partial", "unavailable"]


@dataclass(frozen=True, slots=True)
class _CostObservation:
    status: Literal["priced", "unavailable"]
    quality: str
    catalog_id: str
    catalog_model_key: str | None = None
    amount_nano_usd: int | None = None
    components: tuple[int, int, int, int] | None = None
    reason: str | None = None
    run_source: str = "unknown"


@dataclass(slots=True)
class _Operation:
    phase: Literal["started", "completed", "failed"] = "started"
    usage_source: str | None = None
    costs: list[_CostObservation] = field(default_factory=list)
    duplicate: bool = False
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class CostAggregate:
    """One immutable reconciled Cost result for a bounded observation scope."""

    completed_calls: int
    failed_attempts: int
    priced: tuple[_CostObservation, ...]
    unavailable_reasons: tuple[str, ...]
    missing_calls: int
    invalid_events: int
    duplicate_events: int
    conflict_events: int
    orphan_events: int
    limited: bool
    run_source: str
    diagnostics: tuple[dict[str, str], ...]


def _event_parts(event: object) -> tuple[object, object]:
    if isinstance(event, Mapping):
        return event.get("type"), event.get("payload")
    return getattr(event, "type", None), getattr(event, "payload", None)


def _operation_id(payload: object) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("operationId")
    if isinstance(value, str) and _MODEL_OPERATION_ID_RE.fullmatch(value):
        return value
    return None


def _usage_source(payload: Mapping[str, Any]) -> str | None:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping) or set(usage) != {"source", *_USAGE_FIELDS}:
        return None
    source = usage.get("source")
    if source not in {"provider", "estimated", "unavailable"}:
        return None
    values = [usage.get(field) for field in _USAGE_FIELDS]
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > _MAX_TOKEN_COUNT
        )
        for value in values
    ):
        return None
    if source == "unavailable":
        return source if all(value is None for value in values) else None
    if values[0] is None or values[1] is None:
        return None
    return source


def _nano_usd(value: object) -> int | None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_COMPONENT_NANO_USD
    ):
        return value
    return None


def _parse_cost(payload: Mapping[str, Any]) -> _CostObservation | None:
    version = payload.get("costVersion")
    catalog_id = payload.get("catalogId")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != 1
        or payload.get("currency") != "USD"
        or not isinstance(catalog_id, str)
        or not _CATALOG_ID_RE.fullmatch(catalog_id)
    ):
        return None
    status = payload.get("status")
    quality = payload.get("quality")
    if status == "unavailable":
        reason = payload.get("reason")
        if quality != "unavailable" or reason not in _UNAVAILABLE_REASONS:
            return None
        return _CostObservation(
            status="unavailable",
            quality="unavailable",
            catalog_id=catalog_id,
            reason=reason,
        )
    if status != "priced" or quality not in set(_QUALITY_BY_USAGE.values()):
        return None
    model_key = payload.get("catalogModelKey")
    amount = _nano_usd(payload.get("amountNanoUsd"))
    raw_components = payload.get("components")
    if (
        not isinstance(model_key, str)
        or not _MODEL_KEY_RE.fullmatch(model_key)
        or amount is None
        or not isinstance(raw_components, Mapping)
    ):
        return None
    components: list[int] = []
    for field_name in _COMPONENT_FIELDS:
        value = _nano_usd(raw_components.get(field_name))
        if value is None:
            return None
        components.append(value)
    if sum(components) != amount:
        return None
    return _CostObservation(
        status="priced",
        quality=quality,
        catalog_id=catalog_id,
        catalog_model_key=model_key,
        amount_nano_usd=amount,
        components=tuple(components),  # type: ignore[arg-type]
    )


def project_cost_event_detail(payload: Mapping[str, Any]) -> dict[str, object]:
    """Whitelist one timeline Cost payload through the aggregation validator."""
    operation_id = _operation_id(payload)
    version = payload.get("costVersion")
    catalog_id = payload.get("catalogId")
    if (
        operation_id is None
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != 1
        or payload.get("currency") != "USD"
        or not isinstance(catalog_id, str)
        or not _CATALOG_ID_RE.fullmatch(catalog_id)
    ):
        return {}
    base: dict[str, object] = {
        "costVersion": 1,
        "operationId": operation_id,
        "currency": "USD",
        "catalogId": catalog_id,
    }
    observation = _parse_cost(payload)
    if observation is None:
        return {
            **base,
            "status": "unavailable",
            "quality": "unavailable",
            "reason": "pricing_failed",
        }
    if observation.status == "unavailable":
        return {
            **base,
            "status": "unavailable",
            "quality": "unavailable",
            "reason": observation.reason or "pricing_failed",
        }
    return {
        **base,
        "status": "priced",
        "quality": observation.quality,
        "catalogModelKey": observation.catalog_model_key,
        "amountNanoUsd": observation.amount_nano_usd,
        "components": {
            field_name: (observation.components or (0, 0, 0, 0))[index]
            for index, field_name in enumerate(_COMPONENT_FIELDS)
        },
    }


def aggregate_run_cost(
    events: Iterable[object],
    *,
    run_source: str = "unknown",
    limited: bool = False,
    journal_read_failed: bool = False,
    max_events: int = _MAX_EVENTS,
) -> CostAggregate:
    """Reconcile one Run's bounded ordered Model events without repricing."""
    source = run_source if run_source in _RUN_SOURCES else "unknown"
    operations: dict[str, _Operation] = {}
    invalid_events = 0
    duplicate_events = 0
    conflict_events = 0
    orphan_events = 0
    diagnostics: list[dict[str, str]] = []

    def diagnostic(code: str, message: str) -> None:
        item = {"source": "cost", "code": code, "message": message}
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)

    if limited:
        diagnostic(
            "cost_scan_limited",
            "Cost observations were limited by the Dashboard scan scope.",
        )

    event_limit = (
        min(max_events, _MAX_EVENTS)
        if isinstance(max_events, int)
        and not isinstance(max_events, bool)
        and max_events > 0
        else _MAX_EVENTS
    )
    scanned = 0
    for event in events:
        if scanned >= event_limit:
            limited = True
            diagnostic(
                "cost_scan_limited",
                "Cost observations reached the Dashboard event scan limit.",
            )
            break
        scanned += 1
        event_type, raw_payload = _event_parts(event)
        if event_type not in {
            "model.started",
            "model.completed",
            "model.failed",
            "model.costed",
        }:
            continue
        operation_id = _operation_id(raw_payload)
        if operation_id is None or not isinstance(raw_payload, Mapping):
            invalid_events += 1
            diagnostic(
                "cost_event_invalid",
                "A malformed Cost-related Model event was ignored.",
            )
            continue
        if event_type == "model.started":
            if operation_id in operations:
                invalid_events += 1
                duplicate_events += 1
                diagnostic(
                    "cost_operation_duplicate",
                    "A duplicate Cost-related Model operation was ignored.",
                )
            else:
                operations[operation_id] = _Operation()
            continue
        operation = operations.get(operation_id)
        if operation is None:
            invalid_events += 1
            orphan_events += 1
            diagnostic(
                "cost_operation_unpaired",
                "An unpaired Cost-related Model event was ignored.",
            )
            continue
        if event_type == "model.completed":
            if operation.phase != "started":
                invalid_events += 1
                duplicate_events += 1
                diagnostic(
                    "cost_operation_duplicate",
                    "A duplicate Cost-related Model operation was ignored.",
                )
                continue
            operation.phase = "completed"
            operation.usage_source = _usage_source(raw_payload)
            if operation.usage_source is None:
                invalid_events += 1
                diagnostic(
                    "cost_quality_mismatch",
                    "A Cost observation had no trustworthy canonical usage source.",
                )
            continue
        if event_type == "model.failed":
            if operation.phase != "started":
                invalid_events += 1
                duplicate_events += 1
                diagnostic(
                    "cost_operation_duplicate",
                    "A duplicate Cost-related Model operation was ignored.",
                )
                continue
            operation.phase = "failed"
            continue

        if operation.phase != "completed":
            invalid_events += 1
            orphan_events += 1
            diagnostic(
                "cost_operation_unpaired",
                "A Cost event without a preceding completed operation was ignored.",
            )
            continue
        parsed = _parse_cost(raw_payload)
        if parsed is None:
            invalid_events += 1
            diagnostic(
                "cost_event_invalid",
                "An invalid Cost event was ignored.",
            )
            continue
        if operation.costs:
            invalid_events += 1
            if parsed == operation.costs[0] and not operation.conflict:
                duplicate_events += 1
                operation.duplicate = True
                diagnostic(
                    "cost_operation_duplicate",
                    "A duplicate Cost event was counted at most once.",
                )
            else:
                conflict_events += 1
                operation.conflict = True
                diagnostic(
                    "cost_operation_conflict",
                    "Conflicting Cost events were excluded from observed amount.",
                )
            continue
        operation.costs.append(parsed)

    if journal_read_failed:
        limited = True
        diagnostic(
            "cost_journal_read_failed",
            "Cost observations could not be read completely.",
        )

    completed_calls = 0
    failed_attempts = 0
    priced: list[_CostObservation] = []
    unavailable_reasons: list[str] = []
    missing_calls = 0
    priced_total = 0
    for operation in operations.values():
        if operation.phase == "failed":
            failed_attempts += 1
            continue
        if operation.phase != "completed":
            invalid_events += 1
            orphan_events += 1
            diagnostic(
                "cost_operation_unpaired",
                "An unterminated Model operation was excluded from Cost coverage.",
            )
            continue
        completed_calls += 1
        if not operation.costs:
            missing_calls += 1
            diagnostic(
                "cost_operation_missing",
                "A completed Model operation has no valid Cost observation.",
            )
            continue
        if operation.conflict:
            continue
        cost = operation.costs[0]
        if cost.status == "unavailable":
            unavailable_reasons.append(cost.reason or "pricing_failed")
            continue
        expected_quality = _QUALITY_BY_USAGE.get(operation.usage_source or "")
        if expected_quality != cost.quality:
            invalid_events += 1
            diagnostic(
                "cost_quality_mismatch",
                "Cost quality did not match canonical usage provenance.",
            )
            continue
        next_total = priced_total + (cost.amount_nano_usd or 0)
        if next_total > _MAX_RUN_AMOUNT_NANO_USD:
            invalid_events += 1
            diagnostic(
                "cost_event_invalid",
                "A Cost total exceeded the bounded aggregation range.",
            )
            continue
        priced_total = next_total
        priced.append(replace(cost, run_source=source))

    if failed_attempts:
        diagnostic(
            "failed_attempt_unpriced",
            "Failed Model attempts are not assigned a Cost observation.",
        )
    if (
        len({item.catalog_id for item in priced}) > _MAX_CATALOG_BREAKDOWN
        or len({item.catalog_model_key for item in priced}) > _MAX_MODEL_BREAKDOWN
    ):
        limited = True
        diagnostic(
            "cost_scan_limited",
            "Cost breakdown reached the Dashboard response limit.",
        )
    return CostAggregate(
        completed_calls=completed_calls,
        failed_attempts=failed_attempts,
        priced=tuple(priced),
        unavailable_reasons=tuple(unavailable_reasons),
        missing_calls=missing_calls,
        invalid_events=invalid_events,
        duplicate_events=duplicate_events,
        conflict_events=conflict_events,
        orphan_events=orphan_events,
        limited=limited,
        run_source=source,
        diagnostics=tuple(diagnostics),
    )


def merge_cost_aggregates(
    items: Iterable[CostAggregate],
    *,
    limited: bool = False,
    journal_read_failed: bool = False,
    max_aggregates: int = _MAX_AGGREGATES,
) -> CostAggregate:
    """Merge bounded per-Run results without pairing operations across Runs."""
    aggregate_limit = (
        min(max_aggregates, _MAX_AGGREGATES)
        if isinstance(max_aggregates, int)
        and not isinstance(max_aggregates, bool)
        and max_aggregates > 0
        else _MAX_AGGREGATES
    )
    selected: list[CostAggregate] = []
    diagnostics: list[dict[str, str]] = []

    def diagnostic(code: str, message: str) -> None:
        item = {"source": "cost", "code": code, "message": message}
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)

    if limited:
        diagnostic(
            "cost_scan_limited",
            "Cost aggregation was limited to retained Dashboard Runs.",
        )

    for item in items:
        if len(selected) >= aggregate_limit:
            limited = True
            diagnostic(
                "cost_scan_limited",
                "Cost aggregation reached the Dashboard Run scan limit.",
            )
            break
        if not isinstance(item, CostAggregate):
            limited = True
            diagnostic(
                "cost_journal_read_failed",
                "One Run's Cost observations could not be merged.",
            )
            continue
        selected.append(item)
        for item_diagnostic in item.diagnostics:
            if (
                len(diagnostics) < _MAX_DIAGNOSTICS
                and item_diagnostic not in diagnostics
            ):
                diagnostics.append(item_diagnostic)
    priced: list[_CostObservation] = []
    total = 0
    overflowed = False
    for item in selected:
        for observation in item.priced:
            next_total = total + (observation.amount_nano_usd or 0)
            if next_total > _MAX_AGGREGATE_AMOUNT_NANO_USD:
                overflowed = True
                break
            total = next_total
            priced.append(observation)
        if overflowed:
            break
    if overflowed:
        priced = []
        limited = True
        diagnostic(
            "cost_event_invalid",
            "The retained Cost total exceeded the bounded aggregation range.",
        )
    if journal_read_failed:
        limited = True
        diagnostic(
            "cost_journal_read_failed",
            "Retained Cost observations could not be read completely.",
        )
    if (
        len({item.catalog_id for item in priced}) > _MAX_CATALOG_BREAKDOWN
        or len({item.catalog_model_key for item in priced}) > _MAX_MODEL_BREAKDOWN
    ):
        limited = True
        diagnostic(
            "cost_scan_limited",
            "Cost breakdown reached the Dashboard response limit.",
        )
    return CostAggregate(
        completed_calls=sum(item.completed_calls for item in selected),
        failed_attempts=sum(item.failed_attempts for item in selected),
        priced=tuple(priced),
        unavailable_reasons=tuple(
            reason for item in selected for reason in item.unavailable_reasons
        ),
        missing_calls=sum(item.missing_calls for item in selected),
        invalid_events=(
            sum(item.invalid_events for item in selected) + int(overflowed)
        ),
        duplicate_events=sum(item.duplicate_events for item in selected),
        conflict_events=sum(item.conflict_events for item in selected),
        orphan_events=sum(item.orphan_events for item in selected),
        limited=limited or any(item.limited for item in selected),
        run_source="unknown",
        diagnostics=tuple(diagnostics),
    )


def _money_breakdown(
    observations: tuple[_CostObservation, ...],
    key_name: str,
    key: Any,
    *,
    limit: int,
) -> list[dict[str, object]]:
    grouped: dict[str, tuple[int, int]] = {}
    for observation in observations:
        raw_group = key(observation)
        if not isinstance(raw_group, str):
            continue
        calls, amount = grouped.get(raw_group, (0, 0))
        grouped[raw_group] = (
            calls + 1,
            amount + (observation.amount_nano_usd or 0),
        )
    ordered = sorted(grouped.items(), key=lambda item: (-item[1][1], item[0]))
    return [
        {
            key_name: group,
            "pricedCalls": calls,
            "amountNanoUsd": str(amount),
        }
        for group, (calls, amount) in ordered[:limit]
    ]


def project_cost_breakdown(aggregate: CostAggregate) -> dict[str, object]:
    """Return bounded, stably sorted safe Cost dimensions for Ops."""
    reason_counts: dict[str, int] = {}
    for reason in aggregate.unavailable_reasons:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if aggregate.missing_calls:
        reason_counts["missing_cost_event"] = aggregate.missing_calls
    if aggregate.failed_attempts:
        reason_counts["failed_attempt_unpriced"] = aggregate.failed_attempts
    return {
        "quality": _money_breakdown(
            aggregate.priced,
            "quality",
            lambda item: item.quality,
            limit=2,
        ),
        "catalogs": _money_breakdown(
            aggregate.priced,
            "catalogId",
            lambda item: item.catalog_id,
            limit=_MAX_CATALOG_BREAKDOWN,
        ),
        "models": _money_breakdown(
            aggregate.priced,
            "catalogModelKey",
            lambda item: item.catalog_model_key,
            limit=_MAX_MODEL_BREAKDOWN,
        ),
        "sources": _money_breakdown(
            aggregate.priced,
            "source",
            lambda item: item.run_source,
            limit=len(_RUN_SOURCES),
        ),
        "unavailableReasons": [
            {"reason": reason, "calls": calls}
            for reason, calls in sorted(
                reason_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


def project_cost_metric(aggregate: CostAggregate) -> dict[str, object]:
    """Project exact integer facts as JavaScript-safe decimal strings."""
    priced_calls = len(aggregate.priced)
    coverage = {
        "completedCalls": aggregate.completed_calls,
        "pricedCalls": priced_calls,
        "unavailableCalls": len(aggregate.unavailable_reasons),
        "missingCalls": aggregate.missing_calls,
        "failedAttempts": aggregate.failed_attempts,
        "invalidEvents": aggregate.invalid_events,
        "duplicateEvents": aggregate.duplicate_events,
        "conflictEvents": aggregate.conflict_events,
        "orphanEvents": aggregate.orphan_events,
        "historical": "partial",
        "scope": "retained-run-journal",
        "limited": aggregate.limited,
    }
    if not priced_calls:
        return {"status": "unavailable", "value": None, "coverage": coverage}
    amount = sum(item.amount_nano_usd or 0 for item in aggregate.priced)
    provider_amount = sum(
        item.amount_nano_usd or 0
        for item in aggregate.priced
        if item.quality == "provider_usage_catalog_rate"
    )
    estimated_amount = amount - provider_amount
    component_totals = [
        sum((item.components or (0, 0, 0, 0))[index] for item in aggregate.priced)
        for index in range(len(_COMPONENT_FIELDS))
    ]
    qualities = {item.quality for item in aggregate.priced}
    quality = (
        "mixed"
        if len(qualities) > 1
        else "provider"
        if qualities == {"provider_usage_catalog_rate"}
        else "estimated"
    )
    complete = (
        aggregate.completed_calls > 0
        and priced_calls == aggregate.completed_calls
        and not aggregate.unavailable_reasons
        and aggregate.missing_calls == 0
        and aggregate.failed_attempts == 0
        and aggregate.invalid_events == 0
        and not aggregate.limited
    )
    return {
        "status": "complete" if complete else "partial",
        "value": {
            "currency": "USD",
            "amountNanoUsd": str(amount),
            "providerUsageNanoUsd": str(provider_amount),
            "estimatedUsageNanoUsd": str(estimated_amount),
            "components": {
                field_name: str(component_totals[index])
                for index, field_name in enumerate(_COMPONENT_FIELDS)
            },
            "pricedCalls": priced_calls,
            "quality": quality,
            "catalogIds": sorted(
                {item.catalog_id for item in aggregate.priced}
            ),
        },
        "coverage": coverage,
    }


def project_run_cost_summary(aggregate: CostAggregate) -> dict[str, object]:
    """Return the compact Runs-list view of the unified Cost metric."""
    metric = project_cost_metric(aggregate)
    value = metric["value"] if isinstance(metric["value"], dict) else {}
    priced_calls = len(aggregate.priced)
    return {
        "status": metric["status"],
        "amountNanoUsd": value.get("amountNanoUsd"),
        "currency": "USD",
        "pricedCalls": priced_calls,
        "unpricedCalls": max(aggregate.completed_calls - priced_calls, 0),
        "failedAttempts": aggregate.failed_attempts,
        "limited": aggregate.limited,
    }


__all__ = [
    "CostAggregate",
    "aggregate_run_cost",
    "merge_cost_aggregates",
    "project_cost_breakdown",
    "project_cost_event_detail",
    "project_cost_metric",
    "project_run_cost_summary",
]
