"""A failing final check can be repaired before a fresh structured handoff."""

import subprocess
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.implementation_phases import ImplementationPhaseController
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.write_file import write_file_tool


def _submit():
    return ToolDefinition(name="executor_submit", description="bound result",
                          input_schema={"type": "object"}, validator=lambda data: data,
                          run=lambda _data, _ctx: ToolResult(ok=True, output="submitted",
                                                           _implementation_submitted=True))


def _call(name, identity=None):
    return {"id": identity or name, "toolName": name, "input": {}}


@pytest.mark.parametrize("dependency", ["edit_file", "write_file", "executor_check", "executor_test"])
def test_penultimate_batch_must_observe_its_results_before_submit(dependency):
    budget = AgentTurnBudget(max_model_calls=20, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=20,
        now=0, deadline=600, analysis_complete=True, allow_test_completion=True,
        require_structured_handoff=True)
    dependency_tool = ToolDefinition(name=dependency, description="feedback-producing operation",
        input_schema={"type": "object"}, validator=lambda data: data,
        run=lambda _data, _ctx: ToolResult(ok=True, output="actual feedback"))
    registry = ToolRegistry([_submit(), dependency_tool])
    policy.observe_verification(failed=True)
    policy.final_calls = 1
    # The last slot is a real local-call boundary, not the former fixed
    # number of finalization requests while repair allowance remains.
    policy.calls = policy.call_limit - 2
    policy.request(budget.snapshot(), estimated_input=1000, now=480)
    policy.tool_view(registry)
    policy.admitted()
    rejected = policy.admit_batch([_call(dependency), _call("executor_submit")])
    assert set(rejected) == {"executor_submit"}, "do the operation, then give its result to the next request"
    assert "feedback" in rejected["executor_submit"]
    handoff = policy.request(budget.snapshot(), estimated_input=2000, now=480)
    assert handoff.handoff_only
    assert [tool.name for tool in policy.tool_view(registry).list()] == ["executor_submit"]
    policy.admitted()
    rejected = policy.admit_batch([_call(dependency), _call("executor_submit")])
    assert set(rejected) == {dependency}, "a stale dependency cannot consume or block the last submit slot"
    assert policy.exhausted


@pytest.mark.parametrize("native", [False, True])
def test_final_source_repair_requires_current_host_failure_without_reopening_reads(native):
    budget = AgentTurnBudget(max_model_calls=20)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=20,
        now=0, deadline=600, analysis_complete=True, allow_test_completion=True,
        require_structured_handoff=native)
    registry = ToolRegistry([edit_file_tool, write_file_tool] + ([_submit()] if native else []))
    policy.request(budget.snapshot(), estimated_input=1000, now=480)
    policy.tool_view(registry)
    assert not policy.allowed_name("edit_file")
    policy.observe_verification(failed=True)
    assert policy.allowed_name("edit_file")
    assert policy.allowed_name("write_file")
    assert not policy.allowed_name("read_file")
    assert not policy.allowed_name("grep_files")
    policy.observe_change(True)
    assert policy.phase.value == "finalize"
    policy.observe_verification(failed=False)
    assert not policy.allowed_name("edit_file"), "fresh passing checks retire the exceptional repair surface"


def test_real_worker_repairs_failed_source_in_finalization_then_submits_fresh_feedback(tmp_path, monkeypatch):
    from minicode import subagent_model_routing
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.permissions import PermissionManager
    import minicode.workflow_runner as runner_module
    from minicode.types import AgentStep, ModelAdapter
    from test_workflow_v2_structured_routing import _request, _run, _write_reviewer_result
    from minicode.tools import task as task_module

    actual_turn = task_module.run_agent_turn

    def bounded_turn(**kwargs):
        # This historical fixture exercises the explicit finalization policy;
        # production worker turns now use continuous execution by default.
        kwargs["bounded_implementation_phases"] = True
        return actual_turn(**kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", bounded_turn)
    (tmp_path / "allowed.txt").write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_value():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, write_file_tool]))
    actual_request = ImplementationPhaseController.request

    def enter_final_after_source_change(self, snapshot, **kwargs):
        if self.implementation_started:
            kwargs["now"] = max(kwargs["now"], self.final_start)
        return actual_request(self, snapshot, **kwargs)

    monkeypatch.setattr(ImplementationPhaseController, "request", enter_final_after_source_change)
    commands = []
    observed = []
    original_worker = task_module._run

    def worker(input_data, context):
        # This fixture isolates Executor feedback; the actual Reviewer model
        # and repair loop are covered by test_workflow_completion_coverage.
        if context._logical_agent_role == "reviewer":
            assert (tmp_path / "allowed.txt").read_text() == "before"
            _write_reviewer_result(input_data, context)
            return ToolResult(ok=True, output="Reviewed repaired candidate.")
        return original_worker(input_data, context)

    monkeypatch.setattr(task_module, "_run", worker)

    def command_runner(_argv, **kwargs):
        commands.append(kwargs["cwd"])
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    class Model(ModelAdapter):
        def next(self, messages, tools_override=None, **kwargs):
            observed.append(({tool.name for tool in tools_override.list()}, messages))
            turn = len(observed)
            submission = {"id": f"submit-{turn}", "toolName": "executor_submit", "input": {
                "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                "deviations": [], "blockers": [],
            }}
            if turn == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "initial", "toolName": "edit_file",
                    "input": {"path": "allowed.txt", "old": "before", "new": "almost"},
                }])
            if turn == 2:
                assert "AssertionError" in str(messages)
                assert "edit_file" in observed[-1][0], "actual failed check must leave a supported source-repair path"
                assert "read_file" not in observed[-1][0]
                return AgentStep(type="tool_calls", calls=[{
                    "id": "repair", "toolName": "edit_file",
                    "input": {"path": "allowed.txt", "old": "almost", "new": "after"},
                }, submission])
            raise AssertionError("a checked final repair and guarded native handoff must use the same response")

    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: Model())
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch, commit_audit_waived=True)
    assert result.ok, result.output
    assert len(observed) == 2
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert len(commands) == 3, "the final Verifier must independently rerun after the typed submission"
    assert len(set(commands)) == 3
