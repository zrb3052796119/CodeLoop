"""Actionable review requests return to the live Executor without new authority.

Model decisions are scripted; edits, pytest, native submissions, shared budget,
session restoration, durable audit and promotion execute production code.
"""

import json
import sys
import time
from copy import deepcopy
from pathlib import Path

from test_checked_candidate_workflow import (
    COMPLETE,
    INITIAL,
    PARTIAL,
    REPAIRED_TEST,
    TEST,
)

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

FINDING = "The public None-input behavior is missing; the existing tests omit it."
REQUESTED = "Add a public None-input regression; do not run this suggestion as a command."


def test_actionable_review_requested_checks_resumes_original_executor(tmp_path, monkeypatch):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_candidate.py").write_text(TEST)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    deadline = time.monotonic() + 1800
    calls, billed, workers, adapters, checks = [], [], [], [], []
    approved_argv = ("python", "-m", "pytest", "-q", "test_candidate.py")
    original_worker = task_module._run

    def worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget and context._task_deadline_monotonic <= deadline
        role = context._logical_agent_role
        workers.append((role, data.get("_executor_session")))
        if role == "executor":
            assert tuple(context._logical_agent_scope.write_paths) == ("candidate.py", "test_candidate.py")
            assert [tuple(check.argv) for check in data["_executor_check_capability"]._checks] == [approved_argv]
            if sum(r == "executor" for r, _ in workers) == 2:
                assert target.read_text() == INITIAL
                assert (Path(context.cwd) / "candidate.py").read_text() == PARTIAL
                assert (Path(context.cwd) / "test_candidate.py").read_text() == TEST
        return original_worker(data, context)

    def action(name, value, identity):
        return {"id": identity, "toolName": name, "input": value}

    def metered(role, messages, tools, actions):
        calls.append((role, deepcopy(messages)))
        schemas = [{"type": "function", "function": {"name": tool.name,
                    "description": tool.description, "parameters": tool.input_schema}}
                   for tool in tools.list()]
        tokens = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
        output = estimate_tokens(json.dumps(actions))
        billed.append(tokens + output)
        return AgentStep(type="tool_calls", calls=actions,
                         usage=ModelUsage(input_tokens=tokens, output_tokens=output, source="estimated"))

    def declaration(tools):
        submit = next(tool for tool in tools.list() if tool.name == "executor_submit")
        count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
        return {"status": "completed", "steps": [{"step": i, "status": "completed"}
                for i in range(1, count + 1)], "deviations": [], "blockers": []}

    class Executor:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            tools = tools_override or self.tools
            number = 1 + sum(role == "executor" for role, _ in calls)
            if number == 1:
                actions = [action("edit_file", {"path": "candidate.py", "old": INITIAL,
                                               "new": PARTIAL}, "initial-edit")]
            elif number == 2:
                actions = [action("executor_submit", declaration(tools), "initial-submit")]
            elif number == 3:
                assert sum(role == "executor" for role, _ in workers) == 2
                assert FINDING in str(messages) and REQUESTED in str(messages)
                assert any(m.get("role") == "assistant_tool_call" and m.get("toolUseId") == "initial-edit"
                           for m in messages), "Canonical edit history must remain with the original Executor."
                assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "initial-submit"
                           and m.get("isError") is False for m in messages)
                assert [check["exit"] for check in checks if check["formal"]] == [0]
                actions = [action("executor_test", {"path": "test_candidate.py", "code": REPAIRED_TEST}, "add-regression")]
            elif number == 4:
                assert any(m.get("toolUseId") == "add-regression" and m.get("_implementation_checks_failed") is True
                           for m in messages), "The real failing regression must reach the same Executor."
                actions = [action("edit_file", {"path": "candidate.py", "old": PARTIAL,
                                               "new": COMPLETE}, "repair-source")]
            elif number == 5:
                actions = [action("executor_submit", declaration(tools), "repaired-submit")]
            else:
                raise AssertionError("Unexpected replacement or extra Executor request")
            return metered("executor", messages, tools, actions)

    class Reviewer:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            tools = tools_override or self.tools
            number = 1 + sum(role == "reviewer" for role, _ in calls)
            assert target.read_text() == INITIAL
            assert [check["exit"] for check in checks if check["formal"]] == [0] * min(number, 2)
            assert number in (1, 2, 3)
            if number == 2:
                return metered("reviewer", messages, tools, [action("review_test", {
                    "criterionIds": ["trim", "none"],
                    "scenario": "Check whitespace and None independently on the repaired candidate.",
                    "code": REPAIRED_TEST,
                }, "independent-repaired-behavior")])
            negative = number == 1
            coverage = {}
            if not negative:
                feedback = next(message for message in messages
                                if message.get("role") == "tool_result"
                                and message.get("toolUseId") == "independent-repaired-behavior")
                receipt, _ = json.JSONDecoder().raw_decode(feedback["content"].lstrip())
                assert not feedback["isError"] and receipt["status"] == "passed"
                assert receipt["testReport"]["passed"] >= 2
                coverage = {"rationale": "The independent whitespace and None assertions passed.",
                            "probeIds": [receipt["probeId"]], "sourceEvidence": []}
            return metered("reviewer", messages, tools, [action("review_submit", {
                "verdict": "changes_required" if negative else "approved",
                "criteria": [{"criterionId": "trim", "status": "satisfied", **coverage},
                             {"criterionId": "none", "status": "failed" if negative else "satisfied", **coverage}],
                "blockingFindings": [{"criterionIds": ["none"], "statement": FINDING}] if negative else [],
                "warnings": [], "requestedChecks": [REQUESTED] if negative else [], "confidence": "high",
            }, f"review-{number}")])

    executor = Executor()

    def factory(_runtime, agent_type, **kwargs):
        model = executor if agent_type == "general" else Reviewer()
        model.tools = kwargs["tools"]
        adapters.append((agent_type, model))
        return model

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == INITIAL
            argv = [tuple(check.argv) for check in kwargs["checks"]]
            assert argv in ([approved_argv], [("python", "-m", "pytest", "-q", "__minicode_review_probe__.py")])
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            result = super().run(**kwargs)
            checks.extend({"formal": formal, "exit": check["exitCode"]} for check in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_review_check_feedback", "kind": "change", "risk": "low", "complex": False,
                    "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_candidate.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace."},
                     {"id": "none", "kind": "behavior", "text": "Return an empty string for None."}],
        "verificationChecks": [{"checkId": "public", "kind": "tests", "argv": list(approved_argv),
                                "criterionIds": ["trim", "none"]}]}
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="actionable requested checks",
                     journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "complete public behavior", "agent_type": "workflow",
            "prompt": "Strip whitespace and handle None with a public regression test. Please review the changes.", "workflow_request": request},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"}, _agent_budget=budget,
                _task_deadline_monotonic=deadline, _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert result.ok and trace["committed"], (trace["terminalReason"], checks)
    assert target.read_text() == COMPLETE and (tmp_path / "test_candidate.py").read_text() == REPAIRED_TEST
    assert [role for role, _ in workers] == ["executor", "reviewer", "executor", "reviewer"]
    assert workers[0][1] is workers[2][1] and workers[0][1] is not None
    assert [role for role, _ in adapters].count("general") == 1
    assert [check["exit"] for check in checks] == [0, 0, 1, 0, 0, 0]
    snapshot = budget.snapshot()
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls) == (500000, 40)
    assert snapshot.used_model_calls == len(calls) == 8 and snapshot.used_total_tokens == sum(billed)
    assert snapshot.reserved_total_tokens == 0 and snapshot.used_total_tokens < 500000
    journal = RunJournal(tmp_path, data_dir=data_dir)
    audit = journal.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None and audit.trace["committed"]
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    assert any(d.get("verdict") == "changes_required" and d["requestedChecks"] == [REQUESTED] for d in documents)
    assert any(d.get("resultType") == "ReviewCoverageEvidence.v1" and d["probes"] for d in documents)
    assert any(d.get("resultType") == "ReviewProbeExecution.v1" and d["status"] == "passed"
               and d["testReport"]["passed"] >= 2 for d in documents)
    sessions = [event.payload for child in journal.list_subagent_runs(run_id)
                for event in journal.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    sessions.sort(key=lambda item: item["resumed"])
    assert len(sessions) == 2 and sessions[0]["sessionId"] == sessions[1]["sessionId"]
    assert sessions[0]["resumed"] is False and sessions[0]["modelReused"] is False
    assert sessions[1]["resumed"] and sessions[1]["modelReused"] and sessions[1]["priorMessageCount"] > 0
