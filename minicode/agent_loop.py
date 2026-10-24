from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import http.client
import json
import os
import re
import sys
import time
import uuid
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from minicode.agent_budget import (
    AgentBudgetExceeded,
    AgentTurnBudget,
    record_budgeted_model_call,
)
from minicode.api_retry import MAX_RETRIES, ErrorCategory, calculate_backoff
from minicode.concurrent_tool_terminal import (
    CONCURRENT_TOOL_TERMINAL_EVENT_TYPE,
    MAX_CONCURRENT_TOOL_TERMINAL_EVENTS,
    project_concurrent_tool_terminal,
)
from minicode.context_manager import ContextManager, estimate_message_tokens
from minicode.curator_schedule import curator_completion_id
from minicode.logging_config import get_logger
from minicode.memory_provenance import MemoryProvenanceBatch
from minicode.model_call_control import (
    ModelCallDeadlineExceeded,
    call_model_next,
    estimate_budgeted_request_input,
    controlled_retry_sleep,
)
from minicode.permissions import PermissionManager
from minicode.pricing import (
    pricing_failure_event_payload,
    project_model_cost_event,
)
from minicode.run_events import (
    AgentEventSink,
    SkillUsageTracker,
    VerificationTracker,
    emit_context_compaction_failure_safely,
    emit_context_compaction_safely,
    emit_event_safely,
    emit_memory_result_safely,
    emit_recovery_completed_safely,
    emit_recovery_started_safely,
    emit_skill_attribution_safely,
    emit_task_outcome_safely,
    emit_working_memory_safely,
    new_context_operation_id,
    new_model_operation_id,
    project_model_duration_ms,
    project_model_usage,
    record_memory_provenance_safely,
    record_written_memory_ids_safely,
    record_written_memory_versions_safely,
)
from minicode.state import Store, AppState, increment_tool_calls, set_busy, set_idle
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_sessions import SubagentSessionStore
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.task_ledger import TaskLedger
from minicode.turn_completion import TurnCompletionPolicy, workflow_delivery_summary
from minicode.workflow_delivery import TurnWorkflowDelivery, invoke_with_delivery
from minicode.coding_progress import (
    CodingProgressTracker, SOURCE_READ_TOOLS, is_implementation_task,
)
from minicode.implementation_phases import (
    ImplementationPhaseController, PhaseCallDeadlineExceeded,
)
from minicode.tooling import (
    SkillLoadPolicy,
    ToolContext,
    ToolDefinition,
    ToolExecutionAbandoned,
    ToolRegistry,
    ToolResult,
    ToolTerminationReason,
)
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
    raise_if_cancelled,
)
from minicode.types import AgentStep, ChatMessage, ModelAdapter
from minicode.user_evidence import (
    TrustedUserEvidenceSeed,
    bind_trusted_user_evidence_to_prompt,
)
from minicode.verification_observation import (
    VERIFICATION_EVENT_TYPE,
    normalize_tool_verification,
)

# Hooks integration
from minicode.hooks import HookEvent, fire_hook_sync

# Intelligence integration
from minicode.agent_metrics import AgentMetricsCollector
from minicode.agent_intelligence import ErrorClassifier, NudgeGenerator, ToolScheduler
from minicode.recovery_control import RecoveryGuard
from minicode.working_memory import get_working_memory, protect_context

# Work chain integration
from minicode.intent_parser import ParsedIntent, parse_intent
from minicode.task_object import build_task, TaskObject, TaskState
from minicode.task_outcome import AgentOutcomeCapture, canonicalize_task_outcome
from minicode.pipeline_engine import get_pipeline_engine
from minicode.layered_context import ContextBuilder, LayeredContext
from minicode.decision_audit import get_auditor, DecisionOutcome

# 工程控制论集成
from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.cybernetic_supervisor import CyberneticSupervisor, save_supervisor_report  # noqa: F401
from minicode.feedforward_controller import FeedforwardController

# 高级控制论模块
from minicode.adaptive_pid_tuner import AdaptivePIDTuner  # noqa: F401
from minicode.state_observer import StateObserver, MeasurementVector  # noqa: F401
from minicode.decoupling_controller import DecouplingController  # noqa: F401
from minicode.predictive_controller import PredictiveController  # noqa: F401
from minicode.self_healing_engine import SelfHealingEngine

# 任务进度控制
from minicode.progress_controller import ProgressController, ProgressSignal, ProgressAction  # noqa: F401

# 记忆注入和模型选择控制
from minicode.model_registry import ModelSelectionController, ModelSelectionSignal  # noqa: F401

# 智能路由与自省 (Phase 3 导入)
from minicode.smart_router import SmartRouter, TaskOutcome  # noqa: F401
from minicode.agent_reflection import ReflectionEngine  # noqa: F401
from minicode.model_switcher import ModelSwitcher  # noqa: F401
from minicode.reflection_evidence import (
    append_trace_event,
    extract_tool_file_roles,
)

# 上下文管理集成 (Claude Code-style + Engineering Cybernetics)
from minicode.context_compactor import (
    ContextCompactor,
    AutoCompactConfig,
    strip_historical_session_memory,
)
from minicode.context_cybernetics import ContextCyberneticsOrchestrator
from minicode.cost_control import CostControlLoop
from minicode.memory import MemoryManager


class AgentTurnDeadlineExceeded(RuntimeError):
    """A caller-owned monotonic deadline expired at an Agent boundary."""


logger = get_logger("agent_loop")

# 甯搁噺锛氶伩鍏嶉噸澶嶇殑鎻愮ず鏂囨湰
NUDGE_CONTINUE = (
    "Continue immediately from your <progress> update with concrete tool calls, "
    "code changes, or an explicit <final> answer only if the task is complete. "
    "Prefer taking the next concrete action over explaining what you plan to do."
)

NUDGE_AFTER_TOOL_RESULT = (
    "You have received tool results. Review them briefly, then take the next "
    "concrete action: call another tool, edit code, or give an explicit <final> "
    "answer only if the task is truly complete. Do not restate what you just saw."
)

NUDGE_AFTER_UNFINISHED_ACTION = (
    "Your last message described the next action but did not perform it. "
    "Continue now with the concrete tool call. Give an explicit <final> answer "
    "only after the requested result is actually available."
)

NUDGE_AFTER_EMPTY_RESPONSE = (
    "Your last response was empty. This often happens after tool errors or when "
    "the model is uncertain. Pick the most likely next action and try it — you can "
    "adjust based on results. Call a tool, edit code, or give <final> if done."
)

NUDGE_AFTER_EMPTY_NO_TOOLS = (
    "Your last response was empty but you have not used any tools yet. Start by "
    "inspecting the relevant files (read_file, grep_files, list_files) to understand "
    "the codebase before making changes."
)

RESUME_AFTER_PAUSE = (
    "Resume from the previous pause. Continue with the next concrete tool call, "
    "code change, or <final> answer."
)

RESUME_AFTER_MAX_TOKENS = (
    "Your previous response was cut short by the token limit. Resume immediately "
    "with the next concrete action — pick up where you left off."
)

# Soft-landing instruction appended when a bounded nested run (a task
# sub-agent) exhausts its step limit: one closing model turn, with tool use
# declined, converts the work already done into a partial summary instead of
# discarding it as a hard failure.
MAX_STEPS_SOFT_LANDING_PROMPT = (
    "Your tool budget for this task is exhausted: the maximum number of steps "
    "has been reached. Do NOT call any more tools — they will not be executed. "
    "Based only on the findings you have already gathered, provide your final "
    "answer now: a concise summary of what you found, clearly marking what "
    "remains unverified or unexplored. End with <final>."
)

_TRACE_MAX_EVENTS = 500
_TRACE_MAX_FIELD_CHARS = 600
_TRACE_MAX_LIST_ITEMS = 12
_MAX_MODEL_TOOL_CALL_COUNT = MAX_CONCURRENT_TOOL_TERMINAL_EVENTS
_TRACE_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


def _redact_trace_text(text: str, limit: int = _TRACE_MAX_FIELD_CHARS) -> str:
    """Redact and bound one trace field.

    This runs before the evidence layer sees anything, so it -- not
    ``sanitize_evidence_text`` -- decides what a durable memory can ever
    contain. It reuses the evidence layer's rules rather than keeping a second
    copy: the local copy redacted every assignment unconditionally, which is
    how a correct root-cause explanation reached memory as
    "sets `_token=[REDACTED] BEFORE the increment".
    """
    from minicode.reflection_evidence import (
        _BEARER_RE,
        _SECRET_ASSIGNMENT_RE,
        _redact_secret_assignment,
        bound_keeping_both_ends,
    )

    redacted = _SECRET_ASSIGNMENT_RE.sub(_redact_secret_assignment, text)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    # Elide the middle rather than the tail. This bound runs before the
    # evidence layer sees anything, so tail-truncation here discarded pytest's
    # closing "FAILED tests/... - StaleTokenError" for good, and the memory
    # built from it could only say "fails with lease.transfer".
    return bound_keeping_both_ends(redacted, limit)


def _sanitize_trace_value(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, str):
        return _redact_trace_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in list(value.items())[:_TRACE_MAX_LIST_ITEMS]:
            key_str = str(key)
            if key_str.lower() in _TRACE_SECRET_KEYS or any(s in key_str.lower() for s in _TRACE_SECRET_KEYS):
                sanitized[key_str] = "[REDACTED]"
            else:
                sanitized[key_str] = _sanitize_trace_value(nested, depth + 1)
        if len(value) > _TRACE_MAX_LIST_ITEMS:
            sanitized["..."] = f"{len(value) - _TRACE_MAX_LIST_ITEMS} more keys"
        return sanitized
    if isinstance(value, (list, tuple)):
        items = [_sanitize_trace_value(v, depth + 1) for v in list(value)[:_TRACE_MAX_LIST_ITEMS]]
        if len(value) > _TRACE_MAX_LIST_ITEMS:
            items.append(f"... {len(value) - _TRACE_MAX_LIST_ITEMS} more items")
        return items
    return _redact_trace_text(str(value))


def _trace_error_type(output: str) -> str:
    match = re.match(r"\[([A-Za-z0-9_]+)\]", output.strip())
    if match:
        return match.group(1)
    lowered = output.lower()
    if "timeout" in lowered:
        return "TimeoutError"
    if "permission" in lowered or "denied" in lowered:
        return "PermissionError"
    return "ToolError"


def _append_trace_event(trace: list[dict[str, Any]], event: dict[str, Any]) -> None:
    append_trace_event(trace, event, max_events=_TRACE_MAX_EVENTS)


def _append_tool_trace_events(
    trace: list[dict[str, Any]],
    call: dict,
    result: ToolResult,
    step: int,
    recovery_note: str | None = None,
) -> None:
    tool_name = str(call.get("toolName", ""))
    call_id = str(call.get("id", ""))
    tool_input = call.get("input", {})
    file_roles = extract_tool_file_roles(tool_name, tool_input, event_type="tool_call")
    role_fields = {key: value for key, value in file_roles.items() if value}
    files = sorted(set(file_roles["files_read"] + file_roles["files_changed"]))
    _append_trace_event(trace, {
        "type": "tool_call",
        "step": step,
        "call_id": call_id,
        "tool_name": tool_name,
        "input": _sanitize_trace_value(tool_input),
        "files": files,
        **role_fields,
    })
    _append_trace_event(trace, {
        "type": "tool_result",
        "step": step,
        "call_id": call_id,
        "tool_name": tool_name,
        "status": "success" if result.ok else "error",
        "is_error": not result.ok,
        # Use the tool's own output, not the model-facing result_output: the
        # latter may carry an appended "[System note: ...]" retry nudge, which
        # must never leak into persisted trace summaries or reflection claims.
        "output_summary": _redact_trace_text(result.output, 500),
        "files": files,
        **role_fields,
    })
    if not result.ok:
        _append_trace_event(trace, {
            "type": "error",
            "step": step,
            "call_id": call_id,
            "tool_name": tool_name,
            "error_type": _trace_error_type(result.output),
            "message": _redact_trace_text(result.output, 500),
            "files": files,
            **role_fields,
        })
        if recovery_note:
            _append_trace_event(trace, {
                "type": "recovery_suggestion",
                "step": step,
                "call_id": call_id,
                "tool_name": tool_name,
                "suggestion": _redact_trace_text(recovery_note, 400),
                "files": files,
                **role_fields,
            })


def _emit_concurrent_terminal_event(
    sink: AgentEventSink | None,
    *,
    step: int,
    call: dict,
    status: str,
) -> None:
    """Persist one content-free terminal observation for a cancelled batch."""

    emit_event_safely(
        sink,
        CONCURRENT_TOOL_TERMINAL_EVENT_TYPE,
        step=step,
        payload=project_concurrent_tool_terminal(
            call_id=call.get("id", ""),
            tool_name=call.get("toolName", ""),
            status=status,
        ),
    )


@dataclass(frozen=True, slots=True)
class _ConcurrentToolDisposition:
    call: dict
    result: ToolResult
    status: str
    completed_result: bool


@dataclass(frozen=True, slots=True)
class _ConcurrentBatchDecision:
    dispositions: tuple[_ConcurrentToolDisposition, ...]
    control_error: BaseException | None
    cancellation: TurnCancellationRequested | None
    must_deliver: bool


def _is_fatal_concurrent_error(error: BaseException | None) -> bool:
    """Return whether an error must retain BaseException control authority."""

    return error is not None and not isinstance(error, Exception)


def _is_completed_concurrent_exception(error: BaseException | None) -> bool:
    """Return whether a finished tool raised an ordinary, account-able error."""

    return isinstance(error, Exception) and not isinstance(
        error,
        (TurnCancellationRequested, concurrent.futures.CancelledError),
    )


def _concurrent_exception_result(error: BaseException) -> ToolResult:
    """Project one ordinary concurrent tool exception as a stable result."""

    return ToolResult(
        ok=False,
        output=f"Concurrent execution error: {error}",
    )


def _reduce_concurrent_batch_shutdown(
    *,
    future_to_call: dict[concurrent.futures.Future, dict],
    observed_results: dict[concurrent.futures.Future, ToolResult],
    observed_errors: dict[concurrent.futures.Future, BaseException],
    cancellation: TurnCancellationRequested | None,
    collection_error: BaseException | None = None,
) -> _ConcurrentBatchDecision:
    """Snapshot one interrupted batch with a closed authority precedence.

    A successful ``Future.cancel`` proves the call never started.  A failed
    cancellation is followed by a fresh completion check; only a still-running
    Future is detached.  Results already finalized by a tool outrank every
    control-flow signal, followed by fatal process signals and then turn
    cancellation.
    """

    results = dict(observed_results)
    errors = dict(observed_errors)
    cancelled_before_start: set[concurrent.futures.Future] = set()
    detached_running: set[concurrent.futures.Future] = set()
    for future in future_to_call:
        if future in results or future in errors:
            continue
        if future.cancel():
            cancelled_before_start.add(future)
        elif future.done():
            try:
                results[future] = future.result()
            except BaseException as exc:  # noqa: BLE001 - reduced below
                errors[future] = exc
        else:
            detached_running.add(future)

    fatal_errors = (
        [collection_error]
        if _is_fatal_concurrent_error(collection_error)
        else []
    )
    fatal_errors.extend(
        error
        for future in future_to_call
        if _is_fatal_concurrent_error(error := errors.get(future))
    )
    cancellation_errors = (
        [cancellation] if cancellation is not None else []
    )
    if isinstance(collection_error, TurnCancellationRequested):
        cancellation_errors.append(collection_error)
    cancellation_errors.extend(
        error
        for future in future_to_call
        if isinstance(
            (error := errors.get(future)),
            TurnCancellationRequested,
        )
        and error is not cancellation
    )
    effective_cancellation = (
        cancellation_errors[0] if cancellation_errors else None
    )
    cancellation_driven = effective_cancellation is not None
    must_deliver = any(
        result._must_deliver for result in results.values()
    )

    dispositions: list[_ConcurrentToolDisposition] = []
    for future, call in future_to_call.items():
        result = results.get(future)
        error = errors.get(future)
        completed_result = result is not None or (
            _is_completed_concurrent_exception(error)
        )

        if result is not None:
            status = (
                "cancelled"
                if result._termination_reason
                is ToolTerminationReason.TURN_CANCELLED
                else "completed"
                if result.ok
                else "failed"
            )
        elif future in detached_running:
            result = ToolResult(
                ok=False,
                output=(
                    "error[turn_cancelled]: concurrent tool execution was "
                    "abandoned while still running"
                    if cancellation_driven
                    else "Concurrent execution was abandoned while still "
                    "running after a fatal sibling failure"
                ),
                _termination_reason=ToolTerminationReason.ABANDONED,
            )
            status = "abandoned_running"
        elif future in cancelled_before_start or isinstance(
            error,
            concurrent.futures.CancelledError,
        ):
            result = ToolResult(
                ok=False,
                output=(
                    "error[turn_cancelled]: concurrent tool was cancelled "
                    "before start"
                    if cancellation_driven
                    else "Concurrent execution was cancelled before start "
                    "after a fatal sibling failure"
                ),
                _termination_reason=(
                    ToolTerminationReason.TURN_CANCELLED
                    if cancellation_driven
                    else None
                ),
            )
            status = "cancelled_before_start"
        elif isinstance(error, TurnCancellationRequested):
            result = ToolResult(
                ok=False,
                output="error[turn_cancelled]: concurrent tool stopped",
                _termination_reason=ToolTerminationReason.TURN_CANCELLED,
            )
            status = "cancelled"
        elif _is_completed_concurrent_exception(error):
            assert error is not None
            result = _concurrent_exception_result(error)
            status = "failed"
        else:
            result = ToolResult(
                ok=False,
                output=(
                    "Concurrent execution error: "
                    + (type(error).__name__ if error is not None else "unknown")
                ),
            )
            status = "failed"
        dispositions.append(
            _ConcurrentToolDisposition(
                call=call,
                result=result,
                status=status,
                completed_result=completed_result,
            )
        )

    control_error: BaseException | None
    if must_deliver:
        control_error = None
    elif fatal_errors:
        control_error = fatal_errors[0]
    elif effective_cancellation is not None:
        control_error = effective_cancellation
    elif collection_error is not None:
        control_error = collection_error
    else:
        control_error = None
    return _ConcurrentBatchDecision(
        dispositions=tuple(dispositions),
        control_error=control_error,
        cancellation=effective_cancellation,
        must_deliver=must_deliver,
    )


def _record_concurrent_shutdown_observations(
    *,
    decision: _ConcurrentBatchDecision,
    execution_trace: list[dict[str, Any]],
    event_sink: AgentEventSink | None,
    step: int,
) -> BaseException | None:
    """Record bounded terminal observations without overriding FINAL authority."""

    first_fatal: BaseException | None = None

    def attempt(label: str, operation: Callable[[], object]) -> None:
        nonlocal first_fatal
        try:
            operation()
        except BaseException as exc:  # noqa: BLE001 - priority reduced below
            logger.warning(
                "Concurrent shutdown %s observation failed",
                label,
                exc_info=True,
            )
            if (
                not decision.must_deliver
                and first_fatal is None
                and _is_fatal_concurrent_error(exc)
            ):
                first_fatal = exc

    for disposition in decision.dispositions:
        attempt(
            "trace",
            lambda disposition=disposition: _append_tool_trace_events(
                execution_trace,
                disposition.call,
                disposition.result,
                step,
            ),
        )
        if decision.cancellation is not None:
            attempt(
                "journal",
                lambda disposition=disposition: _emit_concurrent_terminal_event(
                    event_sink,
                    step=step,
                    call=disposition.call,
                    status=disposition.status,
                ),
            )
    return first_fatal


def _finalize_completed_concurrent_dispositions(
    *,
    dispositions: tuple[_ConcurrentToolDisposition, ...],
    store: Store[AppState] | None,
    task_ledger: TaskLedger | None,
    recovery_guard: RecoveryGuard,
    step: int,
) -> BaseException | None:
    """Bounded accounting for real results captured by a shutdown sweep."""

    first_fatal: BaseException | None = None

    def attempt(label: str, operation: Callable[[], object]) -> None:
        nonlocal first_fatal
        try:
            operation()
        except BaseException as exc:  # noqa: BLE001 - re-raised after sweep
            logger.warning(
                "Concurrent completed-result %s finalization failed",
                label,
                exc_info=True,
            )
            if first_fatal is None and _is_fatal_concurrent_error(exc):
                first_fatal = exc

    for disposition in dispositions:
        if not disposition.completed_result:
            continue
        call = disposition.call
        result = disposition.result
        tool_name = call["toolName"]
        if store is not None:
            attempt(
                "store-busy",
                lambda tool_name=tool_name: store.set_state(
                    set_busy(tool_name)
                ),
            )
            attempt(
                "store-count",
                lambda: store.set_state(increment_tool_calls()),
            )
            attempt("store-idle", lambda: store.set_state(set_idle()))
        attempt(
            "post-hook",
            lambda tool_name=tool_name, result=result: fire_hook_sync(
                HookEvent.POST_TOOL_USE,
                tool_name=tool_name,
                tool_output=result.output,
                is_error=not result.ok,
                step=step,
            ),
        )
        attempt(
            "recovery",
            lambda call=call, result=result: recovery_guard.observe(
                call,
                ok=result.ok,
                output=result.output,
            ),
        )
        if task_ledger is not None:

            def record_ledger(
                call: dict = call,
                result: ToolResult = result,
            ) -> None:
                verification = normalize_tool_verification(
                    call["toolName"],
                    result.verification,
                )
                if verification is not None:
                    task_ledger.record_verification(verification)
                if not result.ok:
                    task_ledger.record_failed_attempt(
                        call["toolName"],
                        result.output,
                    )

            attempt("ledger", record_ledger)
    return first_fatal


def _is_empty_assistant_response(content: str) -> bool:
    return len(content.strip()) == 0


def _extract_task_description(messages: list[ChatMessage]) -> str:
    """Extract the task currently being worked on.

    Scans backwards. Scanning forwards returned the session's *first* user
    message, so in any multi-turn conversation every task -- and every memory
    written from it -- was filed under whatever was asked first: a run that
    fixed a failing test was stored as "你能用网络搜索小红是谁嘛？". Retrieval
    matches on that text, so those memories were also unfindable by their
    real subject.
    """
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            content = str(msg["content"])
            if not content.startswith("Continue") and not content.startswith("Your last"):
                return content[:500]
    return "Unknown task"


def _build_work_chain_task(
    messages: list[ChatMessage],
    parsed_intent: ParsedIntent | None = None,
) -> tuple[TaskObject | None, dict]:
    """Build TaskObject from conversation messages and return it with metadata."""
    raw_input = _extract_task_description(messages)
    if raw_input == "Unknown task":
        return None, {}
    intent = parsed_intent if parsed_intent is not None else parse_intent(raw_input)
    task = build_task(intent, raw_input)
    metadata = {
        "intent_type": intent.intent_type.value,
        "action_type": intent.action_type.value,
        "confidence": intent.confidence,
        "entities": intent.entities,
        "complexity": intent.complexity_hint,
    }
    logger.info(
        "Work chain: intent=%s action=%s confidence=%.2f complexity=%s",
        intent.intent_type.value, intent.action_type.value,
        intent.confidence, intent.complexity_hint,
    )
    return task, metadata


def _build_layered_context(
    messages: list[ChatMessage],
    system_prompt: str = "",
    project_context: str = "",
    task: TaskObject | None = None,
) -> tuple[LayeredContext, ContextBuilder]:
    """Build layered context from conversation and task."""
    context = LayeredContext()
    builder = ContextBuilder(context)
    if system_prompt:
        builder.set_system_prompt(system_prompt)
    if project_context:
        builder.add_project_memory(project_context)
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            builder.add_session_message(role, content)
    if task:
        scratchpad = (
            f"Task: {task.title}\n"
            f"Goal: {task.goal}\n"
            f"Constraints: {len(task.constraints)}\n"
            f"Expected outputs: {len(task.expected_outputs)}"
        )
        builder.add_scratchpad(scratchpad)
    return context, builder


def _parent_handoff_token_floor(
    messages: list[dict], calls: list[dict], *,
    assistant_content: str = "", reasoning_content: str | None = None,
    closing_prompt: str = "",
    completed_results: list[tuple[dict, ToolResult]] | None = None,
) -> int:
    """Price one parent hand-back before its workflow children spend tokens.

    This is an admission floor, not a second reservation or allowance. Every
    sibling uses the same floor against the shared atomic reservation; nested
    parents retain their ancestors' floors through the existing budget views.
    Receipt allowance covers the 8000-char authority projection, 3000-char
    implementation diagnostics, workspace receipt and bounded routing notes.
    CJK weighting gives an upper bound for our existing text estimator, not
    a guarantee about a provider's tokenizer or unknown failed-call usage.
    """
    workflows = [call for call in calls if call.get("toolName") == "task"
                 and isinstance(call.get("input"), dict)
                 and call["input"].get("agent_type") == "workflow"
                 and call["input"].get("action", "run") in {"run", "spawn"}]
    if not workflows:
        return 0
    from minicode.context_manager import estimate_message_tokens
    from minicode.tooling import _DEFAULT_MAX_OUTPUT, _LARGE_OUTPUT_THRESHOLD, _TOOL_OUTPUT_LIMITS
    from minicode.workflow_finalization import _WORKFLOW_OUTPUT_MAX_CHARS
    from minicode.workflow_runner import _EXECUTOR_OBSERVATION_MAX_CHARS

    pending = []
    for index, call in enumerate(calls):
        message = {"role": "assistant_tool_call", "input": call.get("input", {})}
        if index == 0:
            message["content"] = assistant_content or ""
            message["reasoningContent"] = reasoning_content or ""
        pending.append(message)
    input_tokens = _estimated_context_tokens(messages + pending)
    input_tokens += estimate_message_tokens({"role": "user", "content": closing_prompt})
    observed = {call["id"]: result.output for call, result in completed_results or []}
    workflow_ids = {call["id"] for call in workflows}
    receipt_tokens = 0
    for call in calls:
        if call["id"] in observed:
            # Serial siblings that already finished contribute their actual
            # text, avoiding a speculative large allowance for a tiny todo.
            receipt_tokens += estimate_message_tokens({"role": "tool_result", "content": observed[call["id"]]})
        else:
            if call["id"] in workflow_ids:
                chars = (_WORKFLOW_OUTPUT_MAX_CHARS + _EXECUTOR_OBSERVATION_MAX_CHARS
                         + 512 + 1024)  # workspace state + markers/routing/nudge
            else:
                # Mixed batches are legal. Reuse the registry's existing
                # projection thresholds; no output limit is changed here.
                chars = max(_LARGE_OUTPUT_THRESHOLD,
                            _TOOL_OUTPUT_LIMITS.get(call.get("toolName", ""), _DEFAULT_MAX_OUTPUT)) + 1024
            receipt_tokens += chars * 2 // 3 + 6
    return input_tokens + receipt_tokens + 2048


def _execute_single_tool(
    call: dict,
    tools: ToolRegistry,
    cwd: str,
    permissions: Any | None,
    runtime: dict | None,
    store: Any | None,
    step: int,
    on_tool_start: Callable[[str, dict], None] | None,
    on_tool_result: Callable[[str, str, bool], None] | None,
    tool_scheduler: Any | None = None,
    event_sink: AgentEventSink | None = None,
    skill_usage_tracker: SkillUsageTracker | None = None,
    cancellation_token: TurnCancellationToken | None = None,
    verification_tracker: VerificationTracker | None = None,
    agent_depth: int = 0,
    presentation: Any | None = None,
    agent_budget: Any | None = None,
    subagent_mailbox: Any | None = None,
    subagent_lifecycle: Any | None = None,
    subagent_sessions: Any | None = None,
    todo_state: Any | None = None,
    allow_user_interaction: bool = True,
    deadline_monotonic: float | None = None,
    skill_load_policy: SkillLoadPolicy | None = None,
    implementation_candidate_guard: Any | None = None,
    parent_handoff_preserve_tokens: int = 0,
    workflow_delivery: TurnWorkflowDelivery | None = None,
    workflow_user_request: str | None = None,
) -> ToolResult:
    """Execute a single tool call with hooks, state updates, and crash protection.
    
    Used both for serial execution and as a worker function for concurrent execution.
    Concurrent workers may receive callbacks so per-invocation observation stays
    ordered around the actual tool call; UI state and hooks remain serialized.
    
    Includes a global exception safety net: any unexpected crash in the tool
    execution pipeline (hooks, state updates, etc.) is caught and converted
    to an error ToolResult, preventing the entire agent loop from crashing.
    """
    tool_name = call["toolName"]
    tool_input = call["input"]
    result: ToolResult | None = None
    evidence_id = None
    evidence_finished = False

    def record_private_result(observed: ToolResult) -> None:
        nonlocal evidence_finished
        if evidence_finished:
            return
        evidence_finished = True
        if callable(evidence_recorder):
            try:
                evidence_recorder(tool_name=tool_name, phase="finished",
                    output=observed.output, is_error=not observed.ok,
                    invocation_id=evidence_id)
            except BaseException:  # Private diagnostics never replace control flow.
                pass

    evidence_recorder = getattr(event_sink, "record_tool_evidence", None)
    try:
        raise_if_cancelled(cancellation_token)
        if callable(evidence_recorder):
            try:
                evidence_id = evidence_recorder(tool_name=tool_name, phase="started", tool_input=tool_input)
            except BaseException:  # Observation must not prevent actual execution.
                pass
        # Pre-tool hooks and UI (only for serial execution)
        if on_tool_start:
            on_tool_start(tool_name, tool_input)
        
        if store:
            store.set_state(set_busy(tool_name))
        
        # Execute the tool with timeout protection. The timeout cannot kill
        # the worker thread, so it sets a shared abandonment event instead;
        # nested loops that receive this context can then unwind at their
        # next checkpoint.
        import concurrent.futures
        import contextvars
        import os
        import threading
        abandoned_event = threading.Event()
        _base_timeout = int(os.environ.get("MINICODE_TOOL_TIMEOUT", "120"))
        TOOL_TIMEOUT = (
            int(getattr(tool_scheduler, '_force_tool_timeout', _base_timeout))
            if tool_scheduler and hasattr(tool_scheduler, '_force_tool_timeout')
            else _base_timeout
        )
        def execute_tool() -> ToolResult:
            from minicode.verifier_sandbox import resolve_verifier_host_capabilities

            raise_if_cancelled(cancellation_token)
            if tool_name == "ask_user" and not allow_user_interaction:
                return ToolResult(
                    ok=False,
                    output=(
                        "User interaction is unavailable in this execution "
                        "mode. Continue using the available context and return "
                        "the result through the assistant final response."
                    ),
                )
            verifier_backend, verifier_executables = (
                resolve_verifier_host_capabilities(runtime)
            )
            from minicode.verifier_sandbox import (
                user_registered_trusted_executables,
            )

            verifier_explicit = user_registered_trusted_executables()
            candidate_before = (
                implementation_candidate_guard.capture_before([call], mutation_epoch=step)
                if implementation_candidate_guard is not None and tool_name in {"test_runner", "run_command"}
                else None
            )
            executed = invoke_with_delivery(workflow_delivery, call, lambda: tools.execute(
                tool_name, tool_input,
                ToolContext(
                    cwd=cwd,
                    permissions=permissions,
                    _runtime=runtime,
                    _implementation_candidate_guard=implementation_candidate_guard,
                    _verifier_sandbox_backend=verifier_backend,
                    _verifier_trusted_executables=verifier_executables,
                    _verifier_explicit_trusted_executables=(
                        verifier_explicit or None
                    ),
                    _central_workflow_v2_enforced=True,
                    _workflow_user_request=workflow_user_request,
                    _event_sink=event_sink,
                    _step=step,
                    _skill_usage_tracker=skill_usage_tracker,
                    _cancellation_token=cancellation_token,
                    _task_deadline_monotonic=deadline_monotonic,
                    _agent_depth=agent_depth,
                    _presentation=presentation,
                    _agent_budget=agent_budget,
                    _logical_agent_preserve_total_tokens=parent_handoff_preserve_tokens,
                    _tool_abandoned=abandoned_event,
                    _subagent_mailbox=subagent_mailbox,
                    _subagent_lifecycle=subagent_lifecycle,
                    _subagent_sessions=subagent_sessions,
                    _todo_state=todo_state,
                    _skill_load_policy=skill_load_policy,
                ),
            ))
            if candidate_before is not None:
                decision = implementation_candidate_guard.observe_results(
                    [(call, executed)], candidate_before, mutation_epoch=step,
                )
                if not decision.allowed and not executed._must_deliver:
                    executed.ok = False
                    executed.verification = None
                    executed.output += f"\nerror[{decision.code}]: {decision.message}"
            return executed

        tool_definition = tools.find(tool_name)
        if tool_name == "task":
            # Workspace-read-only tasks still spend shared model budget and
            # publish mailbox/session/lifecycle state. Concurrency-safe is not
            # abandon-safe: run/spawn stay in their current batch worker and
            # inherit Task's deadline/cancellation controls. Only poll merely
            # observes existing work; its result is acknowledged by the owner
            # after delivery, so a timed-out poll can safely be discarded.
            abandon_safe = (
                isinstance(tool_input, dict) and tool_input.get("action") == "poll"
            )
        else:
            abandon_safe = bool(
                tool_definition is not None and tool_definition.is_read_only
            )
        if not abandon_safe:
            # Python cannot kill a running thread. Returning a timeout while a
            # write-capable tool continues would let it mutate files/Memory
            # after the parent has moved on. Writers therefore run in the
            # owning thread and must enforce their own cancellable timeout.
            result = execute_tool()
        else:
            execution_context = contextvars.copy_context()
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(execution_context.run, execute_tool)
                try:
                    result = future.result(timeout=TOOL_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    abandoned_event.set()
                    result = ToolResult(
                        ok=False,
                        output=f"Tool '{tool_name}' timed out after {TOOL_TIMEOUT}s",
                        _termination_reason=ToolTerminationReason.DEADLINE_EXCEEDED,
                    )
            finally:
                # Only side-effect-free work may outlive this timeout. Writers
                # never enter this branch.
                pool.shutdown(wait=False, cancel_futures=True)
        # Any exception raised by execute_tool() propagates: cancellation is
        # re-raised below, everything else is converted to an error result by
        # the outer safety net. (The previous blind `execute_tool()` fallback
        # re-ran tools with side effects a second time.)

        # The owning invocation observes the returned value before cancellation
        # can suppress public delivery. IDs are local to this call, not its name.
        record_private_result(result)
        if not result._must_deliver:
            raise_if_cancelled(cancellation_token)

        verification = normalize_tool_verification(
            tool_name,
            result.verification,
        )
        if verification is not None:
            emit_event_safely(
                event_sink,
                VERIFICATION_EVENT_TYPE,
                step=step,
                payload=verification,
            )
            if verification_tracker is not None:
                verification_tracker.record(verification)

        # Post-tool state updates (only for serial execution)
        if store:
            store.set_state(increment_tool_calls())
            store.set_state(set_idle())
        
        if on_tool_result:
            on_tool_result(tool_name, result.output, not result.ok)
        
        return result
    
    except (
        KeyboardInterrupt,
        SystemExit,
        TurnCancellationRequested,
        ToolExecutionAbandoned,
    ):
        if result is not None and result._must_deliver:
            logger.exception(
                "Post-tool control flow could not replace a finalized state change"
            )
            return result
        # Always propagate these
        raise
    except Exception as exc:  # noqa: BLE001
        # Global safety net: catch ANY unexpected error in the tool execution
        # pipeline (hooks, state updates, permission checks, etc.) and convert
        # it to an error result. This prevents a single tool crash from
        # cascading into a full session failure.
        import traceback
        tb_excerpt = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:]).strip()
        error_type = type(exc).__name__

        if result is not None and result._must_deliver:
            logger.exception(
                "Post-tool failure could not replace a finalized state change"
            )
            if store:
                try:
                    store.set_state(set_idle())
                except Exception:
                    pass
            return result
        
        logger.error("Tool execution pipeline crashed (%s): %s", error_type, exc)
        
        # Ensure state is reset even on crash
        if store:
            try:
                store.set_state(set_idle())
            except Exception:
                pass
        
        failed = ToolResult(
            ok=False,
            output=f"[{error_type}] Tool execution pipeline crashed: {exc}\n"
                   f"Traceback:\n{tb_excerpt}"
        )
        record_private_result(result if result is not None else failed)
        return failed
    except BaseException:
        if result is not None and result._must_deliver:
            logger.exception(
                "Post-tool base exception could not replace a finalized state change"
            )
            if store:
                try:
                    store.set_state(set_idle())
                except BaseException:
                    pass
            return result
        raise


