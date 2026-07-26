from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import minicode.working_memory as working_memory_module
from minicode.web.context_aggregation import (
    aggregate_run_context,
    merge_context_aggregates,
    project_context_breakdown,
    project_context_event_detail,
    project_context_metric,
    project_recovery_metric,
    project_run_context_summary,
    project_working_memory_metric,
)
from minicode.web.read_model import DashboardReadModel


def event(sequence: int, event_type: str, payload: dict[str, object], *, run_id: str = "run_" + "1" * 32, timestamp: str | None = None):
    return SimpleNamespace(
        type=event_type,
        payload=payload,
        sequence=sequence,
        timestamp=timestamp or f"2026-07-17T10:00:{sequence:02d}.000Z",
        run_id=run_id,
    )


def ctx(operation: str = "a") -> str:
    return "ctxop_" + operation * 32


def compaction(operation: str = "a", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contextVersion": 1,
        "contextOperationId": ctx(operation),
        "path": "pre_request_compactor",
        "trigger": "auto",
        "strategy": "full",
        "effective": True,
        "tokensFreed": 1200,
        "messagesBefore": 32,
        "messagesAfter": 18,
        "messagesRemoved": 14,
    }
    payload.update(overrides)
    return payload


def started(operation: str = "a", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "recoveryVersion": 1,
        "contextOperationId": ctx(operation),
        "kind": "cybernetic",
        "reason": "context_overflow",
    }
    payload.update(overrides)
    return payload


def completed(operation: str = "a", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "recoveryVersion": 1,
        "contextOperationId": ctx(operation),
        "kind": "cybernetic",
        "outcome": "recovered",
        "tokensFreed": 900,
        "messagesBefore": 12,
        "messagesAfter": 7,
    }
    payload.update(overrides)
    return payload


def working(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workingMemoryVersion": 1,
        "action": "protected",
        "scope": "process",
        "entries": 3,
        "maxEntries": 15,
        "protectedTokens": 240,
        "maxTokens": 4000,
    }
    payload.update(overrides)
    return payload


def test_direct_compaction_and_unknown_tokens_are_partial_not_live() -> None:
    aggregate = aggregate_run_context(
        [
            event(1, "context.compacted", compaction("a")),
            event(2, "context.compacted", compaction("b", path="context_manager_auto", strategy="context_manager", tokensFreed=None, messagesBefore=9, messagesAfter=6, messagesRemoved=3)),
        ],
        run_source="gateway",
    )

    metric = project_context_metric(aggregate)

    assert metric == {
        "status": "partial",
        "value": {
            "observedCompactions": 2,
            "directCompactions": 2,
            "recoveryCompactions": 0,
            "messagesRemoved": 17,
            "knownTokensFreed": 1200,
            "tokenKnownCompactions": 1,
            "tokenUnknownCompactions": 1,
        },
        "coverage": {
            "integrity": "complete",
            "instrumentation": "partial",
            "historical": "partial",
            "scope": "retained-run-journal",
            "duplicateEvents": 0,
            "conflictingOperations": 0,
            "orphanEvents": 0,
            "danglingRecoveries": 0,
            "orphanCompletions": 0,
            "invalidEvents": 0,
            "limited": False,
        },
    }
    assert "totalTokensFreed" not in json.dumps(metric)


def test_recovered_and_not_recovered_pairing_rules() -> None:
    aggregate = aggregate_run_context(
        [
            event(1, "recovery.started", started("a")),
            event(2, "context.compacted", compaction("a", path="reactive_cybernetic", trigger="reactive", strategy="reactive", tokensFreed=900, messagesBefore=12, messagesAfter=7, messagesRemoved=5)),
            event(3, "recovery.completed", completed("a")),
            event(4, "recovery.started", started("b", kind="compactor")),
            event(5, "recovery.completed", completed("b", kind="compactor", outcome="not_recovered", messagesBefore=4, messagesAfter=4, tokensFreed=None)),
        ],
        run_source="tui",
    )

    assert project_context_metric(aggregate)["value"] == {
        "observedCompactions": 1,
        "directCompactions": 0,
        "recoveryCompactions": 1,
        "messagesRemoved": 5,
        "knownTokensFreed": 900,
        "tokenKnownCompactions": 1,
        "tokenUnknownCompactions": 0,
    }
    assert project_recovery_metric(aggregate)["value"] == {
        "attempts": 2,
        "completedAttempts": 2,
        "recoveredAttempts": 1,
        "notRecoveredAttempts": 1,
    }


