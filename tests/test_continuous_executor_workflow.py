"""Real local execution with two external reviews and one persistent writer.

Only model decisions and sandbox bootstrap are fixtures. Task dispatch, loop,
file tools, subprocess pytest, native result gates, fresh verification, review,
audit and promotion run through production. No provider calls are made.
"""

import json
import sys
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from minicode import workflow_finalization
from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_audit import (
    SchedulerAuditValidationError,
    project_scheduler_audit,
)
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage

INITIAL = "def value():\n    return 0\n"


def _source(revision):
    return f"def value():\n    return {revision}\n"


def _test(revision):
    return ("from candidate import value\n"
            f"def test_candidate_value():\n    assert value() == {revision}\n")


def _exercise(tmp_path, monkeypatch, *, max_tokens=None, failed_check=False):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_compat.py").write_text(
        "from candidate import value\ndef test_integer():\n    assert isinstance(value(), int)\n"
    )
    calls, billed, workers, adapters, commands, audit_errors = [], [], [], [], [], []
    budget_refusals = []

    class ObservedBudget(AgentTurnBudget):
        def reserve_model_call(self, estimated_input_tokens=0, **kwargs):
            try:
                return super().reserve_model_call(estimated_input_tokens, **kwargs)
            except AgentBudgetExceeded as error:
                budget_refusals.append({"reason": error.reason, "estimatedInputTokens": estimated_input_tokens,
                    "preserveTotalTokens": kwargs.get("preserve_total_tokens", 0),
                    "snapshot": self.snapshot(), "workerRole": workers[-1]["role"] if workers else None})
                raise

    budget = ObservedBudget(max_total_tokens=max_tokens)
    original_worker = task_module._run
    original_audit = workflow_finalization.project_scheduler_audit

    def observe_audit(*args, **kwargs):
        try:
            return original_audit(*args, **kwargs)
        except Exception as exc:
            audit_errors.append(f"{type(exc).__name__}: {exc}")
            raise

    def worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        assert time.monotonic() < context._task_deadline_monotonic <= time.monotonic() + 900
        assert context._logical_agent_max_model_calls is None
        role = context._logical_agent_role
        session = data.get("_executor_session")
        entry = {"role": role, "session": session,
                 "beforeContext": session.context_manager if session else None,
                 "budgetRefusals": budget_refusals}
        if role == "executor":
            number = 1 + sum(item["role"] == role for item in workers)
            if number > 1:
                assert (Path(context.cwd) / "candidate.py").read_text() == _source(number - 1)
                assert (Path(context.cwd) / "test_regression.py").read_text() == _test(number - 1)
        workers.append(entry)
        result = original_worker(data, context)
        entry["result"] = result
        if session:
            entry.update(afterContext=session.context_manager, model=session.model)
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
        return AgentStep(type="tool_calls", calls=actions, usage=ModelUsage(
            input_tokens=input_tokens, output_tokens=output_tokens, source="estimated",
        ))

    class Executor:
        tools = None
        count = 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            tools = tools_override or self.tools
            calls.append(("executor", deepcopy(messages)))
            revision = (self.count + 1) // 2
            if failed_check and self.count == 3:
                rejection = next(message for message in messages
                                 if message.get("toolUseId") == "submit-1" and message.get("role") == "tool_result")
                assert rejection["isError"]
                assert "completed requires every approved check to pass" in rejection["content"]
                count = next(tool for tool in tools.list() if tool.name == "executor_submit").input_schema[
                    "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                actions = [call("executor_submit", {"status": "blocked",
                           "steps": [{"step": step, "status": "failed"} for step in range(1, count + 1)],
                           "deviations": [], "blockers": ["The actual regression still fails."]}, "blocked")]
            elif self.count % 2:
                assert revision <= 3
                if revision > 1:
                    assert f"review_revision_{revision - 1}" in str(messages)
                    assert any(message.get("toolUseId") == "edit-1" for message in messages)
                    if revision == 3:
                        assert any(message.get("toolUseId") == "edit-2" for message in messages)
                assert {"read_file", "edit_file", "executor_test"} <= {tool.name for tool in tools.list()}
                actions = [call("edit_file", {"path": "candidate.py", "old": _source(revision - 1),
                           "new": _source(revision)}, f"edit-{revision}"),
                           call("executor_test", {"path": "test_regression.py", "code": _test(3 if failed_check else revision)},
                                f"test-{revision}")]
            else:
                count = next(tool for tool in tools.list() if tool.name == "executor_submit").input_schema[
                    "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                actions = [call("executor_submit", {"status": "completed",
                           "steps": [{"step": step, "status": "completed"} for step in range(1, count + 1)],
                           "deviations": [], "blockers": []}, f"submit-{revision}")]
            return response(messages, tools, actions)

    class Reviewer:
        tools = None

        def __init__(self, revision):
            self.revision = revision
            self.count = 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            tools = tools_override or self.tools
            calls.append(("reviewer", deepcopy(messages)))
            assert target.read_text() == INITIAL
            if self.count == 1:
                actions = [call("read_file", {"path": "candidate.py"}, f"review-source-{self.revision}"),
                           call("read_file", {"path": "test_regression.py"}, f"review-test-{self.revision}")]
                if self.revision == 3:
                    actions.append(call("review_test", {
                        "criterionIds": ["returns_three"],
                        "scenario": "Call value() independently and require the public result 3.",
                        "code": _test(3),
                    }, "independent-review-probe"))
            else:
                assert self.count == 2
                assert any(message.get("toolUseId") == f"review-source-{self.revision}"
                           and f"return {self.revision}" in message.get("content", "")
                           for message in messages if message.get("role") == "tool_result")
                passed = self.revision == 3
                criterion = {"criterionId": "returns_three", "status": "satisfied" if passed else "failed"}
                if passed:
                    receipt = json.loads(next(message["content"] for message in messages
                                              if message.get("role") == "tool_result"
                                              and message.get("toolUseId") == "independent-review-probe"))
                    assert receipt["status"] == "passed" and receipt["evidence"]["exitCode"] == 0
                    criterion.update(rationale="The independent public-result probe passed on this candidate.",
                                     probeIds=[receipt["probeId"]], sourceEvidence=[])
                actions = [call("review_submit", {
                    "verdict": "approved" if passed else "changes_required",
                    "criteria": [criterion],
                    "blockingFindings": [] if passed else [{"criterionIds": ["returns_three"],
                        "statement": f"review_revision_{self.revision}: candidate.py returns {self.revision}, but the required result is 3; update the code and regression."}],
                    "warnings": [], "requestedChecks": [], "confidence": "high",
                }, f"review-submit-{self.revision}")]
            return response(messages, tools, actions)

    executor = Executor()

    def factory(_runtime, agent_type, **kwargs):
        model = executor if agent_type == "general" else Reviewer(
            1 + sum(kind != "general" for kind, _ in adapters))
        model.tools = kwargs["tools"]
        adapters.append((agent_type, model))
        return model

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class ObservedVerifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == INITIAL
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            result = super().run(**kwargs)
            commands.extend({"formal": formal, "exit": item["exitCode"], "cwd": kwargs["cwd"]}
                            for item in result["checks"])
            return result

    verifier = ObservedVerifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(workflow_finalization, "project_scheduler_audit", observe_audit)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    # Never read private host settings in this local mechanism test.
    monkeypatch.setattr("minicode.subagent_types._read_max_turns_override", lambda _name: None)
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_continuous_repair", "kind": "change", "risk": "low", "complex": False,
                    "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_regression.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "returns_three", "kind": "behavior", "text": "value() must return the integer 3."}],
        "verificationChecks": [
            {"checkId": "compat", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_compat.py"], "criterionIds": ["returns_three"]},
            {"checkId": "regression", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_regression.py"], "criterionIds": ["returns_three"]},
        ]}
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="continuous Executor mechanism",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "return the required integer", "agent_type": "workflow",
            "prompt": "Make value() return 3 and add a regression test. Please review the change.", "workflow_request": request},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"}, _agent_budget=budget,
                        _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    snapshot = budget.snapshot()
    assert snapshot.limit_model_calls is None and snapshot.limit_cost_usd is None
    assert snapshot.limit_total_tokens == max_tokens
    assert snapshot.used_model_calls == len(calls)
    assert snapshot.used_total_tokens == sum(billed)
    # Local usage is explicitly estimated, never claimed as provider-reported.
    assert snapshot.usage_uncertain
    assert snapshot.reserved_total_tokens == 0 and not budget._reservations
    assert all(not Path(item["cwd"]).exists() for item in commands)
    reloaded = RunJournal(tmp_path, data_dir=data_dir)
    audit = reloaded.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None, (audit_errors, result.output)
    sessions = [event.payload for child in reloaded.list_subagent_runs(run_id)
                for event in reloaded.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    return result, trace, snapshot, workers, adapters, commands, sessions, audit, reloaded, run_id


def test_unbounded_workflow_survives_two_external_reviews_in_the_same_executor(tmp_path, monkeypatch):
    result, trace, snapshot, workers, adapters, commands, sessions, audit, journal, run_id = _exercise(tmp_path, monkeypatch)
    assert result.ok and trace["committed"], result.output
    assert (tmp_path / "candidate.py").read_text() == _source(3)
    assert (tmp_path / "test_regression.py").read_text() == _test(3)
    executors = [item for item in workers if item["role"] == "executor"]
    assert len(executors) == 3
    assert len({id(item["session"]) for item in executors}) == 1
    assert len({id(item["model"]) for item in executors}) == 1
    assert len({id(item["afterContext"]) for item in executors}) == 1
    assert all(item["beforeContext"] is executors[0]["afterContext"] for item in executors[1:])
    assert sum(kind == "general" for kind, _ in adapters) == 1
    assert len(sessions) == 3 and len({item["sessionId"] for item in sessions}) == 1
    assert sum(item["resumed"] and item["modelReused"] and item["priorMessageCount"] > 0 for item in sessions) == 2
    assert [item["exit"] for item in commands if item["formal"]] == [0] * 6
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 2
    assert snapshot.used_model_calls == 12
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    repairs = [json.loads(artifact.content) for artifact in audit.artifacts
               if json.loads(artifact.content).get("resultType") == "ImplementationRepairRequest.v1"]
    assert len(repairs) == 2
    assert all(item["continuation"][key] is None for item in repairs
               for key in ("limitModelCalls", "limitTotalTokens"))
    deadlines = [item["continuation"]["deadlineMonotonic"] for item in repairs]
    assert all(isinstance(value, (int, float)) for value in deadlines)
    assert deadlines[1] <= deadlines[0]
    review_refs = {item["artifactId"] for item in audit.trace["transitions"]
                   if item["stage"] == "reviewer" and item["outcome"] in {"completed", "rejected"}}
    reviews = [json.loads(item.content)["verdict"] for item in audit.artifacts
               if item.artifact_id in review_refs]
    assert reviews.count("changes_required") == 2 and reviews.count("approved") == 1


def test_explicit_finite_global_budget_still_prevents_unverified_writeback(tmp_path, monkeypatch):
    result, trace, snapshot, workers, _, commands, _, audit, journal, run_id = _exercise(tmp_path, monkeypatch, max_tokens=60000)
    assert not result.ok and not trace["committed"]
    assert (tmp_path / "candidate.py").read_text() == INITIAL
    assert snapshot.used_total_tokens <= 60000
    assert commands, "The budget case must reach actual candidate work before stopping."
    # Independent review evidence adds actual input cost. With the unchanged
    # 60k ledger the last refusal now occurs in Reviewer admission, before any
    # unverified bytes can be promoted.
    assert audit.trace["terminalReason"] == "review_phase_failed"
    refusals = [item for item in workers[-1]["budgetRefusals"] if item["workerRole"] == "reviewer"]
    assert refusals, "Reviewer must encounter an actual shared-budget reserve refusal."
    assert any("token" in item["reason"].lower()
               and item["snapshot"].used_total_tokens + item["snapshot"].reserved_total_tokens
                   + item["estimatedInputTokens"] + item["preserveTotalTokens"] > 60000
               for item in refusals)
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is None
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    candidates = [json.loads(path.read_text()) for path in data_dir.rglob("failure-candidate.json")]
    assert len(candidates) == 1 and candidates[0]["promotable"] is False
    assert {item["path"] for item in candidates[0]["files"]} == {"candidate.py", "test_regression.py"}
    assert next(item["content"] for item in candidates[0]["files"] if item["path"] == "candidate.py") != INITIAL

    # Even a mutually consistent receipt and trace cannot invent a failure cause.
    forged_trace = deepcopy(audit.trace)
    forged_trace["terminalReason"] = "executor_invented_cause"
    terminal = forged_trace["transitions"][-1]
    terminal["errorCode"] = "executor_invented_cause"
    forged_artifacts = []
    for artifact in audit.artifacts:
        if artifact.artifact_id == terminal["artifactId"]:
            value = json.loads(artifact.content)
            assert value["resultType"] == "SchedulerStageReceipt.v1"
            value["errorCode"] = "executor_invented_cause"
            artifact = replace(artifact, content=json.dumps(value, sort_keys=True, separators=(",", ":")))
        forged_artifacts.append(artifact)
    with pytest.raises(SchedulerAuditValidationError, match="terminal cause is outside the closed policy"):
        project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=forged_trace,
            artifacts=tuple(forged_artifacts), contract_mode="v2")
    with pytest.raises(SchedulerAuditValidationError):
        project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=audit.trace,
            artifacts=tuple(item for item in audit.artifacts if item.artifact_id != terminal["artifactId"]),
            contract_mode="v2")


def test_unbounded_execution_does_not_promote_a_real_failed_check(tmp_path, monkeypatch):
    result, trace, snapshot, workers, _, commands, _, _, _, _ = _exercise(tmp_path, monkeypatch, failed_check=True)
    assert not result.ok and not trace["committed"]
    assert (tmp_path / "candidate.py").read_text() == INITIAL
    assert snapshot.used_model_calls == 3
    assert any(item["exit"] == 1 for item in commands)
    assert [item["role"] for item in workers] == ["executor"]
