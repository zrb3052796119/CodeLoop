"""Execute candidate tests before submission without granting shell authority."""

import json
from pathlib import Path
import subprocess
import sys
import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module
from test_workflow_v2_structured_routing import (
    _request, _run, _worker_role, _write_executor_declaration, _write_reviewer_result,
)

assert Path(runner_module.__file__).resolve().parent.parent == Path(__file__).resolve().parent.parent


def _use_bounded_executor_phases(monkeypatch):
    """Exercise the explicit legacy phase policy, not continuous defaults."""
    actual_turn = task_module.run_agent_turn

    def bounded_turn(**kwargs):
        kwargs["bounded_implementation_phases"] = True
        return actual_turn(**kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", bounded_turn)


def _stub_reviewer_for_executor_fixture(monkeypatch, target):
    """Keep Executor model steps separate from the mandatory Reviewer role.

    The Reviewer model-to-Executor repair behavior has its own real tool loop
    in test_workflow_completion_coverage; these fixtures test write feedback.
    """
    original_worker = task_module._run

    def worker(input_data, context):
        if context._logical_agent_role == "reviewer":
            assert target.read_text() == "before"
            _write_reviewer_result(input_data, context)
            return ToolResult(ok=True, output="Reviewed checked candidate.")
        return original_worker(input_data, context)

    monkeypatch.setattr(task_module, "_run", worker)


@pytest.mark.parametrize("deadline_kind", ["verification", "task"])
def test_feedback_deadline_preserves_successful_edit(tmp_path, deadline_kind):
    from minicode.deterministic_verifier import VerificationCheck, VerificationDeadlineExceeded
    from minicode.executor_verification import ExecutorCheckCapability
    from minicode.task_deadline import TaskDeadlineExceeded
    from minicode.tools.edit_file import edit_file_tool

    target = tmp_path / "app.py"
    target.write_text("value = 1\n")

    def deadline(_check, _context):
        if deadline_kind == "verification":
            raise VerificationDeadlineExceeded("feedback deadline")
        raise TaskDeadlineExceeded(1)

    cap = ExecutorCheckCapability((VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "tests/test_app.py"), 1),), deadline)
    result = cap.bind_registry(ToolRegistry([edit_file_tool])).execute(
        "edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"},
        ToolContext(cwd=str(tmp_path)))
    assert target.read_text() == "value = 2\n"
    assert result.ok and result._file_changed, result.output
    assert result._implementation_checks_failed is None
    assert '"status": "inconclusive"' in result.output
    assert '"reason": "verification_deadline"' in result.output


def test_feedback_does_not_swallow_abandoned_execution(tmp_path):
    from minicode.deterministic_verifier import VerificationCheck
    from minicode.executor_verification import ExecutorCheckCapability
    from minicode.tooling import ToolExecutionAbandoned
    from minicode.tools.edit_file import edit_file_tool

    (tmp_path / "app.py").write_text("value = 1\n")

    def abandoned(_check, _context):
        raise ToolExecutionAbandoned("owner cancelled")

    cap = ExecutorCheckCapability((VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "tests/test_app.py"), 1),), abandoned)
    with pytest.raises(ToolExecutionAbandoned):
        cap.bind_registry(ToolRegistry([edit_file_tool])).execute(
            "edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"},
            ToolContext(cwd=str(tmp_path)))


@pytest.fixture(autouse=True)
def local_file_registry_without_skill_bootstrap(monkeypatch):
    from minicode.tools.edit_file import edit_file_tool
    from minicode.tools.write_file import write_file_tool

    # These tests exercise actual files, checks and role wiring. Registry
    # bootstrap must not create unrelated skill-version metadata in the
    # isolated candidate, especially in a clean historical checkout.
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, write_file_tool]))


