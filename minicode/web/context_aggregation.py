"""Bounded read-only Context, Recovery, and WorkingMemory aggregation.

This module consumes persisted RunJournal facts only.  It deliberately does not
import or instantiate ContextManager, ContextCompactor, or WorkingMemoryTracker.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal


_CONTEXT_OPERATION_ID_RE = re.compile(r"^ctxop_[0-9a-f]{32}$")
_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{32}$")
_CONTEXT_PATHS = frozenset(
    {
        "pre_request_cybernetic",
        "pre_request_compactor",
        "in_loop_compactor",
        "context_manager_auto",
        "reactive_cybernetic",
        "reactive_compactor",
        "predictive_recovery",
        "feedback_forced",
    }
)
_DIRECT_CONTEXT_PATHS = frozenset(
    {
        "pre_request_cybernetic",
        "pre_request_compactor",
        "in_loop_compactor",
        "context_manager_auto",
        "predictive_recovery",
        "feedback_forced",
    }
)
_REACTIVE_CONTEXT_PATHS = frozenset({"reactive_cybernetic", "reactive_compactor"})
_PATH_FOR_KIND = {"cybernetic": "reactive_cybernetic", "compactor": "reactive_compactor"}
_CONTEXT_TRIGGERS = frozenset(
    {"manual", "auto", "reactive", "microcompact_time", "microcompact_cached"}
)
_CONTEXT_STRATEGIES = frozenset(
    {
        "session_memory",
        "full",
        "partial",
        "microcompact",
        "tool_budget",
        "read_dedup",
        "reactive",
        "context_manager",
    }
)
_RECOVERY_KINDS = frozenset({"cybernetic", "compactor"})
_CONTEXT_FAILURE_REASONS = frozenset(
    {
        "too_few_messages",
        "no_summarizable_messages",
        "no_token_reduction",
        "strategy_ineffective",
        "unchanged_state",
        "circuit_open",
        "internal_error",
    }
)
_RECOVERY_OUTCOMES = frozenset({"recovered", "not_recovered"})
_RUN_SOURCES = frozenset({"tui", "headless", "gateway", "unknown"})
_MAX_CONTEXT_MESSAGES = 100_000
_MAX_CONTEXT_TOKENS = 1_000_000_000
_MAX_MEMORY_COUNT = 100_000
_MAX_MEMORY_TOKENS = 10_000_000
_MAX_EVENTS = 1_000
_MAX_AGGREGATES = 100
_MAX_DIAGNOSTICS = 20
_MAX_BREAKDOWN = 20
_MAX_RETAINED_MESSAGES_REMOVED = _MAX_EVENTS * _MAX_AGGREGATES * _MAX_CONTEXT_MESSAGES
_MAX_RETAINED_TOKENS_FREED = _MAX_EVENTS * _MAX_AGGREGATES * _MAX_CONTEXT_TOKENS

ContextStatus = Literal["partial", "unavailable"]
IntegrityStatus = Literal["complete", "partial"]


@dataclass(frozen=True, slots=True)
class _CompactionEvent:
    operation_id: str
    sequence: int
    timestamp: str | None
    path: str
    trigger: str
    strategy: str
    messages_before: int
    messages_after: int
    messages_removed: int
    tokens_freed: int | None
    run_source: str


@dataclass(frozen=True, slots=True)
class _RecoveryStartedEvent:
    operation_id: str
    sequence: int
    kind: str


@dataclass(frozen=True, slots=True)
class _RecoveryCompletedEvent:
    operation_id: str
    sequence: int
    kind: str
    outcome: str
    messages_before: int
    messages_after: int
    tokens_freed: int | None


@dataclass(frozen=True, slots=True)
class _WorkingMemoryObservation:
    sequence: int
    timestamp: str | None
    run_id: str | None
    run_source: str
    scope: str
    entries: int
    max_entries: int
    protected_tokens: int
    max_tokens: int


@dataclass(slots=True)
class _Operation:
    started: _RecoveryStartedEvent | None = None
    completed: _RecoveryCompletedEvent | None = None
    compacted: _CompactionEvent | None = None
    duplicate: bool = False
    conflict: bool = False



@dataclass(frozen=True, slots=True)
class _OperationReconciliation:
    classification: Literal[
        "direct_compaction",
        "recovered",
        "not_recovered",
        "dangling",
        "orphan_completion",
        "orphan_compaction",
        "conflict",
        "empty",
    ]
    trusted_compaction: _CompactionEvent | None = None
    recovery_attempt: bool = False
    recovery_completed: bool = False
    recovery_outcome: Literal["recovered", "not_recovered"] | None = None
    duplicate_events: int = 0
    conflicting_operation: bool = False
    orphan_completion: bool = False
    orphan_compaction: bool = False
    dangling_recovery: bool = False

@dataclass(frozen=True, slots=True)
class ContextAggregate:
    compactions: tuple[_CompactionEvent, ...]
    recovery_attempts: int
    completed_recoveries: int
    recovered_recoveries: int
    not_recovered_recoveries: int
    dangling_recoveries: int
    orphan_completions: int
    orphan_compactions: int
    duplicate_events: int
    conflicting_operations: int
    invalid_events: int
    limited: bool
    journal_read_failed: bool
    working_memory_snapshots: tuple[_WorkingMemoryObservation, ...]
    working_memory_runs: tuple[str, ...]
    latest_working_memory: _WorkingMemoryObservation | None
    diagnostics: tuple[dict[str, str], ...]


class _Diagnostics:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, source: str, code: str, message: str) -> None:
        item = {"source": source, "code": code, "message": message}
        if len(self.items) < _MAX_DIAGNOSTICS and item not in self.items:
            self.items.append(item)


def _event_parts(event: object) -> tuple[object, object, int | None, str | None, str | None]:
    if isinstance(event, Mapping):
        return (
            event.get("type"),
            event.get("payload"),
            event.get("sequence") if isinstance(event.get("sequence"), int) else None,
            event.get("timestamp") if isinstance(event.get("timestamp"), str) else None,
            event.get("runId") if isinstance(event.get("runId"), str) else None,
        )
    return (
        getattr(event, "type", None),
        getattr(event, "payload", None),
        getattr(event, "sequence", None) if isinstance(getattr(event, "sequence", None), int) else None,
        getattr(event, "timestamp", None) if isinstance(getattr(event, "timestamp", None), str) else None,
        getattr(event, "run_id", None) if isinstance(getattr(event, "run_id", None), str) else None,
    )


def _bounded_count(value: object, *, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return None


def _operation_id(payload: Mapping[str, Any], *, version_key: str) -> str | None:
    version = payload.get(version_key)
    value = payload.get("contextOperationId")
    if (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == 1
        and isinstance(value, str)
        and _CONTEXT_OPERATION_ID_RE.fullmatch(value)
    ):
        return value
    return None


def _safe_source(run_source: str) -> str:
    return run_source if run_source in _RUN_SOURCES else "unknown"


def _parse_compaction(
    payload: object,
    *,
    sequence: int,
    timestamp: str | None,
    run_source: str,
) -> _CompactionEvent | None:
    if not isinstance(payload, Mapping):
        return None
    operation_id = _operation_id(payload, version_key="contextVersion")
    path = payload.get("path")
    trigger = payload.get("trigger")
    strategy = payload.get("strategy")
    before = _bounded_count(payload.get("messagesBefore"), maximum=_MAX_CONTEXT_MESSAGES)
    after = _bounded_count(payload.get("messagesAfter"), maximum=_MAX_CONTEXT_MESSAGES)
    removed = _bounded_count(payload.get("messagesRemoved"), maximum=_MAX_CONTEXT_MESSAGES)
    if (
        operation_id is None
        or path not in _CONTEXT_PATHS
        or trigger not in _CONTEXT_TRIGGERS
        or strategy not in _CONTEXT_STRATEGIES
        or payload.get("effective") is not True
        or before is None
        or after is None
        or removed is None
        or after > before
        or removed != before - after
    ):
        return None
    tokens_freed: int | None = None
    if "tokensFreed" in payload and payload.get("tokensFreed") is not None:
        tokens_freed = _bounded_count(payload.get("tokensFreed"), maximum=_MAX_CONTEXT_TOKENS)
        if tokens_freed is None:
            return None
    return _CompactionEvent(
        operation_id=operation_id,
        sequence=sequence,
        timestamp=timestamp,
        path=str(path),
        trigger=str(trigger),
        strategy=str(strategy),
        messages_before=before,
        messages_after=after,
        messages_removed=removed,
        tokens_freed=tokens_freed,
        run_source=_safe_source(run_source),
    )


def _parse_recovery_started(payload: object, *, sequence: int) -> _RecoveryStartedEvent | None:
    if not isinstance(payload, Mapping):
        return None
    operation_id = _operation_id(payload, version_key="recoveryVersion")
    kind = payload.get("kind")
    if operation_id is None or kind not in _RECOVERY_KINDS or payload.get("reason") != "context_overflow":
        return None
    return _RecoveryStartedEvent(operation_id=operation_id, sequence=sequence, kind=str(kind))


def _parse_recovery_completed(payload: object, *, sequence: int) -> _RecoveryCompletedEvent | None:
    if not isinstance(payload, Mapping):
        return None
    operation_id = _operation_id(payload, version_key="recoveryVersion")
    kind = payload.get("kind")
    outcome = payload.get("outcome")
    before = _bounded_count(payload.get("messagesBefore"), maximum=_MAX_CONTEXT_MESSAGES)
    after = _bounded_count(payload.get("messagesAfter"), maximum=_MAX_CONTEXT_MESSAGES)
    if (
        operation_id is None
        or kind not in _RECOVERY_KINDS
        or outcome not in _RECOVERY_OUTCOMES
        or before is None
        or after is None
        or after > before
    ):
        return None
    tokens_freed: int | None = None
    if outcome == "recovered":
        tokens_freed = _bounded_count(payload.get("tokensFreed"), maximum=_MAX_CONTEXT_TOKENS)
        if tokens_freed is None:
            return None
    elif "tokensFreed" in payload and payload.get("tokensFreed") is not None:
        return None
    return _RecoveryCompletedEvent(
        operation_id=operation_id,
        sequence=sequence,
        kind=str(kind),
        outcome=str(outcome),
        messages_before=before,
        messages_after=after,
        tokens_freed=tokens_freed,
    )


def _parse_working_memory(
    payload: object,
    *,
    sequence: int,
    timestamp: str | None,
    run_id: str | None,
    run_source: str,
) -> _WorkingMemoryObservation | None:
    if not isinstance(payload, Mapping):
        return None
    entries = _bounded_count(payload.get("entries"), maximum=_MAX_MEMORY_COUNT)
    max_entries = _bounded_count(payload.get("maxEntries"), maximum=_MAX_MEMORY_COUNT)
    protected_tokens = _bounded_count(payload.get("protectedTokens"), maximum=_MAX_MEMORY_TOKENS)
    max_tokens = _bounded_count(payload.get("maxTokens"), maximum=_MAX_MEMORY_TOKENS)
    if (
        payload.get("workingMemoryVersion") != 1
        or payload.get("action") != "protected"
        or payload.get("scope") != "process"
        or entries is None
        or max_entries is None
        or protected_tokens is None
        or max_tokens is None
        or entries > max_entries
        or protected_tokens > max_tokens
    ):
        return None
    return _WorkingMemoryObservation(
        sequence=sequence,
        timestamp=timestamp,
        run_id=run_id if isinstance(run_id, str) and _RUN_ID_RE.fullmatch(run_id) else None,
        run_source=_safe_source(run_source),
        scope="process",
        entries=entries,
        max_entries=max_entries,
        protected_tokens=protected_tokens,
        max_tokens=max_tokens,
    )



def _identity_key(item: object) -> tuple[object, ...]:
    if isinstance(item, _CompactionEvent):
        return (
            item.operation_id,
            item.path,
            item.trigger,
            item.strategy,
            item.messages_before,
            item.messages_after,
            item.messages_removed,
            item.tokens_freed,
        )
    if isinstance(item, _RecoveryStartedEvent):
        return (item.operation_id, item.kind)
    if isinstance(item, _RecoveryCompletedEvent):
        return (
            item.operation_id,
            item.kind,
            item.outcome,
            item.messages_before,
            item.messages_after,
            item.tokens_freed,
        )
    return (item,)

def _dedupe_or_conflict(
    current: object | None,
    candidate: object,
    *,
    diagnostics: _Diagnostics,
    source: str,
    duplicate_code: str,
    conflict_code: str,
    duplicate_message: str,
    conflict_message: str,
) -> tuple[object | None, int, bool]:
    if current is None:
        return candidate, 0, False
    if _identity_key(current) == _identity_key(candidate):
        diagnostics.add(source, duplicate_code, duplicate_message)
        return current, 1, False
    diagnostics.add(source, conflict_code, conflict_message)
    return current, 0, True


def _reconcile_operation(
    operation: _Operation,
    *,
    diagnostics: _Diagnostics,
) -> _OperationReconciliation:
    started = operation.started
    completed = operation.completed
    compaction = operation.compacted
    if operation.conflict:
        return _OperationReconciliation(
            classification="conflict", conflicting_operation=True
        )
    if started is None and completed is None and compaction is None:
        return _OperationReconciliation(classification="empty")
    if started is None and completed is not None:
        diagnostics.add(
            "recovery",
            "recovery_completion_orphan",
            "A Recovery completion without a trusted start was ignored.",
        )
        return _OperationReconciliation(
            classification="orphan_completion", orphan_completion=True
        )
    if started is None and compaction is not None:
        if compaction.path in _REACTIVE_CONTEXT_PATHS:
            diagnostics.add(
                "context",
                "context_compaction_orphan",
                "A reactive Context compaction without a trusted Recovery was ignored.",
            )
            return _OperationReconciliation(
                classification="orphan_compaction", orphan_compaction=True
            )
        return _OperationReconciliation(
            classification="direct_compaction", trusted_compaction=compaction
        )
    if started is not None and completed is None:
        diagnostics.add(
            "recovery",
            "recovery_operation_dangling",
            "A Recovery start has no trusted completion in the bounded scan.",
        )
        return _OperationReconciliation(
            classification="dangling",
            recovery_attempt=True,
            dangling_recovery=True,
        )
    if started is None or completed is None:
        return _OperationReconciliation(classification="empty")
    if completed.sequence <= started.sequence or started.kind != completed.kind:
        diagnostics.add(
            "recovery",
            "recovery_operation_conflict",
            "Conflicting Recovery operation events were excluded.",
        )
        return _OperationReconciliation(
            classification="conflict", conflicting_operation=True
        )
    if completed.outcome == "not_recovered":
        if compaction is not None:
            diagnostics.add(
                "recovery",
                "recovery_operation_conflict",
                "A not-recovered Recovery with a compaction was excluded.",
            )
            return _OperationReconciliation(
                classification="conflict", conflicting_operation=True
            )
        return _OperationReconciliation(
            classification="not_recovered",
            recovery_attempt=True,
            recovery_completed=True,
            recovery_outcome="not_recovered",
        )
    if compaction is None:
        diagnostics.add(
            "recovery",
            "recovery_compaction_missing",
            "A recovered Recovery without a trusted compaction was excluded.",
        )
        return _OperationReconciliation(
            classification="conflict", conflicting_operation=True
        )
    conflict_message: str | None = None
    if compaction.sequence <= started.sequence or compaction.sequence >= completed.sequence:
        conflict_message = "Recovery and Context compaction ordering or kind did not match."
    elif compaction.path != _PATH_FOR_KIND.get(started.kind):
        conflict_message = "Recovery and Context compaction ordering or kind did not match."
    elif (
        completed.tokens_freed is not None
        and compaction.tokens_freed is not None
        and completed.tokens_freed != compaction.tokens_freed
    ):
        conflict_message = "Recovery and Context token observations did not match."
    elif (
        completed.messages_before != compaction.messages_before
        or completed.messages_after != compaction.messages_after
    ):
        conflict_message = "Recovery and Context message observations did not match."
    if conflict_message is not None:
        diagnostics.add("recovery", "recovery_operation_conflict", conflict_message)
        return _OperationReconciliation(
            classification="conflict", conflicting_operation=True
        )
    return _OperationReconciliation(
        classification="recovered",
        trusted_compaction=compaction,
        recovery_attempt=True,
        recovery_completed=True,
        recovery_outcome="recovered",
    )

def aggregate_run_context(
    events: Iterable[object],
    *,
    run_source: str = "unknown",
    limited: bool = False,
    journal_read_failed: bool = False,
    max_events: int = _MAX_EVENTS,
) -> ContextAggregate:
    """Reconcile one Run's bounded Context facts without executing runtime code."""
    source = _safe_source(run_source)
    diagnostics = _Diagnostics()
    event_limit = min(max_events, _MAX_EVENTS) if isinstance(max_events, int) and not isinstance(max_events, bool) and max_events > 0 else _MAX_EVENTS
    operations: dict[str, _Operation] = {}
    duplicate_events = 0
    conflicting_operations = 0
    invalid_events = 0
    working_memory_snapshots: list[_WorkingMemoryObservation] = []
    run_id_for_wm: str | None = None
    scanned = 0
    for event in events:
        if scanned >= event_limit:
            limited = True
            diagnostics.add("context", "context_scan_limited", "Context observations reached the Dashboard scan limit.")
            break
        scanned += 1
        event_type, raw_payload, sequence, timestamp, run_id = _event_parts(event)
        if sequence is None:
            sequence = scanned
        if isinstance(run_id, str) and _RUN_ID_RE.fullmatch(run_id):
            run_id_for_wm = run_id
        if event_type == "context.compacted":
            parsed = _parse_compaction(raw_payload, sequence=sequence, timestamp=timestamp, run_source=source)
            if parsed is None:
                invalid_events += 1
                diagnostics.add("context", "context_event_invalid", "A malformed Context compaction event was ignored.")
                continue
            operation = operations.setdefault(parsed.operation_id, _Operation())
            current, duplicate, conflict = _dedupe_or_conflict(
                operation.compacted,
                parsed,
                diagnostics=diagnostics,
                source="context",
                duplicate_code="context_operation_duplicate",
                conflict_code="context_operation_conflict",
                duplicate_message="A duplicate Context compaction event was counted at most once.",
                conflict_message="Conflicting Context compaction events were excluded.",
            )
            duplicate_events += duplicate
            operation.conflict = operation.conflict or conflict
            operation.compacted = current  # type: ignore[assignment]
            continue
        if event_type == "recovery.started":
            parsed_started = _parse_recovery_started(raw_payload, sequence=sequence)
            if parsed_started is None:
                invalid_events += 1
                diagnostics.add("recovery", "recovery_event_invalid", "A malformed Recovery start event was ignored.")
                continue
            operation = operations.setdefault(parsed_started.operation_id, _Operation())
            current, duplicate, conflict = _dedupe_or_conflict(
                operation.started,
                parsed_started,
                diagnostics=diagnostics,
                source="recovery",
                duplicate_code="context_operation_duplicate",
                conflict_code="recovery_operation_conflict",
                duplicate_message="A duplicate Recovery event was counted at most once.",
                conflict_message="Conflicting Recovery operation events were excluded.",
            )
            duplicate_events += duplicate
            operation.conflict = operation.conflict or conflict
            operation.started = current  # type: ignore[assignment]
            continue
        if event_type == "recovery.completed":
            parsed_completed = _parse_recovery_completed(raw_payload, sequence=sequence)
            if parsed_completed is None:
                invalid_events += 1
                diagnostics.add("recovery", "recovery_event_invalid", "A malformed Recovery completion event was ignored.")
                continue
            operation = operations.setdefault(parsed_completed.operation_id, _Operation())
            current, duplicate, conflict = _dedupe_or_conflict(
                operation.completed,
                parsed_completed,
                diagnostics=diagnostics,
                source="recovery",
                duplicate_code="context_operation_duplicate",
                conflict_code="recovery_operation_conflict",
                duplicate_message="A duplicate Recovery event was counted at most once.",
                conflict_message="Conflicting Recovery operation events were excluded.",
            )
            duplicate_events += duplicate
            operation.conflict = operation.conflict or conflict
            operation.completed = current  # type: ignore[assignment]
            continue
        if event_type == "working_memory.observed":
            parsed_wm = _parse_working_memory(
                raw_payload,
                sequence=sequence,
                timestamp=timestamp,
                run_id=run_id_for_wm or run_id,
                run_source=source,
            )
            if parsed_wm is None:
                invalid_events += 1
                diagnostics.add("working_memory", "working_memory_event_invalid", "A malformed WorkingMemory observation was ignored.")
                continue
            working_memory_snapshots.append(parsed_wm)

    if limited:
        diagnostics.add("context", "context_scan_limited", "Context observations were limited by the Dashboard scan scope.")
        diagnostics.add("working_memory", "working_memory_scan_limited", "WorkingMemory observations were limited by the Dashboard scan scope.")
    if journal_read_failed:
        limited = True
        diagnostics.add("context", "context_journal_read_failed", "Context observations could not be read completely.")

    trusted_compactions: list[_CompactionEvent] = []
    dangling_recoveries = 0
    orphan_completions = 0
    orphan_compactions = 0
    recovery_attempts = 0
    completed_recoveries = 0
    recovered_recoveries = 0
    not_recovered_recoveries = 0
    for operation in operations.values():
        result = _reconcile_operation(operation, diagnostics=diagnostics)
        if result.trusted_compaction is not None:
            trusted_compactions.append(result.trusted_compaction)
        if result.conflicting_operation:
            conflicting_operations += 1
        if result.orphan_completion:
            orphan_completions += 1
        if result.orphan_compaction:
            orphan_compactions += 1
        if result.dangling_recovery:
            dangling_recoveries += 1
        if result.recovery_attempt:
            recovery_attempts += 1
        if result.recovery_completed:
            completed_recoveries += 1
        if result.recovery_outcome == "recovered":
            recovered_recoveries += 1
        elif result.recovery_outcome == "not_recovered":
            not_recovered_recoveries += 1

    latest_wm = _latest_observation(working_memory_snapshots)
    return ContextAggregate(
        compactions=tuple(trusted_compactions),
        recovery_attempts=recovery_attempts,
        completed_recoveries=completed_recoveries,
        recovered_recoveries=recovered_recoveries,
        not_recovered_recoveries=not_recovered_recoveries,
        dangling_recoveries=dangling_recoveries,
        orphan_completions=orphan_completions,
        orphan_compactions=orphan_compactions,
        duplicate_events=duplicate_events,
        conflicting_operations=conflicting_operations,
        invalid_events=invalid_events,
        limited=limited,
        journal_read_failed=journal_read_failed,
        working_memory_snapshots=tuple(working_memory_snapshots),
        working_memory_runs=tuple({item.run_id for item in working_memory_snapshots if item.run_id}),
        latest_working_memory=latest_wm,
        diagnostics=tuple(diagnostics.items),
    )


