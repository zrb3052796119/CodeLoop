"""A failed final edit remains pending feedback, not a discarded candidate.

The deterministic adapter chooses tools; edits, pytest, budget rejection,
candidate freezing, same-session recovery, final gates and audit are production.
Local input/schema estimates are billed without a synthetic historical prefix.
This reproduces the live-v1 state transition, not its provider token transcript.
"""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_failed_candidate_repair import INITIAL, PARTIAL, COMPLETE, TEST, REPAIRED_TEST


GREEN_PENDING = "def normalize(value):\n    return ('' if value is None else value).strip()\n"


@pytest.mark.parametrize("initial_checks_pass", [False, True], ids=["failed-checks", "green-checks-still-pending"])
def test_failed_final_write_returns_to_same_executor_before_delivery(tmp_path, monkeypatch, initial_checks_pass):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_candidate.py").write_text(TEST)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    deadline = time.monotonic() + 1800
    calls, billed, workers, adapters, commands = [], [], [], [], []
    review_receipts = []
    first_source = GREEN_PENDING if initial_checks_pass else PARTIAL
    original_worker = task_module._run

    def formal_rounds():
        # Per-check callbacks finish concurrently. Keep each candidate's
        # requested check identity instead of assuming completion order.
        rounds = {}
        expected_argv = {
            check["checkId"]: tuple(check["argv"])
            for check in request["verificationChecks"]
        }
        for item in commands:
            if not item["formal"]:
                continue
            assert item["argv"] == expected_argv[item["checkId"]]
            identity = (item["assignmentId"], item["patchDigest"])
            checks = rounds.setdefault(identity, {})
            assert item["checkId"] not in checks, "duplicate formal result"
            checks[item["checkId"]] = item["exit"]
        assert all(set(checks) == set(expected_argv) for checks in rounds.values())
        return list(rounds.values())

    def run_worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        if context._logical_agent_role == "executor":
            number = 1 + sum(item["role"] == "executor" for item in workers)
            if number == 1:
                # Admit one real request on the shared parent ledger, then
                # exhaust only this first worker's local call allowance after
                # actual edits and pytest feedback. Do not hardcode schema size.
                context = replace(context, _agent_budget=_WorkerCallBudget(
                    context._agent_budget, "first_test_slice", max_calls=1,
                    preserve_calls=0))
            else:
                assert number == 2, "Recovery must not start a replacement workflow"
                assert (Path(context.cwd) / "candidate.py").read_text() == first_source
                assert (Path(context.cwd) / "test_none.py").read_text() == REPAIRED_TEST
                assert target.read_text() == INITIAL
        workers.append({"role": context._logical_agent_role, "session": data.get("_executor_session")})
        result = original_worker(data, context)
        workers[-1]["result"] = {"ok": result.ok, "reason": str(result._termination_reason)}
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
                actions = [call("edit_file", {"path": "candidate.py", "old": INITIAL, "new": first_source}, "first-source-edit"),
                           call("executor_test", {"path": "test_none.py", "code": REPAIRED_TEST}, "write-regression"),
                           call("edit_file", {"path": "candidate.py", "old": "return unavailable_previous_value\n",
                                              "new": "return ''\n"}, "failed-final-edit")]
            elif number == 2:
                assert sum(item["role"] == "executor" for item in workers) == 2
                assert any(message.get("toolUseId") == "failed-final-edit" and message.get("isError")
                           and "Search string not found" in message.get("content", "") for message in messages), (
                               "The original Executor must receive its exact last edit failure")
                assert any(message.get("toolUseId") == "write-regression" for message in messages)
                assert formal_rounds() == [
                    {"compat": 0, "regression": 0 if initial_checks_pass else 1},
                ]
                if initial_checks_pass:
                    # A green check cannot acknowledge an unseen edit error.
                    # The same owner resolves the failed edit using the
                    # current exact source and a real revised patch. Existing
                    # repair_no_change protection stays in force.
                    assert target.read_text() == INITIAL
                else:
                    assert "AttributeError" in str(messages)
                actions = [call("edit_file", {"path": "candidate.py", "old": first_source,
                                              "new": COMPLETE}, "fix-product")]
            elif number == 3:
                count = next(tool for tool in tools.list() if tool.name == "executor_submit").input_schema[
                    "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                actions = [call("executor_submit", {"status": "completed",
                    "steps": [{"step": step, "status": "completed"} for step in range(1, count + 1)],
                    "deviations": [], "blockers": []}, "deliver")]
            else:
                raise AssertionError("Unexpected replacement or extra model request")
            return response(messages, tools, actions)

    class Reviewer:
        tools = None
        requests = 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.requests += 1
            calls.append(("reviewer", deepcopy(messages)))
            tools = tools_override or self.tools
            assert target.read_text() == INITIAL
            assert sum(item["role"] == "executor" for item in workers) == 2
            if self.requests == 1:
                actions = [call("read_file", {"path": "candidate.py"}, "review-source"),
                           call("read_file", {"path": "test_none.py"}, "review-tests")]
            elif self.requests == 2:
                assert self.requests == 2
                assert any(message.get("toolUseId") == "review-source" and "if value is None:" in message.get("content", "")
                           for message in messages)
                assert any(message.get("toolUseId") == "review-tests" and "test_none" in message.get("content", "")
                           for message in messages)
                actions = [call("review_test", {
                    "criterionIds": ["trim", "none"],
                    "scenario": "Independently check None and mixed surrounding whitespace on the final candidate.",
                    "code": "from candidate import normalize\n"
                            "def test_independent_none():\n    assert normalize(None) == ''\n"
                            "def test_independent_whitespace():\n    assert normalize('\\t Alice \\n') == 'Alice'\n",
                }, "independent-final-check")]
            else:
                assert self.requests == 3
                feedback = next(message for message in messages
                                if message.get("role") == "tool_result"
                                and message.get("toolUseId") == "independent-final-check")
                receipt, _ = json.JSONDecoder().raw_decode(feedback["content"].lstrip())
                assert not feedback["isError"] and receipt["status"] == "passed"
                assert receipt["evidence"]["exitCode"] == 0 and receipt["testReport"]["passed"] == 2
                review_receipts.append(receipt)
                actions = [call("review_submit", {"verdict": "approved",
                    "criteria": [{"criterionId": cid, "status": "satisfied",
                        "rationale": "The independent None and whitespace assertions passed on this candidate.",
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
            assert target.read_text() == INITIAL, "Pending feedback and unreviewed revisions cannot reach the parent"
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            result = super().run(**kwargs)
            requested = {check.check_id: check for check in kwargs["checks"]}
            assert len(result["checks"]) == len(requested)
            assert {item["checkId"] for item in result["checks"]} == set(requested)
            commands.extend({"formal": formal, "exit": item["exitCode"], "cwd": kwargs["cwd"],
                             "checkId": item["checkId"], "argv": requested[item["checkId"]].argv,
                             "assignmentId": kwargs["execution_assignment_id"], "patchDigest": kwargs["patch_digest"]}
                            for item in result["checks"])
            return result

    verifier = ObservedVerifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(task_module, "_run", run_worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_failed_write_candidate", "kind": "change", "risk": "low", "complex": False,
                    "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_none.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace."},
                     {"id": "none", "kind": "behavior", "text": "Return an empty string for None."}],
        "verificationChecks": [
            {"checkId": "compat", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_candidate.py"], "criterionIds": ["trim"]},
            {"checkId": "regression", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_none.py"], "criterionIds": ["none"]},
        ]}
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="failed final edit continuation",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "finish both public behaviors", "agent_type": "workflow",
            "prompt": "Strip whitespace and return an empty string for None, including a public regression test. Please review the changes.",
            "workflow_request": request}, ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture", "modelTimeoutSeconds": 300},
                _agent_budget=budget, _task_deadline_monotonic=deadline, _event_sink=observation,
                _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    snapshot = budget.snapshot()
    print(json.dumps({"usageSource": "estimated", "initialChecksPass": initial_checks_pass,
        "usedTokens": snapshot.used_total_tokens, "usedCalls": snapshot.used_model_calls,
        "globalTokens": snapshot.limit_total_tokens, "globalCalls": snapshot.limit_model_calls,
        "checks": [item["exit"] for item in commands],
        "formalExits": [item["exit"] for item in commands if item["formal"]],
        "workers": [item.get("result") for item in workers],
        "terminalReason": trace["terminalReason"], "committed": trace["committed"]}))
    assert snapshot.used_total_tokens == sum(billed) and snapshot.used_model_calls == len(calls)
    assert snapshot.limit_total_tokens == 500000 and snapshot.limit_model_calls == 40
    assert snapshot.reserved_total_tokens == 0 and snapshot.used_total_tokens < 150000
    assert time.monotonic() < deadline - 120
    assert workers[0]["result"]["reason"] == "ToolTerminationReason.BUDGET_EXCEEDED"
    assert [item["exit"] for item in commands if not item["formal"]][:2] == [0, 0 if initial_checks_pass else 1]
    assert result.ok and trace["committed"], (
        "Current checked candidate was discarded because the final write failed instead of resuming its owner",
        trace["terminalReason"], [item["exit"] for item in commands if item["formal"]])
    assert target.read_text() == COMPLETE and (tmp_path / "test_none.py").read_text() == REPAIRED_TEST
    assert [kind for kind, _ in adapters].count("general") == 1
    assert len(review_receipts) == 1 and sum(role == "reviewer" for role, _ in calls) == 3
    executor_workers = [item for item in workers if item["role"] == "executor"]
    assert len(executor_workers) == 2 and executor_workers[0]["session"] is not None
    assert executor_workers[0]["session"] is executor_workers[1]["session"]
    assert formal_rounds() == [
        {"compat": 0, "regression": 0 if initial_checks_pass else 1},
        {"compat": 0, "regression": 0},
    ]
    assert all(not Path(item["cwd"]).exists() for item in commands)
    reloaded = RunJournal(tmp_path, data_dir=data_dir)
    audit = reloaded.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None and audit.trace["committed"]
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    candidates = [item for item in documents if item.get("resultType") == "ExecutionCandidate.v1"]
    assert len(candidates) == 1 and candidates[0]["handoffReason"] == "executor_write_feedback_pending"
    assert candidates[0]["status"] == "awaiting_verification" and "stepResults" not in candidates[0]
    first_verification = next(item for item in documents if item.get("resultType") == "VerificationResult.v1"
                              and item["executionAssignmentId"] == candidates[0]["assignmentId"])
    if initial_checks_pass:
        # Test evidence stays truthful: all checks passed. The scheduler still
        # has an unresolved delivery decision, so it must continue the owner.
        assert first_verification["status"] == "passed"
        assert all(item["status"] == "passed" for item in first_verification["checks"])
        verification_artifact = next(artifact.artifact_id for artifact in audit.artifacts
                                     if json.loads(artifact.content) == first_verification)
        pending = next(item for item in trace["transitions"]
                       if item["stage"] == "verifier" and item["artifactId"] == verification_artifact
                       and item["outcome"] == "inconclusive")
        assert pending["errorCode"] == "acceptance_evidence_incomplete"
        assert all(item["sequence"] > pending["sequence"] for item in trace["transitions"]
                   if item["stage"] == "reviewer")
    assert any(item.get("resultType") == "ExecutionResult.v1" and item["status"] == "completed" for item in documents)
    assert reloaded.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    sessions = [event.payload for child in reloaded.list_subagent_runs(run_id)
                for event in reloaded.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    assert len(sessions) == 2 and len({item["sessionId"] for item in sessions}) == 1
    assert any(item["resumed"] and item["modelReused"] and item["priorMessageCount"] > 0 for item in sessions)
    assert any(item["stage"] == "reviewer" and item["outcome"] == "completed" for item in trace["transitions"])
