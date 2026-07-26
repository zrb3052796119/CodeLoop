from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from minicode.mcp_observation import mcp_server_key
from minicode.run_journal import stable_workspace_id
from minicode.web.mcp_runtime_aggregation import (
    EVENT_SCAN_LIMIT_PER_RUN,
    RUN_SCAN_LIMIT,
    aggregate_historical_mcp_runtime,
)


def _run(workspace: Path, suffix: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="run_" + suffix * 32,
        workspace_id=stable_workspace_id(workspace),
    )


def _payload(
    server_key: str,
    outcome: str,
    *,
    connection_attempted: bool,
    protocol: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "mcpVersion": 1,
        "serverKey": server_key,
        "transport": "stdio",
        "activity": "tool_request",
        "outcome": outcome,
        "connectionAttempted": connection_attempted,
    }
    if protocol is not None:
        payload["protocol"] = protocol
    if outcome != "request_succeeded":
        payload["failureKind"] = (
            "timeout" if outcome == "connection_failed" else "request_error"
        )
    return payload


def _event(
    workspace: Path,
    run_id: str,
    sequence: int,
    payload: object,
    *,
    timestamp: str = "2026-07-18T08:00:00.000Z",
    workspace_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        type="mcp.runtime.observed",
        timestamp=timestamp,
        sequence=sequence,
        workspace_id=workspace_id or stable_workspace_id(workspace),
        run_id=run_id,
        payload=payload,
    )


class FakeJournal:
    def __init__(
        self,
        records: list[SimpleNamespace],
        events: dict[str, list[SimpleNamespace]],
        *,
        run_diagnostics: tuple[dict[str, str], ...] = (),
        failing_runs: frozenset[str] = frozenset(),
        event_diagnostics: frozenset[str] = frozenset(),
    ) -> None:
        self.records = records
        self.events = events
        self.run_diagnostics = run_diagnostics
        self.failing_runs = failing_runs
        self.event_diagnostics = event_diagnostics

    def list_runs(self, *, limit: int):
        return SimpleNamespace(
            items=tuple(self.records[:limit]),
            known_total=len(self.records),
            has_more=len(self.records) > limit,
            diagnostics=self.run_diagnostics,
        )

    def list_events(self, run_id: str, *, limit: int, cursor: str | None):
        if run_id in self.failing_runs:
            raise OSError("fixture exception text must stay hidden")
        offset = int(cursor) if cursor is not None else 0
        events = self.events.get(run_id, [])
        items = tuple(events[offset : offset + limit])
        next_offset = offset + len(items)
        has_more = next_offset < len(events)
        return SimpleNamespace(
            items=items,
            has_more=has_more,
            next_cursor=str(next_offset) if has_more else None,
            diagnostics=(
                ({"source": "runs", "code": "event_invalid", "message": "safe"},)
                if run_id in self.event_diagnostics
                else ()
            ),
        )


def test_historical_mcp_runtime_projects_all_outcomes_and_deterministic_latest(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    earlier = _run(workspace, "a")
    later = _run(workspace, "b")
    keys = {
        name: mcp_server_key(workspace, name)
        for name in ("success", "connection", "request")
    }
    events = {
        earlier.id: [
            _event(
                workspace,
                earlier.id,
                8,
                _payload(
                    keys["request"],
                    "request_succeeded",
                    connection_attempted=True,
                    protocol="content-length",
                ),
            ),
            _event(
                workspace,
                earlier.id,
                9,
                _payload(
                    keys["success"],
                    "request_succeeded",
                    connection_attempted=False,
                    protocol="newline-json",
                ),
            ),
        ],
        later.id: [
            _event(
                workspace,
                later.id,
                3,
                _payload(
                    keys["connection"],
                    "connection_failed",
                    connection_attempted=True,
                    protocol="content-length",
                ),
            ),
            _event(
                workspace,
                later.id,
                4,
                _payload(
                    keys["request"],
                    "request_failed",
                    connection_attempted=False,
                ),
            ),
        ],
    }

    aggregate = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=FakeJournal([earlier, later], events),
        configured_server_names=("success", "connection", "request"),
    )

    assert aggregate.status == "stale"
    assert aggregate.retained_observation_count == 4
    assert aggregate.observed_configured_count == 3
    assert aggregate.server_runtime["success"].to_dict() == {
        "status": "stale",
        "current": "unavailable",
        "observed": True,
        "lastObservedAt": "2026-07-18T08:00:00.000Z",
        "lastOutcome": "request_succeeded",
        "connectionAttempted": False,
        "observedProtocol": "newline-json",
        "retainedObservationCount": 1,
    }
    assert aggregate.server_runtime["connection"].last_outcome == "connection_failed"
    assert aggregate.server_runtime["connection"].observed_protocol == "content-length"
    assert aggregate.server_runtime["request"].last_outcome == "request_failed"
    assert aggregate.server_runtime["request"].connection_attempted is False
    assert aggregate.server_runtime["request"].observed_protocol is None
    assert aggregate.server_runtime["request"].retained_observation_count == 2


def test_historical_mcp_runtime_counts_unmatched_keys_without_exposing_them(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = _run(workspace, "a")
    removed_key = mcp_server_key(workspace, "removed-server")
    journal = FakeJournal(
        [record],
        {
            record.id: [
                _event(
                    workspace,
                    record.id,
                    sequence,
                    _payload(
                        removed_key,
                        "request_succeeded",
                        connection_attempted=sequence == 1,
                    ),
                )
                for sequence in (1, 2)
            ]
        },
    )

    aggregate = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=journal,
        configured_server_names=("configured",),
    )

    assert aggregate.retained_observation_count == 2
    assert aggregate.unmatched_observed_server_count == 1
    assert aggregate.observed_configured_count == 0
    assert aggregate.server_runtime["configured"].status == "unavailable"
    assert removed_key not in str(aggregate.runtime_dict())
    assert removed_key not in str(aggregate.coverage_dict())


