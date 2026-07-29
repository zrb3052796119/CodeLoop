"""Task tool — spawn a sub-agent to handle complex multi-step tasks.

Inspired by Claude Code's Task tool which launches an independent agent loop
with its own context window, isolated from the main conversation.

The sub-agent runs a full agent loop (model + tools) with:
- Its own system prompt tailored to the task type
- A filtered tool set based on the agent type
- A turn limit to prevent runaway execution
- Result summarized back into the parent context
"""
from __future__ import annotations

import time

from minicode.agent_loop import run_agent_turn
from minicode.run_events import emit_event_safely
from minicode.subagent_observation import (
    SUBAGENT_EVENT_TYPE,
    project_subagent_event,
)
from minicode.tooling import ToolDefinition, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested


# ---------------------------------------------------------------------------
# Agent type definitions
# ---------------------------------------------------------------------------

# Maximum agent nesting depth. The top-level agent loop runs at depth 0, so a
# limit of 1 means "sub-agents may not spawn further sub-agents".
#
# This is deliberately strict. The `general` agent type is granted the full
# tool registry, which includes this very tool, so without a hard limit a
# sub-agent can spawn a sub-agent indefinitely — and every level rebuilds a
# complete tool registry (including MCP server processes), making the blow-up
# exponential rather than linear.
MAX_AGENT_DEPTH = 1

AGENT_TYPES = {
    "explore": {
        "name": "Explore",
        "description": "Fast, read-only agent for codebase exploration and search",
        "system_prompt": (
            "You are an exploration agent. Your job is to quickly search and "
            "understand codebases. You should be fast and focused on finding "
            "relevant files and understanding structure. "
            "You can only use read-only tools. "
            "When done, provide a concise summary of your findings."
        ),
        "allowed_tools": {"read_file", "list_files", "grep_files", "file_tree", "find_symbols", "find_references", "get_ast_info"},
        # A real exploration needs a tree/grep pass plus several reads before
        # it can summarize; 5 turns ran out mid-survey.
        "max_turns": 12,
    },
    "plan": {
        "name": "Plan",
        "description": "Thorough agent for gathering context and understanding code",
        "system_prompt": (
            "You are a planning agent. Your job is to thoroughly understand "
            "the codebase and task before acting. Read multiple files, trace "
            "code paths, and build a complete mental model. "
            "You can only use read-only tools. "
            "When done, provide a detailed analysis with actionable recommendations."
        ),
        "allowed_tools": {"read_file", "list_files", "grep_files", "file_tree", "find_symbols", "find_references", "get_ast_info", "code_review"},
        "max_turns": 8,
    },
    "general": {
        "name": "General",
        "description": "Full-featured agent for complex multi-step tasks",
        "system_prompt": (
            "You are a general-purpose coding agent. You can read, write, "
            "and modify code. Follow best practices and explain your changes. "
            "Break complex tasks into smaller steps. "
            "When done, provide a summary of what you did and any important findings."
        ),
        "allowed_tools": None,  # None = all tools allowed
        "max_turns": 15,
    },
}


def _validate(input_data: dict) -> dict:
    description = input_data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    
    agent_type = input_data.get("agent_type", "general")
    if agent_type not in AGENT_TYPES:
        valid = ", ".join(AGENT_TYPES.keys())
        raise ValueError(f"agent_type must be one of: {valid}. Got: {agent_type}")

    # `description` is specified as 3-5 words for display. Silently using it
    # as the sub-agent's entire task brief produces near-useless runs, so a
    # real prompt is required rather than defaulted.
    raw_prompt = input_data.get("prompt")
    prompt = raw_prompt.strip() if isinstance(raw_prompt, str) else ""
    if not prompt:
        raise ValueError(
            "prompt is required: give the sub-agent a full, self-contained task "
            "description (it cannot see this conversation)"
        )

    return {
        "description": description.strip(),
        "agent_type": agent_type,
        "prompt": prompt,
    }