def test_duplicate_conflict_orphan_and_dangling_are_diagnostic_not_repaired() -> None:
    aggregate = aggregate_run_context(
        [
            event(1, "context.compacted", compaction("a")),
            event(2, "context.compacted", compaction("a")),
            event(3, "context.compacted", compaction("b", messagesBefore=8, messagesAfter=4, messagesRemoved=4)),
            event(4, "context.compacted", compaction("b", messagesBefore=9, messagesAfter=4, messagesRemoved=5)),
            event(5, "context.compacted", compaction("c", path="reactive_cybernetic", trigger="reactive", strategy="reactive", messagesBefore=5, messagesAfter=4, messagesRemoved=1)),
            event(6, "recovery.started", started("d")),
            event(7, "recovery.completed", completed("e")),
        ],
        run_source="gateway",
    )

    context = project_context_metric(aggregate)
    recovery = project_recovery_metric(aggregate)
    codes = {item["code"] for item in aggregate.diagnostics}

    assert context["value"]["observedCompactions"] == 1
    assert context["coverage"]["integrity"] == "partial"
    assert recovery["value"]["attempts"] == 1
    assert recovery["coverage"]["danglingRecoveries"] == 1
    assert "context_operation_duplicate" in codes
    assert "context_operation_conflict" in codes
    assert "context_compaction_orphan" in codes
    assert "recovery_operation_dangling" in codes
    assert "recovery_completion_orphan" in codes


def test_working_memory_latest_snapshot_is_not_summed_across_runs() -> None:
    first = aggregate_run_context(
        [
            event(1, "working_memory.observed", working(entries=1, protectedTokens=10), run_id="run_" + "1" * 32, timestamp="2026-07-17T10:00:01.000Z"),
            event(2, "working_memory.observed", working(entries=3, protectedTokens=30), run_id="run_" + "1" * 32, timestamp="2026-07-17T10:00:02.000Z"),
        ],
        run_source="gateway",
    )
    second = aggregate_run_context(
        [event(1, "working_memory.observed", working(entries=7, protectedTokens=70), run_id="run_" + "2" * 32, timestamp="2026-07-17T10:00:03.000Z")],
        run_source="headless",
    )
    merged = merge_context_aggregates([first, second])

    run_metric = project_working_memory_metric(first)
    merged_metric = project_working_memory_metric(merged)

    assert run_metric["value"]["observedSnapshots"] == 2
    assert run_metric["value"]["latestObservation"]["entries"] == 3
    assert merged_metric["value"]["observedSnapshots"] == 3
    assert merged_metric["value"]["runsWithSnapshots"] == 2
    assert merged_metric["value"]["latestObservation"]["runId"] == "run_" + "2" * 32
    assert merged_metric["value"]["latestObservation"]["entries"] == 7
    assert merged_metric["coverage"]["summedAcrossRuns"] is False
    assert "average" not in json.dumps(merged_metric).lower()
    assert "global" not in json.dumps(merged_metric).lower()


def test_invalid_working_memory_falls_back_to_latest_valid_and_marks_partial() -> None:
    aggregate = aggregate_run_context(
        [
            event(1, "working_memory.observed", working(entries=2)),
            event(2, "working_memory.observed", working(entries=16, maxEntries=15, content="secret")),
        ],
        run_source="gateway",
    )

    metric = project_working_memory_metric(aggregate)

    assert metric["value"]["latestObservation"]["entries"] == 2
    assert metric["coverage"]["integrity"] == "partial"
    assert {item["code"] for item in aggregate.diagnostics} == {"working_memory_event_invalid"}
    assert "secret" not in json.dumps(metric)