def merge_context_aggregates(
    items: Iterable[ContextAggregate],
    *,
    limited: bool = False,
    journal_read_failed: bool = False,
    max_aggregates: int = _MAX_AGGREGATES,
) -> ContextAggregate:
    """Merge retained per-Run Context facts without pairing across Runs."""
    aggregate_limit = min(max_aggregates, _MAX_AGGREGATES) if isinstance(max_aggregates, int) and not isinstance(max_aggregates, bool) and max_aggregates > 0 else _MAX_AGGREGATES
    selected: list[ContextAggregate] = []
    diagnostics = _Diagnostics()
    for item in items:
        if len(selected) >= aggregate_limit:
            limited = True
            break
        if isinstance(item, ContextAggregate):
            selected.append(item)
        else:
            limited = True
            journal_read_failed = True
    for item in selected:
        for diagnostic in item.diagnostics:
            if len(diagnostics.items) < _MAX_DIAGNOSTICS and diagnostic not in diagnostics.items:
                diagnostics.items.append(diagnostic)
    if limited:
        diagnostics.add("context", "context_scan_limited", "Context aggregation reached the Dashboard Run scan limit.")
        diagnostics.add("working_memory", "working_memory_scan_limited", "WorkingMemory aggregation reached the Dashboard Run scan limit.")
    if journal_read_failed:
        diagnostics.add("context", "context_journal_read_failed", "Retained Context observations could not be read completely.")
    compactions: list[_CompactionEvent] = []
    messages_removed = 0
    known_tokens = 0
    overflowed = False
    for item in selected:
        for compaction in item.compactions:
            messages_removed += compaction.messages_removed
            if compaction.tokens_freed is not None:
                known_tokens += compaction.tokens_freed
            if messages_removed > _MAX_RETAINED_MESSAGES_REMOVED or known_tokens > _MAX_RETAINED_TOKENS_FREED:
                overflowed = True
                break
            compactions.append(compaction)
        if overflowed:
            break
    if overflowed:
        limited = True
        compactions = []
        diagnostics.add("context", "context_event_invalid", "Retained Context totals exceeded the bounded aggregation range.")
    snapshots = tuple(snapshot for item in selected for snapshot in item.working_memory_snapshots)
    latest = _latest_observation(snapshots)
    return ContextAggregate(
        compactions=tuple(compactions),
        recovery_attempts=sum(item.recovery_attempts for item in selected),
        completed_recoveries=sum(item.completed_recoveries for item in selected),
        recovered_recoveries=sum(item.recovered_recoveries for item in selected),
        not_recovered_recoveries=sum(item.not_recovered_recoveries for item in selected),
        dangling_recoveries=sum(item.dangling_recoveries for item in selected),
        orphan_completions=sum(item.orphan_completions for item in selected),
        orphan_compactions=sum(item.orphan_compactions for item in selected),
        duplicate_events=sum(item.duplicate_events for item in selected),
        conflicting_operations=sum(item.conflicting_operations for item in selected),
        invalid_events=sum(item.invalid_events for item in selected) + int(overflowed),
        limited=limited or any(item.limited for item in selected),
        journal_read_failed=journal_read_failed or any(item.journal_read_failed for item in selected),
        working_memory_snapshots=snapshots,
        working_memory_runs=tuple(sorted({run for item in selected for run in item.working_memory_runs})),
        latest_working_memory=latest,
        diagnostics=tuple(diagnostics.items),
    )


