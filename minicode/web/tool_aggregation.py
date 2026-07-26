"""Bounded read-only reconciliation for persisted Tool and failure events.

The module consumes safe RunJournal facts only.  It does not inspect the Tool
registry, execute a Tool, infer duration, or write observations.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal


_TOOL_OPERATION_ID_RE = re.compile(r"^toolop_[0-9a-f]{32}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_RUN_SOURCES = frozenset({"tui", "headless", "gateway", "unknown"})
_MAX_EVENTS = 1_000
_MAX_AGGREGATES = 100
_MAX_DIAGNOSTICS = 20
_MAX_TOOL_BREAKDOWN = 50

MetricStatus = Literal["complete", "partial", "unavailable"]


@dataclass(frozen=True, slots=True)
class _ToolObservation:
    tool_name: str | None
    started: bool
    completed: bool
    paired: bool
    outcome: Literal["success", "error"] | None
    run_source: str


@dataclass(slots=True)
class _ToolOperation:
    tool_name: str | None
    finish: tuple[str, Literal["success", "error"]] | None = None
    finish_seen: bool = False
    conflicting: bool = False


@dataclass(frozen=True, slots=True)
class ToolAggregate:
    """One immutable Tool result for a bounded Run or retained Run scope."""

    observations: tuple[_ToolObservation, ...]
    dangling_starts: int
    unpaired_finishes: int
    duplicate_events: int
    conflicting_operations: int
    orphan_finishes: int
    invalid_events: int
    limited: bool
    diagnostics: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class _FailureRunFacts:
    run_source: str
    observed: bool
    tool_errors: int
    model_failure_kinds: tuple[str, ...]
    run_failed: bool
    interrupted: bool
    cancelled: bool


@dataclass(frozen=True, slots=True)
class FailureAggregate:
    """Classified failure facts without an ambiguous cross-category total."""

    runs: tuple[_FailureRunFacts, ...]
    invalid_events: int
    duplicate_events: int
    conflicting_operations: int
    coverage_incomplete: bool
    limited: bool
    diagnostics: tuple[dict[str, str], ...]


def _event_parts(event: object) -> tuple[object, object]:
    if isinstance(event, Mapping):
        return event.get("type"), event.get("payload")
    return getattr(event, "type", None), getattr(event, "payload", None)


def _operation_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("operationId")
    if isinstance(value, str) and _TOOL_OPERATION_ID_RE.fullmatch(value):
        return value
    return None


def _tool_name(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("toolName")
    if isinstance(value, str) and _TOOL_NAME_RE.fullmatch(value):
        return value
    return None


def _tool_counts(aggregate: ToolAggregate) -> dict[str, int]:
    observations = aggregate.observations
    return {
        "observedCalls": len(observations),
        "startedCalls": sum(item.started for item in observations),
        "completedCalls": sum(item.completed for item in observations),
        "pairedCalls": sum(item.completed and item.paired for item in observations),
        "successfulCalls": sum(item.outcome == "success" for item in observations),
        "errorCalls": sum(item.outcome == "error" for item in observations),
        "uniqueTools": len(
            {item.tool_name for item in observations if item.tool_name is not None}
        ),
    }


def aggregate_run_tools(
    events: Iterable[object],
    *,
    run_source: str = "unknown",
    limited: bool = False,
    journal_read_failed: bool = False,
    max_events: int = _MAX_EVENTS,
) -> ToolAggregate:
    """Reconcile one Run's ordered Tool events without inferring callbacks."""
    source = run_source if run_source in _RUN_SOURCES else "unknown"
    operations: dict[str, _ToolOperation] = {}
    unpaired: list[_ToolObservation] = []
    duplicate_events = 0
    orphan_finishes = 0
    invalid_events = 0
    conflicting_ids: set[str] = set()
    diagnostics: list[dict[str, str]] = []

    def diagnostic(code: str, message: str) -> None:
        item = {"source": "tools", "code": code, "message": message}
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)

    if limited:
        diagnostic(
            "tool_scan_limited",
            "Tool observations were limited by the Dashboard scan scope.",
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
                "tool_scan_limited",
                "Tool observations reached the Dashboard event scan limit.",
            )
            break
        scanned += 1
        event_type, raw_payload = _event_parts(event)
        if event_type not in {"tool.started", "tool.finished"}:
            continue
        if not isinstance(raw_payload, Mapping):
            invalid_events += 1
            diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
            continue
        name = _tool_name(raw_payload)
        if event_type == "tool.started":
            if set(raw_payload) != {"toolName", "operationId"}:
                invalid_events += 1
                diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
                continue
            operation_id = _operation_id(raw_payload)
            if name is None or operation_id is None:
                invalid_events += 1
                diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
                continue
            operation = operations.get(operation_id)
            if operation is None:
                operations[operation_id] = _ToolOperation(tool_name=name)
            elif operation.tool_name == name and not operation.conflicting:
                duplicate_events += 1
                diagnostic(
                    "tool_operation_duplicate",
                    "A duplicate Tool event was counted at most once.",
                )
            else:
                operation.tool_name = None
                operation.conflicting = True
                conflicting_ids.add(operation_id)
                diagnostic(
                    "tool_operation_conflict",
                    "A conflicting Tool operation was excluded from trusted outcome counts.",
                )
            continue

        paired = raw_payload.get("paired")
        outcome = raw_payload.get("outcome")
        operation_id = _operation_id(raw_payload)
        if (
            name is None
            or not isinstance(paired, bool)
            or outcome not in {"success", "error"}
        ):
            invalid_events += 1
            diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
            continue
        if paired is False:
            if set(raw_payload) != {"toolName", "outcome", "paired"}:
                invalid_events += 1
                diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
                continue
            unpaired.append(
                _ToolObservation(
                    tool_name=name,
                    started=False,
                    completed=True,
                    paired=False,
                    outcome=outcome,
                    run_source=source,
                )
            )
            diagnostic(
                "tool_finish_unpaired",
                "A valid unpaired Tool finish has incomplete start coverage.",
            )
            continue
        if operation_id is None:
            invalid_events += 1
            diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
            continue
        if set(raw_payload) != {"toolName", "operationId", "outcome", "paired"}:
            invalid_events += 1
            diagnostic("tool_event_invalid", "A malformed Tool event was ignored.")
            continue
        operation = operations.get(operation_id)
        if operation is None:
            orphan_finishes += 1
            diagnostic(
                "tool_operation_orphan",
                "A paired Tool finish without a preceding start was ignored.",
            )
            continue
        operation.finish_seen = True
        finish = (name, outcome)
        if operation.conflicting or operation.tool_name != name:
            operation.conflicting = True
            conflicting_ids.add(operation_id)
            diagnostic(
                "tool_operation_conflict",
                "A conflicting Tool operation was excluded from trusted outcome counts.",
            )
            continue
        if operation.finish is None:
            operation.finish = finish
        elif operation.finish == finish:
            duplicate_events += 1
            diagnostic(
                "tool_operation_duplicate",
                "A duplicate Tool event was counted at most once.",
            )
        else:
            operation.finish = None
            operation.conflicting = True
            conflicting_ids.add(operation_id)
            diagnostic(
                "tool_operation_conflict",
                "A conflicting Tool operation was excluded from trusted outcome counts.",
            )

    observations: list[_ToolObservation] = []
    dangling_starts = 0
    for operation in operations.values():
        if not operation.finish_seen:
            dangling_starts += 1
            diagnostic(
                "tool_operation_dangling",
                "A Tool start has no observed finish in the bounded scan.",
            )
        trusted_finish = operation.finish if not operation.conflicting else None
        observations.append(
            _ToolObservation(
                tool_name=operation.tool_name,
                started=True,
                completed=trusted_finish is not None,
                paired=trusted_finish is not None,
                outcome=trusted_finish[1] if trusted_finish is not None else None,
                run_source=source,
            )
        )
    observations.extend(unpaired)
    if journal_read_failed:
        limited = True
        diagnostic(
            "tool_journal_read_failed",
            "Tool observations could not be read completely.",
        )
    if len({item.tool_name for item in observations if item.tool_name}) > _MAX_TOOL_BREAKDOWN:
        limited = True
        diagnostic(
            "tool_scan_limited",
            "Tool-name breakdown reached the Dashboard response limit.",
        )
    return ToolAggregate(
        observations=tuple(observations),
        dangling_starts=dangling_starts,
        unpaired_finishes=len(unpaired),
        duplicate_events=duplicate_events,
        conflicting_operations=len(conflicting_ids),
        orphan_finishes=orphan_finishes,
        invalid_events=invalid_events,
        limited=limited,
        diagnostics=tuple(diagnostics),
    )


