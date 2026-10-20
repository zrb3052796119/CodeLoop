"""Forced workflow entry for a stock coding task.

ImplementationSpec(behavior_review=True) never calls this tool, so a parent
GLM turn on that path makes no Qwen subagent calls. The scheduler starts only
when task(agent_type="workflow") runs with the central-workflow flag the
parent loop already sets on tool contexts.
"""

from __future__ import annotations

import pytest

from minicode.central_scheduler import CentralScheduler, DispatchStage
from minicode.multi_agent_contracts import parse_workflow_request
from minicode.subagent_model_routing import resolve_subagent_model_route
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task as task_module


STOCK_TASK = {
    "action": "run",
    "description": "inventory hold and release",
    "prompt": (
        "Implement both inventory operations in stock.py.\n"
        "hold(available, amount) returns available minus amount.\n"
        "release(available, amount) returns available plus amount.\n"
    ),
    "agent_type": "workflow",
    "write_paths": ["stock.py", "test_public.py"],
    "verification_commands": [["python3", "-m", "pytest", "-q", "test_public.py"]],
}

# Scheduler workers, not the workflow wrapper. Verifier is the host.
WORKER_MODEL_ROUTES = {
    "analyst": "plan",
    "executor": "general",
    "reviewer": "plan",
}


def test_stock_task_enters_run_workflow_with_a_reviewed_executor_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run_workflow(input_data, context, *, depth, subagent_id, run_subagent):
        captured["input"] = input_data
        captured["depth"] = depth
        captured["subagent_id"] = subagent_id
        captured["run_subagent"] = run_subagent
        captured["enforced"] = context._central_workflow_v2_enforced
        return ToolResult(ok=True, output="workflow-entered")

    monkeypatch.setattr(task_module, "run_workflow", run_workflow)
    validated = task_module.task_tool.validator(STOCK_TASK)
    result = task_module.task_tool.run(
        validated,
        ToolContext(cwd=".", _central_workflow_v2_enforced=True),
    )

    assert result.ok is True
    assert result.output == "workflow-entered"
    assert captured["depth"] == 0
    assert captured["enforced"] is True
    assert captured["run_subagent"] is task_module._run
    assert str(captured["subagent_id"]).startswith("sub_")
    request = parse_workflow_request(
        captured["input"]["workflow_request"],
        legacy_task_id="task_test",
    )
    assert request.scope.write_paths == ("stock.py", "test_public.py")
    assert request.verification_checks[0].argv == (
        "python3", "-m", "pytest", "-q", "test_public.py",
    )
    plan = CentralScheduler().plan(request.task_profile)
    assert plan.rule_id == "route.scoped_change"
    assert plan.stages == (
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )


def test_workflow_workers_use_the_dedicated_subagent_route() -> None:
    runtime = {
        "model": "glm-5.3-flash",
        "subagentRoutingEnabled": True,
        "subagentProvider": "openai-compatible",
        "subagentBaseUrl": "https://dashscope.example.invalid/compatible-mode/v1",
        "subagentApiKey": "child-key",
        "subagentModels": {
            "default": "qwen3.6-flash",
            "explore": "qwen3.6-flash",
            "plan": "qwen-plan",
            "general": "qwen-executor",
        },
    }

    for role, agent_type in WORKER_MODEL_ROUTES.items():
        route = resolve_subagent_model_route(runtime, agent_type)
        assert route.enabled is True, role
        assert route.provider == "openai-compatible"
        assert route.model == runtime["subagentModels"][agent_type]
        assert route.model != runtime["model"]
    # The workflow wrapper returns from task._run before this resolver.
    # Qwen is selected only for the worker types above.


def test_omitting_the_central_flag_does_not_build_the_workflow_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run_workflow(input_data, _context, **_kwargs):
        captured["input"] = input_data
        return ToolResult(ok=True, output="legacy")

    monkeypatch.setattr(task_module, "run_workflow", run_workflow)
    validated = task_module.task_tool.validator(STOCK_TASK)
    task_module.task_tool.run(validated, ToolContext(cwd="."))

    assert "workflow_request" not in captured["input"]
