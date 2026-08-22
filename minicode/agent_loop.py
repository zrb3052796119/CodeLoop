from __future__ import annotations

import concurrent.futures
import inspect
import re
import time
from typing import Any, Callable

from minicode.context_manager import ContextManager, estimate_message_tokens
from minicode.logging_config import get_logger
from minicode.permissions import PermissionManager
from minicode.pricing import (
    pricing_failure_event_payload,
    project_model_cost_event,
)
from minicode.run_events import (
    AgentEventSink,
    SkillUsageTracker,
    VerificationTracker,
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
)
from minicode.state import Store, AppState, increment_tool_calls, set_busy, set_idle
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
    raise_if_cancelled,
)
from minicode.types import AgentStep, ChatMessage, ModelAdapter
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
from minicode.intent_parser import parse_intent
from minicode.task_object import build_task, TaskObject, TaskState
from minicode.task_outcome import canonicalize_task_outcome
from minicode.pipeline_engine import get_pipeline_engine
from minicode.capability_registry import register_tool_capabilities
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
)
from minicode.context_cybernetics import ContextCyberneticsOrchestrator
from minicode.cost_control import CostControlLoop
from minicode.memory import MemoryManager

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

_TRACE_MAX_EVENTS = 500
_TRACE_MAX_FIELD_CHARS = 600
_TRACE_MAX_LIST_ITEMS = 12
_MAX_MODEL_TOOL_CALL_COUNT = 1_000
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
    redacted = re.sub(
        r"(?i)(api[_-]?key|secret[_-]?key|token|secret|password|credential|authorization)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    redacted = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", redacted)
    if len(redacted) > limit:
        return redacted[:limit] + "...[truncated]"
    return redacted


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


def _is_empty_assistant_response(content: str) -> bool:
    return len(content.strip()) == 0


def _extract_task_description(messages: list[ChatMessage]) -> str:
    """Extract the original task description from messages."""
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            content = str(msg["content"])
            if not content.startswith("Continue") and not content.startswith("Your last"):
                return content[:500]
    return "Unknown task"


def _build_work_chain_task(messages: list[ChatMessage]) -> tuple[TaskObject | None, dict]:
    """Build TaskObject from conversation messages and return it with metadata."""
    raw_input = _extract_task_description(messages)
    if raw_input == "Unknown task":
        return None, {}
    intent = parse_intent(raw_input)
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


def _register_tool_capabilities(tools: ToolRegistry) -> None:
    """Register existing tools as capabilities in the registry."""
    register_tool_capabilities(tools)


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
    
    try:
        raise_if_cancelled(cancellation_token)
        # Pre-tool hooks and UI (only for serial execution)
        if on_tool_start:
            on_tool_start(tool_name, tool_input)
        
        if store:
            store.set_state(set_busy(tool_name))
        
        # Execute the tool with timeout protection
        import concurrent.futures
        import contextvars
        import os
        _base_timeout = int(os.environ.get("MINICODE_TOOL_TIMEOUT", "120"))
        TOOL_TIMEOUT = (
            int(getattr(tool_scheduler, '_force_tool_timeout', _base_timeout))
            if tool_scheduler and hasattr(tool_scheduler, '_force_tool_timeout')
            else _base_timeout
        )
        def execute_tool() -> ToolResult:
            raise_if_cancelled(cancellation_token)
            executed = tools.execute(
                tool_name, tool_input,
                ToolContext(
                    cwd=cwd,
                    permissions=permissions,
                    _runtime=runtime,
                    _event_sink=event_sink,
                    _step=step,
                    _skill_usage_tracker=skill_usage_tracker,
                    _cancellation_token=cancellation_token,
                    _agent_depth=agent_depth,
                    _presentation=presentation,
                ),
            )
            raise_if_cancelled(cancellation_token)
            return executed

        execution_context = contextvars.copy_context()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(execution_context.run, execute_tool)
            try:
                result = future.result(timeout=TOOL_TIMEOUT)
            except concurrent.futures.TimeoutError:
                result = ToolResult(
                    ok=False,
                    output=f"Tool '{tool_name}' timed out after {TOOL_TIMEOUT}s",
                )
        finally:
            # Never block on a possibly-hung worker thread: a `with` block
            # (or wait=True) would call shutdown(wait=True) and stall until
            # the tool actually finishes, defeating the timeout entirely.
            # The abandoned thread cannot be killed, but the loop moves on.
            pool.shutdown(wait=False, cancel_futures=True)
        # Any exception raised by execute_tool() propagates: cancellation is
        # re-raised below, everything else is converted to an error result by
        # the outer safety net. (The previous blind `execute_tool()` fallback
        # re-ran tools with side effects a second time.)

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
    
    except (KeyboardInterrupt, SystemExit, TurnCancellationRequested):
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
        
        logger.error("Tool execution pipeline crashed (%s): %s", error_type, exc)
        
        # Ensure state is reset even on crash
        if store:
            try:
                store.set_state(set_idle())
            except Exception:
                pass
        
        return ToolResult(
            ok=False,
            output=f"[{error_type}] Tool execution pipeline crashed: {exc}\n"
                   f"Traceback:\n{tb_excerpt}"
        )


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


