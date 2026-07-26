from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from minicode.run_journal import RunJournal
from minicode.web.read_model import DashboardReadModel


def _append_attempt(
    journal: RunJournal,
    run_id: str,
    operation_suffix: str,
    *,
    usage: dict[str, object] | None | object = ...,
    duration_ms: int | None = None,
    failed: bool = False,
    cost: dict[str, object] | None | object = ...,
) -> None:
    operation_id = "modelop_" + operation_suffix * 32
    journal.append_event(
        run_id, "model.started", payload={"operationId": operation_id}
    )
    payload: dict[str, object] = {"operationId": operation_id}
    if duration_ms is not None:
        payload["durationMs"] = duration_ms
    if failed:
        payload["failureKind"] = "provider_error"
        journal.append_event(run_id, "model.failed", payload=payload)
        return
    payload.update(
        {
            "resultType": "assistant",
            "contentPresent": True,
            "toolCallCount": 0,
        }
    )
    if usage is not ...:
        payload["usage"] = usage
    journal.append_event(run_id, "model.completed", payload=payload)
    if cost is not ...:
        cost_payload = {
            "costVersion": 1,
            "operationId": operation_id,
            "status": "unavailable",
            "quality": "unavailable",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "reason": "pricing_failed",
        }
        if isinstance(cost, dict):
            cost_payload.update(cost)
        journal.append_event(run_id, "model.costed", payload=cost_payload)


