"""A typed parent handoff frees its own summary floor for the live workers.

Model decisions are scripted; edits, pytest, native submissions, shared budget,
session restoration, durable audit and promotion execute production code.
"""

import json
import re
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
from minicode.agent_loop import run_agent_turn
from minicode.agent_runtime import final_response_text
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolRegistry, ToolUsageCounts
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage

FINDING = "The public None-input behavior is missing; the existing tests omit it."
REQUESTED = "Add a public None-input regression; do not run this suggestion as a command."


def _exercise(tmp_path, monkeypatch, *, reject_final_review=False):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_candidate.py").write_text(TEST)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    deadline = time.monotonic() + 1800
    calls, billed, workers, adapters, checks = [], [], [], [], []
    worker_tools = []
    worker_failures = []
    receipts, floors, correction_admissions = [], [], []
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
            assert all(tuple(check.argv) == approved_argv
                       for check in data["_executor_check_capability"]._checks)
            if sum(r == "executor" for r, _ in workers) == 2:
                assert target.read_text() == INITIAL
                assert (Path(context.cwd) / "candidate.py").read_text() == PARTIAL
                assert (Path(context.cwd) / "test_candidate.py").read_text() == TEST
        result = original_worker(data, context)
        if not result.ok:
            worker_failures.append(result.output)
        return result

    def action(name, value, identity):
        return {"id": identity, "toolName": name, "input": value}

    def metered(role, messages, tools, actions):
        calls.append((role, deepcopy(messages)))
        if role != "parent":
            worker_tools.append(len(actions or []))
        schemas = [{"type": "function", "function": {"name": tool.name,
                    "description": tool.description, "parameters": tool.input_schema}}
                   for tool in tools.list()]
        tokens = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
        output = estimate_tokens(json.dumps(actions))
        if actions and actions[0]["id"] == "corrected-review":
            correction_admissions.append({"snapshot": budget.snapshot(), "input": tokens, "output": output})
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
                                               "new": PARTIAL}, "initial-edit"),
                           action("executor_submit", declaration(tools), "initial-submit")]
            elif number == 2:
                assert sum(role == "executor" for role, _ in workers) == 2
                assert FINDING in str(messages) and REQUESTED in str(messages)
                assert any(m.get("role") == "assistant_tool_call" and m.get("toolUseId") == "initial-edit"
                           for m in messages), "Canonical edit history must remain with the original Executor."
                assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "initial-submit"
                           and m.get("isError") is False for m in messages)
                assert [check["exit"] for check in checks if check["formal"]] == [0]
                actions = [action("executor_test", {"path": "test_candidate.py", "code": REPAIRED_TEST}, "add-regression")]
            elif number == 3:
                assert any(m.get("toolUseId") == "add-regression" and m.get("_implementation_checks_failed") is True
                           for m in messages), "The real failing regression must reach the same Executor."
                actions = [action("edit_file", {"path": "candidate.py", "old": PARTIAL,
                                               "new": COMPLETE}, "repair-source"),
                           action("executor_submit", declaration(tools), "repaired-submit")]
            else:
                raise AssertionError("Unexpected replacement or extra Executor request")
            return metered("executor", messages, tools, actions)

    class Reviewer:
        tools = None

        def __init__(self):
            self.calls = 0
            self.index = 1 + sum(role != "general" for role, _ in adapters)
            self.rejected = False

        def next(self, messages, tools_override=None, **_kwargs):
            self.calls += 1
            tools = tools_override or self.tools
            assert target.read_text() == INITIAL
            completed_repairs = sum(role == "executor" for role, _ in workers) == 2
            assert [check["exit"] for check in checks if check["formal"]] == [0] * (2 if completed_repairs else 1)
            negative = not completed_repairs
            value = {
                "verdict": "changes_required" if negative else "approved",
                "criteria": [{"criterionId": "trim", "status": "satisfied"},
                             {"criterionId": "none", "status": "failed" if negative else "satisfied"}],
                "blockingFindings": [{"criterionIds": ["none"], "statement": FINDING}] if negative else [],
                "warnings": [], "requestedChecks": [REQUESTED] if negative else [], "confidence": "high",
            }
            if not negative:
                probe_ids = sorted(set(re.findall(r"review_probe_[0-9a-f]{24}", str(messages))))
                assert probe_ids, "The repaired candidate's retained probe must reach its Reviewer."
                for criterion in value["criteria"]:
                    criterion.update(
                        rationale="The retained public probe checks whitespace and None on this repaired candidate.",
                        probeIds=probe_ids,
                    )
            if negative and self.calls == 1:
                actions = [action("review_test", {
                    "criterionIds": ["trim", "none"],
                    "scenario": "Both public normalization cases, including missing None support.",
                    "code": ("from candidate import normalize\n"
                             "def test_public_cases():\n"
                             "    assert normalize(' a ') == 'a'\n"
                             "    assert normalize(None) == ''\n"),
                }, "independent-public-probe")]
            elif negative and self.calls <= 6 and "read_file" in tools.list_all():
                actions = [action("read_file", {"path": "candidate.py"}, f"review-source-{self.calls}")]
            elif negative and not self.rejected:
                value.pop("verdict")
                self.rejected = True
                actions = [action("review_submit", value, "invalid-review")]
            else:
                if negative:
                    assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "invalid-review"
                               and m.get("isError") and "verdict" in m.get("content", "") for m in messages)
                if reject_final_review and not negative:
                    # Repeated malformed declarations remain rejected. Spend
                    # only real admitted requests; never invent a conclusion.
                    value.pop("verdict")
                actions = [action("review_submit", value, "corrected-review" if negative else "approved-review")]
            return metered("reviewer", messages, tools, actions)

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
            actual_argv = [tuple(check.argv) for check in kwargs["checks"]]
            assert actual_argv in ([approved_argv], [(*approved_argv[:-1], "__minicode_review_probe__.py")])
            formal = getattr(kwargs.get("evidence_sink"), "__name__", "") == "per_check_sink"
            result = super().run(**kwargs)
            checks.extend({"formal": formal, "probe": actual_argv != [approved_argv],
                           "exit": check["exitCode"]} for check in result["checks"])
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
    original_task = task_module.task_tool.run

    def workflow(data, context):
        floors.append(context._logical_agent_preserve_total_tokens)
        result = original_task(data, context)
        receipts.append(result)
        return result

    from dataclasses import replace
    parent_tools = ToolRegistry([replace(task_module.task_tool, run=workflow)])

    class Parent:
        count = 0

        def next(self, messages, **kwargs):
            self.count += 1
            if self.count <= 7:
                actions = None
                text = "Preparing the public workflow."
            elif self.count == 8:
                actions = [action("task", {"description": "complete public behavior", "agent_type": "workflow",
                    "prompt": "Strip whitespace and handle None with a public regression test; request independent review.",
                    "workflow_request": request}, "workflow")]
                return metered("parent", messages, parent_tools, actions)
            else:
                actions = None
                text = "The workflow did not complete."
            calls.append(("parent", deepcopy(messages)))
            schemas = [] if kwargs.get("disable_tools") else [{"type": "function", "function": {
                "name": tool.name, "description": tool.description, "parameters": tool.input_schema}}
                for tool in parent_tools.list()]
            input_tokens = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            output_tokens = estimate_tokens(text)
            billed.append(input_tokens + output_tokens)
            return AgentStep(type="assistant", kind="progress" if self.count <= 7 else None,
                content=text, usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens, source="estimated"))

    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="typed parent floor",
                     journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir)) as observation:
        messages = run_agent_turn(model=Parent(), tools=parent_tools,
            messages=[{"role": "system", "content": "Public fixture context. " + "x" * 181200},
                      {"role": "user", "content": "Strip whitespace and handle None with a public regression test; request independent review."}],
            cwd=str(tmp_path), runtime={"model": "local-fixture"}, agent_budget=budget,
            deadline_monotonic=deadline, event_sink=observation, enable_work_chain=False,
            enable_bounded_completion=True, enable_implementation_phases=False, max_steps=40)
        run_id = observation.run_id
    assert len(receipts) == 1
    result = receipts[0]
    assert result._usage_counts == ToolUsageCounts(
        sum(role != "parent" for role, _ in calls), sum(worker_tools))
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    journal = RunJournal(tmp_path, data_dir=data_dir)
    rejections = [event.payload for child in journal.list_subagent_runs(run_id)
                  for event in journal.list_subagent_events(run_id, child.subagent_id)
                  if event.type == "execution.request_admission_rejected"]
    snapshot = budget.snapshot()
    print(json.dumps({"case": "failure" if reject_final_review else "success", "floor": floors[0],
                      "usedTokens": snapshot.used_total_tokens, "calls": len(calls),
                      "roles": [role for role, _ in calls], "rejections": rejections,
                      "terminal": trace["terminalReason"]}))
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls) == (500000, 40)
    assert snapshot.used_model_calls == len(calls) and snapshot.used_total_tokens == sum(billed)
    assert snapshot.reserved_total_tokens == 0 and snapshot.used_total_tokens < 500000
    if reject_final_review:
        assert not result.ok and not trace["committed"] and target.read_text() == INITIAL
        assert sum(role == "parent" for role, _ in calls) == 8
        assert messages[-1].get("_workflow_delivery_receipt_summary") is True
        assert messages[-1].get("_partial") is True
        document = messages[-1]["_workflow_delivery"]
        assert document["overallGoal"] == "not_assessed"
        assert len(document["workflows"]) == 1
        handoff = document["workflows"][0]
        assert handoff["commit"] == "not_applied" and handoff["candidateRef"]
        assert any(data_dir.rglob("failure-candidate.json"))
        assert [role for role, _ in adapters].count("general") == 1
        assert [check["exit"] for check in checks if check["formal"]] == [0, 0]
        assert rejections
        return
    assert result.ok and trace["committed"], (trace["terminalReason"], checks, worker_failures)
    assert target.read_text() == COMPLETE and (tmp_path / "test_candidate.py").read_text() == REPAIRED_TEST
    assert [role for role, _ in workers] == ["executor", "reviewer", "executor", "reviewer"]
    assert workers[0][1] is workers[2][1] and workers[0][1] is not None
    assert [role for role, _ in adapters].count("general") == 1
    assert [check["exit"] for check in checks if not check["probe"]] == [0, 0, 1, 0, 0]
    assert [check["exit"] for check in checks if check["probe"]] == [1, 1, 0, 0]
    audit = journal.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None and audit.trace["committed"]
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    assert any(d.get("verdict") == "changes_required" and d["requestedChecks"] == [REQUESTED] for d in documents)
    sessions = [event.payload for child in journal.list_subagent_runs(run_id)
                for event in journal.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    sessions.sort(key=lambda item: item["resumed"])
    assert len(sessions) == 2 and sessions[0]["sessionId"] == sessions[1]["sessionId"]
    assert sessions[0]["resumed"] is False and sessions[0]["modelReused"] is False
    assert sessions[1]["resumed"] and sessions[1]["modelReused"] and sessions[1]["priorMessageCount"] > 0

    assert sum(role == "parent" for role, _ in calls) == 8, "A typed handoff must not purchase a parent model summary."
    assert len(correction_admissions) == 1 and floors[0] > 0
    assert messages[-1].get("_workflow_delivery_receipt_summary") is True
    assert messages[-1].get("_partial") is False
    assert "committed" in final_response_text(messages).lower()
    correction = correction_admissions[0]
    remaining = 500000 - correction["snapshot"].used_total_tokens
    assert correction["input"] + 1 > remaining - floors[0]
    assert correction["input"] + correction["output"] < remaining
    print(json.dumps({"floor": floors[0], "usedTokens": snapshot.used_total_tokens,
                      "calls": len(calls), "correctionRemaining": remaining,
                      "correctionInput": correction["input"], "correctionOutput": correction["output"]}))


def test_typed_parent_handoff_allows_reviewer_correction_and_original_executor_repair(tmp_path, monkeypatch):
    _exercise(tmp_path, monkeypatch)


def test_budget_terminal_review_preserves_candidate_without_new_parent_request(tmp_path, monkeypatch):
    _exercise(tmp_path, monkeypatch, reject_final_review=True)