def _acknowledge_tool_delivery(result: ToolResult) -> bool:
    """Settle delivery only on the owning loop after transcript insertion."""
    if not callable(result._delivery_ack):
        return True
    try:
        result._delivery_ack()
        return True
    except Exception:  # unread results remain eligible for barrier redelivery
        logger.warning("Tool delivery acknowledgement failed", exc_info=True)
        return False


def _format_diagnostics(stop_reason: str | None, block_types: list[str] | None, ignored_block_types: list[str] | None) -> str:
    parts: list[str] = []
    if stop_reason:
        parts.append(f"stop_reason={stop_reason}")
    if block_types:
        parts.append(f"blocks={','.join(block_types)}")
    if ignored_block_types:
        parts.append(f"ignored={','.join(ignored_block_types)}")
    return f" Diagnostics: {'; '.join(parts)}." if parts else ""


def _is_recoverable_thinking_stop(*, is_empty: bool, stop_reason: str | None, ignored_block_types: list[str] | None) -> bool:
    if not is_empty:
        return False
    if stop_reason not in {"pause_turn", "max_tokens"}:
        return False
    return "thinking" in (ignored_block_types or [])


_UNFINISHED_ENGLISH_ACTION_TAIL = re.compile(
    r"(?:^|[.!?]\s+)(?:let\s+me|i(?:['’]ll|\s+will|\s+shall|\s+am\s+going\s+to)|"
    r"(?:next|now),?\s+i(?:['’]ll|\s+will))\s+(?:first\s+)?"
    r"(?:read|inspect|check|open|run|search|locate|try|fix|edit|verify|test|"
    r"continue|look(?:\s+at)?)\b[^.!?]*(?:[.!?])?\s*$",
    re.IGNORECASE,
)
_UNFINISHED_CHINESE_ACTION_TAIL = re.compile(
    r"(?:^|[。！？]\s*)(?:让我|我(?:先|现在|接下来|来|会|将|准备))\s*"
    r"(?:读取|检查|查看|打开|运行|搜索|查找|尝试|修复|编辑|验证|测试|继续)"
    r"[^。！？]*(?:[。！？])?\s*$"
)


def _looks_like_unfinished_action(content: str) -> bool:
    normalized = content.strip()
    if not normalized or len(normalized) > 600 or "```" in normalized:
        return False
    return bool(
        _UNFINISHED_ENGLISH_ACTION_TAIL.search(normalized)
        or _UNFINISHED_CHINESE_ACTION_TAIL.search(normalized)
    )


def _should_treat_assistant_as_progress(
    *,
    kind: str | None,
    content: str,
    saw_tool_result: bool,
    unfinished_retry_count: int,
) -> bool:
    if kind == "progress":
        return True
    if kind == "final":
        return False
    if not saw_tool_result:
        return False
    return unfinished_retry_count < 2 and _looks_like_unfinished_action(content)


def _phase_reasoning_options(model: ModelAdapter, *, implementation: bool = False) -> dict[str, str]:
    # Never assume a model/provider supports this control. Factory-owned
    # adapters advertise the exact route capability; otherwise leave it alone.
    if {"low", "high"}.issubset(getattr(model, "supported_reasoning_efforts", ()) or ()):
        return {"reasoning_effort_override": "high" if implementation else "low"}
    return {}


def _is_transient_model_transport_error(error: Exception) -> bool:
    """Retry interrupted exchanges, never infer retry authority from prose.

Authentication/certificate failures and invalid requests remain terminal.
A URLError is only a wrapper here; its typed underlying connection failure or
exact stdlib CONNECT gateway status must independently qualify.
Partial response bytes are not an AgentStep and
cannot authorize any tool execution.
"""
    if isinstance(error, urllib.error.HTTPError):
        return False
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    # http.client._tunnel exposes CONNECT's HTTP status only in this exact
    # OSError format; urllib preserves that typed exception as reason. Do not
    # classify arbitrary exception strings, authentication or TLS errors this way.
    if isinstance(error, urllib.error.URLError) and type(cause) is OSError:
        tunnel_status = re.fullmatch(r"Tunnel connection failed: (502|503|504)(?: [^\r\n]*)?", str(cause))
        if cause.errno is None and tunnel_status is not None:
            return True
    if isinstance(cause, (ModelCallDeadlineExceeded, PhaseCallDeadlineExceeded)):
        return False
    return isinstance(cause, (ConnectionError, http.client.IncompleteRead, TimeoutError))