def _integrity(aggregate: ContextAggregate) -> IntegrityStatus:
    return "partial" if (
        aggregate.duplicate_events
        or aggregate.conflicting_operations
        or aggregate.orphan_completions
        or aggregate.orphan_compactions
        or aggregate.dangling_recoveries
        or aggregate.invalid_events
        or aggregate.limited
        or aggregate.journal_read_failed
    ) else "complete"


def _coverage(aggregate: ContextAggregate, *, scope: str = "retained-run-journal") -> dict[str, object]:
    return {
        "integrity": _integrity(aggregate),
        "instrumentation": "partial",
        "historical": "partial",
        "scope": scope,
        "duplicateEvents": aggregate.duplicate_events,
        "conflictingOperations": aggregate.conflicting_operations,
        "orphanEvents": aggregate.orphan_completions + aggregate.orphan_compactions,
        "danglingRecoveries": aggregate.dangling_recoveries,
        "orphanCompletions": aggregate.orphan_completions,
        "invalidEvents": aggregate.invalid_events,
        "limited": aggregate.limited,
    }


def project_context_metric(aggregate: ContextAggregate) -> dict[str, object]:
    compactions = aggregate.compactions
    if not compactions:
        return {"status": "unavailable", "value": None, "coverage": _coverage(aggregate)}
    token_known = sum(1 for item in compactions if item.tokens_freed is not None)
    return {
        "status": "partial",
        "value": {
            "observedCompactions": len(compactions),
            "directCompactions": sum(1 for item in compactions if item.path in _DIRECT_CONTEXT_PATHS),
            "recoveryCompactions": sum(1 for item in compactions if item.path in _REACTIVE_CONTEXT_PATHS),
            "messagesRemoved": sum(item.messages_removed for item in compactions),
            "knownTokensFreed": sum(item.tokens_freed or 0 for item in compactions),
            "tokenKnownCompactions": token_known,
            "tokenUnknownCompactions": len(compactions) - token_known,
        },
        "coverage": _coverage(aggregate),
    }