def merge_tool_aggregates(
    items: Iterable[ToolAggregate],
    *,
    limited: bool = False,
    journal_read_failed: bool = False,
    max_aggregates: int = _MAX_AGGREGATES,
) -> ToolAggregate:
    """Merge bounded per-Run Tool facts without pairing across Runs."""
    aggregate_limit = (
        min(max_aggregates, _MAX_AGGREGATES)
        if isinstance(max_aggregates, int)
        and not isinstance(max_aggregates, bool)
        and max_aggregates > 0
        else _MAX_AGGREGATES
    )
    selected: list[ToolAggregate] = []
    diagnostics: list[dict[str, str]] = []
    for item in items:
        if len(selected) >= aggregate_limit:
            limited = True
            break
        if isinstance(item, ToolAggregate):
            selected.append(item)
        else:
            limited = True
            journal_read_failed = True
    for item in selected:
        for diagnostic in item.diagnostics:
            if len(diagnostics) < _MAX_DIAGNOSTICS and diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
    if limited:
        item = {
            "source": "tools",
            "code": "tool_scan_limited",
            "message": "Tool aggregation reached the Dashboard Run scan limit.",
        }
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)
    if journal_read_failed:
        item = {
            "source": "tools",
            "code": "tool_journal_read_failed",
            "message": "Retained Tool observations could not be read completely.",
        }
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)
    observations = tuple(
        observation for item in selected for observation in item.observations
    )
    if len({item.tool_name for item in observations if item.tool_name}) > _MAX_TOOL_BREAKDOWN:
        limited = True
        item = {
            "source": "tools",
            "code": "tool_scan_limited",
            "message": "Tool-name breakdown reached the Dashboard response limit.",
        }
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)
    return ToolAggregate(
        observations=observations,
        dangling_starts=sum(item.dangling_starts for item in selected),
        unpaired_finishes=sum(item.unpaired_finishes for item in selected),
        duplicate_events=sum(item.duplicate_events for item in selected),
        conflicting_operations=sum(item.conflicting_operations for item in selected),
        orphan_finishes=sum(item.orphan_finishes for item in selected),
        invalid_events=sum(item.invalid_events for item in selected),
        limited=limited or any(item.limited for item in selected),
        diagnostics=tuple(diagnostics),
    )