def test_historical_mcp_runtime_excludes_other_workspace_and_invalid_payloads(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    record = _run(workspace, "a")
    other_record = _run(other, "b")
    server_key = mcp_server_key(workspace, "server")
    invalid_payloads = [
        {**_payload(server_key, "request_succeeded", connection_attempted=True), "command": "secret"},
        {**_payload(server_key, "request_succeeded", connection_attempted=True), "mcpVersion": True},
        {**_payload(server_key, "request_succeeded", connection_attempted=True), "outcome": "online"},
        {**_payload(server_key, "request_failed", connection_attempted=True), "failureKind": "stack-trace"},
    ]
    events = [
        _event(workspace, record.id, index + 1, payload)
        for index, payload in enumerate(invalid_payloads)
    ]
    events.append(
        _event(
            workspace,
            record.id,
            10,
            _payload(
                server_key,
                "request_succeeded",
                connection_attempted=True,
            ),
            workspace_id=stable_workspace_id(other),
        )
    )
    events_by_run = {
        record.id: events,
        # Even a forged current-workspace event cannot make a Run owned by a
        # different workspace participate in the aggregate.
        other_record.id: [
            _event(
                workspace,
                other_record.id,
                1,
                _payload(
                    server_key,
                    "request_succeeded",
                    connection_attempted=True,
                ),
            )
        ],
    }

    aggregate = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=FakeJournal([record, other_record], events_by_run),
        configured_server_names=("server",),
    )

    assert aggregate.status == "unavailable"
    assert aggregate.retained_observation_count == 0
    assert aggregate.server_runtime["server"].observed is False
    assert {item["code"] for item in aggregate.diagnostics} == {
        "mcp_runtime_event_invalid",
        "mcp_runtime_run_invalid",
    }


def test_historical_mcp_runtime_localizes_one_run_failure_and_event_corruption(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broken = _run(workspace, "a")
    valid = _run(workspace, "b")
    server_key = mcp_server_key(workspace, "server")
    journal = FakeJournal(
        [broken, valid],
        {
            valid.id: [
                _event(
                    workspace,
                    valid.id,
                    2,
                    _payload(
                        server_key,
                        "request_succeeded",
                        connection_attempted=True,
                    ),
                )
            ]
        },
        failing_runs=frozenset({broken.id}),
        event_diagnostics=frozenset({valid.id}),
    )

    aggregate = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=journal,
        configured_server_names=("server",),
    )

    assert aggregate.status == "stale"
    assert aggregate.retained_observation_count == 1
    assert aggregate.server_runtime["server"].observed is True
    assert {item["code"] for item in aggregate.diagnostics} == {
        "mcp_runtime_run_read_failed",
        "mcp_runtime_events_partial",
    }
    assert "fixture exception" not in str(aggregate.diagnostics)


def test_historical_mcp_runtime_global_failure_keeps_configured_servers_visible(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FailingJournal:
        def list_runs(self, *, limit: int):
            assert limit == RUN_SCAN_LIMIT
            raise OSError("Authorization=journal-secret")

    aggregate = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=FailingJournal(),
        configured_server_names=("server",),
    )

    assert aggregate.status == "error"
    assert aggregate.retained_runs is None
    assert aggregate.scanned_runs == 0
    assert aggregate.server_runtime["server"].status == "error"
    assert "journal-secret" not in str(aggregate.diagnostics)

    diagnostic_failure = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=FakeJournal(
            [],
            {},
            run_diagnostics=(
                {
                    "source": "runs",
                    "code": "journal_read_failed",
                    "message": "RunJournal storage could not be read.",
                },
            ),
        ),
        configured_server_names=("server",),
    )
    assert diagnostic_failure.status == "error"
    assert diagnostic_failure.retained_runs is None
    assert diagnostic_failure.server_runtime["server"].status == "error"


def test_historical_mcp_runtime_reports_run_and_event_scan_limits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    records = [
        SimpleNamespace(
            id=f"run_{index:032x}",
            workspace_id=stable_workspace_id(workspace),
        )
        for index in range(RUN_SCAN_LIMIT + 1)
    ]
    server_key = mcp_server_key(workspace, "server")
    first = records[0]
    events = {
        first.id: [
            _event(
                workspace,
                first.id,
                sequence,
                _payload(
                    server_key,
                    "request_succeeded",
                    connection_attempted=sequence == 1,
                ),
            )
            for sequence in range(1, EVENT_SCAN_LIMIT_PER_RUN + 2)
        ]
    }

    aggregate = aggregate_historical_mcp_runtime(
        workspace=workspace,
        run_journal=FakeJournal(records, events),
        configured_server_names=("server",),
    )

    assert aggregate.retained_runs == RUN_SCAN_LIMIT + 1
    assert aggregate.scanned_runs == RUN_SCAN_LIMIT
    assert aggregate.retained_observation_count == EVENT_SCAN_LIMIT_PER_RUN
    assert aggregate.limited is True
    assert {item["code"] for item in aggregate.diagnostics} == {
        "mcp_runtime_runs_limited",
        "mcp_runtime_events_limited",
    }