def project_recovery_metric(aggregate: ContextAggregate) -> dict[str, object]:
    if aggregate.recovery_attempts == 0:
        return {"status": "unavailable", "value": None, "coverage": _coverage(aggregate)}
    return {
        "status": "partial",
        "value": {
            "attempts": aggregate.recovery_attempts,
            "completedAttempts": aggregate.completed_recoveries,
            "recoveredAttempts": aggregate.recovered_recoveries,
            "notRecoveredAttempts": aggregate.not_recovered_recoveries,
        },
        "coverage": _coverage(aggregate),
    }


def _latest_observation(items: Iterable[_WorkingMemoryObservation]) -> _WorkingMemoryObservation | None:
    selected = list(items)
    if not selected:
        return None
    return max(
        selected,
        key=lambda item: (
            item.timestamp or "",
            item.sequence,
            item.run_id or "",
        ),
    )


def _project_latest(item: _WorkingMemoryObservation | None, *, include_run: bool) -> dict[str, object] | None:
    if item is None:
        return None
    payload: dict[str, object] = {
        "runSource": item.run_source,
        "observedAt": item.timestamp,
        "scope": item.scope,
        "entries": item.entries,
        "maxEntries": item.max_entries,
        "protectedTokens": item.protected_tokens,
        "maxTokens": item.max_tokens,
    }
    if include_run and item.run_id is not None:
        payload["runId"] = item.run_id
    return payload