def _should_treat_assistant_as_progress(*, kind: str | None, content: str, saw_tool_result: bool) -> bool:
    if kind == "progress":
        return True
    if kind == "final":
        return False
    if not saw_tool_result:
        return False
    return False


def _model_next(
    model: ModelAdapter,
    messages: list[ChatMessage],
    *,
    on_stream_chunk: Callable[[str], None] | None,
    on_thinking_chunk: Callable[[str], None] | None = None,
    store: Store[AppState] | None,
) -> AgentStep:
    """Call provider adapters with store/thinking support while preserving test doubles."""
    kwargs: dict[str, Any] = {"on_stream_chunk": on_stream_chunk}

    try:
        sig = inspect.signature(model.next)
        param_names = set(sig.parameters.keys())
        has_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if has_kwargs or "on_thinking_delta" in param_names:
            kwargs["on_thinking_delta"] = on_thinking_chunk
        if has_kwargs or "store" in param_names:
            kwargs["store"] = store
    except (TypeError, ValueError):
        # Can't inspect signature (e.g. some mock objects) — be conservative
        pass

    return model.next(messages, **kwargs)


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


def _apply_control_signal(
    *,
    control_signal: Any,
    system_state: Any,
    max_steps: int | None,
    tool_scheduler: ToolScheduler,
    context_compactor: ContextCompactor | None,
    model_switcher: Any | None,
    feedback_controller: Any | None = None,
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

    if control_signal.force_compaction and context_compactor:
        try:
            compacted = context_compactor.compact_messages()
            logger.info(
                "FeedbackController: forced compaction completed (%d messages)",
                len(compacted) if compacted else 0,
            )
        except Exception as exc:
            logger.warning("FeedbackController: forced compaction failed: %s", exc)

    return max_steps


def run_agent_turn(
    *,
    model: ModelAdapter,
    tools: ToolRegistry,
    messages: list[ChatMessage],
    cwd: str,
    permissions: PermissionManager | None = None,
    store: Store[AppState] | None = None,
    max_steps: int = 50,
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
    cancellation_token: TurnCancellationToken | None = None,
    agent_depth: int = 0,
    presentation: Any | None = None,
) -> list[ChatMessage]:
    current_messages = list(messages)
    saw_tool_result = False
    empty_response_retry_count = 0
    recoverable_thinking_retry_count = 0
    tool_error_count = 0
    recovery_guard = RecoveryGuard()
    turn_started_at = time.perf_counter()
    last_step_duration_seconds = 0.0
    last_step_tool_call_count = 0
    last_step_tool_error_count = 0
    total_tool_call_count = 0
    completed_tool_step_count = 0
    failed_tool_step_count = 0
    step = 0
    turn_outcome = "unknown"
    execution_trace: list[dict[str, Any]] = []
    memory_mgr = memory_manager

    tool_scheduler = ToolScheduler(metrics_collector=metrics_collector)
    skill_usage_tracker = SkillUsageTracker() if event_sink is not None else None
    verification_tracker = VerificationTracker()

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
    reflection_engine: Any = None
    model_switcher: Any = None
    context_compactor: ContextCompactor | None = None
    context_cybernetics: ContextCyberneticsOrchestrator | None = None
    cost_control: CostControlLoop | None = None

    if enable_work_chain:
        task, task_metadata = _build_work_chain_task(current_messages)
        layered_context, context_builder = _build_layered_context(
            current_messages, system_prompt, project_context, task,
        )
        get_pipeline_engine()
        _register_tool_capabilities(tools)

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
        reflection_engine = orch.reflection
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
                max_steps = min(max_steps, preemptive_config.max_turn_steps)
                logger.info(
                    "Feedforward: max_steps=%d model=%s timeout=%.1fs",
                    preemptive_config.max_turn_steps,
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
            if reflection_engine:
                reflection_engine.memory = memory_mgr
            if orch:
                orch._last_model = model
                orch._workspace = cwd
                orch.wire_memory(memory_mgr)
                if task:
                    try:
                        task_desc = task.raw_input if hasattr(task, "raw_input") else ""
                        current_files = list(getattr(task, "relevant_files", []) or [])
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
                        )
                        if event_sink is not None:
                            emit_memory_result_safely(
                                event_sink,
                                orch.memory_pipeline.last_retrieval_result,
                            )
                    except Exception as exc:
                        logger.warning("Memory injection failed safely: %s", exc)
        if context_manager:
            compact_config = AutoCompactConfig(
                threshold_ratio=0.85,
                circuit_breaker_limit=3,
                session_memory_enabled=True,
            )
            context_compactor = ContextCompactor(
                context_window=context_manager.context_window,
                workspace=cwd,
                memory_manager=memory_mgr,
                estimate_fn=estimate_message_tokens,
                config=compact_config,
            )
            context_cybernetics = ContextCyberneticsOrchestrator(
                context_compactor,
                kp=2.0, ki=0.15, kd=0.3,
                pid_setpoint=0.70,
                base_threshold=0.85,
                safety_margin_turns=3,
                enabled=True,
            )
            if task and hasattr(task, 'parsed_intent') and task.parsed_intent:
                context_cybernetics.set_intent(str(task.parsed_intent.intent_type))
            logger.info("ContextCybernetics initialized: PID control loop + predictive guard")
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

            context_messages_before = (
                len(current_messages) if event_sink is not None else None
            )
            cyber_messages, cyber_result, cyber_action = context_cybernetics.run_cycle(
                current_messages,
                error_rate=float(tool_error_count) / max(step, 1) if step > 0 else 0.0,
                avg_latency=step * 2.0,
                turn_id=step,
            )
            if cyber_result and cyber_result.effective:
                current_messages = cyber_messages
                context_manager.messages = current_messages
                context_operation_id = _new_context_operation_observation(event_sink)
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
                        messages_after=len(current_messages),
                    )
                logger.info(
                    "Cybernetics[%s]: %s intensity=%.2f freed=%d tokens [%s]",
                    cyber_action.reason if cyber_action else "unknown",
                    cyber_result.strategy.value,
                    cyber_action.compaction_intensity if cyber_action else 0,
                    cyber_result.tokens_freed,
                    cyber_result.summary_text[:80] if cyber_result.summary_text else "",
                )
        elif context_compactor:
            context_messages_before = (
                len(current_messages) if event_sink is not None else None
            )
            compaction_result = context_compactor.process_request(current_messages)
            if compaction_result.effective:
                current_messages = compaction_result.messages
                context_manager.messages = current_messages
                context_operation_id = _new_context_operation_observation(event_sink)
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
                        messages_after=len(current_messages),
                    )
                logger.info(
                    "ContextCompactor: %s freed %d tokens [%s]",
                    compaction_result.strategy.value,
                    compaction_result.tokens_freed,
                    compaction_result.summary_text[:80],
                )
        elif context_manager.should_auto_compact():
            logger.warning("Context near limit, auto-compacting...")
            context_messages_before = current_messages if event_sink is not None else None
            context_messages_before_count = (
                len(current_messages) if event_sink is not None else None
            )
            current_messages = context_manager.compact_messages()
            if (
                context_messages_before is not None
                and context_messages_before_count is not None
                and current_messages != context_messages_before
            ):
                context_operation_id = _new_context_operation_observation(event_sink)
                if context_operation_id is not None:
                    emit_context_compaction_safely(
                        event_sink,
                        None,
                        context_operation_id=context_operation_id,
                        path="context_manager_auto",
                        trigger="auto",
                        strategy="context_manager",
                        messages_before=context_messages_before_count,
                        messages_after=len(current_messages),
                    )
            if on_assistant_message:
                on_assistant_message(context_manager.get_context_summary())

    try:
        while max_steps is None or step < max_steps:
            raise_if_cancelled(cancellation_token)
            step += 1
            step_started_at = time.perf_counter()

            # Hook: agent turn started
            fire_hook_sync(HookEvent.AGENT_START, step=step, cwd=cwd)

            # 高级控制论闭环（每个 step 开始时执行）
            if enable_work_chain and orch:
                orch.step_start(
                    context_manager=context_manager,
                    step=step,
                    tool_error_count=tool_error_count,
                    saw_tool_result=saw_tool_result,
                    response_time_seconds=last_step_duration_seconds,
                    recent_tool_error_count=last_step_tool_error_count,
                    recent_tool_call_count=last_step_tool_call_count,
                )
            elif enable_work_chain:
                # 状态观测：通过可测量输出估计系统内部状态
                if state_observer:
                    measurement = MeasurementVector(
                        timestamp=time.time(),
                        response_time=step * 2.0,  # 估算响应时间
                        success_rate=1.0 - (tool_error_count / max(step, 1)),
                        context_length=context_manager.get_stats().total_tokens if context_manager else 0,
                        error_count=tool_error_count,
                        tool_calls=0,
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
                            dispatch: dict[str, Callable[[], None]] = {
                                "trigger_compaction": lambda: (
                                    context_cybernetics.try_reactive_recover(current_messages, "predictive")
                                    if context_cybernetics else None
                                ),
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

            if metrics_collector:
                metrics_collector.start_turn(step)

            next_step: AgentStep
            model_operation_id = (
                new_model_operation_id() if event_sink is not None else ""
            )
            if event_sink is not None:
                emit_event_safely(
                    event_sink,
                    "model.started",
                    step=step,
                    payload={"operationId": model_operation_id},
                )
            model_started_at = (
                _start_model_observation_clock()
                if event_sink is not None
                else None
            )
            try:
                raise_if_cancelled(cancellation_token)
                next_step = _model_next(
                    model,
                    current_messages,
                    on_stream_chunk=on_assistant_stream_chunk,
                    on_thinking_chunk=on_thinking_chunk,
                    store=store,
                )
                raise_if_cancelled(cancellation_token)
            except TurnCancellationRequested:
                raise
            except (KeyboardInterrupt, SystemExit):
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
            except ConnectionError as error:
                raise_if_cancelled(cancellation_token)
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
                                "failureKind": "network",
                            },
                            model_duration_ms,
                        ),
                    )
                turn_outcome = "failed"
                fallback = f"Network error (connection failed or dropped): {error}"
                logger.error("Model API connection error: %s", error)
                if on_assistant_message:
                    on_assistant_message(fallback)
                current_messages.append({"role": "assistant", "content": fallback})
                if metrics_collector:
                    metrics_collector.end_turn(total_tokens=0)
                return current_messages
            except TimeoutError as error:
                raise_if_cancelled(cancellation_token)
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
                                "failureKind": "timeout",
                            },
                            model_duration_ms,
                        ),
                    )
                turn_outcome = "failed"
                fallback = f"Model API timeout: {error}"
                logger.error("Model API timeout: %s", error)
                if on_assistant_message:
                    on_assistant_message(fallback)
                current_messages.append({"role": "assistant", "content": fallback})
                if metrics_collector:
                    metrics_collector.end_turn(total_tokens=0)
                return current_messages
            except Exception as error:
                raise_if_cancelled(cancellation_token)
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
                                "failureKind": "provider_error",
                            },
                            model_duration_ms,
                        ),
                    )
                # Catch-all for unexpected errors (rate limit, auth, server 5xx, etc.)
                error_type = type(error).__name__
                fallback = f"Model API error ({error_type}): {error}"
                logger.error("Model API error (%s): %s", error_type, error)

                # Reactive Compact: 控制论恢复路径
                error_str = str(error).lower()
                needs_recovery = "prompt" in error_str and ("too long" in error_str or "exceeds" in error_str)
                if context_cybernetics and needs_recovery:
                    context_operation_id = _new_context_operation_observation(
                        event_sink
                    )
                    recovery_messages_before = (
                        len(current_messages)
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
                        current_messages = recovered_messages
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
                                messages_after=len(current_messages),
                            )
                            emit_recovery_completed_safely(
                                event_sink,
                                recovery_result,
                                step=step,
                                context_operation_id=context_operation_id,
                                kind="cybernetic",
                                messages_before=recovery_messages_before,
                                messages_after=len(current_messages),
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
                            messages_after=len(recovered_messages),
                        )
                elif context_compactor and needs_recovery:
                    context_operation_id = _new_context_operation_observation(
                        event_sink
                    )
                    recovery_messages_before = (
                        len(current_messages)
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
                        current_messages = recovery_result.messages
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
                                messages_after=len(current_messages),
                            )
                            emit_recovery_completed_safely(
                                event_sink,
                                recovery_result,
                                step=step,
                                context_operation_id=context_operation_id,
                                kind="compactor",
                                messages_before=recovery_messages_before,
                                messages_after=len(current_messages),
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
            if event_sink is not None:
                usage_observation = _model_usage_observation(next_step)
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
                cost_payload = _model_cost_observation(
                    model,
                    usage_observation,
                    model_operation_id,
                )
                if cost_payload is not None:
                    emit_event_safely(
                        event_sink,
                        "model.costed",
                        step=step,
                        payload=cost_payload,
                    )

            if next_step.type == "assistant":
                is_empty = _is_empty_assistant_response(next_step.content)
                if not is_empty and _should_treat_assistant_as_progress(
                    kind=getattr(next_step, 'kind', None),
                    content=next_step.content,
                    saw_tool_result=saw_tool_result,
                ):
                    _append_trace_event(execution_trace, {
                        "type": "assistant_step",
                        "step": step,
                        "content_kind": getattr(next_step, 'kind', None) or "progress",
                        "content": _redact_trace_text(next_step.content),
                    })
                    if on_progress_message:
                        on_progress_message(next_step.content)
                    current_messages.append({"role": "assistant_progress", "content": next_step.content})
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                NUDGE_AFTER_TOOL_RESULT
                                if saw_tool_result and getattr(next_step, 'kind', None) != "progress"
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
                    current_messages.append({"role": role, "content": next_step.content})

            if not next_step.calls and next_step.content and next_step.contentKind != "progress":
                raise_if_cancelled(cancellation_token)
                turn_outcome = "success"
                return current_messages

            # --- Concurrent tool execution ---
            # Classify calls into concurrent-safe (read-only) vs serial (writes/commands)
            calls = next_step.calls
            _results: list[tuple[dict, ToolResult]] = []
            suppressed_call_ids: set[str] = set()
            executed_outcomes: list[bool] = []
            raise_if_cancelled(cancellation_token)

            executable_calls: list[dict] = []
            for call in calls:
                suppression = recovery_guard.suppression_for(call)
                if suppression is None:
                    executable_calls.append(call)
                    continue
                suppressed_call_ids.add(call["id"])
                _results.append(
                    (call, ToolResult(ok=False, output=suppression.message))
                )

            if len(executable_calls) == 1:
                # Single call — no benefit from concurrency, run directly
                call = executable_calls[0]
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
                )
                if metrics_collector:
                    metrics_collector.end_tool(
                        success=result.ok,
                        error=result.output if not result.ok else "",
                    )
                recovery_guard.observe(call, ok=result.ok, output=result.output)
                executed_outcomes.append(result.ok)
                _results.append((call, result))
            elif len(executable_calls) > 1:
                # Multiple calls — use ToolScheduler for intelligent partitioning
                concurrent_calls, serial_calls = tool_scheduler.schedule_calls(
                    executable_calls, tools
                )

                # Phase 1: Run all concurrent-safe tools in parallel
                if concurrent_calls:
                    raise_if_cancelled(cancellation_token)
                    max_workers = tool_scheduler.get_recommended_max_workers(
                        concurrent_calls,
                        error_rate=tool_error_count / max(step, 1),
                        avg_latency=step * 2.0,
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
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=max_workers,
                        thread_name_prefix="mc-tool",
                    ) as pool:
                        future_to_call = {
                            pool.submit(
                                _execute_single_tool,
                                call, tools, cwd, permissions, runtime, None, step,
                                on_tool_start, on_tool_result, tool_scheduler, event_sink,
                                skill_usage_tracker,
                                cancellation_token,
                                verification_tracker,
                                agent_depth,
                                presentation,
                            ): call
                            for call in concurrent_calls
                        }
                        for future in concurrent.futures.as_completed(future_to_call):
                            call = future_to_call[future]
                            try:
                                result = future.result()
                            except TurnCancellationRequested:
                                raise
                            except Exception as exc:
                                result = ToolResult(ok=False, output=f"Concurrent execution error: {exc}")
                            recovery_guard.observe(
                                call, ok=result.ok, output=result.output
                            )
                            executed_outcomes.append(result.ok)
                            _results.append((call, result))

                # Phase 2: Run serial tools sequentially (in original order)
                if serial_calls:
                    for call in serial_calls:
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
                            continue
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
                        )
                        if metrics_collector:
                            metrics_collector.end_tool(
                                success=result.ok,
                                error=result.output if not result.ok else "",
                            )
                        recovery_guard.observe(
                            call, ok=result.ok, output=result.output
                        )
                        executed_outcomes.append(result.ok)
                        _results.append((call, result))
                        # If a serial tool awaits user, return immediately
                        if result.awaitUser:
                            # Still need to process remaining results for messages
                            break
            
            raise_if_cancelled(cancellation_token)

            # Process all results and build messages (preserve original call order)
            call_order = {call["id"]: idx for idx, call in enumerate(calls)}
            _results.sort(key=lambda pair: call_order.get(pair[0]["id"], 999))
            
            for call, result in _results:
                was_suppressed = call["id"] in suppressed_call_ids
                # Fire hooks and UI callbacks for concurrent calls (deferred)
                tool_def = tools.find(call["toolName"])
                is_concurrent = tool_def and tool_def.is_concurrency_safe and len(calls) > 1
                
                if is_concurrent and not was_suppressed:
                    # Deferred UI callbacks for concurrent tools
                    if store:
                        store.set_state(set_busy(call["toolName"]))
                        store.set_state(increment_tool_calls())
                        store.set_state(set_idle())
                    # Hook: pre-tool-use (fire after the fact for concurrent tools)
                    fire_hook_sync(
                        HookEvent.PRE_TOOL_USE,
                        tool_name=call["toolName"],
                        tool_input=call["input"],
                        step=step,
                    )
                
                if not was_suppressed:
                    # Hook: post-tool-use
                    fire_hook_sync(
                        HookEvent.POST_TOOL_USE,
                        tool_name=call["toolName"],
                        tool_output=result.output,
                        is_error=not result.ok,
                        step=step,
                    )
                
                saw_tool_result = True
                if was_suppressed:
                    result_output = result.output
                    recovery_note = None
                elif not result.ok:
                    tool_error_count += 1
                    # Use ErrorClassifier for intelligent error handling
                    classified = ErrorClassifier.classify(result.output, tool_name=call["toolName"])
                    prior_retries = max(
                        0, recovery_guard.denial_count(call) - 1
                    )
                    nudge = NudgeGenerator.generate(
                        classified, retry_count=prior_retries
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

                # Record conflicts between concurrent tools if both failed
                if not result.ok and len(calls) > 1:
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
                        if dedup_mgr.should_dedup(file_path, result_output):
                            result_output = dedup_mgr.get_stub(file_path)
                            logger.debug("ReadDedup replaced content for %s (stub)", file_path)
                        dedup_mgr.register_read(file_path, result_output, len(current_messages))

                if not was_suppressed:
                    _append_tool_trace_events(
                        execution_trace,
                        call,
                        result,
                        step,
                        recovery_note=recovery_note,
                    )

                current_messages.append(
                    {
                        "role": "assistant_tool_call",
                        "toolUseId": call["id"],
                        "toolName": call["toolName"],
                        "input": call["input"],
                    }
                )
                current_messages.append(
                    {
                        "role": "tool_result",
                        "toolUseId": call["id"],
                        "toolName": call["toolName"],
                        "content": result_output,
                        "isError": not result.ok,
                    }
                )
                if result.awaitUser:
                    if on_assistant_message:
                        on_assistant_message(result_output)
                    current_messages.append({"role": "assistant", "content": result_output})
                    if metrics_collector:
                        metrics_collector.end_turn(total_tokens=0)
                    turn_outcome = "unknown"
                    return current_messages

            strategy_switch_nudge = recovery_guard.complete_step(executed_outcomes)
            step_tool_call_count = len(executed_outcomes)
            step_tool_error_count = sum(not outcome for outcome in executed_outcomes)
            step_made_progress = any(executed_outcomes)
            total_tool_call_count += step_tool_call_count
            if step_tool_call_count > 0:
                if step_made_progress:
                    completed_tool_step_count += 1
                else:
                    failed_tool_step_count += 1
            last_step_tool_call_count = step_tool_call_count
            last_step_tool_error_count = step_tool_error_count
            last_step_duration_seconds = max(
                0.0, time.perf_counter() - step_started_at
            )

            if strategy_switch_nudge is not None:
                for message in reversed(current_messages):
                    if message.get("role") == "tool_result":
                        message["content"] = (
                            str(message.get("content") or "")
                            + "\n\n[System note: "
                            + strategy_switch_nudge
                            + "]"
                        )
                        break

            recovery_stop = recovery_guard.stop_decision()
            if recovery_stop is not None:
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
                return current_messages

            # 工具执行完成后的控制论反馈
            if enable_work_chain:
                # 多变量解耦：消除工具间的耦合影响
                if decoupling_controller:
                    decoupling_controller.record_measurement({
                        "token_usage_to_latency": (
                            context_manager.get_stats().usage_percentage / 100.0 if context_manager else 0.0,
                            step * 2.0 / 60.0,
                        ),
                        "context_pressure_to_errors": (
                            context_manager.get_stats().usage_percentage / 100.0 if context_manager else 0.0,
                            tool_error_count / max(step, 1),
                        ),
                    })
                    decoupling_controller.compute_decoupling_matrix()

                if orch:
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
                        tests_passed=(
                            verification_tracker.snapshot()[1] == 0
                            if verification_tracker.snapshot()[0] > 0
                            else None
                        ),
                    )
                    max_steps = _apply_control_signal(
                        control_signal=step_summary.get("control_signal"),
                        system_state=step_summary.get("system_state"),
                        max_steps=max_steps,
                        tool_scheduler=tool_scheduler,
                        context_compactor=context_compactor,
                        model_switcher=model_switcher,
                        feedback_controller=feedback_controller,
                    )
                else:
                    # 自愈检测：检测并修复故障
                    if self_healing_engine:
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
                            total_steps=max_steps,
                            completed_steps=step - tool_error_count,
                            failed_steps=tool_error_count,
                            tool_calls=step,
                            tool_errors=tool_error_count,
                            output_changed=saw_tool_result,
                            elapsed_seconds=step * 2.0,
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

        fallback = "Reached the maximum tool step limit for this turn."
        raise_if_cancelled(cancellation_token)
        if on_assistant_message:
            on_assistant_message(fallback)
        current_messages.append({"role": "assistant", "content": fallback})
        turn_outcome = "failed"
        return current_messages
    except TurnCancellationRequested:
        turn_outcome = "cancelled"
        enable_work_chain = False
        raise
    finally:
        canonical_outcome = canonicalize_task_outcome(
            turn_outcome,
            tool_error_count,
        )
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
                if canonical_outcome.goal_achieved
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
                    if canonical_outcome.goal_achieved
                    else DecisionOutcome.FAILURE
                )
                auditor.complete_decision(
                    outcome,
                    step * 100.0,
                    task.result_summary,
                    task.error_message
                    if not canonical_outcome.goal_achieved
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
                "had_errors": canonical_outcome.had_tool_errors,
                "errors_recovered": canonical_outcome.errors_recovered,
                "tool_error_count": tool_error_count,
                "summary": _redact_trace_text(task.result_summary),
            })
            if orch and task:
                try:
                    orch.reflect_on_task(
                        task_description=task.raw_input if hasattr(task, 'raw_input') else str(task.id),
                        step=step,
                        tool_error_count=tool_error_count,
                        execution_trace=structured_trace,
                    )
                except Exception:
                    pass
            elif reflection_engine and task:
                try:
                    reflection = reflection_engine.reflect(
                        task_description=task.raw_input if hasattr(task, 'raw_input') else str(task.id),
                        execution_trace=structured_trace,
                    )
                    logger.info(
                        "AgentReflection: success=%s confidence=%.2f lessons=%d improvements=%d",
                        reflection.success, reflection.confidence,
                        len(reflection.lessons_learned), len(reflection.suggested_improvements),
                    )
                except Exception:
                    pass

            # 记忆质量反馈：只反馈给本轮实际注入的 entry_id。
            if orch and getattr(orch, "memory_pipeline", None) is not None:
                try:
                    _verification_passed, _verification_failed = (
                        verification_tracker.snapshot()
                    )
                    orch.memory_pipeline.feedback(
                        canonical_outcome.status,
                        verification_passed=_verification_passed,
                        verification_failed=_verification_failed,
                    )
                except Exception:
                    pass

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
            ):
                try:
                    outcome = TaskOutcome(
                        task_text=task.raw_input if hasattr(task, 'raw_input') else str(task.id),
                        assigned_model=routing_model_id,
                        success=canonical_outcome.learning_success,
                        duration_ms=step * 2000.0,
                        cost_usd=0.0,
                        tool_errors=tool_error_count,
                        model_switches=model_switcher.switch_count if model_switcher else 0,
                    )
                    smart_router.learner.record_outcome(outcome)
                    smart_router.learner.flush()
                except Exception:
                    pass

            if orch:
                try:
                    orch.task_end()
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
                avg_latency=step * 2.0,  # 简化估算
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
                if control_signal.limit_max_steps and control_signal.limit_max_steps < max_steps:
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
                        compacted = context_compactor.compact_messages()
                        logger.info(
                            "FeedbackController: forced compaction (%d messages)",
                            len(compacted) if compacted else 0,
                        )
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