def test_run_detail_aggregates_one_paired_provider_model_call(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Provider usage", source="gateway")
    journal.transition(record.id, "running")
    operation_id = "modelop_" + "a" * 32
    journal.append_event(
        record.id,
        "model.started",
        step=1,
        payload={"operationId": operation_id},
    )
    journal.append_event(
        record.id,
        "model.completed",
        step=1,
        payload={
            "operationId": operation_id,
            "resultType": "assistant",
            "contentPresent": True,
            "toolCallCount": 0,
            "usage": {
                "source": "provider",
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadTokens": 5,
                "cacheCreationTokens": 0,
            },
            "durationMs": 842,
        },
    )
    journal.append_event(
        record.id,
        "model.costed",
        step=1,
        payload={
            "costVersion": 1,
            "operationId": operation_id,
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "catalogModelKey": "openai/gpt-4o",
            "amountNanoUsd": 530_000,
            "components": {
                "inputNanoUsd": 280_000,
                "outputNanoUsd": 240_000,
                "cacheReadNanoUsd": 10_000,
                "cacheCreationNanoUsd": 0,
            },
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)

    completed = next(
        event for event in detail["events"] if event["type"] == "model.completed"
    )
    assert completed["details"]["usage"] == {
        "source": "provider",
        "inputTokens": 100,
        "outputTokens": 20,
        "cacheReadTokens": 5,
        "cacheCreationTokens": 0,
    }
    assert completed["details"]["durationMs"] == 842
    assert detail["metrics"] == {
        "cost": {
            "status": "complete",
            "value": {
                "currency": "USD",
                "amountNanoUsd": "530000",
                "providerUsageNanoUsd": "530000",
                "estimatedUsageNanoUsd": "0",
                "components": {
                    "inputNanoUsd": "280000",
                    "outputNanoUsd": "240000",
                    "cacheReadNanoUsd": "10000",
                    "cacheCreationNanoUsd": "0",
                },
                "pricedCalls": 1,
                "quality": "provider",
                "catalogIds": ["minicode-pricing-2026-07-17-v1"],
            },
            "coverage": {
                "completedCalls": 1,
                "pricedCalls": 1,
                "unavailableCalls": 0,
                "missingCalls": 0,
                "failedAttempts": 0,
                "invalidEvents": 0,
                "duplicateEvents": 0,
                "conflictEvents": 0,
                "orphanEvents": 0,
                "historical": "partial",
                "scope": "retained-run-journal",
                "limited": False,
            },
        },
        "tokens": {
            "status": "live",
            "value": {
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadTokens": 5,
                "cacheCreationTokens": 0,
                "totalTokens": 125,
                "providerCalls": 1,
                "estimatedCalls": 0,
                "unavailableCalls": 0,
                "provenance": "provider",
            },
        },
        "duration": {
            "status": "live",
            "value": {
                "modelCalls": 1,
                "completedCalls": 1,
                "failedCalls": 0,
                "observedCalls": 1,
                "totalMs": 842,
                "averageMs": 842,
            },
        },
        "toolCalls": {
            "status": "unavailable",
            "value": None,
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
        },
        "errors": {
            "status": "complete",
            "value": {
                "affectedRuns": 0,
                "toolErrors": 0,
                "modelFailures": 0,
                "runFailures": 0,
                "interruptedRuns": 0,
                "cancelledRuns": 0,
                "hasObservedFailure": False,
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
        },
        "context": {
            "status": "unavailable",
            "value": None,
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
        },
        "recovery": {
            "status": "unavailable",
            "value": None,
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
        },
        "workingMemory": {
            "status": "unavailable",
            "value": None,
            "coverage": {
                "integrity": "complete",
                "instrumentation": "partial",
                "historical": "partial",
                "scope": "process-local-observation",
                "duplicateEvents": 0,
                "conflictingOperations": 0,
                "orphanEvents": 0,
                "danglingRecoveries": 0,
                "orphanCompletions": 0,
                "invalidEvents": 0,
                "limited": False,
                "summedAcrossRuns": False,
            },
        },
    }
    assert detail["diagnostics"] == []


def test_snapshot_and_ops_share_retained_mixed_usage_and_duration_totals(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Mixed usage", source="headless")
    journal.transition(record.id, "running")
    _append_attempt(
        journal,
        record.id,
        "a",
        usage={
            "source": "provider",
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheReadTokens": 5,
            "cacheCreationTokens": 0,
        },
        duration_ms=100,
        cost={
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "catalogModelKey": "openai/gpt-4o",
            "amountNanoUsd": 100,
            "components": {
                "inputNanoUsd": 100,
                "outputNanoUsd": 0,
                "cacheReadNanoUsd": 0,
                "cacheCreationNanoUsd": 0,
            },
        },
    )
    _append_attempt(
        journal,
        record.id,
        "b",
        usage={
            "source": "estimated",
            "inputTokens": 50,
            "outputTokens": 10,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        },
        duration_ms=200,
        cost={
            "status": "priced",
            "quality": "estimated_usage_catalog_rate",
            "catalogModelKey": "openai/gpt-4o-mini",
            "amountNanoUsd": 200,
            "components": {
                "inputNanoUsd": 0,
                "outputNanoUsd": 200,
                "cacheReadNanoUsd": 0,
                "cacheCreationNanoUsd": 0,
            },
        },
    )
    _append_attempt(
        journal,
        record.id,
        "c",
        usage={
            "source": "unavailable",
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        },
        duration_ms=300,
        cost={"reason": "usage_unavailable"},
    )
    _append_attempt(journal, record.id, "d")
    _append_attempt(
        journal, record.id, "e", duration_ms=400, failed=True
    )
    journal.transition(record.id, "completed")
    model = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    )

    ops = model.ops()
    snapshot = model.snapshot()

    assert ops["source"]["status"] == "partial"
    assert ops["coverage"] == {
        "historical": "partial",
        "scope": "model-usage-duration-cost-tool-failure-context-working-memory",
        "cost": "partial",
        "tools": "unavailable",
        "failures": "complete",
        "context": "unavailable",
        "recovery": "unavailable",
        "workingMemory": "unavailable",
        "runScanLimit": 100,
        "eventScanLimitPerRun": 1_000,
    }
    assert ops["summary"] == {
        "retainedRuns": 1,
        "scannedRuns": 1,
        "completedModelCalls": 4,
        "failedModelCalls": 1,
        "providerCalls": 1,
        "estimatedCalls": 1,
        "unavailableCalls": 2,
        "pricedCalls": 2,
        "unavailableCostCalls": 1,
        "missingCostCalls": 1,
        "invalidCostEvents": 1,
        "observedToolCalls": 0,
        "completedToolCalls": 0,
        "successfulToolCalls": 0,
        "toolErrorCalls": 0,
        "uniqueTools": 0,
        "affectedRuns": 1,
        "modelFailureAttempts": 1,
        "runFailures": 0,
        "interruptedRuns": 0,
        "cancelledRuns": 0,
        "invalidToolEvents": 0,
        "observedCompactions": 0,
        "directCompactions": 0,
        "recoveryCompactions": 0,
        "knownTokensFreed": 0,
        "tokenUnknownCompactions": 0,
        "messagesRemoved": 0,
        "recoveryAttempts": 0,
        "recoveredAttempts": 0,
        "notRecoveredAttempts": 0,
        "workingMemorySnapshots": 0,
        "runsWithWorkingMemorySnapshots": 0,
        "invalidContextEvents": 0,
    }
    assert ops["usage"] == {
        "provider": {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheReadTokens": 5,
            "cacheCreationTokens": 0,
        },
        "estimated": {
            "inputTokens": 50,
            "outputTokens": 10,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        },
        "combined": {
            "status": "partial",
            "inputTokens": 150,
            "outputTokens": 30,
            "cacheReadTokens": 5,
            "cacheCreationTokens": 0,
            "totalTokens": 185,
            "provenance": "mixed",
        },
    }
    assert ops["duration"] == {
        "status": "partial",
        "modelCalls": 5,
        "completedCalls": 4,
        "failedCalls": 1,
        "observedCalls": 4,
        "totalMs": 1_000,
        "averageMs": 250,
    }
    assert ops["cost"]["status"] == "partial"
    assert ops["cost"]["value"] == {
        "currency": "USD",
        "amountNanoUsd": "300",
        "providerUsageNanoUsd": "100",
        "estimatedUsageNanoUsd": "200",
        "components": {
            "inputNanoUsd": "100",
            "outputNanoUsd": "200",
            "cacheReadNanoUsd": "0",
            "cacheCreationNanoUsd": "0",
        },
        "pricedCalls": 2,
        "quality": "mixed",
        "catalogIds": ["minicode-pricing-2026-07-17-v1"],
    }
    assert ops["cost"]["coverage"] == {
        "completedCalls": 4,
        "pricedCalls": 2,
        "unavailableCalls": 1,
        "missingCalls": 1,
        "failedAttempts": 1,
        "invalidEvents": 1,
        "duplicateEvents": 0,
        "conflictEvents": 0,
        "orphanEvents": 0,
        "historical": "partial",
        "scope": "retained-run-journal",
        "limited": False,
    }
    assert ops["costBreakdown"]["quality"] == [
        {
            "quality": "estimated_usage_catalog_rate",
            "pricedCalls": 1,
            "amountNanoUsd": "200",
        },
        {
            "quality": "provider_usage_catalog_rate",
            "pricedCalls": 1,
            "amountNanoUsd": "100",
        },
    ]
    overview_usage = snapshot["overview"]["usage"]
    assert overview_usage["status"] == "partial"
    assert overview_usage["inputTokens"] == 150
    assert overview_usage["outputTokens"] == 30
    assert overview_usage["cacheReadTokens"] == 5
    assert overview_usage["cacheCreationTokens"] == 0
    assert overview_usage["providerCalls"] == 1
    assert overview_usage["estimatedCalls"] == 1
    assert overview_usage["unavailableCalls"] == 2
    assert overview_usage["durationMs"] == 1_000
    assert overview_usage["costUsd"] is None
    assert overview_usage["cost"] == ops["cost"]
    assert snapshot["sources"]["usage"]["status"] == "partial"


def test_snapshot_and_ops_share_tool_and_failure_aggregates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)

    recovered = journal.create_run(title="Recovered", source="gateway")
    journal.transition(recovered.id, "running")
    for suffix, name, outcome in (
        ("a", "read_file", "success"),
        ("b", "run_command", "error"),
    ):
        operation_id = "toolop_" + suffix * 32
        journal.append_event(
            recovered.id,
            "tool.started",
            payload={"toolName": name, "operationId": operation_id},
        )
        journal.append_event(
            recovered.id,
            "tool.finished",
            payload={
                "toolName": name,
                "operationId": operation_id,
                "outcome": outcome,
                "paired": True,
            },
        )
    _append_attempt(journal, recovered.id, "c", failed=True)
    journal.transition(recovered.id, "completed")

    failed = journal.create_run(title="Terminal failed", source="headless")
    journal.transition(failed.id, "running")
    journal.append_event(
        failed.id,
        "tool.finished",
        payload={
            "toolName": "read_file",
            "outcome": "error",
            "paired": False,
        },
    )
    journal.transition(failed.id, "failed", reason="Bearer hidden-reason")

    model = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    )
    ops = model.ops()
    overview = model.snapshot()["overview"]["usage"]

    assert ops["coverage"]["scope"] == "model-usage-duration-cost-tool-failure-context-working-memory"
    assert ops["tools"]["status"] == "partial"
    assert ops["tools"]["value"] == {
        "observedCalls": 3,
        "startedCalls": 2,
        "completedCalls": 3,
        "pairedCalls": 2,
        "successfulCalls": 1,
        "errorCalls": 2,
        "uniqueTools": 2,
    }
    assert ops["failures"]["status"] == "partial"
    assert ops["failures"]["value"] == {
        "affectedRuns": 2,
        "toolErrors": 2,
        "modelFailures": 1,
        "runFailures": 1,
        "interruptedRuns": 0,
        "cancelledRuns": 0,
        "hasObservedFailure": True,
    }
    assert ops["summary"]["observedToolCalls"] == 3
    assert ops["summary"]["completedToolCalls"] == 3
    assert ops["summary"]["successfulToolCalls"] == 1
    assert ops["summary"]["toolErrorCalls"] == 2
    assert ops["summary"]["uniqueTools"] == 2
    assert ops["summary"]["affectedRuns"] == 2
    assert ops["summary"]["modelFailureAttempts"] == 1
    assert ops["summary"]["runFailures"] == 1
    assert "totalErrors" not in ops["summary"]
    assert [row["toolName"] for row in ops["toolBreakdown"]["tools"]] == [
        "read_file",
        "run_command",
    ]
    assert ops["failureBreakdown"]["categories"][:3] == [
        {"category": "tool_errors", "count": 2},
        {"category": "model_failures", "count": 1},
        {"category": "run_failures", "count": 1},
    ]
    assert overview["tools"] == ops["tools"]
    assert overview["failures"] == ops["failures"]
    assert overview["toolCalls"] is None
    assert overview["errors"] is None
    assert "hidden-reason" not in json.dumps({"ops": ops, "overview": overview})

    restarted_ops = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        run_journal=RunJournal(workspace, data_dir=data_dir),
    ).ops()
    for key in ("coverage", "summary", "usage", "duration", "cost"):
        assert restarted_ops[key] == ops[key]