def project_working_memory_metric(aggregate: ContextAggregate) -> dict[str, object]:
    snapshots = aggregate.working_memory_snapshots
    coverage = _coverage(aggregate, scope="process-local-observation")
    coverage["summedAcrossRuns"] = False
    if not snapshots:
        return {"status": "unavailable", "value": None, "coverage": coverage}
    return {
        "status": "partial",
        "value": {
            "observedSnapshots": len(snapshots),
            "runsWithSnapshots": len({item.run_id for item in snapshots if item.run_id}),
            "latestObservation": _project_latest(aggregate.latest_working_memory, include_run=True),
        },
        "coverage": coverage,
    }


def project_run_context_summary(aggregate: ContextAggregate) -> dict[str, object]:
    context = project_context_metric(aggregate)
    recovery = project_recovery_metric(aggregate)
    wm = project_working_memory_metric(aggregate)
    context_value = context["value"] or {}
    recovery_value = recovery["value"] or {}
    latest = aggregate.latest_working_memory
    return {
        "context": {
            "status": context["status"],
            "compactions": context_value.get("observedCompactions"),
            "recoveries": recovery_value.get("attempts"),
            "messagesRemoved": context_value.get("messagesRemoved"),
            "knownTokensFreed": context_value.get("knownTokensFreed"),
            "limited": aggregate.limited,
        },
        "workingMemory": {
            "status": wm["status"],
            "observed": latest is not None,
            "entries": latest.entries if latest else None,
            "maxEntries": latest.max_entries if latest else None,
            "limited": aggregate.limited,
        },
    }