def aggregate_run_failures(
    events: Iterable[object],
    *,
    run_status: str = "unknown",
    run_source: str = "unknown",
    limited: bool = False,
    journal_read_failed: bool = False,
    max_events: int = _MAX_EVENTS,
) -> FailureAggregate:
    """Classify one Run's Tool, Model-attempt, and lifecycle failures."""
    source = run_source if run_source in _RUN_SOURCES else "unknown"
    event_limit = (
        min(max_events, _MAX_EVENTS)
        if isinstance(max_events, int)
        and not isinstance(max_events, bool)
        and max_events > 0
        else _MAX_EVENTS
    )
    bounded_events: list[object] = []
    for event in events:
        if len(bounded_events) >= event_limit:
            limited = True
            break
        bounded_events.append(event)
    tools = aggregate_run_tools(
        bounded_events,
        run_source=source,
        limited=limited,
        journal_read_failed=journal_read_failed,
        max_events=event_limit,
    )
    diagnostics: list[dict[str, str]] = []

    def diagnostic(code: str, message: str) -> None:
        item = {"source": "failures", "code": code, "message": message}
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)

    model_states: dict[str, tuple[str, str | None]] = {}
    invalid_events = tools.invalid_events + tools.orphan_finishes
    duplicate_events = tools.duplicate_events
    conflicting_operations = tools.conflicting_operations
    lifecycle_events: set[str] = set()
    terminal_events: set[str] = set()
    lifecycle_observed = False
    model_observed = False
    model_id_re = re.compile(r"^modelop_[0-9a-f]{32}$")
    failure_kinds = {"interrupted", "network", "timeout", "provider_error"}
    lifecycle_types = {
        "run.queued",
        "run.started",
        "run.completed",
        "run.failed",
        "run.interrupted",
        "run.cancel_requested",
        "run.cancelled",
    }
    terminal_types = {
        "run.completed",
        "run.failed",
        "run.interrupted",
        "run.cancelled",
    }
    for event in bounded_events:
        event_type, raw_payload = _event_parts(event)
        if event_type in lifecycle_types:
            lifecycle_observed = True
            if event_type in lifecycle_events:
                duplicate_events += 1
                diagnostic(
                    "failure_event_invalid",
                    "A duplicate lifecycle failure observation was counted at most once.",
                )
            lifecycle_events.add(str(event_type))
            if event_type in terminal_types:
                terminal_events.add(str(event_type))
            continue
        if event_type not in {"model.started", "model.completed", "model.failed"}:
            continue
        if not isinstance(raw_payload, Mapping):
            invalid_events += 1
            diagnostic(
                "failure_event_invalid", "A malformed Model failure event was ignored."
            )
            continue
        operation_id = raw_payload.get("operationId")
        if not isinstance(operation_id, str) or not model_id_re.fullmatch(operation_id):
            invalid_events += 1
            diagnostic(
                "failure_event_invalid", "A malformed Model failure event was ignored."
            )
            continue
        if event_type == "model.started":
            if set(raw_payload) != {"operationId"}:
                invalid_events += 1
                diagnostic(
                    "failure_event_invalid",
                    "A malformed Model failure event was ignored.",
                )
                continue
            model_observed = True
            if operation_id in model_states:
                duplicate_events += 1
                diagnostic(
                    "failure_event_invalid",
                    "A duplicate Model failure observation was counted at most once.",
                )
            else:
                model_states[operation_id] = ("started", None)
            continue
        state = model_states.get(operation_id)
        if state is None:
            invalid_events += 1
            diagnostic(
                "failure_event_invalid",
                "An unpaired Model failure observation was ignored.",
            )
            continue
        if event_type == "model.completed":
            if state[0] == "started":
                model_states[operation_id] = ("completed", None)
            elif state[0] == "completed":
                duplicate_events += 1
                diagnostic(
                    "failure_event_invalid",
                    "A duplicate Model failure observation was counted at most once.",
                )
            else:
                conflicting_operations += 1
                model_states[operation_id] = ("conflict", None)
                diagnostic(
                    "failure_event_invalid",
                    "Conflicting Model terminal observations were excluded.",
                )
            continue
        failure_kind = raw_payload.get("failureKind")
        duration_ms = raw_payload.get("durationMs")
        if (
            failure_kind not in failure_kinds
            or not set(raw_payload).issubset(
                {"operationId", "failureKind", "durationMs"}
            )
            or (
                "durationMs" in raw_payload
                and (
                    isinstance(duration_ms, bool)
                    or not isinstance(duration_ms, int)
                    or duration_ms < 0
                    or duration_ms > 86_400_000
                )
            )
        ):
            invalid_events += 1
            diagnostic(
                "failure_event_invalid", "A malformed Model failure event was ignored."
            )
            continue
        if state[0] == "started":
            model_states[operation_id] = ("failed", str(failure_kind))
        elif state == ("failed", failure_kind):
            duplicate_events += 1
            diagnostic(
                "failure_event_invalid",
                "A duplicate Model failure observation was counted at most once.",
            )
        else:
            conflicting_operations += 1
            model_states[operation_id] = ("conflict", None)
            diagnostic(
                "failure_event_invalid",
                "Conflicting Model terminal observations were excluded.",
            )

    valid_terminal: str | None = None
    if len(terminal_events) == 1:
        valid_terminal = next(iter(terminal_events))
    elif len(terminal_events) > 1:
        conflicting_operations += 1
        diagnostic(
            "failure_event_invalid",
            "Conflicting Run terminal observations were excluded.",
        )
    expected_lifecycle = {
        "queued": "run.queued",
        "running": "run.started",
        "completed": "run.completed",
        "failed": "run.failed",
        "interrupted": "run.interrupted",
        "cancel_requested": "run.cancel_requested",
        "cancelled": "run.cancelled",
    }.get(run_status)
    if expected_lifecycle is not None and lifecycle_observed:
        if expected_lifecycle not in lifecycle_events:
            invalid_events += 1
            diagnostic(
                "failure_event_invalid",
                "Run lifecycle metadata did not match the bounded event observations.",
            )
        if valid_terminal is not None and valid_terminal != expected_lifecycle:
            valid_terminal = None
    if limited:
        diagnostic(
            "failure_scan_limited",
            "Failure observations reached the Dashboard scan limit.",
        )
    if journal_read_failed:
        limited = True
        diagnostic(
            "failure_journal_read_failed",
            "Failure observations could not be read completely.",
        )
    tool_value = project_tool_metric(tools)["value"]
    tool_errors = (
        int(tool_value.get("errorCalls", 0)) if isinstance(tool_value, dict) else 0
    )
    tool_coverage_incomplete = (
        bool(tools.observations)
        and project_tool_metric(tools)["status"] == "partial"
    ) or any(
        (
            tools.dangling_starts,
            tools.unpaired_finishes,
            tools.duplicate_events,
            tools.conflicting_operations,
            tools.orphan_finishes,
            tools.invalid_events,
            tools.limited,
        )
    )
    observed = lifecycle_observed or model_observed or bool(tools.observations)
    model_failure_kinds = [
        str(state[1])
        for state in model_states.values()
        if state[0] == "failed" and state[1] is not None
    ]
    facts = _FailureRunFacts(
        run_source=source,
        observed=observed,
        tool_errors=tool_errors,
        model_failure_kinds=tuple(model_failure_kinds),
        run_failed=valid_terminal == "run.failed",
        interrupted=valid_terminal == "run.interrupted",
        cancelled=valid_terminal == "run.cancelled",
    )
    return FailureAggregate(
        runs=(facts,),
        invalid_events=invalid_events,
        duplicate_events=duplicate_events,
        conflicting_operations=conflicting_operations,
        coverage_incomplete=tool_coverage_incomplete,
        limited=limited,
        diagnostics=tuple(diagnostics),
    )


