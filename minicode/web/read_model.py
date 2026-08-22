"""Read-only, redacted projection for the MiniCode Dashboard."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import platform
import re
import stat
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from minicode.config import MINI_CODE_DIR
from minicode.memory import (
    MemoryEntry,
    MemoryFile,
    MemoryScope,
    MemoryTier,
    assess_memory_safety,
)
from minicode.mcp_event_contract import normalize_mcp_runtime_payload
from minicode.permission_event_contract import normalize_permission_event_payload
from minicode.mcp_observation import mcp_server_key
from minicode.run_journal import (
    RUN_SOURCES,
    RUN_STATUSES,
    RunJournal,
    RunJournalNotFoundError,
    RunJournalValidationError,
    stable_workspace_id,
)
from minicode.session import (
    SessionMetadata,
    list_sessions,
    persistence_generation,
    validate_session_delta,
)
from minicode.skill_evidence import SkillEvidenceLedger
from minicode.skill_versions import SkillVersionLedger
from minicode.skills import (
    SkillSummary,
    discover_skills,
    extract_description,
    parse_frontmatter,
)
from minicode.task_outcome_event import normalize_task_outcome_payload
from minicode.web.context_aggregation import (
    ContextAggregate,
    aggregate_run_context,
    merge_context_aggregates,
    project_context_breakdown,
    project_context_event_detail,
    project_context_metric,
    project_recovery_metric,
    project_run_context_summary,
    project_working_memory_metric,
)
from minicode.web.cost_aggregation import (
    CostAggregate,
    aggregate_run_cost,
    merge_cost_aggregates,
    project_cost_breakdown,
    project_cost_event_detail,
    project_cost_metric,
    project_run_cost_summary,
)
from minicode.web.mcp_runtime_aggregation import aggregate_historical_mcp_runtime
from minicode.web.mcp_current_projection import (
    McpCurrentStateLoader,
    project_current_mcp_state,
)
from minicode.web.tool_aggregation import (
    FailureAggregate,
    ToolAggregate,
    aggregate_run_failures,
    aggregate_run_tools,
    merge_failure_aggregates,
    merge_tool_aggregates,
    project_failure_breakdown,
    project_failure_metric,
    project_run_failure_summary,
    project_run_tool_summary,
    project_tool_breakdown,
    project_tool_metric,
)


SessionLoader = Callable[[], list[SessionMetadata]]
SkillLoader = Callable[[str | Path], list[SkillSummary]]

_SENSITIVE_KEYS = {
    "apikey",
    "accesstoken",
    "authtoken",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "env",
    "password",
    "privatekey",
    "providercredential",
    "providercredentials",
    "secret",
    "token",
}
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_TOKEN_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret|"
    r"credential|authorization|cookie)\b(\s*[:=]\s*)[^\s,;/'\"]+"
)
_MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
_MAX_MEMORY_ENTRIES = 1_000
_MAX_SESSION_INDEX_ENTRIES = 10_000
_MAX_SKILL_SUMMARIES = 10_000
_DEFAULT_PAGE_LIMIT = 20
_MAX_PAGE_LIMIT = 100
_MAX_SESSION_PREVIEW_CHARS = 240
_DEFAULT_SESSION_MESSAGE_LIMIT = 50
_MAX_SESSION_MESSAGES = 10_000
_MAX_SESSION_DELTA_FILES = 50
_MAX_SESSION_MESSAGE_CHARS = 2_000
_MAX_SESSION_RESPONSE_CONTENT_CHARS = 20_000
_MAX_MEMORY_CONTENT_CHARS = 1_000
_MAX_MEMORY_RESPONSE_CONTENT_CHARS = 20_000
_MAX_SKILL_DESCRIPTION_CHARS = 400
_MAX_SKILL_LIST_ITEMS = 20
_MAX_SKILL_LIST_VALUE_CHARS = 64
_MAX_SKILL_EXAMPLES = 1_000
_MAX_SKILL_RESPONSE_CONTENT_CHARS = 30_000
_MAX_MCP_SERVER_ENTRIES = 1_000
_MAX_MCP_RESPONSE_SERVERS = 100
_MAX_DIAGNOSTICS = 20
_MAX_DIAGNOSTIC_MESSAGE_CHARS = 240
_MAX_CURSOR_CHARS = 512
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CATEGORY_RE = re.compile(r"^[\w.-]{1,64}$")
_MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SKILL_NAME_RE = re.compile(r"^[\w][\w.-]{0,127}$")
_SKILL_QUALIFIED_NAME_RE = re.compile(r"^[\w][\w./-]{0,255}$")
_SKILL_DIRECTORY_RE = re.compile(r"^[\w][\w.-]{0,63}$")
_SKILL_SOURCES = ("project", "user", "compat_project", "compat_user")
_MCP_SERVER_NAME_RE = re.compile(r"^[^\x00-\x1f/\\]{1,128}$")
_TRACE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TRACE_OPERATION_ID_RE = re.compile(r"^toolop_[0-9a-f]{32}$")
_TRACE_MODEL_OPERATION_ID_RE = re.compile(r"^modelop_[0-9a-f]{32}$")
_TRACE_CONTEXT_OPERATION_ID_RE = re.compile(r"^ctxop_[0-9a-f]{32}$")
_TRACE_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}$")
_TRACE_SKILL_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_TRACE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TRACE_ASSISTANT_CONTENT_LENGTH = 1_000_000
_MAX_TRACE_MODEL_TOOL_CALL_COUNT = 1_000
_MAX_TRACE_MODEL_TOKENS = 1_000_000_000
_MAX_TRACE_MODEL_DURATION_MS = 86_400_000
_MAX_USAGE_RUNS = 100
_MAX_USAGE_EVENTS_PER_RUN = 1_000
_USAGE_EVENT_PAGE_LIMIT = 100
_MAX_TRACE_ROUTING_SKILLS = 100_000
_MAX_TRACE_SELECTED_SKILLS = 20
_TRACE_TASK_OUTCOME_STATUSES = frozenset(
    {"success", "failed", "unknown", "cancelled"}
)
_MAX_TRACE_MEMORY_COUNT = 100_000
_MAX_TRACE_MEMORY_TOKENS = 10_000_000
_MAX_TRACE_CONTEXT_MESSAGES = 100_000
_MAX_TRACE_CONTEXT_TOKENS = 1_000_000_000
_TRACE_CONTEXT_PATHS = frozenset(
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
_TRACE_CONTEXT_TRIGGERS = frozenset(
    {"manual", "auto", "reactive", "microcompact_time", "microcompact_cached"}
)
_TRACE_CONTEXT_STRATEGIES = frozenset(
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
_TRACE_RECOVERY_KINDS = frozenset({"cybernetic", "compactor"})
_TRACE_SKILL_SOURCES = frozenset(
    {"project", "user", "compat_project", "compat_user"}
)
_TRACE_INTENT_TYPES = frozenset(
    {
        "code", "debug", "refactor", "explain", "search", "review", "test",
        "document", "configure", "question", "chat", "memory", "system",
        "unknown",
    }
)
_TRACE_ACTION_TYPES = frozenset(
    {
        "create", "read", "update", "delete", "execute", "analyze",
        "compare", "merge", "split", "move", "rename", "unknown",
    }
)
_TRACE_MEMORY_CONTROLLER_MODES = frozenset(
    {"none", "summary", "standard", "strong"}
)
_TRACE_MEMORY_NO_MATCH_REASONS = frozenset(
    {
        "queryless_production_request",
        "query_has_no_informative_terms",
        "controller_disabled",
        "budget_exhausted",
        "unresolved_conflict_fail_closed",
        "candidate_consolidation_suppressed_all",
        "relevance_gate_rejected_all",
        "no_active_memories",
        "system_message_missing",
        "prompt_injection_failed",
        "other",
    }
)
_RUN_COVERAGE = {
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

_TRACE_MODEL_USAGE_SOURCES = frozenset(
    {"provider", "estimated", "unavailable"}
)
_TRACE_MODEL_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheCreationTokens",
)


class DashboardReadError(ValueError):
    """Safe, structured request error exposed by Dashboard read interfaces."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _unavailable_trace_model_usage() -> dict[str, object]:
    return {
        "source": "unavailable",
        "inputTokens": None,
        "outputTokens": None,
        "cacheReadTokens": None,
        "cacheCreationTokens": None,
    }


def _trace_model_usage(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != {
        "source",
        *_TRACE_MODEL_USAGE_FIELDS,
    }:
        return None
    source = value.get("source")
    if source not in _TRACE_MODEL_USAGE_SOURCES:
        return None
    projected: dict[str, object] = {"source": source}
    for field in _TRACE_MODEL_USAGE_FIELDS:
        token_value = value.get(field)
        if token_value is not None and (
            isinstance(token_value, bool)
            or not isinstance(token_value, int)
            or token_value < 0
            or token_value > _MAX_TRACE_MODEL_TOKENS
        ):
            return None
        projected[field] = token_value
    if source == "unavailable":
        return (
            projected
            if all(projected[field] is None for field in _TRACE_MODEL_USAGE_FIELDS)
            else None
        )
    return projected


def _trace_model_duration(value: object) -> int | None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_TRACE_MODEL_DURATION_MS
    ):
        return value
    return None


class _ModelObservationAggregate:
    """Pair and aggregate bounded safe Model terminal events for one or more Runs."""

    def __init__(self) -> None:
        self.completed_calls = 0
        self.failed_calls = 0
        self.usage_items: list[dict[str, object]] = []
        self.durations: list[int] = []
        self.diagnostics: list[dict[str, str]] = []

    def _diagnostic(self, code: str, message: str) -> None:
        if len(self.diagnostics) >= _MAX_DIAGNOSTICS:
            return
        item = {"source": "usage", "code": code, "message": message}
        if item not in self.diagnostics:
            self.diagnostics.append(item)

    def observe(self, events: list[object]) -> None:
        started: set[str] = set()
        terminal: set[str] = set()
        for event in events:
            event_type = getattr(event, "type", None)
            if event_type not in {
                "model.started",
                "model.completed",
                "model.failed",
            }:
                continue
            payload = getattr(event, "payload", None)
            if not isinstance(payload, dict):
                self._diagnostic(
                    "model_event_invalid",
                    "A malformed Model observation was ignored.",
                )
                continue
            operation_id = payload.get("operationId")
            if (
                not isinstance(operation_id, str)
                or not _TRACE_MODEL_OPERATION_ID_RE.fullmatch(operation_id)
            ):
                self._diagnostic(
                    "model_event_invalid",
                    "A malformed Model observation was ignored.",
                )
                continue
            if event_type == "model.started":
                if operation_id in started or operation_id in terminal:
                    self._diagnostic(
                        "model_operation_duplicate",
                        "A duplicate Model operation event was ignored.",
                    )
                    continue
                started.add(operation_id)
                continue
            if operation_id in terminal:
                self._diagnostic(
                    "model_operation_duplicate",
                    "A duplicate Model operation event was ignored.",
                )
                continue
            if operation_id not in started:
                self._diagnostic(
                    "model_operation_unpaired",
                    "An unpaired Model operation event was ignored.",
                )
                continue
            terminal.add(operation_id)
            duration_value = payload.get("durationMs")
            if "durationMs" in payload:
                duration = _trace_model_duration(duration_value)
                if duration is None:
                    self._diagnostic(
                        "model_duration_invalid",
                        "An invalid Model duration was ignored.",
                    )
                else:
                    self.durations.append(duration)
            if event_type == "model.failed":
                self.failed_calls += 1
                if "usage" in payload:
                    self._diagnostic(
                        "model_usage_invalid",
                        "Unexpected failed-call usage was ignored.",
                    )
                continue
            self.completed_calls += 1
            if "usage" not in payload:
                self.usage_items.append(_unavailable_trace_model_usage())
                continue
            usage = _trace_model_usage(payload.get("usage"))
            if usage is None:
                self._diagnostic(
                    "model_usage_invalid",
                    "An invalid Model usage observation was ignored.",
                )
                usage = _unavailable_trace_model_usage()
            self.usage_items.append(usage)
        if started - terminal:
            self._diagnostic(
                "model_operation_unpaired",
                "An unpaired Model operation event was ignored.",
            )

    def merge(self, other: "_ModelObservationAggregate") -> None:
        self.completed_calls += other.completed_calls
        self.failed_calls += other.failed_calls
        self.usage_items.extend(other.usage_items)
        self.durations.extend(other.durations)
        for diagnostic in other.diagnostics:
            if (
                len(self.diagnostics) < _MAX_DIAGNOSTICS
                and diagnostic not in self.diagnostics
            ):
                self.diagnostics.append(diagnostic)

    def _calls(self, source: str) -> list[dict[str, object]]:
        return [item for item in self.usage_items if item["source"] == source]

    @staticmethod
    def _bucket(
        items: list[dict[str, object]], field: str
    ) -> int | None:
        values = [item[field] for item in items if item[field] is not None]
        return sum(values) if values else None  # type: ignore[arg-type]

    def usage_bucket(self, source: str) -> dict[str, int | None]:
        items = self._calls(source)
        return {
            field: self._bucket(items, field) for field in _TRACE_MODEL_USAGE_FIELDS
        }

    def tokens_metric(self) -> dict[str, object]:
        if self.completed_calls == 0:
            return {"status": "unavailable", "value": None}
        provider_calls = len(self._calls("provider"))
        estimated_calls = len(self._calls("estimated"))
        unavailable_calls = len(self._calls("unavailable"))
        known = self._calls("provider") + self._calls("estimated")
        combined = {
            field: self._bucket(known, field) for field in _TRACE_MODEL_USAGE_FIELDS
        }
        known_total_values = [
            item[field]
            for item in known
            for field in _TRACE_MODEL_USAGE_FIELDS
            if item[field] is not None
        ]
        total_tokens = (
            sum(known_total_values) if known_total_values else None
        )
        if provider_calls and estimated_calls:
            provenance = "mixed"
        elif provider_calls:
            provenance = "provider"
        elif estimated_calls:
            provenance = "estimated"
        else:
            provenance = "unavailable"
        status = (
            "live"
            if provider_calls == self.completed_calls
            else "partial"
            if provider_calls or estimated_calls
            else "unavailable"
        )
        return {
            "status": status,
            "value": {
                **combined,
                "totalTokens": total_tokens,
                "providerCalls": provider_calls,
                "estimatedCalls": estimated_calls,
                "unavailableCalls": unavailable_calls,
                "provenance": provenance,
            },
        }

    def duration_metric(self) -> dict[str, object]:
        model_calls = self.completed_calls + self.failed_calls
        if model_calls == 0:
            return {"status": "unavailable", "value": None}
        observed_calls = len(self.durations)
        total_ms = sum(self.durations) if self.durations else None
        average_ms = (
            round(total_ms / observed_calls)
            if total_ms is not None and observed_calls
            else None
        )
        return {
            "status": (
                "live"
                if observed_calls == model_calls
                else "partial"
                if observed_calls
                else "unavailable"
            ),
            "value": {
                "modelCalls": model_calls,
                "completedCalls": self.completed_calls,
                "failedCalls": self.failed_calls,
                "observedCalls": observed_calls,
                "totalMs": total_ms,
                "averageMs": average_ms,
            },
        }


def _trace_bounded_count(value: object, *, maximum: int) -> int | None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    ):
        return value
    return None


def _trace_context_identity(
    payload: Mapping[str, Any], *, version_key: str
) -> tuple[str, str] | None:
    operation_id = payload.get("contextOperationId")
    if (
        payload.get(version_key) != 1
        or not isinstance(operation_id, str)
        or not _TRACE_CONTEXT_OPERATION_ID_RE.fullmatch(operation_id)
    ):
        return None
    return version_key, operation_id