def _breakdown(
    compactions: tuple[_CompactionEvent, ...],
    *,
    key_name: str,
    key: Any,
) -> list[dict[str, object]]:
    counts: dict[str, dict[str, int]] = {}
    for compaction in compactions:
        group = key(compaction)
        if not isinstance(group, str):
            continue
        item = counts.setdefault(group, {"count": 0, "messagesRemoved": 0, "knownTokensFreed": 0, "tokenKnown": 0, "tokenUnknown": 0})
        item["count"] += 1
        item["messagesRemoved"] += compaction.messages_removed
        if compaction.tokens_freed is None:
            item["tokenUnknown"] += 1
        else:
            item["tokenKnown"] += 1
            item["knownTokensFreed"] += compaction.tokens_freed
    ordered = sorted(counts.items(), key=lambda item: (-item[1]["count"], item[0]))[:_MAX_BREAKDOWN]
    return [{key_name: group, **values} for group, values in ordered]


def _counter_breakdown(counter: Counter[str], *, key_name: str) -> list[dict[str, object]]:
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:_MAX_BREAKDOWN]
    ]


def project_context_breakdown(aggregate: ContextAggregate) -> dict[str, object]:
    outcomes = Counter()
    if aggregate.recovered_recoveries:
        outcomes["recovered"] = aggregate.recovered_recoveries
    if aggregate.not_recovered_recoveries:
        outcomes["not_recovered"] = aggregate.not_recovered_recoveries
    if aggregate.dangling_recoveries:
        outcomes["dangling"] = aggregate.dangling_recoveries
    # Kind is intentionally bounded to valid enum values inferred from trusted reactive paths.
    kinds = Counter(
        "cybernetic" if item.path == "reactive_cybernetic" else "compactor"
        for item in aggregate.compactions
        if item.path in _REACTIVE_CONTEXT_PATHS
    )
    sources = Counter(item.run_source for item in aggregate.compactions)
    for snapshot in aggregate.working_memory_snapshots:
        sources[snapshot.run_source] += 0
    return {
        "paths": _breakdown(aggregate.compactions, key_name="path", key=lambda item: item.path),
        "triggers": _breakdown(aggregate.compactions, key_name="trigger", key=lambda item: item.trigger),
        "strategies": _breakdown(aggregate.compactions, key_name="strategy", key=lambda item: item.strategy),
        "recoveryKinds": _counter_breakdown(kinds, key_name="kind"),
        "recoveryOutcomes": _counter_breakdown(outcomes, key_name="outcome"),
        "sources": _breakdown(aggregate.compactions, key_name="source", key=lambda item: item.run_source),
    }