def merge_failure_aggregates(
    items: Iterable[FailureAggregate],
    *,
    limited: bool = False,
    journal_read_failed: bool = False,
    max_aggregates: int = _MAX_AGGREGATES,
) -> FailureAggregate:
    """Merge already classified per-Run facts; affected Runs stay deduplicated."""
    aggregate_limit = (
        min(max_aggregates, _MAX_AGGREGATES)
        if isinstance(max_aggregates, int)
        and not isinstance(max_aggregates, bool)
        and max_aggregates > 0
        else _MAX_AGGREGATES
    )
    selected: list[FailureAggregate] = []
    diagnostics: list[dict[str, str]] = []
    for item in items:
        if len(selected) >= aggregate_limit:
            limited = True
            break
        if isinstance(item, FailureAggregate):
            selected.append(item)
        else:
            limited = True
            journal_read_failed = True
    for item in selected:
        for diagnostic in item.diagnostics:
            if len(diagnostics) < _MAX_DIAGNOSTICS and diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
    if limited:
        item = {
            "source": "failures",
            "code": "failure_scan_limited",
            "message": "Failure aggregation reached the Dashboard Run scan limit.",
        }
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)
    if journal_read_failed:
        item = {
            "source": "failures",
            "code": "failure_journal_read_failed",
            "message": "Retained Failure observations could not be read completely.",
        }
        if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
            diagnostics.append(item)
    return FailureAggregate(
        runs=tuple(run for item in selected for run in item.runs),
        invalid_events=sum(item.invalid_events for item in selected),
        duplicate_events=sum(item.duplicate_events for item in selected),
        conflicting_operations=sum(
            item.conflicting_operations for item in selected
        ),
        coverage_incomplete=any(item.coverage_incomplete for item in selected),
        limited=(
            limited
            or journal_read_failed
            or any(item.limited for item in selected)
        ),
        diagnostics=tuple(diagnostics),
    )


