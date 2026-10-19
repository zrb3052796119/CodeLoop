"""Session projection survives real Assignment renewal without renewing budget.

The large public instruction/source fixture exercises a bounded admission edge.
All scripted inputs are metered as estimated from actual messages and schemas;
the historical V25 remaining allowance is a control, not a new provider bill.
"""

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from test_executor_session_continuity import _context, _input, _session, _submit_binding

from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_execution import ImplementationSpec
from minicode.tooling import ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget


def test_same_executor_repair_keeps_projection_and_can_admit_edit_then_submit(tmp_path, monkeypatch):
    source = ("def value():\n" + "    # Keep this unrelated public behavior and its documented invariant.\n" * 180
              + "    return 'before'\n")
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "test_app.py").write_text("from app import value\ndef test_value():\n    assert value() == 'after'\n")
    (tmp_path / "dependency.py").write_text("UNCHANGED_API = 'explicit read evidence retained'\n")
    owner = object()
    session = _session(owner, tmp_path)
    context = _context(tmp_path, _task_deadline_monotonic=time.monotonic() + 600,
                       _implementation_analysis_complete=True)
    budget = context._agent_budget
    seen, checks, adapters, bills, channels, prices = [], [], [], [], [], []
    public_check = VerificationCheck("value", "tests", ("python", "-m", "pytest", "-q", "test_app.py"), 30)

    def run_check(check, tool_context):
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_app.py"],
            cwd=tool_context.cwd, capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                 "PYTEST_ADDOPTS": ""})
        checks.append(result.returncode)
        return {"checkId": check.check_id, "status": "passed" if result.returncode == 0 else "failed",
                "stdout": result.stdout, "stderr": result.stderr, "verification": "implementation_feedback"}

    submission = {"status": "completed", "steps": [{"step": 1, "status": "completed"}],
                  "deviations": [], "blockers": []}

    class Model:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            tools = tools_override or self.tools
            seen.append((deepcopy(messages), {tool.name for tool in tools.list()}))
            number = len(seen)
            if number == 1:
                name, values = "read_file", {"path": "dependency.py"}
            elif number == 2:
                name, values = "edit_file", {"path": "app.py", "old": "'before'", "new": "'almost'"}
            elif number == 4:
                if "edit_file" in seen[-1][1]:
                    name, values = "edit_file", {"path": "app.py", "old": "'almost'", "new": "'after'"}
                else:
                    name, values = "executor_submit", {"status": "blocked",
                        "steps": [{"step": 1, "status": "blocked"}], "deviations": [],
                        "blockers": ["The resumed tool view exposes submission only."]}
            else:
                assert number in {3, 5}
                name, values = "executor_submit", submission
            step = AgentStep(type="tool_calls", calls=[{"id": f"call-{number}", "toolName": name, "input": values}])
            schemas = [{"type": "function", "function": {"name": t.name, "description": t.description,
                        "parameters": t.input_schema}} for t in tools.list()]
            input_tokens = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            output_tokens = estimate_tokens(json.dumps(step.calls))
            step.usage = ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens, source="estimated")
            bills.append((input_tokens, output_tokens))
            prices.append({"call": number,
                "canonicalInputEstimate": sum(estimate_message_tokens(m) for m in session.context_manager.messages),
                "requestInputEstimate": sum(estimate_message_tokens(m) for m in messages),
                "billedInputEstimateWithSchemas": input_tokens})
            return step

    def factory(*_args, **kwargs):
        model = Model()
        model.tools = kwargs["tools"]
        adapters.append(model)
        return model

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k:
                        ToolRegistry([read_file_tool, edit_file_tool]))
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    instructions = "Preserve all unrelated documented behavior. " * 3800
    snapshot_before_repair = None
    try:
        for number in (1, 2):
            assignment, store, channel, submit = _submit_binding(number)
            channels.append(channel)
            capability = ExecutorCheckCapability((public_check,), run_check,
                spec=ImplementationSpec(("app.py",), (public_check,), "Correct value while preserving unrelated behavior."))
            worker = _WorkerCallBudget(budget, f"assignment-{number}", max_calls=14,
                preserve_calls=1, preserve_tokens=17227)
            prompt = instructions if number == 1 else "Repair the existing candidate: the public test still expects after."
            data = {**_input(session, owner, prompt), "_executor_submit_tool": submit,
                    "_executor_check_capability": capability, "_result_key": channel.write_key,
                    "_result_instruction": f"CURRENT_BINDING: {assignment.assignment_id}"}
            result = task_module._run(data, replace(context, _agent_budget=worker, _subagent_mailbox=channel))
            capability.close()
            if number == 2:
                assert "edit_file" in seen[3][1], (
                    "Resume discarded projection and stranded the remaining repair budget.", prices[3])
            assert result.ok, result.output
            declaration = json.loads(store.read(channel.write_key).content)
            assert declaration["assignmentId"] == assignment.assignment_id
            channel.close()
            if number == 1:
                # Match V25's remaining token allowance without crediting back
                # any fixture call. The difference represents prior parent work.
                prior = 385150 - budget.snapshot().used_total_tokens
                assert prior >= 0
                budget.record_model_call(input_tokens=prior)
                snapshot_before_repair = worker.snapshot()
                assert snapshot_before_repair.limit_total_tokens - snapshot_before_repair.used_total_tokens == 97623
                session.context_manager.messages.append({"role": "user", "content": "Host batch check receipt retained.",
                    "_implementation_batch_feedback": True})
        assert len(adapters) == 1 and len(seen) == 5
        assert checks == [1, 0]
        assert "return 'after'" in (tmp_path / "app.py").read_text()
        first_packet = next(m["content"] for m in seen[0][0] if m.get("_implementation_context"))
        resumed_packet = next(m["content"] for m in seen[3][0] if m.get("_implementation_context"))
        assert "return 'before'" in first_packet and "return 'before'" not in resumed_packet
        assert "host-observed byte change" in resumed_packet
        original_read = next(m for m in seen[1][0] if m.get("role") == "tool_result" and m.get("toolUseId") == "call-1")
        assert original_read in seen[3][0]
        assert {"role": "user", "content": "Host batch check receipt retained.",
                "_implementation_batch_feedback": True} in seen[3][0]
        assert next(m["content"] for m in session.context_manager.messages if m.get("_implementation_context")) == first_packet
        assert all("CURRENT_BINDING: asg_session_2" in messages[0]["content"] for messages, _ in seen[3:])
        assert budget.snapshot().used_total_tokens == 385150 + sum(i + o for i, o in bills[3:])
        assert budget.snapshot().limit_total_tokens == 500000 and budget.snapshot().limit_model_calls == 40
        assert budget.snapshot().used_model_calls == 5
        assert budget.snapshot().reserved_total_tokens == 0
        assert 500000 - budget.snapshot().used_total_tokens >= 17227
        print(json.dumps({"kind": "local-two-assignment-admission-control", "source": "estimated",
            "remainingBeforeRepair": 97623, "prices": prices,
            "remainingAfterRepair": 500000 - budget.snapshot().used_total_tokens}, sort_keys=True))
    finally:
        for channel in channels:
            channel.close()
        session.close()


