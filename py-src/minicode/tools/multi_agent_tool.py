"""Multi-Agent orchestration tool for MiniCode.

Provides the /multi command as a tool that can be invoked by the agent.
"""

from __future__ import annotations

from minicode.logging_config import get_logger
from minicode.tooling import ToolDefinition, ToolResult

logger = get_logger("multi_agent_tool")


VALID_PATTERNS = ["sequential", "parallel", "hierarchical", "consensus", "tool_mediated"]


def _multi_agent_validate(input_data: dict) -> dict:
    pattern = input_data.get("pattern", "sequential")
    task = input_data.get("task", "")
    if not task:
        raise ValueError("task is required")
    if pattern not in VALID_PATTERNS:
        raise ValueError(f"pattern must be one of: {VALID_PATTERNS}")
    return {
        "pattern": pattern,
        "task": task,
        "max_roles": int(input_data.get("max_roles", 3)),
    }


def _multi_agent_run(input_data: dict, context) -> ToolResult:
    try:
        from minicode.config import load_runtime_config
        from minicode.model_registry import create_model_adapter
        from minicode.multi_agent.orchestrator import create_minicode_orchestrator
        from minicode.tools import create_default_tool_registry

        runtime = load_runtime_config(context.cwd)
        tools = create_default_tool_registry(context.cwd, runtime=runtime)
        model = create_model_adapter(
            model=runtime.get("model", ""),
            tools=tools,
            runtime=runtime,
        )

        orchestrator = create_minicode_orchestrator(model, tools, context.cwd)

        trace = orchestrator.execute(
            task=input_data["task"],
            pattern=input_data["pattern"],
            max_roles=input_data["max_roles"],
        )

        lines = [
            "Multi-Agent Execution Complete",
            f"Pattern: {trace.pattern}",
            f"Duration: {trace.duration_ms:.0f}ms" if hasattr(trace, "duration_ms") else "Duration: N/A",
            f"Agents: {len(trace.agent_results)}",
            "=" * 60,
            "",
        ]

        for result in trace.agent_results:
            output = result.output
            if len(output) > 500:
                output = output[:500] + "..."
            lines.extend([
                f"Agent: {result.agent_id} ({result.role})",
                f"Status: {result.status.value}",
                f"Output: {output}",
                "",
            ])

        return ToolResult(ok=True, output="\n".join(lines))
    except Exception as e:
        logger.error("Multi-agent execution failed: %s", e)
        return ToolResult(ok=False, output=f"Multi-agent execution failed: {e}")


multi_agent_tool = ToolDefinition(
    name="multi_agent_orchestrate",
    description="Orchestrate multiple agents to solve a complex task. Patterns: sequential (one after another), parallel (simultaneous), hierarchical (manager + workers), consensus (debate), tool_mediated (shared tools).",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Orchestration pattern: sequential, parallel, hierarchical, consensus, tool_mediated",
            },
            "task": {
                "type": "string",
                "description": "The task description for the multi-agent team",
            },
            "max_roles": {
                "type": "number",
                "description": "Maximum number of agent roles to generate (default: 3)",
            },
        },
        "required": ["pattern", "task"],
    },
    validator=_multi_agent_validate,
    run=_multi_agent_run,
)
