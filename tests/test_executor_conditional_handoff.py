"""A final affordable response can repair, check, and conditionally submit.

The V27 R24 ledger prefix is real; the task and model decisions are local test
fixtures. Every new request is metered from its actual messages and schemas as
estimated usage. Passing this test is not a successful real-model delivery.
"""

import json
from pathlib import Path
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

from test_executor_session_continuity import _context, _input, _session
from test_implementation_finalize_deadline import _submission_tools

from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget, _WorkflowRunSession
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


PREFIX_USED = 441311
DOWNSTREAM_FLOOR = 17417
EFFECTIVE_REMAINING = 41272


def _exercise(tmp_path, monkeypatch, *, mode):
    (tmp_path / "candidate.py").write_text("def value():\n    return 1\n")
    (tmp_path / "test_candidate.py").write_text(
        "from pathlib import Path\n"
        "def test_value():\n"
        "    namespace = {}\n"
        "    exec(Path('candidate.py').read_text(), namespace)\n"
        "    assert namespace['value']() == 2\n"
    )
    deadline = time.monotonic() + 600
    assignment, _order, store, channel, submit = _submission_tools(tmp_path)
    check = VerificationCheck("value", "tests", ("pytest", "-q", "test_candidate.py"), 30)
    checks, observed, bills, models, evidence = [], [], [], [], []
    parent = _context(tmp_path, _task_deadline_monotonic=deadline)
    budget = parent._agent_budget
    # Twenty-two earlier calls precede the observed failing check. The known
    # historical prefix is filled after that real check without refunding its
    # actual estimated request charge or pretending it was provider usage.
    for _ in range(22):
        lease = budget.reserve_model_call()
        budget.record_model_call(reservation=lease)
    worker = _WorkerCallBudget(budget, "conditional-executor", max_calls=7,
                              preserve_calls=10, preserve_tokens=DOWNSTREAM_FLOOR)

    def command(argv, **kwargs):
        assert tuple(argv) == ("pytest", "-q", "test_candidate.py")
        completed = subprocess.run([sys.executable, "-m", *argv], **kwargs)
        checks.append(completed.returncode)
        evidence.append((Path(kwargs["cwd"]), Path(kwargs["cwd"], "candidate.py").read_text()))
        if len(checks) == 1:
            assert completed.returncode == 1
            prior = PREFIX_USED - budget.snapshot().used_total_tokens
            assert prior >= 0
            budget.record_model_call(input_tokens=prior)
            assert budget.snapshot().used_model_calls == 23
            assert worker.snapshot().limit_total_tokens == 482583
            assert worker.snapshot().limit_model_calls == 29
            assert worker.snapshot().limit_total_tokens - worker.snapshot().used_total_tokens == EFFECTIVE_REMAINING
        return completed

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool]))

    class Model:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            tools = tools_override or self.tools
            names = {tool.name for tool in tools.list()}
            observed.append((deepcopy(messages), names))
            number = len(observed)
            assert number <= (3 if mode == "submit-first" else 2)
            if number == 1:
                calls = [{"id": "initial-check", "toolName": "executor_check", "input": {"check": 1}}]
            elif number == 3:
                # The malformed batch did not edit or submit anything. A
                # smaller funded response may now report that honest blocker.
                assert mode == "submit-first"
                assert any(m.get("toolUseId") == "final-submit" and m.get("isError")
                           and "implementation_handoff_order" in m.get("content", "") for m in messages)
                calls = [{"id": "honest-blocker", "toolName": "executor_submit", "input": {
                    "status": "blocked", "steps": [{"step": 1, "status": "failed"}],
                    "deviations": [], "blockers": ["The rejected batch left the public check failing."]}}]
            else:
                assert any(m.get("toolUseId") == "initial-check" and m.get("isError")
                           and m.get("_implementation_checks_failed") is True for m in messages)
                edit = {"id": "final-edit", "toolName": "edit_file", "input": {
                    "path": "candidate.py", "old": "return 1", "new": "return 2"}}
                completed = mode != "submit-first" and "edit_file" in names
                declaration = {"status": "completed" if completed else "blocked",
                    "steps": [{"step": 1, "status": "completed" if completed else "failed"}],
                    "deviations": [], "blockers": [] if completed else ["The public check still fails."]}
                submit_call = {"id": "final-submit", "toolName": "executor_submit", "input": declaration}
                if "edit_file" not in names:
                    calls = [submit_call]
                elif mode == "missing-submit":
                    calls = [edit]
                elif mode == "submit-first":
                    calls = [submit_call, edit]
                else:
                    calls = [edit, submit_call]
            schemas = [{"type": "function", "function": {"name": t.name,
                "description": t.description, "parameters": t.input_schema}} for t in tools.list()]
            sent = sum(estimate_message_tokens(m) for m in messages)
            input_tokens = sent + estimate_tokens(json.dumps(schemas))
            output_tokens = estimate_tokens(json.dumps(calls))
            bills.append({"requestInputEstimate": sent, "inputWithSchemas": input_tokens,
                          "outputEstimate": output_tokens})
            return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
                input_tokens=input_tokens, output_tokens=output_tokens, source="estimated"))

    def factory(*_args, **kwargs):
        model = Model()
        model.tools = kwargs["tools"]
        models.append(model)
        return model

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    with WorkflowWorkspaceTransaction(str(tmp_path), deadline_monotonic=deadline) as transaction:
        # Use the production factory on both revisions. V27 does not have the
        # digest-bound submit option; it must reach the tool-view assertion,
        # not fail while constructing a newly added capability API.
        runner = _WorkflowRunSession.__new__(_WorkflowRunSession)
        runner._state = SimpleNamespace(transaction=transaction, context=parent,
            requested_checks=(check,), locked_write_paths=("candidate.py", "test_candidate.py"),
            workflow_request=SimpleNamespace(scope=assignment.scope),
            workflow_id=assignment.workflow_id, task_id=assignment.task_id,
            task_prompt="Correct value and verify the assigned public test.",
            implementation_check_runs=0, implementation_check_statuses={}, implementation_check_latest={})
        capability = runner._executor_check_capability(assignment)
        owner = object()
        session = _session(owner, transaction.cwd)
        context = _context(transaction.cwd, _agent_budget=worker,
            _task_deadline_monotonic=deadline, _logical_agent_max_model_calls=7,
            _implementation_analysis_complete=True,
            _logical_agent_scope={"read_roots": (".",),
                "write_paths": ("candidate.py", "test_candidate.py"), "forbidden_paths": (".git",)})
        try:
            prompt = ("Repair value() from 1 to 2; the existing test expresses the required behavior. "
                      + "Preserve the unrelated documented invariant and its public behavior. " * 1000)
            data = {**_input(session, owner, prompt), "_executor_submit_tool": submit,
                    "_executor_check_capability": capability, "_result_key": channel.write_key,
                    "_result_instruction": "Submit through the current native result channel."}
            result = task_module._run(data, replace(context, _subagent_mailbox=channel))
            assert len(observed) == (3 if mode == "submit-first" else 2), result.output
            assert "edit_file" in observed[1][1], (
                "The last affordable response exposed only submit, losing a checked repair.", bills)
            assert len(models) == 1
            assert observed[0][0][0] in session.context_manager.messages
            current = Path(transaction.cwd, "candidate.py").read_text()
            artifact = store.read(channel.write_key)
            assert (tmp_path / "candidate.py").read_text() == "def value():\n    return 1\n", "A declaration cannot commit."
            assert all(path != Path(transaction.cwd) and not path.exists() for path, _ in evidence)
            assert budget.snapshot().used_total_tokens == PREFIX_USED + sum(
                bill["inputWithSchemas"] + bill["outputEstimate"] for bill in bills[1:])
            assert budget.snapshot().used_model_calls == 22 + len(observed)
            assert budget.snapshot().limit_total_tokens == 500000
            assert budget.snapshot().limit_model_calls == 40
            assert worker.snapshot().limit_total_tokens == 482583
            assert budget.snapshot().reserved_total_tokens == 0
            assert 500000 - budget.snapshot().used_total_tokens >= DOWNSTREAM_FLOOR
            assert context._task_deadline_monotonic == deadline
            # Check the admission boundary using the actual local input,
            # without labelling these controlled estimates provider counts.
            assert bills[1]["requestInputEstimate"] * 2 + 4096 + 2048 > EFFECTIVE_REMAINING
            assert bills[1]["inputWithSchemas"] + 4096 < EFFECTIVE_REMAINING
            print(json.dumps({"kind": "v27-conditional-handoff-local-proof", "usageSource": "estimated",
                "mode": mode, "prefixUsed": PREFIX_USED, "effectiveRemaining": EFFECTIVE_REMAINING,
                "downstreamFloor": DOWNSTREAM_FLOOR, "bills": bills,
                "remainingAfter": 500000 - budget.snapshot().used_total_tokens}, sort_keys=True))
            return result, artifact, current, checks, session.context_manager.messages
        finally:
            capability.close()
            channel.close()
            session.close()


