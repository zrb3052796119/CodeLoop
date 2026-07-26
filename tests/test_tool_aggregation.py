from __future__ import annotations

from minicode.web.tool_aggregation import (
    aggregate_run_failures,
    aggregate_run_tools,
    merge_failure_aggregates,
    merge_tool_aggregates,
    project_failure_breakdown,
    project_failure_metric,
    project_run_failure_summary,
    project_tool_breakdown,
    project_tool_metric,
    project_run_tool_summary,
)


def _tool_event(event_type: str, suffix: str | None = None, **payload: object):
    if suffix is not None:
        payload["operationId"] = "toolop_" + suffix * 32
    return {"type": event_type, "payload": payload}


def _model_event(event_type: str, suffix: str, **payload: object):
    payload["operationId"] = "modelop_" + suffix * 32
    return {"type": event_type, "payload": payload}


def _run_event(event_type: str):
    return {"type": event_type, "payload": {}}


def test_paired_success_is_one_complete_tool_observation() -> None:
    aggregate = aggregate_run_tools(
        [
            _tool_event("tool.started", "a", toolName="read_file"),
            _tool_event(
                "tool.finished",
                "a",
                toolName="read_file",
                outcome="success",
                paired=True,
            ),
        ],
        run_source="gateway",
    )

    assert project_tool_metric(aggregate) == {
        "status": "complete",
        "value": {
            "observedCalls": 1,
            "startedCalls": 1,
            "completedCalls": 1,
            "pairedCalls": 1,
            "successfulCalls": 1,
            "errorCalls": 0,
            "uniqueTools": 1,
        },
        "coverage": {
            "danglingStarts": 0,
            "unpairedFinishes": 0,
            "duplicateEvents": 0,
            "conflictingOperations": 0,
            "orphanFinishes": 0,
            "invalidEvents": 0,
            "historical": "partial",
            "scope": "retained-run-journal",
            "limited": False,
        },
    }
    assert project_tool_breakdown(aggregate)["tools"] == [
        {
            "toolName": "read_file",
            "observedCalls": 1,
            "completedCalls": 1,
            "successfulCalls": 1,
            "errorCalls": 0,
            "incompleteCalls": 0,
        }
    ]


def test_paired_error_and_multiple_same_name_operations_remain_distinct() -> None:
    aggregate = aggregate_run_tools(
        [
            _tool_event("tool.started", "a", toolName="run_command"),
            _tool_event("tool.started", "b", toolName="run_command"),
            _tool_event(
                "tool.finished",
                "a",
                toolName="run_command",
                outcome="error",
                paired=True,
            ),
            _tool_event(
                "tool.finished",
                "b",
                toolName="run_command",
                outcome="success",
                paired=True,
            ),
        ],
        run_source="headless",
    )

    metric = project_tool_metric(aggregate)
    assert metric["status"] == "complete"
    assert metric["value"] == {
        "observedCalls": 2,
        "startedCalls": 2,
        "completedCalls": 2,
        "pairedCalls": 2,
        "successfulCalls": 1,
        "errorCalls": 1,
        "uniqueTools": 1,
    }


def test_unpaired_finishes_and_dangling_starts_are_partial_observations() -> None:
    aggregate = aggregate_run_tools(
        [
            _tool_event("tool.started", "a", toolName="write_file"),
            _tool_event(
                "tool.finished",
                None,
                toolName="read_file",
                outcome="success",
                paired=False,
            ),
            _tool_event(
                "tool.finished",
                None,
                toolName="run_command",
                outcome="error",
                paired=False,
            ),
        ]
    )

    metric = project_tool_metric(aggregate)
    assert metric["status"] == "partial"
    assert metric["value"] == {
        "observedCalls": 3,
        "startedCalls": 1,
        "completedCalls": 2,
        "pairedCalls": 0,
        "successfulCalls": 1,
        "errorCalls": 1,
        "uniqueTools": 3,
    }
    assert metric["coverage"]["danglingStarts"] == 1
    assert metric["coverage"]["unpairedFinishes"] == 2