def _model_next(
    model: ModelAdapter,
    messages: list[ChatMessage],
    *,
    on_stream_chunk: Callable[[str], None] | None,
    on_thinking_chunk: Callable[[str], None] | None = None,
    store: Store[AppState] | None,
    cancellation_token: TurnCancellationToken | None = None,
    deadline_monotonic: float | None = None,
    global_deadline_monotonic: float | None = None,
    request_options: dict[str, Any] | None = None,
) -> AgentStep:
    """Call provider adapters with store/thinking support while preserving test doubles."""
    try:
        return call_model_next(
            model,
            messages,
            optional_kwargs={
                "on_stream_chunk": on_stream_chunk,
                "on_thinking_delta": on_thinking_chunk,
                "store": store,
                **(request_options or {}),
            },
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
    except ModelCallDeadlineExceeded as error:
        if (deadline_monotonic is not None
                and (global_deadline_monotonic is None
                     or deadline_monotonic < global_deadline_monotonic)) and (
            global_deadline_monotonic is None or time.monotonic() < global_deadline_monotonic
        ):
            raise PhaseCallDeadlineExceeded(str(error)) from error
        raise AgentTurnDeadlineExceeded(str(error)) from error
    except TimeoutError as error:
        # Socket/header/idle timeouts are transport failures, even when the
        # request carries an Executor or native-only tool view. Only an elapsed
        # absolute control deadline makes this a phase/owner deadline failure.
        now = time.monotonic()
        if global_deadline_monotonic is not None and now >= global_deadline_monotonic:
            raise AgentTurnDeadlineExceeded(str(error)) from error
        if (deadline_monotonic is not None
                and (global_deadline_monotonic is None
                     or deadline_monotonic < global_deadline_monotonic)
                and now >= deadline_monotonic):
            raise PhaseCallDeadlineExceeded(str(error)) from error
        raise


def _record_context_token_observation(
    context_manager: ContextManager | None,
    current_messages: list[dict],
    next_step: Any,
    *, request_messages: list[dict] | None = None,
) -> None:
    """Feed provider-reported input tokens back into the context estimator.

    The adapters already return ``usage`` on every successful step; without
    this call ContextManager's calibration EMA was dead code and compaction
    thresholds relied solely on the character heuristic, which is especially
    wrong for CJK and tool-heavy contexts.
    """
    if context_manager is None:
        return
    try:
        usage = getattr(next_step, "usage", None)
        observed = getattr(usage, "input_tokens", None)
        if (
            isinstance(observed, int)
            and not isinstance(observed, bool)
            and observed > 0
        ):
            context_manager.messages = request_messages if request_messages is not None else current_messages
            try:
                context_manager.record_observed_tokens(observed)
            finally:
                context_manager.messages = current_messages
    except Exception as exc:  # noqa: BLE001 - calibration is optional
        logger.warning(
            "Context token calibration skipped: %s",
            exc,
        )


def _estimated_context_tokens(messages: list[dict]) -> int:
    try:
        return sum(estimate_message_tokens(message) for message in messages)
    except Exception:  # noqa: BLE001 - estimate is advisory
        return len(messages) * 128


def _record_agent_budget_usage(
    agent_budget: Any,
    next_step: Any,
    *,
    reservation: Any = None,
    model: Any,
    usage_observation: dict[str, object],
    cost_payload: dict[str, object] | None,
) -> None:
    """Charge one completed model response against the shared turn budget."""
    if agent_budget is None:
        return
    try:
        if not usage_observation:
            usage_observation = project_model_usage(
                getattr(next_step, "usage", None)
            )
        record_budgeted_model_call(
            agent_budget,
            model=model,
            usage=usage_observation,
            reservation=reservation,
            cost_payload=cost_payload,
        )
    except Exception as exc:  # noqa: BLE001 - budget accounting is advisory
        logger.warning("Agent budget usage recording failed: %s", exc)


def _fail_agent_budget_reservation(
    agent_budget: Any,
    reservation: Any,
    *,
    charge_estimate: bool = False,
) -> None:
    """Release a failed provider call's pending token lease, best effort."""
    if agent_budget is None or reservation is None:
        return
    try:
        settle = getattr(agent_budget, "fail_model_call", None)
        if callable(settle):
            settle(reservation, charge_estimate=charge_estimate)
    except Exception as exc:  # noqa: BLE001 - accounting cannot mask provider errors
        logger.warning("Agent budget failure settlement failed: %s", exc)


def _start_model_observation_clock() -> float | None:
    """Read the monotonic clock without allowing observation to alter execution."""
    try:
        return time.monotonic()
    except BaseException:  # noqa: BLE001 - the clock is optional observation
        return None


def _finish_model_observation_clock(started_at: float | None) -> int | None:
    if started_at is None:
        return None
    try:
        finished_at = time.monotonic()
    except BaseException:  # noqa: BLE001 - the clock is optional observation
        return None
    try:
        return project_model_duration_ms(started_at, finished_at)
    except BaseException:  # noqa: BLE001 - projection is optional observation
        return None


def _model_usage_observation(next_step: AgentStep) -> dict[str, object]:
    """Keep malformed usage projection from replacing a real model result."""
    try:
        usage = getattr(next_step, "usage", None)
        return project_model_usage(usage)
    except BaseException:  # noqa: BLE001 - projection is optional observation
        return {
            "source": "unavailable",
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadTokens": None,
            "cacheCreationTokens": None,
        }


def _model_cost_observation(
    model: object,
    usage: dict[str, object],
    operation_id: str,
) -> dict[str, object] | None:
    """Keep pricing and identity observation outside the model result path."""
    try:
        return project_model_cost_event(
            model=model,
            usage=usage,
            operation_id=operation_id,
        )
    except BaseException:  # noqa: BLE001 - pricing is optional observation
        try:
            return pricing_failure_event_payload(operation_id)
        except BaseException:  # noqa: BLE001 - never replace a model result
            return None


def _model_event_duration(
    payload: dict[str, object], duration_ms: int | None
) -> dict[str, object]:
    if duration_ms is not None:
        payload["durationMs"] = duration_ms
    return payload


def _working_memory_observation(
    event_sink: AgentEventSink | None, *, step: int | None
) -> None:
    """Snapshot the process-local tracker only when observation is enabled."""
    if event_sink is None:
        return
    try:
        snapshot = get_working_memory().snapshot()
    except BaseException:  # noqa: BLE001 - snapshot is optional observation
        try:
            logger.warning("WorkingMemory observation unavailable.")
        except Exception:
            pass
        return
    emit_working_memory_safely(event_sink, snapshot, step=step)


def _new_context_operation_observation(
    event_sink: AgentEventSink | None,
) -> str | None:
    """Generate no Context ID unless a real observed attempt needs one."""
    if event_sink is None:
        return None
    try:
        return new_context_operation_id()
    except BaseException:  # noqa: BLE001 - ID is optional observation
        try:
            logger.warning("Context observation unavailable.")
        except Exception:
            pass
        return None


def _strict_memory_authority_versions(
    value: object,
) -> tuple[tuple[str, str], ...] | None:
    """Normalize trusted pipeline output without accepting a valid subset."""
    if not isinstance(value, (list, tuple)):
        return None
    normalized: list[tuple[str, str]] = []
    for version in value:
        if (
            not isinstance(version, (list, tuple))
            or len(version) != 2
            or not all(isinstance(item, str) for item in version)
        ):
            return None
        normalized.append((version[0], version[1]))
    return tuple(normalized)


def _reflection_memory_operation_id(
    provenance: dict[str, Any],
) -> str | None:
    """Derive one opaque retry identity from trusted Run/Turn identities."""
    stable_identity = {
        key: value
        for key, value in provenance.items()
        if key in {"run_id", "subagent_id", "turn_id"}
        and isinstance(value, str)
        and value
    }
    if not stable_identity:
        return None
    stable_identity["agent_depth"] = provenance.get("agent_depth", 0)
    encoded = json.dumps(
        {
            "schema": "reflection_memory_operation_v1",
            **stable_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"memoryop_{uuid.uuid5(uuid.NAMESPACE_OID, encoded).hex}"


def _memory_provenance_batch(
    *,
    effect: str,
    origin: str,
    authority_versions: object,
    effect_observed: bool,
    receipt_id: object = None,
    context_operation_id: object = None,
) -> MemoryProvenanceBatch | None:
    """Build exact provenance or an explicit unknown marker, never a subset."""
    normalized = _strict_memory_authority_versions(authority_versions)
    safe_receipt_id = (
        receipt_id
        if isinstance(receipt_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", receipt_id)
        else None
    )
    safe_context_operation_id = (
        context_operation_id
        if isinstance(context_operation_id, str)
        and re.fullmatch(r"ctxop_[0-9a-f]{32}", context_operation_id)
        else None
    )
    if normalized:
        try:
            return MemoryProvenanceBatch(
                effect=effect,
                origin=origin,
                authority_versions=normalized,
                receipt_id=safe_receipt_id,
                context_operation_id=safe_context_operation_id,
                coverage="complete",
            )
        except (TypeError, ValueError):
            # The effect is real but one or more authority values are not
            # representable under the strict transport contract.
            pass
    if not effect_observed:
        return None
    return MemoryProvenanceBatch(
        effect=effect,
        origin=origin,
        authority_versions=(),
        receipt_id=safe_receipt_id,
        context_operation_id=safe_context_operation_id,
        coverage="unknown",
    )


def _emit_failed_context_compaction(
    event_sink: AgentEventSink | None,
    result: object | None,
    context_compactor: ContextCompactor | None,
    *,
    path: str,
    step: int | None = None,
    context_operation_id: str | None = None,
) -> None:
    """Observe a real ineffective attempt, excluding cheap policy skips."""
    if event_sink is None or result is None or context_compactor is None:
        return
    if getattr(result, "effective", None) is not False:
        return
    if getattr(result, "error", "") in {
        "Compaction retry suppressed for unchanged message state",
        "Compaction circuit breaker is open",
    }:
        return
    operation_id = context_operation_id or _new_context_operation_observation(
        event_sink
    )
    if operation_id is None:
        return
    dispatcher = context_compactor.auto_compact
    emit_context_compaction_failure_safely(
        event_sink,
        result,
        step=step,
        context_operation_id=operation_id,
        path=path,
        consecutive_failures=dispatcher.consecutive_failures,
        circuit_breaker_tripped=dispatcher.is_tripped,
    )


def _commit_materialized_compaction_memory(
    result: object | None,
    memory_pipeline: object | None,
    materialize: Callable[[], None],
    rollback: Callable[[], None] | None = None,
    *,
    event_sink: AgentEventSink | None = None,
    context_operation_id: str | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Atomically validate/install compaction Memory, then account for it."""
    if (
        result is None
        or not bool(getattr(result, "effective", False))
    ):
        return False, ()
    receipt = getattr(result, "memory_receipt", None)
    if receipt is None:
        materialize()
        return True, ()
    if memory_pipeline is None:
        return False, ()
    guard = getattr(memory_pipeline, "materialize_prompt_receipt", None)
    if not callable(guard):
        return False, ()
    if not callable(rollback):
        logger.warning(
            "Compaction Memory materialization refused without rollback"
        )
        return False, ()
    try:
        guarded = bool(guard(receipt, materialize, rollback))
    except TurnCancellationRequested:
        raise
    except ModelCallDeadlineExceeded as exc:
        raise AgentTurnDeadlineExceeded(str(exc)) from exc
    except Exception:
        # Store contention/corruption must reject this candidate, not abort an
        # entire agent turn.  The materializer only runs after a valid fence.
        logger.warning(
            "Compaction Memory authority guard failed safely",
            exc_info=True,
        )
        # Canonical MemoryPipeline already rolls back.  Repeat defensively for
        # compatibility guards that may raise after invoking the callback.
        try:
            rollback()
        except Exception:
            logger.critical(
                "Compaction Memory rollback failed after guard error",
                exc_info=True,
            )
        return False, ()
    if not guarded:
        try:
            rollback()
        except Exception:
            logger.critical(
                "Compaction Memory rollback failed after guard rejection",
                exc_info=True,
            )
        logger.info(
            "Compaction Memory authority changed before materialization"
        )
        return False, ()

    # The model-visible mutation has completed at this point.  Record the
    # exact receipt version independently of durable injection counters: a
    # later counter-write failure must not erase what influenced the model.
    provenance = _memory_provenance_batch(
        effect="rendered",
        origin="context_compaction",
        authority_versions=getattr(receipt, "rendered_versions", ()),
        effect_observed=True,
        receipt_id=getattr(receipt, "receipt_id", None),
        context_operation_id=context_operation_id,
    )
    if provenance is not None:
        record_memory_provenance_safely(event_sink, provenance)

    commit = getattr(memory_pipeline, "commit_prompt_receipt", None)
    if not callable(commit):
        return True, ()
    before_ids = set(
        getattr(memory_pipeline, "injected_memory_ids", ()) or ()
    )
    try:
        return True, tuple(commit(receipt))
    except Exception:
        logger.warning(
            "Compaction Memory receipt commit failed safely",
            exc_info=True,
        )
        after_ids = tuple(
            getattr(memory_pipeline, "injected_memory_ids", ()) or ()
        )
        return True, tuple(
            entry_id for entry_id in after_ids if entry_id not in before_ids
        )


def _finalize_compaction_materialization_state(
    result: Any,
    context_compactor: object | None,
    *,
    installed: bool,
    original_messages: list[dict[str, Any]],
    context_cybernetics: object | None = None,
) -> None:
    """Finalize deferred dispatcher/dedup/PID state for one candidate."""
    if not installed:
        result.success = False
    expected_state = "installed" if installed else "rejected"
    control_state_terminal = (
        str(getattr(getattr(result, "materialization_state", None), "value", ""))
        == expected_state
    )
    if context_compactor is not None:
        method_name = (
            "confirm_materialized"
            if installed
            else "discard_unmaterialized"
        )
        finalize = getattr(context_compactor, method_name, None)
        if callable(finalize):
            try:
                if installed:
                    finalize(result)
                else:
                    finalize(result, original_messages)
                control_state_terminal = (
                    str(
                        getattr(
                            getattr(result, "materialization_state", None),
                            "value",
                            "",
                        )
                    )
                    == expected_state
                )
            except Exception:
                control_state_terminal = False
                logger.warning(
                    "Compaction control-state finalization failed safely",
                    exc_info=True,
                )
    if context_cybernetics is not None and control_state_terminal:
        finalize_feedback = getattr(
            context_cybernetics,
            "finalize_materialization",
            None,
        )
        if callable(finalize_feedback):
            try:
                finalize_feedback(result, installed=installed)
            except Exception:
                logger.warning(
                    "Cybernetic compaction feedback finalization failed safely",
                    exc_info=True,
                )


def _deliver_memory_quality_feedback(
    *,
    orch: Any,
    canonical_outcome: Any,
    tool_abandoned: bool,
    verification_passed: int,
    verification_failed: int,
    event_sink: Any,
    task_description: str | None = None,
    execution_trace: list[dict[str, Any]] | None = None,
    agent_budget: Any = None,
) -> None:
    """Deliver end-of-turn memory utility feedback on every finalization path.

    The reduced authority path (step-budget exhaustion / soft landing)
    returns from inside ``finally`` before the full finalization section, so
    it must deliver this feedback explicitly: a turn that exhausted its
    budget right after a compaction install would otherwise never teach the
    Memory layer how its injected entries fared.

    When ``execution_trace`` is available, a per-memory contribution review
    runs first: whole-turn feedback spreads one outcome boolean over every
    rendered entry, while the review cites concrete memory/tool-event
    evidence per entry (task success alone never counts as help).
    """
    if tool_abandoned or orch is None:
        return
    pipeline = getattr(orch, "memory_pipeline", None)
    if pipeline is None:
        return
    feedback_observation_id = getattr(
        event_sink,
        "feedback_observation_id",
        None,
    )
    if not feedback_observation_id:
        feedback_observation_id = getattr(event_sink, "run_id", None)
    if (
        isinstance(execution_trace, list)
        and execution_trace
        and task_description is not None
    ):
        review = getattr(pipeline, "review_injected_memories", None)
        if callable(review):
            try:
                review(
                    task_description,
                    execution_trace,
                    observation_id=(
                        feedback_observation_id
                        if isinstance(feedback_observation_id, str)
                        and feedback_observation_id.strip()
                        else None
                    ),
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                )
            except TurnCancellationRequested:
                raise
            except Exception:  # noqa: BLE001 - review stays best-effort
                logger.debug(
                    "Memory contribution review delivery failed", exc_info=True
                )
    try:
        learning_success = getattr(canonical_outcome, "learning_success", None)
        memory_feedback_outcome = (
            learning_success if learning_success is not None else "unknown"
        )
        memory_feedback_kwargs: dict[str, object] = {
            "verification_passed": verification_passed,
            "verification_failed": verification_failed,
        }
        if (
            isinstance(feedback_observation_id, str)
            and feedback_observation_id.strip()
        ):
            memory_feedback_kwargs["observation_id"] = feedback_observation_id
        pipeline.feedback(memory_feedback_outcome, **memory_feedback_kwargs)
    except Exception:  # noqa: BLE001 - feedback stays best-effort
        logger.debug("Memory quality feedback delivery failed", exc_info=True)


def _looks_like_context_overflow(error_str: str) -> bool:
    """Match context-overflow API errors across providers.

    Anthropic says "prompt is too long: N tokens > ... maximum"; OpenAI says
    "context_length_exceeded" / "maximum context length"; other compatible
    providers say "input tokens exceed ...". String matching stays the last
    resort after the pre-request pipeline, so it must at least speak every
    dialect.
    """
    lowered = error_str.lower()
    if "prompt" in lowered and ("too long" in lowered or "exceeds" in lowered):
        return True
    if "context" in lowered and any(
        marker in lowered
        for marker in ("length", "window", "limit", "exceed")
    ):
        return True
    return "input tokens" in lowered and any(
        marker in lowered for marker in ("exceed", "limit", "maximum")
    )


def _apply_control_signal(
    *,
    control_signal: Any,
    system_state: Any,
    max_steps: int | None,
    tool_scheduler: ToolScheduler,
    context_compactor: ContextCompactor | None,
    model_switcher: Any | None,
    feedback_controller: Any | None = None,
    current_messages: list[dict] | None = None,
    context_manager: Any | None = None,
    event_sink: AgentEventSink | None = None,
) -> int | None:
    """Apply FeedbackController output to live runtime knobs."""
    if not control_signal or control_signal.confidence <= 0.6:
        return max_steps

    if (
        control_signal.limit_max_steps
        and max_steps is not None
        and control_signal.limit_max_steps < max_steps
    ):
        logger.info(
            "FeedbackController: limiting max_steps %d -> %d",
            max_steps, control_signal.limit_max_steps,
        )
        max_steps = control_signal.limit_max_steps

    if control_signal.adjust_token_budget != 1.0:
        if (
            context_compactor
            and hasattr(context_compactor, "_tool_budget")
            and context_compactor._tool_budget
        ):
            new_budget = max(
                1000,
                int(
                    context_compactor._tool_budget.budget_per_message
                    * control_signal.adjust_token_budget
                ),
            )
            context_compactor._tool_budget.budget_per_message = new_budget
            logger.info(
                "FeedbackController: token budget adjusted to %d (mult=%.2f)",
                new_budget, control_signal.adjust_token_budget,
            )

    if control_signal.reduce_parallelism:
        tool_scheduler._force_max_workers = min(
            getattr(tool_scheduler, "_force_max_workers", 2) or 2,
            2,
        )
        logger.info(
            "FeedbackController: reduce_parallelism -> max_workers=2 "
            "(oscillation=%.2f)",
            control_signal.oscillation_index,
        )

    if control_signal.adjust_concurrency != 0:
        cap = max(1, 4 + control_signal.adjust_concurrency)
        tool_scheduler._force_max_workers = cap
        logger.info(
            "FeedbackController: adjust_concurrency=%+d -> max_workers=%d",
            control_signal.adjust_concurrency, cap,
        )

    if control_signal.increase_model_level:
        logger.info(
            "FeedbackController: model upgrade recommended (errors=%.2f perf=%.2f)",
            system_state.error_frequency,
            system_state.performance_score(),
        )
        if model_switcher:
            model_switcher._pending_upgrade = True

    if control_signal.decrease_model_level:
        logger.info(
            "FeedbackController: model downgrade recommended (efficiency=%.2f)",
            system_state.token_efficiency,
        )

    if control_signal.suggest_memory_persistence:
        logger.info(
            "FeedbackController: memory-persistence signal observed; "
            "durable writes remain gated by task-end reflection"
        )

    if control_signal.recommend_skill_update:
        logger.info(
            "FeedbackController: skill-update signal observed without an "
            "approved update actuator (pattern=%.2f)",
            system_state.pattern_reuse_rate,
        )

    if control_signal.reduce_tool_timeout:
        new_timeout = max(5.0, control_signal.reduce_tool_timeout)
        tool_scheduler._force_tool_timeout = new_timeout
        logger.info(
            "FeedbackController: tool timeout reduced to %.1fs (high error rate)",
            new_timeout,
        )
    elif hasattr(tool_scheduler, '_force_tool_timeout'):
        # Reset timeout when signal no longer active
        del tool_scheduler._force_tool_timeout

    if control_signal.increase_nudge_frequency:
        tool_scheduler._force_nudge_frequency = True
        logger.info(
            "FeedbackController: nudge frequency increased (stability=%.2f)",
            system_state.stability_score(),
        )
    elif hasattr(tool_scheduler, '_force_nudge_frequency'):
        del tool_scheduler._force_nudge_frequency

    if control_signal.promote_pattern:
        if feedback_controller:
            feedback_controller.record_pattern_effectiveness(
                control_signal.promote_pattern, True
            )
            logger.info(
                "FeedbackController: pattern promoted '%s'",
                control_signal.promote_pattern,
            )

    if control_signal.force_compaction and context_compactor and current_messages is not None:
        try:
            compacted = context_compactor.compact_messages(current_messages, force=True)
            if compacted is not None and len(compacted) < len(current_messages):
                previous_count = len(current_messages)
                current_messages[:] = compacted
                if context_manager is not None:
                    context_manager.messages = list(compacted)
                logger.info(
                    "FeedbackController: forced compaction %d -> %d messages",
                    previous_count, len(compacted),
                )
            else:
                _emit_failed_context_compaction(
                    event_sink,
                    context_compactor.last_result,
                    context_compactor,
                    path="feedback_forced",
                )
        except TurnCancellationRequested:
            raise
        except ModelCallDeadlineExceeded as exc:
            raise AgentTurnDeadlineExceeded(str(exc)) from exc
        except Exception as exc:
            logger.warning("FeedbackController: forced compaction failed: %s", exc)

    return max_steps


_REQUIRED_SKILL_MIN_STEPS = 15


def _preemptive_step_cap(
    *,
    max_steps: int | None,
    recommended_steps: int,
    required_skill_count: int,
) -> int | None:
    """Keep Skill load-and-apply work from being classified into starvation."""
    if max_steps is None:
        return None
    effective_recommendation = recommended_steps
    if required_skill_count > 0:
        effective_recommendation = max(
            effective_recommendation,
            _REQUIRED_SKILL_MIN_STEPS,
        )
    return min(max_steps, effective_recommendation)


_PENDING_MEMORY_NOTICE_MARK = "[memory:pending-notice]"


def _count_pending_written_memories(
    orch: Any, written_memory_ids: tuple[str, ...]
) -> int:
    """Count how many of this turn's written entries await user review."""

    pipeline = getattr(orch, "memory_pipeline", None)
    manager = getattr(pipeline, "_memory", None)
    if manager is None:
        return 0
    pending = 0
    for entry_id in written_memory_ids:
        try:
            scope, entry = manager._find_entry_by_id(entry_id)
        except Exception:  # noqa: BLE001 - notice is observational only
            continue
        if entry is not None and getattr(entry, "approval_status", "") == "pending":
            pending += 1
    return pending


def _append_pending_memory_notice(
    current_messages: list[dict], pending_count: int, event_sink: Any
) -> None:
    """Surface awaiting-review lessons on the turn's final assistant message.

    Pending entries are invisible to retrieval, so without this footer the
    lesson backlog is silent until the user happens to run a memory command.
    """

    if pending_count <= 0:
        return
    notice = (
        f"\n\n{_PENDING_MEMORY_NOTICE_MARK} 本轮产生 {pending_count} 条待审教训："
        f"输入 /memory pending 查看列表，/memory review <id> 审核。"
    )
    for message in reversed(current_messages):
        if message.get("role") == "assistant" and isinstance(
            message.get("content"), str
        ):
            if _PENDING_MEMORY_NOTICE_MARK not in message["content"]:
                message["content"] = message["content"] + notice
            break
    emit_event_safely(
        event_sink,
        "memory.pending_notice",
        payload={
            "pendingWrittenCount": pending_count,
            "notice": notice.strip(),
        },
    )


def run_agent_turn(
    *,
    model: ModelAdapter,
    tools: ToolRegistry,
    messages: list[ChatMessage],
    cwd: str,
    permissions: PermissionManager | None = None,
    store: Store[AppState] | None = None,
    max_steps: int | None = None,
    on_tool_start: Callable[[str, dict], None] | None = None,
    on_tool_result: Callable[[str, str, bool], None] | None = None,
    on_assistant_message: Callable[[str], None] | None = None,
    on_progress_message: Callable[[str], None] | None = None,
    on_assistant_stream_chunk: Callable[[str], None] | None = None,
    on_thinking_chunk: Callable[[str], None] | None = None,
    context_manager: ContextManager | None = None,
    runtime: dict | None = None,
    metrics_collector: AgentMetricsCollector | None = None,
    system_prompt: str = "",
    project_context: str = "",
    enable_work_chain: bool = True,
    memory_manager: MemoryManager | None = None,
    event_sink: AgentEventSink | None = None,
    on_memory_written: Callable[[str], None] | None = None,
    agent_budget: Any | None = None,
    budget_exhausted_policy: str = "fallback",
    abandoned_event: Any | None = None,
    subagent_mailbox: Any | None = None,
    subagent_lifecycle: Any | None = None,
    cancellation_token: TurnCancellationToken | None = None,
    deadline_monotonic: float | None = None,
    agent_depth: int = 0,
    presentation: Any | None = None,
    outcome_capture: AgentOutcomeCapture | None = None,
    required_skill_names: list[str] | None = None,
    skill_load_policy: SkillLoadPolicy | None = None,
    # Advisory discovery gate: when the routing layer itself flagged likely
    # directories and the turn never touched the skill channel, nudge one
    # discovery round before the final answer is accepted.
    suggested_skill_directories: frozenset[str] | None = None,
    skill_discovery_gate: bool = False,
    allow_user_interaction: bool = True,
    max_steps_soft_landing: bool = False,
    enable_bounded_completion: bool = False,
    parsed_intent: ParsedIntent | None = None,
    implementation_task: bool | None = None,
    implementation_analysis_complete: bool = False,
    implementation_spec: Any | None = None,
    implementation_continuation: Any | None = None,
    implementation_checkpoint_sink: Callable[[dict, list], None] | None = None,
    context_checkpoint_sink: Callable[[list[ChatMessage]], None] | None = None,
    implementation_context_seed: bool = True,
    implementation_request_state: Any | None = None,
    enable_implementation_phases: bool = True,
    bounded_implementation_phases: bool = False,
    trusted_user_evidence: tuple[TrustedUserEvidenceSeed, ...] = (),
    defer_curator_maintenance: bool = False,
    host_native_result_tool: ToolDefinition | None = None,
) -> list[ChatMessage]:
    restored_completion_review = None
    if implementation_continuation is not None:
        from minicode.implementation_continuation import HostImplementationContinuation

        if not isinstance(implementation_continuation, HostImplementationContinuation):
            raise ValueError("continuation must be a host-bound capability")
        if (not {"edit_file", "write_file"}.intersection(tools.list_all())
                or getattr(tools, "_implementation_feedback", None) is not None
                or "executor_submit" in tools.list_all()):
            raise ValueError("continuation requires the original direct implementation owner")
        implementation_continuation.validate(spec=implementation_spec, messages=messages,
            context=ToolContext(cwd=cwd, permissions=permissions))
        restored_budget = agent_budget or AgentTurnBudget.from_runtime(runtime or {})
        if restored_budget.has_limits:
            raise ValueError("finite-budget continuation requires restoring its consumed budget")
        restored_completion_review = implementation_continuation.restore_review(implementation_spec)
        implementation_context_seed = False
    # Only the scheduler can bind this capability. A name in model-visible
    # messages or a generic note tool never grants a native closing path.
    if host_native_result_tool is not None and (
        host_native_result_tool.name != "review_submit"
        or not any(tool is host_native_result_tool for tool in tools.list())
    ):
        raise ValueError("native result capability must be the bound Reviewer tool")
    native_handoff_only = False
    native_handoff_attempted = False
    native_handoff_rejected = False
    native_handoff_prompt_sent = False
    seen_submission_rejections: set[str] = set()
    native_handoff_prompt = (
        "The remaining allowance is reserved for the assigned review conclusion. "
        "Submit review_submit using the evidence already available. If it does not "
        "establish a verdict, submit inconclusive with unknown criteria; do not "
        "invent findings or approval. No further inspection tools are available."
    )
    # Entry points opt the primary Agent into bounded completion. Keep the
    # caller's hard ceiling separate from feedforward's task recommendation.
    caller_max_steps = max_steps
    # Session Memory is current-Turn prompt material, not conversation
    # history. Every UI and nested-agent entrypoint converges here, so remove
    # old material before task parsing and let canonical retrieval decide what
    # is relevant for this Turn.
    current_messages = strip_historical_session_memory(list(messages))
    completion_evidence_views = None
    if (implementation_spec is not None
            and {"edit_file", "write_file"}.intersection(tools.list_all())
            and getattr(tools, "_implementation_feedback", None) is None
            and "executor_submit" not in tools.list_all()):
        from minicode.completion_feedback_view import CompletionFeedbackViews

        completion_evidence_views = CompletionFeedbackViews()
        # Restore only after native owner/context validation above, but before
        # compaction can copy a multi-megabyte host receipt into another request.
        # Host-only metadata retains exact originals for the next checkpoint.
        if implementation_continuation is not None:
            current_messages = [completion_evidence_views.project_message(message)
                                for message in current_messages]
        tools = tools.overlay([completion_evidence_views.tool_definition()])
    # A bound implementation keeps its original requirement authority even
    # when the latest user-role message is a host source packet or receipt.
    from minicode.implementation_execution import ImplementationSpec
    ledger_messages = ([{"role": "user", "content": implementation_spec.objective}]
        if isinstance(implementation_spec, ImplementationSpec) else current_messages)
    task_ledger = TaskLedger.from_messages(ledger_messages)
    implementation_completion_feedback = (implementation_continuation.feedback
        if implementation_continuation is not None else None)
    if implementation_completion_feedback is not None and completion_evidence_views is not None:
        implementation_completion_feedback = completion_evidence_views.project_message(
            implementation_completion_feedback)

    def reconcile_task_ledger(
        candidate: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = (
            task_ledger.reconcile(candidate)
            if task_ledger is not None
            else list(candidate)
        )
        if (implementation_completion_feedback is not None
                and implementation_completion_feedback not in result):
            # Restore compacted state ahead of retained history. A previously
            # delivered decision must never move behind its subsequent results.
            result = [message for message in result if not message.get("_implementation_completion_feedback")]
            insert_at = 0
            while insert_at < len(result) and result[insert_at].get("role") == "system":
                insert_at += 1
            result.insert(insert_at, implementation_completion_feedback)
        return result

    def observable_message_count(candidate: list[dict[str, Any]]) -> int:
        return sum(not message.get("_task_ledger") for message in candidate)

    current_messages = reconcile_task_ledger(current_messages)
    if agent_budget is None and runtime is not None:
        agent_budget = AgentTurnBudget.from_runtime(runtime)
    if subagent_mailbox is None:
        subagent_mailbox = SubagentMailbox()
    # Sibling of the mailbox: turn-scoped continuation sessions for finished
    # read-only sub-agents. Created fresh even when a caller supplied a
    # mailbox because nested loops have no `task` tool to consult it.
    subagent_sessions = SubagentSessionStore()
    # Loop-scoped todo state: this loop's todo_write target. Sub-agent
    # loops create their own below at recursion, so a child's list never
    # touches the parent's.
    todo_state: dict = {"tasks": [], "counter": 0}
    if budget_exhausted_policy not in {"fallback", "raise"}:
        budget_exhausted_policy = "fallback"
    tool_abandoned = False
    saw_tool_result = False
    empty_response_retry_count = 0
    recoverable_thinking_retry_count = 0
    model_timeout_retry_count = 0
    model_timeout_retry_pending = False
    model_transport_retry_count = 0
    unfinished_action_retry_count = 0
    tool_error_count = 0
    recovery_guard = RecoveryGuard()
    turn_started_at = time.perf_counter()
    last_step_duration_seconds = 0.0
    last_step_tool_call_count = 0
    last_step_tool_error_count = 0
    total_tool_call_count = 0
    completed_tool_step_count = 0
    failed_tool_step_count = 0
    skill_requirement_nudged = False
    skill_discovery_nudged = False
    step = 0
    turn_outcome = "unknown"
    execution_trace: list[dict[str, Any]] = []
    current_user_message = next(
        (
            message.get("content")
            for message in reversed(current_messages)
            if message.get("role") == "user"
            and isinstance(message.get("content"), str)
            and not message.get("_task_ledger")
        ),
        "",
    )
    # This is feedback, never authorization. Read-only role registries cannot
    # be turned into executors by a quoted "fix" inside their assignment.
    progress_capable = (
        bool({"edit_file", "write_file", "task"}.intersection(tools.list_all()))
        and getattr(getattr(permissions, "auto_checker", None), "mode", None) != "plan"
    )
    progress_intent = parsed_intent
    # Host-bound roles already specify whether they implement. In particular,
    # a read-only review's source/receipt packet is evidence, not a new user
    # instruction to classify. Parse only when the progress tracker actually
    # needs to infer its enabled state; retain the complete model context.
    if (progress_capable and implementation_task is None
            and progress_intent is None and current_user_message):
        progress_intent = parse_intent(current_user_message)
    coding_progress = CodingProgressTracker(enabled=(
        progress_capable and (
            implementation_task if implementation_task is not None
            else is_implementation_task(progress_intent)
        )
    ))
    source_read_names = {
        tool.name for tool in tools.list()
        if tool.name in SOURCE_READ_TOOLS and tool.is_read_only
    }
    delivered_read_ids: set[str] = set()
    delivered_source_paths: dict[str, str] = {}
    invalidated_read_ids: set[str] = set()
    current_turn_id = next(
        (
            message.get("_conversation_turn_id")
            for message in reversed(current_messages)
            if message.get("role") == "user"
            and isinstance(message.get("_conversation_turn_id"), str)
        ),
        None,
    )
    current_run_id = getattr(event_sink, "run_id", None)
    run_only_curator_completion_id = curator_completion_id(current_run_id)
    event_sink_curator_completion_id = getattr(
        event_sink,
        "curator_completion_id",
        None,
    )
    current_curator_completion_id = (
        event_sink_curator_completion_id
        if event_sink_curator_completion_id == run_only_curator_completion_id
        and event_sink_curator_completion_id is not None
        else curator_completion_id(current_run_id, current_turn_id)
    )
    normalized_user_evidence = bind_trusted_user_evidence_to_prompt(
        trusted_user_evidence,
        current_user_message,
        agent_depth=agent_depth,
        current_turn_id=current_turn_id,
        current_run_id=current_run_id,
    )
    for trusted_seed in normalized_user_evidence:
        if trusted_seed.kind == "followup_correction" and (
            trusted_seed.current_turn_id != current_turn_id
            or trusted_seed.current_run_id != current_run_id
        ):
            continue
        if trusted_seed.kind == "stable_constraint" and (
            trusted_seed.current_turn_id is not None
            and (
                trusted_seed.current_turn_id != current_turn_id
                or trusted_seed.current_run_id != current_run_id
            )
        ):
            continue
        safe_statement = _redact_trace_text(
            trusted_seed.statement,
            _TRACE_MAX_FIELD_CHARS,
        )
        # A redacted correction/constraint no longer proves the exact user
        # statement bound by the seed digest. Drop it instead of persisting a
        # partially rewritten strong claim.
        if safe_statement != trusted_seed.statement:
            continue
        trace_event = trusted_seed.to_trace_event()
        trace_event["content"] = safe_statement
        _append_trace_event(execution_trace, trace_event)
    memory_mgr = memory_manager

    tool_scheduler = ToolScheduler(metrics_collector=metrics_collector)
    # Loading is execution authority, not merely an optional event. Keep the
    # tracker even without an event sink so routed-Skill enforcement works on
    # every entrypoint.
    skill_usage_tracker = SkillUsageTracker()
    verification_tracker = VerificationTracker()
    required_skills = {
        str(name).strip()
        for name in (required_skill_names or [])
        if str(name).strip()
    }

    def raise_if_deadline_exceeded() -> None:
        if (
            deadline_monotonic is not None
            and time.monotonic() >= deadline_monotonic
        ):
            raise AgentTurnDeadlineExceeded("agent turn deadline exceeded")

    def enforce_skill_load_before_final() -> str:
        """Return ``ok``, ``retry``, or a terminal failure message."""
        nonlocal skill_requirement_nudged
        loaded_names = skill_usage_tracker.loaded_names()
        missing = sorted(required_skills - loaded_names)
        if not missing:
            return "ok"
        if not skill_requirement_nudged:
            skill_requirement_nudged = True
            current_messages.append(
                {
                    "role": "user",
                    "content": (
                        "The routed Skill contract is not satisfied. Before "
                        "answering, call load_skill for: " + ", ".join(missing)
                    ),
                }
            )
            return "retry"
        return (
            "The turn stopped because required routed Skills were not loaded: "
            + ", ".join(missing)
        )

    def enforce_discovery_before_final() -> str:
        """Return ``ok`` or ``retry``; advisory-once, never fails the turn."""
        nonlocal skill_discovery_nudged
        if not skill_discovery_gate or not suggested_skill_directories:
            return "ok"
        if skill_usage_tracker.discovery_seen():
            return "ok"
        if not skill_discovery_nudged:
            skill_discovery_nudged = True
            current_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Skill routing flagged these directories as possibly "
                        "relevant to this task: "
                        + ", ".join(sorted(suggested_skill_directories))
                        + ". Before answering, inspect the actual skill "
                        "descriptions with browse_skill_directory or "
                        "search_skills, and load an applicable skill if one "
                        "covers the requested workflow."
                    ),
                }
            )
            return "retry"
        return "ok"

    def enforce_subagent_completion_before_final(content: str) -> bool:
        """Keep assistant final closed until owned async results are visible."""
        barrier = getattr(subagent_lifecycle, "finalization_barrier", None)
        if not callable(barrier):
            return False
        try:
            try:
                state = barrier(wait_seconds=0.25)
            except TypeError:
                # Compatibility for an externally supplied lifecycle that
                # implemented the original no-argument barrier seam.
                state = barrier()
        except Exception:  # noqa: BLE001 - lifecycle authority fails closed
            logger.warning(
                "Async sub-agent finalization barrier failed",
                exc_info=True,
            )
            current_messages.append(
                {
                    "role": "user",
                    "content": (
                        "The asynchronous sub-agent lifecycle could not be "
                        "verified. Do not finalize; use task poll or cancel "
                        "for every spawned sub-agent first."
                    ),
                }
            )
            return True
        if bool(state.get("ready")):
            return False

        if content:
            if on_progress_message:
                on_progress_message(content)
            current_messages.append(
                {"role": "assistant_progress", "content": content}
            )
            _append_trace_event(
                execution_trace,
                {
                    "type": "assistant_step",
                    "step": step,
                    "content_kind": "subagent_finalization_blocked",
                    "content": _redact_trace_text(content),
                },
            )

        pending = state.get("pending")
        completed = state.get("completed")
        if isinstance(completed, list) and completed:
            instruction = (
                "One or more sub-agent results are now available. Review and "
                "synthesize these bounded results before giving the final answer."
            )
        else:
            instruction = (
                "One or more owned sub-agents are still running. Do not finalize. "
                "Use the task tool to poll or cancel every pending sub-agent, "
                "then synthesize their terminal results."
            )
        projection = {
            "lifecycleVersion": state.get("lifecycleVersion"),
            "pending": pending if isinstance(pending, list) else [],
            "completed": completed if isinstance(completed, list) else [],
        }
        current_messages.append(
            {
                "role": "user",
                "content": (
                    instruction
                    + "\nLifecycle snapshot: "
                    + json.dumps(
                        projection,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                ),
            }
        )
        acknowledge = getattr(subagent_lifecycle, "acknowledge_delivery", None)
        if isinstance(completed, list) and completed and callable(acknowledge):
            try:
                acknowledge(tuple(str(item["subagentId"]) for item in completed))
            except Exception:
                logger.warning(
                    "Async result delivery acknowledgement failed", exc_info=True
                )
        return True

    # Initialize work chain if enabled
    task: TaskObject | None = None
    task_metadata: dict = {}
    layered_context: LayeredContext | None = None
    context_builder: ContextBuilder | None = None
    auditor = get_auditor() if enable_work_chain else None

    # 工程控制论控制器初始化（通过 Orchestrator 统一管理）
    orch: CyberneticOrchestrator | None = None
    feedback_controller: Any = None
    feedforward_controller: Any = None
    stability_monitor: Any = None
    cybernetic_supervisor: Any = None

    adaptive_pid_tuner: Any = None
    state_observer: Any = None
    decoupling_controller: Any = None
    predictive_controller: Any = None
    self_healing_engine: Any = None
    progress_controller: Any = None
    model_selection_ctrl: Any = None
    smart_router: Any = None
    model_switcher: Any = None
    context_compactor: ContextCompactor | None = None
    context_cybernetics: ContextCyberneticsOrchestrator | None = None
    cost_control: CostControlLoop | None = None

    if enable_work_chain:
        task, task_metadata = (
            _build_work_chain_task(current_messages)
            if parsed_intent is None
            else _build_work_chain_task(current_messages, parsed_intent)
        )
        layered_context, context_builder = _build_layered_context(
            current_messages, system_prompt, project_context, task,
        )
        get_pipeline_engine()
        # 初始化所有工程控制论控制器（通过 Orchestrator 统一管理）
        orch = CyberneticOrchestrator()
        orch._workspace = cwd
        orch.initialize(model, tools, runtime)
        feedback_controller = orch.feedback
        cybernetic_supervisor = orch.cyber_supervisor
        stability_monitor = orch.stability
        adaptive_pid_tuner = orch.adaptive_tuner
        state_observer = orch.state_observer
        decoupling_controller = orch.decoupling
        predictive_controller = orch.predictive
        progress_controller = orch.progress
        model_selection_ctrl = orch.model_ctrl
        smart_router = orch.smart_router
        model_switcher = orch.model_switcher
        logger.info("CyberneticOrchestrator: %d controllers initialized", 15)
        if smart_router and task:
            try:
                current_model_id = model.model_id if hasattr(model, 'model_id') else ""
                task_text = task.raw_input if hasattr(task, 'raw_input') else str(current_messages[-1].get('content', ''))
                routing, switch_result = smart_router.route_and_switch(
                    task_text,
                    current_model=current_model_id,
                )
                logger.info(
                    "SmartRouter: model=%s tier=%s cost=$%.4f reason=%s",
                    routing.selected_model, routing.tier_name,
                    routing.estimated_cost, routing.reasoning[:80],
                )
                # 如果路由推荐了不同模型且切换成功，更新 model 引用
                if switch_result and switch_result.success:
                    model = switch_result.adapter
                    logger.info(
                        "SmartRouter: switched model %s -> %s",
                        switch_result.old_model, switch_result.new_model,
                    )
            except Exception:
                pass

        # 初始化前馈控制器（预判式优化）
        if task:
            feedforward_controller = FeedforwardController()
            preemptive_config = feedforward_controller.preconfigure(task.parsed_intent, task.raw_input)
            risk_assessment = feedforward_controller.assess_risks(task.parsed_intent, preemptive_config)
            logger.info(
                "Feedforward control: config=%s risk=%s",
                preemptive_config.recommended_model, risk_assessment.risk_level,
            )
            # Apply feedforward preemptive config to execution parameters
            if preemptive_config.confidence > 0.6:
                max_steps = _preemptive_step_cap(
                    max_steps=max_steps,
                    recommended_steps=preemptive_config.max_turn_steps,
                    required_skill_count=len(required_skills),
                )
                logger.info(
                    "Feedforward: max_steps=%s model=%s timeout=%.1fs",
                    max_steps,
                    preemptive_config.recommended_model,
                    preemptive_config.tool_timeout_seconds,
                )
            if risk_assessment.risk_level in ("high", "critical"):
                logger.warning(
                    "Feedforward risk assessment: level=%s probability=%.2f risks=%s",
                    risk_assessment.risk_level,
                    risk_assessment.estimated_failure_probability,
                    ", ".join(risk_assessment.identified_risks[:3]),
                )

        # 模型选择控制器：根据任务特征推荐模型
        if model_selection_ctrl and task:
            try:
                model_signal = ModelSelectionSignal(
                    task_complexity=getattr(task, 'complexity', 'moderate') if hasattr(task, 'complexity') else "moderate",
                    budget_pressure=0.3,
                    latency_pressure=0.3,
                    recent_failures=0,
                    current_model=model.model_id if hasattr(model, 'model_id') else "",
                )
                model_decision = model_selection_ctrl.decide(model_signal)
                logger.info(
                    "ModelSelectionController: model=%s score=%.2f effort=%s reasons=%s",
                    model_decision.model, model_decision.score,
                    model_decision.reasoning_effort.value,
                    ", ".join(model_decision.reasons),
                )
            except Exception:
                pass

        # 初始化上下文管理器 (Claude Code-style + Engineering Cybernetics)
        # 必须在 SelfHealingEngine 之前初始化，因为自愈引擎需要委托压缩操作
        if memory_mgr is not None:
            if orch:
                orch._last_model = model
                orch._workspace = cwd
                orch.wire_memory(memory_mgr)
                if task:
                    try:
                        task_desc = task.raw_input if hasattr(task, "raw_input") else ""
                        current_files = list(getattr(task, "relevant_files", []) or [])
                        if context_manager:
                            # Dashboard/TUI pass a fresh or session-owned
                            # manager whose cached messages may still describe
                            # the previous Turn. Hydrate the actual sanitized
                            # Session before pressure-sensitive Memory routing.
                            context_manager.messages = list(current_messages)
                        context_usage = (
                            context_manager.get_stats().usage_percentage / 100.0
                            if context_manager
                            else 0.5
                        )
                        current_messages = orch.inject_memories(
                            task_desc,
                            current_messages,
                            current_files=current_files,
                            context_usage=context_usage,
                            agent_budget=agent_budget,
                            event_sink=event_sink,
                            cancellation_token=cancellation_token,
                            deadline_monotonic=deadline_monotonic,
                        )
                        if event_sink is not None:
                            receipt = (
                                orch.memory_pipeline.last_committed_prompt_receipt
                            )
                            provenance = (
                                _memory_provenance_batch(
                                    effect="rendered",
                                    origin="task_start",
                                    authority_versions=(
                                        receipt.rendered_versions
                                    ),
                                    effect_observed=True,
                                    receipt_id=receipt.receipt_id,
                                )
                                if receipt is not None
                                else None
                            )
                            emit_memory_result_safely(
                                event_sink,
                                orch.memory_pipeline.last_retrieval_result,
                                provenance_batch=provenance,
                                draft_rendered_ids=getattr(
                                    orch.memory_pipeline, "injected_draft_ids", ()
                                ),
                            )
                    except ModelCallDeadlineExceeded as exc:
                        raise AgentTurnDeadlineExceeded(str(exc)) from exc
                    except TurnCancellationRequested:
                        raise
                    except Exception as exc:
                        logger.warning("Memory injection failed safely: %s", exc)
        if context_manager:
            # Reuse the session-owned controllers when the model identity is
            # unchanged. Rebuilding ContextCompactor/ContextCybernetics every
            # turn discarded PID, predictor, adaptive threshold, dedup and
            # microcompact state, so cross-turn control was effectively
            # turn-local even though the ContextManager itself lived on.
            model_key = str(
                getattr(model, "model_id", None)
                or (runtime or {}).get("model", "")
                or type(model).__name__
            )
            cached_compactor = getattr(
                context_manager, "_context_compactor", None
            )
            cached_cybernetics = getattr(
                context_manager, "_context_cybernetics", None
            )
            cached_model = getattr(context_manager, "_controller_model", "")
            if (
                cached_compactor is not None
                and cached_cybernetics is not None
                and cached_model == model_key
            ):
                context_compactor = cached_compactor
                context_cybernetics = cached_cybernetics
                # Refresh the bindings that can change while the ContextManager
                # survives (window size, workspace, memory store, model adapter
                # used for LLM summaries).
                context_compactor._context_window = (
                    context_manager.context_window
                )
                context_compactor._workspace = Path(cwd)
                if hasattr(context_compactor, "_auto_compact"):
                    from minicode.context_compactor import LLMSummaryGenerator

                    compaction_memory_source = (
                        getattr(orch, "memory_pipeline", None)
                        if orch is not None
                        else None
                    ) or memory_mgr
                    context_compactor._auto_compact._memory = (
                        compaction_memory_source
                    )
                    context_compactor._auto_compact._session_memory_engine._memory = (
                        compaction_memory_source
                    )
                    context_compactor._auto_compact._summary_generator = (
                        LLMSummaryGenerator(
                            model,
                            agent_budget=agent_budget,
                            event_sink=event_sink,
                            cancellation_token=cancellation_token,
                            deadline_monotonic=deadline_monotonic,
                        )
                        if model is not None
                        else None
                    )
                logger.info(
                    "ContextCybernetics reused across turns (model=%s)",
                    model_key,
                )
            else:
                compact_config = AutoCompactConfig(
                    threshold_ratio=0.85,
                    circuit_breaker_limit=3,
                    session_memory_enabled=True,
                )
                from minicode.context_compactor import LLMSummaryGenerator

                context_compactor = ContextCompactor(
                    context_window=context_manager.context_window,
                    workspace=cwd,
                    memory_manager=(
                        getattr(orch, "memory_pipeline", None)
                        if orch is not None
                        else None
                    ) or memory_mgr,
                    estimate_fn=estimate_message_tokens,
                    config=compact_config,
                    # Model-generated summaries when compacting, with the
                    # heuristic inventory as the always-available floor.
                    summary_generator=(
                        LLMSummaryGenerator(
                            model,
                            agent_budget=agent_budget,
                            event_sink=event_sink,
                            cancellation_token=cancellation_token,
                            deadline_monotonic=deadline_monotonic,
                        )
                    if model is not None
                    else None
                    ),
                )
                context_cybernetics = ContextCyberneticsOrchestrator(
                    context_compactor,
                    kp=2.0, ki=0.15, kd=0.3,
                    pid_setpoint=0.70,
                    base_threshold=0.85,
                    safety_margin_turns=3,
                    enabled=True,
                )
                context_manager._context_compactor = context_compactor
                context_manager._context_cybernetics = context_cybernetics
                context_manager._controller_model = model_key
                logger.info(
                    "ContextCybernetics initialized: PID control loop + "
                    "predictive guard"
                )
            if task and hasattr(task, 'parsed_intent') and task.parsed_intent:
                context_cybernetics.set_intent(task.parsed_intent.intent_type.value)
            if orch:
                orch.context_compactor = context_compactor
                orch.context_cybernetics = context_cybernetics

        # 初始化自愈引擎（接收 cybernetics 引用用于 CONTEXT_OVERFLOW 委托）
        if orch:
            orch.wire_healing(tool_scheduler, context_compactor)
            self_healing_engine = orch.healing
        else:
            self_healing_engine = SelfHealingEngine(
                orchestrator=context_cybernetics,
                tool_scheduler=tool_scheduler,
                compactor=context_compactor,
            )
        logger.info("Self-healing engine initialized: automated recovery + compaction delegation")

        # 初始化成本控制闭环 (CostTracker → PID → ToolResultBudgetManager)
        cost_control = orch.cost_control if orch else None
        if cost_control is None:
            cost_control = CostControlLoop(
                target_cost_per_min=0.50,
                kp=1.5, ki=0.08, kd=0.2,
                enabled=True,
            )
        if orch:
            orch.cost_control = cost_control
        logger.info("CostControlLoop initialized: BudgetPIDController for cost regulation")

    # 检查上下文状态 + 运行 Claude Code-style 预请求优化管线
    if context_manager:
        context_manager.messages = current_messages
        stats = context_manager.get_stats()
        logger.info("Context: %d tokens (%.0f%%), %d messages",
                   stats.total_tokens, stats.usage_percentage, stats.messages_count)

        # 运行控制论闭环优化管线 (Sense → Predict → Control → Act → Learn)
        if context_cybernetics:
            if cost_control:
                est_cost = stats.total_tokens * 0.000015
                adj = cost_control.run(
                    cost_usd=est_cost,
                    total_tokens=stats.total_tokens,
                    total_calls=max(step, 1),
                )
                if context_compactor and hasattr(context_compactor, '_tool_budget') and context_compactor._tool_budget:
                    cost_control.apply_to_budget_manager(context_compactor._tool_budget)
                elif adj and adj.budget_multiplier < 0.8:
                    logger.warning(
                        "CostControl: budget tightened (mult=%.2f reason=%s) but no compactor active",
                        adj.budget_multiplier, adj.reason,
                    )

            context_operation_id = _new_context_operation_observation(
                event_sink
            )
            context_messages_before = (
                observable_message_count(current_messages)
                if context_operation_id is not None
                else None
            )
            try:
                cyber_messages, cyber_result, cyber_action = context_cybernetics.run_cycle(
                    current_messages,
                    error_rate=float(tool_error_count) / max(step, 1) if step > 0 else 0.0,
                    avg_latency=last_step_duration_seconds,
                    turn_id=step,
                )
            except ModelCallDeadlineExceeded as exc:
                raise AgentTurnDeadlineExceeded(str(exc)) from exc
            if cyber_result and cyber_result.effective:
                original_messages = list(current_messages)
                compacted_messages = reconcile_task_ledger(cyber_messages)
                installed, _memory_ids = _commit_materialized_compaction_memory(
                    cyber_result,
                    getattr(orch, "memory_pipeline", None) if orch else None,
                    lambda: current_messages.__setitem__(
                        slice(None),
                        compacted_messages,
                    ),
                    lambda: current_messages.__setitem__(
                        slice(None),
                        original_messages,
                    ),
                    event_sink=event_sink,
                    context_operation_id=context_operation_id,
                )
                if not installed:
                    fallback_messages = (
                        context_compactor.rejection_fallback_messages(
                            cyber_result,
                            original_messages,
                        )
                    )
                    fallback_messages = reconcile_task_ledger(
                        fallback_messages
                    )
                    cyber_result.memory_free_fallback = fallback_messages
                    current_messages[:] = fallback_messages
                _finalize_compaction_materialization_state(
                    cyber_result,
                    context_compactor,
                    installed=installed,
                    original_messages=original_messages,
                    context_cybernetics=context_cybernetics,
                )
                if installed:
                    context_manager.messages = current_messages
                    if (
                        context_operation_id is not None
                        and context_messages_before is not None
                    ):
                        emit_context_compaction_safely(
                            event_sink,
                            cyber_result,
                            context_operation_id=context_operation_id,
                            path="pre_request_cybernetic",
                            messages_before=context_messages_before,
                            messages_after=observable_message_count(
                                current_messages
                            ),
                        )
                    logger.info(
                        "Cybernetics[%s]: %s intensity=%.2f freed=%d tokens [%s]",
                        cyber_action.reason if cyber_action else "unknown",
                        cyber_result.strategy.value,
                        (
                            cyber_action.compaction_intensity
                            if cyber_action
                            else 0
                        ),
                        cyber_result.tokens_freed,
                        (
                            cyber_result.summary_text[:80]
                            if cyber_result.summary_text
                            else ""
                        ),
                    )
                else:
                    context_manager.messages = current_messages
                    cyber_result.success = False
                    cyber_result.error = (
                        "Memory authority changed before compaction install"
                    )
                    _emit_failed_context_compaction(
                        event_sink,
                        cyber_result,
                        path="pre_request_cybernetic",
                        context_compactor=context_compactor,
                        context_operation_id=context_operation_id,
                    )
            elif cyber_result is not None:
                _emit_failed_context_compaction(
                    event_sink,
                    cyber_result,
                    context_compactor,
                    path="pre_request_cybernetic",
                    context_operation_id=context_operation_id,
                )
        elif context_compactor:
            context_operation_id = _new_context_operation_observation(
                event_sink
            )
            context_messages_before = (
                observable_message_count(current_messages)
                if context_operation_id is not None
                else None
            )
            try:
                compaction_result = context_compactor.process_request(
                    current_messages
                )
            except ModelCallDeadlineExceeded as exc:
                raise AgentTurnDeadlineExceeded(str(exc)) from exc
            if compaction_result.effective:
                original_messages = list(current_messages)
                compacted_messages = reconcile_task_ledger(
                    compaction_result.messages
                )
                installed, _memory_ids = _commit_materialized_compaction_memory(
                    compaction_result,
                    getattr(orch, "memory_pipeline", None) if orch else None,
                    lambda: current_messages.__setitem__(
                        slice(None),
                        compacted_messages,
                    ),
                    lambda: current_messages.__setitem__(
                        slice(None),
                        original_messages,
                    ),
                    event_sink=event_sink,
                    context_operation_id=context_operation_id,
                )
                if not installed:
                    fallback_messages = (
                        context_compactor.rejection_fallback_messages(
                            compaction_result,
                            original_messages,
                        )
                    )
                    fallback_messages = reconcile_task_ledger(
                        fallback_messages
                    )
                    compaction_result.memory_free_fallback = fallback_messages
                    current_messages[:] = fallback_messages
                _finalize_compaction_materialization_state(
                    compaction_result,
                    context_compactor,
                    installed=installed,
                    original_messages=original_messages,
                )
                if installed:
                    context_manager.messages = current_messages
                    if (
                        context_operation_id is not None
                        and context_messages_before is not None
                    ):
                        emit_context_compaction_safely(
                            event_sink,
                            compaction_result,
                            context_operation_id=context_operation_id,
                            path="pre_request_compactor",
                            messages_before=context_messages_before,
                            messages_after=observable_message_count(
                                current_messages
                            ),
                        )
                    logger.info(
                        "ContextCompactor: %s freed %d tokens [%s]",
                        compaction_result.strategy.value,
                        compaction_result.tokens_freed,
                        compaction_result.summary_text[:80],
                    )
                else:
                    context_manager.messages = current_messages
                    compaction_result.success = False
                    compaction_result.error = (
                        "Memory authority changed before compaction install"
                    )
                    _emit_failed_context_compaction(
                        event_sink,
                        compaction_result,
                        path="pre_request_compactor",
                        context_compactor=context_compactor,
                        context_operation_id=context_operation_id,
                    )
            else:
                _emit_failed_context_compaction(
                    event_sink,
                    context_compactor.last_result,
                    context_compactor,
                    path="pre_request_compactor",
                    context_operation_id=context_operation_id,
                )

    owns_subagent_lifecycle = subagent_lifecycle is None
    if subagent_lifecycle is None:
        subagent_lifecycle = AsyncSubagentLifecycle()
    authority_delivery_ready = False
    committed_child_batches: list[tuple[ChatMessage, ...]] = []
    workflow_delivery = TurnWorkflowDelivery()

    def observe_workflow_delivery(call, result, *, suppressed: bool, acknowledged: bool) -> None:
        definition = tools.find(call.get("toolName", ""))
        workflow_delivery.observe(call, result, suppressed=suppressed, acknowledged=acknowledged,
                                  read_only=bool(definition and definition.is_read_only))

    def committed_delivery_remaining() -> list[str]:
        remaining = list(workflow_delivery.remaining)
        if any(item.get("status") != "completed" for item in todo_state.get("tasks", [])):
            remaining.append("The parent task list contains unfinished work.")
        barrier = getattr(subagent_lifecycle, "finalization_barrier", None)
        if callable(barrier):
            try:
                try:
                    state = barrier(wait_seconds=0)
                except TypeError:
                    state = barrier()
                if not state.get("ready"):
                    remaining.append("Owned sub-agent results are pending or have not been acknowledged.")
            except Exception:
                remaining.append("Owned sub-agent completion could not be verified.")
        return list(dict.fromkeys(remaining))

    def finish_workflow_delivery(reason: str, *, interruption: bool = False) -> bool:
        nonlocal turn_outcome, authority_delivery_ready
        if not workflow_delivery.receipts:
            return False
        remaining = committed_delivery_remaining()
        if interruption:
            remaining.append(reason)
        content, delivery_metadata = workflow_delivery_summary(workflow_delivery.receipts, remaining=remaining, reason=reason)
        current_messages.append({"role": "assistant", "content": content,
                                 "_partial": bool(remaining), "_workflow_delivery_receipt_summary": True,
                                 "_workflow_delivery": delivery_metadata})
        # Scoped delivery does not establish coverage of an arbitrary user goal.
        turn_outcome = "unknown"
        authority_delivery_ready = True
        if on_assistant_message:
            try:
                on_assistant_message(content)
            except BaseException:
                logger.warning("Failed to present workflow delivery receipt", exc_info=True)
        emit_event_safely(event_sink, "execution.completion_limited", step=step,
                          payload={"reason": reason, "partial": bool(remaining), "modelSummary": False,
                                   "delivery": "partial" if remaining else "delivered", "overallGoal": "not_assessed"})
        return True

    def restore_committed_child_receipts() -> None:
        """Keep exact receipts deliverable even if continuation compacted them."""
        for batch in committed_child_batches:
            if all(message in current_messages for message in batch):
                continue
            call_ids = {message.get("toolUseId") for message in batch}
            current_messages[:] = [
                message for message in current_messages
                if message.get("toolUseId") not in call_ids
            ]
            insert_at = len(current_messages)
            if current_messages and current_messages[-1].get("role") == "assistant":
                insert_at -= 1
            current_messages[insert_at:insert_at] = copy.deepcopy(list(batch))

    def interrupted_parent_message(reason: str) -> None:
        restore_committed_child_receipts()
        if finish_workflow_delivery(reason, interruption=True):
            return
        content = (
            "Earlier workflow changes are already committed; remaining work "
            f"is incomplete because {reason}. The committed receipts are retained above."
        )
        current_messages.append({"role": "assistant", "content": content})
        if on_assistant_message:
            try:
                on_assistant_message(content)
            except BaseException:  # committed receipts must remain deliverable
                logger.warning("Failed to present interrupted parent status", exc_info=True)

    def _revalidate_model_visible_memory() -> None:
        """Apply the latest Memory authority immediately before a model call."""
        pipeline = (
            getattr(orch, "memory_pipeline", None)
            if orch is not None
            else None
        )
        revalidate = getattr(pipeline, "revalidate_prompt_messages", None)
        if not callable(revalidate):
            return
        sanitized = revalidate(current_messages)
        if sanitized == current_messages:
            return
        current_messages[:] = sanitized
        if context_manager is not None:
            context_manager.messages = list(current_messages)

    completion_policy = (
        TurnCompletionPolicy(
            hard_limit=caller_max_steps, soft_limit=max_steps, cwd=cwd
        )
        if enable_bounded_completion
        and agent_depth == 0
        and not max_steps_soft_landing
        and max_steps is not None
        else None
    )
    if completion_policy is not None:
        max_steps = completion_policy.work_limit
    completion_hard_limit = caller_max_steps
    completion_notice_limit = None
    # Reuse byte-based observation even in headless/child loops. A successful
    # read, a todo update or a no-op edit must not count as changed output.
    mutation_observer = completion_policy or (
        TurnCompletionPolicy(hard_limit=max_steps or 50, soft_limit=max_steps or 50, cwd=cwd)
        if coding_progress.enabled else None
    )
    owned_implementation_feedback = None
    direct_implementation_checks_failed = False
    implementation_feedback = getattr(tools, "_implementation_feedback", None)
    if implementation_spec is not None and {"edit_file", "write_file"}.intersection(tools.list_all()):
        from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
        from minicode.implementation_context import build_implementation_context

        if not isinstance(implementation_spec, ImplementationSpec):
            raise ValueError("implementation_spec must be a host-bound ImplementationSpec")
        if implementation_feedback is None and {"edit_file", "write_file"}.intersection(tools.list_all()):
            owned_implementation_feedback = direct_verification_capability(implementation_spec)
            implementation_feedback = owned_implementation_feedback
            tools = implementation_feedback.bind_registry(tools)
        # A continuing Executor already owns its source packet and subsequent
        # observations. Re-seeding would duplicate the whole investigation and
        # consume the repair budget before it can act on the failed checks.
        packet = (build_implementation_context(cwd, implementation_spec.write_paths,
            implementation_spec.checks, implementation_spec.objective, permissions)
            if implementation_context_seed else None)
        if packet:
            current_messages.append({"role": "user", "content": packet, "_implementation_context": True})
            if context_manager is not None:
                context_manager.messages = list(current_messages)
    from minicode.executor_verification import ExecutorCheckCapability
    from minicode.executor_result_submission import ExecutorResultChannel

    has_checked_source_completion = (
        isinstance(implementation_feedback, ExecutorCheckCapability)
        and implementation_feedback.has_automatic_write_checks
    )
    has_structured_handoff = any(tool.name == "executor_submit" for tool in tools.list())
    direct_completion_review = restored_completion_review
    direct_migration_state = (implementation_continuation.migration_state
        if implementation_continuation is not None else None)
    direct_completion_accepted = False
    direct_completion_owner = (implementation_continuation.executor_id
        if implementation_continuation is not None else "implementation_" + uuid.uuid4().hex)
    direct_completion_attempt = (implementation_continuation.attempt
        if implementation_continuation is not None else 0)
    implementation_observation_context = ToolContext(cwd=cwd, _subagent_mailbox=subagent_mailbox)

    def observe_implementation_tool_result(call, result):
        if (isinstance(implementation_feedback, ExecutorCheckCapability)
                and (implementation_feedback.supports_conditional_submit
                     or owned_implementation_feedback is not None)):
            # Registry validation, permission checks and phase suppression can
            # fail before the wrapped writer runs. Observe that real failure
            # before a later submit in this response can accept old evidence.
            implementation_feedback.observe_tool_result(
                call.get("toolName"), call.get("input"), result,
                context=implementation_observation_context,
            )

    def submission_correction_progress(call, result):
        """Recognize live protocol feedback, without changing a tool's failure.

        A different rejected declaration can be corrected in this session.
        Identical/cycling declarations still enter the ordinary no-progress
        circuit. Permission errors, closed channels and arbitrary tool names
        never acquire this treatment from their displayed error text.
        """
        from minicode.reviewer_result_submission import reviewer_submission_rejected
        from minicode.executor_result_submission import executor_submit_channel

        if result.ok or result._termination_reason is not None:
            return False
        recognized = (host_native_result_tool is not None
            and call.get("toolName") == host_native_result_tool.name
            and reviewer_submission_rejected(host_native_result_tool, result))
        if not recognized and call.get("toolName") == "executor_submit":
            submit = next((tool for tool in tools.list() if tool.name == "executor_submit"), None)
            channel = executor_submit_channel(submit) if submit is not None else None
            if (channel is None and isinstance(implementation_feedback, ExecutorCheckCapability)
                    and implementation_feedback.supports_conditional_submit):
                # bind_registry wraps the registered native tool to attach the
                # current-candidate guard; the capability retains its owner.
                channel = implementation_feedback._submission_channel
            if (isinstance(channel, ExecutorResultChannel) and channel is subagent_mailbox
                    and channel.validation_errors and not channel._written):
                with channel._store._lock:
                    live = channel._store._assignment_writes.get(channel._write_authority) == channel.write_key
                diagnostic = channel.validation_errors[-1]
                recognized = live and result.output in {
                    f"Input validation error in executor_submit: {diagnostic}",
                    f"error[executor_submit_invalid]: {diagnostic}",
                }
        if not recognized:
            return False
        fingerprint = hashlib.sha256(json.dumps(
            [call.get("toolName"), call.get("input"), result.output],
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()
        progress = fingerprint not in seen_submission_rejections
        seen_submission_rejections.add(fingerprint)
        emit_event_safely(event_sink, "execution.submission_correction", step=step, payload={
            "tool": call.get("toolName"), "newRejectedDeclaration": progress,
        })
        return progress
    implementation_phases = (
        ImplementationPhaseController(
            bounded=bounded_implementation_phases,
            snapshot=agent_budget.snapshot() if agent_budget is not None else None,
            max_steps=completion_hard_limit - 1 if completion_policy is not None else max_steps,
            now=time.monotonic(), deadline=deadline_monotonic,
            final_response_calls=int(completion_policy is not None),
            allow_test_completion=any(tool.name == "executor_test" for tool in tools.list()),
            allow_checked_source_completion=has_checked_source_completion,
            allow_conditional_handoff=(
                isinstance(implementation_feedback, ExecutorCheckCapability)
                and implementation_feedback.supports_conditional_submit
            ),
            require_structured_handoff=has_structured_handoff,
            analysis_complete=implementation_analysis_complete is True,
            output_limit=(runtime or {}).get("maxOutputTokens", 8192),
            request_timeout_seconds=((runtime or {}).get("modelTimeoutSeconds")
                                     or os.environ.get("MINICODE_MODEL_TIMEOUT")),
        )
        if enable_implementation_phases and coding_progress.enabled
        and {"edit_file", "write_file"}.intersection(tools.list_all())
        else None
    )
    if (bounded_implementation_phases and isinstance(implementation_feedback, ExecutorCheckCapability)
            and deadline_monotonic is not None):
        # Model generation and feedback share one committed handoff boundary.
        # The controller updates it once when finalization actually begins;
        # repeated failed checks cannot move it closer to the hard deadline.
        feedback_deadline = (implementation_phases.handoff_start
                             if implementation_phases is not None else
                             deadline_monotonic - min(45.0,
                                 max(0.0, deadline_monotonic - time.monotonic()) * 0.1))
        implementation_feedback.reserve_handoff_time(feedback_deadline)
    implementation_candidate_guard = None
    if implementation_phases is not None and callable(getattr(permissions, "ensure_path_access", None)):
        from minicode.implementation_candidate import ImplementationCandidateGuard
        implementation_candidate_guard = ImplementationCandidateGuard(cwd, permissions=permissions)
    phase_observed_input = 0
    previous_model_input_estimate = 0
    from minicode.implementation_recovery import (
        InputTokenCalibration, project_implementation_progress_messages,
        project_recovery_messages,
    )

    if implementation_request_state is not None:
        from minicode.executor_session import ExecutorRequestViewState

        if not isinstance(implementation_request_state, ExecutorRequestViewState):
            raise ValueError("implementation request state requires the host Executor session")
        implementation_request_state.bind_observer(model=model, observer=mutation_observer)
    repair_input_calibration = (implementation_request_state.calibration
                                if implementation_request_state is not None else InputTokenCalibration())
    recovery_view_active = (implementation_request_state.recovery_view_active
                            if implementation_request_state is not None else False)
    progress_view_announced = False

    def progress_view_paths():
        if implementation_phases is None or mutation_observer is None:
            return frozenset()
        return (implementation_request_state.changed_paths if implementation_request_state is not None
                else mutation_observer.observed_changed_paths)

    def implementation_request_messages():
        if recovery_view_active:
            return project_recovery_messages(current_messages, changed_paths=progress_view_paths())
        paths = progress_view_paths()
        return (project_implementation_progress_messages(current_messages, changed_paths=paths)
                if paths else current_messages)

    def implementation_view_active():
        return recovery_view_active or bool(progress_view_paths())

    from minicode.context_compactor import ToolResultBudgetManager

    read_history_budget = (
        context_compactor.tool_budget if context_compactor is not None
        else ToolResultBudgetManager(workspace=cwd)
    )

    def configured_output_limit() -> int:
        # The adapter owns the actual request cap; caller runtime is fallback.
        for configured in (getattr(model, "runtime", None), runtime):
            if isinstance(configured, dict):
                value = configured.get("maxOutputTokens")
                if type(value) is int and value > 0:
                    return value
        return 8192

    def completion_input_estimate() -> int:
        if implementation_view_active():
            return repair_input_calibration.estimate(
                _estimated_context_tokens(implementation_request_messages()))
        estimated = _estimated_context_tokens(current_messages)
        return max(estimated, phase_observed_input + max(
            0, estimated - previous_model_input_estimate))

    def _attempt_max_steps_soft_landing(
        *,
        prompt: str = MAX_STEPS_SOFT_LANDING_PROMPT,
        closing_step_index: int | None = None,
        content_prefix: str = "",
    ) -> bool:
        """Spend one tool-free closing turn on a partial-summary hand-back.

        Used when a bounded nested run (a task sub-agent) exhausts its step
        limit mid-survey: instead of failing hard and discarding the work,
        the model gets a single closing turn whose tool calls are declined
        (never executed). Returns True only when usable final text landed;
        the caller then marks the run partial. Cancellation and deadline
        expiry still propagate — they are the parent Turn's control flow.
        Budget/provider/empty-content failures return False and the caller
        keeps the original hard-failure semantics.
        """
        current_messages.append(
            {"role": "user", "content": prompt}
        )
        if context_checkpoint_sink is not None:
            context_checkpoint_sink(current_messages)
        if closing_step_index is None:
            closing_step_index = step + 1
        closing_operation_id = (
            new_model_operation_id() if event_sink is not None else ""
        )
        closing_started_at = (
            _start_model_observation_clock() if event_sink is not None else None
        )
        closing_reservation = None
        closing_model_started = False
        closing_output_limit = 2048
        for configured in (runtime, getattr(model, "runtime", None)):
            if isinstance(configured, dict):
                limit = configured.get("maxOutputTokens")
                if type(limit) is int and limit > 0:
                    closing_output_limit = min(closing_output_limit, limit)

        def closing_failed(kind: str, *, charge_estimate: bool = False) -> None:
            _fail_agent_budget_reservation(
                agent_budget, closing_reservation, charge_estimate=charge_estimate
            )
            if event_sink is not None and closing_model_started:
                emit_event_safely(
                    event_sink,
                    "model.failed",
                    step=closing_step_index,
                    payload=_model_event_duration(
                        {"operationId": closing_operation_id, "failureKind": kind},
                        _finish_model_observation_clock(closing_started_at),
                    ),
                )

        try:
            raise_if_cancelled(cancellation_token)
            raise_if_deadline_exceeded()
            _revalidate_model_visible_memory()
            closing_messages = implementation_request_messages()
            closing_options = ({
                "disable_tools": True,
                "max_output_tokens_override": closing_output_limit,
                "max_retries_override": 0,
                **_phase_reasoning_options(model),
            } if implementation_phases is not None or completion_policy is not None
               else {"tools_override": tools})
            if agent_budget is not None:
                closing_input = (completion_input_estimate() if completion_policy is not None
                    else (repair_input_calibration.estimate(_estimated_context_tokens(closing_messages))
                          if implementation_view_active() else _estimated_context_tokens(closing_messages)))
                closing_input = estimate_budgeted_request_input(model, closing_messages,
                    agent_budget=agent_budget, estimated_input=closing_input,
                    optional_kwargs=closing_options, default_tools=tools)
                closing_reservation = agent_budget.reserve_model_call(
                    closing_input,
                    **({"preserve_total_tokens": closing_output_limit}
                       if completion_policy is not None else {}),
                )
            if event_sink is not None:
                emit_event_safely(
                    event_sink,
                    "model.started",
                    step=closing_step_index,
                    payload={"operationId": closing_operation_id},
                )
            closing_model_started = True
            closing_step = _model_next(
                model,
                closing_messages,
                on_stream_chunk=None,
                store=store,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
                global_deadline_monotonic=deadline_monotonic,
                request_options=closing_options,
            )
            raise_if_cancelled(cancellation_token)
            raise_if_deadline_exceeded()
        except TurnCancellationRequested:
            closing_failed("cancelled")
            raise
        except AgentTurnDeadlineExceeded:
            closing_failed("timeout", charge_estimate=closing_model_started)
            raise
        except (KeyboardInterrupt, SystemExit):
            closing_failed("interrupted")
            raise
        except Exception as exc:  # noqa: BLE001 - degrade to the hard failure
            closing_failed(
                "timeout" if isinstance(exc, TimeoutError)
                else "network" if isinstance(exc, ConnectionError)
                else "provider_error",
                charge_estimate=isinstance(exc, TimeoutError) and closing_model_started,
            )
            logger.warning("Max-steps soft landing failed: %s", type(exc).__name__)
            return False
        closing_usage = _model_usage_observation(closing_step)
        _record_agent_budget_usage(
            agent_budget,
            closing_step,
            reservation=closing_reservation,
            model=model,
            usage_observation=closing_usage,
            cost_payload=None,
        )
        if context_compactor is not None and closing_usage:
            closing_reported = closing_usage.get("inputTokens")
            if isinstance(closing_reported, int) and closing_reported > 0:
                try:
                    context_compactor.record_usage_observation(
                        closing_reported,
                        _estimated_context_tokens(closing_messages),
                    )
                except Exception:  # noqa: BLE001 - calibration is advisory
                    pass
        if event_sink is not None:
            emit_event_safely(
                event_sink,
                "model.completed",
                step=closing_step_index,
                payload=_model_event_duration(
                    {
                        "operationId": closing_operation_id,
                        "resultType": "assistant",
                        "contentPresent": bool(closing_step.content),
                        # Declined by construction: soft-landing tool calls
                        # are discarded without executing.
                        "toolCallCount": 0,
                        "usage": closing_usage,
                    },
                    _finish_model_observation_clock(closing_started_at),
                ),
            )
        content = str(getattr(closing_step, "content", "") or "").strip()
        if not content:
            return False
        content = content_prefix + content
        if on_assistant_message:
            on_assistant_message(content)
        current_messages.append(
            {
                "role": "assistant",
                "content": content,
                # Typed marker for the dispatch layer: this final answer is a
                # partial summary produced after the step limit fired.
                "_partial": True,
            }
        )
        _append_trace_event(execution_trace, {
            "type": "assistant_step",
            "step": closing_step_index,
            "content_kind": "soft_landing",
            "content": _redact_trace_text(content),
        })
        return True

    def finish_bounded_completion(
        reason: str,
        closing_step_index: int,
        *,
        allow_model_summary: bool = True,
    ) -> None:
        nonlocal turn_outcome, authority_delivery_ready
        assert completion_policy is not None
        raise_if_cancelled(cancellation_token)
        raise_if_deadline_exceeded()
        if finish_workflow_delivery(reason):
            return
        landed = (
            allow_model_summary
            and closing_step_index <= completion_hard_limit
            and _attempt_max_steps_soft_landing(
                prompt=completion_policy.closing_prompt(reason),
                closing_step_index=closing_step_index,
                content_prefix=(
                    f"Partial result: {reason}. "
                    "Overall task completion is not confirmed.\n\n"
                ),
            )
        )
        if not landed:
            raise_if_cancelled(cancellation_token)
            raise_if_deadline_exceeded()
            content = completion_policy.summary(reason)
            current_messages.append(
                {"role": "assistant", "content": content, "_partial": True}
            )
            if on_assistant_message:
                on_assistant_message(content)
        # A readable hand-back is not evidence that the user's goal was met.
        turn_outcome = "unknown"
        authority_delivery_ready = True
        emit_event_safely(
            event_sink,
            "execution.completion_limited",
            step=step,
            payload={"reason": reason, "partial": True, "modelSummary": landed},
        )

    write_feedback_batch = None
    write_feedback_group = None
    batch_feedback_messages = []
    batch_feedback_by_call = {}
    direct_implementation_batch_status = None

    def observe_batch_feedback(document):
        nonlocal direct_implementation_checks_failed, direct_implementation_batch_status
        # Inconclusive feedback cannot preserve a previous candidate's pass.
        failed = document["status"] != "passed"
        if owned_implementation_feedback is not None:
            direct_implementation_checks_failed = failed
            direct_implementation_batch_status = document["status"]
        if implementation_phases is not None:
            implementation_phases.observe_verification(failed=failed)

    def flush_write_feedback_batch():
        nonlocal write_feedback_batch, write_feedback_group
        batch = write_feedback_batch
        write_feedback_batch = None
        write_feedback_group = None
        if batch is None:
            return
        document, control_error = batch.finish()
        if document is not None:
            observe_batch_feedback(document)
            batch_feedback_by_call[batch.call_id] = document
            batch_feedback_messages.append({
                "role": "user", "_implementation_batch_feedback": True,
                "content": "=== IMPLEMENTATION BATCH CHECK FEEDBACK ===\n"
                    + json.dumps(document, ensure_ascii=False, sort_keys=True),
            })
        # Cleanup must never replace the original tool/cancellation exception.
        if control_error is not None and sys.exception() is None:
            raise control_error

    def deliver_batch_feedback():
        current_messages.extend(batch_feedback_messages)
        batch_feedback_messages.clear()

    def supersede_completion_feedback():
        for index, message in enumerate(current_messages):
            if message.get("_implementation_completion_feedback"):
                historical = dict(message)
                historical.pop("_implementation_completion_feedback", None)
                historical["_superseded_completion_feedback"] = True
                current_messages[index] = historical

    def enforce_implementation_completion_before_final(content, *, resuming_review=False):
        """Keep the implementation owner alive until current evidence admits final.

        This is a state transition in this loop, not another implementation task.
        The rejected final is never presented as a completed user delivery.
        """
        nonlocal direct_completion_review, direct_completion_accepted, direct_completion_attempt
        nonlocal implementation_completion_feedback, direct_implementation_checks_failed
        nonlocal direct_implementation_batch_status, direct_migration_state
        if owned_implementation_feedback is None or has_structured_handoff:
            return "ok"
        from minicode.implementation_completion_review import ImplementationCompletionReview
        from minicode.implementation_execution import implementation_candidate_digest
        from minicode.verifier_sandbox import (
            resolve_verifier_host_capabilities, user_registered_trusted_executables,
        )

        flush_write_feedback_batch()
        deliver_batch_feedback()
        raise_if_cancelled(cancellation_token)
        raise_if_deadline_exceeded()
        backend, executables = resolve_verifier_host_capabilities(runtime)
        context = ToolContext(cwd=cwd, permissions=permissions, _runtime=runtime,
            _event_sink=event_sink, _step=step, _agent_budget=agent_budget,
            _cancellation_token=cancellation_token, _task_deadline_monotonic=deadline_monotonic,
            _subagent_mailbox=subagent_mailbox, _subagent_lifecycle=subagent_lifecycle,
            _agent_depth=agent_depth, _tool_abandoned=abandoned_event, _presentation=presentation,
            _verifier_sandbox_backend=backend, _verifier_trusted_executables=executables,
            _verifier_explicit_trusted_executables=user_registered_trusted_executables() or None)
        prior_completion_attempt = direct_completion_attempt
        direct_completion_attempt += 1
        checks = owned_implementation_feedback.ensure_completion_checks(context)
        if (direct_migration_state is not None
                and direct_migration_state["state"]["candidateDigest"] != checks.get("patchDigest")):
            emit_event_safely(event_sink, "implementation.migration_checkpoint_stale", step=step,
                payload={"executorId": direct_completion_owner, "reason": "candidate_changed"})
            direct_migration_state = None
        direct_implementation_batch_status = checks["status"]
        direct_implementation_checks_failed = checks["status"] != "passed"
        if checks["status"] != "passed":
            decision = {"status": "changes_required", "reason": "public_checks_not_current_and_passed",
                        "patchDigest": checks.get("patchDigest"), "checks": checks}
            if checks.get("reason") == "completion_checks_unavailable":
                decision["canContinue"] = False
        else:
            if direct_completion_review is None:
                direct_completion_review = ImplementationCompletionReview(implementation_spec)
            if resuming_review and implementation_continuation is not None:
                from minicode.completion_repair_decision import delivered_repair_matches

                # A stored slot is not enough: checks, actual source anchors
                # and the current candidate must still validate before reuse.
                retained = direct_completion_review._accepted_repair_for_candidate(
                    checks.get("patchDigest"), context)
                if (retained is not None
                        and implementation_candidate_digest(implementation_spec, context) == checks.get("patchDigest")
                        and delivered_repair_matches(implementation_continuation.feedback, retained,
                            executor_id=direct_completion_owner, candidate_digest=checks.get("patchDigest"),
                            objective=implementation_spec.objective)):
                    # The authenticated checkpoint already delivered this exact
                    # decision. Keep its original place before later results;
                    # neither another review event nor a new attempt occurred.
                    direct_completion_attempt = prior_completion_attempt
                    return "retry"
            if on_progress_message:
                on_progress_message("Checking the requested behaviors and their evidence before delivery.")
            emit_event_safely(event_sink, "implementation.completion_review_started", step=step,
                payload={"executorId": direct_completion_owner, "attempt": direct_completion_attempt,
                         "patchDigest": checks.get("patchDigest")})
            decision = None
            pending_migration = direct_completion_review.state.get("pendingLegacyResolution")
            if (direct_completion_review.has_legacy_probes and not (
                    isinstance(pending_migration, dict)
                    and pending_migration.get("candidateDigest") == checks.get("patchDigest"))):
                from minicode.completion_probe_migration import CompletionProbeMigration

                def retain_migration_checkpoint(state):
                    nonlocal direct_migration_state
                    from copy import deepcopy
                    direct_migration_state = deepcopy(state)

                migration = CompletionProbeMigration(implementation_spec).assess(
                    legacy_state=direct_completion_review.state,
                    continuation_state=direct_migration_state, checkpoint_sink=retain_migration_checkpoint,
                    model=model, tools=tools, context=context, agent_budget=agent_budget,
                    event_sink=event_sink, cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic)
                if migration.get("status") == "resolved":
                    direct_completion_review.queue_legacy_resolution(migration["resolution"])
                    direct_migration_state = None
                else:
                    # An unchanged ambiguous classification cannot be repaired
                    # by another implementation rewrite or repeated final.
                    decision = {"status": "inconclusive", "reason": "legacy_probe_purpose_unresolved",
                        "patchDigest": checks.get("patchDigest"), "canContinue": False,
                        "migrationEvidence": migration}
            if decision is None:
                decision = direct_completion_review.assess(model=model, tools=tools, context=context,
                    agent_budget=agent_budget, event_sink=event_sink,
                    cancellation_token=cancellation_token, deadline_monotonic=deadline_monotonic,
                    checks_receipt=checks)
            raise_if_cancelled(cancellation_token)
            raise_if_deadline_exceeded()
            current_digest = implementation_candidate_digest(implementation_spec, context)
            if (not isinstance(decision, dict) or decision.get("patchDigest") != current_digest
                    or checks.get("patchDigest") != current_digest):
                decision = {"status": "inconclusive", "reason": "completion_candidate_changed",
                            "patchDigest": current_digest}
        unresolved_probe_failure = decision.get("reason") == "failure_adjudication_inconclusive"
        pending_coverage_audit = decision.get("reason") == "coverage_audit_interrupted"
        if unresolved_probe_failure:
            # An uncertain test does not authorize a product change. Preserve
            # the independent review for resumption instead of dispatching the
            # old assertion as a repair request to this owner.
            decision = {**decision, "canContinue": False}
        direct_completion_accepted = decision.get("status") == "approved"
        receipt = {"completionVersion": 1, "executorId": direct_completion_owner,
                   "attempt": direct_completion_attempt,
                   "continuesSameExecutor": not direct_completion_accepted and decision.get("canContinue") is not False,
                   **decision}
        emit_event_safely(event_sink, "implementation.completion_decided", step=step, payload=receipt)
        _append_trace_event(execution_trace, {"type": "implementation_completion", "step": step, **receipt})
        if direct_completion_accepted:
            # These are actual current-candidate verifier executions. Ordinary
            # model completion text never contributes verification observations.
            for check_receipt in checks["checks"]:
                verification_tracker.record({"outcome": check_receipt["status"]})
            implementation_completion_feedback = None
            if resuming_review:
                # A resumed review may have disproved the old repair request.
                # Keep its text as historical evidence, but replace its active
                # authority before exposing write tools to the implementer.
                supersede_completion_feedback()
                status_message = {"role": "system", "_implementation_completion_status": True,
                    "content": "Current host completion decision (supersedes prior repair feedback):\n"
                        + json.dumps(receipt, ensure_ascii=False, sort_keys=True)}
                current_messages.append(completion_evidence_views.project_message(status_message)
                    if completion_evidence_views is not None else status_message)
                if context_manager is not None:
                    context_manager.messages = list(current_messages)
            return "ok"
        if not resuming_review:
            current_messages.append({"role": "assistant_progress", "content": content,
                                     "_rejected_completion": True})
        else:
            supersede_completion_feedback()
        implementation_completion_feedback = {
            "role": "user", "_implementation_completion_feedback": True,
            "content": (("Host completion decision: the independent coverage audit is paused. "
                "Its original context, delivered evidence and unaccepted drafts are retained for the same owner. "
                "Resume that audit before deciding completion; no product repair is inferred.\n" if pending_coverage_audit else
                "Host completion decision: independent failure adjudication remains unresolved. "
                "Existing candidate, context and tool results are retained. No product repair is inferred "
                "from this unresolved evidence.\n" if unresolved_probe_failure else
                "Host completion decision: delivery is not accepted. Continue in this same "
                "implementation session with the existing candidate, source context and tool results. "
                "Repair the evidenced gaps, update regression tests and rerun checks; no work was reverted.\n")
                + "Original public request (unchanged):\n" + implementation_spec.objective
                + "\nCompletion evidence:\n" + json.dumps(receipt, ensure_ascii=False, sort_keys=True)),
        }
        if completion_evidence_views is not None:
            implementation_completion_feedback = completion_evidence_views.project_message(
                implementation_completion_feedback)
        # Only a new host decision creates a new feedback turn. Routine ledger
        # maintenance preserves this position as the same Executor continues.
        current_messages[:] = [message for message in current_messages
            if not message.get("_implementation_completion_feedback")]
        current_messages.append(implementation_completion_feedback)
        current_messages[:] = reconcile_task_ledger(current_messages)
        if context_manager is not None:
            context_manager.messages = list(current_messages)
        return "stop" if decision.get("canContinue") is False else "retry"

    try:
        if (implementation_continuation is not None and direct_completion_review is not None
                and (implementation_continuation.requires_revalidation
                     or direct_completion_review.has_accepted_repair_decision(
                         implementation_continuation.document["candidateDigest"])
                     or direct_completion_review.has_pending_coverage_audit(
                         implementation_continuation.document["candidateDigest"])
                     or direct_completion_review.needs_failure_adjudication(
                         implementation_continuation.document["candidateDigest"]))):
            # A historical failed probe may itself be wrong. Resolve that
            # unfinished host review before the same owner can act on its old
            # repair instruction; do not fabricate an implementer response.
            if enforce_implementation_completion_before_final(None, resuming_review=True) == "stop":
                turn_outcome = "unknown"
                return current_messages
        while True:
            raise_if_cancelled(cancellation_token)
            raise_if_deadline_exceeded()
            if abandoned_event is not None and abandoned_event.is_set():
                turn_outcome = "failed"
                tool_abandoned = True
                raise ToolExecutionAbandoned(
                    "tool execution was abandoned after timeout"
                )
            if implementation_phases is not None and implementation_phases.exhausted:
                if completion_policy is not None:
                    break
                current_messages.append({"role": "assistant", "_partial": True, "content":
                    "The executable handoff allowance ended. Unsubmitted or unverified work remains incomplete."})
                turn_outcome = "failed"
                return current_messages
            if native_handoff_attempted:
                # A valid native call already returned below. An invalid or
                # missing verdict cannot turn into a tool-free success reply.
                # Only an actual live, host-bound rejection rearms this below.
                break
            if max_steps is not None and step >= max_steps:
                extended_limit = (
                    completion_policy.extend(
                        step=step,
                        current_limit=max_steps,
                        healthy=(
                            last_step_tool_error_count == 0
                            and recovery_guard.consecutive_failed_steps == 0
                            and recovery_guard.stop_decision() is None
                        ),
                    )
                    if completion_policy is not None else max_steps
                )
                retry_snapshot = (agent_budget.snapshot() if native_handoff_rejected
                                  and agent_budget is not None else None)
                if (host_native_result_tool is not None and max_steps_soft_landing
                        and native_handoff_rejected
                        and retry_snapshot is not None
                        and retry_snapshot.limit_model_calls is not None):
                    # The soft step boundary does not discard a real rejected
                    # declaration. Its correction still needs a fresh atomic
                    # admission under the original physical-call/token cap.
                    native_handoff_only = True
                elif (host_native_result_tool is not None and max_steps_soft_landing
                        and step == max_steps):
                    # Use the existing nested-worker closing slot, inside its
                    # immutable physical-call cap, for the actual capability.
                    native_handoff_only = True
                elif extended_limit <= max_steps:
                    break
                if extended_limit > max_steps:
                    emit_event_safely(
                        event_sink,
                        "execution.steps_extended",
                        step=step,
                        payload={"previousLimit": max_steps, "newLimit": extended_limit},
                    )
                    max_steps = extended_limit
            if (
                completion_policy is not None
                and max_steps - step <= 2
                and completion_notice_limit != max_steps
            ):
                current_messages.append({
                    "role": "user",
                    "content": (
                        "The current execution window is nearly exhausted. "
                        "Finish essential verification and summarize completed work, "
                        "checks and remaining issues. Avoid repeating checks or "
                        "starting unrelated work. Further steps require new progress "
                        "and remain bounded by the caller's hard limit."
                    ),
                })
                completion_notice_limit = max_steps
            step += 1
            step_started_at = time.perf_counter()

            # Hook: agent turn started
            fire_hook_sync(HookEvent.AGENT_START, step=step, cwd=cwd)

            # 高级控制论闭环（每个 step 开始时执行）
            if enable_work_chain and orch:
                predictive_action = orch.step_start(
                    context_manager=context_manager,
                    step=step,
                    tool_error_count=tool_error_count,
                    saw_tool_result=saw_tool_result,
                    response_time_seconds=last_step_duration_seconds,
                    recent_tool_error_count=last_step_tool_error_count,
                    recent_tool_call_count=last_step_tool_call_count,
                )
                if predictive_action is not None:
                    try:
                        recommended_action = getattr(
                            predictive_action,
                            "recommended_action",
                            "",
                        )
                        if (
                            recommended_action == "trigger_compaction"
                            and context_compactor is not None
                        ):
                            compacted = context_compactor.compact_messages(
                                current_messages,
                                force=True,
                            )
                            if (
                                compacted is not None
                                and len(compacted) < len(current_messages)
                            ):
                                previous_count = len(current_messages)
                                current_messages[:] = reconcile_task_ledger(
                                    compacted
                                )
                                if context_manager is not None:
                                    context_manager.messages = list(
                                        current_messages
                                    )
                                logger.info(
                                    "Predictive: compaction applied %d -> %d messages",
                                    previous_count,
                                    len(compacted),
                                )
                        elif recommended_action == "enable_safe_mode":
                            logger.info(
                                "Predictive: safe_mode recommended "
                                "(reduce concurrency, extend timeouts)"
                            )
                        elif recommended_action == "reduce_concurrency":
                            logger.info(
                                "Predictive: reduce_concurrency recommended"
                            )
                    except TurnCancellationRequested:
                        raise
                    except ModelCallDeadlineExceeded as exc:
                        raise AgentTurnDeadlineExceeded(str(exc)) from exc
                    except Exception as exc:
                        logger.warning(
                            "Predictive action %s failed: %s",
                            getattr(
                                predictive_action,
                                "recommended_action",
                                "unknown",
                            ),
                            exc,
                        )
            elif enable_work_chain:
                # 状态观测：通过可测量输出估计系统内部状态
                if state_observer:
                    measurement = MeasurementVector(
                        timestamp=time.time(),
                        response_time=last_step_duration_seconds,
                        success_rate=max(
                            0.0,
                            min(
                                1.0,
                                1.0
                                - last_step_tool_error_count
                                / max(last_step_tool_call_count, 1),
                            ),
                        ),
                        context_length=context_manager.get_stats().total_tokens if context_manager else 0,
                        error_count=last_step_tool_error_count,
                        retry_count=last_step_tool_error_count,
                        tool_calls=last_step_tool_call_count,
                    )
                    observed_state = state_observer.update(measurement)

                    # 将 Kalman 估计值输入到控制器
                    if observed_state.confidence > 0.4:
                        if observed_state.internal_load > 0.8:
                            logger.info(
                                "StateObserver: high internal_load=%.2f, reduce concurrency",
                                observed_state.internal_load,
                            )
                        if observed_state.hidden_errors > 0.5 and self_healing_engine:
                            self_healing_engine.detect_and_heal({
                                "error_rate": observed_state.hidden_errors * 5.0,
                                "context_usage": observed_state.context_pressure,
                            })
                        if observed_state.system_degradation > 0.4:
                            logger.warning(
                                "StateObserver: system degradation=%.2f confidence=%.2f",
                                observed_state.system_degradation,
                                observed_state.confidence,
                            )

                # 预测控制：预测未来趋势并提前调整
                if predictive_controller:
                    if context_manager:
                        stats = context_manager.get_stats()
                        predictive_controller.update("context_usage", stats.usage_percentage / 100.0)
                    predictive_controller.update("error_rate", tool_error_count / max(step, 1))

                    if step > 2:
                        actions = predictive_controller.generate_predictive_actions()
                        if actions and actions[0].urgency > 0.7:
                            action = actions[0]
                            logger.info(
                                "Predictive action: %s urgency=%.2f horizon=%s",
                                action.recommended_action, action.urgency,
                                getattr(action, 'horizon', 'unknown'),
                            )
                            # Execute predictive actions via dispatch
                            def _predictive_compaction() -> None:
                                # Proactive compaction: run the pre-request
                                # pipeline (not the reactive engine, whose
                                # retry quota is reserved for real API
                                # overflow errors) and apply its output.
                                if not context_compactor:
                                    return
                                compacted = context_compactor.compact_messages(
                                    current_messages, force=True
                                )
                                if compacted is not None and len(compacted) < len(current_messages):
                                    previous_count = len(current_messages)
                                    current_messages[:] = reconcile_task_ledger(
                                        compacted
                                    )
                                    if context_manager is not None:
                                        context_manager.messages = list(current_messages)
                                    logger.info(
                                        "Predictive: compaction applied %d -> %d messages",
                                        previous_count, len(compacted),
                                    )

                            dispatch: dict[str, Callable[[], None]] = {
                                "trigger_compaction": _predictive_compaction,
                                "enable_safe_mode": lambda: logger.info(
                                    "Predictive: safe_mode recommended (reduce concurrency, extend timeouts)"
                                ),
                                "reduce_concurrency": lambda: logger.info(
                                    "Predictive: reduce_concurrency recommended"
                                ),
                            }
                            handler = dispatch.get(action.recommended_action)
                            if handler:
                                try:
                                    handler()
                                except TurnCancellationRequested:
                                    raise
                                except ModelCallDeadlineExceeded as exc:
                                    raise AgentTurnDeadlineExceeded(str(exc)) from exc
                                except Exception as exc:
                                    logger.warning(
                                        "Predictive action %s failed: %s",
                                        action.recommended_action, exc,
                                    )
                            # Also run self-healing for corroboration
                            if self_healing_engine:
                                healing_actions = self_healing_engine.detect_and_heal({
                                    "context_usage": stats.usage_percentage / 100.0 if context_manager else 0.0,
                                    "error_rate": tool_error_count / max(step, 1),
                                })
                                if healing_actions:
                                    logger.info("Self-healing: %s", healing_actions[0].strategy)

            # Required host persistence is part of request admission, not a
            # best-effort UI event. Fail before charging or calling a provider.
            if context_checkpoint_sink is not None:
                context_checkpoint_sink(current_messages)
            if metrics_collector:
                metrics_collector.start_turn(step)

            next_step: AgentStep
            need_model_observation = (
                event_sink is not None
                or (
                    agent_budget is not None
                    and agent_budget.max_cost_usd is not None
                )
            )
            model_operation_id = (
                new_model_operation_id()
                if need_model_observation
                else ""
            )
            model_started_at = (
                _start_model_observation_clock()
                if event_sink is not None
                else None
            )
            model_budget_reservation = None
            phase_request = None
            phase_tool_view = None
            admission_input_estimate = None
            preserve_tokens = 0
            try:
                raise_if_cancelled(cancellation_token)
                raise_if_deadline_exceeded()
                _revalidate_model_visible_memory()
                request_messages = current_messages
                if implementation_phases is not None:
                    if (isinstance(implementation_feedback, ExecutorCheckCapability)
                            and implementation_feedback.completion_pending):
                        # A rejected/unchecked write cannot become submit-only
                        # merely because an earlier check passed. This state is
                        # owned by the bound evidence gate, not model prose.
                        implementation_phases.observe_verification(failed=True)
                    phase_snapshot = agent_budget.snapshot() if agent_budget is not None else None
                    if progress_view_paths() and not progress_view_announced:
                        progress_view_announced = True
                        emit_event_safely(event_sink, "execution.implementation_context_projected", step=step, payload={
                            "canonicalInputEstimate": _estimated_context_tokens(current_messages),
                            "projectedInputEstimate": _estimated_context_tokens(implementation_request_messages()),
                            "changedPathCount": len(progress_view_paths()),
                        })
                    if (not recovery_view_active and implementation_phases.repair_pending
                            and ((implementation_phases.verification_repair_pending and progress_view_paths())
                                 or implementation_phases.preview(phase_snapshot,
                                     estimated_input=completion_input_estimate(),
                                     now=time.monotonic()).handoff_only)):
                        # Keep the canonical transcript and raw tool evidence.
                        # Actual changed work with an unpassed check can retire
                        # older read windows before each repeated input spends
                        # the repair allowance. Unique old windows may leave
                        # this view; canonical evidence and current read tools
                        # remain governed by their existing phase/scope rules.
                        # Anchor-only errors retain the closing-boundary trigger.
                        recovery_view_active = True
                        if implementation_request_state is not None:
                            implementation_request_state.recovery_view_active = True
                        projected = implementation_request_messages()
                        emit_event_safely(event_sink, "execution.repair_context_projected", step=step, payload={
                            "canonicalInputEstimate": _estimated_context_tokens(current_messages),
                            "projectedInputEstimate": _estimated_context_tokens(projected),
                            "calibratedInputEstimate": repair_input_calibration.estimate(_estimated_context_tokens(projected)),
                        })
                    request_messages = implementation_request_messages()
                progress_notice = coding_progress.before_request(
                    agent_budget.snapshot() if agent_budget is not None and coding_progress.enabled else None,
                    _estimated_context_tokens(request_messages),
                    remaining_steps=(max(0, max_steps - step + 1) if max_steps is not None else None),
                    remaining_seconds=(max(0.0, deadline_monotonic - time.monotonic()) if deadline_monotonic is not None else None),
                )
                if progress_notice is not None:
                    current_messages.append({
                        "role": "user", "content": progress_notice.text,
                        "_coding_progress": True,
                    })
                    if context_manager is not None:
                        context_manager.messages = list(current_messages)
                    emit_event_safely(event_sink, "execution.progress_checkpoint", step=step, payload={
                        "reasonCode": progress_notice.reason,
                        "inspectionBatches": progress_notice.read_batches,
                        "remainingTokens": progress_notice.remaining_tokens,
                        "estimatedRequestsLeft": progress_notice.estimated_requests_left,
                        "remainingSeconds": progress_notice.remaining_seconds,
                    })
                if implementation_phases is not None:
                    def phase_input_estimate(messages):
                        local = _estimated_context_tokens(messages)
                        calibrated = (repair_input_calibration.estimate(local) if implementation_view_active()
                                      else max(local, phase_observed_input))
                        # Output capacity must leave room for the schemas of
                        # the proposed phase, just like atomic admission does.
                        # Preview on a copy so pricing cannot consume a notice
                        # or change the real controller's handoff state.
                        proposed_phase = copy.copy(implementation_phases)
                        proposed = proposed_phase.request(phase_snapshot,
                            estimated_input=calibrated, now=time.monotonic())
                        options = {"tools_override": proposed_phase.tool_view(tools)}
                        if proposed.handoff_only and not has_structured_handoff:
                            options["disable_tools"] = True
                        return estimate_budgeted_request_input(model, messages,
                            agent_budget=agent_budget, estimated_input=calibrated,
                            optional_kwargs=options, default_tools=tools)

                    request_messages = implementation_request_messages()
                    priced_notices = []
                    # Adding the phase notice can itself move admission from
                    # two requests to one conditional response. Reach that
                    # small monotonic fixed point before committing state, so
                    # the tool view, sent instructions and price agree.
                    for _notice_pass in range(3):
                        planned = implementation_phases.preview(phase_snapshot,
                            estimated_input=phase_input_estimate(request_messages), now=time.monotonic())
                        if planned.message is None or planned.message in priced_notices:
                            break
                        notice = planned.message
                        if priced_notices and notice.startswith(priced_notices[-1]):
                            notice = notice[len(priced_notices[-1]):].lstrip()
                        current_messages.append({"role": "user", "content": notice, "_implementation_phase": True})
                        priced_notices.append(planned.message)
                        request_messages = implementation_request_messages()
                    # All phase/progress text is included in the price of the
                    # exact view we send. Preview never consumes a closing slot.
                    request_messages = implementation_request_messages()
                    phase_request = implementation_phases.request(phase_snapshot,
                        estimated_input=phase_input_estimate(request_messages), now=time.monotonic())
                    if isinstance(implementation_feedback, ExecutorCheckCapability):
                        implementation_feedback.reserve_handoff_time(implementation_phases.handoff_start)
                    phase_tool_view = implementation_phases.tool_view(tools)
                    if phase_request.message is not None:
                        emit_event_safely(event_sink, "execution.phase_changed", step=step, payload={
                            "phase": phase_request.phase.value,
                            "investigationCallLimit": implementation_phases.investigation_calls,
                            "localCallLimit": implementation_phases.call_limit,
                            "handoffCallLimit": implementation_phases.final_call_limit,
                            "deadlineMonotonic": phase_request.deadline,
                            "conditionalHandoff": phase_request.conditional_handoff,
                        })
                    if context_manager is not None:
                        context_manager.messages = list(current_messages)
                    if implementation_view_active() and context_compactor is not None:
                        context_compactor.read_dedup.reconcile(request_messages)
                request_options = ({
                        "tools_override": phase_tool_view,
                        "max_output_tokens_override": phase_request.output_limit,
                        "max_retries_override": 0,
                        **({"disable_tools": True} if phase_request.handoff_only and not has_structured_handoff else {}),
                        **_phase_reasoning_options(
                            # A first edit does not finish implementation. Use
                            # the actual authorized view for ongoing repairs,
                            # including writes during FINALIZE.
                            model, implementation=(
                                phase_request.phase.value in {"implement", "finalize"}
                                and not phase_request.handoff_only
                                and any(tool.name in {"edit_file", "write_file", "executor_test"}
                                        for tool in phase_tool_view.list())
                            ),
                        ),

                    } if phase_request else {"tools_override": tools})
                # The provider must see this invocation's executable registry,
                # including nested reviewers which reuse the parent's adapter.
                # Tool visibility does not depend on phase/budget scheduling.
                sent_input_estimate = _estimated_context_tokens(request_messages)
                admission_input_estimate = (repair_input_calibration.estimate(sent_input_estimate)
                                            if implementation_view_active() else sent_input_estimate)
                admission_input_estimate = estimate_budgeted_request_input(model, request_messages,
                    agent_budget=agent_budget, estimated_input=admission_input_estimate,
                    optional_kwargs=request_options, default_tools=tools)
                # A structured verdict may be as large as a normal response.
                # Native-only changes the tool surface, not provider capacity;
                # only the actual finite balance below may reduce this cap.
                native_closing_output = configured_output_limit()
                if host_native_result_tool is not None and not native_handoff_only and agent_budget is not None:
                    # Price one more working request and the same transcript's
                    # conclusion, including this output and its later replay.
                    # The parent-owned floor is added by the existing view.
                    # Future tool-output growth is unknown; this calibrated
                    # admission floor is not an unlimited-output guarantee.
                    native_input = max(admission_input_estimate, completion_input_estimate())
                    native_floor = (2 * configured_output_limit() + native_input
                                    + estimate_message_tokens({"role": "user", "content": native_handoff_prompt})
                                    + native_closing_output)
                    try:
                        model_budget_reservation = agent_budget.reserve_model_call(
                            native_input, preserve_model_calls=1,
                            preserve_total_tokens=native_floor,
                        )
                    except AgentBudgetExceeded:
                        # Failed admission spends nothing. Convert this same
                        # request to closure before an optional read can spend
                        # its conclusion budget; never create a new session.
                        native_handoff_only = True
                if host_native_result_tool is not None and native_handoff_only:
                    if not native_handoff_prompt_sent:
                        current_messages.append({"role": "user", "content": native_handoff_prompt})
                        native_handoff_prompt_sent = True
                    request_messages = current_messages
                    sent_input_estimate = _estimated_context_tokens(request_messages)
                    # Preserve the existing phase-before-native precedence.
                    if phase_request is None:
                        request_options = {
                            "tools_override": ToolRegistry([host_native_result_tool], skills=[], mcp_servers=[]),
                            "max_output_tokens_override": native_closing_output,
                            "max_retries_override": 0,
                            **_phase_reasoning_options(model),
                        }
                    admission_input_estimate = estimate_budgeted_request_input(model, request_messages,
                        agent_budget=agent_budget,
                        estimated_input=max(sent_input_estimate, completion_input_estimate()),
                        optional_kwargs=request_options, default_tools=tools)
                    if agent_budget is not None:
                        closing_snapshot = agent_budget.snapshot()
                        if closing_snapshot.limit_total_tokens is not None:
                            # The worker snapshot already excludes downstream
                            # floors. Shrink only this response's output cap;
                            # reserve_model_call still enforces the same floor
                            # atomically if another consumer spends meanwhile.
                            available_output = (closing_snapshot.limit_total_tokens
                                - closing_snapshot.used_total_tokens
                                - closing_snapshot.reserved_total_tokens
                                - admission_input_estimate)
                            native_closing_output = max(1, min(native_closing_output, available_output))
                    if phase_request is None:
                        request_options["max_output_tokens_override"] = native_closing_output
                    if context_manager is not None:
                        context_manager.messages = list(current_messages)
                    emit_event_safely(event_sink, "execution.native_handoff", step=step, payload={
                        "tool": host_native_result_tool.name,
                        "retryAfterRejectedSubmission": native_handoff_rejected,
                    })
                if agent_budget is not None and model_budget_reservation is None:
                    preserve_tokens = phase_request.preserve_tokens if phase_request else 0
                    if host_native_result_tool is not None:
                        preserve_tokens = max(preserve_tokens, native_closing_output)
                    if completion_policy is not None:
                        output_limit = phase_request.output_limit if phase_request else configured_output_limit()
                        # A phase's tool-free handoff already is the final reply.
                        # Other requests must leave one closing input/output plus
                        # this output's cost and its replay in that closing input.
                        completion_floor = output_limit
                        if not (phase_request and phase_request.handoff_only):
                            completion_floor += (output_limit + completion_input_estimate()
                                + estimate_message_tokens({"role": "user", "content":
                                    completion_policy.closing_prompt("turn_budget_exceeded: token budget exhausted")})
                                + min(2048, configured_output_limit()))
                        # Phase and completion protect the same eventual reply;
                        # floors constrain admission and are never billed twice.
                        preserve_tokens = max(preserve_tokens, completion_floor)
                    model_budget_reservation = agent_budget.reserve_model_call(
                        max(admission_input_estimate, completion_input_estimate()) if completion_policy is not None
                        else admission_input_estimate,
                        preserve_model_calls=int(completion_policy is not None) + (phase_request.preserve_calls if phase_request else 0),
                        **({"preserve_total_tokens": preserve_tokens} if preserve_tokens else {}),
                    )
                previous_model_input_estimate = sent_input_estimate
                if host_native_result_tool is not None and native_handoff_only:
                    native_handoff_attempted = True
                    native_handoff_rejected = False
                if implementation_phases is not None:
                    implementation_phases.admitted()
                if (isinstance(implementation_feedback, ExecutorCheckCapability)
                        and (implementation_feedback.supports_conditional_submit
                             or owned_implementation_feedback is not None)):
                    # This admitted request delivers the prior response's real
                    # failures. A rejected write is not a permanent poison for
                    # the session; stale check evidence remains invalidated.
                    implementation_feedback.begin_model_response(
                        context=implementation_observation_context)
                if event_sink is not None:
                    emit_event_safely(
                        event_sink,
                        "model.started",
                        step=step,
                        payload={"operationId": model_operation_id},
                    )
                next_step = _model_next(
                    model,
                    request_messages,
                    # An unadmitted implementation final must not reach the
                    # UI through streaming before its completion gate runs.
                    on_stream_chunk=(None if owned_implementation_feedback is not None
                                     else on_assistant_stream_chunk),
                    on_thinking_chunk=on_thinking_chunk,
                    store=store,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=phase_request.deadline if phase_request else deadline_monotonic,
                    global_deadline_monotonic=deadline_monotonic,
                    request_options=request_options,
                )
                raise_if_cancelled(cancellation_token)
                raise_if_deadline_exceeded()
                model_timeout_retry_pending = False
                model_transport_retry_count = 0
                step_usage = getattr(next_step, "usage", None)
                observed_input = getattr(step_usage, "input_tokens", None)
                if type(observed_input) is int and observed_input > 0:
                    phase_observed_input = observed_input
                if getattr(step_usage, "source", None) == "provider":
                    repair_input_calibration.observe(observed_input, sent_input_estimate)
                if abandoned_event is not None and abandoned_event.is_set():
                    turn_outcome = "failed"
                    tool_abandoned = True
                    raise ToolExecutionAbandoned(
                        "tool execution was abandoned after timeout"
                    )
            except PhaseCallDeadlineExceeded:
                _fail_agent_budget_reservation(agent_budget, model_budget_reservation, charge_estimate=True)
                raise_if_cancelled(cancellation_token)
                raise_if_deadline_exceeded()
                if implementation_phases is None:
                    # Native closing retains the owner deadline; it does not
                    # introduce Executor phases or grant a timeout retry.
                    emit_event_safely(event_sink, "model.failed", step=step, payload=_model_event_duration({
                        "operationId": model_operation_id,
                        "errorType": "timeout",
                        "reasonCode": "native_result_request_timeout",
                    }, _finish_model_observation_clock(model_started_at)))
                    turn_outcome = "failed"
                    current_messages.append({"role": "assistant", "_partial": True,
                                             "content": "The native result request timed out without a verdict."})
                    return current_messages
                emit_event_safely(event_sink, "model.failed", step=step, payload=_model_event_duration({
                    "operationId": model_operation_id,
                    "errorType": "timeout",
                    "reasonCode": "implementation_phase_request_timeout",
                }, _finish_model_observation_clock(model_started_at)))
                implementation_phases.timeout(now=time.monotonic())
                emit_event_safely(event_sink, "execution.phase_call_timeout", step=step, payload={"nextPhase": implementation_phases.phase.value})
                current_messages.append({"role": "user", "content": "The bounded phase request expired; its usage remains uncertain. Proceed using the remaining phase allowance and report any blocker."})
                continue
            except AgentTurnDeadlineExceeded:
                _fail_agent_budget_reservation(
                    agent_budget,
                    model_budget_reservation,
                )
                raise
            except AgentBudgetExceeded as error:
                _fail_agent_budget_reservation(
                    agent_budget,
                    model_budget_reservation,
                )
                raise_if_cancelled(cancellation_token)
                snapshot = agent_budget.snapshot() if agent_budget is not None else None
                emit_event_safely(event_sink, "execution.request_admission_rejected", step=step, payload={
                    "reason": error.reason,
                    "estimatedInputTokens": admission_input_estimate,
                    "preserveTotalTokens": preserve_tokens,
                    "outputLimit": (phase_request.output_limit if phase_request else native_closing_output
                                    if host_native_result_tool is not None and native_handoff_only
                                    else configured_output_limit()),
                    "preserveModelCalls": phase_request.preserve_calls if phase_request else 0,
                    "usedTotalTokens": getattr(snapshot, "used_total_tokens", None),
                    "limitTotalTokens": getattr(snapshot, "limit_total_tokens", None),
                    "reservedTotalTokens": getattr(snapshot, "reserved_total_tokens", None),
                    "usedModelCalls": getattr(snapshot, "used_model_calls", None),
                    "limitModelCalls": getattr(snapshot, "limit_model_calls", None),
                })
                if budget_exhausted_policy == "raise":
                    turn_outcome = "failed"
                    raise
                if completion_policy is not None:
                    if metrics_collector:
                        metrics_collector.end_turn(total_tokens=0)
                    finish_bounded_completion(
                        f"turn_budget_exceeded: {error.reason}", step,
                        allow_model_summary=not model_timeout_retry_pending,
                    )
                    return current_messages
                fallback = (
                    f"error[turn_budget_exceeded]: {error.reason}. "
                    "The shared turn budget is exhausted; simplify the task "
                    "or raise the budget limit."
                )
                logger.warning("Agent turn budget exceeded: %s", error.reason)
                if on_assistant_message:
                    on_assistant_message(fallback)
                current_messages.append(
                    {"role": "assistant", "content": fallback}
                )
                if metrics_collector:
                    metrics_collector.end_turn(total_tokens=0)
                turn_outcome = "failed"
                return current_messages
            except TurnCancellationRequested:
                _fail_agent_budget_reservation(
                    agent_budget,
                    model_budget_reservation,
                )
                raise
            except (KeyboardInterrupt, SystemExit):
                _fail_agent_budget_reservation(
                    agent_budget,
                    model_budget_reservation,
                )
                model_duration_ms = (
                    _finish_model_observation_clock(model_started_at)
                    if event_sink is not None
                    else None
                )
                if event_sink is not None:
                    emit_event_safely(
                        event_sink,
                        "model.failed",
                        step=step,
                        payload=_model_event_duration(
                            {
                                "operationId": model_operation_id,
                                "failureKind": "interrupted",
                            },
                            model_duration_ms,
                        ),
                    )
                raise
            except Exception as error:
                retryable_transport = _is_transient_model_transport_error(error)
                transport_cause = error.reason if isinstance(error, urllib.error.URLError) else error
                transport_timeout = retryable_transport and isinstance(transport_cause, TimeoutError)
                transport_kind = "timeout" if transport_timeout else "network"
                _fail_agent_budget_reservation(
                    agent_budget,
                    model_budget_reservation,
                    charge_estimate=retryable_transport,
                )
                model_duration_ms = (
                    _finish_model_observation_clock(model_started_at)
                    if event_sink is not None
                    else None
                )
                if event_sink is not None:
                    emit_event_safely(
                        event_sink,
                        "model.failed",
                        step=step,
                        payload=_model_event_duration(
                            {
                                "operationId": model_operation_id,
                                "failureKind": transport_kind if retryable_transport else "provider_error",
                            },
                            model_duration_ms,
                        ),
                    )
                raise_if_cancelled(cancellation_token)
                if transport_timeout:
                    raise_if_deadline_exceeded()
                # Explicit finite primary completion keeps its historical
                # one-retry/host-summary policy. Continuous primary and all
                # workers use the shared transport burst below instead.
                if transport_timeout and completion_policy is not None:
                    model_timeout_retry_pending = True
                    if metrics_collector:
                        metrics_collector.end_turn(total_tokens=0)
                    if (
                        model_timeout_retry_count == 0
                        and step < max_steps
                        and recovery_guard.stop_decision() is None
                    ):
                        model_timeout_retry_count += 1
                        logger.warning(
                            "Model transport timed out; retrying once within turn limits"
                        )
                        # The failed response produced no tool batch. Re-enter
                        # normal admission with the completed tools' messages
                        # intact; never replay those tools or reset this quota.
                        continue
                    finish_bounded_completion(
                        "model transport timeout; automatic retry unavailable or exhausted",
                        step,
                        # A failing provider must not receive a third request
                        # merely to describe its second timeout.
                        allow_model_summary=False,
                    )
                    return current_messages
                if retryable_transport:
                    # This exchange returned no AgentStep. Keep completed tool
                    # calls/results and the same adapter/channel, but charge its
                    # admitted call and conservative input estimate as unknown
                    # usage. Retrying always re-enters ordinary atomic admission.
                    model_timeout_retry_pending = True
                    current_messages.append({
                        "role": "system", "_model_transport_failure": True,
                        "content": json.dumps({"event": "model_transport_failure",
                            "operationId": model_operation_id,
                            "errorType": type(error).__name__, "usage": "unknown"}),
                    })
                    if context_manager is not None:
                        context_manager.messages = list(current_messages)
                    if metrics_collector:
                        metrics_collector.end_turn(total_tokens=0)
                    raise_if_deadline_exceeded()
                    if abandoned_event is not None and abandoned_event.is_set():
                        tool_abandoned = True
                        raise ToolExecutionAbandoned("tool execution was abandoned after timeout")
                    if (model_transport_retry_count < MAX_RETRIES
                            and (max_steps is None or step < max_steps)
                            and recovery_guard.stop_decision() is None):
                        delay = calculate_backoff(model_transport_retry_count,
                            category=ErrorCategory.NETWORK_ERROR)
                        model_transport_retry_count += 1
                        emit_event_safely(event_sink, "model.retry_scheduled", step=step, payload={
                            "operationId": model_operation_id, "failureKind": transport_kind,
                            "attempt": model_transport_retry_count, "delaySeconds": delay,
                            "usage": "unknown",
                        })
                        try:
                            controlled_retry_sleep(delay, cancellation_token=cancellation_token,
                                                   deadline_monotonic=deadline_monotonic)
                        except ModelCallDeadlineExceeded as stopped:
                            raise AgentTurnDeadlineExceeded(str(stopped)) from stopped
                        # A failed native-only exchange supplied no declaration;
                        # rearm its existing surface, never restore broader tools.
                        native_handoff_attempted = False
                        continue
                    turn_outcome = "failed"
                    fallback = "Model transport failed; retries exhausted or the original execution limit ended."
                    current_messages.append({"role": "assistant", "content": fallback, "_partial": True})
                    if on_assistant_message:
                        on_assistant_message(fallback)
                    return current_messages
                # Catch-all for unexpected errors (rate limit, auth, server 5xx, etc.)
                error_type = type(error).__name__
                fallback = f"Model API error ({error_type}): {error}"
                logger.error("Model API error (%s): %s", error_type, error)

                # Reactive Compact: 控制论恢复路径
                error_str = str(error).lower()
                needs_recovery = _looks_like_context_overflow(error_str)
                if context_cybernetics and needs_recovery:
                    context_operation_id = _new_context_operation_observation(
                        event_sink
                    )
                    recovery_messages_before = (
                        observable_message_count(current_messages)
                        if context_operation_id is not None
                        else None
                    )
                    if context_operation_id is not None:
                        emit_recovery_started_safely(
                            event_sink,
                            context_operation_id=context_operation_id,
                            kind="cybernetic",
                            step=step,
                        )
                    recovered_messages, recovery_result = context_cybernetics.try_reactive_recover(current_messages, error_str)
                    if recovery_result and recovery_result.effective:
                        current_messages = reconcile_task_ledger(
                            recovered_messages
                        )
                        if context_manager:
                            context_manager.messages = current_messages
                        if (
                            context_operation_id is not None
                            and recovery_messages_before is not None
                        ):
                            emit_context_compaction_safely(
                                event_sink,
                                recovery_result,
                                step=step,
                                context_operation_id=context_operation_id,
                                path="reactive_cybernetic",
                                messages_before=recovery_messages_before,
                                messages_after=observable_message_count(
                                    current_messages
                                ),
                            )
                            emit_recovery_completed_safely(
                                event_sink,
                                recovery_result,
                                step=step,
                                context_operation_id=context_operation_id,
                                kind="cybernetic",
                                messages_before=recovery_messages_before,
                                messages_after=observable_message_count(
                                    current_messages
                                ),
                            )
                        logger.info(
                            "Cybernetics Reactive recovered: freed %d tokens",
                            recovery_result.tokens_freed,
                        )
                        continue
                    if (
                        context_operation_id is not None
                        and recovery_messages_before is not None
                    ):
                        emit_recovery_completed_safely(
                            event_sink,
                            recovery_result,
                            step=step,
                            context_operation_id=context_operation_id,
                            kind="cybernetic",
                            messages_before=recovery_messages_before,
                            messages_after=observable_message_count(
                                recovered_messages
                            ),
                        )
                elif context_compactor and needs_recovery:
                    context_operation_id = _new_context_operation_observation(
                        event_sink
                    )
                    recovery_messages_before = (
                        observable_message_count(current_messages)
                        if context_operation_id is not None
                        else None
                    )
                    if context_operation_id is not None:
                        emit_recovery_started_safely(
                            event_sink,
                            context_operation_id=context_operation_id,
                            kind="compactor",
                            step=step,
                        )
                    recovery_result = context_compactor.reactive_recover(current_messages, error_str)
                    if recovery_result and recovery_result.effective:
                        current_messages = reconcile_task_ledger(
                            recovery_result.messages
                        )
                        if context_manager:
                            context_manager.messages = current_messages
                        if (
                            context_operation_id is not None
                            and recovery_messages_before is not None
                        ):
                            emit_context_compaction_safely(
                                event_sink,
                                recovery_result,
                                step=step,
                                context_operation_id=context_operation_id,
                                path="reactive_compactor",
                                messages_before=recovery_messages_before,
                                messages_after=observable_message_count(
                                    current_messages
                                ),
                            )
                            emit_recovery_completed_safely(
                                event_sink,
                                recovery_result,
                                step=step,
                                context_operation_id=context_operation_id,
                                kind="compactor",
                                messages_before=recovery_messages_before,
                                messages_after=observable_message_count(
                                    current_messages
                                ),
                            )
                        logger.info(
                            "Reactive Compact recovered: freed %d tokens",
                            recovery_result.tokens_freed,
                        )
                        continue
                    if (
                        context_operation_id is not None
                        and recovery_messages_before is not None
                    ):
                        emit_recovery_completed_safely(
                            event_sink,
                            recovery_result,
                            step=step,
                            context_operation_id=context_operation_id,
                            kind="compactor",
                            messages_before=recovery_messages_before,
                            messages_after=recovery_messages_before,
                        )

                # ModelSwitcher: 尝试切换到备用模型并重试
                if model_switcher and "rate" not in error_str:
                    try:
                        switch_result = model_switcher.switch_to(
                            "",  # Let switcher pick fallback
                            reason=f"{error_type}: {error_str[:80]}",
                        )
                        if switch_result.success and switch_result.adapter is not None:
                            model = switch_result.adapter
                            logger.info(
                                "ModelSwitcher: switched to %s, retrying with new adapter",
                                switch_result.new_model,
                            )
                            continue
                    except Exception:
                        pass

                if on_assistant_message:
                    on_assistant_message(fallback)
                current_messages.append({"role": "assistant", "content": fallback})
                if metrics_collector:
                    metrics_collector.end_turn(total_tokens=0)
                turn_outcome = "failed"
                return current_messages

            model_duration_ms = (
                _finish_model_observation_clock(model_started_at)
                if event_sink is not None
                else None
            )
            _record_context_token_observation(
                context_manager,
                current_messages,
                next_step,
                request_messages=request_messages,
            )
            usage_observation = (
                _model_usage_observation(next_step)
                if need_model_observation
                else {}
            )
            cost_payload = (
                _model_cost_observation(
                    model,
                    usage_observation,
                    model_operation_id,
                )
                if need_model_observation
                else None
            )
            _record_agent_budget_usage(
                agent_budget,
                next_step,
                reservation=model_budget_reservation,
                model=model,
                usage_observation=usage_observation,
                cost_payload=cost_payload,
            )
            # Reconcile provider-reported input tokens with the local
            # estimate that drives the compaction high-water policy.
            # current_messages is still the exact request payload here —
            # the response is appended later.
            if context_compactor is not None and usage_observation:
                reported_input = usage_observation.get("inputTokens")
                if isinstance(reported_input, int) and reported_input > 0:
                    try:
                        context_compactor.record_usage_observation(
                            reported_input,
                            sent_input_estimate,
                        )
                    except Exception:  # noqa: BLE001 - calibration is advisory
                        pass
            if event_sink is not None:
                emit_event_safely(
                    event_sink,
                    "model.completed",
                    step=step,
                    payload=_model_event_duration(
                        {
                            "operationId": model_operation_id,
                            "resultType": (
                                "tool_calls"
                                if next_step.type == "tool_calls"
                                else "assistant"
                            ),
                            "contentPresent": bool(next_step.content),
                            "toolCallCount": min(
                                len(next_step.calls), _MAX_MODEL_TOOL_CALL_COUNT
                            ),
                            "usage": usage_observation,
                        },
                        model_duration_ms,
                    ),
                )
                if cost_payload is not None:
                    emit_event_safely(
                        event_sink,
                        "model.costed",
                        step=step,
                        payload=cost_payload,
                    )

            if next_step.type == "assistant":
                if host_native_result_tool is not None and native_handoff_only:
                    current_messages.append({"role": "assistant", "content": next_step.content})
                    continue
                is_empty = _is_empty_assistant_response(next_step.content)
                if not is_empty and _should_treat_assistant_as_progress(
                    kind=getattr(next_step, 'kind', None),
                    content=next_step.content,
                    saw_tool_result=saw_tool_result,
                    unfinished_retry_count=unfinished_action_retry_count,
                ):
                    explicit_progress = getattr(next_step, 'kind', None) == "progress"
                    if not explicit_progress:
                        unfinished_action_retry_count += 1
                    _append_trace_event(execution_trace, {
                        "type": "assistant_step",
                        "step": step,
                        "content_kind": (
                            "progress"
                            if explicit_progress
                            else "unfinished_action"
                        ),
                        "content": _redact_trace_text(next_step.content),
                    })
                    if on_progress_message:
                        on_progress_message(next_step.content)
                    current_messages.append({"role": "assistant_progress", "content": next_step.content})
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                NUDGE_AFTER_UNFINISHED_ACTION
                                if not explicit_progress
                                else NUDGE_CONTINUE
                            ),
                        }
                    )
                    continue

                diagnostics = next_step.diagnostics

                if _is_recoverable_thinking_stop(
                    is_empty=is_empty,
                    stop_reason=diagnostics.stopReason if diagnostics else None,
                    ignored_block_types=diagnostics.ignoredBlockTypes if diagnostics else None,
                ) and recoverable_thinking_retry_count < 3:
                    recoverable_thinking_retry_count += 1
                    stop_reason = diagnostics.stopReason if diagnostics else None
                    progress_content = (
                        "Model hit max_tokens during thinking; requesting the next step."
                        if stop_reason == "max_tokens"
                        else "Model returned pause_turn; requesting the next step."
                    )
                    if on_progress_message:
                        on_progress_message(progress_content)
                    current_messages.append({"role": "assistant_progress", "content": progress_content})
                    _append_trace_event(execution_trace, {
                        "type": "assistant_step",
                        "step": step,
                        "content_kind": "recoverable_thinking_stop",
                        "content": _redact_trace_text(progress_content),
                    })
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                RESUME_AFTER_PAUSE
                                if stop_reason == "pause_turn"
                                else RESUME_AFTER_MAX_TOKENS
                            ),
                        }
                    )
                    continue

                if is_empty and empty_response_retry_count < 2:
                    empty_response_retry_count += 1
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                NUDGE_AFTER_EMPTY_RESPONSE
                                if saw_tool_result
                                else NUDGE_AFTER_EMPTY_NO_TOOLS
                            ),
                        }
                    )
                    continue

                if is_empty:
                    diagnostics_suffix = _format_diagnostics(
                        diagnostics.stopReason if diagnostics else None,
                        diagnostics.blockTypes if diagnostics else None,
                        diagnostics.ignoredBlockTypes if diagnostics else None,
                    )
                    if saw_tool_result:
                        fallback = (
                            f"Model returned an empty response after tool execution and the turn was stopped. There were {tool_error_count} tool error(s); retry, adjust the command, or choose a different approach.{diagnostics_suffix}"
                            if tool_error_count > 0
                            else f"Model returned an empty response after tool execution and the turn was stopped. Retry or ask the model to continue the remaining steps.{diagnostics_suffix}"
                        )
                    else:
                        fallback = f"Model returned an empty response and the turn was stopped.{diagnostics_suffix}"
                    raise_if_cancelled(cancellation_token)
                    if on_assistant_message:
                        on_assistant_message(fallback)
                    current_messages.append({"role": "assistant", "content": fallback})
                    _append_trace_event(execution_trace, {
                        "type": "assistant_step",
                        "step": step,
                        "content_kind": "empty_fallback",
                        "content": _redact_trace_text(fallback),
                    })
                    turn_outcome = "unknown"
                    return current_messages

                if enforce_subagent_completion_before_final(next_step.content):
                    continue

                skill_contract = enforce_skill_load_before_final()
                if skill_contract == "retry":
                    continue
                if skill_contract != "ok":
                    raise_if_cancelled(cancellation_token)
                    if on_assistant_message:
                        on_assistant_message(skill_contract)
                    current_messages.append(
                        {"role": "assistant", "content": skill_contract}
                    )
                    turn_outcome = "failed"
                    return current_messages

                discovery_contract = enforce_discovery_before_final()
                if discovery_contract == "retry":
                    continue

                implementation_decision = enforce_implementation_completion_before_final(next_step.content)
                if implementation_decision == "retry":
                    continue
                if implementation_decision == "stop":
                    turn_outcome = "unknown"
                    return current_messages
                raise_if_cancelled(cancellation_token)
                if on_assistant_message:
                    on_assistant_message(next_step.content)
                current_messages.append({"role": "assistant", "content": next_step.content})
                _append_trace_event(execution_trace, {
                    "type": "assistant_step",
                    "step": step,
                    "content_kind": getattr(next_step, 'kind', None) or "final",
                    "content": _redact_trace_text(next_step.content),
                })
                turn_outcome = "success"
                # Protect final answer in working memory
                protect_context(
                    content=next_step.content[:500],
                    entry_type="key_decision",
                    ttl_seconds=3600,
                )
                _working_memory_observation(event_sink, step=step)
                return current_messages

            if len(next_step.calls) > _MAX_MODEL_TOOL_CALL_COUNT:
                tool_call_limit_error = (
                    "error[tool_call_limit_exceeded]: model returned "
                    f"{len(next_step.calls)} tool calls; the per-turn limit is "
                    f"{_MAX_MODEL_TOOL_CALL_COUNT}. No tools were executed."
                )
                raise_if_cancelled(cancellation_token)
                if on_assistant_message:
                    on_assistant_message(tool_call_limit_error)
                current_messages.append(
                    {"role": "assistant", "content": tool_call_limit_error}
                )
                _append_trace_event(
                    execution_trace,
                    {
                        "type": "error",
                        "step": step,
                        "error_type": "tool_call_limit_exceeded",
                        "message": tool_call_limit_error,
                    },
                )
                turn_outcome = "failed"
                return current_messages

            if (
                not next_step.calls
                and next_step.content
                and next_step.contentKind != "progress"
                and enforce_subagent_completion_before_final(next_step.content)
            ):
                continue

            if not next_step.calls and next_step.content and next_step.contentKind != "progress":
                implementation_decision = enforce_implementation_completion_before_final(next_step.content)
                if implementation_decision == "retry":
                    continue
                if implementation_decision == "stop":
                    turn_outcome = "unknown"
                    return current_messages

            if next_step.content:
                role = "assistant_progress" if next_step.contentKind == "progress" else "assistant"
                _append_trace_event(execution_trace, {
                    "type": "assistant_step",
                    "step": step,
                    "content_kind": next_step.contentKind or role,
                    "content": _redact_trace_text(next_step.content),
                })
                if role == "assistant_progress":
                    if on_progress_message:
                        on_progress_message(next_step.content)
                    if not next_step.calls:
                        current_messages.append({"role": role, "content": next_step.content})
                        current_messages.append(
                            {
                                "role": "user",
                                "content": NUDGE_CONTINUE,
                            }
                        )
                else:
                    if on_assistant_message:
                        on_assistant_message(next_step.content)
                    if not next_step.calls:
                        current_messages.append({"role": role, "content": next_step.content})

            if not next_step.calls and next_step.content and next_step.contentKind != "progress":
                skill_contract = enforce_skill_load_before_final()
                if skill_contract == "retry":
                    continue
                if skill_contract != "ok":
                    raise_if_cancelled(cancellation_token)
                    if on_assistant_message:
                        on_assistant_message(skill_contract)
                    current_messages.append(
                        {"role": "assistant", "content": skill_contract}
                    )
                    turn_outcome = "failed"
                    return current_messages

                discovery_contract = enforce_discovery_before_final()
                if discovery_contract == "retry":
                    continue
                raise_if_cancelled(cancellation_token)
                turn_outcome = "success"
                return current_messages

            # --- Concurrent tool execution ---
            # Classify calls into concurrent-safe (read-only) vs serial (writes/commands)
            calls = next_step.calls
            completion_before = {}
            mutation_epoch_before = mutation_observer.mutation_epoch if mutation_observer else 0
            if mutation_observer is not None:
                try:
                    observable_calls = calls
                    from minicode.scheduler_permissions import (
                        SchedulerExecutorPermissions, SchedulerWorkerReadPermissions,
                    )
                    from minicode.permissions import PermissionDeniedError

                    if isinstance(permissions, (SchedulerExecutorPermissions, SchedulerWorkerReadPermissions)):
                        # Observation is also a read. This deterministic check
                        # never prompts or grants access to a denied Assignment
                        # path before the actual write tool validates it.
                        observable_calls = []
                        for candidate in calls:
                            inputs = candidate.get("input")
                            target = inputs.get("path") if isinstance(inputs, dict) else None
                            if candidate.get("toolName") not in {"edit_file", "write_file"}:
                                continue
                            try:
                                permissions.ensure_path_access(target, "read")
                            except PermissionDeniedError:
                                continue
                            observable_calls.append(candidate)
                    completion_before = mutation_observer.capture_before(observable_calls)
                except Exception:  # progress observation cannot own execution
                    if max_steps is not None:
                        mutation_observer.lower_limit(max_steps)
                    logger.warning("Completion progress capture unavailable")
            assistant_tool_turn_id = f"tool-turn-{uuid.uuid4().hex}"
            _results: list[tuple[dict, ToolResult]] = []
            concurrent_call_ids: set[str] = set()
            suppressed_call_ids: set[str] = set()
            raise_if_cancelled(cancellation_token)

            executable_calls: list[dict] = []
            phase_rejections = implementation_phases.admit_batch(calls, cwd=cwd, permissions=permissions) if implementation_phases is not None else {}
            if host_native_result_tool is not None and native_handoff_only:
                phase_rejections.update({
                    call["id"]: "The reserved conclusion request permits only the bound review_submit capability."
                    for call in calls if call.get("toolName") != host_native_result_tool.name
                })
            for call in calls:
                if call["id"] in phase_rejections:
                    suppressed_call_ids.add(call["id"])
                    _results.append((call, ToolResult(ok=False, output=phase_rejections[call["id"]])))
                    continue
                suppression = recovery_guard.suppression_for(call)
                if suppression is None:
                    executable_calls.append(call)
                    continue
                suppressed_call_ids.add(call["id"])
                _results.append(
                    (call, ToolResult(ok=False, output=suppression.message))
                )

            for discovery_call in executable_calls:
                if str(discovery_call.get("toolName")) in (
                    "browse_skill_directory",
                    "search_skills",
                    "load_skill",
                ):
                    skill_usage_tracker.record_discovery(
                        str(discovery_call.get("toolName"))
                    )

            # --- Skill load barrier: a batch containing a load_skill for a
            # not-yet-loaded skill must execute only the loads; every other
            # call is deferred so the model can read the skill body before
            # acting on it. ---
            if any(
                call.get("toolName") == "load_skill" for call in executable_calls
            ):
                loaded_skill_names = skill_usage_tracker.loaded_names()
                has_new_load = any(
                    # Model arguments can be valid JSON without being an
                    # object. Let normal tool validation report that error;
                    # an invalid load must not crash this preflight or let
                    # sibling actions run before the model corrects it.
                    not isinstance(call.get("input"), dict)
                    or str(call["input"].get("name") or "").strip()
                    not in loaded_skill_names
                    for call in executable_calls
                    if call.get("toolName") == "load_skill"
                )
                if has_new_load:
                    deferred_calls = [
                        call
                        for call in executable_calls
                        if call.get("toolName") != "load_skill"
                    ]
                    if deferred_calls:
                        deferred_ids = {call["id"] for call in deferred_calls}
                        executable_calls = [
                            call
                            for call in executable_calls
                            if call["id"] not in deferred_ids
                        ]
                        for call in deferred_calls:
                            suppressed_call_ids.add(call["id"])
                            _results.append(
                                (
                                    call,
                                    ToolResult(
                                        ok=False,
                                        output=(
                                            "error[deferred_until_skill_loaded]: "
                                            "not executed in this batch; "
                                            "re-propose after reading the skill "
                                            "body"
                                        ),
                                    ),
                                )
                            )

            for rejected_call, rejected_result in _results:
                observe_implementation_tool_result(rejected_call, rejected_result)

            parent_handoff_preserve_tokens = _parent_handoff_token_floor(
                current_messages, calls,
                assistant_content=next_step.content or "",
                reasoning_content=next_step.reasoningContent,
                closing_prompt=(completion_policy.closing_prompt("turn_budget_exceeded: token budget exhausted")
                                if completion_policy is not None else MAX_STEPS_SOFT_LANDING_PROMPT),
            )

            # Preserve read-first scheduling, but never join writes across an
            # original non-write barrier. Only actual serial execution enters
            # these host-owned groups; explicit checks/submission always flush.
            write_groups = {}
            if isinstance(implementation_feedback, ExecutorCheckCapability) and implementation_feedback.has_automatic_write_checks:
                cohort = []
                for candidate in [*calls, None]:
                    if (candidate is not None and candidate in executable_calls
                            and candidate.get("toolName") in {"edit_file", "write_file", "executor_test"}):
                        cohort.append(candidate["id"])
                        continue
                    if len(cohort) > 1:
                        group = tuple(cohort)
                        write_groups.update((identity, group) for identity in group)
                    cohort = []

            if len(executable_calls) == 1:
                # Single call — no benefit from concurrency, run directly
                call = executable_calls[0]
                fire_hook_sync(
                    HookEvent.PRE_TOOL_USE,
                    tool_name=call["toolName"],
                    tool_input=call["input"],
                    step=step,
                )
                if metrics_collector:
                    metrics_collector.start_tool(call["toolName"])
                result = _execute_single_tool(
                    call, tools, cwd, permissions, runtime, store, step,
                    on_tool_start, on_tool_result, tool_scheduler, event_sink,
                    skill_usage_tracker,
                    cancellation_token,
                    verification_tracker,
                    agent_depth,
                    presentation,
                    agent_budget,
                    subagent_mailbox,
                    subagent_lifecycle,
                    subagent_sessions,
                    todo_state,
                    allow_user_interaction,
                    deadline_monotonic,
                    skill_load_policy,
                    implementation_candidate_guard=implementation_candidate_guard,
                    parent_handoff_preserve_tokens=parent_handoff_preserve_tokens,
                    workflow_delivery=workflow_delivery,
                    workflow_user_request=(current_user_message if agent_depth == 0 else None),
                )
                _results.append((call, result))
                observe_implementation_tool_result(call, result)
                if metrics_collector:
                    try:
                        metrics_collector.end_tool(
                            success=result.ok,
                            error=result.output if not result.ok else "",
                        )
                    except BaseException:  # finalized authority wins
                        if not result._must_deliver:
                            raise
                        logger.warning(
                            "Tool metrics failed after finalized state change",
                            exc_info=True,
                        )
                try:
                    recovery_guard.observe(
                        call,
                        ok=result.ok,
                        output=result.output,
                    )
                except BaseException:  # finalized authority wins
                    if not result._must_deliver:
                        raise
                    logger.warning(
                        "Recovery observation failed after state finalization",
                        exc_info=True,
                    )
            elif len(executable_calls) > 1:
                # Multiple calls — use ToolScheduler for intelligent partitioning
                concurrent_calls, serial_calls = tool_scheduler.schedule_calls(
                    executable_calls, tools
                )
                concurrent_authority_stop = False

                # Phase 1: Run all concurrent-safe tools in parallel
                if concurrent_calls:
                    raise_if_cancelled(cancellation_token)
                    # Pre-tool hooks fire on the main thread before dispatch:
                    # every call must pass through PRE_TOOL_USE regardless of
                    # its concurrency classification, and before it executes.
                    for call in concurrent_calls:
                        fire_hook_sync(
                            HookEvent.PRE_TOOL_USE,
                            tool_name=call["toolName"],
                            tool_input=call["input"],
                            step=step,
                        )
                    concurrent_call_ids = {call["id"] for call in concurrent_calls}
                    max_workers = tool_scheduler.get_recommended_max_workers(
                        concurrent_calls,
                        error_rate=tool_error_count / max(step, 1),
                        avg_latency=last_step_duration_seconds,
                        recent_failures=tool_error_count,
                    )
                    # Apply cybernetic concurrency cap if FeedbackController reduced parallelism
                    force_cap = getattr(tool_scheduler, '_force_max_workers', None)
                    if force_cap:
                        max_workers = min(max_workers, force_cap)
                    if tool_scheduler.last_decision:
                        logger.info(
                            "ToolSchedulerController: workers=%d multiplier=%.2f cooldown=%.2fs [%s]",
                            max_workers,
                            tool_scheduler.last_decision.concurrency_multiplier,
                            tool_scheduler.last_decision.cooldown_seconds,
                            ", ".join(tool_scheduler.last_decision.reasons or []),
                        )
                    pool = concurrent.futures.ThreadPoolExecutor(
                        max_workers=max_workers,
                        thread_name_prefix="mc-tool",
                    )
                    batch_cancellation: TurnCancellationRequested | None = None
                    batch_fatal: BaseException | None = None
                    observed_results: dict[
                        concurrent.futures.Future, ToolResult
                    ] = {}
                    observed_errors: dict[
                        concurrent.futures.Future, BaseException
                    ] = {}
                    future_to_call: dict[
                        concurrent.futures.Future, dict
                    ] = {}
                    unsubmitted_concurrent_calls: list[dict] = []
                    decision: _ConcurrentBatchDecision | None = None
                    try:
                        for dispatch_index, call in enumerate(concurrent_calls):
                            try:
                                future = pool.submit(
                                    _execute_single_tool,
                                    call=call,
                                    tools=tools,
                                    cwd=cwd,
                                    permissions=permissions,
                                    runtime=runtime,
                                    store=None,
                                    step=step,
                                    on_tool_start=on_tool_start,
                                    on_tool_result=on_tool_result,
                                    tool_scheduler=tool_scheduler,
                                    event_sink=event_sink,
                                    skill_usage_tracker=skill_usage_tracker,
                                    cancellation_token=cancellation_token,
                                    verification_tracker=verification_tracker,
                                    agent_depth=agent_depth,
                                    presentation=presentation,
                                    agent_budget=agent_budget,
                                    subagent_mailbox=subagent_mailbox,
                                    subagent_lifecycle=subagent_lifecycle,
                                    subagent_sessions=subagent_sessions,
                                    todo_state=todo_state,
                                    allow_user_interaction=allow_user_interaction,
                                    deadline_monotonic=deadline_monotonic,
                                    skill_load_policy=skill_load_policy,
                                    implementation_candidate_guard=implementation_candidate_guard,
                                    parent_handoff_preserve_tokens=parent_handoff_preserve_tokens,
                                    workflow_delivery=workflow_delivery,
                                    workflow_user_request=(current_user_message if agent_depth == 0 else None),
                                )
                            except TurnCancellationRequested as exc:
                                batch_cancellation = exc
                                unsubmitted_concurrent_calls = concurrent_calls[
                                    dispatch_index:
                                ]
                                break
                            except BaseException as exc:  # noqa: BLE001
                                batch_fatal = exc
                                unsubmitted_concurrent_calls = concurrent_calls[
                                    dispatch_index:
                                ]
                                break
                            future_to_call[future] = call
                        pending_futures = set(future_to_call)
                        while pending_futures:
                            try:
                                raise_if_cancelled(cancellation_token)
                            except TurnCancellationRequested as exc:
                                batch_cancellation = exc
                                break
                            try:
                                done_futures, pending_futures = (
                                    concurrent.futures.wait(
                                        pending_futures,
                                        timeout=0.05,
                                        return_when=(
                                            concurrent.futures.FIRST_COMPLETED
                                        ),
                                    )
                                )
                            except TurnCancellationRequested as exc:
                                batch_cancellation = exc
                                break
                            except BaseException as exc:  # noqa: BLE001
                                # A signal raised on the orchestration thread
                                # must enter the same non-blocking reducer as a
                                # worker signal; otherwise ``shutdown(wait)``
                                # can strand the turn behind hung workers.
                                batch_fatal = exc
                                break
                            for future in done_futures:
                                try:
                                    result = future.result()
                                except TurnCancellationRequested as exc:
                                    observed_errors[future] = exc
                                    if batch_cancellation is None:
                                        batch_cancellation = exc
                                    continue
                                except Exception as exc:
                                    result = _concurrent_exception_result(exc)
                                except BaseException as exc:  # noqa: BLE001
                                    observed_errors[future] = exc
                                    if batch_fatal is None:
                                        batch_fatal = exc
                                    continue
                                observed_results[future] = result
                            if (
                                batch_fatal is not None
                                and batch_cancellation is None
                            ):
                                try:
                                    raise_if_cancelled(cancellation_token)
                                except TurnCancellationRequested as exc:
                                    batch_cancellation = exc
                            if (
                                batch_cancellation is not None
                                or batch_fatal is not None
                            ):
                                break
                        if (
                            batch_cancellation is not None
                            or batch_fatal is not None
                        ):
                            decision = _reduce_concurrent_batch_shutdown(
                                future_to_call=future_to_call,
                                observed_results=observed_results,
                                observed_errors=observed_errors,
                                cancellation=batch_cancellation,
                                collection_error=batch_fatal,
                            )
                            for disposition in decision.dispositions:
                                _results.append(
                                    (disposition.call, disposition.result)
                                )
                            observation_fatal = (
                                _record_concurrent_shutdown_observations(
                                    decision=decision,
                                    execution_trace=execution_trace,
                                    event_sink=event_sink,
                                    step=step,
                                )
                            )
                            finalize_fatal = (
                                _finalize_completed_concurrent_dispositions(
                                    dispositions=decision.dispositions,
                                    store=store,
                                    task_ledger=task_ledger,
                                    recovery_guard=recovery_guard,
                                    step=step,
                                )
                            )
                            if decision.control_error is not None:
                                if _is_fatal_concurrent_error(
                                    decision.control_error
                                ):
                                    raise decision.control_error
                                if observation_fatal is not None:
                                    raise observation_fatal
                                if finalize_fatal is not None:
                                    raise finalize_fatal
                                raise decision.control_error
                            concurrent_authority_stop = True
                            for pending_call in (
                                unsubmitted_concurrent_calls + serial_calls
                            ):
                                suppressed_call_ids.add(pending_call["id"])
                                _results.append(
                                    (
                                        pending_call,
                                        ToolResult(
                                            ok=False,
                                            output=(
                                                "error[skipped_after_finalized_"
                                                "state_change]: a prior "
                                                "stateful result must be "
                                                "delivered first"
                                            ),
                                        ),
                                    )
                                )
                        else:
                            for future, call in future_to_call.items():
                                result = observed_results[future]
                                _results.append((call, result))
                                observe_implementation_tool_result(call, result)
                                try:
                                    recovery_guard.observe(
                                        call,
                                        ok=result.ok,
                                        output=result.output,
                                    )
                                except BaseException:  # finalized authority wins
                                    if not result._must_deliver:
                                        raise
                                    logger.warning(
                                        "Recovery observation failed after state finalization",
                                        exc_info=True,
                                    )
                    finally:
                        active_error = sys.exception()
                        wait_for_workers = (
                            batch_cancellation is None
                            and batch_fatal is None
                        )
                        try:
                            pool.shutdown(
                                wait=wait_for_workers,
                                cancel_futures=not wait_for_workers,
                            )
                        except BaseException as shutdown_error:  # noqa: BLE001
                            has_finalized_authority = (
                                decision is not None
                                and decision.must_deliver
                            ) or any(
                                result._must_deliver
                                for result in observed_results.values()
                            )
                            preserve_active_fatal = (
                                active_error is not None
                                and _is_fatal_concurrent_error(active_error)
                            )
                            shutdown_wins = (
                                not has_finalized_authority
                                and not preserve_active_fatal
                                and (
                                    active_error is None
                                    or _is_fatal_concurrent_error(
                                        shutdown_error
                                    )
                                )
                            )
                            try:
                                logger.warning(
                                    "Concurrent pool shutdown failed",
                                    exc_info=True,
                                )
                            except BaseException:
                                pass
                            if shutdown_wins:
                                raise

                # Phase 2: Run serial tools sequentially (in original order)
                if serial_calls and not concurrent_authority_stop:
                    for serial_index, call in enumerate(serial_calls):
                        group = write_groups.get(call["id"])
                        if write_feedback_batch is not None and group != write_feedback_group:
                            flush_write_feedback_batch()
                        late_suppression = recovery_guard.suppression_for(call)
                        if late_suppression is not None:
                            suppressed_call_ids.add(call["id"])
                            _results.append(
                                (
                                    call,
                                    ToolResult(
                                        ok=False,
                                        output=late_suppression.message,
                                    ),
                                )
                            )
                            observe_implementation_tool_result(call, _results[-1][1])
                            continue
                        fire_hook_sync(
                            HookEvent.PRE_TOOL_USE,
                            tool_name=call["toolName"],
                            tool_input=call["input"],
                            step=step,
                        )
                        if metrics_collector:
                            metrics_collector.start_tool(call["toolName"])
                        if group is not None and write_feedback_batch is None:
                            write_feedback_batch = implementation_feedback.begin_write_batch()
                            write_feedback_group = group
                        if write_feedback_batch is not None:
                            write_feedback_batch.call_id = call["id"]
                        try:
                            result = _execute_single_tool(
                                call, tools, cwd, permissions, runtime, store, step,
                                on_tool_start, on_tool_result, tool_scheduler, event_sink,
                                skill_usage_tracker,
                                cancellation_token,
                                verification_tracker,
                                agent_depth,
                                presentation,
                                agent_budget,
                                subagent_mailbox,
                                subagent_lifecycle,
                                subagent_sessions,
                                todo_state,
                                allow_user_interaction,
                                deadline_monotonic,
                                skill_load_policy,
                                implementation_candidate_guard=implementation_candidate_guard,
                                parent_handoff_preserve_tokens=_parent_handoff_token_floor(
                                    current_messages, calls,
                                    assistant_content=next_step.content or "",
                                    reasoning_content=next_step.reasoningContent,
                                    closing_prompt=(completion_policy.closing_prompt("turn_budget_exceeded: token budget exhausted")
                                                    if completion_policy is not None else MAX_STEPS_SOFT_LANDING_PROMPT),
                                    completed_results=_results,
                                ),
                                workflow_delivery=workflow_delivery,
                                workflow_user_request=(current_user_message if agent_depth == 0 else None),
                            )
                        finally:
                            if (write_feedback_batch is not None and
                                    (sys.exception() is not None or call["id"] == write_feedback_group[-1])):
                                flush_write_feedback_batch()
                        _results.append((call, result))
                        observe_implementation_tool_result(call, result)
                        if metrics_collector:
                            try:
                                metrics_collector.end_tool(
                                    success=result.ok,
                                    error=result.output if not result.ok else "",
                                )
                            except BaseException:  # finalized authority wins
                                if not result._must_deliver:
                                    raise
                                logger.warning(
                                    "Tool metrics failed after finalized state change",
                                    exc_info=True,
                                )
                        try:
                            recovery_guard.observe(
                                call,
                                ok=result.ok,
                                output=result.output,
                            )
                        except BaseException:  # finalized authority wins
                            if not result._must_deliver:
                                raise
                            logger.warning(
                                "Recovery observation failed after state finalization",
                                exc_info=True,
                            )
                        # A finalized state change is authoritative and must be
                        # delivered before any later serial call can run.  Seal
                        # skipped results for the remaining provider calls so
                        # the tool-call protocol stays balanced.
                        submitted = result.ok and result._implementation_submitted and call["toolName"] in {"executor_submit", "review_submit", "completion_review_submit", "completion_probe_migration_submit", "completion_probe_adjudication_submit", "completion_coverage_audit_submit", "completion_coverage_inspection_submit", "subagent_note_write"}
                        budget_terminal = workflow_delivery.is_budget_terminal(call, result)
                        if result.awaitUser or result._must_deliver or submitted or budget_terminal:
                            flush_write_feedback_batch()
                            if result._must_deliver or submitted or budget_terminal:
                                for pending_call in serial_calls[
                                    serial_index + 1 :
                                ]:
                                    suppressed_call_ids.add(pending_call["id"])
                                    _results.append(
                                        (
                                            pending_call,
                                            ToolResult(
                                                ok=False,
                                                output=(
                                                    "error[skipped_after_finalized_"
                                                    "state_change]: a prior "
                                                    "stateful result must be "
                                                    "delivered first"
                                                ),
                                            ),
                                        )
                                    )
                            # Still need to process remaining results for messages
                            break

            # Late suppression or an early await-user return may leave the
            # cohort's final write unexecuted. Check the actual partial bytes.
            flush_write_feedback_batch()
            must_deliver_result = any(
                result._must_deliver for _call, result in _results
            )

            # Process all results and build messages (preserve original call order)
            call_order = {call["id"]: idx for idx, call in enumerate(calls)}
            _results.sort(key=lambda pair: call_order.get(pair[0]["id"], 999))
            if mutation_observer is not None:
                try:
                    mutation_observer.observe_results(
                        [pair for pair in _results if pair[0]["id"] not in suppressed_call_ids],
                        completion_before,
                        step=step,
                    )
                except Exception:  # retain authoritative committed receipts below
                    if max_steps is not None:
                        mutation_observer.lower_limit(max_steps)
                    logger.warning("Completion progress observation unavailable")
            observed_change = bool(
                mutation_observer and mutation_observer.mutation_epoch > mutation_epoch_before
            )
            if implementation_phases is not None:
                implementation_phases.observe_change(observed_change)
            for call, result in _results:
                if (implementation_phases is not None and has_structured_handoff
                        and isinstance(subagent_mailbox, ExecutorResultChannel)
                        and call["id"] not in suppressed_call_ids
                        and call.get("toolName") == "executor_submit" and not result.ok):
                    # Only actual execution feedback from the bound capability
                    # can reopen its remaining submit-only allowance. Model
                    # prose and suppressed tool calls cannot reset this state.
                    implementation_phases.observe_handoff_result(accepted=False)
                if (implementation_phases is not None and call["id"] not in suppressed_call_ids
                        and call.get("toolName") in {"edit_file", "write_file"}):
                    implementation_phases.observe_edit(
                        path=str(call.get("input", {}).get("path", "")),
                        anchor_failed=result._edit_anchor_failed is True,
                        changed=result._file_changed is True,
                    )
                if call["id"] not in suppressed_call_ids and type(result._implementation_checks_failed) is bool:
                    # This flag comes from host-run public checks, never model
                    # prose, and remains available when phase steering is off.
                    if owned_implementation_feedback is not None:
                        direct_implementation_checks_failed = result._implementation_checks_failed
                        direct_implementation_batch_status = None
                    if implementation_phases is not None:
                        implementation_phases.observe_verification(failed=result._implementation_checks_failed)
                if call["id"] in batch_feedback_by_call:
                    observe_batch_feedback(batch_feedback_by_call.pop(call["id"]))
            if observed_change or must_deliver_result:
                # A committed child receipt must also retire earlier source
                # anchors, without changing or projecting the receipt itself.
                invalidated_read_ids.update(delivered_read_ids)
            executed_outcomes: list[bool] = []
            if must_deliver_result:
                # A finalized state change takes a dedicated authority path.
                # Only balance the provider tool protocol and project every
                # result byte-for-byte; mutable telemetry and control sidecars
                # are bypassed until delivery is acknowledged below.
                receipt_start = len(current_messages)
                for result_index, (call, result) in enumerate(_results):
                    suppressed = call["id"] in suppressed_call_ids
                    if not suppressed:
                        executed_outcomes.append(result.ok)
                        if not result.ok:
                            tool_error_count += 1
                    assistant_tool_call_message: ChatMessage = {
                        "role": "assistant_tool_call",
                        "toolUseId": call["id"],
                        "toolName": call["toolName"],
                        "input": call["input"],
                        "assistantTurnId": assistant_tool_turn_id,
                    }
                    if result_index == 0:
                        if next_step.content:
                            assistant_tool_call_message["content"] = (
                                next_step.content
                            )
                        if next_step.reasoningContent is not None:
                            assistant_tool_call_message["reasoningContent"] = (
                                next_step.reasoningContent
                            )
                    current_messages.append(assistant_tool_call_message)
                    current_messages.append(
                        {
                            "role": "tool_result",
                            "toolUseId": call["id"],
                            "toolName": call["toolName"],
                            "content": result.output,
                            "isError": not result.ok,
                        }
                    )
                    if not suppressed:
                        acknowledged = _acknowledge_tool_delivery(result)
                    else:
                        acknowledged = False
                    observe_workflow_delivery(call, result, suppressed=suppressed, acknowledged=acknowledged)
                deliver_batch_feedback()
                if workflow_delivery.budget_terminal:
                    finish_workflow_delivery("workflow_budget_exceeded")
                    return current_messages
                if coding_progress.enabled:
                    try:
                        coding_progress.authoritative_delivery(changed=observed_change)
                    except Exception:
                        # Optional in-memory steering cannot revoke delivered
                        # authority or prevent parent continuation. Even a
                        # logging callback must not join this outcome path.
                        pass
                saw_tool_result = True
                turn_outcome = (
                    "success" if all(executed_outcomes) else "failed"
                )
                continue_parent = all(
                    result._continue_parent and result.ok and not result.awaitUser
                    for _call, result in _results
                    if result._must_deliver
                )
                if continue_parent:
                    try:
                        raise_if_cancelled(cancellation_token)
                        raise_if_deadline_exceeded()
                        if abandoned_event is not None and abandoned_event.is_set():
                            continue_parent = False
                    except (TurnCancellationRequested, AgentTurnDeadlineExceeded):
                        continue_parent = False
                if continue_parent:
                    # Commit authority belongs to this child, not to the
                    # whole user goal. Keep its receipt exact and resume the
                    # parent so remaining tasks and owned async results pass
                    # through the ordinary completion barriers.
                    committed_child_batches.append(
                        tuple(copy.deepcopy(current_messages[receipt_start:]))
                    )
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The workflow changes above are already committed. "
                                "Preserve that receipt and do not repeat the committed "
                                "work. Continue any remaining user-requested tasks, "
                                "collect owned sub-agent results, and then summarize. "
                                "Any calls marked skipped have not executed."
                            ),
                        }
                    )
                    turn_outcome = "unknown"
                    continue
                authority_delivery_ready = True
                return current_messages

            await_user_output: str | None = None
            ledger_changed = False

            for result_index, (call, result) in enumerate(_results):
                suppressed = call["id"] in suppressed_call_ids
                if not suppressed:
                    executed_outcomes.append(result.ok)
                # Deferred AppState updates for tools that ran with store=None
                # (the concurrent batch). Gating on actual batch membership —
                # not on the static tool attribute — avoids double-counting
                # serial executions, which already updated the store.
                if call["id"] in concurrent_call_ids:
                    if store:
                        store.set_state(set_busy(call["toolName"]))
                        store.set_state(increment_tool_calls())
                        store.set_state(set_idle())
                # Hook: post-tool-use
                if not suppressed:
                    fire_hook_sync(
                        HookEvent.POST_TOOL_USE,
                        tool_name=call["toolName"],
                        tool_output=result.output,
                        is_error=not result.ok,
                        step=step,
                    )
                
                saw_tool_result = True
                if suppressed:
                    result_output = result.output
                    recovery_note = None
                elif result._must_deliver:
                    # This output is the sole truthful report of an already
                    # finalized state change. Diagnostics still flow through
                    # counters, hooks, ledger, and execution trace, but the
                    # provider-visible authority document must remain exact.
                    result_output = result.output
                    recovery_note = None
                    if not result.ok:
                        tool_error_count += 1
                elif result._termination_reason is ToolTerminationReason.BUDGET_EXCEEDED:
                    # Host budget terminals are not transient tool failures.
                    # Keep their typed receipt without generic retry advice.
                    tool_error_count += int(not result.ok)
                    result_output = result.output
                    recovery_note = None
                elif not result.ok:
                    tool_error_count += 1
                    # Use ErrorClassifier for intelligent error handling
                    classified = ErrorClassifier.classify(result.output, tool_name=call["toolName"])
                    prior_retries = max(
                        0,
                        recovery_guard.denial_count(call) - 1,
                    )
                    nudge = NudgeGenerator.generate(
                        classified,
                        retry_count=prior_retries,
                    )
                    # Append nudge to tool result content for model context
                    result_output = result.output + "\n\n[System note: " + nudge + "]"
                    recovery_note = nudge
                else:
                    result_output = result.output
                    recovery_note = None
                    # Increased nudge frequency: provide steering even on success
                    if getattr(tool_scheduler, '_force_nudge_frequency', False):
                        success_nudge = (
                            f"Tool '{call['toolName']}' succeeded. "
                            "The system is under stability pressure — prefer smaller, "
                            "incremental steps and verify each result before proceeding."
                        )
                        result_output = result.output + "\n\n[System note: " + success_nudge + "]"

                if task_ledger is not None and not suppressed:
                    normalized_ledger_verification = normalize_tool_verification(
                        call["toolName"],
                        result.verification,
                    )
                    if normalized_ledger_verification is not None:
                        ledger_changed = (
                            task_ledger.record_verification(
                                normalized_ledger_verification
                            )
                            or ledger_changed
                        )
                    if not result.ok:
                        ledger_changed = (
                            task_ledger.record_failed_attempt(
                                call["toolName"],
                                result.output,
                            )
                            or ledger_changed
                        )

                # Record conflicts between concurrent tools if both failed
                if not suppressed and not result.ok and len(calls) > 1:
                    for other_call, other_result in _results:
                        if other_call["id"] == call["id"]:
                            continue
                        if not other_result.ok:
                            tool_scheduler.record_conflict(call["toolName"], other_call["toolName"])

                # ReadDedup: 去重相同文件的重复读取，节省上下文空间
                if (
                    context_compactor
                    and result.ok
                    and call.get("toolName") == "read_file"
                ):
                    file_path = call.get("input", {}).get("path", "")
                    if file_path:
                        dedup_mgr = context_compactor.read_dedup
                        # Register the ORIGINAL content hash: registering the
                        # stub would make the next read of the same file look
                        # like new content and defeat the dedup cache.
                        original_output = result_output
                        if dedup_mgr.should_dedup(file_path, original_output):
                            result_output = dedup_mgr.get_stub(file_path)
                            logger.debug("ReadDedup replaced content for %s (stub)", file_path)
                        # The assistant_tool_call is appended first; the full
                        # read result therefore lives at the following index.
                        dedup_mgr.register_read(
                            file_path,
                            original_output,
                            len(current_messages) + 1,
                        )

                if suppressed:
                    _append_trace_event(
                        execution_trace,
                        {
                            "type": "recovery_suppression",
                            "step": step,
                            "call_id": call["id"],
                            "tool_name": call["toolName"],
                            "reason": "equivalent_denied_action",
                        },
                    )
                else:
                    _append_tool_trace_events(
                        execution_trace,
                        call,
                        result,
                        step,
                        recovery_note=recovery_note,
                    )

                assistant_tool_call_message: ChatMessage = {
                    "role": "assistant_tool_call",
                    "toolUseId": call["id"],
                    "toolName": call["toolName"],
                    "input": call["input"],
                    "assistantTurnId": assistant_tool_turn_id,
                }
                # Text, reasoning, and tool calls are fields of one provider
                # assistant turn.  Splitting text into a preceding assistant
                # message breaks DeepSeek thinking-mode replay and changes the
                # original turn structure for every provider.  Store the text
                # once on the first call in the grouped internal turn.
                if result_index == 0:
                    if next_step.content:
                        assistant_tool_call_message["content"] = next_step.content
                    if next_step.reasoningContent is not None:
                        assistant_tool_call_message["reasoningContent"] = (
                            next_step.reasoningContent
                        )
                current_messages.append(assistant_tool_call_message)
                current_messages.append(
                    {
                        "role": "tool_result",
                        "toolUseId": call["id"],
                        "toolName": call["toolName"],
                        "content": result_output,
                        "isError": not result.ok,
                        **({"_implementation_checks_failed": result._implementation_checks_failed}
                           if not suppressed and type(result._implementation_checks_failed) is bool else {}),
                    }
                )
                if not suppressed:
                    acknowledged = _acknowledge_tool_delivery(result)
                else:
                    acknowledged = False
                observe_workflow_delivery(call, result, suppressed=suppressed, acknowledged=acknowledged)
                if result.awaitUser and await_user_output is None:
                    await_user_output = result_output

            deliver_batch_feedback()
            if workflow_delivery.budget_terminal:
                finish_workflow_delivery("workflow_budget_exceeded")
                return current_messages
            recovery_outcomes = [
                result.ok or submission_correction_progress(call, result)
                for call, result in _results if call["id"] not in suppressed_call_ids
            ]
            strategy_switch_nudge = recovery_guard.complete_step(recovery_outcomes)
            if any(result.ok and result._implementation_submitted and call["toolName"] in {"executor_submit", "review_submit", "completion_review_submit", "completion_probe_migration_submit", "completion_probe_adjudication_submit", "completion_coverage_audit_submit", "completion_coverage_inspection_submit", "subagent_note_write"} for call, result in _results):
                turn_outcome = "success"
                return current_messages
            if host_native_result_tool is not None:
                from minicode.reviewer_result_submission import (
                    reviewer_submission_needs_evidence, reviewer_submission_rejected,
                    reviewer_submission_same_channel,
                )

                submission_results = [
                    (tool, result) for call, result in _results
                    if call["id"] not in suppressed_call_ids
                    for tool in tools.list() if tool.name == call["toolName"]
                    and reviewer_submission_same_channel(tool, host_native_result_tool)
                ]
                needs_evidence = any(reviewer_submission_needs_evidence(tool, result)
                                     for tool, result in submission_results)
                if needs_evidence or any(reviewer_submission_rejected(tool, result)
                                         for tool, result in submission_results):
                    # Formatting feedback needs another submission. Missing
                    # independently checked evidence needs the same Reviewer's
                    # original inspection tools; genuine finite admission still
                    # governs whether another working request can run.
                    native_handoff_only = not needs_evidence
                    native_handoff_attempted = False
                    native_handoff_rejected = True
                    if needs_evidence:
                        native_handoff_prompt_sent = False
            step_tool_call_count = len(executed_outcomes)
            step_tool_error_count = sum(not outcome for outcome in executed_outcomes)
            step_made_progress = observed_change
            successful_reads = {
                call["id"] for call, result in _results
                if call["id"] not in suppressed_call_ids
                and result.ok and call["toolName"] in source_read_names
            }
            delivered_read_ids.update(successful_reads)
            for call, _result in _results:
                if call["id"] in successful_reads and call["toolName"] == "read_file":
                    source_path = call.get("input", {}).get("path")
                    if isinstance(source_path, str):
                        delivered_source_paths[call["id"]] = source_path
            if observed_change:
                # Conservatively retire every previously observed source
                # anchor, including reads from this mixed write/read batch.
                # Later successful reads can establish fresh anchors. This
                # uses the existing byte-change observer, not extra I/O.
                invalidated_read_ids.update(delivered_read_ids)
            coding_progress.observe(
                successful_read=bool(successful_reads), changed=observed_change,
                verified=any(
                    result.ok and normalize_tool_verification(call["toolName"], result.verification)
                    for call, result in _results if call["id"] not in suppressed_call_ids
                ),
            )
            total_tool_call_count += step_tool_call_count
            if step_tool_call_count > 0:
                if any(executed_outcomes):
                    completed_tool_step_count += 1
                else:
                    failed_tool_step_count += 1
            last_step_tool_call_count = step_tool_call_count
            last_step_tool_error_count = step_tool_error_count
            last_step_duration_seconds = max(
                0.0, time.perf_counter() - step_started_at
            )
            if strategy_switch_nudge is not None:
                if not must_deliver_result:
                    for message in reversed(current_messages):
                        if message.get("role") == "tool_result":
                            message["content"] = (
                                str(message.get("content") or "")
                                + "\n\n[System note: "
                                + strategy_switch_nudge
                                + "]"
                            )
                            break
                _append_trace_event(
                    execution_trace,
                    {
                        "type": "recovery_strategy_switch",
                        "step": step,
                        "reason": (
                            "consecutive_command_failures"
                            if strategy_switch_nudge.startswith(
                                "error[command_strategy_switch_required]"
                            )
                            else "consecutive_tool_failures"
                        ),
                    },
                )

            if task_ledger is not None and ledger_changed:
                current_messages = task_ledger.reconcile(current_messages)
                if context_manager is not None:
                    context_manager.messages = list(current_messages)

            # Completed results own their bounded accounting.  Propagate any
            # cancellation requested during POST/store/ledger finalization
            # before recovery or await-user paths can return successfully.
            raise_if_cancelled(cancellation_token)

            recovery_stop = recovery_guard.stop_decision()
            if recovery_stop is not None and not must_deliver_result:
                emit_event_safely(
                    event_sink,
                    "execution.stopped",
                    step=step,
                    payload={
                        "reasonCode": recovery_stop.reason_code,
                        "stepCount": step,
                        "toolErrorCount": tool_error_count,
                        "consecutiveFailedSteps": (
                            recovery_stop.consecutive_failed_steps
                        ),
                        "userActionRequired": recovery_stop.user_action_required,
                    },
                )
                if on_assistant_message:
                    on_assistant_message(recovery_stop.message)
                current_messages.append(
                    {"role": "assistant", "content": recovery_stop.message}
                )
                turn_outcome = "failed"
                raise_if_cancelled(cancellation_token)
                return current_messages

            if await_user_output is not None:
                # Every completed result has been appended above; ending the
                # turn here no longer discards results that finished after
                # the awaiting call.
                if on_assistant_message:
                    on_assistant_message(await_user_output)
                current_messages.append({"role": "assistant", "content": await_user_output})
                if metrics_collector:
                    metrics_collector.end_turn(total_tokens=0)
                turn_outcome = "unknown"
                raise_if_cancelled(cancellation_token)
                return current_messages

            # Keep newly returned results intact for their first model view.
            # Older successful inspection text has an aggregate budget even
            # when the single-request context window is far from full.
            if coding_progress.enabled and (successful_reads or observed_change):
                try:
                    reduced, saved = read_history_budget.compact_read_history(
                        current_messages, eligible_ids=delivered_read_ids,
                        protected_ids=successful_reads,
                        source_paths=delivered_source_paths,
                        invalidated_ids=invalidated_read_ids,
                    )
                    if saved:
                        current_messages = reduced
                        if context_compactor is not None:
                            context_compactor.read_dedup.reconcile(current_messages)
                        if context_manager is not None:
                            context_manager.messages = list(current_messages)
                        emit_event_safely(event_sink, "context.read_history_bounded", step=step, payload={
                            "charactersSaved": saved, "protectedResultCount": len(successful_reads),
                        })
                except (OSError, ValueError):
                    logger.warning("Read history projection unavailable", exc_info=True)

            # In-loop high-water check: one turn can run dozens of tool steps,
            # and pre-request compaction alone lets the context grow unbounded
            # until the API rejects it. Every 10 steps re-check the threshold;
            # the O(n) estimate is cheap compared to a rejected request.
            if (
                step > 0
                and step % 10 == 0
                and context_compactor is not None
                and context_compactor.auto_compact.should_trigger(current_messages)
            ):
                context_operation_id = _new_context_operation_observation(
                    event_sink
                )
                context_messages_before = (
                    observable_message_count(current_messages)
                    if context_operation_id is not None
                    else None
                )
                try:
                    in_loop_result = context_compactor.process_request(
                        current_messages,
                        enable_tool_budget=False,
                    )
                    if in_loop_result.effective:
                        original_messages = list(current_messages)
                        compacted_messages = reconcile_task_ledger(
                            in_loop_result.messages
                        )
                        installed, _memory_ids = (
                            _commit_materialized_compaction_memory(
                                in_loop_result,
                                (
                                    getattr(orch, "memory_pipeline", None)
                                    if orch
                                    else None
                                ),
                                lambda target=current_messages,
                                candidate=compacted_messages: target.__setitem__(
                                    slice(None),
                                    candidate,
                                ),
                                lambda target=current_messages,
                                original=original_messages: target.__setitem__(
                                    slice(None),
                                    original,
                                ),
                                event_sink=event_sink,
                                context_operation_id=context_operation_id,
                            )
                        )
                        if not installed:
                            fallback_messages = (
                                context_compactor.rejection_fallback_messages(
                                    in_loop_result,
                                    original_messages,
                                )
                            )
                            fallback_messages = reconcile_task_ledger(
                                fallback_messages
                            )
                            in_loop_result.memory_free_fallback = (
                                fallback_messages
                            )
                            current_messages[:] = fallback_messages
                        _finalize_compaction_materialization_state(
                            in_loop_result,
                            context_compactor,
                            installed=installed,
                            original_messages=original_messages,
                        )
                        if installed:
                            if context_manager is not None:
                                context_manager.messages = current_messages
                            if (
                                context_operation_id is not None
                                and context_messages_before is not None
                            ):
                                emit_context_compaction_safely(
                                    event_sink,
                                    in_loop_result,
                                    step=step,
                                    context_operation_id=context_operation_id,
                                    path="in_loop_compactor",
                                    messages_before=context_messages_before,
                                    messages_after=observable_message_count(
                                        current_messages
                                    ),
                                )
                            logger.info(
                                "InLoopCompact[%d]: freed %d tokens [%s]",
                                step,
                                in_loop_result.tokens_freed,
                                in_loop_result.strategy.value,
                            )
                        else:
                            if context_manager is not None:
                                context_manager.messages = current_messages
                            in_loop_result.success = False
                            in_loop_result.error = (
                                "Memory authority changed before compaction install"
                            )
                            _emit_failed_context_compaction(
                                event_sink,
                                in_loop_result,
                                context_compactor,
                                path="in_loop_compactor",
                                step=step,
                                context_operation_id=context_operation_id,
                            )
                    else:
                        _emit_failed_context_compaction(
                            event_sink,
                            context_compactor.last_result,
                            context_compactor,
                            path="in_loop_compactor",
                            step=step,
                            context_operation_id=context_operation_id,
                        )
                except TurnCancellationRequested:
                    raise
                except ModelCallDeadlineExceeded as exc:
                    raise AgentTurnDeadlineExceeded(str(exc)) from exc
                except Exception as exc:  # noqa: BLE001 - never break the loop
                    logger.warning("In-loop compaction failed safely: %s", exc)

            # 工具执行完成后的控制论反馈
            if enable_work_chain:
                # 多变量解耦：消除工具间的耦合影响
                if decoupling_controller:
                    decoupling_controller.record_measurement({
                        "token_usage_to_latency": (
                            context_manager.get_stats().usage_percentage / 100.0 if context_manager else 0.0,
                            last_step_duration_seconds / 60.0,
                        ),
                        "context_pressure_to_errors": (
                            context_manager.get_stats().usage_percentage / 100.0 if context_manager else 0.0,
                            tool_error_count / max(step, 1),
                        ),
                    })
                    decoupling_controller.compute_decoupling_matrix()

                if orch:
                    verification_passed, verification_failed = (
                        verification_tracker.snapshot()
                    )
                    tests_passed = (
                        False
                        if verification_failed > 0
                        else True
                        if verification_passed > 0
                        else None
                    )
                    step_summary = orch.step_end(
                        tool_scheduler=tool_scheduler,
                        context_manager=context_manager,
                        step=step,
                        tool_error_count=tool_error_count,
                        saw_tool_result=saw_tool_result,
                        max_steps=max_steps,
                        completed_step_count=completed_tool_step_count,
                        failed_step_count=failed_tool_step_count,
                        tool_call_count=total_tool_call_count,
                        step_made_progress=step_made_progress,
                        elapsed_seconds=max(
                            0.0, time.perf_counter() - turn_started_at
                        ),
                        tests_passed=tests_passed,
                    )
                    previous_step_limit = max_steps
                    max_steps = _apply_control_signal(
                        control_signal=step_summary.get("control_signal"),
                        system_state=step_summary.get("system_state"),
                        max_steps=max_steps,
                        tool_scheduler=tool_scheduler,
                        context_compactor=context_compactor,
                        model_switcher=model_switcher,
                        feedback_controller=feedback_controller,
                        current_messages=current_messages,
                        context_manager=context_manager,
                        event_sink=event_sink,
                    )
                    if completion_policy is not None and max_steps < previous_step_limit:
                        completion_hard_limit = min(completion_hard_limit, max_steps)
                        max_steps = max(0, completion_hard_limit - 1)
                        completion_policy.lower_limit(max_steps)
                    current_messages = reconcile_task_ledger(current_messages)
                    if context_manager is not None:
                        context_manager.messages = list(current_messages)
                else:
                    # 自愈检测：检测并修复故障
                    if self_healing_engine:
                        # Keep the healing engine's message reference fresh —
                        # compaction reassigns current_messages, and a stale
                        # reference makes CONTEXT_OVERFLOW recovery a no-op.
                        self_healing_engine.set_current_messages(current_messages)
                        metrics_for_healing = {
                            "error_rate": tool_error_count / max(step, 1),
                            "context_usage": context_manager.get_stats().usage_percentage / 100.0 if context_manager else 0.0,
                            "oscillation_index": feedback_controller._compute_oscillation() if feedback_controller else 0.0,
                        }
                        healing_actions = self_healing_engine.detect_and_heal(metrics_for_healing)
                        if healing_actions:
                            logger.info("Self-healing triggered: %s", healing_actions[0].strategy)

                    # 进度控制：检测任务是否卡住或完成
                    if progress_controller:
                        progress_signal = ProgressSignal(
                            total_steps=step,
                            completed_steps=completed_tool_step_count,
                            failed_steps=failed_tool_step_count,
                            tool_calls=total_tool_call_count,
                            tool_errors=tool_error_count,
                            output_changed=step_made_progress,
                            tests_passed=(
                                False
                                if verification_tracker.snapshot()[1] > 0
                                else True
                                if verification_tracker.snapshot()[0] > 0
                                else None
                            ),
                            elapsed_seconds=max(
                                0.0, time.perf_counter() - turn_started_at
                            ),
                            max_steps=max_steps,
                        )
                        progress_decision = progress_controller.decide(progress_signal)
                        if progress_decision.action in (ProgressAction.STOP, ProgressAction.REQUEST_CONFIRMATION):
                            logger.warning(
                                "ProgressController: action=%s health=%.2f stall=%.2f reasons=%s",
                                progress_decision.action.value,
                                progress_decision.health_score,
                                progress_decision.stall_score,
                                ", ".join(progress_decision.reasons),
                            )

            # Tool execution completed for this step; ask the model for the next turn
            # instead of falling through to the max-step fallback.
            if metrics_collector:
                total_tokens = sum(
                    estimate_message_tokens(m) for m in current_messages
                ) if context_manager else 0
                metrics_collector.end_turn(total_tokens=total_tokens)
            raise_if_cancelled(cancellation_token)
            continue

        # The while loop only falls through when the step limit fired (every
        # other exit returns or raises). A caller that opted into soft landing
        # — the task sub-agent dispatch — gets one tool-free closing turn to
        # convert the partial survey into a marked partial summary instead of
        # a discarded hard failure.
        if completion_policy is not None:
            finish_bounded_completion(
                "execution step limit reached", step + 1,
                allow_model_summary=not model_timeout_retry_pending,
            )
            return current_messages
        if max_steps_soft_landing and max_steps is not None and host_native_result_tool is None:
            if _attempt_max_steps_soft_landing():
                turn_outcome = "success"
                # A soft landing still ends through a normal assistant
                # message, so the finalized trace (including how the budget
                # was spent) stays eligible for reflection and feedback.
                authority_delivery_ready = True
                return current_messages

        fallback = "Reached the maximum tool step limit for this turn."
        raise_if_cancelled(cancellation_token)
        if on_assistant_message:
            on_assistant_message(fallback)
        current_messages.append({"role": "assistant", "content": fallback})
        turn_outcome = "failed"
        # Budget exhaustion is a complete, finalized trace — the richest
        # lesson source — so it must reach reflection like any failed turn
        # instead of silently skipping the finalization authority.
        authority_delivery_ready = True
        return current_messages
    except AgentTurnDeadlineExceeded:
        turn_outcome = "failed"
        # A partial timed-out run must not write lessons or reinforce injected
        # memories in the finalization path.
        tool_abandoned = True
        enable_work_chain = False
        if committed_child_batches:
            authority_delivery_ready = True
            interrupted_parent_message("the deadline was exceeded")
            return current_messages
        raise
    except TurnCancellationRequested:
        turn_outcome = "cancelled"
        enable_work_chain = False
        if committed_child_batches:
            authority_delivery_ready = True
            interrupted_parent_message("the turn was cancelled")
            return current_messages
        raise
    except (AgentBudgetExceeded, ToolExecutionAbandoned) as error:
        turn_outcome = "failed"
        enable_work_chain = False
        if committed_child_batches:
            authority_delivery_ready = True
            interrupted_parent_message(
                "the shared turn budget was exhausted"
                if isinstance(error, AgentBudgetExceeded)
                else "tool execution was abandoned"
            )
            return current_messages
        raise
    except BaseException as error:
        if not committed_child_batches:
            raise
        turn_outcome = "failed"
        enable_work_chain = False
        authority_delivery_ready = True
        interrupted_parent_message(f"continuation failed ({type(error).__name__})")
        return current_messages
    finally:
        cleanup_original_error = sys.exception()
        try:
            flush_write_feedback_batch()
        except BaseException:
            # Evidence cleanup cannot replace a tool/control-flow exception.
            if cleanup_original_error is None:
                raise
            logger.warning("Interrupted implementation batch cleanup", exc_info=True)
        deliver_batch_feedback()
        if context_manager is not None:
            # The working list can diverge from ContextManager after compaction
            # or tool execution. Preserve terminal observations on cancellation
            # and budget errors as well as on successful returns.
            context_manager.messages = current_messages
        if owned_implementation_feedback is not None:
            owned_implementation_feedback.close()
        if (owned_implementation_feedback is not None
                and (direct_implementation_checks_failed or not direct_completion_accepted)
                and not has_structured_handoff and not committed_child_batches):
            # A model's final answer cannot turn failing host checks into a
            # completed delivery. Keep its wording and actual tool receipts;
            # this separate host receipt closes execution without another call.
            if turn_outcome != "cancelled":
                turn_outcome = "failed"
            enable_work_chain = False
            receipt = {
                "implementationDeliveryVersion": 1,
                "executionEnded": True,
                "delivery": "partial",
                "lastPublicChecks": direct_implementation_batch_status or "unavailable",
                "requirementsAccepted": direct_completion_accepted,
                "continuesAfterResponse": False,
            }
            content = (
                "Host execution receipt: This turn has ended. Current public checks "
                "and requirement coverage have not both been accepted; delivery remains incomplete. No further "
                "edits run after this final response.\n"
                "=== IMPLEMENTATION DELIVERY RECEIPT v1 ===\n"
                + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            )
            current_messages.append({"role": "assistant", "content": content,
                "_partial": True, "_host_implementation_receipt": True})
            if on_assistant_message:
                try:
                    on_assistant_message(content)
                except BaseException:  # presentation cannot mask original failure
                    logger.warning("Failed to present implementation delivery receipt", exc_info=True)
        if implementation_candidate_guard is not None and not committed_child_batches:
            decision = implementation_candidate_guard.delivery_check()
            if not decision.allowed:
                turn_outcome = "failed"
                enable_work_chain = False
                current_messages.append({"role": "assistant", "_partial": True,
                    "content": f"error[{decision.code}]: {decision.message}"})
        restore_committed_child_receipts()
        if committed_child_batches:
            # Once a child has committed, later observers cannot replace the
            # parent's answer or erase the receipt, even on a normal exit.
            authority_delivery_ready = True
            if turn_outcome != "success" and current_messages:
                final_message = current_messages[-1]
                content = final_message.get("content")
                if (
                    final_message.get("role") == "assistant"
                    and not final_message.get("_workflow_delivery_receipt_summary")
                    and isinstance(content, str)
                    and not content.startswith("Earlier workflow changes are already committed;")
                ):
                    final_message["content"] = (
                        "Earlier workflow changes are already committed; remaining work "
                        "is incomplete. The committed receipts are retained above.\n\n"
                        + content
                    )
        if authority_delivery_ready:
            if owns_subagent_lifecycle:
                try:
                    subagent_lifecycle.shutdown()
                except BaseException:  # finalized authority wins
                    logger.warning(
                        "Failed to shut down asynchronous sub-agent lifecycle",
                        exc_info=True,
                    )
            canonical_outcome = None
            try:
                verification_passed_count, verification_failed_count = (
                    verification_tracker.snapshot()
                )
                canonical_outcome = canonicalize_task_outcome(
                    turn_outcome,
                    tool_error_count,
                    verification_passed=verification_passed_count,
                    verification_failed=verification_failed_count,
                )
            except BaseException:  # finalized authority wins
                logger.warning(
                    "Failed to derive finalized task telemetry",
                    exc_info=True,
                )
            if outcome_capture is not None and canonical_outcome is not None:
                try:
                    outcome_capture.record(canonical_outcome)
                except BaseException:  # finalized authority wins
                    logger.warning(
                        "Failed to capture finalized task outcome",
                        exc_info=True,
                    )
            if canonical_outcome is not None:
                try:
                    emit_task_outcome_safely(
                        event_sink,
                        canonical_outcome,
                        step=step,
                    )
                    emit_skill_attribution_safely(
                        event_sink,
                        skill_usage_tracker,
                        canonical_outcome,
                        step=step,
                    )
                except BaseException:  # finalized authority wins
                    logger.warning(
                        "Failed to emit finalized task telemetry",
                        exc_info=True,
                    )
            if orch and not defer_curator_maintenance:
                try:
                    orch.task_end(
                        record_only=True,
                        completion_id=(
                            current_curator_completion_id
                            if agent_depth == 0
                            else None
                        ),
                        agent_depth=agent_depth,
                        agent_budget=agent_budget,
                        event_sink=event_sink,
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                except BaseException:  # finalized authority always wins
                    logger.warning(
                        "Failed to advance curator scheduling after state "
                        "finalization",
                        exc_info=True,
                    )
            try:
                fire_hook_sync(
                    HookEvent.AGENT_STOP,
                    step=step,
                    tool_errors=tool_error_count,
                )
            except BaseException:  # finalized authority wins
                logger.warning(
                    "Failed to run final agent hook after state finalization",
                    exc_info=True,
                )
            if metrics_collector:
                try:
                    total_tokens = (
                        sum(
                            estimate_message_tokens(message)
                            for message in current_messages
                        )
                        if context_manager
                        else 0
                    )
                    metrics_collector.end_turn(total_tokens=total_tokens)
                except BaseException:  # finalized authority wins
                    logger.warning(
                        "Failed to close metrics after state finalization",
                        exc_info=True,
                    )
            # 记忆质量反馈在缩短的权威终结路径上同样必须送达：
            # 预算耗尽的轮次可能刚完成一次压缩安装。
            _deliver_memory_quality_feedback(
                orch=orch,
                canonical_outcome=canonical_outcome,
                tool_abandoned=tool_abandoned,
                verification_passed=verification_passed_count,
                verification_failed=verification_failed_count,
                event_sink=event_sink,
                task_description=(
                    task.raw_input
                    if task is not None and hasattr(task, "raw_input")
                    else None
                ),
                execution_trace=execution_trace or None,
                agent_budget=agent_budget,
            )
            return current_messages
        if owns_subagent_lifecycle:
            try:
                subagent_lifecycle.shutdown()
            except Exception:  # noqa: BLE001 - finalization stays best-effort
                logger.warning(
                    "Failed to shut down asynchronous sub-agent lifecycle",
                    exc_info=True,
                )
        verification_passed_count, verification_failed_count = (
            verification_tracker.snapshot()
        )
        canonical_outcome = canonicalize_task_outcome(
            turn_outcome,
            tool_error_count,
            verification_passed=verification_passed_count,
            verification_failed=verification_failed_count,
        )
        if outcome_capture is not None:
            outcome_capture.record(canonical_outcome)
        emit_task_outcome_safely(
            event_sink,
            canonical_outcome,
            step=step,
        )
        emit_skill_attribution_safely(
            event_sink,
            skill_usage_tracker,
            canonical_outcome,
            step=step,
        )
        fire_hook_sync(HookEvent.AGENT_STOP, step=step, tool_errors=tool_error_count)

        if metrics_collector and metrics_collector._current_turn is not None:
            total_tokens = sum(
                estimate_message_tokens(m) for m in current_messages
            ) if context_manager else 0
            metrics_collector.end_turn(total_tokens=total_tokens)

        if enable_work_chain and task:
            final_state = (
                TaskState.COMPLETED
                if canonical_outcome.completion_succeeded
                else TaskState.CANCELLED
                if canonical_outcome.status == "cancelled"
                else TaskState.FAILED
            )
            task.set_state(final_state)
            task.result_summary = (
                f"Turn {canonical_outcome.status}: {step} steps, "
                f"{tool_error_count} tool errors"
            )

            if auditor:
                outcome = (
                    DecisionOutcome.SUCCESS
                    if canonical_outcome.completion_succeeded
                    else DecisionOutcome.FAILURE
                )
                auditor.complete_decision(
                    outcome,
                    step * 100.0,
                    task.result_summary,
                    task.error_message
                    if not canonical_outcome.completion_succeeded
                    else "",
                )

            logger.info(
                "Work chain completed: task=%s state=%s steps=%d errors=%d",
                task.id, task.state.value, step, tool_error_count,
            )

            # 任务后自省：提取经验教训
            structured_trace = list(execution_trace)
            _append_trace_event(structured_trace, {
                "type": "task_result",
                "step": step,
                "status": canonical_outcome.status,
                "final_outcome": canonical_outcome.status,
                "completion_succeeded": canonical_outcome.completion_succeeded,
                "verification_status": canonical_outcome.verification_status,
                "goal_achieved": canonical_outcome.goal_achieved,
                "had_errors": canonical_outcome.had_tool_errors,
                "errors_recovered": canonical_outcome.errors_recovered,
                "tool_error_count": tool_error_count,
                "summary": _redact_trace_text(task.result_summary),
            })
            if orch and task and not tool_abandoned:
                try:
                    reflection_provenance = {
                        "run_id": (
                            getattr(event_sink, "parent_run_id", None)
                            or getattr(event_sink, "run_id", None)
                        ),
                        "subagent_id": getattr(
                            event_sink,
                            "subagent_id",
                            None,
                        ),
                        "turn_id": next(
                            (
                                message.get("_conversation_turn_id")
                                for message in reversed(current_messages)
                                if message.get("role") == "user"
                                and message.get("_conversation_turn_id")
                            ),
                            None,
                        ),
                        "agent_depth": agent_depth,
                    }
                    written_memory_id = orch.reflect_on_task(
                        task_description=task.raw_input if hasattr(task, 'raw_input') else str(task.id),
                        step=step,
                        tool_error_count=tool_error_count,
                        execution_trace=structured_trace,
                        operation_id=_reflection_memory_operation_id(
                            reflection_provenance
                        ),
                        agent_budget=agent_budget,
                        event_sink=event_sink,
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                        provenance=reflection_provenance,
                    )
                    written_memory_ids = tuple(
                        dict.fromkeys(
                            entry_id
                            for entry_id in getattr(
                                getattr(orch, "memory_pipeline", None),
                                "last_written_ids",
                                (),
                            )
                            if isinstance(entry_id, str) and entry_id
                        )
                    )
                    if not written_memory_ids and isinstance(written_memory_id, str):
                        written_memory_ids = (written_memory_id,)
                    raw_written_memory_versions = getattr(
                        getattr(orch, "memory_pipeline", None),
                        "last_written_versions",
                        (),
                    )
                    written_memory_versions = (
                        _strict_memory_authority_versions(
                            raw_written_memory_versions
                        )
                        or ()
                    )
                    # Publish the exact content authority independently of the
                    # legacy ID callback.  A nested sink binds this batch to
                    # its parent-assigned sub-agent identity; it never merges
                    # nested lifecycle events into the root Run stream.
                    written_provenance = _memory_provenance_batch(
                        effect="written",
                        origin="reflection",
                        authority_versions=raw_written_memory_versions,
                        effect_observed=(
                            bool(written_memory_ids)
                            or (
                                isinstance(
                                    raw_written_memory_versions,
                                    (list, tuple),
                                )
                                and len(raw_written_memory_versions) > 0
                            )
                        ),
                    )
                    if written_provenance is not None:
                        record_memory_provenance_safely(
                            event_sink,
                            written_provenance,
                        )

                    # Keep the narrow ID/version bridges for old embedders.
                    # They remain purely observational and cannot change the
                    # turn outcome.
                    if on_memory_written is not None:
                        for entry_id in written_memory_ids:
                            on_memory_written(entry_id)
                    else:
                        record_written_memory_versions_safely(
                            event_sink,
                            written_memory_versions,
                        )
                        record_written_memory_ids_safely(
                            event_sink, written_memory_ids
                        )
                    _append_pending_memory_notice(
                        current_messages,
                        _count_pending_written_memories(
                            orch, written_memory_ids
                        ),
                        event_sink,
                    )
                except TurnCancellationRequested:
                    raise
                except Exception:
                    pass
            # 记忆质量反馈：只反馈给本轮实际注入的 entry_id。
            _deliver_memory_quality_feedback(
                orch=orch,
                canonical_outcome=canonical_outcome,
                tool_abandoned=tool_abandoned,
                verification_passed=verification_passed_count,
                verification_failed=verification_failed_count,
                event_sink=event_sink,
                task_description=(
                    task.raw_input
                    if task is not None and hasattr(task, "raw_input")
                    else None
                ),
                execution_trace=structured_trace,
                agent_budget=agent_budget,
            )
            # End-of-turn TTL sweep for persisted tool results: long idle
            # sessions never run the compaction pipeline, so the gated
            # cleanup would otherwise never fire for them.
            if context_compactor is not None:
                try:
                    context_compactor.tool_budget.maybe_cleanup_expired()
                except Exception:  # noqa: BLE001 - cleanup stays best-effort
                    logger.debug("Tool-result TTL sweep failed", exc_info=True)

            # 路由反馈学习：记录任务结果以优化未来路由
            routing_model_id = getattr(model, "model_id", None)
            if (
                not isinstance(routing_model_id, str)
                or not routing_model_id.strip()
            ):
                routing_model_id = None
            if (
                smart_router
                and task
                and routing_model_id is not None
                and canonical_outcome.learning_success is not None
                # Parallel sub-agents each own a SmartRouter loaded from the
                # same feedback file; concurrent flushes overwrite each other.
                # Only the top-level turn persists routing feedback.
                and agent_depth == 0
            ):
                try:
                    outcome = TaskOutcome(
                        task_text=task.raw_input if hasattr(task, 'raw_input') else str(task.id),
                        assigned_model=routing_model_id,
                        success=canonical_outcome.learning_success,
                        duration_ms=max(
                            0.0,
                            (time.perf_counter() - turn_started_at) * 1000.0,
                        ),
                        cost_usd=0.0,
                        tool_errors=tool_error_count,
                        model_switches=model_switcher.switch_count if model_switcher else 0,
                    )
                    smart_router.learner.record_outcome(outcome)
                    smart_router.learner.flush()
                except Exception:
                    pass

            if orch and not defer_curator_maintenance:
                try:
                    orch.task_end(
                        completion_id=(
                            current_curator_completion_id
                            if agent_depth == 0
                            else None
                        ),
                        agent_depth=agent_depth,
                        agent_budget=agent_budget,
                        event_sink=event_sink,
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                except TurnCancellationRequested:
                    raise
                except Exception:
                    pass

        # 控制论反馈：记录模式有效性
        if (
            enable_work_chain
            and feedback_controller
            and task
            and canonical_outcome.learning_success is not None
        ):
            pattern_id = f"{task_metadata.get('intent_type', 'unknown')}_{task.id}"
            feedback_controller.record_pattern_effectiveness(
                pattern_id, canonical_outcome.learning_success
            )

        # 稳定性监测：记录快照
        if stability_monitor:
            from minicode.stability_monitor import MetricSnapshot
            snapshot = MetricSnapshot(
                timestamp=time.time(),
                error_rate=float(tool_error_count) / max(step, 1),
                avg_latency=max(
                    0.0, time.perf_counter() - turn_started_at
                ) / max(step, 1),
                context_usage=context_manager.get_stats().usage_percentage if context_manager else 0.0,
                active_tasks=1,
            )
            stability_monitor.record_snapshot(snapshot)
            if context_cybernetics:
                stability_monitor.feed_orchestrator(context_cybernetics)

        # 高级控制论：最终状态报告
        if enable_work_chain:
            # 状态观测器报告
            if state_observer:
                state_summary = state_observer.get_state_summary()
                logger.info("State observer summary: %s", state_summary)

            # 预测控制器报告
            if predictive_controller:
                pred_summary = predictive_controller.get_prediction_summary()
                logger.info("Prediction summary: accuracy=%s", pred_summary.get("accuracy", {}))

            # 自愈引擎统计
            if self_healing_engine:
                healing_stats = self_healing_engine.get_healing_statistics()
                logger.info("Self-healing stats: %s", healing_stats)

            # 多变量解耦状态
            if decoupling_controller:
                coupling_status = decoupling_controller.get_coupling_status()
                logger.info("Coupling status: strong=%s", coupling_status.get("strong_couplings", []))

        # 上下文管理管线统计 (Claude Code-style + Cybernetics)
        if context_compactor:
            compactor_stats = context_compactor.get_stats()
            logger.info(
                "ContextCompactor: passes=%d persisted=%d dedup=%d "
                "microcompact=%d boundaries=%d circuit=%s",
                compactor_stats["total_passes"],
                compactor_stats["tool_results_persisted"],
                compactor_stats["read_dedup_entries"],
                compactor_stats["microcompact_tokens_cleared"],
                compactor_stats["auto_compact_boundaries"],
                "TRIPPED" if compactor_stats["circuit_breaker_tripped"] else "OK",
            )
        # 控制论闭环统计 (Engineering Cybernetics)
        if context_cybernetics:
            cyber_stats = context_cybernetics.get_stats()
            logger.info(
                "Cybernetics: cycles=%d usage=%.1f%% pid_out=%.2f "
                "predict_overflow=%s urgency=%.2f threshold=%.2f feedback_eff=%.0f%%",
                cyber_stats["cycles_executed"],
                (cyber_stats["sensor"]["current_usage"] or 0) * 100,
                cyber_stats["pid"]["last_output"] or 0,
                cyber_stats["predictor"]["turns_until_overflow"],
                cyber_stats["predictor"]["urgency"] or 0,
                cyber_stats["threshold"]["effective_threshold"] or 0,
                (cyber_stats["feedback"]["effectiveness_rate"] or 0) * 100,
            )
        # 成本控制闭环统计 (BudgetPIDController)
        if cost_control:
            cc_stats = cost_control.get_stats()
            adj = cc_stats.get("adjustment")
            logger.info(
                "CostControl: cycles=%d cost/min=$%.4f pid_out=%.2f "
                "budget_mult=%.2f threshold_mult=%.2f [%s]",
                cc_stats["cycles_executed"],
                cc_stats["sensor"]["cost_per_min"],
                cc_stats["pid"]["last_output"] or 1.0,
                adj["budget_mult"] if adj else 1.0,
                adj["threshold_mult"] if adj else 1.0,
                adj["reason"] if adj else "none",
            )
        # 双层 PID 闭环: Cybernetics → FeedbackController
        if context_cybernetics and feedback_controller:
            system_state = context_cybernetics.to_system_state()
            control_signal = feedback_controller.observe(system_state)
            if control_signal.force_compaction and context_cybernetics.enabled:
                logger.info(
                    "Dual-PID: FeedbackController force_compaction=True, "
                    "stability=%.2f performance=%.2f",
                    system_state.stability_score(),
                    system_state.performance_score(),
                )
            # Apply outer-loop ControlSignal to runtime parameters
            if control_signal.confidence > 0.6:
                if (max_steps is not None and control_signal.limit_max_steps
                        and control_signal.limit_max_steps < max_steps):
                    logger.info(
                        "FeedbackController: limiting max_steps %d → %d",
                        max_steps, control_signal.limit_max_steps,
                    )
                    max_steps = control_signal.limit_max_steps
                if control_signal.adjust_token_budget != 1.0:
                    if context_compactor and hasattr(context_compactor, '_tool_budget') and context_compactor._tool_budget:
                        new_budget = max(
                            1000,
                            int(context_compactor._tool_budget.budget_per_message * control_signal.adjust_token_budget),
                        )
                        context_compactor._tool_budget.budget_per_message = new_budget
                        logger.info(
                            "FeedbackController: token budget adjusted to %d (mult=%.2f)",
                            new_budget, control_signal.adjust_token_budget,
                        )
                if control_signal.reduce_parallelism:
                    # Cap tool concurrency at 2
                    if not hasattr(tool_scheduler, '_force_max_workers'):
                        tool_scheduler._force_max_workers = 2
                    logger.info(
                        "FeedbackController: reduce_parallelism → max_workers=2 "
                        "(oscillation=%.2f)", control_signal.oscillation_index,
                    )
                if control_signal.adjust_concurrency != 0:
                    cap = max(1, 4 + control_signal.adjust_concurrency)
                    tool_scheduler._force_max_workers = cap
                    logger.info(
                        "FeedbackController: adjust_concurrency=%+d → max_workers=%d",
                        control_signal.adjust_concurrency, cap,
                    )
                if control_signal.increase_model_level:
                    logger.info(
                        "FeedbackController: model upgrade recommended (errors=%.2f perf=%.2f)",
                        system_state.error_frequency, system_state.performance_score(),
                    )
                    if model_switcher:
                        model_switcher._pending_upgrade = True
                if control_signal.decrease_model_level:
                    logger.info(
                        "FeedbackController: model downgrade recommended (efficiency=%.2f)",
                        system_state.token_efficiency,
                    )
                if control_signal.suggest_memory_persistence:
                    logger.info(
                        "FeedbackController: memory-persistence signal observed; "
                        "durable writes remain gated by task-end reflection"
                    )
                if control_signal.recommend_skill_update:
                    logger.info(
                        "FeedbackController: skill-update signal observed without "
                        "an approved update actuator (pattern=%.2f)",
                        system_state.pattern_reuse_rate,
                    )

                if control_signal.reduce_tool_timeout:
                    new_timeout = max(5.0, control_signal.reduce_tool_timeout)
                    tool_scheduler._force_tool_timeout = new_timeout
                    logger.info(
                        "FeedbackController: tool timeout reduced to %.1fs",
                        new_timeout,
                    )
                elif hasattr(tool_scheduler, '_force_tool_timeout'):
                    del tool_scheduler._force_tool_timeout

                if control_signal.increase_nudge_frequency:
                    tool_scheduler._force_nudge_frequency = True
                    logger.info(
                        "FeedbackController: nudge frequency increased (stability=%.2f)",
                        system_state.stability_score(),
                    )
                elif hasattr(tool_scheduler, '_force_nudge_frequency'):
                    del tool_scheduler._force_nudge_frequency

                if control_signal.promote_pattern:
                    feedback_controller.record_pattern_effectiveness(
                        control_signal.promote_pattern, True
                    )
                    logger.info(
                        "FeedbackController: pattern promoted '%s'",
                        control_signal.promote_pattern,
                    )

                if control_signal.force_compaction and context_compactor:
                    try:
                        compacted = context_compactor.compact_messages(
                            current_messages, force=True
                        )
                        if compacted is not None and len(compacted) < len(current_messages):
                            previous_count = len(current_messages)
                            current_messages[:] = reconcile_task_ledger(compacted)
                            if context_manager is not None:
                                context_manager.messages = list(current_messages)
                            logger.info(
                                "FeedbackController: forced compaction %d → %d messages",
                                previous_count, len(compacted),
                            )
                        else:
                            _emit_failed_context_compaction(
                                event_sink,
                                context_compactor.last_result,
                                context_compactor,
                                path="feedback_forced",
                                step=step,
                            )
                    except TurnCancellationRequested:
                        raise
                    except ModelCallDeadlineExceeded as exc:
                        raise AgentTurnDeadlineExceeded(str(exc)) from exc
                    except Exception as exc:
                        logger.warning("FeedbackController: forced compaction failed: %s", exc)

            # 自适应PID调参：每20轮自动调节内外环PID参数
            if adaptive_pid_tuner and step > 0 and step % 20 == 0 and feedback_controller:
                try:
                    stability_error = 1.0 - system_state.stability_score()
                    perf_score = system_state.performance_score()
                    tuned = adaptive_pid_tuner.tune(
                        stability_error, dt=1.0, performance_score=perf_score
                    )
                    if tuned and adaptive_pid_tuner._performance_history:
                        recent_perf = adaptive_pid_tuner._performance_history[-5:]
                        avg_perf = sum(recent_perf) / len(recent_perf)
                        if context_cybernetics:
                            cp = context_cybernetics.pid
                            cp.kp = tuned.kp
                            cp.ki = tuned.ki
                            cp.kd = tuned.kd
                            logger.info(
                                "AdaptivePIDTuner: context PID tuned kp=%.3f ki=%.3f kd=%.3f "
                                "method=%s perf=%.2f",
                                tuned.kp, tuned.ki, tuned.kd,
                                adaptive_pid_tuner._active_method.value if hasattr(adaptive_pid_tuner, '_active_method') else 'unknown',
                                avg_perf,
                            )
                except Exception:
                    pass  # 调参失败不能拖垮主循环

        # 总监督层: 汇总局部控制器输出为统一风险视图
        if cybernetic_supervisor:
            supervisor_snapshots = []
            if context_cybernetics:
                supervisor_snapshots.append(
                    cybernetic_supervisor.snapshot_from_context(context_cybernetics.get_stats())
                )
            if cost_control:
                supervisor_snapshots.append(
                    cybernetic_supervisor.snapshot_from_cost(cost_control.get_stats())
                )
            if tool_scheduler.last_decision:
                supervisor_snapshots.append(
                    cybernetic_supervisor.snapshot_from_tool_decision(
                        tool_scheduler.last_decision.to_dict()
                    )
                )
            supervisor_report = cybernetic_supervisor.report(supervisor_snapshots)
            save_supervisor_report(supervisor_report)
            logger.info(
                "CyberneticSupervisor: health=%.2f risk=%s actions=%s",
                supervisor_report.overall_health,
                supervisor_report.risk_level.value,
                "; ".join(supervisor_report.recommended_actions[:3]),
            )
        if implementation_checkpoint_sink is not None and owned_implementation_feedback is not None:
            from minicode.implementation_continuation import HostImplementationContinuation
            from copy import deepcopy

            try:
                checkpoint_messages = deepcopy(current_messages)
                if direct_migration_state is not None:
                    from minicode.implementation_execution import implementation_candidate_digest
                    actual_candidate = implementation_candidate_digest(implementation_spec,
                        ToolContext(cwd=cwd, permissions=permissions))
                    if direct_migration_state["state"]["candidateDigest"] != actual_candidate:
                        emit_event_safely(event_sink, "implementation.migration_checkpoint_stale", step=step,
                            payload={"executorId": direct_completion_owner, "reason": "candidate_changed"})
                        direct_migration_state = None
                checkpoint = HostImplementationContinuation.capture(spec=implementation_spec,
                    context=ToolContext(cwd=cwd, permissions=permissions),
                    messages=checkpoint_messages, executor_id=direct_completion_owner,
                    attempt=direct_completion_attempt,
                    review_state=(direct_completion_review.state if direct_completion_review else None),
                    migration_state=direct_migration_state)
                implementation_checkpoint_sink(checkpoint.document, checkpoint_messages)
            except BaseException:
                if cleanup_original_error is None:
                    raise
                logger.warning("Interrupted implementation checkpoint unavailable", exc_info=True)