def test_timeline_projection_uses_same_strict_validator_and_hides_operation_id() -> None:
    operation_id = ctx("a")
    details = project_context_event_detail("context.compacted", compaction("a", summary="secret"))
    recovery = project_context_event_detail("recovery.started", started("a", error="secret"))
    wm = project_context_event_detail("working_memory.observed", working(content="secret"))

    assert "contextOperationId" not in details
    assert operation_id not in json.dumps({"context": details, "recovery": recovery, "wm": wm})
    assert "secret" not in json.dumps({"context": details, "recovery": recovery, "wm": wm})
    assert details["messagesRemoved"] == 14
    assert recovery == {"recoveryVersion": 1, "kind": "cybernetic", "reason": "context_overflow"}
    assert wm["scope"] == "process"


def test_breakdown_is_bounded_enum_only() -> None:
    aggregate = aggregate_run_context(
        [
            event(1, "context.compacted", compaction("a")),
            event(2, "recovery.started", started("b")),
            event(3, "context.compacted", compaction("b", path="reactive_cybernetic", trigger="reactive", strategy="reactive", tokensFreed=4, messagesBefore=5, messagesAfter=4, messagesRemoved=1)),
            event(4, "recovery.completed", completed("b", tokensFreed=4, messagesBefore=5, messagesAfter=4)),
        ],
        run_source="gateway",
    )

    breakdown = project_context_breakdown(aggregate)

    assert breakdown["paths"][0]["path"] == "pre_request_compactor"
    assert breakdown["sources"] == [{"source": "gateway", "count": 2, "messagesRemoved": 15, "knownTokensFreed": 1204, "tokenKnown": 2, "tokenUnknown": 0}]
    assert "ctxop_" not in json.dumps(breakdown)


