"""Small optional seam for structured Agent execution events."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping
from typing import Protocol

from minicode.logging_config import get_logger


_logger = get_logger("run_events")

_MAX_ROUTED_SKILLS = 20
_MAX_ROUTING_SKILLS = 100_000
_MAX_MEMORY_COUNT = 100_000
_MAX_MEMORY_TOKENS = 10_000_000
_MAX_MODEL_TOKENS = 1_000_000_000
_MAX_MODEL_DURATION_MS = 86_400_000
_MAX_CONTEXT_MESSAGES = 100_000
_MAX_CONTEXT_TOKENS = 1_000_000_000
_MODEL_USAGE_SOURCES = frozenset({"provider", "estimated", "unavailable"})
_CONTEXT_OPERATION_ID_RE = re.compile(r"^ctxop_[0-9a-f]{32}$")
_CONTEXT_PATHS = frozenset(
    {
        "pre_request_cybernetic",
        "pre_request_compactor",
        "context_manager_auto",
        "reactive_cybernetic",
        "reactive_compactor",
        "predictive_recovery",
        "feedback_forced",
    }
)
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
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}$")
_SKILL_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_SKILL_SOURCES = frozenset(
    {"project", "user", "compat_project", "compat_user"}
)
_INTENT_TYPES = frozenset(
    {
        "code",
        "debug",
        "refactor",
        "explain",
        "search",
        "review",
        "test",
        "document",
        "configure",
        "question",
        "chat",
        "memory",
        "system",
        "unknown",
    }
)
_ACTION_TYPES = frozenset(
    {
        "create",
        "read",
        "update",
        "delete",
        "execute",
        "analyze",
        "compare",
        "merge",
        "split",
        "move",
        "rename",
        "unknown",
    }
)
_MEMORY_CONTROLLER_MODES = frozenset(
    {"none", "summary", "standard", "strong"}
)
_MEMORY_NO_MATCH_REASONS = frozenset(
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
    }
)


class AgentEventSink(Protocol):
    """Receive one bounded structured Agent event without owning execution."""

    def emit(
        self,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None: ...


def emit_event_safely(
    sink: AgentEventSink | None,
    event_type: str,
    *,
    step: int | None = None,
    payload: Mapping[str, object] | None = None,
) -> None:
    """Emit without allowing ordinary observation failures into Agent flow."""
    if sink is None:
        return
    try:
        sink.emit(event_type, step=step, payload=payload)
    except Exception:  # noqa: BLE001 - event observation is strictly optional
        try:
            _logger.warning("Agent event sink unavailable.")
        except Exception:  # noqa: BLE001 - logging must remain optional too
            pass


def new_model_operation_id() -> str:
    """Return an observer-local ID for one actual model adapter invocation."""
    return f"modelop_{uuid.uuid4().hex}"


def new_context_operation_id() -> str:
    """Return an observer-local ID for one real Context attempt."""
    return f"ctxop_{uuid.uuid4().hex}"


def project_model_usage(usage: object | None) -> dict[str, object]:
    """Project canonical per-call usage into a fixed provider-neutral payload."""
    unavailable: dict[str, object] = {
        "source": "unavailable",
        "inputTokens": None,
        "outputTokens": None,
        "cacheReadTokens": None,
        "cacheCreationTokens": None,
    }
    if usage is None:
        return unavailable
    try:
        source = getattr(usage, "source")
        values = (
            getattr(usage, "input_tokens"),
            getattr(usage, "output_tokens"),
            getattr(usage, "cache_read_tokens"),
            getattr(usage, "cache_creation_tokens"),
        )
    except BaseException:  # noqa: BLE001 - projection must stay optional
        return unavailable
    if source not in _MODEL_USAGE_SOURCES:
        return unavailable
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > _MAX_MODEL_TOKENS
        )
        for value in values
    ):
        return unavailable
    if source == "unavailable":
        return unavailable
    return {
        "source": source,
        "inputTokens": values[0],
        "outputTokens": values[1],
        "cacheReadTokens": values[2],
        "cacheCreationTokens": values[3],
    }


def project_model_duration_ms(
    started_at: object, finished_at: object
) -> int | None:
    """Return a safe integer duration from two monotonic clock readings."""
    if (
        isinstance(started_at, bool)
        or isinstance(finished_at, bool)
        or not isinstance(started_at, (int, float))
        or not isinstance(finished_at, (int, float))
        or not math.isfinite(float(started_at))
        or not math.isfinite(float(finished_at))
    ):
        return None
    elapsed_ms = round((float(finished_at) - float(started_at)) * 1_000)
    if elapsed_ms < 0 or elapsed_ms > _MAX_MODEL_DURATION_MS:
        return None
    return elapsed_ms


def _bounded_count(value: object, *, maximum: int) -> int:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    ):
        return value
    raise ValueError("invalid observation count")


def _context_enum(value: object, allowed: frozenset[str]) -> str:
    projected = value if isinstance(value, str) else getattr(value, "value", None)
    if projected not in allowed:
        raise ValueError("invalid Context observation enum")
    return projected


def _context_operation_id(value: object) -> str:
    if not isinstance(value, str) or not _CONTEXT_OPERATION_ID_RE.fullmatch(value):
        raise ValueError("invalid Context operation ID")
    return value


def project_context_compaction_event(
    result: object | None,
    *,
    context_operation_id: object,
    path: object,
    messages_before: object,
    messages_after: object,
    trigger: object | None = None,
    strategy: object | None = None,
    tokens_freed: object | None = None,
) -> dict[str, object]:
    """Project one already-effective compaction without message content."""
    if result is not None:
        if getattr(result, "effective", None) is not True:
            raise ValueError("Context compaction was not effective")
        trigger = getattr(result, "trigger", trigger)
        strategy = getattr(result, "strategy", strategy)
        tokens_freed = getattr(result, "tokens_freed", tokens_freed)

    before = _bounded_count(messages_before, maximum=_MAX_CONTEXT_MESSAGES)
    after = _bounded_count(messages_after, maximum=_MAX_CONTEXT_MESSAGES)
    if after > before:
        raise ValueError("Context message count increased")

    payload: dict[str, object] = {
        "contextVersion": 1,
        "contextOperationId": _context_operation_id(context_operation_id),
        "path": _context_enum(path, _CONTEXT_PATHS),
        "trigger": _context_enum(trigger, _CONTEXT_TRIGGERS),
        "strategy": _context_enum(strategy, _CONTEXT_STRATEGIES),
        "effective": True,
        "messagesBefore": before,
        "messagesAfter": after,
        "messagesRemoved": before - after,
    }
    if tokens_freed is not None:
        safe_tokens_freed = _bounded_count(
            tokens_freed, maximum=_MAX_CONTEXT_TOKENS
        )
        if result is not None and safe_tokens_freed == 0:
            raise ValueError("effective Context compaction freed no tokens")
        payload["tokensFreed"] = safe_tokens_freed
    return payload


def project_recovery_started_event(
    *, context_operation_id: object, kind: object
) -> dict[str, object]:
    """Project the start of one actual overflow-recovery attempt."""
    return {
        "recoveryVersion": 1,
        "contextOperationId": _context_operation_id(context_operation_id),
        "kind": _context_enum(kind, _RECOVERY_KINDS),
        "reason": "context_overflow",
    }


def project_recovery_completed_event(
    result: object | None,
    *,
    context_operation_id: object,
    kind: object,
    messages_before: object,
    messages_after: object,
) -> dict[str, object]:
    """Project a normally-returned recovery result without its error input."""
    before = _bounded_count(messages_before, maximum=_MAX_CONTEXT_MESSAGES)
    after = _bounded_count(messages_after, maximum=_MAX_CONTEXT_MESSAGES)
    if after > before:
        raise ValueError("Recovery message count increased")
    recovered = result is not None and getattr(result, "effective", None) is True
    payload: dict[str, object] = {
        "recoveryVersion": 1,
        "contextOperationId": _context_operation_id(context_operation_id),
        "kind": _context_enum(kind, _RECOVERY_KINDS),
        "outcome": "recovered" if recovered else "not_recovered",
        "messagesBefore": before,
        "messagesAfter": after,
    }
    if recovered:
        safe_tokens_freed = _bounded_count(
            getattr(result, "tokens_freed", None), maximum=_MAX_CONTEXT_TOKENS
        )
        if safe_tokens_freed == 0:
            raise ValueError("recovered Context operation freed no tokens")
        payload["tokensFreed"] = safe_tokens_freed
    return payload


def project_working_memory_event(snapshot: object) -> dict[str, object]:
    """Project one pure process-local WorkingMemory snapshot."""
    entries = _bounded_count(
        getattr(snapshot, "entries", None), maximum=_MAX_MEMORY_COUNT
    )
    max_entries = _bounded_count(
        getattr(snapshot, "max_entries", None), maximum=_MAX_MEMORY_COUNT
    )
    protected_tokens = _bounded_count(
        getattr(snapshot, "protected_tokens", None), maximum=_MAX_MEMORY_TOKENS
    )
    max_tokens = _bounded_count(
        getattr(snapshot, "max_tokens", None), maximum=_MAX_MEMORY_TOKENS
    )
    if entries > max_entries or protected_tokens > max_tokens:
        raise ValueError("invalid WorkingMemory snapshot limits")
    return {
        "workingMemoryVersion": 1,
        "action": "protected",
        "scope": "process",
        "entries": entries,
        "maxEntries": max_entries,
        "protectedTokens": protected_tokens,
        "maxTokens": max_tokens,
    }


def _observation_warning(kind: str) -> None:
    try:
        _logger.warning("%s observation unavailable.", kind)
    except Exception:  # noqa: BLE001 - logging must remain optional too
        pass


def emit_context_compaction_safely(
    sink: AgentEventSink | None,
    result: object | None,
    *,
    step: int | None = None,
    **facts: object,
) -> None:
    """Project and emit one completed compaction without re-running it."""
    if sink is None:
        return
    try:
        payload = project_context_compaction_event(result, **facts)
    except BaseException:  # noqa: BLE001 - projection is optional observation
        _observation_warning("Context compaction")
        return
    emit_event_safely(sink, "context.compacted", step=step, payload=payload)


def emit_recovery_started_safely(
    sink: AgentEventSink | None,
    *,
    context_operation_id: object,
    kind: object,
    step: int | None = None,
) -> None:
    """Emit a fixed recovery start without exposing the triggering error."""
    if sink is None:
        return
    try:
        payload = project_recovery_started_event(
            context_operation_id=context_operation_id,
            kind=kind,
        )
    except BaseException:  # noqa: BLE001 - projection is optional observation
        _observation_warning("Context recovery")
        return
    emit_event_safely(sink, "recovery.started", step=step, payload=payload)


def emit_recovery_completed_safely(
    sink: AgentEventSink | None,
    result: object | None,
    *,
    step: int | None = None,
    **facts: object,
) -> None:
    """Emit one normally-returned recovery outcome without error text."""
    if sink is None:
        return
    try:
        payload = project_recovery_completed_event(result, **facts)
    except BaseException:  # noqa: BLE001 - projection is optional observation
        _observation_warning("Context recovery")
        return
    emit_event_safely(sink, "recovery.completed", step=step, payload=payload)


def emit_working_memory_safely(
    sink: AgentEventSink | None,
    snapshot: object,
    *,
    step: int | None = None,
) -> None:
    """Project and emit one already-created WorkingMemory snapshot."""
    if sink is None:
        return
    try:
        payload = project_working_memory_event(snapshot)
    except BaseException:  # noqa: BLE001 - projection is optional observation
        _observation_warning("WorkingMemory")
        return
    emit_event_safely(sink, "working_memory.observed", step=step, payload=payload)


def _safe_skill_item(value: object) -> dict[str, object] | None:
    qualified_name = getattr(value, "qualified_name", "") or getattr(
        value, "name", ""
    )
    source = getattr(value, "source", "")
    directory = getattr(value, "directory", "")
    score = getattr(value, "score", None)
    if (
        not isinstance(qualified_name, str)
        or not _SKILL_NAME_RE.fullmatch(qualified_name)
        or not isinstance(source, str)
        or source not in _SKILL_SOURCES
        or not isinstance(directory, str)
        or (directory and not _SKILL_DIRECTORY_RE.fullmatch(directory))
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or abs(float(score)) > 1_000_000
    ):
        return None
    return {
        "qualifiedName": qualified_name,
        "source": source,
        "directory": directory,
        "score": score,
    }


def project_skill_routing_event(routing_result: object) -> dict[str, object]:
    """Project one existing routing result without descriptions or task text."""
    intent_type = getattr(routing_result, "intent_type", None)
    action_type = getattr(routing_result, "action_type", None)
    total_skills = _bounded_count(
        getattr(routing_result, "total_skills", None),
        maximum=_MAX_ROUTING_SKILLS,
    )
    selected_value = getattr(routing_result, "selected_skills", None) or getattr(
        routing_result, "selected", None
    )
    if (
        intent_type not in _INTENT_TYPES
        or action_type not in _ACTION_TYPES
        or not isinstance(selected_value, (list, tuple))
        or len(selected_value) > _MAX_ROUTING_SKILLS
    ):
        raise ValueError("invalid routing observation")
    selected_count = len(selected_value)
    selected: list[dict[str, object]] = []
    for item in selected_value:
        projected = _safe_skill_item(item)
        if projected is not None:
            selected.append(projected)
        if len(selected) >= _MAX_ROUTED_SKILLS:
            break
    used_fallback = getattr(routing_result, "used_fallback", None)
    if not isinstance(used_fallback, bool):
        raise ValueError("invalid routing fallback state")
    return {
        "routingVersion": 1,
        "intentType": intent_type,
        "actionType": action_type,
        "totalSkills": total_skills,
        "selectedCount": selected_count,
        "selected": selected,
        "selectedTruncated": len(selected) < selected_count,
        "usedFallback": used_fallback,
    }


def emit_skill_routing_safely(
    sink: AgentEventSink | None, routing_result: object
) -> None:
    """Observe an already-computed Skill routing decision at most once per call."""
    if sink is None:
        return
    try:
        payload = project_skill_routing_event(routing_result)
    except Exception:  # noqa: BLE001 - projection is optional observation only
        try:
            _logger.warning("Skill routing observation unavailable.")
        except Exception:  # noqa: BLE001 - logging must remain optional too
            pass
        return
    emit_event_safely(sink, "skill.routed", payload=payload)


def _result_count(result: object, attribute: str) -> int:
    value = getattr(result, attribute, None)
    if not isinstance(value, (list, tuple)):
        raise ValueError("invalid Memory observation collection")
    return _bounded_count(len(value), maximum=_MAX_MEMORY_COUNT)


def project_memory_result_events(
    result: object,
) -> tuple[dict[str, object], dict[str, object]]:
    """Project final facts from one already-executed Memory retrieval result."""
    candidate_count = _result_count(result, "candidate_ids")
    selected_count = _result_count(result, "selected_ids")
    suppressed_count = _result_count(result, "suppressed_ids")
    rendered_count = _result_count(result, "rendered_ids")
    no_match = getattr(result, "no_match", None)
    if not isinstance(no_match, bool):
        raise ValueError("invalid Memory no-match state")
    raw_reason = getattr(result, "no_match_reason", "")
    no_match_reason = None
    if no_match:
        no_match_reason = (
            raw_reason if raw_reason in _MEMORY_NO_MATCH_REASONS else "other"
        )
    decision = getattr(result, "controller_decision", None)
    if not isinstance(decision, Mapping):
        raise ValueError("invalid Memory controller decision")
    raw_mode = decision.get("mode")
    controller_mode = (
        raw_mode if raw_mode in _MEMORY_CONTROLLER_MODES else "none"
    )
    total_tokens = _bounded_count(
        getattr(result, "total_tokens", None), maximum=_MAX_MEMORY_TOKENS
    )
    return (
        {
            "retrievalVersion": 1,
            "candidateCount": candidate_count,
            "selectedCount": selected_count,
            "suppressedCount": suppressed_count,
            "noMatch": no_match,
            "noMatchReason": no_match_reason,
        },
        {
            "renderVersion": 1,
            "renderedCount": rendered_count,
            "totalTokens": total_tokens,
            "controllerMode": controller_mode,
            "injected": rendered_count > 0,
        },
    )


def emit_memory_result_safely(
    sink: AgentEventSink | None, result: object | None
) -> None:
    """Observe one final Memory result without executing Memory code again."""
    if sink is None or result is None:
        return
    try:
        retrieved, rendered = project_memory_result_events(result)
    except Exception:  # noqa: BLE001 - projection is optional observation only
        try:
            _logger.warning("Memory result observation unavailable.")
        except Exception:  # noqa: BLE001 - logging must remain optional too
            pass
        return
    emit_event_safely(sink, "memory.retrieved", payload=retrieved)
    emit_event_safely(sink, "memory.rendered", payload=rendered)


__all__ = [
    "AgentEventSink",
    "emit_event_safely",
    "emit_context_compaction_safely",
    "emit_memory_result_safely",
    "emit_recovery_completed_safely",
    "emit_recovery_started_safely",
    "emit_skill_routing_safely",
    "emit_working_memory_safely",
    "new_context_operation_id",
    "new_model_operation_id",
    "project_context_compaction_event",
    "project_model_duration_ms",
    "project_model_usage",
    "project_memory_result_events",
    "project_recovery_completed_event",
    "project_recovery_started_event",
    "project_skill_routing_event",
    "project_working_memory_event",
]
