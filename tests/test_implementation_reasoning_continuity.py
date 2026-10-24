"""Effort follows the actual authorized work view, not the first edit.

The provider boundary is scripted; edits, pytest, native completion gates and
shared ledger accounting are real. This proves request classification only,
not that high effort makes a live model's implementation correct.

Legacy phase-specific expectations explicitly select bounded execution. The
default continuous mode retains the authorized write view and output limit.
"""

from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.permissions import PermissionManager
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_implementation_finalize_deadline import _submission_tools


@pytest.mark.parametrize("bounded", [True, False], ids=["bounded", "continuous-default"])
@pytest.mark.parametrize("max_steps,repair_tool,efforts", [
    (8, "edit_file", ("low", "high", "max")),
    (3, "edit_file", ("low", "high", "max")),
    (3, "executor_test", ("low", "high", "max")),
    (3, "edit_file", ()),
    (3, "edit_file", ("high",)),
])
def test_same_executor_keeps_implementation_effort_through_real_failed_check(
    tmp_path, max_steps, repair_tool, efforts, bounded,
):
    source = tmp_path / "candidate.py"
    test = tmp_path / "test_candidate.py"
    source.write_text("def value():\n    return 0\n")
    test.write_text("from candidate import value\n\ndef test_value():\n    assert value() == 2\n")
    if repair_tool == "executor_test":
        test.write_text(test.read_text().replace("== 2", "== 0"))
    assignment, _, store, channel, submit = _submission_tools(tmp_path)
    checks, seen, bills = [], [], []
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    for index in range(10):
        lease = shared.reserve_model_call()
        shared.record_model_call(input_tokens=250000 if index == 0 else 0, reservation=lease)
    worker = _WorkerCallBudget(shared, "reasoning-continuity", max_calls=max_steps,
                              preserve_calls=10, preserve_tokens=74000)

    def digest(_context):
        return "sha256:" + hashlib.sha256(source.read_bytes() + test.read_bytes()).hexdigest()

    def check(item, context):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(tmp_path),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "",
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
        checks.append(completed.returncode)
        return {"checkId": item.check_id,
                "status": "passed" if completed.returncode == 0 else "failed",
                "patchDigest": digest(context), "stdout": completed.stdout,
                "stderr": completed.stderr}

    cap = ExecutorCheckCapability(
        (VerificationCheck("public", "tests", ("pytest", "-q", "test_candidate.py"), 30),),
        check, test_paths=("test_candidate.py",), current_patch_digest=digest,
    )
    registry = cap.bind_registry(ToolRegistry([edit_file_tool, submit]))
    declaration = {"id": "deliver", "toolName": "executor_submit", "input": {
        "status": "completed", "steps": [{"step": 1, "status": "completed"}],
        "deviations": [], "blockers": [],
    }}

    class Model:
        supported_reasoning_efforts = efforts

        def next(self, messages, **options):
            view = options["tools_override"].list()
            seen.append({"names": {tool.name for tool in view},
                         "effort": options.get("reasoning_effort_override"),
                         "cap": options["max_output_tokens_override"],
                         "retries": options["max_retries_override"]})
            if len(seen) == 1:
                calls = [{"id": "initial-edit", "toolName": "edit_file", "input": {
                    "path": "candidate.py", "old": "return 0",
                    "new": "return 1" if repair_tool == "edit_file" else "return 2",
                }}]
            elif len(seen) == 2:
                assert checks == [1], "the same model must receive a real failed check"
                assert any(message.get("role") == "tool_result"
                           and message.get("toolUseId") == "initial-edit"
                           and not message.get("isError") for message in messages)
                assert any("failed" in message.get("content", "") for message in messages)
                if repair_tool == "edit_file":
                    calls = [{"id": "repair", "toolName": repair_tool, "input": {
                        "path": "candidate.py", "old": "return 1", "new": "return 2",
                    }}]
                else:
                    calls = [{"id": "repair", "toolName": repair_tool, "input": {
                        "path": "test_candidate.py", "code":
                        "from candidate import value\n\ndef test_value():\n    assert value() == 2\n",
                    }}]
            else:
                assert len(seen) == 3 and checks == [1, 0]
                calls = [deepcopy(declaration)]
            schemas = [{"name": tool.name, "parameters": tool.input_schema} for tool in view]
            input_tokens = (sum(estimate_message_tokens(message) for message in messages)
                            + estimate_tokens(json.dumps(schemas)))
            output_tokens = estimate_tokens(json.dumps(calls))
            assert output_tokens <= seen[-1]["cap"]
            bills.append(input_tokens + output_tokens)
            return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
                input_tokens=input_tokens, output_tokens=output_tokens, source="estimated"))

    model = Model()
    try:
        transcript = run_agent_turn(
            model=model, tools=registry,
            messages=[{"role": "user", "content": "Make value return 2 and verify the regression test."}],
            cwd=str(tmp_path), runtime={"maxOutputTokens": 8192},
            permissions=PermissionManager(str(tmp_path), prompt=lambda _: {"decision": "allow_once"}),
            agent_budget=worker, subagent_mailbox=channel,
            deadline_monotonic=time.monotonic() + 1800,
            implementation_task=True, implementation_analysis_complete=True,
            max_steps=max_steps, agent_depth=1, enable_work_chain=False,
            **({"bounded_implementation_phases": True} if bounded else {}),
        )
        assert checks == [1, 0]
        assert source.read_text() == "def value():\n    return 2\n"
        assert "assert value() == 2" in test.read_text()
        assert repair_tool in seen[1]["names"]
        handoff_only = bounded and max_steps == 3
        expected = ["high", "high", "low" if handoff_only else "high"]
        if not {"low", "high"}.issubset(efforts):
            expected = [None, None, None]
        assert [request["effort"] for request in seen] == expected, (
            "Authorized implementation/repair was downgraded after the first successful edit", seen)
        assert model.supported_reasoning_efforts == efforts
        assert all(request["retries"] == 0 for request in seen)
        assert [request["cap"] for request in seen] == [8192, 8192, 2048 if handoff_only else 8192]
        if handoff_only:
            assert seen[-1]["names"] == {"executor_submit"}
        elif not bounded:
            assert all(request["names"] == {tool.name for tool in registry.list()} for request in seen)
        artifact = json.loads(store.read(channel.write_key).content)
        assert artifact["status"] == "completed" and artifact["assignmentId"] == assignment.assignment_id
        assert any(message.get("toolUseId") == "deliver" and message.get("role") == "tool_result"
                   and not message.get("isError") for message in transcript)
        assert shared.snapshot().used_total_tokens == 250000 + sum(bills)
        assert shared.snapshot().used_model_calls == 13
        assert shared.snapshot().reserved_total_tokens == 0
        assert (shared.snapshot().limit_total_tokens, shared.snapshot().limit_model_calls) == (500000, 40)
        assert worker.snapshot().limit_total_tokens == 426000
        assert 500000 - shared.snapshot().used_total_tokens >= 74000
    finally:
        cap.close()
        channel.close()


