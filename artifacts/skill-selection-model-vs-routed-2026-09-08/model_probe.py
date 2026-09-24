"""Bounded observation of spontaneous Skill loading, never task execution.

Importing this file is network-free. Only an explicit ``run_case`` call creates
and invokes the configured model. The caller owns approval and corpus freezing.
No expected labels, evaluator instructions or synthetic tool results enter the
model messages. This is a pre-work observation window, not ``run_agent_turn``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from minicode.model_call_control import call_model_next
from minicode.model_registry import create_model_adapter
from minicode.openai_adapter import _to_openai_messages
from minicode.run_events import (
    SkillUsageTracker,
    emit_event_safely,
    project_model_usage,
)
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.skill_router import (
    required_skill_names_for_routing,
    skill_load_policy_for_routing,
)
from minicode.skill_routing_turn import SkillRoutingTurnDecision
from minicode.skills import resolve_skill_catalog_binding
from minicode.tooling import (
    ToolContext,
    ToolRegistry,
    serialize_skill_load_policy,
)
from minicode.turn_cancellation import TurnCancellationToken


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _initial_messages(
    case: dict[str, Any], system_prompt: str, prior_messages: list[dict] | None
) -> list[dict[str, Any]]:
    history = prior_messages
    if history is None:
        history = case.get("context", {}).get("recent_turns", [])
    # Explicitly copy only the actual message history; case labels stay local.
    for message in history:
        if message.get("role") not in {"user", "assistant"}:
            raise ValueError("probe history must contain user/assistant turns only")
        if not isinstance(message.get("content"), str):
            raise ValueError("probe history content must be text")
    return [
        {"role": "system", "content": system_prompt},
        *[
            {"role": item["role"], "content": item["content"]}
            for item in history
        ],
        {"role": "user", "content": case["text"]},
    ]


def _body_evidence(
    name: str, output: str, ok: bool, catalog: list[dict[str, Any]]
) -> dict[str, Any]:
    binding = resolve_skill_catalog_binding(name, catalog)
    if binding is None:
        return {"qualified_name": None, "body_integrity_ok": False}
    get = binding.get if isinstance(binding, dict) else lambda key: getattr(binding, key, None)
    body = output.split("\n\n", 1)[1] if ok and "\n\n" in output else None
    actual_digest = _sha(body) if body is not None else None
    expected_digest = get("content_digest")
    return {
        "qualified_name": get("qualified_name"),
        "source": get("source"),
        "routing_identity_digest": get("routing_identity_digest"),
        "expected_content_digest": expected_digest,
        "actual_body_sha256": actual_digest,
        "body_chars": len(body) if body is not None else 0,
        "body_integrity_ok": bool(ok and actual_digest == expected_digest),
    }


def run_case(
    case: dict[str, Any],
    decision: SkillRoutingTurnDecision,
    system_prompt: str,
    tool_registry: ToolRegistry,
    runtime: dict[str, Any],
    cwd: str | Path,
    output_dir: str | Path,
    *,
    prior_messages: list[dict] | None = None,
    max_calls: int = 4,
    call_timeout_seconds: float = 30.0,
    max_output_tokens: int = 2048,
) -> dict[str, Any]:
    """Call the real model, dispatch only its load_skill requests, then stop.

    The registry must be the full production core registry built with
    include_mcp=False and the same frozen catalog used to route this case.
    ``output_dir`` is new per case; existing evidence is never overwritten.
    """
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= 4:
        raise ValueError("max_calls must be between 1 and 4")
    if not 0 < call_timeout_seconds <= 30:
        raise ValueError("call timeout must be positive and at most 30 seconds")
    if not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 2048:
        raise ValueError("output limit must be between 1 and 2048 tokens")
    if tool_registry.find("load_skill") is None or len(tool_registry.list()) <= 1:
        raise ValueError("probe requires the full core tool schema, including load_skill")
    if tool_registry.get_mcp_servers():
        raise ValueError("probe requires include_mcp=False")
    if runtime.get("model") != "glm-5.3-flash":
        raise ValueError("the frozen probe expects configured glm-5.3-flash")

    cwd = Path(cwd).resolve()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    local_runtime = copy.deepcopy(runtime)
    local_runtime.update(
        modelTimeoutSeconds=float(call_timeout_seconds),
        modelMaxRetries=0,
        maxOutputTokens=max_output_tokens,
    )
    model = create_model_adapter(runtime["model"], tool_registry, runtime=local_runtime)
    if type(model).__name__ != "OpenAIModelAdapter":
        raise TypeError("probe requires the actual OpenAI-compatible model adapter")
    catalog = tool_registry.get_skills()
    policy = skill_load_policy_for_routing(decision.routing)
    if policy is None:
        raise ValueError("probe requires an actual turn-scoped SkillLoadPolicy")
    required_names = required_skill_names_for_routing(decision.routing)
    messages = _initial_messages(case, system_prompt, prior_messages)
    schemas = model._get_serialized_tools()
    _write(out / "tool-schemas.json", schemas)
    tracker = SkillUsageTracker()
    token = TurnCancellationToken("turn_" + uuid.uuid4().hex)
    result: dict[str, Any] = {
        "case_id": case["id"],
        "mode": "actual-model-pre-work-window",
        "model": runtime["model"],
        "provider": runtime.get("provider"),
        "runtime_overrides": {
            "modelTimeoutSeconds": call_timeout_seconds,
            "modelMaxRetries": 0,
            "maxOutputTokens": max_output_tokens,
        },
        "max_calls": max_calls,
        "system_prompt_sha256": _sha(system_prompt),
        "tool_schemas_sha256": _json_sha(schemas),
        "tool_names": tool_registry.list_all(),
        "catalog_sha256": _json_sha(catalog),
        "policy": serialize_skill_load_policy(policy),
        "required_skill_names": required_names,
        "model_calls": [],
        "load_calls": [],
        "blocked_nonload_calls": [],
        "unexecuted_calls": [],
        "stop_reason": "call_budget_exhausted",
        "probe_interventions": [],
        "limitations": [
            "At most four provider calls before the first non-load tool request or assistant-only response.",
            "No task operation is executed; this does not establish full task completion or workflow adherence.",
            "The AgentLoop required-Skill final nudge/gate is not run; initial spontaneous loading is measured separately.",
            "No synthetic response is supplied for a non-load request; a load preceding it may remain unsent.",
            "A completed observation journal means the probe finished, never that the synthetic task succeeded.",
            "Body submission on a failed provider call is not counted as confirmed provider handoff.",
        ],
    }
    journal = RunJournal(cwd, data_dir=out / "journal")
    started = time.perf_counter()
    with observe_run(
        workspace=cwd,
        source="unknown",
        title="Skill pre-work observation " + str(case["id"]),
        journal_factory=lambda workspace: journal,
    ) as observation:
        if not observation.observation_active:
            raise RuntimeError("probe observation journal is unavailable")
        result["run_id"] = observation.run_id
        for call_index in range(1, max_calls + 1):
            request_messages = copy.deepcopy(messages)
            # Use the adapter's actual internal-message converter, without
            # reading or persisting endpoint/header/authentication settings.
            system_text, wire_messages = _to_openai_messages(
                request_messages,
                require_tool_reasoning_content=bool(
                    local_runtime.get("requireReasoningContentReplay", False)
                ),
            )
            if system_text:
                wire_messages.insert(0, {"role": "system", "content": system_text})
            request_path = out / f"call-{call_index:02d}-request.json"
            _write(request_path, {
                "internal_messages": request_messages,
                "provider_message_projection": wire_messages,
                "tool_schemas_sha256": result["tool_schemas_sha256"],
            })
            for load in result["load_calls"]:
                if not load["ok"]:
                    continue
                exact_output = load["output"]
                internal_contains = any(
                    message.get("role") == "tool_result"
                    and message.get("toolUseId") == load["tool_call_id"]
                    and message.get("content") == exact_output
                    for message in request_messages
                )
                wire_contains = any(
                    message.get("role") == "tool"
                    and message.get("tool_call_id") == load["tool_call_id"]
                    and message.get("content") == exact_output
                    for message in wire_messages
                )
                if internal_contains and wire_contains:
                    load["submitted_on_calls"].append(call_index)
            record: dict[str, Any] = {
                "index": call_index,
                "operation_id": "modelop_" + uuid.uuid4().hex,
                "input_sha256": _json_sha(request_messages),
                "provider_message_projection_sha256": _json_sha(wire_messages),
                "request_file": request_path.name,
                "completed": False,
            }
            result["model_calls"].append(record)
            _write(out / "result.json", result)
            call_started = time.perf_counter()
            emit_event_safely(observation, "model.started", step=call_index,
                              payload={"operationId": record["operation_id"]})
            try:
                step = call_model_next(
                    model,
                    messages,
                    optional_kwargs={
                        "max_retries_override": 0,
                        "max_output_tokens_override": max_output_tokens,
                    },
                    cancellation_token=token,
                    deadline_monotonic=time.monotonic() + call_timeout_seconds,
                )
            except Exception as error:
                record["error_type"] = type(error).__name__
                record["elapsed_ms"] = (time.perf_counter() - call_started) * 1000
                # Exception strings can carry URLs/provider payloads; only a
                # content-free type and digest are retained.
                record["error_message_sha256"] = _sha(str(error))
                result["stop_reason"] = "model_error"
                break
            record.update(
                completed=True,
                elapsed_ms=(time.perf_counter() - call_started) * 1000,
                response=asdict(step),
                usage=project_model_usage(step.usage),
            )
            _write(out / f"call-{call_index:02d}-response.json", record)
            emit_event_safely(observation, "model.completed", step=call_index, payload={
                "operationId": record["operation_id"],
                "resultType": step.type,
                "contentPresent": bool(step.content),
                "toolCallCount": len(step.calls),
                "usage": record["usage"],
                "durationMs": int(record["elapsed_ms"]),
            })
            for load in result["load_calls"]:
                if call_index in load["submitted_on_calls"]:
                    load["confirmed_handoff_calls"].append(call_index)
            if not step.calls:
                result["stop_reason"] = "assistant_response_without_tool_calls"
                break
            turn_id = "tool-turn-" + uuid.uuid4().hex
            stop = False
            for call_position, call in enumerate(step.calls):
                if call["toolName"] != "load_skill":
                    result["unexecuted_calls"] = copy.deepcopy(step.calls[call_position:])
                    result["blocked_nonload_calls"] = [
                        copy.deepcopy(item) for item in step.calls[call_position:]
                        if item["toolName"] != "load_skill"
                    ]
                    result["stop_reason"] = "nonload_tool_requested"
                    stop = True
                    break
                context = ToolContext(
                    cwd=str(cwd),
                    _runtime=local_runtime,
                    _event_sink=observation,
                    _step=call_index,
                    _skill_usage_tracker=tracker,
                    _skill_load_policy=policy,
                    _cancellation_token=token,
                )
                observation.tool_started("load_skill")
                # The sole dispatch site: model-selected load_skill only.
                loaded = tool_registry.execute("load_skill", call["input"], context)
                observation.tool_finished("load_skill", is_error=not loaded.ok)
                requested_name = call["input"].get("name", "") if isinstance(call["input"], dict) else ""
                load_record = {
                    "model_call_index": call_index,
                    "tool_call_id": call["id"],
                    "requested_name": requested_name,
                    "origin": "actual_model_response",
                    "ok": loaded.ok,
                    "output": loaded.output,
                    "output_sha256": _sha(loaded.output),
                    "submitted_on_calls": [],
                    "confirmed_handoff_calls": [],
                    **_body_evidence(requested_name, loaded.output, loaded.ok, catalog),
                }
                result["load_calls"].append(load_record)
                assistant_message: dict[str, Any] = {
                    "role": "assistant_tool_call",
                    "toolUseId": call["id"],
                    "toolName": call["toolName"],
                    "input": copy.deepcopy(call["input"]),
                    "assistantTurnId": turn_id,
                }
                if call_position == 0:
                    if step.content:
                        assistant_message["content"] = step.content
                    if step.reasoningContent is not None:
                        assistant_message["reasoningContent"] = step.reasoningContent
                messages.extend([
                    assistant_message,
                    {
                        "role": "tool_result",
                        "toolUseId": call["id"],
                        "toolName": call["toolName"],
                        "content": loaded.output,
                        "isError": not loaded.ok,
                    },
                ])
            _write(out / "result.json", result)
            if stop:
                break
    result["elapsed_ms"] = (time.perf_counter() - started) * 1000
    result["loaded_skills"], result["loaded_skills_truncated"] = tracker.snapshot()
    result["events"] = []
    cursor = None
    while True:
        page = journal.list_events(result["run_id"], limit=100, cursor=cursor)
        result["events"].extend(
            {"type": event.type, "payload": event.payload} for event in page.items
        )
        cursor = page.next_cursor
        if cursor is None:
            break
    result["loaded_qualified_names"] = sorted({
        load["qualified_name"] for load in result["load_calls"]
        if load["ok"] and load.get("qualified_name")
    })
    result["body_handoff_qualified_names"] = sorted({
        load["qualified_name"] for load in result["load_calls"]
        if load["ok"] and load.get("qualified_name")
        and load["body_integrity_ok"] and load["confirmed_handoff_calls"]
    })
    result["required_skills_missing_at_stop"] = sorted(
        set(required_names) - set(result["loaded_qualified_names"])
    )
    _write(out / "messages-at-stop.json", messages)
    _write(out / "result.json", result)
    return result