def test_identical_duplicates_count_once_and_force_partial() -> None:
    start = _tool_event("tool.started", "a", toolName="read_file")
    finish = _tool_event(
        "tool.finished",
        "a",
        toolName="read_file",
        outcome="success",
        paired=True,
    )
    metric = project_tool_metric(
        aggregate_run_tools([start, dict(start), finish, dict(finish)])
    )

    assert metric["status"] == "partial"
    assert metric["value"]["observedCalls"] == 1
    assert metric["value"]["successfulCalls"] == 1
    assert metric["coverage"]["duplicateEvents"] == 2


def test_conflicting_start_and_finish_never_choose_a_name_or_outcome() -> None:
    start_conflict = aggregate_run_tools(
        [
            _tool_event("tool.started", "a", toolName="read_file"),
            _tool_event("tool.started", "a", toolName="write_file"),
        ]
    )
    finish_conflict = aggregate_run_tools(
        [
            _tool_event("tool.started", "b", toolName="run_command"),
            _tool_event(
                "tool.finished",
                "b",
                toolName="run_command",
                outcome="success",
                paired=True,
            ),
            _tool_event(
                "tool.finished",
                "b",
                toolName="run_command",
                outcome="error",
                paired=True,
            ),
        ]
    )

    assert project_tool_metric(start_conflict)["value"]["observedCalls"] == 1
    assert project_tool_breakdown(start_conflict)["tools"] == []
    finish_metric = project_tool_metric(finish_conflict)
    assert finish_metric["status"] == "partial"
    assert finish_metric["value"]["completedCalls"] == 0
    assert finish_metric["value"]["successfulCalls"] == 0
    assert finish_metric["value"]["errorCalls"] == 0
    assert finish_metric["coverage"]["conflictingOperations"] == 1


def test_orphan_and_finish_before_start_are_not_repaired() -> None:
    orphan_finish = _tool_event(
        "tool.finished",
        "a",
        toolName="read_file",
        outcome="success",
        paired=True,
    )
    orphan_only = project_tool_metric(aggregate_run_tools([orphan_finish]))
    finish_then_start = project_tool_metric(
        aggregate_run_tools(
            [orphan_finish, _tool_event("tool.started", "a", toolName="read_file")]
        )
    )

    assert orphan_only["status"] == "unavailable"
    assert orphan_only["coverage"]["orphanFinishes"] == 1
    assert finish_then_start["status"] == "partial"
    assert finish_then_start["value"]["observedCalls"] == 1
    assert finish_then_start["coverage"]["danglingStarts"] == 1


def test_invalid_tool_contracts_never_become_observations() -> None:
    invalid_events = [
        {"type": "tool.started", "payload": {}},
        _tool_event("tool.started", "z", toolName="read_file"),
        _tool_event(
            "tool.finished", None, toolName="read_file", outcome="success", paired=True
        ),
        _tool_event(
            "tool.finished", "a", toolName="read_file", outcome="success", paired=False
        ),
        _tool_event(
            "tool.finished", "a", toolName="read_file", outcome="success", paired=1
        ),
        _tool_event(
            "tool.finished", "a", toolName="read_file", outcome="fatal", paired=True
        ),
        _tool_event(
            "tool.finished", "a", toolName="<script>", outcome="error", paired=True
        ),
        {"type": "tool.finished", "payload": None},
        _tool_event(
            "tool.started", "a", toolName="read_file", toolInput="secret-input"
        ),
        _tool_event(
            "tool.finished",
            "a",
            toolName="read_file",
            outcome="error",
            paired=True,
            toolOutput="secret-output",
        ),
    ]
    metric = project_tool_metric(aggregate_run_tools(invalid_events))

    assert metric["status"] == "unavailable"
    assert metric["value"] is None
    assert metric["coverage"]["invalidEvents"] == len(invalid_events)


def test_limits_and_read_failures_preserve_available_observations() -> None:
    events = [
        _tool_event("tool.started", "a", toolName="read_file"),
        _tool_event(
            "tool.finished",
            "a",
            toolName="read_file",
            outcome="success",
            paired=True,
        ),
    ]
    limited = project_tool_metric(aggregate_run_tools(events, max_events=1))
    retained = project_tool_metric(
        aggregate_run_tools(events, journal_read_failed=True)
    )
    unreadable = project_tool_metric(
        aggregate_run_tools([], journal_read_failed=True)
    )

    assert limited["status"] == "partial"
    assert limited["coverage"]["limited"] is True
    assert retained["status"] == "partial"
    assert retained["value"]["successfulCalls"] == 1
    assert unreadable["status"] == "unavailable"
    assert unreadable["coverage"]["limited"] is True