def test_read_model_does_not_read_working_memory_singleton(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(working_memory_module, "get_working_memory", lambda: pytest.fail("read model must not read WorkingMemory singleton"))
    monkeypatch.setattr(working_memory_module.WorkingMemoryTracker, "snapshot", lambda _self: pytest.fail("read model must not snapshot tracker"))
    monkeypatch.setattr(working_memory_module.WorkingMemoryTracker, "get_stats", lambda _self: pytest.fail("read model must not read tracker stats"), raising=False)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = DashboardReadModel(workspace, data_dir=tmp_path / "home" / ".mini-code").ops()

    assert payload["workingMemory"]["status"] == "unavailable"


def test_run_summary_projects_context_and_working_memory_compactly() -> None:
    aggregate = aggregate_run_context(
        [
            event(1, "context.compacted", compaction("a", messagesBefore=8, messagesAfter=4, messagesRemoved=4, tokensFreed=100)),
            event(2, "working_memory.observed", working(entries=5)),
        ],
        run_source="gateway",
    )

    assert project_run_context_summary(aggregate) == {
        "context": {
            "status": "partial",
            "compactions": 1,
            "recoveries": None,
            "messagesRemoved": 4,
            "knownTokensFreed": 100,
            "limited": False,
        },
        "workingMemory": {
            "status": "partial",
            "observed": True,
            "entries": 5,
            "maxEntries": 15,
            "limited": False,
        },
    }


def recovery_payload(operation: str, *, kind: str = "cybernetic") -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    start = started(operation, kind=kind)
    path = "reactive_cybernetic" if kind == "cybernetic" else "reactive_compactor"
    compact = compaction(operation, path=path, trigger="reactive", strategy="reactive", tokensFreed=10, messagesBefore=5, messagesAfter=3, messagesRemoved=2)
    complete = completed(operation, kind=kind, tokensFreed=10, messagesBefore=5, messagesAfter=3)
    return start, compact, complete


def assert_no_trusted_recovery_or_context(aggregate) -> None:
    assert project_context_metric(aggregate)["status"] == "unavailable"
    assert project_context_metric(aggregate)["value"] is None
    assert project_recovery_metric(aggregate)["status"] == "unavailable"
    assert project_recovery_metric(aggregate)["value"] is None
    assert project_recovery_metric(aggregate)["coverage"]["conflictingOperations"] == 1


def test_conflict_not_recovered_with_compaction_excluded_from_all_trusted_totals() -> None:
    start, compact, _complete = recovery_payload("a")
    aggregate = aggregate_run_context([
        event(1, "recovery.started", start),
        event(2, "context.compacted", compact),
        event(3, "recovery.completed", completed("a", outcome="not_recovered", messagesBefore=5, messagesAfter=5, tokensFreed=None)),
    ])

    assert_no_trusted_recovery_or_context(aggregate)


def test_conflict_recovered_missing_compaction_excluded_from_totals() -> None:
    start, _compact, complete = recovery_payload("a")
    aggregate = aggregate_run_context([
        event(1, "recovery.started", start),
        event(2, "recovery.completed", complete),
    ])

    assert_no_trusted_recovery_or_context(aggregate)


def test_conflict_wrong_reactive_path_excluded_from_totals() -> None:
    start, compact, complete = recovery_payload("a")
    compact["path"] = "reactive_compactor"
    aggregate = aggregate_run_context([
        event(1, "recovery.started", start),
        event(2, "context.compacted", compact),
        event(3, "recovery.completed", complete),
    ])

    assert_no_trusted_recovery_or_context(aggregate)


@pytest.mark.parametrize(
    "ordered",
    [
        ["compact", "start", "complete"],
        ["start", "complete", "compact"],
        ["complete", "start", "compact"],
    ],
)
def test_conflict_bad_event_order_excluded_from_totals(ordered: list[str]) -> None:
    start, compact, complete = recovery_payload("a")
    payloads = {"start": ("recovery.started", start), "compact": ("context.compacted", compact), "complete": ("recovery.completed", complete)}
    aggregate = aggregate_run_context([
        event(index + 1, payloads[name][0], payloads[name][1]) for index, name in enumerate(ordered)
    ])

    assert_no_trusted_recovery_or_context(aggregate)


def test_conflict_token_and_message_mismatch_excluded_from_totals() -> None:
    start, compact, complete = recovery_payload("a")
    token_mismatch = aggregate_run_context([
        event(1, "recovery.started", start),
        event(2, "context.compacted", compact),
        event(3, "recovery.completed", {**complete, "tokensFreed": 11}),
    ])
    assert_no_trusted_recovery_or_context(token_mismatch)

    start, compact, complete = recovery_payload("b")
    message_mismatch = aggregate_run_context([
        event(1, "recovery.started", start),
        event(2, "context.compacted", compact),
        event(3, "recovery.completed", {**complete, "messagesAfter": 4}),
    ])
    assert_no_trusted_recovery_or_context(message_mismatch)


def test_valid_dangling_and_dangling_with_reactive_compaction_do_not_trust_compaction() -> None:
    plain = aggregate_run_context([event(1, "recovery.started", started("a"))])
    assert project_recovery_metric(plain)["value"] == {
        "attempts": 1,
        "completedAttempts": 0,
        "recoveredAttempts": 0,
        "notRecoveredAttempts": 0,
    }
    assert project_recovery_metric(plain)["coverage"]["danglingRecoveries"] == 1
    assert project_context_metric(plain)["status"] == "unavailable"

    aggregate = aggregate_run_context([
        event(1, "recovery.started", started("b")),
        event(2, "context.compacted", compaction("b", path="reactive_cybernetic", trigger="reactive", strategy="reactive", messagesBefore=5, messagesAfter=3, messagesRemoved=2)),
    ])
    assert project_recovery_metric(aggregate)["value"]["attempts"] == 1
    assert project_recovery_metric(aggregate)["coverage"]["danglingRecoveries"] == 1
    assert project_context_metric(aggregate)["status"] == "unavailable"


def test_multiple_conflicts_same_operation_count_once() -> None:
    start, compact, complete = recovery_payload("a")
    compact["path"] = "reactive_compactor"
    aggregate = aggregate_run_context([
        event(1, "recovery.started", start),
        event(2, "context.compacted", compact),
        event(3, "context.compacted", {**compact, "messagesRemoved": 3, "messagesAfter": 2}),
        event(4, "recovery.completed", {**complete, "kind": "compactor", "tokensFreed": 999}),
    ])

    assert aggregate.conflicting_operations == 1
    assert_no_trusted_recovery_or_context(aggregate)


def test_mixed_valid_invalid_operations_have_consistent_totals() -> None:
    s1, c1, done1 = recovery_payload("b")
    aggregate = aggregate_run_context([
        event(1, "context.compacted", compaction("a", messagesBefore=8, messagesAfter=6, messagesRemoved=2, tokensFreed=20)),
        event(2, "recovery.started", s1),
        event(3, "context.compacted", c1),
        event(4, "recovery.completed", done1),
        event(5, "recovery.started", started("c")),
        event(6, "context.compacted", compaction("c", path="reactive_cybernetic", trigger="reactive", strategy="reactive", messagesBefore=5, messagesAfter=4, messagesRemoved=1)),
        event(7, "recovery.completed", completed("c", outcome="not_recovered", messagesBefore=5, messagesAfter=5, tokensFreed=None)),
        event(8, "recovery.started", started("d")),
        event(9, "recovery.completed", completed("e")),
    ])

    assert project_context_metric(aggregate)["value"]["observedCompactions"] == 2
    assert project_context_metric(aggregate)["value"]["messagesRemoved"] == 4
    assert project_recovery_metric(aggregate)["value"] == {
        "attempts": 2,
        "completedAttempts": 1,
        "recoveredAttempts": 1,
        "notRecoveredAttempts": 0,
    }
    coverage = project_recovery_metric(aggregate)["coverage"]
    assert coverage["conflictingOperations"] == 1
    assert coverage["danglingRecoveries"] == 1
    assert coverage["orphanCompletions"] == 1


def test_working_memory_latest_is_deterministic_for_out_of_order_single_run_and_merge() -> None:
    run_a = "run_" + "a" * 32
    run_b = "run_" + "b" * 32
    old = event(10, "working_memory.observed", working(entries=1), run_id=run_a, timestamp="2026-07-17T10:00:01.000Z")
    latest = event(2, "working_memory.observed", working(entries=9), run_id=run_a, timestamp="2026-07-17T10:00:03.000Z")
    middle = event(99, "working_memory.observed", working(entries=5), run_id=run_a, timestamp="2026-07-17T10:00:02.000Z")
    aggregate = aggregate_run_context([latest, middle, old])
    assert project_working_memory_metric(aggregate)["value"]["latestObservation"]["entries"] == 9

    tie_a = aggregate_run_context([event(1, "working_memory.observed", working(entries=3), run_id=run_a, timestamp="2026-07-17T10:00:04.000Z")])
    tie_b = aggregate_run_context([event(1, "working_memory.observed", working(entries=4), run_id=run_b, timestamp="2026-07-17T10:00:04.000Z")])
    merged = merge_context_aggregates([tie_a, tie_b])
    assert project_working_memory_metric(merged)["value"]["latestObservation"]["runId"] == run_b
    assert project_working_memory_metric(merged)["value"]["latestObservation"]["entries"] == 4

    seq_tie = aggregate_run_context([
        event(1, "working_memory.observed", working(entries=2), run_id=run_a, timestamp="2026-07-17T10:00:05.000Z"),
        event(2, "working_memory.observed", working(entries=6), run_id=run_a, timestamp="2026-07-17T10:00:05.000Z"),
    ])
    assert project_working_memory_metric(seq_tie)["value"]["latestObservation"]["entries"] == 6


def test_invalid_latest_working_memory_does_not_override_valid_by_timestamp() -> None:
    aggregate = aggregate_run_context([
        event(1, "working_memory.observed", working(entries=2), timestamp="2026-07-17T10:00:01.000Z"),
        event(2, "working_memory.observed", working(entries=99, maxEntries=15, content="secret"), timestamp="2026-07-17T10:00:09.000Z"),
    ])

    metric = project_working_memory_metric(aggregate)
    assert metric["value"]["latestObservation"]["entries"] == 2
    assert metric["coverage"]["integrity"] == "partial"
    assert "secret" not in json.dumps(metric)