def _emit_subagent_observation(context, **fields) -> None:
    """Record one bounded summary of this sub-agent run in the parent Run.

    The nested loop's own events are deliberately NOT forwarded: readers such
    as `skill_evidence` require exactly one `task.outcome` / `skill.routed`
    per Run, so replaying a sub-agent's lifecycle into the parent stream would
    disqualify the Run from the Skill evidence ledger entirely.
    """
    sink = getattr(context, "_event_sink", None)
    if sink is None:
        return
    try:
        payload = project_subagent_event(**fields)
        if payload is None:
            return
        emit_event_safely(
            sink,
            SUBAGENT_EVENT_TYPE,
            step=getattr(context, "_step", None),
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - observation must never affect the result
        pass


def _build_sub_agent_system_prompt(
    *,
    cwd: str,
    agent_def: dict,
    task_prompt: str,
    tools,
    permissions,
) -> str:
    """Compose the sub-agent's system prompt: the shared project/Skill-aware
    base plus this agent type's role and hand-back protocol.

    Falls back to the standalone role text if the shared builder is
    unavailable for any reason — a sub-agent with a plain prompt is far
    better than a sub-agent that cannot start.
    """
    role_text = (
        agent_def["system_prompt"]
        + f"\n\nCurrent cwd: {cwd}"
        + "\n\nIMPORTANT: When you have completed your task, end with <final> and provide your findings."
        + " Do not ask the user questions — work autonomously with the tools available."
        + " Be concise and focused."
    )
    try:
        from minicode.capability_registry import get_registry
        from minicode.intent_parser import parse_intent
        from minicode.prompt import build_system_prompt
        from minicode.skill_router import SkillRouter

        routing = SkillRouter().route(
            tools.get_skills(), parse_intent(task_prompt), get_registry()
        )
        try:
            permission_summary = permissions.get_summary()
        except Exception:  # noqa: BLE001 - summary is advisory only
            permission_summary = []
        base = build_system_prompt(
            cwd,
            permission_summary,
            {
                "skills": routing.selected_skill_dicts(),
                "skill_routing": routing.to_dict(),
                "mcpServers": tools.get_mcp_servers(),
                "memory_context": "",
            },
        )
    except Exception:  # noqa: BLE001 - never block the sub-agent on prompt assembly
        return role_text
    return f"{base}\n\n---\n\n{role_text}"


def _run(input_data: dict, context) -> ToolResult:
    """Execute a sub-agent task.
    
    This creates an isolated agent loop with:
    - Its own message history (system + task prompt)
    - Filtered tools based on agent type
    - A turn limit
    - Result summarized for the parent context
    """
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.tools import create_default_tool_registry

    agent_type = input_data["agent_type"]
    agent_def = AGENT_TYPES[agent_type]
    task_prompt = input_data["prompt"]

    depth = getattr(context, "_agent_depth", 0)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        depth = 0
    if depth >= MAX_AGENT_DEPTH:
        _emit_subagent_observation(
            context,
            agent_type=agent_type,
            outcome="depth_rejected",
            model_turns=0,
            tool_calls=0,
            duration_ms=0,
            max_turns=agent_def["max_turns"],
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=(
                f"error[sub_agent_depth_exceeded]: already running at sub-agent "
                f"depth {depth} (max {MAX_AGENT_DEPTH}). Sub-agents cannot spawn "
                f"further sub-agents — complete this task with the tools you have."
            ),
        )

    # Try to get the model from context or fall back to creating one
    # The context object carries runtime info needed for the model adapter
    runtime = None
    model = None
    
    # Attempt to extract runtime from the ToolContext
    if hasattr(context, '_runtime') and context._runtime:
        runtime = context._runtime
    
    if not runtime:
        # Try loading from config
        try:
            from minicode.config import load_runtime_config
            runtime = load_runtime_config(context.cwd)
        except Exception:
            pass
    
    if not runtime:
        return ToolResult(
            ok=False,
            output="Cannot run sub-agent: no model configuration available. Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL."
        )
    
    # Create a filtered tool registry for this agent type
    current_state_registry = getattr(
        context,
        "_mcp_current_state_registry",
        None,
    )
    if current_state_registry is None:
        full_tools = create_default_tool_registry(context.cwd, runtime=runtime)
    else:
        full_tools = create_default_tool_registry(
            context.cwd,
            runtime=runtime,
            mcp_current_state_registry=current_state_registry,
        )
    try:
        from minicode.tooling import ToolRegistry

        allowed = agent_def["allowed_tools"]
        if allowed is not None:
            filtered_tools = [t for t in full_tools.list() if t.name in allowed]
        else:
            # `general` gets the full registry, minus this tool itself: at
            # MAX_AGENT_DEPTH=1 a nested call could only ever be refused, so
            # withholding it entirely is better than advertising a tool whose
            # every invocation returns an error.
            filtered_tools = [t for t in full_tools.list() if t.name != "task"]
        tools = ToolRegistry(
            filtered_tools,
            skills=full_tools.get_skills(),
            mcp_current_state_registry=current_state_registry,
        )

        model = create_model_adapter(
            model=runtime.get("model", ""),
            tools=tools,
            runtime=runtime,
        )

        if agent_def["allowed_tools"] is not None:
            sub_permissions = PermissionManager(context.cwd, prompt=None)
        else:
            sub_permissions = PermissionManager(
                context.cwd,
                prompt=getattr(context.permissions, "prompt", None),
            )

        # Route Skills for the sub-agent's own task, exactly as the top-level
        # runtime does. Without this the sub-agent's system prompt is the
        # hardcoded blurb below and it never learns that Skills exist, so it
        # can neither see candidates nor call load_skill.
        #
        # Note run_agent_turn's `system_prompt`/`project_context` parameters
        # only feed a LayeredContext that nothing consumes; the prompt the
        # model actually receives is messages[0], so it is composed here.
        sub_system_prompt = _build_sub_agent_system_prompt(
            cwd=context.cwd,
            agent_def=agent_def,
            task_prompt=task_prompt,
            tools=tools,
            permissions=sub_permissions,
        )
        sub_messages = [
            {"role": "system", "content": sub_system_prompt},
            {
                "role": "user",
                "content": task_prompt,
            },
        ]

        # Give the sub-agent its own context window governor. Without this the
        # sub-agent runs with no compaction at all, so a long exploration just
        # grows the prompt until the provider rejects it.
        from minicode.context_manager import ContextManager

        sub_context_manager = ContextManager(model=runtime.get("model", "default"))
        sub_context_manager.messages = sub_messages

        # Share the project's memory store. Injection is a clear win — the
        # sub-agent otherwise re-derives conventions the project already
        # recorded. Reflection write-back rides along; that is acceptable
        # because automatic memories land in the pending-approval queue
        # rather than the active pool, so a narrow subtask cannot silently
        # promote low-signal "lessons" into future prompts.
        try:
            from minicode.memory import MemoryManager

            sub_memory = MemoryManager(project_root=context.cwd)
        except Exception:  # noqa: BLE001 - memory is an enhancement, not a prerequisite
            sub_memory = None

        start_time = time.time()
        max_turns = agent_def["max_turns"]
    except BaseException:
        try:
            full_tools.dispose()
        except BaseException:
            pass
        raise
    
    try:
        try:
            result_messages = run_agent_turn(
                model=model,
                tools=tools,
                messages=sub_messages,
                cwd=context.cwd,
                permissions=sub_permissions,
                max_steps=max_turns,
                runtime=runtime,
                context_manager=sub_context_manager,
                memory_manager=sub_memory,
                # Cancelling the parent Turn must stop the sub-agent too;
                # otherwise it keeps calling the model after the user gave up.
                cancellation_token=getattr(context, "_cancellation_token", None),
                agent_depth=depth + 1,
            )
        except TurnCancellationRequested:
            # Cooperative cancellation is the parent Turn's decision, not a
            # sub-agent failure — let it propagate so the parent unwinds.
            raise
        except Exception as e:
            _emit_subagent_observation(
                context,
                agent_type=agent_type,
                outcome="failed",
                model_turns=0,
                tool_calls=0,
                duration_ms=int((time.time() - start_time) * 1000),
                max_turns=max_turns,
                result_truncated=False,
            )
            return ToolResult(
                ok=False,
                output=(
                    f"Sub-agent ({agent_def['name']}) failed: "
                    f"{type(e).__name__}: {e}"
                ),
            )
    finally:
        try:
            full_tools.dispose()
        except BaseException:
            pass
    
    elapsed = time.time() - start_time
    
    # Extract the final assistant message as the result
    final_message = None
    for msg in reversed(result_messages):
        if msg.get("role") == "assistant" and msg.get("content", "").strip():
            final_message = msg["content"]
            break
    
    if not final_message:
        final_message = "(sub-agent completed without a final message)"
    
    # Build summary. Model turns are assistant messages; tool results also
    # arrive with role="user", so counting user messages (as this once did)
    # reported neither turns nor tool calls.
    tool_calls_count = sum(
        1 for m in result_messages if m.get("role") == "assistant_tool_call"
    )
    model_turns = sum(
        1
        for m in result_messages
        if m.get("role") in {"assistant", "assistant_tool_call"}
    )

    header = (
        f"[Sub-agent {agent_def['name']} completed]\n"
        f"  Type: {agent_type}\n"
        f"  Model turns: {model_turns} (tool calls: {tool_calls_count})\n"
        f"  Duration: {elapsed:.1f}s\n"
        f"  Max turns: {max_turns}\n"
    )
    
    # Truncate very long results
    result_text = final_message
    MAX_RESULT_LEN = 8000
    truncated = len(result_text) > MAX_RESULT_LEN
    if truncated:
        result_text = result_text[:MAX_RESULT_LEN] + f"\n\n... (truncated, {len(final_message)} chars total)"

    _emit_subagent_observation(
        context,
        agent_type=agent_type,
        outcome="completed",
        model_turns=model_turns,
        tool_calls=tool_calls_count,
        duration_ms=int(elapsed * 1000),
        max_turns=max_turns,
        result_truncated=truncated,
    )

    return ToolResult(ok=True, output=header + "\n" + result_text)


task_tool = ToolDefinition(
    name="task",
    description=(
        "Launch a sub-agent to handle a complex task autonomously. "
        "The sub-agent runs in its own isolated context with a turn limit. "
        "Use 'explore' for fast read-only codebase exploration, "
        "'plan' for thorough analysis, or 'general' for full-featured multi-step work. "
        "The sub-agent's final result is returned to you."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short 3-5 word description of the task",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Full, self-contained task description for the sub-agent. "
                    "The sub-agent runs in a fresh context and cannot see this "
                    "conversation, so include every file path, constraint, and "
                    "piece of background it needs."
                ),
            },
            "agent_type": {
                "type": "string",
                "enum": ["explore", "plan", "general"],
                "description": "Type of sub-agent: 'explore' (fast, read-only), 'plan' (thorough, read-only), 'general' (full tools, default)",
            },
        },
        "required": ["description", "prompt"],
    },
    validator=_validate,
    run=_run,
)