def test_merge_keeps_runs_isolated_and_breakdowns_stably_sorted() -> None:
    first = aggregate_run_tools(
        [
            _tool_event("tool.started", "a", toolName="z_tool"),
            _tool_event(
                "tool.finished",
                "a",
                toolName="z_tool",
                outcome="success",
                paired=True,
            ),
        ],
        run_source="gateway",
    )
    second = aggregate_run_tools(
        [
            _tool_event("tool.started", "a", toolName="a_tool"),
            _tool_event(
                "tool.finished",
                "a",
                toolName="a_tool",
                outcome="error",
                paired=True,
            ),
            _tool_event(
                "tool.finished",
                None,
                toolName="a_tool",
                outcome="success",
                paired=False,
            ),
        ],
        run_source="headless",
    )
    aggregate = merge_tool_aggregates([first, second])
    breakdown = project_tool_breakdown(aggregate)

    assert project_tool_metric(aggregate)["value"]["observedCalls"] == 3
    assert [row["toolName"] for row in breakdown["tools"]] == [
        "a_tool",
        "z_tool",
    ]
    assert breakdown["outcomes"] == [
        {"outcome": "success", "calls": 2},
        {"outcome": "error", "calls": 1},
        {"outcome": "incomplete", "calls": 0},
        {"outcome": "unpaired", "calls": 1},
    ]
    assert [row["source"] for row in breakdown["sources"]] == [
        "headless",
        "gateway",
    ]


def test_tool_name_breakdown_is_bounded_and_summary_preserves_unavailable() -> None:
    events = []
    for index in range(51):
        suffix = f"{index:032x}"
        operation_id = "toolop_" + suffix
        name = f"tool{index:02d}"
        events.extend(
            [
                {
                    "type": "tool.started",
                    "payload": {"operationId": operation_id, "toolName": name},
                },
                {
                    "type": "tool.finished",
                    "payload": {
                        "operationId": operation_id,
                        "toolName": name,
                        "outcome": "success",
                        "paired": True,
                    },
                },
            ]
        )
    aggregate = aggregate_run_tools(events, run_source="not-a-source")

    assert project_tool_metric(aggregate)["status"] == "partial"
    assert len(project_tool_breakdown(aggregate)["tools"]) == 50
    assert project_tool_breakdown(aggregate)["sources"][0]["source"] == "unknown"
    assert project_run_tool_summary(aggregate)["observedCalls"] == 51
    assert project_run_tool_summary(aggregate_run_tools([])) == {
        "status": "unavailable",
        "observedCalls": None,
        "errorCalls": None,
        "uniqueTools": None,
        "limited": False,
    }


def test_paired_tool_error_is_a_complete_classified_failure() -> None:
    aggregate = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _tool_event("tool.started", "a", toolName="run_command"),
            _tool_event(
                "tool.finished",
                "a",
                toolName="run_command",
                outcome="error",
                paired=True,
            ),
            _run_event("run.completed"),
        ],
        run_status="completed",
        run_source="gateway",
    )

    assert project_failure_metric(aggregate) == {
        "status": "complete",
        "value": {
            "affectedRuns": 1,
            "toolErrors": 1,
            "modelFailures": 0,
            "runFailures": 0,
            "interruptedRuns": 0,
            "cancelledRuns": 0,
            "hasObservedFailure": True,
        },
        "coverage": {
            "observedRuns": 1,
            "invalidEvents": 0,
            "duplicateEvents": 0,
            "conflictingOperations": 0,
            "historical": "partial",
            "scope": "retained-run-journal",
            "limited": False,
        },
    }


def test_model_failure_can_recover_without_becoming_run_failure() -> None:
    aggregate = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _model_event("model.started", "a"),
            _model_event("model.failed", "a", failureKind="timeout"),
            _model_event("model.started", "b"),
            _model_event("model.completed", "b"),
            _run_event("run.completed"),
        ],
        run_status="completed",
        run_source="headless",
    )
    value = project_failure_metric(aggregate)["value"]

    assert value["modelFailures"] == 1
    assert value["runFailures"] == 0
    assert value["affectedRuns"] == 1
    assert project_failure_breakdown(aggregate)["modelFailureKinds"] == [
        {"failureKind": "timeout", "attempts": 1}
    ]