def test_request_view_state_rejects_other_model_and_close_discards_it(tmp_path):
    from minicode.executor_session import ExecutorSession
    from minicode.turn_completion import TurnCompletionPolicy

    owner, model = object(), object()
    session = ExecutorSession(owner=owner, workspace_root=str(tmp_path))
    context = _context(tmp_path)
    session.begin(owner=owner, context=context)
    session.bind_model(lambda: model, tools=ToolRegistry([]))
    state = session.request_view()
    observer = TurnCompletionPolicy(hard_limit=10, soft_limit=10, cwd=str(tmp_path))
    state.bind_observer(model=model, observer=observer)
    (tmp_path / "app.py").write_text("value = 1\n")
    call = {"id": "last-observed", "toolName": "edit_file",
            "input": {"path": "app.py", "old": "value = 1", "new": "value = 2"}}
    before = observer.capture_before([call])
    edited = ToolRegistry([edit_file_tool]).execute("edit_file", call["input"], context)
    assert edited.ok
    observer.observe_results([(call, edited)], before, step=1)
    state.calibration.observe(1200, 1000)
    with pytest.raises(ValueError, match="another Executor model"):
        state.bind_observer(model=object(), observer=observer)
    session.finish(outcome="failed")
    session.begin(owner=owner, context=context)
    assert session.request_view() is state
    assert state.calibration.estimate(800) == 1000
    assert state.changed_paths == frozenset({"app.py"}), "Retain the actual last observation before another request."
    session.finish(outcome="failed")
    session.close()
    with pytest.raises(ValueError, match="active Executor Assignment"):
        session.request_view()