def test_historical_completed_call_is_unavailable_not_zero(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Historical", source="tui")
    journal.transition(record.id, "running")
    _append_attempt(journal, record.id, "a")
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)

    assert detail["metrics"]["tokens"] == {
        "status": "unavailable",
        "value": {
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
            "totalTokens": None,
            "providerCalls": 0,
            "estimatedCalls": 0,
            "unavailableCalls": 1,
            "provenance": "unavailable",
        },
    }
    assert detail["metrics"]["duration"] == {
        "status": "unavailable",
        "value": {
            "modelCalls": 1,
            "completedCalls": 1,
            "failedCalls": 0,
            "observedCalls": 0,
            "totalMs": None,
            "averageMs": None,
        },
    }
    assert {item["code"] for item in detail["diagnostics"]} == {
        "cost_quality_mismatch",
        "cost_operation_missing",
    }
    assert detail["metrics"]["cost"]["status"] == "unavailable"
    assert detail["metrics"]["cost"]["value"] is None
    assert detail["metrics"]["cost"]["coverage"]["missingCalls"] == 1


def test_invalid_duplicate_and_unpaired_model_events_are_localized(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Damaged observations", source="gateway")
    journal.transition(record.id, "running")
    valid_id = "modelop_" + "a" * 32
    journal.append_event(
        record.id, "model.started", payload={"operationId": valid_id}
    )
    invalid_usage = {
        "source": "provider",
        "inputTokens": True,
        "outputTokens": -1,
        "cacheReadTokens": 1_000_000_001,
        "cacheCreationTokens": None,
        "secret": "password=usage-secret",
    }
    journal.append_event(
        record.id,
        "model.completed",
        payload={
            "operationId": valid_id,
            "resultType": "assistant",
            "contentPresent": True,
            "toolCallCount": 0,
            "usage": invalid_usage,
            "durationMs": True,
        },
    )
    journal.append_event(
        record.id,
        "model.completed",
        payload={
            "operationId": valid_id,
            "usage": {
                "source": "provider",
                "inputTokens": 999,
                "outputTokens": 999,
                "cacheReadTokens": 0,
                "cacheCreationTokens": 0,
            },
            "durationMs": 999,
        },
    )
    journal.append_event(
        record.id,
        "model.failed",
        payload={
            "operationId": "modelop_" + "b" * 32,
            "failureKind": "provider_error",
            "durationMs": 77,
            "error": "Bearer failure-secret",
        },
    )
    journal.append_event(
        record.id,
        "model.started",
        payload={"operationId": "modelop_" + "c" * 32},
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)

    assert detail["metrics"]["tokens"]["status"] == "unavailable"
    assert detail["metrics"]["tokens"]["value"]["unavailableCalls"] == 1
    assert detail["metrics"]["duration"]["value"]["modelCalls"] == 1
    assert detail["metrics"]["duration"]["value"]["observedCalls"] == 0
    assert {item["code"] for item in detail["diagnostics"]} == {
        "model_duration_invalid",
        "model_usage_invalid",
        "model_operation_duplicate",
        "model_operation_unpaired",
        "cost_quality_mismatch",
        "cost_operation_duplicate",
        "cost_operation_unpaired",
        "cost_operation_missing",
        "failure_event_invalid",
    }
    serialized = json.dumps(detail)
    for secret in ("usage-secret", "failure-secret", '"secret"'):
        assert secret not in serialized
    assert "999" not in json.dumps(detail["metrics"])


def test_ops_limits_run_and_event_scans_and_isolates_one_failed_run() -> None:
    def event(sequence: int, event_type: str, operation_id: str, payload=None):
        return SimpleNamespace(
            sequence=sequence,
            type=event_type,
            payload={"operationId": operation_id, **(payload or {})},
        )

    valid_run = SimpleNamespace(
        id="run_" + "a" * 32,
        updated_at="2026-07-17T10:00:00.000Z",
    )
    failed_run = SimpleNamespace(
        id="run_" + "b" * 32,
        updated_at="2026-07-17T09:00:00.000Z",
    )
    operation_id = "modelop_" + "c" * 32
    first_events = [
        event(1, "model.started", operation_id),
        event(
            2,
            "model.completed",
            operation_id,
            {
                "usage": {
                    "source": "provider",
                    "inputTokens": 7,
                    "outputTokens": 3,
                    "cacheReadTokens": 0,
                    "cacheCreationTokens": 0,
                },
                "durationMs": 25,
            },
        ),
    ]

    class FakeJournal:
        event_calls = 0

        def list_runs(self, *, limit):
            assert limit == 100
            return SimpleNamespace(
                items=(valid_run, failed_run),
                known_total=101,
                has_more=True,
                diagnostics=(),
            )

        def list_events(self, run_id, *, limit, cursor=None):
            self.event_calls += 1
            if run_id == failed_run.id:
                raise OSError("/private/path password=journal-secret")
            assert 1 <= limit <= 100
            if cursor is None:
                return SimpleNamespace(
                    items=tuple(first_events),
                    has_more=True,
                    next_cursor="next",
                    diagnostics=(),
                )
            return SimpleNamespace(
                items=tuple(
                    event(index + 3, "run.started", operation_id)
                    for index in range(limit)
                ),
                has_more=True,
                next_cursor=f"next-{self.event_calls}",
                diagnostics=(),
            )

    journal = FakeJournal()
    model = DashboardReadModel.__new__(DashboardReadModel)
    model._run_journal = journal
    model._clock = lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )

    ops = model.ops()

    assert ops["source"]["status"] == "partial"
    assert ops["summary"]["retainedRuns"] == 101
    assert ops["summary"]["scannedRuns"] == 2
    assert ops["summary"]["providerCalls"] == 1
    assert ops["usage"]["combined"]["totalTokens"] == 10
    assert ops["duration"]["totalMs"] == 25
    assert journal.event_calls == 12
    assert {item["code"] for item in ops["diagnostics"]} == {
        "usage_runs_limited",
        "usage_events_limited",
        "run_usage_read_failed",
        "cost_scan_limited",
        "cost_operation_missing",
        "cost_journal_read_failed",
        "tool_scan_limited",
        "tool_journal_read_failed",
        "failure_scan_limited",
        "failure_journal_read_failed",
        "failure_event_invalid",
        "context_scan_limited",
        "context_journal_read_failed",
        "working_memory_scan_limited",
    }
    assert "journal-secret" not in json.dumps(ops)
    assert "/private/" not in json.dumps(ops)
