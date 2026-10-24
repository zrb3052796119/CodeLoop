"""A second actionable failure must keep the same candidate and Executor.

Local worker call slices produce real admission stops, while the shared ledger
retains ample allowance. Model decisions are scripted; edits, pytest, frozen
candidates, formal verification, review and durable audit use production paths.
This is a mechanism regression, not a replay of provider usage or model quality.
"""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_failed_candidate_repair import INITIAL, PARTIAL, COMPLETE, TEST, REPAIRED_TEST


BAD_TEST = "from missing_fixture import prepare\n" + REPAIRED_TEST


def test_second_actionable_failure_resumes_same_executor_with_budget_and_candidate(tmp_path, monkeypatch):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_candidate.py").write_text(TEST)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    deadline = time.monotonic() + 1800
    calls, billed, workers, adapters, commands, review_receipts = [], [], [], [], [], []
    original_worker = task_module._run

    def run_worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        if context._logical_agent_role == "executor":
            number = 1 + sum(item["role"] == "executor" for item in workers)
            if number <= 2:
                # A strict local slice must not spend the rest of the workflow
                # grant. Its exhaustion is real, not a synthetic worker result.
                context = replace(context, _agent_budget=_WorkerCallBudget(
                    context._agent_budget, f"test_slice_{number}", max_calls=1, preserve_calls=0))
            if number >= 2:
                assert (Path(context.cwd) / "candidate.py").read_text() == PARTIAL
                expected = BAD_TEST if number == 2 else REPAIRED_TEST
                assert (Path(context.cwd) / "test_none.py").read_text() == expected
        workers.append({"role": context._logical_agent_role, "session": data.get("_executor_session"),
                        "budget": budget.snapshot(), "deadline": context._task_deadline_monotonic})
        if context._logical_agent_role == "analyst":
            # Script only semantic diagnosis. Actual formal failures, frozen
            # source and the assigned ledger channel remain authoritative.
            formal = [item for item in commands if item["formal"]]
            assert formal and formal[-1]["exit"] in {1, 2}
            assert target.read_text() == INITIAL
            assert (Path(context.cwd) / "candidate.py").read_text() == PARTIAL
            bad_fixture = formal[-1]["exit"] == 2
            test_source = (Path(context.cwd) / "test_none.py").read_text()
            assert test_source == (BAD_TEST if bad_fixture else REPAIRED_TEST)
            assignment = json.loads(data["prompt"].split("ASSIGNMENT:\n", 1)[1])
            repair_path = "test_none.py" if bad_fixture else "candidate.py"
            diagnosis = ("The regression imports nonexistent missing_fixture; repair that test import."
                if bad_fixture else "The public None regression now executes and exposes the missing None behavior.")
            context._subagent_mailbox.write(data["_result_key"], json.dumps({
                "resultVersion": 1, "assignmentId": assignment["assignmentId"],
                "status": "completed", "findings": [{"claimId": "current_failure", "statement": diagnosis,
                    "evidence": [{"path": repair_path, "lineStart": 1, "lineEnd": 1}], "confidence": "high"}],
                "unknowns": [], "risks": [], "implementationOrderCandidate": {
                    "required": True, "steps": [{"id": "repair_current_failure", "action": diagnosis,
                        "allowedPaths": [repair_path]}], "verificationRequests": []}, "ballot": None,
            }), author="analyst")
            return ToolResult(ok=True, output=diagnosis)
        result = original_worker(data, context)
        if context._logical_agent_role == "executor" and number <= 2:
            assert context._agent_budget.admitted_model_calls == 1
        workers[-1]["result"] = {"ok": result.ok, "reason": str(result._termination_reason), "output": result.output[:1500]}
        return result

    def call(name, values, identity):
        return {"id": identity, "toolName": name, "input": values}

    def response(messages, tools, actions):
        schemas = [{"type": "function", "function": {"name": tool.name,
                    "description": tool.description, "parameters": tool.input_schema}}
                   for tool in tools.list()]
        input_tokens = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
        output_tokens = estimate_tokens(json.dumps(actions))
        billed.append(input_tokens + output_tokens)
        return AgentStep(type="tool_calls", calls=actions,
                         usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens, source="estimated"))

    class Executor:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            calls.append(("executor", deepcopy(messages)))
            number = sum(role == "executor" for role, _ in calls)
            tools = tools_override or self.tools
            if number == 1:
                actions = [call("edit_file", {"path": "candidate.py", "old": INITIAL, "new": PARTIAL}, "first-source-edit"),
                           call("executor_test", {"path": "test_none.py", "code": BAD_TEST}, "bad-regression")]
            elif number == 2:
                assert "missing_fixture" in str(messages) and "ModuleNotFoundError" in str(messages)
                assert any(item["formal"] and item["exit"] == 2 for item in commands)
                assert any(message.get("toolUseId") == "first-source-edit" for message in messages)
                actions = [call("executor_test", {"path": "test_none.py", "code": REPAIRED_TEST}, "fix-regression")]
            elif number == 3:
                assert "AttributeError" in str(messages), "the product failure must reach the original Executor"
                assert any(item["formal"] and item["exit"] == 1 for item in commands)
                assert any(message.get("toolUseId") == "fix-regression" for message in messages)
                assert any(message.get("toolUseId") == "first-source-edit" for message in messages)
                actions = [call("edit_file", {"path": "candidate.py", "old": PARTIAL, "new": COMPLETE}, "fix-product")]
            elif number == 4:
                assert any(message.get("toolUseId") == "fix-product" and not message.get("isError") for message in messages)
                count = next(tool for tool in tools.list() if tool.name == "executor_submit").input_schema[
                    "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                actions = [call("executor_submit", {"status": "completed",
                    "steps": [{"step": step, "status": "completed"} for step in range(1, count + 1)],
                    "deviations": [], "blockers": []}, "deliver")]
            else:
                raise AssertionError("unexpected replacement Executor or unbounded retry")
            return response(messages, tools, actions)

    class Reviewer:
        tools = None
        requests = 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.requests += 1
            calls.append(("reviewer", deepcopy(messages)))
            tools = tools_override or self.tools
            assert target.read_text() == INITIAL
            if self.requests == 1:
                actions = [call("read_file", {"path": "candidate.py"}, "review-source"),
                           call("read_file", {"path": "test_none.py"}, "review-tests")]
            elif self.requests == 2:
                actions = [call("review_test", {"criterionIds": ["trim", "none"],
                    "scenario": "Whitespace normalization and None handling satisfy both requested behaviors.",
                    "code": REPAIRED_TEST}, "independent-review-probe")]
            else:
                assert self.requests == 3
                probe_result = next(message for message in messages
                    if message.get("role") == "tool_result" and message.get("toolUseId") == "independent-review-probe")
                receipt, _ = json.JSONDecoder().raw_decode(probe_result["content"].lstrip())
                assert not probe_result.get("isError") and receipt["status"] == "passed"
                assert receipt["evidence"]["exitCode"] == 0
                review_receipts.append(receipt)
                assert any(message.get("toolUseId") == "review-source" and "if value is None:" in message.get("content", "")
                           for message in messages if message.get("role") == "tool_result")
                assert any(message.get("toolUseId") == "review-tests" and "test_none" in message.get("content", "")
                           for message in messages if message.get("role") == "tool_result")
                actions = [call("review_submit", {"verdict": "approved",
                    "criteria": [{"criterionId": cid, "status": "satisfied",
                        "rationale": "The independent normalization and None probe passed on this candidate.",
                        "probeIds": [receipt["probeId"]], "sourceEvidence": []} for cid in ("trim", "none")],
                    "findings": [], "warnings": [], "requestedChecks": [], "confidence": "high"}, "review-deliver")]
            return response(messages, tools, actions)

    executor = Executor()

    def factory(_runtime, agent_type, **kwargs):
        model = executor if agent_type == "general" else Reviewer()
        model.tools = kwargs["tools"]
        adapters.append((agent_type, model))
        return model

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class ObservedVerifier(DeterministicVerifier):
        def run(self, **kwargs):
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            assert target.read_text() == INITIAL, "failed or unreviewed candidates must not touch the parent"
            result = super().run(**kwargs)
            commands.extend({"formal": formal, "exit": item["exitCode"], "cwd": kwargs["cwd"],
                             "usedTokens": budget.snapshot().used_total_tokens,
                             "usedCalls": budget.snapshot().used_model_calls}
                            for item in result["checks"])
            return result

    verifier = ObservedVerifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(task_module, "_run", run_worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_repeated_repair", "kind": "change", "risk": "low", "complex": False,
                    "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_none.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace."},
                     {"id": "none", "kind": "behavior", "text": "Return an empty string for None."}],
        "verificationChecks": [
            {"checkId": "compat", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_candidate.py"], "criterionIds": ["trim"]},
            {"checkId": "regression", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_none.py"], "criterionIds": ["none"]},
        ]}
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="repeated same-Executor repair",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "finish both public behaviors", "agent_type": "workflow",
            "prompt": "Strip whitespace and return an empty string for None, including a public regression test. Independently review the resulting change.",
            "workflow_request": request}, ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture", "modelTimeoutSeconds": 300},
                _agent_budget=budget, _task_deadline_monotonic=deadline, _event_sink=observation,
                _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None, result.output
    snapshot = budget.snapshot()
    print(json.dumps({"usageSource": "estimated", "usedTokens": snapshot.used_total_tokens,
        "usedCalls": snapshot.used_model_calls, "globalTokens": snapshot.limit_total_tokens,
        "globalCalls": snapshot.limit_model_calls, "formalExits": [item["exit"] for item in commands if item["formal"]],
        "terminalReason": trace["terminalReason"], "committed": trace["committed"],
        "workers": [item.get("result") for item in workers], "checks": [item["exit"] for item in commands]}))
    assert snapshot.used_total_tokens == sum(billed)
    assert snapshot.used_model_calls == len(calls)
    assert snapshot.limit_total_tokens == 500000 and snapshot.limit_model_calls == 40
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_total_tokens < 150000 and snapshot.used_model_calls == 7
    assert time.monotonic() < deadline - 120
    assert [item["exit"] for item in commands if item["formal"]][:4] == [0, 2, 0, 1]
    assert result.ok and trace["committed"], (
        "The second actionable failure stopped despite ample unchanged budget and the same candidate:",
        trace["terminalReason"], snapshot.used_total_tokens, snapshot.used_model_calls,
    )
    assert len(review_receipts) == 1
    assert len(result.output) <= 8000
    assert '"traceProjection": "compacted"' in result.output
    assert target.read_text() == COMPLETE
    assert (tmp_path / "test_none.py").read_text() == REPAIRED_TEST
    assert [kind for kind, _ in adapters].count("general") == 1
    executor_workers = [item for item in workers if item["role"] == "executor"]
    assert len(executor_workers) == 3
    assert executor_workers[0]["session"] is not None
    assert all(item["session"] is executor_workers[0]["session"] for item in executor_workers)
    assert [item["exit"] for item in commands if item["formal"]] == [0, 2, 0, 1, 0, 0]
    assert all(not Path(item["cwd"]).exists() for item in commands)
    reloaded = RunJournal(tmp_path, data_dir=data_dir)
    audit = reloaded.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None and audit.trace["committed"]
    full_transitions = audit.trace["transitions"]
    assert sum(item["outcome"] == "repairing" for item in full_transitions) == 2
    assert any(item["stage"] == "analyst" and item["outcome"] == "completed"
               for item in full_transitions)
    assert not any(item["stage"] == "analyst" for item in trace["transitions"])
    for stage in ("executor", "verifier", "reviewer"):
        latest = [item for item in full_transitions
                  if item["stage"] == stage and item["outcome"] == "completed"][-1]
        projected = [item for item in trace["transitions"]
                     if item["stage"] == stage and item["outcome"] == "completed"]
        assert len(projected) == 1
        assert projected[0]["artifactId"] == latest["artifactId"]
    assert reloaded.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    sessions = [event.payload for child in reloaded.list_subagent_runs(run_id)
                for event in reloaded.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    assert len(sessions) == 3 and len({item["sessionId"] for item in sessions}) == 1
    assert sum(item["resumed"] is True and item["modelReused"] is True and item["priorMessageCount"] > 0
               for item in sessions) == 2
    assert any(item["stage"] == "reviewer" and item["outcome"] == "completed" for item in trace["transitions"])
