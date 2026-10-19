"""Continuous implementation has no implicit phase quotas or tool cutoffs.

These tests exercise the controller and actual local tools, not model quality.
Explicit owner deadlines, registry permissions and provider output caps remain
authoritative; the phase policy does not create new authority or allowance.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from threading import Event

import pytest
from test_implementation_finalize_deadline import _submission_tools

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import (
    ImplementationPhase,
    ImplementationPhaseController,
)
from minicode.permissions import PermissionManager
from minicode.tooling import (
    ToolContext,
    ToolDefinition,
    ToolExecutionAbandoned,
    ToolRegistry,
    ToolResult,
)
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage


def _snapshot():
    return AgentTurnBudget(max_total_tokens=None, max_model_calls=None,
                           max_cost_usd=None).snapshot()


def _controller(**kwargs):
    return ImplementationPhaseController(snapshot=_snapshot(), max_steps=None,
                                         now=0, deadline=None, **kwargs)


def _call(name, identity="call", **values):
    return {"id": identity, "toolName": name, "input": values}


def test_default_continues_beyond_previous_call_and_stage_time_limits():
    policy = _controller(output_limit=8192)
    for count in range(75):
        request = policy.request(_snapshot(), estimated_input=45000, now=600 + count * 30)
        assert not request.handoff_only, "phase percentages must not remove unfinished work tools"
        assert request.preserve_calls == 0
        assert request.preserve_tokens == 8192, "only this response's output is reserved"
        assert request.output_limit == 8192
        assert request.deadline is None
        assert request.phase is ImplementationPhase.IMPLEMENT
        assert request.message is None
        assert not policy.exhausted, "there is no implicit 50-call ceiling"
        policy.admitted()
        policy.observe_verification(failed=True)
        policy.timeout(now=600 + count * 30)
    assert policy.calls == 75 and not policy.exhausted
    assert policy.handoff_start is None


def test_explicit_shared_limits_do_not_implicitly_enable_phase_quotas():
    budget = AgentTurnBudget(max_total_tokens=500000, max_model_calls=40)
    policy = ImplementationPhaseController(snapshot=budget.snapshot(), max_steps=37,
        now=0, deadline=600, analysis_complete=True)
    budget.record_model_call(input_tokens=470000)
    for _ in range(35):
        policy.admitted()
    request = policy.request(budget.snapshot(), estimated_input=1000, now=590)
    assert not request.handoff_only and request.preserve_calls == 0
    assert request.deadline == 600 and policy.allowed_name("edit_file")
    # Only actual input/output admission consumes the unchanged explicit cap.
    lease = budget.reserve_model_call(1000, preserve_total_tokens=request.preserve_tokens)
    budget.record_model_call(input_tokens=1000, output_tokens=100, reservation=lease)
    assert budget.snapshot().limit_total_tokens == 500000
    assert budget.snapshot().used_total_tokens == 471100


@pytest.mark.parametrize("owner,timeout,now,expected", [
    (600, None, 599, 600), (600, 300, 599, 600),
    (None, 300, 900, 1200), (None, 17.5, 900, 917.5),
    (None, None, 900, None), (None, "invalid", 900, None),
])
def test_only_explicit_request_and_owner_deadlines_apply(owner, timeout, now, expected):
    policy = ImplementationPhaseController(snapshot=_snapshot(), max_steps=None,
        now=0, deadline=owner, request_timeout_seconds=timeout)
    request = policy.request(_snapshot(), estimated_input=45000, now=now)
    assert request.deadline == expected
    assert not request.handoff_only and policy.handoff_start is None
    assert policy.allowed_name("edit_file") and policy.allowed_name("read_file")


def test_original_registry_and_schema_survive_extended_work_and_failed_edits():
    extra = ToolDefinition(name="file_tree", description="authorized discovery",
        input_schema={"type": "object"}, validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="authorized"))
    registry = ToolRegistry([edit_file_tool, read_file_tool, extra])
    policy = _controller()
    policy.observe_change(True)
    policy.observe_edit(path="app.py", anchor_failed=True, changed=False)
    policy.request(_snapshot(), estimated_input=45000, now=10000)
    view = policy.tool_view(registry)
    assert view is registry
    assert view.find("read_file").input_schema["properties"]["limit"]["maximum"] == 20000
    calls = [_call("read_file", str(i), path="app.py", limit=20000) for i in range(30)]
    calls += [_call("file_tree", "discover"),
              _call("grep_files", "search", path=".", pattern="target")]
    assert policy.admit_batch(calls) == {}
    assert policy.repair_pending


def test_real_edit_and_pytest_remain_available_after_old_handoff_floor(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def value():\n    return 1\n")
    (tmp_path / "test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() == 2\n")
    owner = time.monotonic() + 30
    policy = ImplementationPhaseController(snapshot=_snapshot(), max_steps=None,
        now=owner - 600, deadline=owner)
    for _ in range(60):
        policy.admitted()
    request = policy.request(_snapshot(), estimated_input=45000, now=owner - 1)
    assert not request.handoff_only
    registry = ToolRegistry([edit_file_tool])
    assert policy.tool_view(registry).find("edit_file") is edit_file_tool
    edit = _call("edit_file", path="app.py", old="return 1", new="return 2")
    assert policy.admit_batch([edit]) == {}
    context = ToolContext(cwd=str(tmp_path), _task_deadline_monotonic=owner,
        permissions=PermissionManager(str(tmp_path), prompt=lambda _: {"decision": "allow_once"}))
    result = registry.execute(edit["toolName"], edit["input"], context)
    assert result.ok, result.output
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_app.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False,
        env={**os.environ, "PYTHONPATH": str(tmp_path),
             "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert source.read_text() == "def value():\n    return 2\n"


@pytest.mark.parametrize("boundary", ["permission", "deadline", "abandoned"])
def test_continuous_view_cannot_override_real_write_guards(tmp_path, boundary):
    source = tmp_path / "app.py"
    source.write_text("value = 1\n")
    context = ToolContext(cwd=str(tmp_path), permissions=PermissionManager(
        str(tmp_path), prompt=lambda _: {"decision": "deny" if boundary == "permission" else "allow_once"}))
    if boundary == "deadline":
        context._task_deadline_monotonic = time.monotonic() - 1
    if boundary == "abandoned":
        context._tool_abandoned = Event()
        context._tool_abandoned.set()
    registry = ToolRegistry([edit_file_tool])
    policy = _controller()
    policy.request(_snapshot(), estimated_input=1000, now=10000)
    edit = _call("edit_file", path="app.py", old="value = 1", new="value = 2")
    assert policy.admit_batch([edit]) == {}
    if boundary == "abandoned":
        with pytest.raises(ToolExecutionAbandoned):
            policy.tool_view(registry).execute("edit_file", edit["input"], context)
    else:
        result = policy.tool_view(registry).execute("edit_file", edit["input"], context)
        assert not result.ok
    assert source.read_text() == "value = 1\n"


def test_conditional_submission_order_gate_still_rejects_unexecuted_changes():
    policy = _controller(allow_conditional_handoff=True)
    calls = [_call("executor_submit", "submit", status="completed"),
             _call("edit_file", "edit", path="app.py", old="1", new="2")]
    rejected = policy.admit_batch(calls)
    assert set(rejected) == {"submit", "edit"}
    assert all("implementation_handoff_order" in reason for reason in rejected.values())


def test_explicit_bounded_mode_retains_old_phase_schedule_and_tool_view():
    budget = AgentTurnBudget(max_total_tokens=500000, max_model_calls=40)
    policy = ImplementationPhaseController(snapshot=budget.snapshot(), max_steps=37,
        now=0, deadline=600, bounded=True)
    assert policy.investigation_calls == 6 and policy.final_call_limit == 2
    first = policy.request(budget.snapshot(), estimated_input=1000, now=0)
    assert first.phase is ImplementationPhase.INVESTIGATE
    assert first.preserve_calls == 2 and first.output_limit == 2048
    final = policy.request(budget.snapshot(), estimated_input=1000, now=599)
    assert final.phase is ImplementationPhase.FINALIZE
    assert not policy.allowed_name("read_file")
    assert policy.handoff_start < policy.deadline == 600
    assert policy.tool_view(ToolRegistry([read_file_tool])).list() == []


def test_real_continuous_loop_passes_fifty_calls_then_edits_tests_and_submits(tmp_path):
    source = tmp_path / "candidate.py"
    source.write_text("value = 1\n")
    test_file = tmp_path / "test_candidate.py"
    test_file.write_text("from candidate import value\ndef test_value():\n    assert value == 2\n")
    notes = tmp_path / "notes"
    notes.mkdir()
    for index in range(55):
        (notes / f"part_{index}.txt").write_text(f"Public source fragment {index}\n")
    _, _, store, channel, submit = _submission_tools(tmp_path)
    shared = AgentTurnBudget(max_total_tokens=None, max_model_calls=None, max_cost_usd=None)
    checks = []

    def digest(_context):
        return "sha256:" + hashlib.sha256(source.read_bytes() + test_file.read_bytes()).hexdigest()

    def check(item, context):
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=20, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        checks.append(completed.returncode)
        return {"checkId": item.check_id, "status": "passed" if completed.returncode == 0 else "failed",
                "patchDigest": digest(context), "stdout": completed.stdout, "stderr": completed.stderr}

    cap = ExecutorCheckCapability((VerificationCheck(
        "public", "tests", ("pytest", "-q", "test_candidate.py"), 20),),
        check, current_patch_digest=digest)
    registry = cap.bind_registry(ToolRegistry([read_file_tool, edit_file_tool, submit]))

    class Model:
        count = 0

        def next(self, messages, *, tools_override, **_kwargs):
            self.count += 1
            names = {tool.name for tool in tools_override.list()}
            assert {"read_file", "edit_file", "executor_submit"} <= names
            if self.count <= 55:
                calls = [_call("read_file", f"read-{self.count}", path=f"notes/part_{self.count - 1}.txt")]
            elif self.count == 56:
                calls = [_call("edit_file", "actual-edit", path="candidate.py", old="value = 1", new="value = 2")]
            else:
                assert self.count == 57 and checks == [0]
                assert any(message.get("toolUseId") == "actual-edit" and not message.get("isError")
                           for message in messages)
                calls = [_call("executor_submit", "actual-submit", status="completed",
                    steps=[{"step": 1, "status": "completed"}], deviations=[], blockers=[])]
            schemas = [{"name": tool.name, "parameters": tool.input_schema} for tool in tools_override.list()]
            return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
                input_tokens=sum(estimate_message_tokens(message) for message in messages)
                    + estimate_tokens(json.dumps(schemas)),
                output_tokens=estimate_tokens(json.dumps(calls)), source="estimated"))

    model = Model()
    try:
        transcript = run_agent_turn(model=model, tools=registry,
            messages=[{"role": "user", "content": "Change candidate.py value to 2, test and submit."}],
            cwd=str(tmp_path), implementation_task=True, enable_work_chain=False,
            agent_budget=shared, subagent_mailbox=channel, max_steps=None, deadline_monotonic=None,
            permissions=PermissionManager(str(tmp_path), prompt=lambda _: {"decision": "allow_once"}))
        assert model.count == shared.snapshot().used_model_calls == 57
        assert checks == [0] and source.read_text() == "value = 2\n"
        assert json.loads(store.read(channel.write_key).content)["status"] == "completed"
        assert any(message.get("toolUseId") == "actual-submit" and not message.get("isError")
                   for message in transcript)
    finally:
        cap.close()
        channel.close()