def test_executor_repairs_real_test_failure_before_submission(tmp_path, monkeypatch):
    target = tmp_path / "allowed.txt"
    target.write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_candidate():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    commands = []
    captured = []

    def command_runner(_argv, **kwargs):
        commands.append(kwargs["cwd"])
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))

    def worker(data, context):
        if _worker_role(data) == "executor":
            capability = data.get("_executor_check_capability")
            assert capability is not None, "Executor cannot observe actual test failures before submission"
            captured.append(capability)
            registry = ToolRegistry([capability.tool])
            # No model input may supply a command or select a different test.
            assert not registry.execute("executor_check", {"check": 1, "command": "echo pass"}, context).ok
            candidate = Path(context.cwd, "allowed.txt")
            candidate.write_text("almost")
            first = registry.execute("executor_check", {"check": 1}, context)
            assert not first.ok, first.output
            assert first._implementation_checks_failed is True
            receipt = json.loads(first.output)
            assert receipt["status"] == "failed"
            assert "AssertionError" in receipt["evidence"]["stdout"]["preview"]
            assert target.read_text() == "before"
            assert candidate.read_text() == "almost", "verification must not mutate the working candidate"
            candidate.write_text("after")
            second = registry.execute("executor_check", {"check": 1}, context)
            assert second.ok, second.output
            assert second._implementation_checks_failed is False
            assert json.loads(second.output)["patchDigest"] != receipt["patchDigest"]
            assert not second._implementation_submitted
            assert target.read_text() == "before"
            _write_executor_declaration(data, context)
        else:
            _write_reviewer_result(data, context)
        return ToolResult(ok=True, output="worker done")

    monkeypatch.setattr(task_module, "_run", worker)
    class Sink:
        def emit(self, *_a, **_k):
            return None

        def record_scheduler_audit(self, _audit):
            return True

        def record_scheduler_commit_ready(self, _ready):
            return True

    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch, event_sink=Sink())
    assert result.ok, result.output
    assert target.read_text() == "after"
    assert len(commands) == 3, "final Verifier must rerun; implementation checks cannot authorize promotion"
    assert len(set(commands)) == 3
    assert not ToolRegistry([captured[0].tool]).execute("executor_check", {"check": 1}, ToolContext()).ok


def test_repair_receives_actual_failed_check_and_keeps_candidate(tmp_path, monkeypatch):
    from test_workflow_implementation_repair import _repair_run

    original = runner_module._WorkflowRunSession.run_assigned_worker
    seen = []

    def capture(self, data, context, channel, **kwargs):
        if data.get("description", "").startswith("execute:"):
            seen.append((data["prompt"], context._implementation_analysis_complete,
                         Path(context.cwd, "allowed.txt").read_text()))
        return original(self, data, context, channel, **kwargs)

    monkeypatch.setattr(runner_module._WorkflowRunSession, "run_assigned_worker", capture)
    result, _roles, _inputs, _audits = _repair_run(tmp_path, monkeypatch, rejection="verifier")
    assert result.ok, result.output
    assert len(seen) == 2
    prompt, completed_analysis, candidate = seen[1]
    assert candidate == "almost"
    assert "AssertionError" in prompt, "failure references alone are not repair feedback"
    assert completed_analysis, "a repair must resume implementation, not restart investigation"


def test_real_worker_receives_test_feedback_after_each_actual_write(tmp_path, monkeypatch):
    from minicode import subagent_model_routing
    from minicode.types import AgentStep, ModelAdapter

    _use_bounded_executor_phases(monkeypatch)
    target = tmp_path / "allowed.txt"
    target.write_text("before")
    _stub_reviewer_for_executor_fixture(monkeypatch, target)
    (tmp_path / "test_candidate.py").write_text(
        "from pathlib import Path\n"
        "def test_value():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    seen = []
    commands = []

    def command_runner(_argv, **kwargs):
        commands.append(kwargs['cwd'])
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    class Model(ModelAdapter):
        calls = 0
        supported_reasoning_efforts = ("low", "high")

        def next(self, messages, **kwargs):
            self.calls += 1
            seen.append((str(messages), kwargs.get("reasoning_effort_override")))
            if self.calls <= 2:
                return AgentStep(type="tool_calls", calls=[{
                    "id": str(self.calls), "toolName": "edit_file", "input": {
                        "path": "allowed.txt", "old": "before" if self.calls == 1 else "almost",
                        "new": "almost" if self.calls == 1 else "after",
                    },
                }])
            return AgentStep(type="tool_calls", calls=[{
                "id": "submit", "toolName": "executor_submit", "input": {
                    "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                    "deviations": [], "blockers": [],
                },
            }])

    model = Model()
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: model)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch, commit_audit_waived=True)
    assert result.ok, result.output
    assert model.calls == 3
    assert "AssertionError" in seen[1][0], "test failure must reach the next implementation turn without a model test call"
    assert "implementation_feedback" in seen[2][0]
    assert [item[1] for item in seen] == ["low", "high", "high"]
    assert target.read_text() == "after"
    assert len(commands) == 3


def test_focused_repair_reads_survive_survey_quota_but_not_total_budget():
    from minicode.agent_budget import AgentTurnBudget
    from minicode.implementation_phases import ImplementationPhaseController

    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(snapshot=budget.snapshot(), max_steps=37,
                                          now=0, deadline=600, analysis_complete=True, bounded=True)
    for i in range(9):
        rejected = policy.admit_batch([{"id": str(i), "toolName": "read_file",
                                       "input": {"path": "app.py", "start_line": 10, "end_line": 20}}])
        assert not rejected, "a failed edit still needs exact source within its existing work budget"
    assert policy.focused_read_batches == 9
    assert not policy.allowed_name("list_files")
    policy.request(budget.snapshot(), estimated_input=1000, now=480)
    assert not policy.allowed_name("read_file")