def _trace_context_compaction_details(
    payload: Mapping[str, Any],
) -> dict[str, object]:
    identity = _trace_context_identity(payload, version_key="contextVersion")
    path = payload.get("path")
    trigger = payload.get("trigger")
    strategy = payload.get("strategy")
    before = _trace_bounded_count(
        payload.get("messagesBefore"), maximum=_MAX_TRACE_CONTEXT_MESSAGES
    )
    after = _trace_bounded_count(
        payload.get("messagesAfter"), maximum=_MAX_TRACE_CONTEXT_MESSAGES
    )
    removed = _trace_bounded_count(
        payload.get("messagesRemoved"), maximum=_MAX_TRACE_CONTEXT_MESSAGES
    )
    if (
        identity is None
        or path not in _TRACE_CONTEXT_PATHS
        or trigger not in _TRACE_CONTEXT_TRIGGERS
        or strategy not in _TRACE_CONTEXT_STRATEGIES
        or payload.get("effective") is not True
        or before is None
        or after is None
        or removed is None
        or after > before
        or removed != before - after
    ):
        return {}
    details: dict[str, object] = {
        "contextVersion": 1,
        "contextOperationId": identity[1],
        "path": path,
        "trigger": trigger,
        "strategy": strategy,
        "effective": True,
        "messagesBefore": before,
        "messagesAfter": after,
        "messagesRemoved": removed,
    }
    if "tokensFreed" in payload and payload.get("tokensFreed") is not None:
        tokens_freed = _trace_bounded_count(
            payload.get("tokensFreed"), maximum=_MAX_TRACE_CONTEXT_TOKENS
        )
        if tokens_freed is None:
            return {}
        details["tokensFreed"] = tokens_freed
    return details


def _trace_recovery_details(
    event_type: str, payload: Mapping[str, Any]
) -> dict[str, object]:
    identity = _trace_context_identity(payload, version_key="recoveryVersion")
    kind = payload.get("kind")
    if identity is None or kind not in _TRACE_RECOVERY_KINDS:
        return {}
    if event_type == "recovery.started":
        if payload.get("reason") != "context_overflow":
            return {}
        return {
            "recoveryVersion": 1,
            "contextOperationId": identity[1],
            "kind": kind,
            "reason": "context_overflow",
        }

    outcome = payload.get("outcome")
    before = _trace_bounded_count(
        payload.get("messagesBefore"), maximum=_MAX_TRACE_CONTEXT_MESSAGES
    )
    after = _trace_bounded_count(
        payload.get("messagesAfter"), maximum=_MAX_TRACE_CONTEXT_MESSAGES
    )
    if (
        outcome not in {"recovered", "not_recovered"}
        or before is None
        or after is None
        or after > before
    ):
        return {}
    details = {
        "recoveryVersion": 1,
        "contextOperationId": identity[1],
        "kind": kind,
        "outcome": outcome,
        "messagesBefore": before,
        "messagesAfter": after,
    }
    tokens_value = payload.get("tokensFreed")
    if outcome == "recovered":
        tokens_freed = _trace_bounded_count(
            tokens_value, maximum=_MAX_TRACE_CONTEXT_TOKENS
        )
        if tokens_freed is None:
            return {}
        details["tokensFreed"] = tokens_freed
    elif "tokensFreed" in payload and tokens_value is not None:
        return {}
    return details


def _trace_working_memory_details(
    payload: Mapping[str, Any],
) -> dict[str, object]:
    entries = _trace_bounded_count(
        payload.get("entries"), maximum=_MAX_TRACE_MEMORY_COUNT
    )
    max_entries = _trace_bounded_count(
        payload.get("maxEntries"), maximum=_MAX_TRACE_MEMORY_COUNT
    )
    protected_tokens = _trace_bounded_count(
        payload.get("protectedTokens"), maximum=_MAX_TRACE_MEMORY_TOKENS
    )
    max_tokens = _trace_bounded_count(
        payload.get("maxTokens"), maximum=_MAX_TRACE_MEMORY_TOKENS
    )
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
        return {}
    return {
        "workingMemoryVersion": 1,
        "action": "protected",
        "scope": "process",
        "entries": entries,
        "maxEntries": max_entries,
        "protectedTokens": protected_tokens,
        "maxTokens": max_tokens,
    }


def _run_event_details(
    event_type: str, payload: Mapping[str, Any]
) -> dict[str, object]:
    """Project only the declared safe fields for supported trace events."""
    details: dict[str, object] = {}
    if event_type in {"permission.requested", "permission.decided"}:
        normalized_permission = normalize_permission_event_payload(
            event_type, payload
        )
        return normalized_permission or {}
    if event_type in {"context.compacted", "context.compaction.failed", "recovery.started", "recovery.completed", "working_memory.observed"}:
        return project_context_event_detail(event_type, payload)
    if event_type == "model.costed":
        return project_cost_event_detail(payload)
    if event_type == "mcp.runtime.observed":
        normalized = normalize_mcp_runtime_payload(payload)
        return normalized or {}
    if event_type in {"model.started", "model.completed", "model.failed"}:
        operation_id = payload.get("operationId")
        if (
            isinstance(operation_id, str)
            and _TRACE_MODEL_OPERATION_ID_RE.fullmatch(operation_id)
        ):
            details["operationId"] = operation_id
        if event_type == "model.completed":
            result_type = payload.get("resultType")
            content_present = payload.get("contentPresent")
            tool_call_count = payload.get("toolCallCount")
            if isinstance(result_type, str) and result_type in {
                "assistant",
                "tool_calls",
            }:
                details["resultType"] = result_type
            if isinstance(content_present, bool):
                details["contentPresent"] = content_present
            if (
                isinstance(tool_call_count, int)
                and not isinstance(tool_call_count, bool)
                and 0 <= tool_call_count <= _MAX_TRACE_MODEL_TOOL_CALL_COUNT
            ):
                details["toolCallCount"] = tool_call_count
            usage = _trace_model_usage(payload.get("usage"))
            details["usage"] = (
                usage if usage is not None else _unavailable_trace_model_usage()
            )
        elif event_type == "model.failed":
            failure_kind = payload.get("failureKind")
            if isinstance(failure_kind, str) and failure_kind in {
                "interrupted",
                "network",
                "timeout",
                "provider_error",
            }:
                details["failureKind"] = failure_kind
        duration_ms = _trace_model_duration(payload.get("durationMs"))
        if duration_ms is not None:
            details["durationMs"] = duration_ms
        return details
    if event_type in {"tool.started", "tool.finished"}:
        tool_name = payload.get("toolName")
        if isinstance(tool_name, str) and _TRACE_TOOL_NAME_RE.fullmatch(tool_name):
            details["toolName"] = tool_name
        if event_type == "tool.finished":
            outcome = payload.get("outcome")
            paired = payload.get("paired")
            if isinstance(outcome, str) and outcome in {"success", "error"}:
                details["outcome"] = outcome
            if isinstance(paired, bool):
                details["paired"] = paired
        return details
    if event_type == "assistant.completed":
        content_present = payload.get("contentPresent")
        content_length = payload.get("contentLength")
        kind = payload.get("kind")
        if isinstance(content_present, bool):
            details["contentPresent"] = content_present
        if (
            isinstance(content_length, int)
            and not isinstance(content_length, bool)
            and 0 <= content_length <= _MAX_TRACE_ASSISTANT_CONTENT_LENGTH
        ):
            details["contentLength"] = content_length
        if kind == "returned_assistant":
            details["kind"] = kind
        return details
    if event_type == "execution.stopped":
        reason_code = payload.get("reasonCode")
        step_count = _trace_bounded_count(
            payload.get("stepCount"), maximum=_MAX_TRACE_ROUTING_SKILLS
        )
        tool_error_count = _trace_bounded_count(
            payload.get("toolErrorCount"), maximum=_MAX_TRACE_ROUTING_SKILLS
        )
        consecutive_failed_steps = _trace_bounded_count(
            payload.get("consecutiveFailedSteps"),
            maximum=_MAX_TRACE_ROUTING_SKILLS,
        )
        user_action_required = payload.get("userActionRequired")
        if (
            reason_code
            not in {
                "repeated_denied_action",
                "consecutive_tool_failures",
                "failure_window_exhausted",
            }
            or step_count is None
            or tool_error_count is None
            or consecutive_failed_steps is None
            or not isinstance(user_action_required, bool)
        ):
            return {}
        return {
            "reasonCode": reason_code,
            "stepCount": step_count,
            "toolErrorCount": tool_error_count,
            "consecutiveFailedSteps": consecutive_failed_steps,
            "userActionRequired": user_action_required,
        }
    if event_type == "task.outcome":
        return normalize_task_outcome_payload(payload) or details
    if event_type == "skill.routed":
        routing_version = payload.get("routingVersion")
        if routing_version not in {1, 2}:
            return details
        intent_type = payload.get("intentType")
        action_type = payload.get("actionType")
        total_skills = payload.get("totalSkills")
        selected_count = payload.get("selectedCount")
        used_fallback = payload.get("usedFallback")
        selected_value = payload.get("selected")
        if intent_type in _TRACE_INTENT_TYPES:
            details["routingVersion"] = routing_version
            details["intentType"] = intent_type
        if action_type in _TRACE_ACTION_TYPES:
            details["actionType"] = action_type
        if (
            isinstance(total_skills, int)
            and not isinstance(total_skills, bool)
            and 0 <= total_skills <= _MAX_TRACE_ROUTING_SKILLS
        ):
            details["totalSkills"] = total_skills
        if (
            isinstance(selected_count, int)
            and not isinstance(selected_count, bool)
            and 0 <= selected_count <= _MAX_TRACE_ROUTING_SKILLS
        ):
            details["selectedCount"] = selected_count
        selected: list[dict[str, object]] = []
        if isinstance(selected_value, list):
            for raw_item in selected_value[:_MAX_TRACE_SELECTED_SKILLS]:
                if not isinstance(raw_item, dict):
                    continue
                qualified_name = raw_item.get("qualifiedName")
                source = raw_item.get("source")
                directory = raw_item.get("directory")
                score = raw_item.get("score")
                content_digest = raw_item.get("contentDigest")
                evidence_adjustment = raw_item.get("evidenceAdjustment")
                if (
                    not isinstance(qualified_name, str)
                    or not _TRACE_SKILL_NAME_RE.fullmatch(qualified_name)
                    or source not in _TRACE_SKILL_SOURCES
                    or not isinstance(directory, str)
                    or (
                        directory
                        and not _TRACE_SKILL_DIRECTORY_RE.fullmatch(directory)
                    )
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                    or abs(float(score)) > 1_000_000
                    or (
                        routing_version == 2
                        and (
                            not isinstance(content_digest, str)
                            or _TRACE_SHA256_RE.fullmatch(content_digest)
                            is None
                        )
                    )
                ):
                    continue
                item = {
                    "qualifiedName": qualified_name,
                    "source": source,
                    "directory": directory,
                    "score": score,
                }
                if routing_version == 2:
                    item["contentDigest"] = content_digest
                if (
                    isinstance(evidence_adjustment, (int, float))
                    and not isinstance(evidence_adjustment, bool)
                    and math.isfinite(float(evidence_adjustment))
                    and 0 < abs(float(evidence_adjustment)) <= 0.25
                ):
                    item["evidenceAdjustment"] = float(evidence_adjustment)
                selected.append(item)
        details["selected"] = selected
        if "selectedCount" in details:
            details["selectedTruncated"] = (
                len(selected) < details["selectedCount"]
            )
        if isinstance(used_fallback, bool):
            details["usedFallback"] = used_fallback
        return details
    if event_type == "skill.loaded":
        if payload.get("loadVersion") != 1:
            return details
        qualified_name = payload.get("qualifiedName")
        source = payload.get("source")
        directory = payload.get("directory")
        content_digest = payload.get("contentDigest")
        if (
            not isinstance(qualified_name, str)
            or _TRACE_SKILL_NAME_RE.fullmatch(qualified_name) is None
            or source not in _TRACE_SKILL_SOURCES
            or not isinstance(directory, str)
            or (
                directory
                and _TRACE_SKILL_DIRECTORY_RE.fullmatch(directory) is None
            )
            or not isinstance(content_digest, str)
            or _TRACE_SHA256_RE.fullmatch(content_digest) is None
        ):
            return details
        return {
            "loadVersion": 1,
            "qualifiedName": qualified_name,
            "source": source,
            "directory": directory,
            "contentDigest": content_digest,
        }
    if event_type == "skill.attributed":
        attribution_version = payload.get("attributionVersion")
        if attribution_version not in {1, 2} or payload.get(
            "attributionKind"
        ) != "task_correlation":
            return details
        outcome_status = payload.get("outcomeStatus")
        completion_succeeded = payload.get("completionSucceeded")
        verification_status = payload.get("verificationStatus")
        goal_achieved = payload.get("goalAchieved")
        had_tool_errors = payload.get("hadToolErrors")
        errors_recovered = payload.get("errorsRecovered")
        tool_error_count = payload.get("toolErrorCount")
        loaded_skill_count = payload.get("loadedSkillCount")
        loaded_skills_value = payload.get("loadedSkills")
        loaded_skills_truncated = payload.get("loadedSkillsTruncated")
        expected_completion = outcome_status == "success"
        expected_goal = (
            expected_completion and verification_status == "verified"
            if attribution_version == 2
            else expected_completion
        )
        if (
            outcome_status not in _TRACE_TASK_OUTCOME_STATUSES
            or (
                attribution_version == 2
                and (
                    not isinstance(completion_succeeded, bool)
                    or completion_succeeded != expected_completion
                    or verification_status
                    not in {"verified", "failed", "unverified"}
                )
            )
            or not isinstance(goal_achieved, bool)
            or goal_achieved != expected_goal
            or not isinstance(had_tool_errors, bool)
            or not isinstance(errors_recovered, bool)
            or isinstance(tool_error_count, bool)
            or not isinstance(tool_error_count, int)
            or not 0 <= tool_error_count <= _MAX_TRACE_ROUTING_SKILLS
            or had_tool_errors != (tool_error_count > 0)
            or errors_recovered
            != (
                had_tool_errors
                and (
                    completion_succeeded
                    if attribution_version == 2
                    else goal_achieved
                )
            )
            or isinstance(loaded_skill_count, bool)
            or not isinstance(loaded_skill_count, int)
            or not 1 <= loaded_skill_count <= _MAX_TRACE_SELECTED_SKILLS
            or not isinstance(loaded_skills_value, list)
            or len(loaded_skills_value) != loaded_skill_count
            or not isinstance(loaded_skills_truncated, bool)
        ):
            return details

        loaded_skills: list[dict[str, object]] = []
        for raw_item in loaded_skills_value:
            if not isinstance(raw_item, dict):
                return details
            qualified_name = raw_item.get("qualifiedName")
            source = raw_item.get("source")
            directory = raw_item.get("directory")
            content_digest = raw_item.get("contentDigest")
            if (
                not isinstance(qualified_name, str)
                or _TRACE_SKILL_NAME_RE.fullmatch(qualified_name) is None
                or source not in _TRACE_SKILL_SOURCES
                or not isinstance(directory, str)
                or (
                    directory
                    and _TRACE_SKILL_DIRECTORY_RE.fullmatch(directory) is None
                )
                or not isinstance(content_digest, str)
                or _TRACE_SHA256_RE.fullmatch(content_digest) is None
            ):
                return details
            loaded_skills.append(
                {
                    "qualifiedName": qualified_name,
                    "source": source,
                    "directory": directory,
                    "contentDigest": content_digest,
                }
            )
        return {
            "attributionVersion": attribution_version,
            "attributionKind": "task_correlation",
            "outcomeStatus": outcome_status,
            **(
                {
                    "completionSucceeded": completion_succeeded,
                    "verificationStatus": verification_status,
                }
                if attribution_version == 2
                else {}
            ),
            "goalAchieved": goal_achieved,
            "hadToolErrors": had_tool_errors,
            "errorsRecovered": errors_recovered,
            "toolErrorCount": tool_error_count,
            "loadedSkillCount": loaded_skill_count,
            "loadedSkills": loaded_skills,
            "loadedSkillsTruncated": loaded_skills_truncated,
        }
    if event_type == "memory.retrieved":
        if payload.get("retrievalVersion") != 1:
            return details
        details["retrievalVersion"] = 1
        for source_name, response_name in (
            ("candidateCount", "candidateCount"),
            ("selectedCount", "selectedCount"),
            ("suppressedCount", "suppressedCount"),
        ):
            value = payload.get(source_name)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= _MAX_TRACE_MEMORY_COUNT
            ):
                details[response_name] = value
        no_match = payload.get("noMatch")
        if isinstance(no_match, bool):
            details["noMatch"] = no_match
            raw_reason = payload.get("noMatchReason")
            if no_match:
                details["noMatchReason"] = (
                    raw_reason
                    if raw_reason in _TRACE_MEMORY_NO_MATCH_REASONS
                    else "other"
                )
            else:
                details["noMatchReason"] = None
        return details
    if event_type == "memory.rendered":
        if payload.get("renderVersion") != 1:
            return details
        details["renderVersion"] = 1
        rendered_count = payload.get("renderedCount")
        total_tokens = payload.get("totalTokens")
        controller_mode = payload.get("controllerMode")
        injected = payload.get("injected")
        if (
            isinstance(rendered_count, int)
            and not isinstance(rendered_count, bool)
            and 0 <= rendered_count <= _MAX_TRACE_MEMORY_COUNT
        ):
            details["renderedCount"] = rendered_count
        if (
            isinstance(total_tokens, int)
            and not isinstance(total_tokens, bool)
            and 0 <= total_tokens <= _MAX_TRACE_MEMORY_TOKENS
        ):
            details["totalTokens"] = total_tokens
        if controller_mode in _TRACE_MEMORY_CONTROLLER_MODES:
            details["controllerMode"] = controller_mode
        if isinstance(injected, bool):
            details["injected"] = injected
    return details


