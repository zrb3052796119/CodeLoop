"""Bounded historical MCP runtime aggregation for Dashboard Connections.

The aggregate contains retained, run-scoped observations only.  It never
represents current process, connection, availability, or health state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import re

from minicode.mcp_event_contract import (
    MCP_RUNTIME_EVENT,
    normalize_mcp_runtime_payload,
)
from minicode.mcp_observation import mcp_server_key
from minicode.run_journal import RunJournal, stable_workspace_id


RUN_SCAN_LIMIT = 100
EVENT_SCAN_LIMIT_PER_RUN = 1_000
EVENT_PAGE_LIMIT = 100
_MAX_DIAGNOSTICS = 20
_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{32}$")
_ISO_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$"
)


@dataclass(frozen=True, slots=True)
class McpServerHistoricalRuntime:
    """Safe retained-window facts for one currently configured server."""

    status: str
    observed: bool
    last_observed_at: str | None
    last_outcome: str | None
    connection_attempted: bool | None
    observed_protocol: str | None
    retained_observation_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "current": "unavailable",
            "observed": self.observed,
            "lastObservedAt": self.last_observed_at,
            "lastOutcome": self.last_outcome,
            "connectionAttempted": self.connection_attempted,
            "observedProtocol": self.observed_protocol,
            "retainedObservationCount": self.retained_observation_count,
        }


@dataclass(frozen=True, slots=True)
class McpHistoricalRuntimeAggregate:
    """One bounded, best-effort current-workspace retained observation scan."""

    status: str
    last_observed_at: str | None
    retained_observation_count: int
    unmatched_observed_server_count: int
    retained_runs: int | None
    scanned_runs: int
    limited: bool
    server_runtime: dict[str, McpServerHistoricalRuntime]
    diagnostics: tuple[dict[str, str], ...]

    @property
    def observed_configured_count(self) -> int:
        return sum(1 for item in self.server_runtime.values() if item.observed)

    def runtime_dict(self) -> dict[str, object]:
        messages = {
            "stale": (
                "Retained Run observations are historical; current MCP status "
                "is unavailable."
            ),
            "unavailable": (
                "No retained MCP observation is available in the scanned window; "
                "current MCP status is unavailable."
            ),
            "error": (
                "Retained MCP observations could not be read; current MCP status "
                "is unavailable."
            ),
        }
        return {
            "status": self.status,
            "current": "unavailable",
            "historical": "partial",
            "lastObservedAt": self.last_observed_at,
            "retainedObservationCount": self.retained_observation_count,
            # Compatibility field: historical facts never produce a live count.
            "liveCount": None,
            "message": messages[self.status],
        }

    def coverage_dict(self) -> dict[str, object]:
        return {
            "scope": "retained-run-scoped-mcp-observations",
            "historical": "partial",
            "current": "unavailable",
            "runScanLimit": RUN_SCAN_LIMIT,
            "eventScanLimitPerRun": EVENT_SCAN_LIMIT_PER_RUN,
            "retainedRuns": self.retained_runs,
            "scannedRuns": self.scanned_runs,
            "limited": self.limited,
        }


@dataclass(slots=True)
class _ServerAccumulator:
    count: int = 0
    last_key: tuple[str, str, int] | None = None
    last_payload: dict[str, object] | None = None

    def observe(
        self,
        *,
        timestamp: str,
        run_id: str,
        sequence: int,
        payload: dict[str, object],
    ) -> None:
        self.count += 1
        candidate = (timestamp, run_id, sequence)
        if self.last_key is None or candidate > self.last_key:
            self.last_key = candidate
            self.last_payload = payload


def _diagnostic(code: str, message: str) -> dict[str, str]:
    return {"source": "connections", "code": code, "message": message}


def _append_diagnostic(
    diagnostics: list[dict[str, str]], code: str, message: str
) -> None:
    item = _diagnostic(code, message)
    if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
        diagnostics.append(item)


def _server_projection(
    accumulator: _ServerAccumulator, *, journal_failed: bool
) -> McpServerHistoricalRuntime:
    payload = accumulator.last_payload
    if payload is None:
        return McpServerHistoricalRuntime(
            status="error" if journal_failed else "unavailable",
            observed=False,
            last_observed_at=None,
            last_outcome=None,
            connection_attempted=None,
            observed_protocol=None,
            retained_observation_count=0,
        )
    return McpServerHistoricalRuntime(
        status="stale",
        observed=True,
        last_observed_at=accumulator.last_key[0] if accumulator.last_key else None,
        last_outcome=str(payload["outcome"]),
        connection_attempted=bool(payload["connectionAttempted"]),
        observed_protocol=(
            str(payload["protocol"]) if "protocol" in payload else None
        ),
        retained_observation_count=accumulator.count,
    )


def aggregate_historical_mcp_runtime(
    *,
    workspace: str | Path,
    run_journal: RunJournal,
    configured_server_names: Iterable[str],
) -> McpHistoricalRuntimeAggregate:
    """Read and aggregate the bounded retained MCP observation window."""
    resolved_workspace = Path(workspace).expanduser().resolve()
    workspace_id = stable_workspace_id(resolved_workspace)
    configured_names = tuple(configured_server_names)
    key_to_name = {
        mcp_server_key(resolved_workspace, name): name for name in configured_names
    }
    accumulators = {name: _ServerAccumulator() for name in configured_names}
    unmatched_keys: set[str] = set()
    diagnostics: list[dict[str, str]] = []
    retained_observations = 0
    latest_key: tuple[str, str, int] | None = None
    limited = False

    try:
        run_page = run_journal.list_runs(limit=RUN_SCAN_LIMIT)
    except Exception:  # noqa: BLE001 - runtime facts are optional and isolated
        _append_diagnostic(
            diagnostics,
            "mcp_runtime_journal_read_failed",
            "Retained MCP observations could not be read.",
        )
        return McpHistoricalRuntimeAggregate(
            status="error",
            last_observed_at=None,
            retained_observation_count=0,
            unmatched_observed_server_count=0,
            retained_runs=None,
            scanned_runs=0,
            limited=False,
            server_runtime={
                name: _server_projection(item, journal_failed=True)
                for name, item in accumulators.items()
            },
            diagnostics=tuple(diagnostics),
        )

    journal_failed = any(
        isinstance(item, dict) and item.get("code") == "journal_read_failed"
        for item in run_page.diagnostics
    )
    if journal_failed:
        _append_diagnostic(
            diagnostics,
            "mcp_runtime_journal_read_failed",
            "Retained MCP observations could not be read.",
        )
        return McpHistoricalRuntimeAggregate(
            status="error",
            last_observed_at=None,
            retained_observation_count=0,
            unmatched_observed_server_count=0,
            retained_runs=None,
            scanned_runs=0,
            limited=False,
            server_runtime={
                name: _server_projection(item, journal_failed=True)
                for name, item in accumulators.items()
            },
            diagnostics=tuple(diagnostics),
        )
    if run_page.diagnostics:
        _append_diagnostic(
            diagnostics,
            "mcp_runtime_journal_partial",
            "Some retained Run records were unavailable during the MCP scan.",
        )
    if run_page.has_more:
        limited = True
        _append_diagnostic(
            diagnostics,
            "mcp_runtime_runs_limited",
            "Historical MCP aggregation reached the Run scan limit.",
        )

    for record in run_page.items:
        run_id = getattr(record, "id", None)
        record_workspace_id = getattr(record, "workspace_id", None)
        if (
            not isinstance(run_id, str)
            or not _RUN_ID_RE.fullmatch(run_id)
            or record_workspace_id != workspace_id
        ):
            _append_diagnostic(
                diagnostics,
                "mcp_runtime_run_invalid",
                "A retained Run was excluded from the MCP scan.",
            )
            continue

        cursor: str | None = None
        scanned_events = 0
        while scanned_events < EVENT_SCAN_LIMIT_PER_RUN:
            remaining = EVENT_SCAN_LIMIT_PER_RUN - scanned_events
            try:
                event_page = run_journal.list_events(
                    run_id,
                    limit=min(EVENT_PAGE_LIMIT, remaining),
                    cursor=cursor,
                )
            except Exception:  # noqa: BLE001 - isolate one retained Run
                _append_diagnostic(
                    diagnostics,
                    "mcp_runtime_run_read_failed",
                    "One retained Run's MCP observations could not be read.",
                )
                break

            if event_page.diagnostics:
                _append_diagnostic(
                    diagnostics,
                    "mcp_runtime_events_partial",
                    "Some retained Run events were unavailable during the MCP scan.",
                )
            scanned_events += len(event_page.items)
            for event in event_page.items:
                if getattr(event, "type", None) != MCP_RUNTIME_EVENT:
                    continue
                timestamp = getattr(event, "timestamp", None)
                sequence = getattr(event, "sequence", None)
                event_workspace_id = getattr(event, "workspace_id", None)
                event_run_id = getattr(event, "run_id", None)
                payload = normalize_mcp_runtime_payload(
                    getattr(event, "payload", None)
                )
                if (
                    payload is None
                    or not isinstance(timestamp, str)
                    or not _ISO_UTC_RE.fullmatch(timestamp)
                    or isinstance(sequence, bool)
                    or not isinstance(sequence, int)
                    or sequence < 1
                    or event_workspace_id != workspace_id
                    or event_run_id != run_id
                ):
                    _append_diagnostic(
                        diagnostics,
                        "mcp_runtime_event_invalid",
                        "A malformed MCP runtime observation was ignored.",
                    )
                    continue

                retained_observations += 1
                candidate = (timestamp, run_id, sequence)
                if latest_key is None or candidate > latest_key:
                    latest_key = candidate
                server_key = str(payload["serverKey"])
                server_name = key_to_name.get(server_key)
                if server_name is None:
                    unmatched_keys.add(server_key)
                    continue
                accumulators[server_name].observe(
                    timestamp=timestamp,
                    run_id=run_id,
                    sequence=sequence,
                    payload=payload,
                )

            if not event_page.has_more:
                break
            if scanned_events >= EVENT_SCAN_LIMIT_PER_RUN:
                limited = True
                _append_diagnostic(
                    diagnostics,
                    "mcp_runtime_events_limited",
                    "Historical MCP aggregation reached a per-Run event scan limit.",
                )
                break
            if not event_page.next_cursor or not event_page.items:
                limited = True
                _append_diagnostic(
                    diagnostics,
                    "mcp_runtime_scan_incomplete",
                    "Historical MCP aggregation could not continue safely.",
                )
                break
            cursor = event_page.next_cursor

    server_runtime = {
        name: _server_projection(item, journal_failed=False)
        for name, item in accumulators.items()
    }
    return McpHistoricalRuntimeAggregate(
        status="stale" if retained_observations else "unavailable",
        last_observed_at=latest_key[0] if latest_key else None,
        retained_observation_count=retained_observations,
        unmatched_observed_server_count=len(unmatched_keys),
        retained_runs=run_page.known_total,
        scanned_runs=len(run_page.items),
        limited=limited,
        server_runtime=server_runtime,
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "EVENT_SCAN_LIMIT_PER_RUN",
    "McpHistoricalRuntimeAggregate",
    "McpServerHistoricalRuntime",
    "RUN_SCAN_LIMIT",
    "aggregate_historical_mcp_runtime",
]