def project_context_event_detail(event_type: str, payload: Mapping[str, Any]) -> dict[str, object]:
    """Whitelist one timeline Context/Recovery/WorkingMemory event safely."""
    if event_type == "context.compaction.failed":
        operation_id = payload.get("contextOperationId")
        path = payload.get("path")
        trigger = payload.get("trigger")
        strategy = payload.get("strategy")
        attempted = payload.get("attempted")
        reason = payload.get("reason")
        failures = payload.get("consecutiveFailures")
        tripped = payload.get("circuitBreakerTripped")
        if (
            payload.get("contextVersion") != 1
            or not isinstance(operation_id, str)
            or not _CONTEXT_OPERATION_ID_RE.fullmatch(operation_id)
            or path not in _CONTEXT_PATHS
            or trigger not in _CONTEXT_TRIGGERS
            or strategy not in _CONTEXT_STRATEGIES
            or payload.get("effective") is not False
            or not isinstance(attempted, bool)
            or reason not in _CONTEXT_FAILURE_REASONS
            or isinstance(failures, bool)
            or not isinstance(failures, int)
            or not 0 <= failures <= 100_000
            or not isinstance(tripped, bool)
        ):
            return {}
        return {
            "contextVersion": 1,
            "path": path,
            "trigger": trigger,
            "strategy": strategy,
            "effective": False,
            "attempted": attempted,
            "reason": reason,
            "consecutiveFailures": failures,
            "circuitBreakerTripped": tripped,
        }
    if event_type == "context.compacted":
        parsed = _parse_compaction(payload, sequence=0, timestamp=None, run_source="unknown")
        if parsed is None:
            return {}
        details: dict[str, object] = {
            "contextVersion": 1,
            "path": parsed.path,
            "trigger": parsed.trigger,
            "strategy": parsed.strategy,
            "effective": True,
            "messagesBefore": parsed.messages_before,
            "messagesAfter": parsed.messages_after,
            "messagesRemoved": parsed.messages_removed,
        }
        if parsed.tokens_freed is not None:
            details["tokensFreed"] = parsed.tokens_freed
        return details
    if event_type == "recovery.started":
        parsed_started = _parse_recovery_started(payload, sequence=0)
        if parsed_started is None:
            return {}
        return {
            "recoveryVersion": 1,
            "kind": parsed_started.kind,
            "reason": "context_overflow",
        }
    if event_type == "recovery.completed":
        parsed_completed = _parse_recovery_completed(payload, sequence=0)
        if parsed_completed is None:
            return {}
        details = {
            "recoveryVersion": 1,
            "kind": parsed_completed.kind,
            "outcome": parsed_completed.outcome,
            "messagesBefore": parsed_completed.messages_before,
            "messagesAfter": parsed_completed.messages_after,
        }
        if parsed_completed.tokens_freed is not None:
            details["tokensFreed"] = parsed_completed.tokens_freed
        return details
    if event_type == "working_memory.observed":
        parsed_wm = _parse_working_memory(
            payload,
            sequence=0,
            timestamp=None,
            run_id=None,
            run_source="unknown",
        )
        if parsed_wm is None:
            return {}
        return {
            "workingMemoryVersion": 1,
            "action": "protected",
            "scope": "process",
            "entries": parsed_wm.entries,
            "maxEntries": parsed_wm.max_entries,
            "protectedTokens": parsed_wm.protected_tokens,
            "maxTokens": parsed_wm.max_tokens,
        }
    return {}