def test_terminal_failure_interruption_and_cancellation_stay_separate() -> None:
    failed = aggregate_run_failures(
        [_run_event("run.queued"), _run_event("run.started"), _run_event("run.failed")],
        run_status="failed",
        run_source="tui",
    )
    interrupted = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _run_event("run.interrupted"),
        ],
        run_status="interrupted",
    )
    cancelled = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _run_event("run.cancel_requested"),
            _run_event("run.cancelled"),
        ],
        run_status="cancelled",
    )

    assert project_failure_metric(failed)["value"] == {
        "affectedRuns": 1,
        "toolErrors": 0,
        "modelFailures": 0,
        "runFailures": 1,
        "interruptedRuns": 0,
        "cancelledRuns": 0,
        "hasObservedFailure": True,
    }
    assert project_failure_metric(interrupted)["value"]["affectedRuns"] == 0
    assert project_failure_metric(interrupted)["value"]["interruptedRuns"] == 1
    assert project_failure_metric(cancelled)["value"]["affectedRuns"] == 0
    assert project_failure_metric(cancelled)["value"]["cancelledRuns"] == 1


def test_failure_zero_is_complete_for_a_fully_scanned_recorded_run() -> None:
    metric = project_failure_metric(
        aggregate_run_failures(
            [
                _run_event("run.queued"),
                _run_event("run.started"),
                _run_event("run.completed"),
            ],
            run_status="completed",
        )
    )

    assert metric["status"] == "complete"
    assert metric["value"]["hasObservedFailure"] is False
    assert metric["value"]["affectedRuns"] == 0
    assert metric["value"]["toolErrors"] == 0


def test_invalid_duplicate_conflict_and_orphan_failures_do_not_double_count() -> None:
    duplicate_model = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _model_event("model.started", "a"),
            _model_event("model.failed", "a", failureKind="network"),
            _model_event("model.failed", "a", failureKind="network"),
            _model_event("model.failed", "b", failureKind="provider_error"),
            _model_event("model.started", "c"),
            _model_event("model.failed", "c", failureKind="unknown"),
        ],
        run_status="queued",
    )
    conflict_tool = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _tool_event("tool.started", "d", toolName="run_command"),
            _tool_event(
                "tool.finished",
                "d",
                toolName="run_command",
                outcome="error",
                paired=True,
            ),
            _tool_event(
                "tool.finished",
                "d",
                toolName="run_command",
                outcome="success",
                paired=True,
            ),
        ],
        run_status="queued",
    )

    duplicate_metric = project_failure_metric(duplicate_model)
    assert duplicate_metric["status"] == "partial"
    assert duplicate_metric["value"]["modelFailures"] == 1
    assert duplicate_metric["coverage"]["duplicateEvents"] == 1
    assert duplicate_metric["coverage"]["invalidEvents"] == 2
    conflict_metric = project_failure_metric(conflict_tool)
    assert conflict_metric["status"] == "partial"
    assert conflict_metric["value"]["toolErrors"] == 0
    assert conflict_metric["coverage"]["conflictingOperations"] == 1


def test_failure_payload_with_provider_error_text_is_invalid_and_never_projected() -> None:
    aggregate = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _model_event("model.started", "a"),
            _model_event(
                "model.failed",
                "a",
                failureKind="provider_error",
                error="Bearer provider-secret",
            ),
        ],
        run_status="queued",
    )
    serialized = repr(
        {
            "metric": project_failure_metric(aggregate),
            "breakdown": project_failure_breakdown(aggregate),
            "diagnostics": aggregate.diagnostics,
        }
    )

    assert project_failure_metric(aggregate)["value"]["modelFailures"] == 0
    assert project_failure_metric(aggregate)["coverage"]["invalidEvents"] == 1
    assert "provider-secret" not in serialized
    assert "Bearer" not in serialized


