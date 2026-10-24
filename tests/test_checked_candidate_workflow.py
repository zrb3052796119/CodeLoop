"""Budget-ended checked candidates still need independent verification/review.

The model is a deterministic local adapter. Files, pytest, task/worker loops,
shared budgets, durable audit and promotion use production paths. No completed
Executor declaration is invented and no provider usage is claimed.
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


INITIAL = "def normalize(value):\n    return value\n"
PARTIAL = "def normalize(value):\n    return value.strip()\n"
COMPLETE = "def normalize(value):\n    if value is None:\n        return ''\n    return value.strip()\n"
TEST = ("from pathlib import Path\n"
        "def test_whitespace():\n"
        "    namespace = {}\n"
        "    exec(Path('candidate.py').read_text(), namespace)\n"
        "    assert namespace['normalize']('  Alice  ') == 'Alice'\n")
REPAIRED_TEST = TEST + ("def test_none():\n"
                        "    namespace = {}\n"
                        "    exec(Path('candidate.py').read_text(), namespace)\n"
                        "    assert namespace['normalize'](None) == ''\n")
FINDING = "The public None-input behavior is missing from the candidate."


def _exercise(tmp_path, monkeypatch, *, mode):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_candidate.py").write_text(TEST)
    if mode == "inconclusive-check":
        (tmp_path / "test_required_missing.py").write_text(
            "import time\ndef test_wait():\n    time.sleep(5)\n")
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    monkeypatch.delenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raising=False)
    prefix_tokens, prefix_calls = [0], [0]
    calls, commands, adapters, workers, billed, probe_receipts = [], [], [], [], [], []
    deadline = time.monotonic() + 600
    original_run = task_module._run

    def observe_worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        workers.append({"role": context._logical_agent_role,
                        "session": data.get("_executor_session"),
                        "budget": context._agent_budget})
        return original_run(data, context)

    def metered(messages, tools, tool_calls):
        schemas = [{"type": "function", "function": {"name": t.name,
                    "description": t.description, "parameters": t.input_schema}}
                   for t in tools.list()]
        sent = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
        output = estimate_tokens(json.dumps(tool_calls))
        billed.append(sent + output)
        return AgentStep(type="tool_calls", calls=tool_calls,
                         usage=ModelUsage(input_tokens=sent, output_tokens=output, source="estimated"))

    def tool(name, values, call_id):
        return {"id": call_id, "toolName": name, "input": values}

    class Executor:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            calls.append(("executor", deepcopy(messages)))
            number = sum(role == "executor" for role, _ in calls)
            tools = tools_override or self.tools
            if number == 1:
                if mode == "blocked":
                    action = tool("edit_file", {"path": "candidate.py", "old": INITIAL,
                                                "new": COMPLETE}, "first-edit")
                else:
                    action = tool("executor_check", {"check": 1}, "initial-failed-check")
            elif number == 2:
                if mode == "blocked":
                    count = next(t for t in tools.list() if t.name == "executor_submit").input_schema[
                        "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                    action = tool("executor_submit", {"status": "blocked",
                        "steps": [{"step": n, "status": "failed"} for n in range(1, count + 1)],
                        "deviations": [], "blockers": ["Public requirement remains unassessed."]}, "explicit-blocked")
                else:
                    assert any(m.get("toolUseId") == "initial-failed-check" and m.get("isError")
                               for m in messages), "The last edit follows a real failed pytest."
                    action = tool("edit_file", {"path": "candidate.py", "old": INITIAL,
                        "new": PARTIAL if mode.startswith("review-") else COMPLETE}, "last-edit-no-submit")
            elif number == 3 and mode == "review-repair":
                assert FINDING in str(messages), "Same Executor must receive the actual review finding."
                assert any(m.get("toolUseId") == "last-edit-no-submit" for m in messages)
                action = tool("executor_test", {"path": "test_candidate.py", "code": REPAIRED_TEST}, "test-none")
            elif number == 4 and mode == "review-repair":
                assert any(m.get("_implementation_checks_failed") is True for m in messages)
                action = tool("edit_file", {"path": "candidate.py", "old": PARTIAL,
                                            "new": COMPLETE}, "repair-no-submit")
            else:
                raise AssertionError("The checked final edit must hit the real token admission boundary.")
            return metered(messages, tools, [action])

    class Reviewer:
        tools = None

        def __init__(self):
            self.count = 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            calls.append(("reviewer", deepcopy(messages)))
            tools = tools_override or self.tools
            native_only = {t.name for t in tools.list()} == {"review_submit"}
            if self.count == 1 and not native_only:
                actions = [tool("read_file", {"path": "candidate.py"}, "review-source")]
                if not probe_receipts:
                    actions.append(tool("review_test", {
                        "criterionIds": ["trim", "none"],
                        "scenario": "Whitespace is stripped and None returns the documented empty string.",
                        "code": REPAIRED_TEST,
                    }, "independent-normalize-probe"))
            else:
                assert self.count == 2 or native_only, json.dumps(messages[-3:])
                for message in messages:
                    if (message.get("role") == "tool_result"
                            and message.get("toolUseId") == "independent-normalize-probe"):
                        receipt, _ = json.JSONDecoder().raw_decode(message["content"].lstrip())
                        assert receipt["status"] in {"passed", "failed"}, receipt
                        if receipt not in probe_receipts:
                            probe_receipts.append(receipt)
                if native_only:
                    # The real tail tool view permits only the conclusion.
                    # Its candidate packet is already present; no fabricated
                    # read or extra work request is needed for this toy case.
                    source = "\n".join(str(m.get("content", "")) for m in messages)
                    assert "candidate.py" in source
                else:
                    source = next(m["content"] for m in reversed(messages)
                                  if m.get("role") == "tool_result" and m.get("toolUseId") == "review-source")
                complete = "if value is None:" in source
                assert target.read_text() == INITIAL, "Reviewer cannot observe premature parent writeback."
                criteria = [{"criterionId": "trim", "status": "satisfied"},
                            {"criterionId": "none", "status": "satisfied" if complete else "failed"}]
                if complete:
                    assert probe_receipts and probe_receipts[0]["probeId"] in str(messages)
                    for criterion in criteria:
                        criterion.update(
                            rationale="The independently executed tests assert whitespace normalization and the None boundary.",
                            probeIds=[probe_receipts[0]["probeId"]], sourceEvidence=[])
                if mode == "review-no-budget":
                    # A recorded historical call prefix leaves the original
                    # ten downstream calls, insufficient for another Executor
                    # Assignment. The current admitted verdict still completes.
                    while budget.snapshot().used_model_calls < 30:
                        lease = budget.reserve_model_call()
                        budget.record_model_call(reservation=lease)
                        prefix_calls[0] += 1
                actions = [tool("review_submit", {
                    "verdict": "approved" if complete else "changes_required",
                    "criteria": criteria,
                    "findings": [] if complete else [{"criterionIds": ["none"], "statement": FINDING}],
                    "warnings": [], "requestedChecks": [], "confidence": "high",
                }, "real-review-verdict")]
            return metered(messages, tools, actions)

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
            if mode == "inconclusive-check":
                kwargs["checks"] = tuple(replace(c, timeout_seconds=1)
                    if c.check_id == "missing" else c for c in kwargs["checks"])
            result = super().run(**kwargs)
            probe_run = any("__minicode_review_probe__.py" in check.argv
                            for check in kwargs["checks"])
            commands.extend({"exit": c["exitCode"], "cwd": kwargs["cwd"],
                             "probe": probe_run,
                             "parent": target.read_text()} for c in result["checks"])
            executor_count = sum(role == "executor" for role, _ in calls)
            if (not probe_run and any(c["exitCode"] == 1 for c in result["checks"])
                    and executor_count in (1, 3)):
                worker = next(w["budget"] for w in reversed(workers) if w["role"] == "executor")
                # Price the next actual request, including its current phase
                # output/handoff floor. A previous request's input plus a
                # hard-coded output cap can stop before the intended edit as
                # schemas evolve. Historical usage remains a separate charge
                # on the same 500k ledger; subsequent requests are not granted
                # an extra allowance.
                reserve = worker.reserve_model_call

                def price_final_edit(estimated_input_tokens=0, **reserve_kwargs):
                    monkeypatch.setattr(worker, "reserve_model_call", reserve)
                    allowance = (estimated_input_tokens
                                 + reserve_kwargs.get("preserve_total_tokens", 0) + 512)
                    prefix = (worker.snapshot().limit_total_tokens - allowance
                              - budget.snapshot().used_total_tokens)
                    assert prefix >= 0
                    budget.record_model_call(input_tokens=prefix)
                    prefix_tokens[0] += prefix
                    return reserve(estimated_input_tokens, **reserve_kwargs)

                monkeypatch.setattr(worker, "reserve_model_call", price_final_edit)
            return result

    verifier = ObservedVerifier(sandbox_backend=TestSandbox(),
                                explicit_trusted_executables={"python": sys.executable})

    monkeypatch.setattr(task_module, "_run", observe_worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
                        lambda *_a, **_k: verifier)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_checked_candidate", "kind": "change", "risk": "low",
                    "complex": False, "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_candidate.py"],
                  "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace."},
                     {"id": "none", "kind": "behavior", "text": "Return an empty string for None."}],
        "verificationChecks": [{"checkId": "public", "kind": "tests",
            "argv": ["python", "-m", "pytest", "-q", "test_candidate.py"], "criterionIds": ["trim", "none"]}]}
    if mode == "inconclusive-check":
        request["verificationChecks"].append({"checkId": "missing", "kind": "tests",
            "argv": ["python", "-m", "pytest", "-q", "test_required_missing.py"], "criterionIds": ["none"]})
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="checked candidate local proof",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "check candidate delivery",
            "prompt": "Update normalize: strip surrounding whitespace and return an empty string for None. Review the resulting change.",
            "agent_type": "workflow", "workflow_request": request},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"}, _agent_budget=budget,
                        _task_deadline_monotonic=deadline, _central_workflow_v2_enforced=True,
                        _event_sink=observation))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert all(c["parent"] == INITIAL for c in commands)
    assert all(not Path(c["cwd"]).exists() for c in commands), "Isolated test clones must close."
    assert budget.snapshot().limit_total_tokens == 500000
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().used_model_calls == prefix_calls[0] + len(calls)
    assert budget.snapshot().used_total_tokens == prefix_tokens[0] + sum(billed)
    assert budget.snapshot().reserved_total_tokens == 0
    assert [kind for kind, _ in adapters].count("general") == 1
    reloaded = RunJournal(tmp_path, data_dir=data_dir)
    audit = reloaded.get_scheduler_audit(run_id, trace["workflowId"])
    if audit is not None:
        assert audit.trace["committed"] == trace["committed"]
        trace = audit.trace
        if result.ok:
            documents = [json.loads(artifact.content) for artifact in audit.artifacts]
            candidates = [d for d in documents if d.get("resultType") == "ExecutionCandidate.v1"]
            assert candidates and all(d["status"] == "awaiting_verification" for d in candidates)
            assert all(d["unassessedStepIds"] and "stepResults" not in d for d in candidates)
            assert all(len(d["implementationReceiptRefs"]) == len(request["verificationChecks"])
                       for d in candidates)
            assert any(d.get("resultType") == "ReviewProbeSource.v1" for d in documents)
            assert any(d.get("resultType") == "ReviewCoverageEvidence.v1" for d in documents)
            assert not any(d.get("resultType") == "ExecutionResult.v1" for d in documents)
            assert reloaded.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    else:
        assert not result.ok, "Successful promotion requires a persisted, independently replayable audit."
    sessions = [event.payload for child in reloaded.list_subagent_runs(run_id)
                for event in reloaded.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    sessions.sort(key=lambda item: item["resumed"])
    print(json.dumps({"kind": "checked-candidate-local-proof", "mode": mode,
        "usageSource": "estimated", "historicalTokenPrefix": prefix_tokens[0],
        "historicalCallPrefix": prefix_calls[0], "newModelCalls": len(calls),
        "newEstimatedTokens": sum(billed), "usedTokens": budget.snapshot().used_total_tokens,
        "globalTokenLimit": 500000, "globalCallLimit": 40,
        "checks": [c["exit"] for c in commands], "committed": trace["committed"],
        "terminalReason": trace["terminalReason"], "sessionCount": len(sessions),
        "resumed": any(s["resumed"] for s in sessions)}, sort_keys=True))
    return result, trace, commands, calls, target, sessions, data_dir


def test_checked_budget_ended_candidate_reaches_formal_verification_review_and_commit(tmp_path, monkeypatch):
    result, trace, commands, calls, target, sessions, data_dir = _exercise(tmp_path, monkeypatch, mode="approve")
    assert result.ok and trace["committed"], (
        "A checked budget-ended candidate never reached independent gates:",
        trace["terminalReason"], [c["exit"] for c in commands])
    assert target.read_text() == COMPLETE
    assert [c["exit"] for c in commands if not c["probe"]] == [1, 0, 0]
    assert [c["exit"] for c in commands if c["probe"]] == [0]
    assert [role for role, _ in calls] == ["executor", "executor", "reviewer", "reviewer"]
    assert len(sessions) == 1 and sessions[0]["resumed"] is False
    assert any(data_dir.rglob("commit-ready.json"))


def test_review_rejection_returns_checked_candidate_to_same_executor_session(tmp_path, monkeypatch):
    result, trace, commands, calls, target, sessions, _data_dir = _exercise(tmp_path, monkeypatch, mode="review-repair")
    assert result.ok and trace["committed"], (
        "A host candidate did not retain the standard review/repair loop:",
        trace["terminalReason"], [c["exit"] for c in commands])
    assert target.read_text() == COMPLETE
    assert [c["exit"] for c in commands if not c["probe"]] == [1, 0, 0, 1, 0, 0]
    # The unchanged probe fails in review and after adding the regression,
    # then passes after the real repair and again at independent review.
    assert [c["exit"] for c in commands if c["probe"]] == [1, 1, 0, 0]
    assert [role for role, _ in calls].count("executor") == 4
    assert len(sessions) == 2 and sessions[0]["sessionId"] == sessions[1]["sessionId"]
    assert sessions[1]["resumed"] and sessions[1]["modelReused"] and sessions[1]["priorMessageCount"] > 0
    assert any(t["outcome"] == "repairing" for t in trace["transitions"])


def test_review_rejection_without_repair_budget_preserves_candidate_without_commit(tmp_path, monkeypatch):
    result, trace, commands, _calls, target, _sessions, data_dir = _exercise(tmp_path, monkeypatch, mode="review-no-budget")
    assert not result.ok and not trace["committed"]
    assert target.read_text() == INITIAL
    assert [c["exit"] for c in commands if not c["probe"]] == [1, 0, 0]
    assert [c["exit"] for c in commands if c["probe"]] == [1]
    assert any(t["stage"] == "reviewer" for t in trace["transitions"]), "Rejection must be an actual Reviewer verdict."
    assert any(data_dir.rglob("failure-candidate.json"))


@pytest.mark.parametrize("mode", ["inconclusive-check", "blocked"])
def test_inconclusive_check_or_explicit_blocker_cannot_become_host_candidate(tmp_path, monkeypatch, mode):
    result, trace, commands, calls, target, _sessions, _data_dir = _exercise(tmp_path, monkeypatch, mode=mode)
    assert not result.ok and not trace["committed"]
    assert target.read_text() == INITIAL
    assert commands
    assert not any(role == "reviewer" for role, _ in calls)
    assert not any(t["stage"] == "verifier" for t in trace["transitions"])