def test_v27_last_affordable_response_repairs_checks_and_submits(tmp_path, monkeypatch):
    result, artifact, current, checks, messages = _exercise(tmp_path, monkeypatch, mode="repair-submit")
    assert result.ok, result.output
    assert checks == [1, 0]
    assert "return 2" in current
    assert json.loads(artifact.content)["status"] == "completed"
    submit = next(m for m in messages if m.get("role") == "tool_result" and m.get("toolUseId") == "final-submit")
    assert not submit["isError"] and '"verification":"pending"' in submit["content"]


def test_checked_edit_without_native_submit_does_not_complete(tmp_path, monkeypatch):
    result, artifact, current, checks, _messages = _exercise(tmp_path, monkeypatch, mode="missing-submit")
    assert checks == [1, 0] and "return 2" in current
    assert artifact is None
    assert not result.ok, result.output


def test_submission_before_write_rejects_the_entire_response(tmp_path, monkeypatch):
    result, artifact, current, checks, messages = _exercise(tmp_path, monkeypatch, mode="submit-first")
    assert checks == [1] and "return 1" in current
    assert json.loads(artifact.content)["status"] == "blocked"
    assert any(m.get("toolUseId") == "final-submit" and m.get("isError")
               and "implementation_handoff_order" in m.get("content", "") for m in messages)
    assert not any(m.get("role") == "tool_result" and m.get("toolUseId") == "final-edit"
                   and not m.get("isError") for m in messages)