def test_conflicting_model_terminals_do_not_choose_failure_or_success() -> None:
    aggregate = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _model_event("model.started", "a"),
            _model_event("model.failed", "a", failureKind="timeout"),
            _model_event("model.completed", "a"),
        ],
        run_status="queued",
    )
    metric = project_failure_metric(aggregate)

    assert metric["status"] == "partial"
    assert metric["value"]["modelFailures"] == 0
    assert metric["coverage"]["conflictingOperations"] == 1


def test_one_run_with_multiple_failure_categories_counts_one_affected_run() -> None:
    aggregate = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _tool_event("tool.started", "a", toolName="run_command"),
            _tool_event(
                "tool.finished",
                "a",
                toolName="run_command",
                outcome="error",
                paired=True,
            ),
            _model_event("model.started", "b"),
            _model_event("model.failed", "b", failureKind="provider_error"),
            _run_event("run.failed"),
        ],
        run_status="failed",
        run_source="gateway",
    )
    value = project_failure_metric(aggregate)["value"]

    assert value == {
        "affectedRuns": 1,
        "toolErrors": 1,
        "modelFailures": 1,
        "runFailures": 1,
        "interruptedRuns": 0,
        "cancelledRuns": 0,
        "hasObservedFailure": True,
    }
    assert "totalErrors" not in value


def test_failure_merge_deduplicates_affected_runs_and_breaks_down_sources() -> None:
    gateway = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _model_event("model.started", "a"),
            _model_event("model.failed", "a", failureKind="timeout"),
            _run_event("run.failed"),
        ],
        run_status="failed",
        run_source="gateway",
    )
    headless = aggregate_run_failures(
        [
            _run_event("run.queued"),
            _run_event("run.started"),
            _tool_event("tool.started", "b", toolName="read_file"),
            _tool_event(
                "tool.finished",
                "b",
                toolName="read_file",
                outcome="error",
                paired=True,
            ),
            _run_event("run.completed"),
        ],
        run_status="completed",
        run_source="headless",
    )
    clean = aggregate_run_failures(
        [_run_event("run.queued"), _run_event("run.started"), _run_event("run.completed")],
        run_status="completed",
        run_source="gateway",
    )
    aggregate = merge_failure_aggregates([gateway, headless, clean])
    breakdown = project_failure_breakdown(aggregate)

    assert project_failure_metric(aggregate)["value"]["affectedRuns"] == 2
    assert [row["category"] for row in breakdown["categories"]] == [
        "tool_errors",
        "model_failures",
        "run_failures",
        "interruptions",
        "cancellations",
    ]
    assert [row["source"] for row in breakdown["sources"]] == [
        "gateway",
        "headless",
    ]
    assert breakdown["sources"][0]["affectedRuns"] == 1


def test_failure_unavailable_partial_limits_and_run_status_mismatch() -> None:
    unavailable = project_failure_metric(
        aggregate_run_failures([], run_status="unknown")
    )
    unreadable = project_failure_metric(
        aggregate_run_failures(
            [], run_status="completed", journal_read_failed=True
        )
    )
    limited = project_failure_metric(
        aggregate_run_failures(
            [_run_event("run.queued"), _run_event("run.started")],
            run_status="running",
            max_events=1,
        )
    )
    mismatch = project_failure_metric(
        aggregate_run_failures(
            [_run_event("run.queued"), _run_event("run.completed")],
            run_status="failed",
        )
    )

    assert unavailable["status"] == "unavailable"
    assert unavailable["value"] is None
    assert unreadable["status"] == "unavailable"
    assert unreadable["coverage"]["limited"] is True
    assert limited["status"] == "partial"
    assert mismatch["status"] == "partial"
    assert mismatch["value"]["runFailures"] == 0


def test_run_failure_summary_is_compact_and_never_contains_total_errors() -> None:
    aggregate = aggregate_run_failures(
        [_run_event("run.queued"), _run_event("run.started"), _run_event("run.interrupted")],
        run_status="interrupted",
    )
    summary = project_run_failure_summary(aggregate)

    assert summary == {
        "status": "complete",
        "hasObservedFailure": False,
        "toolErrors": 0,
        "modelFailures": 0,
        "runFailed": False,
        "interrupted": True,
        "cancelled": False,
        "limited": False,
    }
    assert "totalErrors" not in summary
