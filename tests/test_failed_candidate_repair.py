"""Definite failed checks remain evidence for independent verification and repair.

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
    if mode == "inconclusive":
        (tmp_path / "test_required_missing.py").write_text("import time\ndef test_wait():\n    time.sleep(5)\n")
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    monkeypatch.delenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raising=False)
    prefix_tokens, prefix_calls = [0], [0]
    calls, commands, adapters, workers, billed = [], [], [], [], []
    input_estimates = []
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
        input_estimates.append(sent)
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
                action = tool("executor_check", {"check": 1}, "initial-failed-check")
            elif number == 2:
                assert any(m.get("toolUseId") == "initial-failed-check" and m.get("isError")
                           for m in messages), "The last edit follows a real failed pytest."
                action = tool("edit_file", {"path": "candidate.py", "old": INITIAL,
                                            "new": COMPLETE}, "last-edit-with-missing-test")
            elif number == 3 and mode == "repair":
                assert any(m.get("toolUseId") == "last-edit-with-missing-test" for m in messages)
                assert "test_required_missing.py" in str(messages)
                assert "exitCode" in str(messages) and "4" in str(messages)
                assert any(c["exit"] == 4 and c["formal"] for c in commands), (
                    "A fresh formal verifier must fail before repair, not a fabricated model finding.")
                action = tool("executor_test", {"path": "test_required_missing.py",
                                               "code": REPAIRED_TEST}, "create-regression")
            elif number == 4 and mode == "repair":
                count = next(t for t in tools.list() if t.name == "executor_submit").input_schema[
                    "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                action = tool("executor_submit", {"status": "completed",
                    "steps": [{"step": n, "status": "completed"} for n in range(1, count + 1)],
                    "deviations": [], "blockers": []}, "submit-after-real-tests")
            else:
                raise AssertionError("Expected real budget admission or the same-session bounded repair.")
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
            else:
                assert self.count == 2 or native_only
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
                actions = [tool("review_submit", {
                    "verdict": "approved" if complete else "changes_required",
                    "criteria": [{"criterionId": "trim", "status": "satisfied"},
                                 {"criterionId": "none", "status": "satisfied" if complete else "failed"}],
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
            # Formal verification publishes its real receipt through the
            # production per-check sink; implementation feedback uses a local
            # collector. Both execute the same real verifier subprocess path.
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            evidence = []
            original_sink = kwargs["evidence_sink"]
            def capture_evidence(artifact_id, document):
                evidence.append(document)
                return original_sink(artifact_id, document)
            kwargs["evidence_sink"] = capture_evidence
            if mode == "inconclusive":
                kwargs["checks"] = tuple(replace(c, timeout_seconds=1)
                    if c.check_id == "missing" else c for c in kwargs["checks"])
            result = super().run(**kwargs)
            commands.extend({"exit": c["exitCode"], "cwd": kwargs["cwd"],
                             "formal": formal, "status": c["status"],
                             "failureReason": next(e.get("failureReason") for e in evidence if e["checkId"] == c["checkId"]),
                             "parent": target.read_text()} for c in result["checks"])
            if formal and mode == "no-repair-budget" and any(c["exitCode"] == 4 for c in result["checks"]):
                while budget.snapshot().used_model_calls < 30:
                    lease = budget.reserve_model_call()
                    budget.record_model_call(reservation=lease)
                    prefix_calls[0] += 1
            executor_count = sum(role == "executor" for role, _ in calls)
            if any(c["exitCode"] == 1 for c in result["checks"]) and executor_count == 1:
                worker = next(w["budget"] for w in reversed(workers) if w["role"] == "executor")
                # Reproduce the known spent-prefix decision boundary without
                # a large artificial prompt. New calls are billed from their
                # actual messages/schemas; this is separate historical usage.
                # Leave room for the final edit request, but less than a
                # further request's input after its real check feedback. A
                # fixed 4096 output reservation no longer forces handoff:
                # the production loop can legitimately shrink that ceiling.
                allowance = input_estimates[-1] + 512
                prefix = worker.snapshot().limit_total_tokens - allowance - budget.snapshot().used_total_tokens
                assert prefix >= 0
                budget.record_model_call(input_tokens=prefix)
                prefix_tokens[0] += prefix
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
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_required_missing.py"],
                  "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace."},
                     {"id": "none", "kind": "behavior", "text": "Return an empty string for None."}],
        "verificationChecks": [{"checkId": "public", "kind": "tests",
            "argv": ["python", "-m", "pytest", "-q", "test_candidate.py"], "criterionIds": ["trim", "none"]}]}
    request["verificationChecks"].append({"checkId": "missing", "kind": "tests",
            "argv": ["python", "-m", "pytest", "-q", "test_required_missing.py"], "criterionIds": ["none"]})
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="checked candidate local proof",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "check candidate delivery",
            "prompt": "Update normalize: strip surrounding whitespace and return an empty string for None.",
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
            assert any(d.get("resultType") == "ExecutionResult.v1" for d in documents)
            assert reloaded.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    else:
        assert not result.ok, "Successful promotion requires a persisted, independently replayable audit."
    sessions = [event.payload for child in reloaded.list_subagent_runs(run_id)
                for event in reloaded.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    sessions.sort(key=lambda item: item["resumed"])
    print(json.dumps({"kind": "failed-candidate-local-proof", "mode": mode,
        "usageSource": "estimated", "historicalTokenPrefix": prefix_tokens[0],
        "historicalCallPrefix": prefix_calls[0], "newModelCalls": len(calls),
        "newEstimatedTokens": sum(billed), "usedTokens": budget.snapshot().used_total_tokens,
        "globalTokenLimit": 500000, "globalCallLimit": 40,
        "checks": [c["exit"] for c in commands], "committed": trace["committed"],
        "terminalReason": trace["terminalReason"], "sessionCount": len(sessions),
        "resumed": any(s["resumed"] for s in sessions)}, sort_keys=True))
    return result, trace, commands, calls, target, sessions, data_dir, audit



def test_definite_failed_candidate_runs_formal_verifier_and_same_executor_repairs(tmp_path, monkeypatch):
    result, trace, commands, calls, target, sessions, data_dir, audit = _exercise(tmp_path, monkeypatch, mode="repair")
    assert result.ok and trace["committed"], (
        "Definite failed candidate never reached formal verification and same-session repair:",
        trace["terminalReason"], [c["exit"] for c in commands])
    assert sorted(c["exit"] for c in commands) == [0, 0, 0, 0, 0, 0, 1, 4, 4]
    assert sorted(c["exit"] for c in commands if c["formal"]) == [0, 0, 0, 4]
    assert target.read_text() == COMPLETE
    assert (tmp_path / "test_required_missing.py").read_text() == REPAIRED_TEST
    assert [role for role, _ in calls].count("executor") == 4
    assert len(sessions) == 2 and sessions[0]["sessionId"] == sessions[1]["sessionId"]
    assert sessions[1]["resumed"] and sessions[1]["modelReused"] and sessions[1]["priorMessageCount"] > 0
    assert any(t["stage"] == "verifier" and t["outcome"] == "repairing" for t in trace["transitions"])
    assert any(t["outcome"] == "repairing" for t in trace["transitions"])
    assert any(t["stage"] == "reviewer" for t in trace["transitions"])
    assert any(data_dir.rglob("commit-ready.json"))
    documents = [json.loads(a.content) for a in audit.artifacts]
    assert any(d.get("resultType") == "VerificationResult.v1" and d["status"] == "failed"
               for d in documents), "The audit must retain the actual failed formal verification."
    pending = [d for d in documents if d.get("resultType") == "ExecutionCandidate.v1"]
    assert len(pending) == 1 and pending[0]["status"] == "awaiting_verification"
    assert any(d.get("resultType") == "ExecutionResult.v1" for d in documents)


def test_failed_candidate_without_remaining_repair_calls_cannot_commit(tmp_path, monkeypatch):
    result, trace, commands, calls, target, sessions, data_dir, _audit = _exercise(tmp_path, monkeypatch, mode="no-repair-budget")
    assert not result.ok and not trace["committed"]
    assert target.read_text() == INITIAL
    assert sorted(c["exit"] for c in commands) == [0, 0, 1, 4, 4], (
        "A definite failure must reach fresh formal verification before repair admission is denied.")
    assert len(sessions) == 1
    assert not any(role == "reviewer" for role, _ in calls)
    assert any(data_dir.rglob("failure-candidate.json"))
    assert not any(data_dir.rglob("commit-ready.json"))


def test_inconclusive_candidate_cannot_enter_formal_verification(tmp_path, monkeypatch):
    result, trace, commands, calls, target, sessions, data_dir, _audit = _exercise(tmp_path, monkeypatch, mode="inconclusive")
    assert not result.ok and not trace["committed"]
    assert target.read_text() == INITIAL
    assert any(c["failureReason"] for c in commands)
    assert not any(c["formal"] for c in commands)
    assert len(sessions) == 1
    assert not any(role == "reviewer" for role, _ in calls)
    assert not any(data_dir.rglob("commit-ready.json"))