def test_validator_rejection_invalidates_old_pass_then_same_executor_recovers(tmp_path, monkeypatch):
    """Validator failures reach the real capability before a native declaration."""
    original = "def value():\n    return 1\n"
    initial_test = ("from pathlib import Path\n"
                    "def test_value():\n"
                    "    namespace = {}\n"
                    "    exec(Path('candidate.py').read_text(), namespace)\n"
                    "    assert namespace['value']() == 1\n")
    (tmp_path / "candidate.py").write_text(original)
    (tmp_path / "test_candidate.py").write_text(initial_test)
    deadline = time.monotonic() + 600
    parent = _context(tmp_path, _task_deadline_monotonic=deadline)
    assignment, _order, store, channel, submit = _submission_tools(tmp_path)
    check = VerificationCheck("value", "tests", ("pytest", "-q", "test_candidate.py"), 30)
    observed, checks, models, bills = [], [], [], []

    def command(argv, **kwargs):
        assert tuple(argv) == ("pytest", "-q", "test_candidate.py")
        completed = subprocess.run([sys.executable, "-m", *argv], **kwargs)
        checks.append((completed.returncode, Path(kwargs["cwd"], "candidate.py").read_text(),
                       Path(kwargs["cwd"], "test_candidate.py").read_text()))
        return completed

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool]))

    class Model:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            observed.append(deepcopy(messages))
            number = len(observed)
            declaration = {"status": "completed", "steps": [{"step": 1, "status": "completed"}],
                           "deviations": [], "blockers": []}
            if number == 1:
                calls = [{"id": "old-pass", "toolName": "executor_check", "input": {"check": 1}}]
            elif number == 2:
                prior = next(m for m in messages if m.get("toolUseId") == "old-pass"
                             and m.get("role") == "tool_result")
                assert not prior["isError"] and checks[0][0] == 0
                calls = [{"id": "invalid-edit", "toolName": "edit_file", "input": {
                    "path": "candidate.py", "old": "return 1"}},
                    {"id": "stale-submit", "toolName": "executor_submit", "input": declaration}]
            elif number == 3:
                results = {m.get("toolUseId"): m for m in messages if m.get("role") == "tool_result"}
                assert results["invalid-edit"]["isError"]
                assert "Input validation error in edit_file" in results["invalid-edit"]["content"]
                assert results["stale-submit"]["isError"]
                assert store.read(channel.write_key) is None
                assert Path(transaction.cwd, "candidate.py").read_text() == original
                calls = [{"id": "correct-edit", "toolName": "edit_file", "input": {
                    "path": "candidate.py", "old": "return 1", "new": "return 2"}},
                    {"id": "correct-test", "toolName": "executor_test", "input": {
                        "path": "test_candidate.py", "code": initial_test.replace("== 1", "== 2")}},
                    {"id": "fresh-submit", "toolName": "executor_submit", "input": declaration}]
            else:
                raise AssertionError("Valid repair and fresh checks must finish this same Executor.")
            tools = tools_override or self.tools
            schemas = [{"type": "function", "function": {"name": t.name,
                "description": t.description, "parameters": t.input_schema}} for t in tools.list()]
            billed = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            output = estimate_tokens(json.dumps(calls))
            bills.append(billed + output)
            return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
                input_tokens=billed, output_tokens=output, source="estimated"))

    def factory(*_args, **kwargs):
        model = Model()
        model.tools = kwargs["tools"]
        models.append(model)
        return model

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    with WorkflowWorkspaceTransaction(str(tmp_path), deadline_monotonic=deadline) as transaction:
        runner = _WorkflowRunSession.__new__(_WorkflowRunSession)
        runner._state = SimpleNamespace(transaction=transaction, context=parent,
            requested_checks=(check,), locked_write_paths=("candidate.py", "test_candidate.py"),
            workflow_request=SimpleNamespace(scope=assignment.scope),
            workflow_id=assignment.workflow_id, task_id=assignment.task_id,
            task_prompt="Correct value and its public regression test.",
            implementation_check_runs=0, implementation_check_statuses={}, implementation_check_latest={})
        capability = runner._executor_check_capability(assignment)
        owner = object()
        session = _session(owner, transaction.cwd)
        context = _context(transaction.cwd, _agent_budget=parent._agent_budget,
            _task_deadline_monotonic=deadline, _logical_agent_max_model_calls=7,
            _implementation_analysis_complete=True, _subagent_mailbox=channel,
            _logical_agent_scope={"read_roots": (".",),
                "write_paths": ("candidate.py", "test_candidate.py"), "forbidden_paths": (".git",)})
        try:
            data = {**_input(session, owner, "Correct value to 2 and update its public test."),
                    "_executor_submit_tool": submit, "_executor_check_capability": capability,
                    "_result_key": channel.write_key,
                    "_result_instruction": "Submit through the current native result channel."}
            result = task_module._run(data, context)
            assert result.ok, result.output
            messages = session.context_manager.messages
            rejected = next(m for m in messages if m.get("role") == "tool_result"
                            and m.get("toolUseId") == "stale-submit")
            assert rejected["isError"], "A validator-rejected edit cannot reuse the old passing check."
            assert len(observed) == 3 and len(models) == 1
            assert [entry[0] for entry in checks] == [0, 0]
            assert "return 1" in checks[0][1] and "== 1" in checks[0][2]
            assert "return 2" in checks[1][1] and "== 2" in checks[1][2]
            artifact = store.read(channel.write_key)
            assert json.loads(artifact.content)["status"] == "completed"
            accepted = next(m for m in messages if m.get("role") == "tool_result"
                            and m.get("toolUseId") == "fresh-submit")
            assert rejected["isError"] and not accepted["isError"]
            assert (tmp_path / "candidate.py").read_text() == original
            assert (tmp_path / "test_candidate.py").read_text() == initial_test
            assert parent._agent_budget.snapshot().used_model_calls == 3
            assert parent._agent_budget.snapshot().used_total_tokens == sum(bills)
            assert parent._agent_budget.snapshot().reserved_total_tokens == 0
        finally:
            capability.close()
            channel.close()
            session.close()