def _failure_counts(aggregate: FailureAggregate) -> dict[str, int | bool]:
    tool_errors = sum(run.tool_errors for run in aggregate.runs)
    model_failures = sum(len(run.model_failure_kinds) for run in aggregate.runs)
    run_failures = sum(run.run_failed for run in aggregate.runs)
    affected_runs = sum(
        bool(run.tool_errors or run.model_failure_kinds or run.run_failed)
        for run in aggregate.runs
    )
    return {
        "affectedRuns": affected_runs,
        "toolErrors": tool_errors,
        "modelFailures": model_failures,
        "runFailures": run_failures,
        "interruptedRuns": sum(run.interrupted for run in aggregate.runs),
        "cancelledRuns": sum(run.cancelled for run in aggregate.runs),
        "hasObservedFailure": affected_runs > 0,
    }


def project_failure_metric(aggregate: FailureAggregate) -> dict[str, object]:
    """Project separate failure categories without a duplicated total."""
    observed_runs = sum(run.observed for run in aggregate.runs)
    coverage = {
        "observedRuns": observed_runs,
        "invalidEvents": aggregate.invalid_events,
        "duplicateEvents": aggregate.duplicate_events,
        "conflictingOperations": aggregate.conflicting_operations,
        "historical": "partial",
        "scope": "retained-run-journal",
        "limited": aggregate.limited,
    }
    if observed_runs == 0:
        return {"status": "unavailable", "value": None, "coverage": coverage}
    partial = (
        aggregate.invalid_events > 0
        or aggregate.duplicate_events > 0
        or aggregate.conflicting_operations > 0
        or aggregate.coverage_incomplete
        or aggregate.limited
    )
    return {
        "status": "partial" if partial else "complete",
        "value": _failure_counts(aggregate),
        "coverage": coverage,
    }


