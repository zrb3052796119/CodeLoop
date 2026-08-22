from __future__ import annotations

import json
import base64
from pathlib import Path

import pytest

from minicode.run_journal import RunJournal
from minicode.web.read_model import DashboardReadError, DashboardReadModel, _run_event_details


def _cursor(*values: object) -> str:
    raw = json.dumps(list(values), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_runs_page_returns_real_empty_journal_with_trace_coverage(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    payload = DashboardReadModel(workspace, data_dir=data_dir).runs()

    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["source"]["status"] == "live"
    assert "historical Runs were not backfilled" in payload["source"]["message"]
    assert payload["coverage"] == {
        "journal": "live",
        "tui": "live",
        "headless": "live",
        "gateway": "live",
        "historical": "partial",
        "scope": "lifecycle-model-usage-cost-tool-assistant-skill-memory-context",
        "model": "live",
        "tool": "live",
        "assistant": "live",
        "usage": "live",
        "cost": "live",
        "memory": "live",
        "skills": "live",
        "context": "partial",
        "workingMemory": "partial",
        "mcpRuntime": "partial",
        "mcpRuntimeScope": "run-scoped observation",
        "mcpRuntimeHistorical": "partial",
        "mcpRuntimeCurrent": "unavailable",
        "mcpRuntimeCrossProcess": "unavailable",
    }
    assert payload["summary"]["knownTotal"] == 0
    assert payload["items"] == []
    assert payload["page"] == {
        "limit": 20,
        "hasMore": False,
        "nextCursor": None,
    }
    assert payload["filters"] == {"status": None, "source": None}
    assert payload["diagnostics"] == []
    assert not data_dir.exists()
    assert json.loads(json.dumps(payload)) == payload


def test_runs_list_cost_summary_matches_full_run_detail_metric(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    priced = journal.create_run(title="Priced", source="gateway")
    journal.transition(priced.id, "running")
    operation_id = "modelop_" + "a" * 32
    journal.append_event(
        priced.id, "model.started", payload={"operationId": operation_id}
    )
    journal.append_event(
        priced.id,
        "model.completed",
        payload={
            "operationId": operation_id,
            "usage": {
                "source": "provider",
                "inputTokens": 120,
                "outputTokens": 24,
                "cacheReadTokens": 8,
                "cacheCreationTokens": 0,
            },
        },
    )
    journal.append_event(
        priced.id,
        "model.costed",
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
    journal.transition(priced.id, "completed")
    missing = journal.create_run(title="Historical", source="tui")
    journal.transition(missing.id, "running")
    missing_operation = "modelop_" + "b" * 32
    journal.append_event(
        missing.id, "model.started", payload={"operationId": missing_operation}
    )
    journal.append_event(
        missing.id,
        "model.completed",
        payload={
            "operationId": missing_operation,
            "usage": {
                "source": "provider",
                "inputTokens": 1,
                "outputTokens": 1,
                "cacheReadTokens": 0,
                "cacheCreationTokens": 0,
            },
        },
    )
    journal.transition(missing.id, "completed")
    model = DashboardReadModel(workspace, data_dir=data_dir, run_journal=journal)

    items = {item["id"]: item for item in model.runs(limit=100)["items"]}
    detail = model.run_detail(priced.id, limit=1)

    assert items[priced.id]["cost"] == {
        "status": "complete",
        "amountNanoUsd": "530000",
        "currency": "USD",
        "pricedCalls": 1,
        "unpricedCalls": 0,
        "failedAttempts": 0,
        "limited": False,
    }
    assert items[missing.id]["cost"] == {
        "status": "unavailable",
        "amountNanoUsd": None,
        "currency": "USD",
        "pricedCalls": 0,
        "unpricedCalls": 1,
        "failedAttempts": 0,
        "limited": False,
    }
    assert (
        items[priced.id]["cost"]["amountNanoUsd"]
        == detail["metrics"]["cost"]["value"]["amountNanoUsd"]
    )


def test_one_run_cost_read_failure_is_local_to_that_list_item(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    healthy = journal.create_run(title="Healthy", source="gateway")
    journal.transition(healthy.id, "running")
    operation_id = "modelop_" + "a" * 32
    journal.append_event(
        healthy.id, "model.started", payload={"operationId": operation_id}
    )
    journal.append_event(
        healthy.id,
        "model.completed",
        payload={
            "operationId": operation_id,
            "usage": {
                "source": "provider",
                "inputTokens": 1,
                "outputTokens": 1,
                "cacheReadTokens": 0,
                "cacheCreationTokens": 0,
            },
        },
    )
    journal.append_event(
        healthy.id,
        "model.costed",
        payload={
            "costVersion": 1,
            "operationId": operation_id,
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "catalogModelKey": "openai/gpt-4o",
            "amountNanoUsd": 300,
            "components": {
                "inputNanoUsd": 100,
                "outputNanoUsd": 200,
                "cacheReadNanoUsd": 0,
                "cacheCreationNanoUsd": 0,
            },
        },
    )
    journal.transition(healthy.id, "completed")
    damaged = journal.create_run(title="Damaged", source="tui")

    class OneRunFails:
        def list_runs(self, **kwargs):
            return journal.list_runs(**kwargs)

        def list_events(self, run_id, **kwargs):
            if run_id == damaged.id:
                raise OSError("Bearer one-run-secret")
            return journal.list_events(run_id, **kwargs)

    payload = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=OneRunFails()
    ).runs(limit=100)
    items = {item["id"]: item for item in payload["items"]}

    assert payload["source"]["status"] == "partial"
    assert items[healthy.id]["cost"]["amountNanoUsd"] == "300"
    assert items[damaged.id]["cost"] == {
        "status": "unavailable",
        "amountNanoUsd": None,
        "currency": "USD",
        "pricedCalls": 0,
        "unpricedCalls": 0,
        "failedAttempts": 0,
        "limited": True,
    }
    assert any(
        item["code"] == "cost_journal_read_failed"
        for item in payload["diagnostics"]
    )
    assert "one-run-secret" not in json.dumps(payload)


def test_runs_list_and_detail_project_only_safe_journal_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    completed = journal.create_run(
        title="Use api_key=hidden-value",
        source="gateway",
        metadata={"origin": "provider credential=secret"},
    )
    journal.transition(completed.id, "running")
    journal.append_event(
        completed.id,
        "model.completed",
        step=1,
        payload={
            "summary": "Bearer very-secret-token",
            "toolOutput": "sk-test-secret",
        },
    )
    completed = journal.transition(completed.id, "completed")
    failed = journal.create_run(title="Failed", source="tui")
    failed = journal.transition(failed.id, "failed", reason="password=failure-secret")
    model = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        run_journal=journal,
    )

    first = model.runs(limit=1)
    second = model.runs(limit=1, cursor=first["page"]["nextCursor"])
    filtered = model.runs(status="completed", source="gateway")
    detail = model.run_detail(completed.id, limit=2)
    detail_next = model.run_detail(
        completed.id,
        limit=2,
        cursor=detail["page"]["nextCursor"],
    )

    assert [item["id"] for item in first["items"] + second["items"]] == [
        failed.id,
        completed.id,
    ]
    assert first["summary"]["knownTotal"] == 2
    assert first["summary"]["byStatus"]["completed"] == 1
    assert first["summary"]["byStatus"]["failed"] == 1
    assert [item["id"] for item in filtered["items"]] == [completed.id]
    assert filtered["filters"] == {"status": "completed", "source": "gateway"}
    assert detail["run"]["id"] == completed.id
    assert detail["run"]["eventCount"] == 4
    assert [event["sequence"] for event in detail["events"] + detail_next["events"]] == [
        1,
        2,
        3,
        4,
    ]
    assert [event["summary"] for event in detail["events"]] == [
        "Run queued",
        "Run started",
    ]
    assert detail["coverage"] == {
        "journal": "live",
        "tui": "live",
        "headless": "live",
        "gateway": "live",
        "historical": "partial",
        "scope": "lifecycle-model-usage-cost-tool-assistant-skill-memory-context",
        "model": "live",
        "tool": "live",
        "assistant": "live",
        "usage": "live",
        "cost": "live",
        "memory": "live",
        "skills": "live",
        "context": "partial",
        "workingMemory": "partial",
        "mcpRuntime": "partial",
        "mcpRuntimeScope": "run-scoped observation",
        "mcpRuntimeHistorical": "partial",
        "mcpRuntimeCurrent": "unavailable",
        "mcpRuntimeCrossProcess": "unavailable",
    }
    assert detail["metrics"]["cost"]["status"] == "unavailable"
    assert detail["metrics"]["cost"]["value"] is None
    assert detail["metrics"]["cost"]["coverage"]["invalidEvents"] == 1
    for name in ("tokens", "duration"):
        assert detail["metrics"][name] == {"status": "unavailable", "value": None}
    assert detail["metrics"]["toolCalls"]["status"] == "unavailable"
    assert detail["metrics"]["toolCalls"]["value"] is None
    assert detail["metrics"]["errors"]["status"] == "partial"
    assert detail["metrics"]["errors"]["value"]["hasObservedFailure"] is False
    serialized = json.dumps([first, second, filtered, detail, detail_next])
    for forbidden in (
        "hidden-value",
        "very-secret-token",
        "sk-test-secret",
        "failure-secret",
        "provider credential",
        '"payload"',
        '"metadata"',
        '"workspaceId"',
        '"errorCount"',
        str(data_dir),
    ):
        assert forbidden not in serialized

    for run_id, status, code in (
        ("../secret", 400, "invalid_run_id"),
        ("run_" + "f" * 32, 404, "run_not_found"),
    ):
        with pytest.raises(DashboardReadError) as error:
            model.run_detail(run_id)
        assert (error.value.status, error.value.code) == (status, code)


def test_run_detail_projects_only_safe_mcp_runtime_details(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="MCP runtime", source="headless")
    journal.transition(record.id, "running")
    journal.append_event(
        record.id,
        "mcp.runtime.observed",
        step=4,
        payload={
            "mcpVersion": 1,
            "serverKey": "mcpsrv_" + "b" * 32,
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_failed",
            "connectionAttempted": False,
            "protocol": "newline-json",
            "failureKind": "request_error",
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(workspace, data_dir=data_dir).run_detail(record.id, limit=20)
    event = next(item for item in detail["events"] if item["type"] == "mcp.runtime.observed")

    assert event["summary"] == "MCP runtime observed"
    assert event["step"] == 4
    assert event["details"] == {
        "mcpVersion": 1,
        "serverKey": "mcpsrv_" + "b" * 32,
        "transport": "stdio",
        "activity": "tool_request",
        "outcome": "request_failed",
        "connectionAttempted": False,
        "protocol": "newline-json",
        "failureKind": "request_error",
    }
    assert detail["coverage"]["mcpRuntime"] == "partial"
    assert detail["coverage"]["mcpRuntimeCurrent"] == "unavailable"


def test_run_detail_degrades_invalid_mcp_runtime_payload_without_echoing_secrets(tmp_path: Path) -> None:
    payload = {
        "mcpVersion": 99,
        "serverKey": "raw-server-secret",
        "transport": "stdio",
        "activity": "tool_request",
        "outcome": "online",
        "connectionAttempted": True,
        "command": "Bearer dashboard-secret",
        "exception": "stack trace dashboard-secret",
    }

    assert _run_event_details("mcp.runtime.observed", payload) == {}


def test_run_detail_strictly_projects_task_correlated_skill_attribution() -> None:
    payload = {
        "attributionVersion": 1,
        "attributionKind": "task_correlation",
        "outcomeStatus": "success",
        "goalAchieved": True,
        "hadToolErrors": True,
        "errorsRecovered": True,
        "toolErrorCount": 1,
        "loadedSkillCount": 1,
        "loadedSkills": [
            {
                "qualifiedName": "project/memory-audit",
                "source": "project",
                "directory": "project",
                "contentDigest": "a" * 64,
                "path": "/private/path-secret",
                "content": "password=skill-secret",
            }
        ],
        "loadedSkillsTruncated": False,
        "taskText": "password=task-secret",
        "modelResponse": "Bearer response-secret",
    }

    details = _run_event_details("skill.attributed", payload)

    assert details == {
        "attributionVersion": 1,
        "attributionKind": "task_correlation",
        "outcomeStatus": "success",
        "goalAchieved": True,
        "hadToolErrors": True,
        "errorsRecovered": True,
        "toolErrorCount": 1,
        "loadedSkillCount": 1,
        "loadedSkills": [
            {
                "qualifiedName": "project/memory-audit",
                "source": "project",
                "directory": "project",
                "contentDigest": "a" * 64,
            }
        ],
        "loadedSkillsTruncated": False,
    }
    serialized = json.dumps(details)
    for forbidden in (
        "/private/",
        "skill-secret",
        "task-secret",
        "response-secret",
    ):
        assert forbidden not in serialized


def test_run_detail_strictly_projects_canonical_task_outcome() -> None:
    payload = {
        "outcomeVersion": 1,
        "outcomeStatus": "success",
        "goalAchieved": True,
        "learningSuccess": True,
        "hadToolErrors": True,
        "errorsRecovered": True,
        "toolErrorCount": 1,
        "taskText": "password=task-secret",
        "modelResponse": "Bearer response-secret",
    }

    details = _run_event_details("task.outcome", payload)

    assert details == {
        "outcomeVersion": 1,
        "outcomeStatus": "success",
        "goalAchieved": True,
        "learningSuccess": True,
        "hadToolErrors": True,
        "errorsRecovered": True,
        "toolErrorCount": 1,
    }
    serialized = json.dumps(details)
    assert "task-secret" not in serialized
    assert "response-secret" not in serialized


def test_run_detail_strictly_projects_execution_stop_without_content() -> None:
    payload = {
        "reasonCode": "consecutive_tool_failures",
        "stepCount": 5,
        "toolErrorCount": 5,
        "consecutiveFailedSteps": 5,
        "userActionRequired": True,
        "command": "password=task-secret",
        "error": "Bearer response-secret",
    }

    details = _run_event_details("execution.stopped", payload)

    assert details == {
        "reasonCode": "consecutive_tool_failures",
        "stepCount": 5,
        "toolErrorCount": 5,
        "consecutiveFailedSteps": 5,
        "userActionRequired": True,
    }
    serialized = json.dumps(details)
    assert "task-secret" not in serialized
    assert "response-secret" not in serialized


def test_run_detail_projects_versioned_skill_routing_digests() -> None:
    payload = {
        "routingVersion": 2,
        "intentType": "review",
        "actionType": "analyze",
        "totalSkills": 1,
        "selectedCount": 1,
        "selected": [
            {
                "qualifiedName": "project/memory-audit",
                "source": "project",
                "directory": "project",
                "score": 4.25,
                "contentDigest": "a" * 64,
                "content": "password=skill-secret",
                "path": "/private/SKILL.md",
            }
        ],
        "selectedTruncated": False,
        "usedFallback": False,
    }

    details = _run_event_details("skill.routed", payload)

    assert details["routingVersion"] == 2
    assert details["selected"] == [
        {
            "qualifiedName": "project/memory-audit",
            "source": "project",
            "directory": "project",
            "score": 4.25,
            "contentDigest": "a" * 64,
        }
    ]
    serialized = json.dumps(details)
    assert "skill-secret" not in serialized
    assert "/private/" not in serialized


def test_run_detail_rejects_inconsistent_skill_attribution() -> None:
    valid = {
        "attributionVersion": 1,
        "attributionKind": "task_correlation",
        "outcomeStatus": "success",
        "goalAchieved": True,
        "hadToolErrors": False,
        "errorsRecovered": False,
        "toolErrorCount": 0,
        "loadedSkillCount": 1,
        "loadedSkills": [
            {
                "qualifiedName": "memory-audit",
                "source": "project",
                "directory": "",
                "contentDigest": "a" * 64,
            }
        ],
        "loadedSkillsTruncated": False,
    }
    invalid_payloads = [
        {**valid, "outcomeStatus": "password=invalid"},
        {**valid, "goalAchieved": False},
        {**valid, "hadToolErrors": True},
        {**valid, "errorsRecovered": True},
        {**valid, "loadedSkillCount": 2},
        {
            **valid,
            "hadToolErrors": True,
            "toolErrorCount": 1,
            "errorsRecovered": False,
        },
        {
            **valid,
            "loadedSkills": [
                {
                    **valid["loadedSkills"][0],
                    "contentDigest": "password=invalid",
                }
            ],
        },
    ]

    assert all(
        _run_event_details("skill.attributed", payload) == {}
        for payload in invalid_payloads
    )


def test_run_detail_exposes_skill_attribution_summary(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Use a Skill", source="headless")
    journal.transition(record.id, "running")
    journal.append_event(
        record.id,
        "skill.attributed",
        step=3,
        payload={
            "attributionVersion": 1,
            "attributionKind": "task_correlation",
            "outcomeStatus": "success",
            "goalAchieved": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
            "loadedSkillCount": 1,
            "loadedSkills": [
                {
                    "qualifiedName": "memory-audit",
                    "source": "project",
                    "directory": "",
                    "contentDigest": "a" * 64,
                }
            ],
            "loadedSkillsTruncated": False,
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        run_journal=journal,
    ).run_detail(record.id, limit=20)
    event = next(
        item for item in detail["events"] if item["type"] == "skill.attributed"
    )

    assert event["summary"] == "Skill outcome attributed"
    assert event["details"]["loadedSkillCount"] == 1
    assert event["details"]["outcomeStatus"] == "success"


def test_run_detail_whitelists_tool_and_assistant_event_details(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Trace", source="headless")
    journal.transition(record.id, "running")
    operation_id = "toolop_" + "a" * 32
    journal.append_event(
        record.id,
        "tool.started",
        payload={
            "toolName": "read_file",
            "operationId": operation_id,
            "toolInput": "password=input-secret",
            "path": "/private/path-secret",
        },
    )
    journal.append_event(
        record.id,
        "tool.finished",
        payload={
            "toolName": "read_file",
            "operationId": operation_id,
            "outcome": "error",
            "paired": True,
            "toolOutput": "Bearer output-secret",
            "error": "failure-secret",
        },
    )
    journal.append_event(
        record.id,
        "assistant.completed",
        payload={
            "contentPresent": True,
            "contentLength": 428,
            "kind": "returned_assistant",
            "content": "password=assistant-secret",
        },
    )
    journal.append_event(
        record.id,
        "tool.finished",
        payload={
            "toolName": "<script>unsafe</script>",
            "operationId": "original-call-id",
            "outcome": ["fatal"],
            "paired": "yes",
            "command": "rm private-command-secret",
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)
    trace_events = detail["events"][2:6]

    assert trace_events[0]["details"] == {
        "toolName": "read_file",
    }
    assert trace_events[1]["details"] == {
        "toolName": "read_file",
        "outcome": "error",
        "paired": True,
    }
    assert trace_events[2]["details"] == {
        "contentPresent": True,
        "contentLength": 428,
        "kind": "returned_assistant",
    }
    assert trace_events[3]["details"] == {}
    serialized = json.dumps(detail)
    for forbidden in (
        "input-secret",
        "path-secret",
        "output-secret",
        "failure-secret",
        "assistant-secret",
        "private-command-secret",
        '"payload"',
        "original-call-id",
        "<script>",
    ):
        assert forbidden not in serialized
    assert detail["metrics"]["cost"]["status"] == "unavailable"
    assert detail["metrics"]["cost"]["value"] is None
    assert detail["metrics"]["cost"]["coverage"]["completedCalls"] == 0
    for name in ("tokens", "duration"):
        assert detail["metrics"][name] == {"status": "unavailable", "value": None}
    assert detail["metrics"]["toolCalls"]["status"] == "unavailable"
    assert detail["metrics"]["toolCalls"]["coverage"]["invalidEvents"] == 3
    assert detail["metrics"]["errors"]["status"] == "partial"
    assert detail["metrics"]["errors"]["value"]["toolErrors"] == 0


def test_runs_list_and_detail_share_tool_and_failure_metrics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Tool facts", source="gateway")
    journal.transition(record.id, "running")
    for suffix, name, outcome in (
        ("a", "read_file", "success"),
        ("b", "run_command", "error"),
    ):
        operation_id = "toolop_" + suffix * 32
        journal.append_event(
            record.id,
            "tool.started",
            payload={"toolName": name, "operationId": operation_id},
        )
        journal.append_event(
            record.id,
            "tool.finished",
            payload={
                "toolName": name,
                "operationId": operation_id,
                "outcome": outcome,
                "paired": True,
            },
        )
    journal.transition(record.id, "completed")
    model = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    )

    item = model.runs(limit=20)["items"][0]
    detail = model.run_detail(record.id, limit=2)

    assert item["tools"] == {
        "status": "complete",
        "observedCalls": 2,
        "errorCalls": 1,
        "uniqueTools": 2,
        "limited": False,
    }
    assert item["failures"] == {
        "status": "complete",
        "hasObservedFailure": True,
        "toolErrors": 1,
        "modelFailures": 0,
        "runFailed": False,
        "interrupted": False,
        "cancelled": False,
        "limited": False,
    }
    assert detail["metrics"]["toolCalls"]["status"] == "complete"
    assert detail["metrics"]["toolCalls"]["value"]["observedCalls"] == 2
    assert detail["metrics"]["toolCalls"]["value"]["errorCalls"] == 1
    assert detail["metrics"]["errors"]["status"] == "complete"
    assert detail["metrics"]["errors"]["value"]["affectedRuns"] == 1
    assert detail["metrics"]["errors"]["value"]["toolErrors"] == 1
    assert detail["page"]["hasMore"] is True


def test_run_detail_whitelists_model_event_details_and_preserves_step(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Model trace", source="gateway")
    journal.transition(record.id, "running")
    operation_id = "modelop_" + "a" * 32
    journal.append_event(
        record.id,
        "model.started",
        step=4,
        payload={
            "operationId": operation_id,
            "prompt": "password=prompt-secret",
            "providerRequestId": "provider-secret",
        },
    )
    journal.append_event(
        record.id,
        "model.completed",
        step=4,
        payload={
            "operationId": operation_id,
            "resultType": "tool_calls",
            "contentPresent": False,
            "toolCallCount": 2,
            "output": "Bearer output-secret",
            "usage": {"tokens": 999},
            "duration": 12.5,
        },
    )
    journal.append_event(
        record.id,
        "model.failed",
        step=5,
        payload={
            "operationId": "modelop_" + "b" * 32,
            "failureKind": "network",
            "error": "api_key=error-secret",
        },
    )
    journal.append_event(
        record.id,
        "model.completed",
        step=6,
        payload={
            "operationId": "provider-request-id",
            "resultType": "stream",
            "contentPresent": 1,
            "toolCallCount": True,
            "messages": ["password=message-secret"],
        },
    )
    journal.append_event(
        record.id,
        "model.failed",
        step=7,
        payload={
            "operationId": "modelop_" + "c" * 32,
            "failureKind": "unknown",
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)
    model_events = detail["events"][2:7]

    assert [event["step"] for event in model_events] == [4, 4, 5, 6, 7]
    assert model_events[0]["details"] == {"operationId": operation_id}
    assert model_events[1]["details"] == {
        "operationId": operation_id,
        "resultType": "tool_calls",
        "contentPresent": False,
        "toolCallCount": 2,
        "usage": {
            "source": "unavailable",
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        },
    }
    assert model_events[2]["details"] == {
        "operationId": "modelop_" + "b" * 32,
        "failureKind": "network",
    }
    assert model_events[3]["details"] == {
        "usage": {
            "source": "unavailable",
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        }
    }
    assert model_events[4]["details"] == {
        "operationId": "modelop_" + "c" * 32
    }
    serialized = json.dumps(detail)
    for forbidden in (
        "prompt-secret",
        "provider-secret",
        "output-secret",
        '"tokens": 999',
        '"duration": 12.5',
        "error-secret",
        "message-secret",
        "provider-request-id",
        '"payload"',
    ):
        assert forbidden not in serialized
    assert detail["metrics"]["cost"]["status"] == "unavailable"
    assert detail["metrics"]["tokens"]["status"] == "unavailable"
    assert detail["metrics"]["toolCalls"]["status"] == "unavailable"


def test_run_detail_strictly_projects_cost_events_without_aggregation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Cost trace", source="gateway")
    journal.transition(record.id, "running")
    operation_id = "modelop_" + "d" * 32
    journal.append_event(
        record.id,
        "model.costed",
        step=3,
        payload={
            "costVersion": 1,
            "operationId": operation_id,
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "catalogModelKey": "openai/gpt-4o",
            "amountNanoUsd": 300,
            "components": {
                "inputNanoUsd": 100,
                "outputNanoUsd": 150,
                "cacheReadNanoUsd": 50,
                "cacheCreationNanoUsd": 0,
                "password": "component-secret",
            },
            "providerModel": "sk-raw-model-secret",
            "prompt": "password=prompt-secret",
            "endpoint": "https://private.example.invalid",
        },
    )
    journal.append_event(
        record.id,
        "model.costed",
        step=4,
        payload={
            "costVersion": 1,
            "operationId": "modelop_" + "e" * 32,
            "status": "unavailable",
            "quality": "unavailable",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "reason": "model_unpriced",
            "catalogModelKey": "custom/password=raw-model-secret",
            "model": "sk-raw-model-secret",
        },
    )
    journal.append_event(
        record.id,
        "model.costed",
        step=5,
        payload={
            "costVersion": 1,
            "operationId": "modelop_" + "f" * 32,
            "status": "priced",
            "quality": "estimated_usage_catalog_rate",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "catalogModelKey": "openai/gpt-4o-mini",
            "amountNanoUsd": True,
            "components": {
                "inputNanoUsd": 1,
                "outputNanoUsd": 1,
                "cacheReadNanoUsd": 1,
                "cacheCreationNanoUsd": 1,
            },
            "error": "api_key=projection-secret",
        },
    )
    journal.append_event(
        record.id,
        "model.costed",
        step=6,
        payload={
            "costVersion": 1,
            "operationId": "modelop_" + "1" * 32,
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "catalogModelKey": "openai/gpt-4o",
            "amountNanoUsd": 301,
            "components": {
                "inputNanoUsd": 100,
                "outputNanoUsd": 150,
                "cacheReadNanoUsd": 50,
                "cacheCreationNanoUsd": 0,
            },
        },
    )
    journal.transition(record.id, "completed")

    model = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    )
    detail = model.run_detail(record.id, limit=100)
    cost_events = [
        event for event in detail["events"] if event["type"] == "model.costed"
    ]

    assert [event["summary"] for event in cost_events] == [
        "Cost observation recorded",
        "Cost observation recorded",
        "Cost observation recorded",
        "Cost observation recorded",
    ]
    assert cost_events[0]["details"] == {
        "costVersion": 1,
        "operationId": operation_id,
        "status": "priced",
        "quality": "provider_usage_catalog_rate",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "catalogModelKey": "openai/gpt-4o",
        "amountNanoUsd": 300,
        "components": {
            "inputNanoUsd": 100,
            "outputNanoUsd": 150,
            "cacheReadNanoUsd": 50,
            "cacheCreationNanoUsd": 0,
        },
    }
    assert cost_events[1]["details"] == {
        "costVersion": 1,
        "operationId": "modelop_" + "e" * 32,
        "status": "unavailable",
        "quality": "unavailable",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "reason": "model_unpriced",
    }
    assert cost_events[2]["details"] == {
        "costVersion": 1,
        "operationId": "modelop_" + "f" * 32,
        "status": "unavailable",
        "quality": "unavailable",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "reason": "pricing_failed",
    }
    assert cost_events[3]["details"] == {
        "costVersion": 1,
        "operationId": "modelop_" + "1" * 32,
        "status": "unavailable",
        "quality": "unavailable",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "reason": "pricing_failed",
    }
    assert detail["metrics"]["cost"]["status"] == "unavailable"
    assert detail["metrics"]["cost"]["value"] is None
    assert detail["metrics"]["cost"]["coverage"]["orphanEvents"] == 4
    assert detail["metrics"]["cost"]["coverage"]["invalidEvents"] == 4
    assert detail["source"]["status"] == "partial"
    serialized = json.dumps(detail)
    for forbidden in (
        "component-secret",
        "raw-model-secret",
        "prompt-secret",
        "private.example.invalid",
        "projection-secret",
    ):
        assert forbidden not in serialized


def test_run_detail_strictly_projects_skill_and_memory_runtime_events(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Runtime trace", source="headless")
    journal.transition(record.id, "running")
    journal.append_event(
        record.id,
        "skill.routed",
        payload={
            "routingVersion": 1,
            "intentType": "code",
            "actionType": "update",
            "totalSkills": 30,
            "selectedCount": 2,
            "selected": [
                {
                    "qualifiedName": "project/safe-skill",
                    "source": "project",
                    "directory": "project",
                    "score": 4.25,
                    "description": "password=skill-secret",
                    "path": "/Users/example/private/SKILL.md",
                },
                {
                    "qualifiedName": "../password=unsafe-name",
                    "source": "external",
                    "directory": "../private",
                    "score": True,
                },
            ],
            "selectedTruncated": False,
            "usedFallback": False,
            "reasons": ["keyword:password=prompt-secret"],
            "tools": ["run_command"],
            "toolAffinity": {"secret": 1.0},
        },
    )
    journal.append_event(
        record.id,
        "skill.loaded",
        step=2,
        payload={
            "loadVersion": 1,
            "qualifiedName": "project/safe-skill",
            "source": "project",
            "directory": "project",
            "contentDigest": "a" * 64,
            "path": "/Users/example/private/SKILL.md",
            "content": "password=loaded-skill-secret",
        },
    )
    journal.append_event(
        record.id,
        "memory.retrieved",
        payload={
            "retrievalVersion": 1,
            "candidateCount": 8,
            "selectedCount": 3,
            "suppressedCount": 5,
            "noMatch": False,
            "noMatchReason": "password=unsafe-reason",
            "candidateIds": ["memory-secret-id"],
            "queryHash": "query-secret",
            "diagnostics": {"query": "prompt-secret"},
        },
    )
    journal.append_event(
        record.id,
        "memory.rendered",
        payload={
            "renderVersion": 1,
            "renderedCount": 2,
            "totalTokens": 186,
            "controllerMode": "standard",
            "injected": True,
            "renderedIds": ["memory-rendered-secret"],
            "content": "password=memory-secret",
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)
    events = detail["events"][2:6]

    assert events[0]["details"] == {
        "routingVersion": 1,
        "intentType": "code",
        "actionType": "update",
        "totalSkills": 30,
        "selectedCount": 2,
        "selected": [
            {
                "qualifiedName": "project/safe-skill",
                "source": "project",
                "directory": "project",
                "score": 4.25,
            }
        ],
        "selectedTruncated": True,
        "usedFallback": False,
    }
    assert events[1]["summary"] == "Skill loaded"
    assert events[1]["details"] == {
        "loadVersion": 1,
        "qualifiedName": "project/safe-skill",
        "source": "project",
        "directory": "project",
        "contentDigest": "a" * 64,
    }
    assert events[2]["details"] == {
        "retrievalVersion": 1,
        "candidateCount": 8,
        "selectedCount": 3,
        "suppressedCount": 5,
        "noMatch": False,
        "noMatchReason": None,
    }
    assert events[3]["details"] == {
        "renderVersion": 1,
        "renderedCount": 2,
        "totalTokens": 186,
        "controllerMode": "standard",
        "injected": True,
    }
    serialized = json.dumps(detail)
    for forbidden in (
        "skill-secret",
        "loaded-skill-secret",
        "/Users/",
        "unsafe-name",
        "unsafe-reason",
        "prompt-secret",
        "run_command",
        "memory-secret-id",
        "memory-rendered-secret",
        "memory-secret",
        "query-secret",
        '"payload"',
    ):
        assert forbidden not in serialized


def test_run_detail_strictly_projects_context_recovery_and_working_memory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Context trace", source="gateway")
    journal.transition(record.id, "running")
    operation_id = "ctxop_" + "a" * 32
    journal.append_event(
        record.id,
        "recovery.started",
        payload={
            "recoveryVersion": 1,
            "contextOperationId": operation_id,
            "kind": "cybernetic",
            "reason": "context_overflow",
            "error": "password=overflow-secret",
        },
    )
    journal.append_event(
        record.id,
        "context.compacted",
        payload={
            "contextVersion": 1,
            "contextOperationId": operation_id,
            "path": "reactive_cybernetic",
            "trigger": "reactive",
            "strategy": "reactive",
            "effective": True,
            "tokensFreed": 1_200,
            "messagesBefore": 32,
            "messagesAfter": 18,
            "messagesRemoved": 14,
            "messages": [{"content": "context-message-secret"}],
            "summary": "password=context-summary-secret",
        },
    )
    journal.append_event(
        record.id,
        "recovery.completed",
        payload={
            "recoveryVersion": 1,
            "contextOperationId": operation_id,
            "kind": "cybernetic",
            "outcome": "recovered",
            "tokensFreed": 1_200,
            "messagesBefore": 32,
            "messagesAfter": 18,
            "providerError": "recovery-provider-secret",
        },
    )
    journal.append_event(
        record.id,
        "working_memory.observed",
        payload={
            "workingMemoryVersion": 1,
            "action": "protected",
            "scope": "process",
            "entries": 3,
            "maxEntries": 15,
            "protectedTokens": 240,
            "maxTokens": 4_000,
            "content": "working-memory-content-secret",
            "entryType": "key_decision",
            "expiresAt": "ttl-secret",
        },
    )
    journal.append_event(
        record.id,
        "context.compacted",
        payload={
            "contextVersion": 1,
            "contextOperationId": "ctxop_bad",
            "path": "private/path/secret",
            "trigger": "invented",
            "strategy": "invented",
            "effective": True,
            "tokensFreed": True,
            "messagesBefore": 1,
            "messagesAfter": 2,
            "messagesRemoved": -1,
            "summary": "malformed-secret",
        },
    )
    journal.append_event(
        record.id,
        "working_memory.observed",
        payload={
            "workingMemoryVersion": 2,
            "action": "protected",
            "scope": "global",
            "entries": True,
            "maxEntries": 15,
            "protectedTokens": -1,
            "maxTokens": 4_000,
            "content": "malformed-working-secret",
        },
    )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id, limit=100)
    events = {
        event["sequence"]: event
        for event in detail["events"]
        if event["type"]
        in {
            "context.compacted",
            "recovery.started",
            "recovery.completed",
            "working_memory.observed",
        }
    }
    projected = list(events.values())

    assert projected[0]["details"] == {
        "recoveryVersion": 1,
        "kind": "cybernetic",
        "reason": "context_overflow",
    }
    assert projected[1]["details"] == {
        "contextVersion": 1,
        "path": "reactive_cybernetic",
        "trigger": "reactive",
        "strategy": "reactive",
        "effective": True,
        "tokensFreed": 1_200,
        "messagesBefore": 32,
        "messagesAfter": 18,
        "messagesRemoved": 14,
    }
    assert projected[2]["details"] == {
        "recoveryVersion": 1,
        "kind": "cybernetic",
        "outcome": "recovered",
        "tokensFreed": 1_200,
        "messagesBefore": 32,
        "messagesAfter": 18,
    }
    assert projected[3]["details"] == {
        "workingMemoryVersion": 1,
        "action": "protected",
        "scope": "process",
        "entries": 3,
        "maxEntries": 15,
        "protectedTokens": 240,
        "maxTokens": 4_000,
    }
    assert projected[4]["details"] == {}
    assert projected[5]["details"] == {}
    serialized = json.dumps(detail)
    for forbidden in (
        "overflow-secret",
        "context-message-secret",
        "context-summary-secret",
        "recovery-provider-secret",
        "working-memory-content-secret",
        "ttl-secret",
        "private/path/secret",
        "malformed-secret",
        "malformed-working-secret",
        '"payload"',
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"status": "complete"}, "invalid_status"),
        ({"source": "web"}, "invalid_source"),
        ({"limit": True}, "invalid_limit"),
        ({"limit": 101}, "invalid_limit"),
        ({"cursor": "../secret"}, "invalid_cursor"),
        ({"cursor": "x" * 513}, "invalid_cursor"),
    ],
)
def test_runs_page_rejects_invalid_filters_and_paging(
    tmp_path: Path, kwargs: dict[str, object], code: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = DashboardReadModel(workspace, data_dir=tmp_path / "home" / ".mini-code")

    with pytest.raises(DashboardReadError) as error:
        model.runs(**kwargs)  # type: ignore[arg-type]

    assert error.value.status == 400
    assert error.value.code == code


def test_runs_page_rejects_boolean_cursor_timestamps(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = DashboardReadModel(workspace, data_dir=tmp_path / "home" / ".mini-code")
    cursor = _cursor(
        "runs",
        "",
        "",
        True,
        1,
        "run_" + "a" * 32,
    )

    with pytest.raises(DashboardReadError) as error:
        model.runs(cursor=cursor)

    assert error.value.code == "invalid_cursor"


def test_run_detail_keeps_valid_events_when_final_line_is_partial(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Partial")
    journal.transition(record.id, "running")
    events_path = (
        data_dir
        / "dashboard"
        / "workspaces"
        / record.workspace_id
        / "runs"
        / record.id
        / "events.ndjson"
    )
    with events_path.open("ab") as handle:
        handle.write(b'{"password":"hidden-value"')
    before = (events_path.read_bytes(), events_path.stat().st_mtime_ns)

    payload = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).run_detail(record.id)

    assert payload["source"]["status"] == "error"
    assert [event["sequence"] for event in payload["events"]] == [1, 2]
    assert payload["diagnostics"] == [
        {
            "source": "runs",
            "code": "partial_final_event",
            "message": "An incomplete final Run event was ignored.",
        },
        {
            "source": "cost",
            "code": "cost_journal_read_failed",
            "message": "Cost observations could not be read completely.",
        },
        {
            "source": "tools",
            "code": "tool_journal_read_failed",
            "message": "Tool observations could not be read completely.",
        },
        {
            "source": "failures",
            "code": "failure_journal_read_failed",
            "message": "Failure observations could not be read completely.",
        },
    ]
    assert "hidden-value" not in json.dumps(payload)
    assert (events_path.read_bytes(), events_path.stat().st_mtime_ns) == before
