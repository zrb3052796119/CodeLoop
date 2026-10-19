"""Full loop delivery exercises real file tools and a real local pytest exit."""

import os
import subprocess
import sys

from minicode.agent_loop import run_agent_turn
from minicode.permissions import PermissionManager
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter
from minicode.verification_observation import project_command_verification


def test_verified_patch_cannot_be_reverted_by_a_later_real_file_tool(tmp_path):
    (tmp_path / "app.py").write_text("def value():\n    return 0\n")
    (tmp_path / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 1\n")
    real_checks = []
    def check(arguments, context):
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_app.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        real_checks.append(completed.returncode)
        return ToolResult(ok=completed.returncode == 0, output=completed.stdout + completed.stderr,
            verification=project_command_verification(arguments, passed=completed.returncode == 0))
    checker = ToolDefinition(name="run_command", description="actual local pytest", input_schema={"type": "object"},
                             validator=lambda v: v, run=check)
    calls = [
        {"id": "fix", "toolName": "edit_file", "input": {"path": "app.py", "old": "return 0", "new": "return 1"}},
        {"id": "check", "toolName": "run_command", "input": {"command": "python", "args": ["-m", "pytest", "test_app.py"]}},
        {"id": "revert", "toolName": "edit_file", "input": {"path": "app.py", "old": "return 1", "new": "return 0"}},
    ]
    class FixtureModel(ModelAdapter):
        def next(self, messages, **kwargs):
            if not calls:
                return AgentStep(type="assistant", content="Implementation and check completed; external validation remains pending.")
            return AgentStep(type="tool_calls", calls=[calls.pop(0)])
    messages = run_agent_turn(bounded_implementation_phases=True, model=FixtureModel(), tools=ToolRegistry([read_file_tool, edit_file_tool, checker]),
        messages=[{"role": "user", "content": "Change app.py value from 0 to 1 and verify."}],
        cwd=str(tmp_path), permissions=PermissionManager(str(tmp_path), prompt=lambda _: {"decision": "allow_once"}),
        implementation_task=True, enable_work_chain=False, max_steps=10)
    assert real_checks == [0]
    assert "return 1" in (tmp_path / "app.py").read_text()
    results = {m["toolUseId"]: m for m in messages if m.get("role") == "tool_result"}
    assert not results["fix"]["isError"] and not results["check"]["isError"]
    assert results["revert"]["isError"]
    assert "verified_candidate_baseline_revert" in results["revert"]["content"]


def test_dynamic_tool_projection_does_not_mutate_either_provider_cache():
    from minicode.agent_budget import AgentTurnBudget
    from minicode.anthropic_adapter import AnthropicModelAdapter
    from minicode.implementation_phases import ImplementationPhaseController
    from minicode.openai_adapter import OpenAIModelAdapter

    registry = ToolRegistry([read_file_tool, edit_file_tool])
    policy = ImplementationPhaseController(bounded=True, snapshot=AgentTurnBudget(max_model_calls=20).snapshot(),
        max_steps=20, now=0, deadline=600)
    view = policy.tool_view(registry)
    for adapter_type in (OpenAIModelAdapter, AnthropicModelAdapter):
        adapter = adapter_type({"model": "fixture"}, registry)
        original = adapter._get_serialized_tools()
        original_cache = adapter._cached_tools_json
        filtered = adapter._get_serialized_tools(view)
        assert adapter.tools is registry
        assert adapter._cached_tools_json is original_cache
        assert filtered is not original
        assert adapter._get_serialized_tools() is original_cache
    assert read_file_tool.input_schema["properties"]["limit"]["maximum"] == 20000
    assert view.list()[0].input_schema["properties"]["limit"]["maximum"] == 8000
