"""Subprocess worker entry point for write-capable ``general`` sub-agents.

A write-capable sub-agent cannot be interrupted reliably inside the parent
process: Python cannot kill a thread stuck in a provider HTTP call, so an
in-process timeout would leave the worker burning model budget and mutating
files after the parent moved on. Running the nested loop in a child process
gives the parent a hard boundary — the supervisor kills the whole process
group on deadline or cancellation, and a dead process cannot write late.

Protocol: the parent writes a request JSON file and spawns
``python -m minicode.subagent_worker --request <path> --result <path>``.
The worker rebuilds a non-interactive runtime from the serialized request
and writes exactly one result JSON file (atomically) before exiting. The
result never travels over stdout because models and tools may print there.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from minicode.agent_loop import run_agent_turn

REQUEST_VERSION = 2
_LEGACY_REQUEST_VERSION = 1
RESULT_VERSION = 1

_RESULT_STATUSES = frozenset(
    {"completed", "failed", "deadline_exceeded", "budget_exceeded", "error"}
)
_SUBAGENT_ID_RE = re.compile(r"^sub_[0-9a-f]{32}$")
_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{32}$")

# Bound the payload the supervisor has to read; the parent applies its own
# 8000-char presentation truncation on top of this transport cap.
_MAX_FINAL_MESSAGE_CHARS = 64_000
_MAX_ERROR_CHARS = 4_000


def _bounded(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _payload(status: str, **fields: object) -> dict[str, object]:
    if status not in _RESULT_STATUSES:
        raise ValueError("worker result status is invalid")
    payload: dict[str, object] = {
        "version": RESULT_VERSION,
        "status": status,
        "final_message": "",
        "report_content": None,
        "model_turns": 0,
        "tool_calls": 0,
        "result_truncated": False,
        # True only when the loop soft-landed on its step limit and the final
        # message is a partial summary from the tool-free closing turn.
        "partial": False,
        "error": None,
        "input_tokens": None,
        "output_tokens": None,
        # Strict, content-free settlement receipt for the parent-issued
        # budget lease. The parent charges finite dimensions conservatively
        # and marks missing/invalid usage uncertain, including null dimensions.
        "budget_usage": None,
        # Optional, closed telemetry bridge. The parent revalidates this
        # lifecycle and assigns journal sequence/timestamps itself.
        "semantic_intent_events": [],
    }
    payload.update(fields)
    return payload


def _budget_token_fields(budget: object, lease: object) -> dict[str, object]:
    """Attributed token split from the child's process-local budget.

    Every result the child publishes carries exactly the usage this worker
    recorded. A hard kill publishes nothing, so the parent charges the full
    lease conservatively while leaving exact journal attribution absent.
    """
    from minicode.agent_budget import SubprocessBudgetLease

    if not isinstance(lease, SubprocessBudgetLease):
        return {}
    usage_receipt = getattr(budget, "subprocess_usage", None)
    if not callable(usage_receipt):
        return {}
    try:
        usage = usage_receipt(lease)
    except Exception:  # noqa: BLE001 - telemetry must not mask the outcome
        return {}
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    if (
        isinstance(input_tokens, int)
        and not isinstance(input_tokens, bool)
        and isinstance(output_tokens, int)
        and not isinstance(output_tokens, bool)
    ):
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "budget_usage": usage.to_dict(),
        }
    return {}


def _write_result(result_path: str, payload: dict[str, object]) -> None:
    """Publish the terminal result atomically so the parent never reads half."""
    target = Path(result_path)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _validate_request(request: object) -> dict[str, object]:
    from minicode.agent_budget import SubprocessBudgetLease
    from minicode.tooling import SkillLoadPolicy, deserialize_skill_load_policy

    if not isinstance(request, dict):
        raise TypeError("subagent worker request must be an object")
    raw_request_version = request.get("version")
    if (
        not isinstance(raw_request_version, int)
        or isinstance(raw_request_version, bool)
        or raw_request_version not in {_LEGACY_REQUEST_VERSION, REQUEST_VERSION}
    ):
        raise ValueError("subagent worker request version is unsupported")
    request_version = raw_request_version
    cwd = request.get("cwd")
    prompt = request.get("prompt")
    subagent_id = request.get("subagent_id")
    parent_run_id = request.get("parent_run_id")
    result_key = request.get("result_key")
    max_turns = request.get("max_turns")
    timeout_seconds = request.get("timeout_seconds")
    runtime = request.get("runtime")
    try:
        budget_lease = SubprocessBudgetLease.from_dict(request.get("budget_lease"))
    except ValueError as exc:
        raise ValueError(
            "subagent worker request budget lease is invalid"
        ) from exc
    try:
        if "parent_skill_load_policy" in request:
            parent_skill_load_policy = deserialize_skill_load_policy(
                request.get("parent_skill_load_policy")
            )
        elif request_version == REQUEST_VERSION:
            raise ValueError("parent skill load policy is missing")
        else:
            # Legacy v1 callers omitted this field. Compatibility must not
            # reinterpret absence as unrestricted Skill authority.
            parent_skill_load_policy = SkillLoadPolicy(
                allowed_bindings=frozenset(),
                reason_code="parent-policy-unavailable",
            )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "subagent worker request parent_skill_load_policy is invalid"
        ) from exc
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("subagent worker request cwd is invalid")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("subagent worker request prompt is invalid")
    if not isinstance(subagent_id, str) or not _SUBAGENT_ID_RE.fullmatch(subagent_id):
        raise ValueError("subagent worker request subagent_id is invalid")
    if parent_run_id is not None and (
        not isinstance(parent_run_id, str)
        or _RUN_ID_RE.fullmatch(parent_run_id) is None
    ):
        raise ValueError("subagent worker request parent_run_id is invalid")
    if not isinstance(result_key, str) or not result_key:
        raise ValueError("subagent worker request result_key is invalid")
    if "max_turns" not in request or (max_turns is not None and (
        isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1
    )):
        raise ValueError("subagent worker request max_turns is invalid")
    if "timeout_seconds" not in request or (timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not float(timeout_seconds) > 0
    )):
        raise ValueError("subagent worker request timeout_seconds is invalid")
    if runtime is not None and not isinstance(runtime, dict):
        raise ValueError("subagent worker request runtime is invalid")
    semantic_observation_active = request.get(
        "semantic_observation_active",
        False,
    )
    if not isinstance(semantic_observation_active, bool):
        raise TypeError(
            "subagent worker request semantic_observation_active is invalid"
        )
    instruction = request.get("result_instruction")
    return {
        "cwd": cwd,
        "prompt": prompt,
        "subagent_id": subagent_id,
        "parent_run_id": parent_run_id,
        "result_key": result_key,
        "result_instruction": instruction if isinstance(instruction, str) else "",
        "max_turns": max_turns,
        "timeout_seconds": float(timeout_seconds) if timeout_seconds is not None else None,
        "runtime": runtime,
        "inject_memory": request.get("inject_memory") is True,
        "semantic_observation_active": semantic_observation_active,
        "parent_skill_load_policy": parent_skill_load_policy,
        "budget_lease": budget_lease,
    }


def _execute(request: dict[str, object], *, tool_evidence_recorder=None) -> dict[str, object]:
    """Rebuild a non-interactive ``general`` runtime and run the nested turn."""
    from minicode.agent_budget import (
        AgentBudgetExceeded,
        AgentTurnBudget,
        SubprocessBudgetLease,
    )
    from minicode.agent_loop import AgentTurnDeadlineExceeded
    from minicode.config import load_runtime_config
    from minicode.context_manager import ContextManager
    from minicode.model_call_control import ModelCallDeadlineExceeded
    from minicode.permissions import PermissionManager
    from minicode.run_events import emit_skill_routing_safely
    from minicode.skill_router import (
        required_skill_names_for_routing,
        skill_load_policy_for_routing,
    )
    from minicode.subagent_mailbox import SubagentMailbox
    from minicode.subagent_model_routing import create_subagent_model_adapter
    from minicode.subagent_types import AGENT_TYPES
    from minicode.task_deadline import TaskDeadlineExceeded
    from minicode.task_outcome import AgentOutcomeCapture
    from minicode.tooling import intersect_skill_load_policies
    from minicode.tools import create_default_tool_registry
    from minicode.tools.task import (
        _build_sub_agent_system_prompt,
        _filtered_subagent_registry,
        _read_subagent_report,
        _subagent_result_instruction,
        _SubagentJournalSink,
    )

    cwd = str(request["cwd"])
    os.chdir(cwd)
    runtime = request.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        runtime = load_runtime_config(cwd)
    agent_def = dict(AGENT_TYPES["general"])

    full_tools = create_default_tool_registry(
        cwd,
        runtime=runtime,
        include_mcp=True,
        include_user_interaction=False,
    )
    try:
        # Same registry contract as the in-process general path: the full
        # registry minus `task`, which keeps the sub-agent tree depth-bounded.
        tools = _filtered_subagent_registry(
            full_tools,
            lambda tool: tool.name != "task",
            skills=full_tools.get_skills(),
        )

        # Fail closed: a subprocess has no interactive user, so any decision
        # that would prompt is denied (PermissionManager prompt=None
        # semantics) instead of auto-approved in an unwatched process.
        permissions = PermissionManager(cwd, prompt=None)

        model = create_subagent_model_adapter(runtime, "general", tools=tools)
        model_runtime = dict(getattr(model, "runtime", runtime) or runtime)

        # Routing and execution share one process-local budget, event sink,
        # and cooperative deadline. This keeps optional semantic routing from
        # becoming an unaccounted model-call prelude to the nested turn.
        budget_lease = request["budget_lease"]
        if not isinstance(budget_lease, SubprocessBudgetLease):
            raise TypeError("subagent worker budget lease is invalid")
        budget = AgentTurnBudget(
            max_total_tokens=budget_lease.max_total_tokens,
            max_model_calls=budget_lease.max_model_calls,
            max_cost_usd=budget_lease.max_cost_usd,
        )
        semantic_observation_active = request["semantic_observation_active"] is True
        event_sink = _SubagentJournalSink(
            None,
            str(request["subagent_id"]),
            trusted_parent_run_id=(
                str(request["parent_run_id"])
                if request.get("parent_run_id") is not None
                else None
            ),
            capture_semantic_intent_events=semantic_observation_active,
            semantic_observation_active=semantic_observation_active,
            tool_evidence_recorder=tool_evidence_recorder,
        )
        timeout_seconds = request["timeout_seconds"]
        deadline_monotonic = (time.monotonic() + float(timeout_seconds)
                              if timeout_seconds is not None else None)
        started_at = time.time()

        task_prompt = str(request["prompt"])
        from minicode.intent_parser import parse_intent
        from minicode.intent_semantic_classifier import (
            build_semantic_intent_adapter,
        )

        try:
            parsed_intent = parse_intent(
                task_prompt,
                semantic_adapter=build_semantic_intent_adapter(
                    model,
                    runtime=model_runtime,
                ),
                call_context={
                    "agent_budget": budget,
                    "event_sink": event_sink,
                    "cancellation_token": None,
                    "deadline_monotonic": deadline_monotonic,
                },
            )
            system_prompt, routing = _build_sub_agent_system_prompt(
                cwd=cwd,
                agent_def=agent_def,
                task_prompt=task_prompt,
                tools=tools,
                permissions=permissions,
                routing_call_context={
                    "agent_budget": budget,
                    "event_sink": event_sink,
                    "cancellation_token": None,
                    "deadline_monotonic": deadline_monotonic,
                    "purpose": "skill-routing",
                    "preserve_model_calls": 1,
                    "skill_selection_mode": str(
                        (runtime or {}).get("skillSelection", {}).get(
                            "mode", "routed"
                        )
                        or "routed"
                    ),
                    "skill_flat_catalog_chars": int(
                        (runtime or {}).get("skillSelection", {}).get(
                            "flatCatalogCharBudget", 8000
                        )
                        or 8000
                    ),
                    "skill_page_chars": int(
                        (runtime or {}).get("skillSelection", {}).get(
                            "skillPageCharBudget", 4000
                        )
                        or 4000
                    ),
                    "skill_intent_hints": bool(
                        (runtime or {}).get("skillSelection", {}).get(
                            "intentHints", False
                        )
                    ),
                    "skill_intent_prefetch": bool(
                        (runtime or {}).get("skillSelection", {}).get(
                            "intentPrefetch", False
                        )
                    ),
                    "skill_directory_presel": bool(
                        (runtime or {}).get("skillSelection", {}).get(
                            "directoryPresel", False
                        )
                    ),
                    "skill_discovery_gate": bool(
                        (runtime or {}).get("skillSelection", {}).get(
                            "discoveryGate", False
                        )
                    ),
                },
                parsed_intent=parsed_intent,
            )
        except ModelCallDeadlineExceeded as exc:
            return _payload(
                "deadline_exceeded",
                error=_bounded(f"{type(exc).__name__}: {exc}", _MAX_ERROR_CHARS),
                model_turns=event_sink.model_turns,
                duration_ms=int((time.time() - started_at) * 1000),
                semantic_intent_events=event_sink.semantic_intent_events,
                **_budget_token_fields(budget, budget_lease),
            )
        result_key = str(request["result_key"])
        result_instruction = str(request["result_instruction"])
        if not result_instruction:
            result_instruction = _subagent_result_instruction(result_key)
        messages = [
            {"role": "system", "content": system_prompt + result_instruction},
            {"role": "user", "content": task_prompt},
        ]

        context_manager = ContextManager(model=model_runtime.get("model", "default"))
        context_manager.messages = messages

        memory_manager = None
        if request["inject_memory"]:
            try:
                from minicode.memory import MemoryManager

                memory_manager = MemoryManager(project_root=cwd)
            except Exception:  # noqa: BLE001 - optional enhancement
                memory_manager = None

        if routing is not None:
            emit_skill_routing_safely(event_sink, routing)

        effective_skill_load_policy = intersect_skill_load_policies(
            request.get("parent_skill_load_policy"),
            skill_load_policy_for_routing(routing),
        )

        # Process-local mailbox: the child writes its typed report here and
        # the payload carries it back to the parent's projection.
        mailbox = SubagentMailbox()
        outcome_capture = AgentOutcomeCapture()
        try:
            result_messages = run_agent_turn(
                model=model,
                tools=tools,
                messages=messages,
                cwd=cwd,
                permissions=permissions,
                max_steps=request["max_turns"],
                # Same soft-landing contract as the in-process path: on
                # step-limit exhaustion, one tool-free closing turn produces
                # a partial summary instead of a discarded hard failure.
                max_steps_soft_landing=True,
                runtime=model_runtime,
                context_manager=context_manager,
                memory_manager=memory_manager,
                event_sink=event_sink,
                agent_budget=budget,
                budget_exhausted_policy="raise",
                subagent_mailbox=mailbox,
                cancellation_token=None,
                # Cooperative inner deadline slightly inside the parent's hard
                # kill: if it fires first the child reports a clean typed
                # result instead of being SIGKILLed mid-write.
                deadline_monotonic=deadline_monotonic,
                agent_depth=1,
                outcome_capture=outcome_capture,
                required_skill_names=required_skill_names_for_routing(routing),
                skill_load_policy=effective_skill_load_policy,
                allow_user_interaction=False,
                parsed_intent=parsed_intent,
            )
        except (TaskDeadlineExceeded, AgentTurnDeadlineExceeded) as exc:
            return _payload(
                "deadline_exceeded",
                error=_bounded(f"{type(exc).__name__}: {exc}", _MAX_ERROR_CHARS),
                model_turns=event_sink.model_turns,
                duration_ms=int((time.time() - started_at) * 1000),
                semantic_intent_events=event_sink.semantic_intent_events,
                **_budget_token_fields(budget, budget_lease),
            )
        except AgentBudgetExceeded as exc:
            return _payload(
                "budget_exceeded",
                error=_bounded(exc.reason, _MAX_ERROR_CHARS),
                model_turns=event_sink.model_turns,
                duration_ms=int((time.time() - started_at) * 1000),
                semantic_intent_events=event_sink.semantic_intent_events,
                **_budget_token_fields(budget, budget_lease),
            )

        final_message = None
        final_partial = False
        for message in reversed(result_messages):
            if (
                message.get("role") == "assistant"
                and message.get("content", "").strip()
            ):
                final_message = message["content"]
                # The loop's max-steps soft landing marks its closing summary
                # so the parent can report a partial hand-back, not a win.
                final_partial = message.get("_partial") is True
                break
        if not final_message:
            final_message = "(sub-agent completed without a final message)"
        tool_calls = sum(
            1 for m in result_messages if m.get("role") == "assistant_tool_call"
        )
        message_model_turns = sum(
            1
            for m in result_messages
            if m.get("role") in {"assistant", "assistant_tool_call"}
        )
        model_turns = event_sink.model_turns or message_model_turns
        duration_ms = int((time.time() - started_at) * 1000)

        typed = outcome_capture.outcome
        if typed is None or typed.status != "success":
            typed_status = typed.status if typed is not None else "unknown"
            return _payload(
                "failed",
                typed_status=typed_status,
                final_message=_bounded(final_message, _MAX_FINAL_MESSAGE_CHARS),
                model_turns=model_turns,
                tool_calls=tool_calls,
                result_truncated=len(final_message) > 8000,
                duration_ms=duration_ms,
                semantic_intent_events=event_sink.semantic_intent_events,
                **_budget_token_fields(budget, budget_lease),
            )

        # The mailbox is process-local: the child writes its typed report
        # here and the parent reads it back through this result payload.
        mailbox_report = _read_subagent_report(mailbox, result_key)
        return _payload(
            "completed",
            final_message=_bounded(final_message, _MAX_FINAL_MESSAGE_CHARS),
            report_content=(
                mailbox_report if isinstance(mailbox_report, str) else None
            ),
            model_turns=model_turns,
            tool_calls=tool_calls,
            result_truncated=len(final_message) > 8000,
            partial=final_partial,
            duration_ms=duration_ms,
            semantic_intent_events=event_sink.semantic_intent_events,
            **_budget_token_fields(budget, budget_lease),
        )
    finally:
        try:
            full_tools.dispose()
        except BaseException:  # noqa: BLE001,S110 - cleanup cannot mask the result
            pass


def run_worker(request_path: str, result_path: str) -> int:
    """Execute one request file and publish exactly one result file."""
    try:
        raw = json.loads(Path(request_path).read_text(encoding="utf-8"))
        request = _validate_request(raw)
    except Exception as exc:  # noqa: BLE001 - always leave a typed result
        _write_result(
            result_path,
            _payload(
                "error",
                error=_bounded(
                    f"request_invalid: {type(exc).__name__}: {exc}",
                    _MAX_ERROR_CHARS,
                ),
            ),
        )
        return 2
    try:
        from minicode.subagent_tool_evidence import tool_evidence_writer
        # This is only the supervisor's existing disposable transport directory;
        # the child never receives a parent journal path or write capability.
        with tool_evidence_writer(Path(result_path).parent) as recorder:
            payload = _execute(request, tool_evidence_recorder=recorder)
    except Exception as exc:  # noqa: BLE001 - safety net above the agent loop
        payload = _payload(
            "error",
            error=_bounded(f"{type(exc).__name__}: {exc}", _MAX_ERROR_CHARS),
        )
    _write_result(result_path, payload)
    return 0 if payload["status"] == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minicode.subagent_worker",
        description="Run one write-capable sub-agent turn in this process.",
    )
    parser.add_argument("--request", required=True, help="request JSON path")
    parser.add_argument("--result", required=True, help="result JSON path")
    args = parser.parse_args(argv)
    return run_worker(args.request, args.result)


if __name__ == "__main__":
    sys.exit(main())