def project_run_failure_summary(aggregate: FailureAggregate) -> dict[str, object]:
    """Return one compact Runs-list Failure projection."""
    metric = project_failure_metric(aggregate)
    value = metric["value"] if isinstance(metric["value"], dict) else {}
    return {
        "status": metric["status"],
        "hasObservedFailure": value.get("hasObservedFailure"),
        "toolErrors": value.get("toolErrors"),
        "modelFailures": value.get("modelFailures"),
        "runFailed": bool(value.get("runFailures", 0)),
        "interrupted": bool(value.get("interruptedRuns", 0)),
        "cancelled": bool(value.get("cancelledRuns", 0)),
        "limited": aggregate.limited,
    }


def project_failure_breakdown(aggregate: FailureAggregate) -> dict[str, object]:
    """Return fixed failure categories, Model kinds, and Run-source facts."""
    counts = _failure_counts(aggregate)
    kind_counts: dict[str, int] = {}
    for run in aggregate.runs:
        for kind in run.model_failure_kinds:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
    sources: list[dict[str, object]] = []
    for source in sorted(_RUN_SOURCES):
        runs = [run for run in aggregate.runs if run.run_source == source]
        if not runs:
            continue
        sources.append(
            {
                "source": source,
                "observedRuns": sum(run.observed for run in runs),
                "affectedRuns": sum(
                    bool(run.tool_errors or run.model_failure_kinds or run.run_failed)
                    for run in runs
                ),
                "toolErrors": sum(run.tool_errors for run in runs),
                "modelFailures": sum(len(run.model_failure_kinds) for run in runs),
                "runFailures": sum(run.run_failed for run in runs),
                "interruptedRuns": sum(run.interrupted for run in runs),
                "cancelledRuns": sum(run.cancelled for run in runs),
            }
        )
    sources.sort(
        key=lambda item: (-int(item["observedRuns"]), str(item["source"]))
    )
    return {
        "categories": [
            {"category": "tool_errors", "count": counts["toolErrors"]},
            {"category": "model_failures", "count": counts["modelFailures"]},
            {"category": "run_failures", "count": counts["runFailures"]},
            {"category": "interruptions", "count": counts["interruptedRuns"]},
            {"category": "cancellations", "count": counts["cancelledRuns"]},
        ],
        "modelFailureKinds": [
            {"failureKind": kind, "attempts": count}
            for kind, count in sorted(
                kind_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "sources": sources,
    }


def project_tool_metric(aggregate: ToolAggregate) -> dict[str, object]:
    """Project the unified Tool metric without claiming unobserved zero calls."""
    coverage = {
        "danglingStarts": aggregate.dangling_starts,
        "unpairedFinishes": aggregate.unpaired_finishes,
        "duplicateEvents": aggregate.duplicate_events,
        "conflictingOperations": aggregate.conflicting_operations,
        "orphanFinishes": aggregate.orphan_finishes,
        "invalidEvents": aggregate.invalid_events,
        "historical": "partial",
        "scope": "retained-run-journal",
        "limited": aggregate.limited,
    }
    if not aggregate.observations:
        return {"status": "unavailable", "value": None, "coverage": coverage}
    partial = (
        aggregate.dangling_starts > 0
        or aggregate.unpaired_finishes > 0
        or aggregate.duplicate_events > 0
        or aggregate.conflicting_operations > 0
        or aggregate.orphan_finishes > 0
        or aggregate.invalid_events > 0
        or aggregate.limited
    )
    return {
        "status": "partial" if partial else "complete",
        "value": _tool_counts(aggregate),
        "coverage": coverage,
    }


def project_run_tool_summary(aggregate: ToolAggregate) -> dict[str, object]:
    """Return the compact Runs-list projection of the unified Tool metric."""
    metric = project_tool_metric(aggregate)
    value = metric["value"] if isinstance(metric["value"], dict) else {}
    return {
        "status": metric["status"],
        "observedCalls": value.get("observedCalls"),
        "errorCalls": value.get("errorCalls"),
        "uniqueTools": value.get("uniqueTools"),
        "limited": aggregate.limited,
    }


def _tool_breakdown_rows(aggregate: ToolAggregate) -> list[dict[str, object]]:
    grouped: dict[str, list[_ToolObservation]] = {}
    for observation in aggregate.observations:
        if observation.tool_name is not None:
            grouped.setdefault(observation.tool_name, []).append(observation)
    rows = []
    for tool_name, observations in grouped.items():
        rows.append(
            {
                "toolName": tool_name,
                "observedCalls": len(observations),
                "completedCalls": sum(item.completed for item in observations),
                "successfulCalls": sum(
                    item.outcome == "success" for item in observations
                ),
                "errorCalls": sum(item.outcome == "error" for item in observations),
                "incompleteCalls": sum(
                    item.started and not item.completed for item in observations
                ),
            }
        )
    return sorted(
        rows, key=lambda item: (-int(item["observedCalls"]), str(item["toolName"]))
    )[:_MAX_TOOL_BREAKDOWN]


def project_tool_breakdown(aggregate: ToolAggregate) -> dict[str, object]:
    """Return bounded Tool-name, outcome, and Run-source observations."""
    counts = _tool_counts(aggregate)
    incomplete = sum(
        item.started and not item.completed for item in aggregate.observations
    )
    unpaired = sum(
        item.completed and not item.paired for item in aggregate.observations
    )
    sources: list[dict[str, object]] = []
    for source in sorted(_RUN_SOURCES):
        observations = [
            item for item in aggregate.observations if item.run_source == source
        ]
        if not observations:
            continue
        sources.append(
            {
                "source": source,
                "observedCalls": len(observations),
                "completedCalls": sum(item.completed for item in observations),
                "successfulCalls": sum(
                    item.outcome == "success" for item in observations
                ),
                "errorCalls": sum(item.outcome == "error" for item in observations),
                "incompleteCalls": sum(
                    item.started and not item.completed for item in observations
                ),
            }
        )
    sources.sort(key=lambda item: (-int(item["observedCalls"]), str(item["source"])))
    return {
        "tools": _tool_breakdown_rows(aggregate),
        "outcomes": [
            {"outcome": "success", "calls": counts["successfulCalls"]},
            {"outcome": "error", "calls": counts["errorCalls"]},
            {"outcome": "incomplete", "calls": incomplete},
            {"outcome": "unpaired", "calls": unpaired},
        ],
        "sources": sources,
    }


__all__ = [
    "FailureAggregate",
    "ToolAggregate",
    "aggregate_run_failures",
    "aggregate_run_tools",
    "merge_failure_aggregates",
    "merge_tool_aggregates",
    "project_failure_breakdown",
    "project_failure_metric",
    "project_run_failure_summary",
    "project_run_tool_summary",
    "project_tool_breakdown",
    "project_tool_metric",
]