@pytest.mark.parametrize("bounded", [True, False], ids=["bounded", "continuous-default"])
@pytest.mark.parametrize("analysis_complete,max_steps,expected", [
    (False, 20, ["low"]),
    (True, 3, ["high", "low"]),
])
def test_investigation_and_final_view_effort_matches_execution_mode(
    tmp_path, analysis_complete, max_steps, expected, bounded,
):
    seen = []
    todo = ToolDefinition(name="todo_write", description="bookkeeping",
                          input_schema={"type": "object"}, validator=lambda value: value,
                          run=lambda _args, _context: ToolResult(ok=True, output="recorded"))

    class Model:
        supported_reasoning_efforts = ("low", "high", "max")

        def next(self, messages, **options):
            names = {tool.name for tool in options["tools_override"].list()}
            seen.append((options.get("reasoning_effort_override"), names))
            if analysis_complete and len(seen) == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "bookkeeping", "toolName": "todo_write", "input": {},
                }])
            return AgentStep(type="assistant", content="No implementation is claimed; work remains incomplete.")

    run_agent_turn(model=Model(), tools=ToolRegistry([edit_file_tool, todo]),
        messages=[{"role": "user", "content": "Correct the source and tests."}],
        cwd=str(tmp_path), implementation_task=True,
        implementation_analysis_complete=analysis_complete,
        max_steps=max_steps, enable_work_chain=False,
        **({"bounded_implementation_phases": True} if bounded else {}))
    if not bounded:
        expected = ["high"] * len(expected)
    assert [effort for effort, _names in seen] == expected
    if analysis_complete and bounded:
        assert seen[-1][1] == {"todo_write"}
    elif bounded:
        assert "edit_file" in seen[0][1], "investigation stays low even if a write is available"
    else:
        assert all(names == {"edit_file", "todo_write"} for _effort, names in seen)
