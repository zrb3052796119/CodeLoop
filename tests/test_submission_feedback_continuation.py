"""Rejected native declarations preserve repair-only, owner-bound candidates.

Only model choices are scripted. File mutations, isolated pytest, native
validation, shared-ledger exhaustion, session reuse and final gates are real.
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
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_executor_completion_check_gate import _gated, _check_all
from test_failed_candidate_repair import INITIAL, PARTIAL, COMPLETE, TEST, REPAIRED_TEST


PENDING = "executor_submission_feedback_pending"
ERROR = "ExecutionResult deviation step is unknown or duplicated"
LIST_COMPLETE = COMPLETE.replace("if value is None:", "if value is None or value == []:")
LIST_TEST = REPAIRED_TEST + ("def test_empty_list():\n"
                           "    namespace = {}\n"
                           "    exec(Path('candidate.py').read_text(), namespace)\n"
                           "    assert namespace['normalize']([]) == ''\n")
FINDING = "The public empty-list requirement is unimplemented and untested."


def _formal_exits(commands):
    # Formal independent checks may finish in either order. Preserve each
    # actual receipt and compare rounds in approved-check order.
    checks = sorted((item for item in commands if item["formal"]),
                    key=lambda item: (item["executorCalls"], item["checkId"]))
    return [item["exit"] for item in checks]


def _invalid_submission(count=2):
    return {"status": "completed", "steps": [{"step": i, "status": "completed"} for i in range(1, count + 1)],
            "deviations": [{"step": count, "reason": reason, "impact": "low"}
                           for reason in ("First diagnostic.", "Another diagnostic for the same step.")],
            "blockers": []}


@pytest.mark.parametrize("failed", [False, True])
def test_invalid_native_declaration_keeps_current_receipts_as_submission_pending(tmp_path, failed):
    cap, registry, context, state, store, channel, target, *_ = _gated(tmp_path)
    state["b"] = "failed" if failed else "passed"
    _check_all(registry, context)
    result = registry.execute("executor_submit", _invalid_submission(), context)
    assert not result.ok and not result._implementation_submitted and ERROR in result.output
    assert channel.validation_errors and store.read(channel.write_key) is None
    snapshot = cap.capture_checked_candidate(context=context)
    assert snapshot is not None and snapshot.handoff_reason == PENDING
    receipts = [json.loads(item) for item in snapshot.receipts_json]
    assert [item["status"] for item in receipts] == ["passed", state["b"]]
    assert all(item["patchDigest"] == snapshot.patch_digest for item in receipts)
    assert cap.capture_checked_candidate(context=replace(context, _subagent_mailbox=None)) is None
    target.write_text("out of band change\n")
    assert cap.capture_checked_candidate(context=context) is None


def _exercise(tmp_path, monkeypatch, *, mode):
    failed_checks = mode in {"failed-repair", "failed-no-change"}
    review_repair = mode in {"format-review-repair", "format-review-no-change"}
    first_source = PARTIAL if failed_checks else COMPLETE
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_candidate.py").write_text(TEST)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    deadline = time.monotonic() + 1800
    calls, billed, workers, adapters, commands = [], [], [], [], []
    original_worker = task_module._run
    original_loop = task_module.run_agent_turn

    def historical_bounded_loop(**kwargs):
        # The 7,800-token Assignment slice deliberately exercises the old
        # conditional final response; default continuous execution reserves
        # its normal 8,192 output allowance and cannot admit this fixture.
        return original_loop(**(kwargs | {"bounded_implementation_phases": True}))

    monkeypatch.setattr(task_module, "run_agent_turn", historical_bounded_loop)

    def run_worker(data, context):
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        if context._logical_agent_role == "executor":
            number = 1 + sum(item["role"] == "executor" for item in workers)
            if number == 1:
                context = replace(context, _agent_budget=_WorkerCallBudget(
                    context._agent_budget, "first_submission_slice", max_calls=30,
                    preserve_calls=0, max_tokens=7800))
            else:
                assert number in ({2, 3} if review_repair else {2}) and target.read_text() == INITIAL
                assert (Path(context.cwd) / "candidate.py").read_text() == first_source
                assert (Path(context.cwd) / "test_none.py").read_text() == REPAIRED_TEST
                if mode == "failed-no-change":
                    context = replace(context, _agent_budget=_WorkerCallBudget(
                        context._agent_budget, "one_actual_repair_request", max_calls=30,
                        preserve_calls=0, max_tokens=25000))
        workers.append({"role": context._logical_agent_role, "session": data.get("_executor_session")})
        if mode == "no-model-call" and context._logical_agent_role == "executor" and number == 2:
            # A host/custom worker can check and submit without ever admitting
            # a model request. That is not the required same-owner progress.
            cap = data["_executor_check_capability"]
            registry = cap.bind_registry(ToolRegistry([data["_executor_submit_tool"]]))
            for index in range(1, len(cap._checks) + 1):
                assert registry.execute("executor_check", {"check": index}, context).ok
            count = data["_executor_submit_tool"].input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
            declaration = _invalid_submission(count)
            declaration["deviations"] = []
            assert registry.execute("executor_submit", declaration, context).ok
            result = ToolResult(ok=True, output="Custom worker submitted without a new model call.")
        else:
            result = original_worker(data, context)
        workers[-1]["result"] = {"ok": result.ok, "reason": str(result._termination_reason)}
        return result

    def call(name, values, identity):
        return {"id": identity, "toolName": name, "input": values}

    def response(messages, tools, actions):
        schemas = [{"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": tool.input_schema}} for tool in tools.list()]
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
            count = next(tool for tool in tools.list() if tool.name == "executor_submit").input_schema[
                "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
            if number == 1:
                actions = [call("edit_file", {"path": "candidate.py", "old": INITIAL, "new": first_source}, "source"),
                           call("executor_test", {"path": "test_none.py", "code": REPAIRED_TEST}, "new-test"),
                           call("executor_submit", _invalid_submission(count), "invalid-submit")]
            elif number == 2:
                assert sum(item["role"] == "executor" for item in workers) == 2
                assert any(item.get("role") == "tool_result" and item.get("toolUseId") == "invalid-submit"
                           and item.get("isError") and ERROR in item.get("content", "") for item in messages)
                assert any(item.get("toolUseId") == "new-test" for item in messages)
                assert _formal_exits(commands) == [0, 1 if failed_checks else 0]
                if mode == "failed-repair":
                    actions = [call("edit_file", {"path": "candidate.py", "old": PARTIAL, "new": COMPLETE}, "fix")]
                else:
                    # No source mutation: fresh checks and a corrected native
                    # declaration are the only state progress in this case.
                    declaration = _invalid_submission(count)
                    declaration["deviations"] = []
                    actions = [call("executor_check", {"check": index}, f"fresh-check-{index}") for index in (1, 2)]
                    actions.append(call("executor_submit", declaration, "corrected-submit"))
            elif number == 3 and mode == "failed-repair":
                declaration = _invalid_submission(count)
                declaration["deviations"] = []
                actions = [call("executor_submit", declaration, "corrected-submit")]
            elif number >= 3 and mode == "failed-no-change":
                assert any(item.get("toolUseId") == "corrected-submit" and item.get("isError")
                           and "every approved check to pass" in item.get("content", "") for item in messages)
                declaration = _invalid_submission(count)
                declaration["deviations"] = []
                actions = [call("executor_submit", declaration, f"still-unfixed-submit-{number}")]
            elif number == 3 and review_repair:
                assert sum(item["role"] == "executor" for item in workers) == 3
                assert FINDING in str(messages)
                if mode == "format-review-repair":
                    actions = [call("edit_file", {"path": "candidate.py", "old": COMPLETE, "new": LIST_COMPLETE}, "review-fix"),
                               call("executor_test", {"path": "test_none.py", "code": LIST_TEST}, "review-regression")]
                else:
                    declaration = _invalid_submission(count)
                    declaration["deviations"] = []
                    actions = [call("executor_check", {"check": index}, f"unchanged-check-{index}") for index in (1, 2)]
                    actions.append(call("executor_submit", declaration, "unchanged-submit"))
            elif number == 4 and mode == "format-review-repair":
                declaration = _invalid_submission(count)
                declaration["deviations"] = []
                actions = [call("executor_submit", declaration, "submit-review-repair")]
            else:
                raise AssertionError("Unexpected extra or replacement Executor request")
            return response(messages, tools, actions)

    class Reviewer:
        tools = None
        count = 0

        def __init__(self, revision):
            self.revision = revision

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            calls.append(("reviewer", deepcopy(messages)))
            review_number = self.revision
            assert sum(item["role"] == "executor" for item in workers) == (review_number + 1 if review_repair else 2)
            assert target.read_text() == INITIAL
            assert _formal_exits(commands)[-2:] == [0, 0]
            negative = review_repair and review_number == 1
            evidence = {}
            if not negative:
                if self.count == 1:
                    return response(messages, tools_override or self.tools, [call("review_test", {
                        "criterionIds": ["trim", "none"],
                        "scenario": "Exercise whitespace trimming and the declared empty input branches independently.",
                        "code": LIST_TEST if review_repair else REPAIRED_TEST,
                    }, "independent-review-check")])
                assert self.count == 2
                feedback = next(item for item in messages if item.get("role") == "tool_result"
                                and item.get("toolUseId") == "independent-review-check")
                receipt = json.loads(feedback["content"])
                assert receipt["status"] == "passed" and receipt["testReport"]["passed"] > 0
                evidence = {"probeIds": [receipt["probeId"]], "sourceEvidence": [],
                            "rationale": "The independently executed public input cases pass on this candidate."}
            return response(messages, tools_override or self.tools, [call("review_submit", {
                "verdict": "changes_required" if negative else "approved",
                "criteria": [{"criterionId": "trim", "status": "satisfied", **evidence},
                             {"criterionId": "none", "status": "failed" if negative else "satisfied", **evidence}],
                "blockingFindings": [{"criterionIds": ["none"], "statement": FINDING}] if negative else [],
                "warnings": [], "requestedChecks": [], "confidence": "high",
            }, "review")])

    executor = Executor()

    def factory(_runtime, agent_type, **kwargs):
        model = executor if agent_type == "general" else Reviewer(1 + sum(kind != "general" for kind, _ in adapters))
        model.tools = kwargs["tools"]
        adapters.append((agent_type, model))
        return model

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == INITIAL
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            result = super().run(**kwargs)
            commands.extend({"formal": formal, "exit": item["exitCode"], "cwd": kwargs["cwd"],
                             "checkId": item["checkId"], "executorCalls": sum(role == "executor" for role, _ in calls)}
                            for item in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(task_module, "_run", run_worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_submission_feedback", "kind": "change", "risk": "low", "complex": False,
                    "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_none.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace."},
                     {"id": "none", "kind": "behavior", "text": "Return an empty string for None or an empty list."
                      if review_repair else "Return an empty string for None."}],
        "verificationChecks": [
            {"checkId": "compat", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_candidate.py"], "criterionIds": ["trim"]},
            {"checkId": "regression", "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "test_none.py"], "criterionIds": ["none"]}]}
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="submission feedback continuation",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "complete public behavior", "agent_type": "workflow",
            "prompt": "Strip whitespace and handle None with a public regression test.", "workflow_request": request},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"}, _agent_budget=budget,
                _task_deadline_monotonic=deadline, _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    snapshot = budget.snapshot()
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls) == (500000, 40)
    assert snapshot.used_total_tokens == sum(billed) and snapshot.used_model_calls == len(calls)
    assert snapshot.reserved_total_tokens == 0
    assert workers[0]["result"]["reason"] == "ToolTerminationReason.BUDGET_EXCEEDED"
    assert all(not Path(item["cwd"]).exists() for item in commands)
    reloaded = RunJournal(tmp_path, data_dir=data_dir)
    audit = reloaded.get_scheduler_audit(run_id, trace["workflowId"])
    return result, trace, target, calls, workers, adapters, commands, reloaded, run_id, audit


@pytest.mark.parametrize("mode", ["failed-repair", "format-only"])
def test_rejected_submission_returns_to_original_executor_and_completes_fresh_gates(tmp_path, monkeypatch, mode):
    result, trace, target, calls, workers, adapters, commands, journal, run_id, audit = _exercise(tmp_path, monkeypatch, mode=mode)
    assert result.ok and trace["committed"], (trace["terminalReason"], result.output)
    assert target.read_text() == COMPLETE
    executors = [item for item in workers if item["role"] == "executor"]
    assert len(executors) == 2 and executors[0]["session"] is executors[1]["session"]
    assert [kind for kind, _ in adapters].count("general") == 1
    assert _formal_exits(commands) == [0, 1 if mode == "failed-repair" else 0, 0, 0]
    assert audit is not None and audit.trace["committed"]
    documents = [json.loads(item.content) for item in audit.artifacts]
    candidate = next(item for item in documents if item.get("resultType") == "ExecutionCandidate.v1")
    final = next(item for item in documents if item.get("resultType") == "ExecutionResult.v1")
    assert candidate["handoffReason"] == PENDING and candidate["status"] == "awaiting_verification"
    assert "stepResults" not in candidate and final["status"] == "completed"
    assert (candidate["changeSet"]["patchDigest"] == final["changeSet"]["patchDigest"]) is (mode == "format-only")
    assert all(item.get("executionAssignmentId") != candidate["assignmentId"] for item in documents
               if item.get("resultType") == "AcceptanceEvidenceMatrix.v1")
    sessions = [event.payload for child in journal.list_subagent_runs(run_id)
                for event in journal.list_subagent_events(run_id, child.subagent_id)
                if event.type == "execution.executor_session"]
    assert len(sessions) == 2 and len({item["sessionId"] for item in sessions}) == 1
    resumed = [item for item in sessions if item["resumed"]]
    assert len(resumed) == 1 and resumed[0]["modelReused"] and resumed[0]["priorMessageCount"] > 0
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None


def test_same_patch_without_new_executor_model_request_remains_blocked(tmp_path, monkeypatch):
    result, trace, target, calls, workers, _, commands, _, _, _ = _exercise(tmp_path, monkeypatch, mode="no-model-call")
    assert not result.ok and not trace["committed"]
    assert target.read_text() == INITIAL
    assert len([item for item in workers if item["role"] == "executor"]) == 2
    assert [role for role, _ in calls] == ["executor"]
    assert _formal_exits(commands) == [0, 0]
    assert any(item["errorCode"] == "implementation_repair_no_change" for item in trace["transitions"])


def test_same_patch_with_prior_failed_check_cannot_be_declaration_only_progress(tmp_path, monkeypatch):
    result, trace, target, calls, workers, _, commands, _, _, audit = _exercise(tmp_path, monkeypatch, mode="failed-no-change")
    assert not result.ok and not trace["committed"]
    assert target.read_text() == INITIAL
    assert 3 <= len(calls) < 40 and all(role == "executor" for role, _ in calls)
    executors = [item for item in workers if item["role"] == "executor"]
    assert len(executors) == 2 and executors[0]["session"] is executors[1]["session"]
    assert _formal_exits(commands) == [0, 1]
    assert any(item["errorCode"] == "implementation_repair_no_change" for item in trace["transitions"]), (
        [item["result"] for item in workers], [item["errorCode"] for item in trace["transitions"]])
    assert audit is not None and not audit.trace["committed"]
    documents = [json.loads(item.content) for item in audit.artifacts]
    assert not any(item.get("resultType") in {"ExecutionResult.v1", "ReviewVerdict.v1", "AcceptanceEvidenceMatrix.v1"}
                   for item in documents)


@pytest.mark.parametrize("mode", ["format-review-repair", "format-review-no-change"])
def test_same_patch_declaration_progress_keeps_subsequent_review_repair_guarded(tmp_path, monkeypatch, mode):
    result, trace, target, calls, workers, adapters, commands, _, _, audit = _exercise(tmp_path, monkeypatch, mode=mode)
    executors = [item for item in workers if item["role"] == "executor"]
    assert len(executors) == 3 and all(item["session"] is executors[0]["session"] for item in executors)
    assert [kind for kind, _ in adapters].count("general") == 1
    if mode == "format-review-repair":
        assert result.ok and trace["committed"] and target.read_text() == LIST_COMPLETE
        assert [role for role, _ in calls] == ["executor", "executor", "reviewer", "executor", "executor", "reviewer", "reviewer"]
        assert _formal_exits(commands) == [0, 0, 0, 0, 0, 0]
        assert audit is not None and audit.trace["committed"]
    else:
        assert not result.ok and not trace["committed"] and target.read_text() == INITIAL
        assert [role for role, _ in calls] == ["executor", "executor", "reviewer", "executor"]
        assert _formal_exits(commands) == [0, 0, 0, 0]
        assert any(item["errorCode"] == "implementation_repair_no_change" for item in trace["transitions"])