class DashboardReadModel:
    """Produce one bounded Dashboard snapshot from local read-only sources."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path | None = None,
        session_loader: SessionLoader = list_sessions,
        skill_loader: SkillLoader = discover_skills,
        run_journal: RunJournal | None = None,
        mcp_current_state_loader: McpCurrentStateLoader | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.data_dir = Path(data_dir if data_dir is not None else MINI_CODE_DIR)
        self._session_loader = session_loader
        self._session_loader_is_default = session_loader is list_sessions
        self._skill_loader = skill_loader
        self._skill_loader_is_default = skill_loader is discover_skills
        self._run_journal = run_journal or RunJournal(
            self.workspace, data_dir=self.data_dir, clock=clock
        )
        self._mcp_current_state_loader = mcp_current_state_loader
        self._clock = clock

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        data_dir: str | Path | None = None,
        mcp_current_state_loader: McpCurrentStateLoader | None = None,
    ) -> "DashboardReadModel":
        """Resolve one startup workspace without accepting HTTP input."""
        environment = os.environ if environ is None else environ
        workspace = environment.get("MINI_CODE_DASHBOARD_WORKSPACE") or cwd or Path.cwd()
        return cls(
            workspace=workspace,
            data_dir=data_dir,
            mcp_current_state_loader=mcp_current_state_loader,
        )

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable, independently fault-tolerant snapshot."""
        generated_at = _iso_time(self._clock())
        workspace_status = (
            "live"
            if self.workspace.is_dir()
            and os.access(self.workspace, os.R_OK | os.X_OK)
            else "error"
        )
        workspace_id = self._workspace_id()

        sessions, session_error = self._session_summary()
        skills, skill_error = self._skill_summary()
        memory, memory_errors = self._memory_summary()
        connections, connection_error = self._connection_summary()
        runs, runs_source, run_diagnostics = self._run_summary(generated_at)
        usage_state = self._retained_model_aggregate(generated_at)
        usage_overview = self._overview_usage(usage_state)
        context_aggregate = usage_state["contextAggregate"]
        context_metric = project_context_metric(context_aggregate)
        recovery_metric = project_recovery_metric(context_aggregate)
        working_memory_metric = project_working_memory_metric(context_aggregate)
        sources: dict[str, dict[str, object]] = {
            "workspace": self._source(
                workspace_status,
                generated_at if workspace_status == "live" else None,
                (
                    None
                    if workspace_status == "live"
                    else "Workspace does not exist or is not readable."
                ),
            ),
            "sessions": self._source(
                "error" if session_error else "live",
                None if session_error else generated_at,
                "The session index could not be read." if session_error else None,
            ),
            "memory": self._source(
                "error" if memory_errors else "live",
                generated_at,
                (
                    "One or more memory scopes could not be read."
                    if memory_errors
                    else None
                ),
            ),
            "skills": self._source(
                "error" if skill_error else "live",
                None if skill_error else generated_at,
                "Skills could not be discovered." if skill_error else None,
            ),
            "connections": self._source(
                "error" if connection_error else "live",
                generated_at,
                (
                    "Gateway is live; MCP configuration could not be read."
                    if connection_error
                    else "Gateway is live; MCP live status is unavailable."
                ),
            ),
            "runs": runs_source,
            "usage": usage_state["source"],
        }

        diagnostics: list[dict[str, str]] = []
        if workspace_status == "error":
            diagnostics.append(
                {
                    "source": "workspace",
                    "code": "workspace_unreadable",
                    "message": "Workspace does not exist or is not readable.",
                }
            )
        if session_error:
            diagnostics.append(
                {
                    "source": "sessions",
                    "code": "index_read_failed",
                    "message": "The session index could not be read.",
                }
            )
        diagnostics.extend(
            {
                "source": "memory",
                "code": "scope_read_failed",
                "message": f"The {scope} memory scope could not be read.",
            }
            for scope in memory_errors
        )
        if connection_error:
            diagnostics.append(
                {
                    "source": "connections",
                    "code": "mcp_config_read_failed",
                    "message": "MCP configuration could not be read.",
                }
            )
        if skill_error:
            diagnostics.append(
                {
                    "source": "skills",
                    "code": "discovery_failed",
                    "message": "Skills could not be discovered.",
                }
            )
        diagnostics.extend(run_diagnostics)
        for diagnostic in usage_state["diagnostics"]:
            if (
                len(diagnostics) < _MAX_DIAGNOSTICS
                and diagnostic not in diagnostics
            ):
                diagnostics.append(diagnostic)

        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "status": "partial",
            "workspace": {
                "id": workspace_id,
                "name": self.workspace.name or str(self.workspace),
                # Schema-v1 compatibility key; machine paths are never exposed.
                "path": None,
                "status": workspace_status,
            },
            "overview": {
                "sessions": sessions,
                "memory": memory,
                "skills": skills,
                "connections": connections,
                "runs": runs,
                "usage": usage_overview,
                "context": context_metric,
                "recovery": recovery_metric,
                "workingMemory": working_memory_metric,
            },
            "sources": sources,
            "diagnostics": diagnostics,
        }
        return _redact_value(payload)

    def ops(self) -> dict[str, object]:
        """Return bounded retained Model, Cost, Tool, and Failure telemetry."""
        generated_at = _iso_time(self._clock())
        state = self._retained_model_aggregate(generated_at)
        aggregate = state["aggregate"]
        cost_aggregate = state["costAggregate"]
        tool_aggregate = state["toolAggregate"]
        failure_aggregate = state["failureAggregate"]
        context_aggregate = state["contextAggregate"]
        cost_metric = project_cost_metric(cost_aggregate)
        tool_metric = project_tool_metric(tool_aggregate)
        failure_metric = project_failure_metric(failure_aggregate)
        context_metric = project_context_metric(context_aggregate)
        recovery_metric = project_recovery_metric(context_aggregate)
        working_memory_metric = project_working_memory_metric(context_aggregate)
        context_value = context_metric["value"] or {}
        recovery_value = recovery_metric["value"] or {}
        working_memory_value = working_memory_metric["value"] or {}
        tool_value = tool_metric["value"] or {}
        failure_value = failure_metric["value"] or {}
        tokens_metric = aggregate.tokens_metric()
        token_value = tokens_metric["value"] or {}
        duration_metric = aggregate.duration_metric()
        duration_value = duration_metric["value"] or {}
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": state["source"],
            "coverage": {
                "historical": "partial",
                "scope": "model-usage-duration-cost-tool-failure-context-working-memory",
                "cost": cost_metric["status"],
                "tools": tool_metric["status"],
                "failures": failure_metric["status"],
                "context": context_metric["status"],
                "recovery": recovery_metric["status"],
                "workingMemory": working_memory_metric["status"],
                "runScanLimit": _MAX_USAGE_RUNS,
                "eventScanLimitPerRun": _MAX_USAGE_EVENTS_PER_RUN,
            },
            "summary": {
                "retainedRuns": state["retainedRuns"],
                "scannedRuns": state["scannedRuns"],
                "completedModelCalls": aggregate.completed_calls,
                "failedModelCalls": aggregate.failed_calls,
                "providerCalls": len(aggregate._calls("provider")),
                "estimatedCalls": len(aggregate._calls("estimated")),
                "unavailableCalls": len(aggregate._calls("unavailable")),
                "pricedCalls": len(cost_aggregate.priced),
                "unavailableCostCalls": len(
                    cost_aggregate.unavailable_reasons
                ),
                "missingCostCalls": cost_aggregate.missing_calls,
                "invalidCostEvents": cost_aggregate.invalid_events,
                "observedToolCalls": tool_value.get("observedCalls", 0),
                "completedToolCalls": tool_value.get("completedCalls", 0),
                "successfulToolCalls": tool_value.get("successfulCalls", 0),
                "toolErrorCalls": tool_value.get("errorCalls", 0),
                "uniqueTools": tool_value.get("uniqueTools", 0),
                "affectedRuns": failure_value.get("affectedRuns", 0),
                "modelFailureAttempts": failure_value.get("modelFailures", 0),
                "runFailures": failure_value.get("runFailures", 0),
                "interruptedRuns": failure_value.get("interruptedRuns", 0),
                "cancelledRuns": failure_value.get("cancelledRuns", 0),
                "invalidToolEvents": tool_aggregate.invalid_events,
                "observedCompactions": context_value.get("observedCompactions", 0),
                "directCompactions": context_value.get("directCompactions", 0),
                "recoveryCompactions": context_value.get("recoveryCompactions", 0),
                "knownTokensFreed": context_value.get("knownTokensFreed", 0),
                "tokenUnknownCompactions": context_value.get("tokenUnknownCompactions", 0),
                "messagesRemoved": context_value.get("messagesRemoved", 0),
                "recoveryAttempts": recovery_value.get("attempts", 0),
                "recoveredAttempts": recovery_value.get("recoveredAttempts", 0),
                "notRecoveredAttempts": recovery_value.get("notRecoveredAttempts", 0),
                "workingMemorySnapshots": working_memory_value.get("observedSnapshots", 0),
                "runsWithWorkingMemorySnapshots": working_memory_value.get("runsWithSnapshots", 0),
                "invalidContextEvents": context_aggregate.invalid_events,
            },
            "usage": {
                "provider": aggregate.usage_bucket("provider"),
                "estimated": aggregate.usage_bucket("estimated"),
                "combined": {
                    "status": tokens_metric["status"],
                    **{
                        field: token_value.get(field)
                        for field in _TRACE_MODEL_USAGE_FIELDS
                    },
                    "totalTokens": token_value.get("totalTokens"),
                    "provenance": token_value.get(
                        "provenance", "unavailable"
                    ),
                },
            },
            "duration": {
                "status": duration_metric["status"],
                "modelCalls": duration_value.get("modelCalls", 0),
                "completedCalls": duration_value.get("completedCalls", 0),
                "failedCalls": duration_value.get("failedCalls", 0),
                "observedCalls": duration_value.get("observedCalls", 0),
                "totalMs": duration_value.get("totalMs"),
                "averageMs": duration_value.get("averageMs"),
            },
            "cost": cost_metric,
            "costBreakdown": project_cost_breakdown(cost_aggregate),
            "tools": tool_metric,
            "toolBreakdown": project_tool_breakdown(tool_aggregate),
            "failures": failure_metric,
            "failureBreakdown": project_failure_breakdown(failure_aggregate),
            "context": context_metric,
            "recovery": recovery_metric,
            "workingMemory": working_memory_metric,
            "contextBreakdown": project_context_breakdown(context_aggregate),
            "diagnostics": state["diagnostics"],
        }
        return _redact_value(payload)

    def _retained_model_aggregate(
        self, generated_at: str
    ) -> dict[str, object]:
        aggregate = _ModelObservationAggregate()
        cost_items: list[CostAggregate] = []
        tool_items: list[ToolAggregate] = []
        failure_items: list[FailureAggregate] = []
        context_items: list[ContextAggregate] = []
        try:
            page = self._run_journal.list_runs(limit=_MAX_USAGE_RUNS)
        except Exception:  # noqa: BLE001 - isolate usage from other Snapshot sources
            aggregate._diagnostic(
                "usage_journal_read_failed",
                "RunJournal usage observations could not be read.",
            )
            cost_aggregate = merge_cost_aggregates(
                [], journal_read_failed=True
            )
            tool_aggregate = merge_tool_aggregates(
                [], journal_read_failed=True
            )
            failure_aggregate = merge_failure_aggregates(
                [], journal_read_failed=True
            )
            context_aggregate = merge_context_aggregates(
                [], journal_read_failed=True
            )
            diagnostics = [
                *aggregate.diagnostics,
                *cost_aggregate.diagnostics,
                *tool_aggregate.diagnostics,
                *failure_aggregate.diagnostics,
                *context_aggregate.diagnostics,
            ][:_MAX_DIAGNOSTICS]
            return {
                "aggregate": aggregate,
                "costAggregate": cost_aggregate,
                "toolAggregate": tool_aggregate,
                "failureAggregate": failure_aggregate,
                "contextAggregate": context_aggregate,
                "retainedRuns": None,
                "scannedRuns": 0,
                "diagnostics": diagnostics,
                "source": self._source(
                    "error",
                    None,
                    "Model, Cost, Tool, Failure, Context, and WorkingMemory observations could not be read.",
                ),
            }
        for diagnostic in page.diagnostics:
            if (
                len(aggregate.diagnostics) < _MAX_DIAGNOSTICS
                and diagnostic not in aggregate.diagnostics
            ):
                aggregate.diagnostics.append(diagnostic)
        for record in page.items:
            model_item, cost_item, tool_item, failure_item, context_item = (
                self._run_observation_aggregates(
                    record.id,
                    run_status=getattr(record, "status", "unknown"),
                    run_source=getattr(record, "source", "unknown"),
                )
            )
            aggregate.merge(model_item)
            cost_items.append(cost_item)
            tool_items.append(tool_item)
            failure_items.append(failure_item)
            context_items.append(context_item)
        if page.has_more:
            aggregate._diagnostic(
                "usage_runs_limited",
                "Usage aggregation reached the Dashboard Run scan limit.",
            )
        cost_aggregate = merge_cost_aggregates(
            cost_items,
            limited=page.has_more,
            max_aggregates=_MAX_USAGE_RUNS,
        )
        tool_aggregate = merge_tool_aggregates(
            tool_items,
            limited=page.has_more,
            max_aggregates=_MAX_USAGE_RUNS,
        )
        failure_aggregate = merge_failure_aggregates(
            failure_items,
            limited=page.has_more,
            max_aggregates=_MAX_USAGE_RUNS,
        )
        context_aggregate = merge_context_aggregates(
            context_items,
            limited=page.has_more,
            max_aggregates=_MAX_USAGE_RUNS,
        )
        for diagnostic in (
            *cost_aggregate.diagnostics,
            *tool_aggregate.diagnostics,
            *failure_aggregate.diagnostics,
            *context_aggregate.diagnostics,
        ):
            if (
                len(aggregate.diagnostics) < _MAX_DIAGNOSTICS
                and diagnostic not in aggregate.diagnostics
            ):
                aggregate.diagnostics.append(diagnostic)
        tokens_status = aggregate.tokens_metric()["status"]
        duration_status = aggregate.duration_metric()["status"]
        cost_status = project_cost_metric(cost_aggregate)["status"]
        tool_status = project_tool_metric(tool_aggregate)["status"]
        failure_status = project_failure_metric(failure_aggregate)["status"]
        context_status = project_context_metric(context_aggregate)["status"]
        recovery_status = project_recovery_metric(context_aggregate)["status"]
        working_memory_status = project_working_memory_metric(context_aggregate)["status"]
        model_calls = aggregate.completed_calls + aggregate.failed_calls
        if model_calls == 0:
            status = "partial" if aggregate.diagnostics else "unavailable"
        elif (
            aggregate.diagnostics
            or (model_calls > 0 and tokens_status != "live")
            or (model_calls > 0 and duration_status != "live")
            or (model_calls > 0 and cost_status != "complete")
            or tool_status == "partial"
            or failure_status == "partial"
            or context_status == "partial"
            or recovery_status == "partial"
            or working_memory_status == "partial"
        ):
            status = "partial"
        else:
            status = "live"
        messages = {
            "live": (
                "Retained RunJournal Model, Cost, Tool, Failure, Context, and WorkingMemory observations "
                "are live; historical Runs were not backfilled."
            ),
            "partial": (
                "Retained RunJournal Model, Cost, Tool, Failure, Context, and WorkingMemory coverage is "
                "partial; historical Runs were not backfilled."
            ),
            "unavailable": (
                "No retained Model, Cost, Tool, Failure, Context, or WorkingMemory observations are "
                "available; historical Runs were not backfilled."
            ),
        }
        updated_at = page.items[0].updated_at if page.items else generated_at
        return {
            "aggregate": aggregate,
            "costAggregate": cost_aggregate,
            "toolAggregate": tool_aggregate,
            "failureAggregate": failure_aggregate,
            "contextAggregate": context_aggregate,
            "retainedRuns": page.known_total,
            "scannedRuns": len(page.items),
            "diagnostics": list(aggregate.diagnostics)[:_MAX_DIAGNOSTICS],
            "source": self._source(status, updated_at, messages[status]),
        }

    @staticmethod
    def _overview_usage(state: Mapping[str, object]) -> dict[str, object]:
        aggregate = state["aggregate"]
        tokens_metric = aggregate.tokens_metric()
        token_value = tokens_metric["value"] or {}
        duration_metric = aggregate.duration_metric()
        duration_value = duration_metric["value"] or {}
        cost_metric = project_cost_metric(state["costAggregate"])
        tool_metric = project_tool_metric(state["toolAggregate"])
        failure_metric = project_failure_metric(state["failureAggregate"])
        return {
            "status": state["source"]["status"],
            "inputTokens": token_value.get("inputTokens"),
            "outputTokens": token_value.get("outputTokens"),
            "cacheReadTokens": token_value.get("cacheReadTokens"),
            "cacheCreationTokens": token_value.get("cacheCreationTokens"),
            "providerCalls": token_value.get("providerCalls", 0),
            "estimatedCalls": token_value.get("estimatedCalls", 0),
            "unavailableCalls": token_value.get("unavailableCalls", 0),
            "provenance": token_value.get("provenance", "unavailable"),
            "durationMs": duration_value.get("totalMs"),
            "costUsd": None,
            "cost": cost_metric,
            "tools": tool_metric,
            "failures": failure_metric,
            "coverage": "retained-run-journal",
            "historical": "partial",
            # Compatibility aliases remain nullable and derived from the same data.
            "tokensIn": token_value.get("inputTokens"),
            "tokensOut": token_value.get("outputTokens"),
            "toolCalls": None,
            "errors": None,
        }

    def runs(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Return current-workspace RunJournal summaries and coverage truth."""
        if status is not None and status not in RUN_STATUSES:
            raise DashboardReadError(400, "invalid_status", "Run status is invalid.")
        if source is not None and source not in RUN_SOURCES:
            raise DashboardReadError(400, "invalid_source", "Run source is invalid.")
        page_limit = self._request_page_limit(limit)
        try:
            page = self._run_journal.list_runs(
                status=status,
                source=source,
                limit=page_limit,
                cursor=cursor,
            )
        except RunJournalValidationError as exc:
            code = "invalid_cursor" if cursor not in (None, "") else "invalid_query"
            raise DashboardReadError(400, code, "Run query is invalid.") from exc

        generated_at = _iso_time(self._clock())
        diagnostics = list(page.diagnostics)[:_MAX_DIAGNOSTICS]
        updated_at = page.items[0].updated_at if page.items else generated_at
        items: list[dict[str, object]] = []
        for record in page.items:
            (
                _model_aggregate,
                cost_aggregate,
                tool_aggregate,
                failure_aggregate,
                context_aggregate,
            ) = self._run_observation_aggregates(
                record.id,
                run_status=record.status,
                run_source=record.source,
            )
            for diagnostic in (
                *cost_aggregate.diagnostics,
                *tool_aggregate.diagnostics,
                *failure_aggregate.diagnostics,
                *context_aggregate.diagnostics,
            ):
                if (
                    len(diagnostics) < _MAX_DIAGNOSTICS
                    and diagnostic not in diagnostics
                ):
                    diagnostics.append(diagnostic)
            items.append(
                {
                    "id": record.id,
                    "status": record.status,
                    "source": record.source,
                    "title": _redact_text(record.title, max_chars=240),
                    "sessionId": record.session_id,
                    "createdAt": record.created_at,
                    "startedAt": record.started_at,
                    "completedAt": record.completed_at,
                    "updatedAt": record.updated_at,
                    "lastSequence": record.last_sequence,
                    "eventCount": record.event_count,
                    "cost": project_run_cost_summary(cost_aggregate),
                    "tools": project_run_tool_summary(tool_aggregate),
                    "failures": project_run_failure_summary(failure_aggregate),
                    **project_run_context_summary(context_aggregate),
                }
            )
        source_status = (
            "error" if page.diagnostics else "partial" if diagnostics else "live"
        )
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                source_status,
                updated_at,
                (
                    "One or more Run records could not be read; execution trace coverage is partial."
                    if diagnostics
                    else "Lifecycle, Model, Cost, Tool, Assistant, Skill, and Memory observation is live for TUI, Headless, and Gateway; historical Runs were not backfilled."
                ),
            ),
            "coverage": dict(_RUN_COVERAGE),
            "summary": {
                "knownTotal": page.known_total,
                "byStatus": dict(page.by_status),
            },
            "items": items,
            "page": {
                "limit": page.limit,
                "hasMore": page.has_more,
                "nextCursor": page.next_cursor,
            },
            "filters": {"status": status, "source": source},
            "diagnostics": diagnostics,
        }
        return _redact_value(payload)

    def run_detail(
        self,
        run_id: str,
        *,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Return one safe Run summary plus a bounded event-summary page."""
        page_limit = self._request_page_limit(
            limit, default=_DEFAULT_SESSION_MESSAGE_LIMIT
        )
        try:
            record = self._run_journal.get_run(run_id)
        except RunJournalValidationError as exc:
            raise DashboardReadError(
                400, "invalid_run_id", "Run ID is invalid."
            ) from exc
        if record is None:
            raise DashboardReadError(404, "run_not_found", "Run was not found.")
        try:
            event_page = self._run_journal.list_events(
                run_id,
                limit=page_limit,
                cursor=cursor,
            )
        except RunJournalValidationError as exc:
            raise DashboardReadError(
                400, "invalid_cursor", "Cursor is invalid."
            ) from exc
        except RunJournalNotFoundError as exc:
            raise DashboardReadError(
                404, "run_not_found", "Run was not found."
            ) from exc

        summaries = {
            "run.queued": "Run queued",
            "run.started": "Run started",
            "run.completed": "Run completed",
            "run.failed": "Run failed",
            "run.interrupted": "Run interrupted",
            "run.cancel_requested": "Cancellation requested",
            "run.cancelled": "Run cancelled",
            "model.started": "Model request started",
            "model.completed": "Model request completed",
            "model.costed": "Cost observation recorded",
            "model.failed": "Model request failed",
            "tool.started": "Tool started",
            "tool.finished": "Tool finished",
            "mcp.runtime.observed": "MCP runtime observed",
            "permission.requested": "Permission requested",
            "permission.decided": "Permission decided",
            "assistant.completed": "Assistant response completed",
            "execution.stopped": "Execution recovery circuit opened",
            "task.outcome": "Canonical task outcome recorded",
            "skill.routed": "Skill routing recorded",
            "skill.loaded": "Skill loaded",
            "skill.attributed": "Skill outcome attributed",
            "memory.retrieved": "Memory retrieval recorded",
            "memory.rendered": "Memory rendering recorded",
            "working_memory.observed": "Working memory observed",
            "context.compacted": "Context compacted",
            "context.compaction.failed": "Context compaction failed",
            "recovery.started": "Recovery started",
            "recovery.completed": "Recovery completed",
        }
        (
            model_aggregate,
            cost_aggregate,
            tool_aggregate,
            failure_aggregate,
            context_aggregate,
        ) = self._run_observation_aggregates(
            run_id,
            run_status=record.status,
            run_source=record.source,
        )
        diagnostics = list(event_page.diagnostics)
        for diagnostic in (
            *model_aggregate.diagnostics,
            *cost_aggregate.diagnostics,
            *tool_aggregate.diagnostics,
            *failure_aggregate.diagnostics,
        ):
            if (
                len(diagnostics) < _MAX_DIAGNOSTICS
                and diagnostic not in diagnostics
            ):
                diagnostics.append(diagnostic)
        diagnostics = diagnostics[:_MAX_DIAGNOSTICS]
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-only",
            "source": self._source(
                (
                    "error"
                    if event_page.diagnostics
                    else "partial"
                    if (
                        model_aggregate.diagnostics
                        or cost_aggregate.diagnostics
                        or tool_aggregate.diagnostics
                        or failure_aggregate.diagnostics
                        or context_aggregate.diagnostics
                    )
                    else "live"
                ),
                record.updated_at,
                "Some Run events could not be read."
                if diagnostics
                else None,
            ),
            "coverage": dict(_RUN_COVERAGE),
            "run": {
                "id": record.id,
                "status": record.status,
                "source": record.source,
                "title": _redact_text(record.title, max_chars=240),
                "sessionId": record.session_id,
                "createdAt": record.created_at,
                "startedAt": record.started_at,
                "completedAt": record.completed_at,
                "updatedAt": record.updated_at,
                "eventCount": record.event_count,
                "lastSequence": record.last_sequence,
            },
            "events": [
                {
                    "id": event.event_id,
                    "sequence": event.sequence,
                    "timestamp": event.timestamp,
                    "type": event.type,
                    "step": event.step,
                    "summary": summaries.get(event.type, "Run event"),
                    "details": _run_event_details(event.type, event.payload),
                }
                for event in event_page.items
            ],
            "page": {
                "limit": event_page.limit,
                "hasMore": event_page.has_more,
                "nextCursor": event_page.next_cursor,
            },
            "metrics": {
                "cost": project_cost_metric(cost_aggregate),
                "tokens": model_aggregate.tokens_metric(),
                "duration": model_aggregate.duration_metric(),
                "toolCalls": project_tool_metric(tool_aggregate),
                "errors": project_failure_metric(failure_aggregate),
                "context": project_context_metric(context_aggregate),
                "recovery": project_recovery_metric(context_aggregate),
                "workingMemory": project_working_memory_metric(context_aggregate),
            },
            "diagnostics": diagnostics,
        }
        return _redact_value(payload)

    def _run_observation_aggregates(
        self,
        run_id: str,
        *,
        run_status: str = "unknown",
        run_source: str = "unknown",
    ) -> tuple[
        _ModelObservationAggregate,
        CostAggregate,
        ToolAggregate,
        FailureAggregate,
        ContextAggregate,
    ]:
        """Read once and project one Run's Model, Cost, Tool, Failure, Context, and WorkingMemory facts."""
        aggregate = _ModelObservationAggregate()
        events: list[object] = []
        cursor: str | None = None
        has_more = False
        limited = False
        journal_read_failed = False
        while len(events) < _MAX_USAGE_EVENTS_PER_RUN:
            remaining = _MAX_USAGE_EVENTS_PER_RUN - len(events)
            try:
                page = self._run_journal.list_events(
                    run_id,
                    limit=min(_USAGE_EVENT_PAGE_LIMIT, remaining),
                    cursor=cursor,
                )
            except Exception:  # noqa: BLE001 - isolate one Run's usage scan
                journal_read_failed = True
                aggregate._diagnostic(
                    "run_usage_read_failed",
                    "One Run's Model observations could not be read.",
                )
                break
            has_more = page.has_more
            if page.diagnostics:
                journal_read_failed = True
            for diagnostic in page.diagnostics:
                if (
                    len(aggregate.diagnostics) < _MAX_DIAGNOSTICS
                    and diagnostic not in aggregate.diagnostics
                ):
                    aggregate.diagnostics.append(diagnostic)
            events.extend(page.items)
            if not page.has_more:
                break
            if not page.next_cursor or not page.items:
                aggregate._diagnostic(
                    "usage_scan_incomplete",
                    "Model observation scanning could not continue safely.",
                )
                limited = True
                break
            cursor = page.next_cursor
        if len(events) >= _MAX_USAGE_EVENTS_PER_RUN and has_more:
            limited = True
            aggregate._diagnostic(
                "usage_events_limited",
                "Model observations reached the Dashboard event scan limit.",
            )
        aggregate.observe(events)
        cost = aggregate_run_cost(
            events,
            run_source=run_source,
            limited=limited,
            journal_read_failed=journal_read_failed,
            max_events=_MAX_USAGE_EVENTS_PER_RUN,
        )
        tools = aggregate_run_tools(
            events,
            run_source=run_source,
            limited=limited,
            journal_read_failed=journal_read_failed,
            max_events=_MAX_USAGE_EVENTS_PER_RUN,
        )
        failures = aggregate_run_failures(
            events,
            run_status=run_status,
            run_source=run_source,
            limited=limited,
            journal_read_failed=journal_read_failed,
            max_events=_MAX_USAGE_EVENTS_PER_RUN,
        )
        context = aggregate_run_context(
            events,
            run_source=run_source,
            limited=limited,
            journal_read_failed=journal_read_failed,
            max_events=_MAX_USAGE_EVENTS_PER_RUN,
        )
        return aggregate, cost, tools, failures, context

    def _run_model_aggregate(self, run_id: str) -> _ModelObservationAggregate:
        """Compatibility helper for existing usage-only callers."""
        aggregate, _cost, _tools, _failures, _context = self._run_observation_aggregates(
            run_id
        )
        return aggregate

    def sessions(
        self,
        *,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Return bounded current-workspace Session metadata."""
        page_limit = self._request_page_limit(limit)
        generated_at = _iso_time(self._clock())
        records, diagnostics, fatal = self._session_records()
        sort_key = lambda item: (  # noqa: E731 - shared with cursor comparison
            -float(item.updated_at),
            -float(item.created_at),
            item.session_id,
        )
        records.sort(key=sort_key)
        current = [item for item in records if self._same_workspace(item.workspace)]
        latest = max((item.updated_at for item in current), default=None)
        if cursor not in (None, ""):
            try:
                cursor_values = self._decode_cursor("sessions", cursor)
                if (
                    len(cursor_values) != 3
                    or isinstance(cursor_values[0], bool)
                    or isinstance(cursor_values[1], bool)
                    or not isinstance(cursor_values[0], (int, float))
                    or not isinstance(cursor_values[1], (int, float))
                    or not isinstance(cursor_values[2], str)
                    or not math.isfinite(float(cursor_values[0]))
                    or not math.isfinite(float(cursor_values[1]))
                    or not _SESSION_ID_RE.fullmatch(cursor_values[2])
                ):
                    raise ValueError("invalid sessions cursor")
                cursor_key = (
                    -float(cursor_values[0]),
                    -float(cursor_values[1]),
                    cursor_values[2],
                )
            except ValueError as exc:
                raise DashboardReadError(
                    400, "invalid_cursor", "Cursor is invalid."
                ) from exc
            current = [item for item in current if sort_key(item) > cursor_key]
        page_records = current[:page_limit]
        has_more = len(current) > page_limit
        items = [
            {
                "id": metadata.session_id,
                "createdAt": self._timestamp(metadata.created_at),
                "updatedAt": self._timestamp(metadata.updated_at),
                "title": _redact_text(
                    metadata.first_message or "Untitled session",
                    max_chars=_MAX_SESSION_PREVIEW_CHARS,
                ),
                "lastMessagePreview": _redact_text(
                    metadata.last_message,
                    max_chars=_MAX_SESSION_PREVIEW_CHARS,
                ),
                "messageCount": metadata.message_count,
                "workspaceId": self._workspace_id(),
                "status": "saved",
            }
            for metadata in page_records
        ]
        source_status = "error" if fatal or diagnostics else "live"
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                source_status,
                self._timestamp(latest) if latest is not None and not fatal else None,
                (
                    "The session index could not be read."
                    if fatal
                    else (
                        "One or more session records could not be read."
                        if diagnostics
                        else None
                    )
                ),
            ),
            "items": items,
            "page": {
                "limit": page_limit,
                "hasMore": has_more,
                "nextCursor": (
                    self._encode_cursor(
                        "sessions",
                        [
                            page_records[-1].updated_at,
                            page_records[-1].created_at,
                            page_records[-1].session_id,
                        ],
                    )
                    if has_more and page_records
                    else None
                ),
            },
            "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        }
        return _redact_value(payload)

    def session_detail(
        self,
        session_id: str,
        *,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Return one authorized, bounded, redacted Session transcript page."""
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise DashboardReadError(
                400, "invalid_session_id", "Session ID is invalid."
            )
        page_limit = self._request_page_limit(
            limit, default=_DEFAULT_SESSION_MESSAGE_LIMIT
        )
        offset = 0
        if cursor not in (None, ""):
            try:
                cursor_values = self._decode_cursor("session_messages", cursor)
                if (
                    len(cursor_values) != 2
                    or cursor_values[0] != session_id
                    or isinstance(cursor_values[1], bool)
                    or not isinstance(cursor_values[1], int)
                    or cursor_values[1] < 0
                    or cursor_values[1] > _MAX_SESSION_MESSAGES
                ):
                    raise ValueError("invalid cursor")
                offset = cursor_values[1]
            except ValueError as exc:
                raise DashboardReadError(
                    400, "invalid_cursor", "Cursor is invalid."
                ) from exc
        generated_at = _iso_time(self._clock())
        records, index_diagnostics, fatal = self._session_records()
        if fatal:
            return _redact_value(
                {
                    "schemaVersion": 1,
                    "generatedAt": generated_at,
                    "mode": "read-only",
                    "source": self._source(
                        "error", None, "The session index could not be read."
                    ),
                    "session": None,
                    "messages": [],
                    "page": {
                        "limit": page_limit,
                        "hasMore": False,
                        "nextCursor": None,
                    },
                    "diagnostics": index_diagnostics[:_MAX_DIAGNOSTICS],
                }
            )
        metadata = next(
            (
                item
                for item in records
                if item.session_id == session_id
                and self._same_workspace(item.workspace)
            ),
            None,
        )
        if metadata is None:
            raise DashboardReadError(
                404, "session_not_found", "Session was not found."
            )

        session_projection = {
            "id": metadata.session_id,
            "createdAt": self._timestamp(metadata.created_at),
            "updatedAt": self._timestamp(metadata.updated_at),
            "messageCount": metadata.message_count,
            "workspaceId": self._workspace_id(),
            "status": "saved",
        }
        diagnostics = list(index_diagnostics)
        try:
            session_data, session_diagnostics = self._read_session_data(session_id)
            diagnostics.extend(session_diagnostics)
            if not self._same_workspace(str(session_data.get("workspace", ""))):
                raise ValueError("session workspace mismatch")
            raw_messages = session_data.get("messages", [])
            if not isinstance(raw_messages, list) or len(raw_messages) > _MAX_SESSION_MESSAGES:
                raise ValueError("invalid session messages")
        except Exception:  # noqa: BLE001 - source-local safe error
            diagnostics.append(
                self._diagnostic(
                    "sessions",
                    "session_read_failed",
                    "The Session file could not be read.",
                )
            )
            return _redact_value(
                {
                    "schemaVersion": 1,
                    "generatedAt": generated_at,
                    "mode": "read-only",
                    "source": self._source(
                        "error", None, "The Session file could not be read."
                    ),
                    "session": session_projection,
                    "messages": [],
                    "page": {
                        "limit": page_limit,
                        "hasMore": False,
                        "nextCursor": None,
                    },
                    "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
                }
            )

        visible: list[tuple[int, str, str]] = []
        for index, raw_message in enumerate(raw_messages):
            if not isinstance(raw_message, dict):
                diagnostics.append(
                    self._diagnostic(
                        "sessions",
                        "message_invalid",
                        "A malformed Session message was skipped.",
                    )
                )
                continue
            role = raw_message.get("role")
            content = raw_message.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str):
                diagnostics.append(
                    self._diagnostic(
                        "sessions",
                        "message_invalid",
                        "A malformed Session message was skipped.",
                    )
                )
                continue
            visible.append((index, role, content))

        messages: list[dict[str, object]] = []
        used_chars = 0
        budget_applied = False
        consumed = 0
        for index, role, raw_content in visible[offset : offset + page_limit]:
            content, truncated = _redact_bounded_text(
                raw_content, _MAX_SESSION_MESSAGE_CHARS
            )
            remaining = _MAX_SESSION_RESPONSE_CONTENT_CHARS - used_chars
            if remaining <= 0:
                budget_applied = True
                break
            if len(content) > remaining:
                content = content[:remaining].rstrip() + "…"
                truncated = True
                budget_applied = True
            messages.append(
                {
                    "index": index,
                    "role": role,
                    "content": content,
                    "truncated": truncated,
                }
            )
            consumed += 1
            used_chars += len(content)
            if used_chars >= _MAX_SESSION_RESPONSE_CONTENT_CHARS:
                budget_applied = True
                break
        next_offset = offset + consumed
        has_more = next_offset < len(visible)
        if budget_applied:
            diagnostics.append(
                self._diagnostic(
                    "sessions",
                    "response_budget_applied",
                    "Session messages were truncated to the response budget.",
                )
            )
        has_source_error = any(
            item["code"] != "response_budget_applied" for item in diagnostics
        )
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                "error" if has_source_error else "live",
                self._timestamp(metadata.updated_at),
                (
                    "Some Session content was unavailable."
                    if has_source_error
                    else None
                ),
            ),
            "session": {
                **session_projection,
                "visibleMessageCount": len(visible),
            },
            "messages": messages,
            "page": {
                "limit": page_limit,
                "hasMore": has_more,
                "nextCursor": (
                    self._encode_cursor(
                        "session_messages", [session_id, next_offset]
                    )
                    if has_more and consumed
                    else None
                ),
            },
            "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        }
        return _redact_value(payload)

    def _read_session_data(
        self, session_id: str
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        sessions_root = self.data_dir / "sessions"
        session_path = sessions_root / f"{session_id}.json"
        resolved_session = self._validate_source_file(session_path, self.data_dir)
        total_bytes = resolved_session.stat().st_size
        parsed = json.loads(resolved_session.read_text(encoding="utf-8"))
        if (
            not isinstance(parsed, dict)
            or parsed.get("session_id") != session_id
            or not isinstance(parsed.get("workspace"), str)
        ):
            raise ValueError("invalid Session file")
        base_generation = persistence_generation(parsed)
        messages = parsed.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError("invalid Session messages")
        messages = list(messages)
        transcripts = parsed.get("transcript_entries", [])
        created_at = parsed.get("created_at")
        if (
            not isinstance(transcripts, list)
            or isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or not math.isfinite(float(created_at))
        ):
            raise ValueError("invalid Session file")
        transcript_count = len(transcripts)
        diagnostics: list[dict[str, str]] = []

        delta_root = sessions_root / "deltas"
        delta_dir = delta_root / session_id
        if delta_dir.exists():
            resolved_root = self.data_dir.resolve()
            resolved_delta_dir = delta_dir.resolve(strict=True)
            if (
                not resolved_delta_dir.is_relative_to(resolved_root)
                or not resolved_delta_dir.is_dir()
            ):
                raise OSError("Session delta directory escapes its configured root")
            delta_files = sorted(
                path
                for path in resolved_delta_dir.iterdir()
                if re.fullmatch(r"delta_[0-9]{4,8}\.json", path.name)
            )
            if len(delta_files) > _MAX_SESSION_DELTA_FILES:
                raise ValueError("Session delta count exceeds the Dashboard limit")
            for delta_path in delta_files:
                try:
                    resolved_delta = self._validate_source_file(
                        delta_path, self.data_dir
                    )
                    total_bytes += resolved_delta.stat().st_size
                    if total_bytes > _MAX_SOURCE_FILE_BYTES:
                        raise ValueError("Session data exceeds the Dashboard read limit")
                    delta = json.loads(resolved_delta.read_text(encoding="utf-8"))
                    if not isinstance(delta, dict):
                        raise ValueError("invalid Session delta")
                    validated = validate_session_delta(
                        delta,
                        session_id=session_id,
                        base_generation=base_generation,
                        current_message_count=len(messages),
                        current_transcript_count=transcript_count,
                        workspace=str(parsed["workspace"]),
                        created_at=float(created_at),
                    )
                    if validated is None:
                        continue
                    delta_messages = validated.messages
                    offset = validated.msg_offset
                except Exception:  # noqa: BLE001 - isolate one corrupt delta
                    diagnostics.append(
                        self._diagnostic(
                            "sessions",
                            "delta_invalid",
                            "A malformed Session delta was skipped.",
                        )
                    )
                    continue
                if offset >= len(messages):
                    messages.extend(delta_messages)
                elif offset + len(delta_messages) > len(messages):
                    overlap = len(messages) - offset
                    messages.extend(delta_messages[overlap:])
                transcript_count = max(
                    transcript_count,
                    validated.transcript_offset + len(validated.transcripts),
                )
                if len(messages) > _MAX_SESSION_MESSAGES:
                    raise ValueError("Session message count exceeds the Dashboard limit")
        parsed["messages"] = messages
        return parsed, diagnostics

    def memory(
        self,
        *,
        scope: str | None = None,
        tier: str | None = None,
        category: str | None = None,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Return bounded Memory aggregates and read-only entry projections."""
        page_limit = self._request_page_limit(limit)
        if scope is not None and scope not in {item.value for item in MemoryScope}:
            raise DashboardReadError(400, "invalid_scope", "Memory scope is invalid.")
        if tier is not None and tier not in {item.value for item in MemoryTier}:
            raise DashboardReadError(400, "invalid_tier", "Memory tier is invalid.")
        if category is not None and not _CATEGORY_RE.fullmatch(category):
            raise DashboardReadError(
                400, "invalid_category", "Memory category is invalid."
            )
        generated_at = _iso_time(self._clock())
        scope_paths = {
            MemoryScope.USER: (self.data_dir / "memory", self.data_dir),
            MemoryScope.PROJECT: (
                self.workspace / ".mini-code-memory",
                self.workspace,
            ),
            MemoryScope.LOCAL: (
                self.workspace / ".mini-code-memory-local",
                self.workspace,
            ),
        }
        all_entries: list[MemoryEntry] = []
        diagnostics: list[dict[str, str]] = []
        scope_states: dict[str, dict[str, object]] = {}
        for memory_scope, (directory, configured_root) in scope_paths.items():
            try:
                memory_file, entry_diagnostics = self._read_memory_scope_for_page(
                    memory_scope, directory, configured_root
                )
            except Exception:  # noqa: BLE001 - isolate one corrupt scope
                scope_states[memory_scope.value] = {
                    "status": "error",
                    "count": None,
                    "location": self._memory_location(memory_scope),
                }
                diagnostics.append(
                    self._diagnostic(
                        "memory",
                        "scope_read_failed",
                        f"The {memory_scope.value} memory scope could not be read.",
                    )
                )
                continue
            diagnostics.extend(entry_diagnostics)
            scope_states[memory_scope.value] = {
                "status": "error" if entry_diagnostics else "live",
                "count": len(memory_file.entries),
                "location": self._memory_location(memory_scope),
            }
            all_entries.extend(memory_file.entries)

        by_scope = {
            memory_scope.value: scope_states[memory_scope.value]["count"]
            for memory_scope in MemoryScope
        }
        tier_counts = Counter(entry.tier.value for entry in all_entries)
        category_counts = Counter(entry.category for entry in all_entries)
        filtered = list(all_entries)
        if scope is not None:
            filtered = [entry for entry in filtered if entry.scope.value == scope]
        if tier is not None:
            filtered = [entry for entry in filtered if entry.tier.value == tier]
        if category is not None:
            filtered = [entry for entry in filtered if entry.category == category]
        sort_key = lambda entry: (  # noqa: E731 - shared with cursor comparison
                -float(entry.updated_at),
                -float(entry.created_at),
                entry.scope.value,
                entry.id,
            )
        filtered.sort(key=sort_key)
        if cursor not in (None, ""):
            try:
                cursor_values = self._decode_cursor("memory", cursor)
                if (
                    len(cursor_values) != 7
                    or cursor_values[0] != (scope or "")
                    or cursor_values[1] != (tier or "")
                    or cursor_values[2] != (category or "")
                    or isinstance(cursor_values[3], bool)
                    or isinstance(cursor_values[4], bool)
                    or not isinstance(cursor_values[3], (int, float))
                    or not isinstance(cursor_values[4], (int, float))
                    or not isinstance(cursor_values[5], str)
                    or not isinstance(cursor_values[6], str)
                    or not math.isfinite(float(cursor_values[3]))
                    or not math.isfinite(float(cursor_values[4]))
                    or cursor_values[5] not in {item.value for item in MemoryScope}
                    or not _MEMORY_ID_RE.fullmatch(cursor_values[6])
                ):
                    raise ValueError("invalid cursor")
                cursor_key = (
                    -float(cursor_values[3]),
                    -float(cursor_values[4]),
                    cursor_values[5],
                    cursor_values[6],
                )
            except ValueError as exc:
                raise DashboardReadError(
                    400, "invalid_cursor", "Cursor is invalid."
                ) from exc
            filtered = [entry for entry in filtered if sort_key(entry) > cursor_key]
        page_entries = filtered[:page_limit]
        items: list[dict[str, object]] = []
        used_chars = 0
        for entry in page_entries:
            content_hidden = (
                entry.safety_status != "safe"
                or entry.approval_status != "approved"
                or entry.lifecycle_status != "active"
            )
            if content_hidden:
                content, truncated = "[Content hidden by safety policy]", False
            else:
                content, truncated = _redact_bounded_text(
                    entry.content, _MAX_MEMORY_CONTENT_CHARS
                )
            remaining = _MAX_MEMORY_RESPONSE_CONTENT_CHARS - used_chars
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining].rstrip() + "…"
                truncated = True
            items.append(
                {
                    "id": entry.id,
                    "scope": entry.scope.value,
                    "category": entry.category,
                    "tier": entry.tier.value,
                    "content": content,
                    "createdAt": self._timestamp(float(entry.created_at)),
                    "updatedAt": self._timestamp(float(entry.updated_at)),
                    "retrievalCount": entry.retrieval_count,
                    "injectionCount": entry.injection_count,
                    "usefulnessScore": entry.usefulness_score,
                    "corroboratedSuccessCount": entry.corroborated_success_count,
                    "corroboratedFailureCount": entry.corroborated_failure_count,
                    "corroboratedUsefulnessScore": entry.corroborated_usefulness_score,
                    "lifecycleStatus": entry.lifecycle_status,
                    "safetyStatus": entry.safety_status,
                    "approvalStatus": entry.approval_status,
                    "contentHidden": content_hidden,
                    "truncated": truncated,
                }
            )
            used_chars += len(content)
        has_more = len(items) < len(filtered)
        if len(items) < len(page_entries):
            diagnostics.append(
                self._diagnostic(
                    "memory",
                    "response_budget_applied",
                    "Memory content was truncated to the response budget.",
                )
            )
        known_total = len(all_entries)
        complete = all(
            state["status"] == "live" for state in scope_states.values()
        )
        latest = max((entry.updated_at for entry in all_entries), default=None)
        has_source_error = any(
            item["code"] != "response_budget_applied" for item in diagnostics
        )
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                "error" if has_source_error else "live",
                self._timestamp(float(latest)) if latest is not None else generated_at,
                (
                    "One or more Memory records could not be read."
                    if has_source_error
                    else None
                ),
            ),
            "summary": {
                "total": known_total if complete else None,
                "knownTotal": known_total,
                "complete": complete,
                "byScope": by_scope,
                "byTier": self._tier_counts(tier_counts),
                "byCategory": dict(sorted(category_counts.items())),
            },
            "scopes": scope_states,
            "items": items,
            "page": {
                "limit": page_limit,
                "hasMore": has_more,
                "nextCursor": (
                    self._encode_cursor(
                        "memory",
                        [
                            scope or "",
                            tier or "",
                            category or "",
                            page_entries[len(items) - 1].updated_at,
                            page_entries[len(items) - 1].created_at,
                            page_entries[len(items) - 1].scope.value,
                            page_entries[len(items) - 1].id,
                        ],
                    )
                    if has_more and items
                    else None
                ),
            },
            "filters": {"scope": scope, "tier": tier, "category": category},
            "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        }
        return _redact_value(payload)

    @staticmethod
    def _memory_location(scope: MemoryScope) -> str:
        return {
            MemoryScope.USER: "user memory",
            MemoryScope.PROJECT: ".mini-code-memory/",
            MemoryScope.LOCAL: ".mini-code-memory-local/",
        }[scope]

    def skills(
        self,
        *,
        source: str | None = None,
        directory: str | None = None,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Return bounded Skill summaries without paths or full markdown bodies."""
        page_limit = self._request_page_limit(limit)
        if source is not None and source not in _SKILL_SOURCES:
            raise DashboardReadError(400, "invalid_source", "Skill source is invalid.")
        if directory is not None and not _SKILL_DIRECTORY_RE.fullmatch(directory):
            raise DashboardReadError(
                400, "invalid_directory", "Skill directory is invalid."
            )

        generated_at = _iso_time(self._clock())
        records, diagnostics, latest = self._skill_records_for_page()
        by_source = {name: 0 for name in _SKILL_SOURCES}
        for skill in records:
            by_source[skill.source] += 1
        directories = sorted({skill.directory for skill in records if skill.directory})

        filtered = list(records)
        if source is not None:
            filtered = [skill for skill in filtered if skill.source == source]
        if directory is not None:
            filtered = [skill for skill in filtered if skill.directory == directory]
        source_rank = {name: index for index, name in enumerate(_SKILL_SOURCES)}

        def sort_key(skill: SkillSummary) -> tuple[str, str, int, str]:
            qualified = skill.qualified_name or skill.name
            return (
                qualified.casefold(),
                qualified,
                source_rank[skill.source],
                skill.name,
            )

        filtered.sort(key=sort_key)
        if cursor not in (None, ""):
            try:
                cursor_values = self._decode_cursor("skills", cursor)
                if (
                    len(cursor_values) != 6
                    or cursor_values[0] != (source or "")
                    or cursor_values[1] != (directory or "")
                    or not all(isinstance(value, str) for value in cursor_values[2:])
                    or not _SKILL_QUALIFIED_NAME_RE.fullmatch(cursor_values[2])
                    or not _SKILL_QUALIFIED_NAME_RE.fullmatch(cursor_values[3])
                    or cursor_values[4] not in _SKILL_SOURCES
                    or not _SKILL_NAME_RE.fullmatch(cursor_values[5])
                ):
                    raise ValueError("invalid skills cursor")
                cursor_key = (
                    cursor_values[2],
                    cursor_values[3],
                    source_rank[cursor_values[4]],
                    cursor_values[5],
                )
            except ValueError as exc:
                raise DashboardReadError(
                    400, "invalid_cursor", "Cursor is invalid."
                ) from exc
            filtered = [skill for skill in filtered if sort_key(skill) > cursor_key]

        page_records = filtered[:page_limit]
        items: list[dict[str, object]] = []
        used_chars = 0
        for skill in page_records:
            description, description_truncated = _redact_bounded_text(
                skill.description, _MAX_SKILL_DESCRIPTION_CHARS
            )
            item = {
                "name": _redact_text(skill.name, max_chars=128),
                "qualifiedName": _redact_text(
                    skill.qualified_name or skill.name, max_chars=256
                ),
                "description": description,
                "descriptionTruncated": description_truncated,
                "source": skill.source,
                "directory": _redact_text(skill.directory, max_chars=64),
                "domains": self._bounded_skill_values(skill.domains),
                "scopes": self._bounded_skill_values(skill.scopes),
                "tools": self._bounded_skill_values(skill.tools),
                "keywords": self._bounded_skill_values(skill.keywords),
                "exampleCount": len(skill.examples),
            }
            item_chars = sum(
                len(value)
                for value in item.values()
                if isinstance(value, str)
            ) + sum(
                len(value)
                for value in [
                    *item["domains"],
                    *item["scopes"],
                    *item["tools"],
                    *item["keywords"],
                ]
            )
            if used_chars + item_chars > _MAX_SKILL_RESPONSE_CONTENT_CHARS:
                break
            items.append(item)
            used_chars += item_chars

        has_more = len(items) < len(filtered)
        if len(items) < len(page_records):
            diagnostics.append(
                self._diagnostic(
                    "skills",
                    "response_budget_applied",
                    "Skill summaries were truncated to the response budget.",
                )
            )
        has_source_error = any(
            item["code"] != "response_budget_applied" for item in diagnostics
        )
        last = page_records[len(items) - 1] if items else None
        last_key = sort_key(last) if last is not None else None
        evidence = self._skill_evidence()
        version_ledger = self._skill_versions(records, evidence)
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                "error" if has_source_error else "live",
                self._timestamp(latest) if latest is not None else generated_at,
                "One or more Skill records could not be read."
                if has_source_error
                else None,
            ),
            "summary": {
                "total": len(records),
                "bySource": by_source,
                "directoryCount": len(directories),
                "directories": directories[:100],
            },
            "items": items,
            "evidence": evidence,
            "versionLedger": version_ledger,
            "page": {
                "limit": page_limit,
                "hasMore": has_more,
                "nextCursor": (
                    self._encode_cursor(
                        "skills",
                        [
                            source or "",
                            directory or "",
                            last_key[0],
                            last_key[1],
                            last.source,
                            last.name,
                        ],
                    )
                    if has_more and last is not None and last_key is not None
                    else None
                ),
            },
            "filters": {"source": source, "directory": directory},
            "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        }
        return _redact_value(payload)

    def _skill_evidence(self) -> dict[str, object]:
        message = (
            "Shadow-only correlation; never grants routing or promotion authority."
        )
        try:
            ledger = SkillEvidenceLedger(self._run_journal).snapshot()
        except Exception:  # noqa: BLE001 - source-local read failure
            return {
                "status": "unavailable",
                "scope": "retained-run-journal",
                "message": "Skill evidence could not be read.",
                "ledger": None,
            }
        partial = (
            ledger["runsTruncated"] is True
            or ledger["evaluationsTruncated"] is True
            or ledger["journalDiagnostics"] > 0
            or ledger["excludedRuns"]["eventScanLimited"] > 0
            or ledger["excludedRuns"]["eventReadIncomplete"] > 0
        )
        return {
            "status": "partial" if partial else "live",
            "scope": "retained-run-journal",
            "message": message,
            "ledger": ledger,
        }

    def _skill_versions(
        self,
        records: list[SkillSummary],
        evidence: Mapping[str, object],
    ) -> dict[str, object]:
        evidence_ledger = evidence.get("ledger")
        evidence_available = isinstance(evidence_ledger, Mapping)
        if not evidence_available:
            evidence_ledger = {
                "ledgerVersion": 1,
                "mode": "shadow",
                "evaluations": [],
                "promotionEligible": False,
            }
        try:
            ledger = SkillVersionLedger(self.workspace).snapshot(
                records,
                evidence_ledger,
            )
        except Exception:  # noqa: BLE001 - source-local read failure
            return {
                "status": "unavailable",
                "scope": "project-skill-version-ledger",
                "message": "Skill version history could not be read.",
                "ledger": None,
            }
        partial = not evidence_available or evidence.get("status") != "live"
        return {
            "status": "partial" if partial else "live",
            "scope": "project-skill-version-ledger",
            "message": (
                "Observed immutable lineage; all promotion and rollback "
                "execution remains locked."
            ),
            "ledger": ledger,
        }

    def _skill_records_for_page(
        self,
    ) -> tuple[list[SkillSummary], list[dict[str, str]], float | None]:
        if self._skill_loader_is_default:
            raw_skills, diagnostics, latest = self._discover_skill_records()
        else:
            diagnostics = []
            latest = None
            try:
                raw_skills = list(self._skill_loader(self.workspace))
                if len(raw_skills) > _MAX_SKILL_SUMMARIES:
                    raise ValueError("skill list exceeds the Dashboard read limit")
            except Exception:  # noqa: BLE001 - source-local safe error
                return [], [
                    self._diagnostic(
                        "skills", "discovery_failed", "Skills could not be discovered."
                    )
                ], None

        records: list[SkillSummary] = []
        for skill in raw_skills:
            try:
                if (
                    not isinstance(skill, SkillSummary)
                    or not _SKILL_NAME_RE.fullmatch(skill.name)
                    or not _SKILL_QUALIFIED_NAME_RE.fullmatch(
                        skill.qualified_name or skill.name
                    )
                    or skill.source not in _SKILL_SOURCES
                    or (
                        skill.directory
                        and not _SKILL_DIRECTORY_RE.fullmatch(skill.directory)
                    )
                    or not isinstance(skill.description, str)
                    or not all(
                        isinstance(values, list)
                        and all(isinstance(value, str) for value in values)
                        for values in (
                            skill.domains,
                            skill.scopes,
                            skill.tools,
                            skill.keywords,
                            skill.examples,
                        )
                    )
                    or len(skill.examples) > _MAX_SKILL_EXAMPLES
                ):
                    raise ValueError("invalid Skill summary")
            except Exception:  # noqa: BLE001 - isolate one malformed record
                diagnostics.append(
                    self._diagnostic(
                        "skills",
                        "skill_invalid",
                        "A malformed Skill record was skipped.",
                    )
                )
                continue
            records.append(skill)
        return records, diagnostics, latest

    def _discover_skill_records(
        self,
    ) -> tuple[list[SkillSummary], list[dict[str, str]], float | None]:
        roots = (
            (self.workspace / ".mini-code" / "skills", "project", self.workspace),
            (self.data_dir / "skills", "user", self.data_dir),
            (self.workspace / ".claude" / "skills", "compat_project", self.workspace),
            (
                self.data_dir.parent / ".claude" / "skills",
                "compat_user",
                self.data_dir.parent,
            ),
        )
        by_qualified_name: dict[str, SkillSummary] = {}
        diagnostics: list[dict[str, str]] = []
        latest: float | None = None
        scanned = 0

        for root, source, anchor in roots:
            if not root.exists():
                continue
            try:
                resolved_root = self._validate_source_directory(root, anchor)
                entries = sorted(resolved_root.iterdir(), key=lambda path: path.name)
            except Exception:  # noqa: BLE001 - isolate one Skill root
                diagnostics.append(
                    self._diagnostic(
                        "skills", "skill_read_failed", "A Skill root could not be read."
                    )
                )
                continue
            for entry in entries:
                try:
                    entry_mode = entry.lstat().st_mode
                except OSError:
                    diagnostics.append(
                        self._diagnostic(
                            "skills",
                            "skill_read_failed",
                            "A Skill directory could not be read.",
                        )
                    )
                    continue
                if stat.S_ISREG(entry_mode):
                    continue
                if not (stat.S_ISDIR(entry_mode) or stat.S_ISLNK(entry_mode)):
                    diagnostics.append(
                        self._diagnostic(
                            "skills",
                            "skill_read_failed",
                            "A Skill directory could not be read.",
                        )
                    )
                    continue
                scanned += 1
                if scanned > _MAX_SKILL_SUMMARIES:
                    diagnostics.append(
                        self._diagnostic(
                            "skills",
                            "discovery_limited",
                            "Skill discovery reached the Dashboard entry limit.",
                        )
                    )
                    return list(by_qualified_name.values()), diagnostics, latest
                try:
                    resolved_entry = self._validate_source_directory(entry, anchor)
                except Exception:  # noqa: BLE001 - isolate one Skill directory
                    diagnostics.append(
                        self._diagnostic(
                            "skills",
                            "skill_read_failed",
                            "A Skill directory could not be read.",
                        )
                    )
                    continue

                directory_summary: dict[str, object] | None = None
                directory_file = resolved_entry / "SKILL_DIR.md"
                if directory_file.exists():
                    try:
                        directory_summary, mtime = self._read_skill_directory_file(
                            directory_file, source, anchor
                        )
                        latest = mtime if latest is None else max(latest, mtime)
                    except Exception:  # noqa: BLE001 - isolate one directory summary
                        diagnostics.append(
                            self._diagnostic(
                                "skills",
                                "skill_read_failed",
                                "A Skill directory summary could not be read.",
                            )
                        )

                direct_file = resolved_entry / "SKILL.md"
                if direct_file.exists():
                    try:
                        skill, mtime = self._read_skill_file(
                            direct_file,
                            source,
                            anchor,
                            fallback_name=resolved_entry.name,
                        )
                        by_qualified_name.setdefault(
                            skill.qualified_name or skill.name, skill
                        )
                        latest = mtime if latest is None else max(latest, mtime)
                    except Exception:  # noqa: BLE001 - isolate one Skill file
                        diagnostics.append(
                            self._diagnostic(
                                "skills",
                                "skill_read_failed",
                                "A Skill file could not be read.",
                            )
                        )

                if directory_summary is None:
                    continue
                try:
                    nested_entries = sorted(
                        resolved_entry.iterdir(), key=lambda path: path.name
                    )
                except OSError:
                    diagnostics.append(
                        self._diagnostic(
                            "skills",
                            "skill_read_failed",
                            "A Skill directory could not be read.",
                        )
                    )
                    continue
                for nested in nested_entries:
                    nested_file = nested / "SKILL.md"
                    if not nested_file.exists():
                        continue
                    try:
                        self._validate_source_directory(nested, anchor)
                        skill, mtime = self._read_skill_file(
                            nested_file,
                            source,
                            anchor,
                            fallback_name=nested.name,
                            directory_summary=directory_summary,
                        )
                        by_qualified_name.setdefault(
                            skill.qualified_name or skill.name, skill
                        )
                        latest = mtime if latest is None else max(latest, mtime)
                    except Exception:  # noqa: BLE001 - isolate one nested Skill
                        diagnostics.append(
                            self._diagnostic(
                                "skills",
                                "skill_read_failed",
                                "A Skill file could not be read.",
                            )
                        )

        return list(by_qualified_name.values()), diagnostics, latest

    def _read_skill_directory_file(
        self, path: Path, source: str, anchor: Path
    ) -> tuple[dict[str, object], float]:
        text = self._read_bounded_text(path, anchor)
        metadata, _ = self._strict_skill_frontmatter(text)
        name = str(metadata.get("name") or path.parent.name).strip()
        if not _SKILL_DIRECTORY_RE.fullmatch(name):
            raise ValueError("invalid Skill directory name")
        return {
            "name": name,
            "description": extract_description(text),
            "source": source,
            "domains": self._skill_metadata_values(metadata.get("domains")),
            "scopes": self._skill_metadata_values(metadata.get("scopes")),
            "keywords": self._skill_metadata_values(metadata.get("keywords")),
        }, path.resolve(strict=True).stat().st_mtime

    def _read_skill_file(
        self,
        path: Path,
        source: str,
        anchor: Path,
        *,
        fallback_name: str,
        directory_summary: dict[str, object] | None = None,
    ) -> tuple[SkillSummary, float]:
        text = self._read_bounded_text(path, anchor)
        metadata, _ = self._strict_skill_frontmatter(text)
        name = str(metadata.get("name") or fallback_name).strip()
        if not _SKILL_NAME_RE.fullmatch(name):
            raise ValueError("invalid Skill name")
        inherited_directory = (
            str(directory_summary["name"]) if directory_summary is not None else ""
        )
        directory = str(metadata.get("directory") or inherited_directory).strip()
        if directory and not _SKILL_DIRECTORY_RE.fullmatch(directory):
            raise ValueError("invalid Skill directory")
        qualified_name = f"{directory}/{name}" if directory else name
        if not _SKILL_QUALIFIED_NAME_RE.fullmatch(qualified_name):
            raise ValueError("invalid qualified Skill name")

        def values(key: str) -> list[str]:
            own = self._skill_metadata_values(metadata.get(key))
            if own or directory_summary is None:
                return own
            inherited = directory_summary.get(key, [])
            return list(inherited) if isinstance(inherited, list) else []

        return (
            SkillSummary(
                name=name,
                qualified_name=qualified_name,
                description=extract_description(text),
                path=str(path),
                source=source,
                directory=directory,
                directory_description=(
                    str(directory_summary.get("description", ""))
                    if directory_summary is not None
                    else ""
                ),
                domains=values("domains"),
                scopes=values("scopes"),
                tools=self._skill_metadata_values(metadata.get("tools")),
                keywords=values("keywords"),
                examples=self._skill_metadata_values(metadata.get("examples")),
                content_digest=hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
            ),
            path.resolve(strict=True).stat().st_mtime,
        )

    @staticmethod
    def _strict_skill_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        normalized = text.replace("\r\n", "\n")
        if normalized.startswith("---\n") and normalized.find("\n---\n", 4) < 0:
            raise ValueError("unterminated Skill frontmatter")
        return parse_frontmatter(text)

    @staticmethod
    def _skill_metadata_values(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _bounded_skill_values(values: list[str]) -> list[str]:
        return [
            _redact_text(value, max_chars=_MAX_SKILL_LIST_VALUE_CHARS)
            for value in values[:_MAX_SKILL_LIST_ITEMS]
        ]

    def connections(self) -> dict[str, object]:
        """Return independently fault-tolerant config/current/history MCP facts."""
        generated_at = _iso_time(self._clock())
        servers, config_sources, diagnostics, latest = self._mcp_catalog()
        config_has_error = any(
            source["status"] == "error" for source in config_sources.values()
        ) or any(
            item["code"] in {"mcp_entry_invalid", "mcp_config_read_failed"}
            for item in diagnostics
        )
        current = project_current_mcp_state(
            self.workspace,
            (str(item["_runtimeName"]) for item in servers),
            self._mcp_current_state_loader,
            configured_set_complete=not config_has_error,
        )
        runtime = aggregate_historical_mcp_runtime(
            workspace=self.workspace,
            run_journal=self._run_journal,
            configured_server_names=(str(item["_runtimeName"]) for item in servers),
        )
        diagnostics.extend(runtime.diagnostics)
        response_servers = [
            {
                **{
                    key: value
                    for key, value in server.items()
                    if key != "_runtimeName"
                },
                "liveStatus": current_state.live_status,
                "current": current_state.to_dict(),
                "runtime": runtime.server_runtime[
                    str(server["_runtimeName"])
                ].to_dict(),
            }
            for server, current_state in zip(
                servers[:_MAX_MCP_RESPONSE_SERVERS],
                current.servers[:_MAX_MCP_RESPONSE_SERVERS],
                strict=True,
            )
        ]
        response_limited = len(response_servers) < len(servers)
        if response_limited:
            diagnostics.append(
                self._diagnostic(
                    "connections",
                    "response_budget_applied",
                    "MCP server summaries were limited for this response.",
                )
            )
        if config_has_error:
            message = (
                "One or more MCP configuration sources could not be read; "
                "available configuration and runtime facts remain independently projected."
            )
        elif current.status == "error":
            message = (
                "Current MCP process snapshot could not be read; configuration "
                "and retained history remain independently projected."
            )
        elif runtime.status == "error":
            message = (
                "Retained MCP observations could not be read; configuration and "
                "current process state remain independently projected."
            )
        elif current.status == "unavailable":
            message = (
                "MCP runtime facts are retained and historical; current MCP status "
                "is unavailable."
            )
        else:
            message = (
                "Current MCP process state is scoped to this Gateway snapshot; "
                "retained Run observations remain historical."
            )
        source_status = (
            "error"
            if config_has_error
            or current.status == "error"
            or runtime.status == "error"
            else "stale" if current.status == "unavailable" else "live"
        )
        source_updated_at = (
            current.checked_at
            if current.status == "live" and current.checked_at is not None
            else self._timestamp(latest) if latest is not None else generated_at
        )
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                source_status,
                source_updated_at,
                message,
            ),
            "summary": {
                "gatewayStatus": "live",
                "configuredMcpCount": len(servers),
                "registeredConfiguredMcpCount": (
                    current.registered_configured_mcp_count
                ),
                "activeMcpInstanceCount": current.active_mcp_instance_count,
                "liveMcpCount": current.live_mcp_count,
                "complete": not config_has_error,
                "observedConfiguredCount": runtime.observed_configured_count,
                "unobservedConfiguredCount": (
                    len(servers) - runtime.observed_configured_count
                ),
                "unmatchedObservedServerCount": (
                    runtime.unmatched_observed_server_count
                ),
            },
            "gateway": {
                "status": "live",
                "transport": "http",
                "scope": "local",
            },
            "mcpCurrent": current.to_dict(),
            "mcpRuntime": runtime.runtime_dict(),
            "coverage": runtime.coverage_dict(),
            "mcpServers": response_servers,
            "configSources": config_sources,
            "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        }
        return _redact_value(payload)

    def configured_mcp_server_keys(self) -> frozenset[str]:
        """Return bounded opaque keys for the effective Workspace MCP config.

        This internal composition seam lets observers select registry entries
        before probing them. Raw commands, arguments, environment values, and
        server names never leave the read model through this interface.
        """
        servers, _sources, _diagnostics, _latest = self._mcp_catalog()
        return frozenset(
            mcp_server_key(self.workspace, str(server["_runtimeName"]))
            for server in servers
        )

    def _mcp_catalog(
        self,
    ) -> tuple[
        list[dict[str, object]],
        dict[str, dict[str, object]],
        list[dict[str, str]],
        float | None,
    ]:
        definitions = (
            ("user", self.data_dir / "mcp.json", self.data_dir),
            ("project", self.workspace / ".mcp.json", self.workspace),
        )
        configs: dict[str, dict[str, dict[str, Any]]] = {}
        config_sources: dict[str, dict[str, object]] = {}
        diagnostics: list[dict[str, str]] = []
        latest: float | None = None
        for scope, path, root in definitions:
            try:
                records, updated_at, entry_diagnostics = self._read_mcp_config_source(
                    path, root, scope
                )
                diagnostics.extend(entry_diagnostics)
                configs[scope] = records
                config_sources[scope] = {
                    "status": "error" if entry_diagnostics else "live",
                    "updatedAt": (
                        self._timestamp(updated_at) if updated_at is not None else None
                    ),
                    "count": len(records),
                }
                if updated_at is not None:
                    latest = updated_at if latest is None else max(latest, updated_at)
            except Exception:  # noqa: BLE001 - isolate one configuration source
                configs[scope] = {}
                config_sources[scope] = {
                    "status": "error",
                    "updatedAt": None,
                    "count": None,
                }
                diagnostics.append(
                    self._diagnostic(
                        "connections",
                        "mcp_config_read_failed",
                        f"The {scope} MCP configuration could not be read.",
                    )
                )

        effective: dict[str, tuple[dict[str, Any], str]] = {
            name: (dict(config), "user")
            for name, config in configs["user"].items()
        }
        for name, project_config in configs["project"].items():
            if name in effective:
                base_config = effective[name][0]
                merged = {**base_config, **project_config}
                merged["env"] = {
                    **base_config.get("env", {}),
                    **project_config.get("env", {}),
                }
                effective[name] = (merged, "project")
            else:
                effective[name] = (dict(project_config), "project")

        servers: list[dict[str, object]] = []
        for name, (config, scope) in sorted(
            effective.items(), key=lambda item: (item[0].casefold(), item[0])
        ):
            enabled = config.get("enabled") is not False
            has_command = bool(str(config.get("command", "")).strip())
            status = "disabled" if not enabled else "configured"
            if enabled and not has_command:
                status = "error"
                diagnostics.append(
                    self._diagnostic(
                        "connections",
                        "mcp_entry_invalid",
                        "An MCP server configuration is missing its command.",
                    )
                )
            protocol_value = config.get("protocol")
            protocol = (
                _redact_text(protocol_value, max_chars=64)
                if isinstance(protocol_value, str) and len(protocol_value) <= 64
                else None
            )
            servers.append(
                {
                    "_runtimeName": name,
                    "name": _redact_text(name, max_chars=128),
                    "scope": scope,
                    "status": status,
                    "liveStatus": "unavailable",
                    "protocol": protocol,
                }
            )
        return servers, config_sources, diagnostics, latest

    def _read_mcp_config_source(
        self, path: Path, root: Path, scope: str
    ) -> tuple[dict[str, dict[str, Any]], float | None, list[dict[str, str]]]:
        if not path.exists():
            return {}, None, []
        resolved = self._validate_source_file(path, root)
        parsed = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("mcpServers", {}), dict
        ):
            raise ValueError("invalid MCP configuration")
        raw_servers = parsed.get("mcpServers", {})
        if len(raw_servers) > _MAX_MCP_SERVER_ENTRIES:
            raise ValueError("MCP configuration exceeds the Dashboard entry limit")
        records: dict[str, dict[str, Any]] = {}
        diagnostics: list[dict[str, str]] = []
        for name, config in raw_servers.items():
            if (
                not isinstance(name, str)
                or not _MCP_SERVER_NAME_RE.fullmatch(name)
                or not isinstance(config, dict)
            ):
                diagnostics.append(
                    self._diagnostic(
                        "connections",
                        "mcp_entry_invalid",
                        f"A malformed {scope} MCP server entry was skipped.",
                    )
                )
                continue
            normalized = dict(config)
            nested_invalid = False
            if "env" in normalized and not isinstance(normalized["env"], dict):
                normalized["env"] = {}
                nested_invalid = True
            if "args" in normalized and not isinstance(normalized["args"], list):
                normalized["args"] = []
                nested_invalid = True
            if "command" in normalized and not isinstance(
                normalized["command"], str
            ):
                normalized["command"] = ""
                nested_invalid = True
            if "enabled" in normalized and not isinstance(
                normalized["enabled"], bool
            ):
                normalized["enabled"] = True
                nested_invalid = True
            if "protocol" in normalized and not isinstance(
                normalized["protocol"], str
            ):
                normalized["protocol"] = None
                nested_invalid = True
            if nested_invalid:
                diagnostics.append(
                    self._diagnostic(
                        "connections",
                        "mcp_entry_invalid",
                        f"A malformed field in a {scope} MCP server entry was ignored.",
                    )
                )
            records[name] = normalized
        return records, resolved.stat().st_mtime, diagnostics

    def system(self) -> dict[str, object]:
        """Return a strict safe-field summary of the local Gateway runtime."""
        generated_at = _iso_time(self._clock())
        diagnostics: list[dict[str, str]] = []
        workspace_status = (
            "live"
            if self.workspace.is_dir()
            and os.access(self.workspace, os.R_OK | os.X_OK)
            else "error"
        )
        if workspace_status == "error":
            diagnostics.append(
                self._diagnostic(
                    "workspace",
                    "workspace_unreadable",
                    "Workspace does not exist or is not readable.",
                )
            )

        _, session_diagnostics, session_fatal = self._session_records()
        sessions_status = (
            "error" if session_fatal or session_diagnostics else "live"
        )
        diagnostics.extend(session_diagnostics)

        memory_statuses: dict[MemoryScope, str] = {}
        memory_paths = {
            MemoryScope.USER: (self.data_dir / "memory", self.data_dir),
            MemoryScope.PROJECT: (
                self.workspace / ".mini-code-memory",
                self.workspace,
            ),
            MemoryScope.LOCAL: (
                self.workspace / ".mini-code-memory-local",
                self.workspace,
            ),
        }
        for scope, (directory, root) in memory_paths.items():
            try:
                _, entry_diagnostics = self._read_memory_scope_for_page(
                    scope, directory, root
                )
                memory_statuses[scope] = "error" if entry_diagnostics else "live"
                diagnostics.extend(entry_diagnostics)
            except Exception:  # noqa: BLE001 - isolate one storage source
                memory_statuses[scope] = "error"
                diagnostics.append(
                    self._diagnostic(
                        "memory",
                        "scope_read_failed",
                        f"The {scope.value} memory scope could not be read.",
                    )
                )

        _, skill_diagnostics, _ = self._skill_records_for_page()
        skills_status = "error" if skill_diagnostics else "live"
        diagnostics.extend(skill_diagnostics)

        _, config_sources, connection_diagnostics, _ = self._mcp_catalog()
        mcp_config_status = (
            "error"
            if connection_diagnostics
            or any(item["status"] == "error" for item in config_sources.values())
            else "stale"
        )
        diagnostics.extend(connection_diagnostics)

        memory_status = (
            "error"
            if any(status == "error" for status in memory_statuses.values())
            else "live"
        )
        has_error = workspace_status == "error" or any(
            status == "error"
            for status in (
                sessions_status,
                memory_status,
                skills_status,
                mcp_config_status,
            )
        )
        system_name = platform.system()
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "mode": "read-only",
            "source": self._source(
                "error" if has_error else "live",
                generated_at,
                "One or more local data sources could not be read."
                if has_error
                else None,
            ),
            "application": {
                "name": "minicode-py",
                "version": self._package_version(),
                "dashboardSchemaVersion": 1,
            },
            "runtime": {
                "pythonVersion": platform.python_version(),
                "platform": "macOS" if system_name == "Darwin" else system_name,
                "architecture": platform.machine() or "unknown",
                "processMode": "gateway",
            },
            "workspace": {
                "id": self._workspace_id(),
                "name": self.workspace.name or "workspace",
                "status": workspace_status,
            },
            "features": {
                "dashboard": "read-only",
                "sessions": sessions_status,
                "memory": memory_status,
                "skills": skills_status,
                "mcpConfig": mcp_config_status,
                "mcpRuntime": "unavailable",
                "runs": "lifecycle-model-usage-cost-tool-assistant-skill-memory-context",
                "usage": "live",
                "sse": "unavailable",
                "writes": "unavailable",
            },
            "storage": {
                "sessions": {"status": sessions_status, "writable": None},
                "memoryUser": {
                    "status": memory_statuses[MemoryScope.USER],
                    "writable": None,
                },
                "memoryProject": {
                    "status": memory_statuses[MemoryScope.PROJECT],
                    "writable": None,
                },
                "memoryLocal": {
                    "status": memory_statuses[MemoryScope.LOCAL],
                    "writable": None,
                },
                "skills": {"status": skills_status, "writable": None},
                "mcpConfig": {"status": mcp_config_status, "writable": None},
            },
            "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        }
        return _redact_value(payload)

    @staticmethod
    def _package_version() -> str:
        try:
            version = importlib_metadata.version("minicode-py")
            if not isinstance(version, str) or not version or len(version) > 64:
                raise ValueError("invalid package version")
            return version
        except Exception:  # noqa: BLE001 - source checkout fallback
            return "0.1.0"

    @staticmethod
    def _read_memory_scope_for_page(
        scope: MemoryScope, directory: Path, configured_root: Path
    ) -> tuple[MemoryFile, list[dict[str, str]]]:
        memory_json = directory / "memory.json"
        memory_md = directory / "MEMORY.md"
        diagnostics: list[dict[str, str]] = []
        if memory_json.exists():
            parsed = json.loads(
                DashboardReadModel._read_bounded_text(memory_json, configured_root)
            )
            if not isinstance(parsed, dict) or not isinstance(parsed.get("entries"), list):
                raise ValueError("invalid memory data")
            if len(parsed["entries"]) > _MAX_MEMORY_ENTRIES:
                raise ValueError("memory scope exceeds the Dashboard read limit")
            entries: list[MemoryEntry] = []
            for raw_entry in parsed["entries"]:
                try:
                    if not isinstance(raw_entry, dict):
                        raise TypeError("invalid memory entry")
                    entry_id = raw_entry.get("id")
                    category = raw_entry.get("category", "general")
                    content = raw_entry.get("content")
                    raw_tier = raw_entry.get("tier", MemoryTier.SHORT_TERM.value)
                    if (
                        not isinstance(entry_id, str)
                        or not _MEMORY_ID_RE.fullmatch(entry_id)
                        or not isinstance(category, str)
                        or not _CATEGORY_RE.fullmatch(category)
                        or not isinstance(content, str)
                        or raw_tier not in {item.value for item in MemoryTier}
                    ):
                        raise ValueError("invalid memory entry")
                    normalized = dict(raw_entry)
                    normalized["scope"] = scope.value
                    entry = MemoryEntry.from_dict(normalized)
                    assessed_safety = assess_memory_safety(
                        entry.content, source="dashboard_read"
                    )
                    safety_rank = {"safe": 0, "suspicious": 1, "unsafe": 2}
                    if safety_rank.get(assessed_safety.status, 1) > safety_rank.get(
                        entry.safety_status, 1
                    ):
                        entry.safety_status = assessed_safety.status
                        if assessed_safety.status == "unsafe":
                            entry.approval_status = "rejected"
                            entry.lifecycle_status = "rejected"
                        elif entry.approval_status == "approved":
                            entry.approval_status = "pending"
                    numeric_values = (
                        entry.created_at,
                        entry.updated_at,
                        entry.last_accessed,
                        entry.last_used,
                        entry.usefulness_score,
                        entry.corroborated_usefulness_score,
                    )
                    counters = (
                        entry.retrieval_count,
                        entry.injection_count,
                        entry.success_count,
                        entry.failure_count,
                        entry.corroborated_success_count,
                        entry.corroborated_failure_count,
                    )
                    if (
                        any(not math.isfinite(float(value)) for value in numeric_values)
                        or any(value < 0 for value in counters)
                        or len(entry.lifecycle_status) > 64
                        or len(entry.safety_status) > 64
                        or len(entry.approval_status) > 64
                    ):
                        raise ValueError("invalid memory entry")
                except Exception:  # noqa: BLE001 - isolate one malformed entry
                    diagnostics.append(
                        DashboardReadModel._diagnostic(
                            "memory",
                            "entry_invalid",
                            f"A malformed {scope.value} Memory entry was skipped.",
                        )
                    )
                    continue
                entries.append(entry)
            return MemoryFile(scope=scope, entries=entries), diagnostics

        if memory_md.exists():
            entries: list[MemoryEntry] = []
            category = "general"
            text = DashboardReadModel._read_bounded_text(memory_md, configured_root)
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    candidate = stripped[3:].strip().lower()
                    category = candidate if _CATEGORY_RE.fullmatch(candidate) else "general"
                elif stripped.startswith("- "):
                    if len(entries) >= _MAX_MEMORY_ENTRIES:
                        raise ValueError("memory scope exceeds the Dashboard read limit")
                    entries.append(
                        MemoryEntry(
                            id=f"{scope.value}-markdown-{len(entries) + 1}",
                            scope=scope,
                            category=category,
                            content=stripped[2:].strip(),
                            tier=MemoryTier.SHORT_TERM,
                            safety_status="suspicious",
                            approval_status="pending",
                        )
                    )
            return MemoryFile(scope=scope, entries=entries), diagnostics

        return MemoryFile(scope=scope), diagnostics

    def _workspace_id(self) -> str:
        return stable_workspace_id(self.workspace)

    @staticmethod
    def _encode_cursor(kind: str, values: list[object]) -> str:
        raw = json.dumps([kind, *values], separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(kind: str, cursor: str) -> list[object]:
        if (
            not isinstance(cursor, str)
            or len(cursor) < 1
            or len(cursor) > _MAX_CURSOR_CHARS
            or not _CURSOR_RE.fullmatch(cursor)
        ):
            raise ValueError("invalid cursor")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
            payload = json.loads(decoded.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid cursor") from exc
        if (
            not isinstance(payload, list)
            or not payload
            or payload[0] != kind
            or len(payload) > 8
        ):
            raise ValueError("invalid cursor")
        return payload[1:]

    @staticmethod
    def _page_limit(value: int | str | None, *, default: int = _DEFAULT_PAGE_LIMIT) -> int:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            raise ValueError("invalid page limit")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid page limit") from exc
        if parsed < 1 or parsed > _MAX_PAGE_LIMIT:
            raise ValueError("invalid page limit")
        return parsed

    @classmethod
    def _request_page_limit(
        cls, value: int | str | None, *, default: int = _DEFAULT_PAGE_LIMIT
    ) -> int:
        try:
            return cls._page_limit(value, default=default)
        except ValueError as exc:
            raise DashboardReadError(
                400, "invalid_limit", "Page limit is invalid."
            ) from exc

    @staticmethod
    def _timestamp(value: float) -> str:
        return _iso_time(datetime.fromtimestamp(value, tz=timezone.utc))

    @staticmethod
    def _diagnostic(source: str, code: str, message: str) -> dict[str, str]:
        return {
            "source": source,
            "code": code,
            "message": _redact_text(message, max_chars=_MAX_DIAGNOSTIC_MESSAGE_CHARS),
        }

    def _session_records(
        self,
    ) -> tuple[list[SessionMetadata], list[dict[str, str]], bool]:
        index_path = self.data_dir / "sessions_index.json"
        if not index_path.exists():
            if self._session_loader_is_default:
                return [], [], False
            try:
                return list(self._session_loader()), [], False
            except Exception:  # noqa: BLE001 - source isolation is the contract
                return [], [
                    self._diagnostic(
                        "sessions",
                        "index_read_failed",
                        "The session index could not be read.",
                    )
                ], True
        try:
            parsed = json.loads(self._read_bounded_text(index_path, self.data_dir))
            if not isinstance(parsed, dict):
                raise TypeError("invalid session index")
            if len(parsed) > _MAX_SESSION_INDEX_ENTRIES:
                raise ValueError("session index exceeds the Dashboard read limit")
        except Exception:  # noqa: BLE001 - never expose parser details
            return [], [
                self._diagnostic(
                    "sessions",
                    "index_read_failed",
                    "The session index could not be read.",
                )
            ], True

        records: list[SessionMetadata] = []
        diagnostics: list[dict[str, str]] = []
        for session_id, raw_metadata in parsed.items():
            try:
                if (
                    not isinstance(session_id, str)
                    or not _SESSION_ID_RE.fullmatch(session_id)
                    or not isinstance(raw_metadata, dict)
                ):
                    raise TypeError("invalid session metadata")
                metadata = SessionMetadata(**raw_metadata)
                if metadata.session_id != session_id:
                    raise ValueError("session id mismatch")
                if (
                    isinstance(metadata.created_at, bool)
                    or isinstance(metadata.updated_at, bool)
                    or not isinstance(metadata.created_at, (int, float))
                    or not isinstance(metadata.updated_at, (int, float))
                    or not math.isfinite(float(metadata.created_at))
                    or not math.isfinite(float(metadata.updated_at))
                    or not isinstance(metadata.workspace, str)
                    or not isinstance(metadata.first_message, str)
                    or not isinstance(metadata.last_message, str)
                    or isinstance(metadata.message_count, bool)
                    or not isinstance(metadata.message_count, int)
                    or metadata.message_count < 0
                ):
                    raise TypeError("invalid session metadata")
            except Exception:  # noqa: BLE001 - isolate one malformed record
                diagnostics.append(
                    self._diagnostic(
                        "sessions",
                        "metadata_invalid",
                        "A malformed session record was skipped.",
                    )
                )
                continue
            records.append(metadata)
        return records, diagnostics, False

    @staticmethod
    def _source(
        status: str,
        updated_at: str | None,
        message: str | None = None,
    ) -> dict[str, object]:
        return {"status": status, "updatedAt": updated_at, "message": message}

    def _run_summary(
        self, generated_at: str
    ) -> tuple[dict[str, object], dict[str, object], list[dict[str, str]]]:
        """Project one bounded Run lifecycle summary for Overview."""
        try:
            page = self._run_journal.list_runs(limit=1)
        except Exception:  # noqa: BLE001 - isolate Journal from other sources
            diagnostic = self._diagnostic(
                "runs", "journal_read_failed", "RunJournal storage could not be read."
            )
            return (
                {
                    "status": "error",
                    "count": None,
                    "byStatus": {name: 0 for name in RUN_STATUSES},
                    "latestUpdatedAt": None,
                    "coverage": dict(_RUN_COVERAGE),
                },
                self._source(
                    "error", None, "RunJournal lifecycle data could not be read."
                ),
                [diagnostic],
            )

        diagnostics = list(page.diagnostics)[:_MAX_DIAGNOSTICS]
        status = "error" if diagnostics else "live"
        latest = page.items[0].updated_at if page.items else None
        message = (
            "One or more Run records could not be read; known lifecycle totals remain bounded."
            if diagnostics
            else "Shows Run lifecycle summaries recorded after instrumentation; Model, Tool, Assistant, Skill, and Memory details remain in Run detail, and historical Runs were not backfilled."
        )
        return (
            {
                "status": status,
                "count": page.known_total,
                "byStatus": dict(page.by_status),
                "latestUpdatedAt": latest,
                "coverage": dict(_RUN_COVERAGE),
            },
            self._source(status, latest or generated_at, message),
            diagnostics,
        )

    def _session_summary(self) -> tuple[dict[str, object], bool]:
        try:
            self._validate_session_index()
            sessions = [
                session
                for session in self._session_loader()
                if self._same_workspace(session.workspace)
            ]
            latest = max((session.updated_at for session in sessions), default=None)
            latest_updated_at = (
                _iso_time(datetime.fromtimestamp(latest, tz=timezone.utc))
                if latest is not None
                else None
            )
        except Exception:  # noqa: BLE001 - source isolation is the contract
            return {
                "status": "error",
                "count": None,
                "latestUpdatedAt": None,
            }, True
        return {
            "status": "live",
            "count": len(sessions),
            "latestUpdatedAt": latest_updated_at,
        }, False

    def _validate_session_index(self) -> None:
        index_path = self.data_dir / "sessions_index.json"
        if not index_path.exists():
            return
        parsed = json.loads(self._read_bounded_text(index_path, self.data_dir))
        if not isinstance(parsed, dict):
            raise TypeError("invalid session index")
        if len(parsed) > _MAX_SESSION_INDEX_ENTRIES:
            raise ValueError("session index exceeds the Dashboard read limit")
        for session_id, raw_metadata in parsed.items():
            if not isinstance(session_id, str) or not isinstance(raw_metadata, dict):
                raise TypeError("invalid session metadata")
            metadata = SessionMetadata(**raw_metadata)
            if metadata.session_id != session_id:
                raise ValueError("session id mismatch")
            if not isinstance(metadata.created_at, (int, float)) or not isinstance(
                metadata.updated_at, (int, float)
            ):
                raise TypeError("invalid session timestamp")
            if not isinstance(metadata.workspace, str):
                raise TypeError("invalid session workspace")

    def _skill_summary(self) -> tuple[dict[str, object], bool]:
        skills, diagnostics, _ = self._skill_records_for_page()
        if diagnostics and not skills:
            return {"status": "error", "count": None, "bySource": {}}, True
        by_source: dict[str, int] = {}
        for skill in skills:
            by_source[skill.source] = by_source.get(skill.source, 0) + 1
        return {
            "status": "error" if diagnostics else "live",
            "count": len(skills),
            "bySource": dict(sorted(by_source.items())),
        }, bool(diagnostics)

    def _connection_summary(self) -> tuple[dict[str, object], bool]:
        servers, config_sources, _, _ = self._mcp_catalog()
        config_error = any(
            source["status"] == "error" for source in config_sources.values()
        )
        if config_error:
            return {
                "status": "partial",
                "gateway": {"status": "live"},
                "mcp": {
                    "status": "error",
                    "configuredCount": len(servers),
                    "liveCount": None,
                },
            }, True
        return {
            "status": "live",
            "gateway": {"status": "live"},
            "mcp": {
                "status": "unavailable",
                "configuredCount": len(servers),
                "liveCount": None,
            },
        }, False

    def _same_workspace(self, candidate: str) -> bool:
        if not candidate:
            return False
        try:
            return Path(candidate).expanduser().resolve() == self.workspace
        except (OSError, RuntimeError):
            return False

    def _memory_summary(self) -> tuple[dict[str, object], list[str]]:
        scope_paths = {
            MemoryScope.USER: (self.data_dir / "memory", self.data_dir),
            MemoryScope.PROJECT: (
                self.workspace / ".mini-code-memory",
                self.workspace,
            ),
            MemoryScope.LOCAL: (
                self.workspace / ".mini-code-memory-local",
                self.workspace,
            ),
        }
        scopes: dict[str, dict[str, object]] = {}
        total_tiers: Counter[str] = Counter()
        total_categories: Counter[str] = Counter()
        errors: list[str] = []
        known_count = 0

        for scope, (directory, configured_root) in scope_paths.items():
            try:
                memory_file = self._read_memory_scope(
                    scope, directory, configured_root
                )
            except Exception:  # noqa: BLE001 - isolate one corrupt scope
                errors.append(scope.value)
                scopes[scope.value] = {
                    "status": "error",
                    "count": None,
                    "tiers": None,
                    "categories": None,
                }
                continue

            tiers = Counter(entry.tier.value for entry in memory_file.entries)
            categories = Counter(entry.category for entry in memory_file.entries)
            scope_tiers = self._tier_counts(tiers)
            scope_categories = dict(sorted(categories.items()))
            scopes[scope.value] = {
                "status": "live",
                "count": len(memory_file.entries),
                "tiers": scope_tiers,
                "categories": scope_categories,
            }
            known_count += len(memory_file.entries)
            total_tiers.update(tiers)
            total_categories.update(categories)

        return {
            "status": "partial" if errors else "live",
            "totalCount": None if errors else known_count,
            "knownCount": known_count,
            "complete": not errors,
            "scopes": scopes,
            "tiers": self._tier_counts(total_tiers),
            "categories": dict(sorted(total_categories.items())),
        }, errors

    @staticmethod
    def _read_memory_scope(
        scope: MemoryScope, directory: Path, configured_root: Path
    ) -> MemoryFile:
        memory_json = directory / "memory.json"
        memory_md = directory / "MEMORY.md"
        if memory_json.exists():
            parsed = json.loads(
                DashboardReadModel._read_bounded_text(
                    memory_json, configured_root
                )
            )
            if not isinstance(parsed, dict) or not isinstance(parsed.get("entries"), list):
                raise ValueError("invalid memory data")
            if len(parsed["entries"]) > _MAX_MEMORY_ENTRIES:
                raise ValueError("memory scope exceeds the Dashboard read limit")
            entries: list[MemoryEntry] = []
            for raw_entry in parsed["entries"]:
                if not isinstance(raw_entry, dict):
                    raise TypeError("invalid memory entry")
                if not isinstance(raw_entry.get("id"), str) or not isinstance(
                    raw_entry.get("content"), str
                ):
                    raise ValueError("invalid memory entry")
                normalized = dict(raw_entry)
                normalized["scope"] = scope.value
                entries.append(MemoryEntry.from_dict(normalized))
            return MemoryFile(scope=scope, entries=entries)

        if memory_md.exists():
            entries = []
            category = "general"
            text = DashboardReadModel._read_bounded_text(
                memory_md, configured_root
            )
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    category = stripped[3:].strip().lower() or "general"
                elif stripped.startswith("- "):
                    entries.append(
                        MemoryEntry(
                            id=f"{scope.value}-markdown-{len(entries) + 1}",
                            scope=scope,
                            category=category,
                            content=stripped[2:].strip(),
                            tier=MemoryTier.SHORT_TERM,
                        )
                    )
            return MemoryFile(scope=scope, entries=entries)

        return MemoryFile(scope=scope)

    @staticmethod
    def _tier_counts(counts: Counter[str]) -> dict[str, int]:
        return {
            tier.value: counts.get(tier.value, 0)
            for tier in MemoryTier
        }

    @staticmethod
    def _validate_source_file(path: Path, root: Path) -> Path:
        resolved_root = root.resolve()
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved_root):
            raise OSError("source file escapes its configured root")
        if resolved_path.stat().st_size > _MAX_SOURCE_FILE_BYTES:
            raise ValueError("source file exceeds the Dashboard read limit")
        return resolved_path

    @staticmethod
    def _validate_source_directory(path: Path, root: Path) -> Path:
        resolved_root = root.resolve()
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved_root) or not resolved_path.is_dir():
            raise OSError("source directory escapes its configured root")
        return resolved_path

    @staticmethod
    def _read_bounded_text(path: Path, root: Path) -> str:
        resolved = DashboardReadModel._validate_source_file(path, root)
        return resolved.read_text(encoding="utf-8")


def _redact_bounded_text(value: str, max_chars: int) -> tuple[str, bool]:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _KEY_TOKEN_RE.sub("[REDACTED]", value)
    value = _ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )
    if len(value) > max_chars:
        return value[:max_chars] + "…", True
    return value, False


def _redact_text(value: str, max_chars: int = 1_000) -> str:
    return _redact_bounded_text(value, max_chars)[0]


def _redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, nested in list(value.items())[:100]:
            key = _redact_text(str(raw_key), max_chars=100)
            normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
            redacted[key] = (
                "[REDACTED]"
                if normalized_key in _SENSITIVE_KEYS
                else _redact_value(nested, depth + 1)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, tuple):
        return [_redact_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value), max_chars=240)