def test_first_actual_edit_keeps_implementation_reasoning_for_remaining_work(tmp_path):
    from minicode.agent_loop import run_agent_turn
    from minicode.tooling import ToolRegistry
    from minicode.tools.edit_file import edit_file_tool
    from minicode.types import AgentStep, ModelAdapter

    (tmp_path / "app.py").write_text("value = 1\n")
    seen = []

    class Model(ModelAdapter):
        supported_reasoning_efforts = ("low", "high")

        def next(self, messages, **kwargs):
            seen.append(kwargs.get("reasoning_effort_override"))
            if len(seen) == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "edit", "toolName": "edit_file",
                    "input": {"path": "app.py", "old": "value = 1", "new": "value = 2"},
                }])
            return AgentStep(type="assistant", content="<final> Updated app.py.")

    run_agent_turn(model=Model(), tools=ToolRegistry([edit_file_tool]),
                   messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
                   implementation_task=True, implementation_analysis_complete=True,
                   enable_work_chain=False, max_steps=20)
    assert seen == ["high", "high"]
    assert (tmp_path / "app.py").read_text() == "value = 2\n"


@pytest.mark.parametrize("prefer_more_tests", [False, True])
def test_reserved_rounds_finish_and_repair_test_before_strict_handoff(tmp_path, monkeypatch, prefer_more_tests):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.implementation_phases import ImplementationPhaseController
    from minicode.types import AgentStep, ModelAdapter
    from minicode import subagent_model_routing

    _use_bounded_executor_phases(monkeypatch)
    (tmp_path / "allowed.txt").write_text("before")
    _stub_reviewer_for_executor_fixture(monkeypatch, tmp_path / "allowed.txt")
    (tmp_path / "tests").mkdir()
    request = _request(risk="low", complex=False)
    request["scope"]["writePaths"].append("tests/test_eval_regression.py")
    actual_request = ImplementationPhaseController.request
    def enter_final_after_first_edit(self, snapshot, **kwargs):
        if self.implementation_started:
            kwargs["now"] = max(kwargs["now"], self.final_start)
        return actual_request(self, snapshot, **kwargs)
    monkeypatch.setattr(ImplementationPhaseController, "request", enter_final_after_first_edit)
    observed = []
    commands = []

    class Model(ModelAdapter):
        calls = 0

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            observed.append((tools_override.list() if tools_override else [], str(messages)))
            if self.calls == 1:
                tool, values = "edit_file", {"path": "allowed.txt", "old": "before", "new": "after"}
            elif self.calls <= 3 or (prefer_more_tests and any(
                    tool.name == "executor_test" for tool in tools_override.list())):
                tool, values = "executor_test", {
                    "path": "tests/test_eval_regression.py", "code": (
                        "from pathlib import Path\n"
                        "def test_value():\n"
                        f"    assert Path('allowed.txt').read_text() == {'wrong' if self.calls == 2 else 'after'!r}\n"
                    ),
                }
            else:
                tool, values = "executor_submit", {
                    "status": "completed", "steps": [{"step": i, "status": "completed"} for i in (1, 2)],
                    "deviations": [], "blockers": [],
                }
            return AgentStep(type="tool_calls", calls=[{"id": str(self.calls), "toolName": tool, "input": values}])

    def command_runner(_argv, **kwargs):
        commands.append(kwargs["cwd"])
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    model = Model()
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: model)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    result = task_module.task_tool.run({"agent_type": "workflow", "description": "finish test",
        "prompt": "Complete the source repair and new regression test.", "workflow_request": request},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"},
                    _agent_budget=AgentTurnBudget(max_model_calls=40, max_total_tokens=500000),
                    _workflow_commit_audit_waived=True))
    assert any("executor_test" in {tool.name for tool in tools} for tools, _ in observed), \
        "assigned regression-test completion is absent from the worker surface"
    assert result.ok, result.output
    assert model.calls == 4
    assert "AssertionError" in observed[2][1]
    assert len(commands) == 4
    assert (tmp_path / "tests/test_eval_regression.py").exists()
    assert {tool.name for tool in observed[-1][0]} == {"executor_submit"}, \
        "test completion must leave one actual request for the structured handoff"
    for tools, _text in observed[1:-1]:
        assert {tool.name for tool in tools} == {
            "executor_test", "executor_submit", "executor_check", "edit_file", "write_file",
        }
        test = next(tool for tool in tools if tool.name == "executor_test")
        assert test.input_schema["properties"]["path"]["enum"] == ["tests/test_eval_regression.py"]
        with pytest.raises(ValueError):
            test.validator({"path": "allowed.txt", "code": "escape"})
