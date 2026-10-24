"""Green partial tests must reach semantic review and the existing Executor.

The adapters are scripted, so this is a host state-machine regression rather
than a quality benchmark. The Executor uses real edits and tests; the Reviewer
reads the actual candidate with real tools; final verification and promotion
are real. Every expected behavior is stated in the public task below.
"""

from copy import deepcopy
from contextlib import nullcontext
import json
import re
import subprocess
import sys

import pytest

from minicode import subagent_model_routing
from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_audit import SchedulerAuditJournal
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.subagent_notes import subagent_note_write_tool
from minicode.types import AgentStep, ModelAdapter, ModelUsage
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module

from test_workflow_v2_structured_routing import _prompt_json, _request, _review_patch_digest


INITIAL = "def normalize_name(value):\n    return value\n"
PARTIAL = "def normalize_name(value):\n    return value.strip()\n"
COMPLETE = "def normalize_name(value):\n    if value is None:\n        return ''\n    return value.strip()\n"
PUBLIC_TEST = (
    "from candidate import normalize_name\n"
    "def test_whitespace():\n"
    "    assert normalize_name('  Alice  ') == 'Alice'\n"
)
COMPLETE_TEST = PUBLIC_TEST + (
    "def test_none():\n"
    "    assert normalize_name(None) == ''\n"
)
TASK = (
    "Update normalize_name in candidate.py.\n"
    "1. Strip surrounding whitespace from a string.\n"
    "2. Return the empty string when the input is None.\n"
    "Add regression tests for both requirements in tests/test_eval_regression.py. "
    "Independently review the implementation and test coverage before delivery."
)
FINDING = "The public None-input requirement has neither implementation nor a regression assertion."


def _tool(name, values, call_id):
    return AgentStep(type="tool_calls", calls=[{"id": call_id, "toolName": name, "input": values}],
                     usage=ModelUsage(input_tokens=100, output_tokens=10, source="provider"))


@pytest.mark.parametrize("repair,durable", [(True, False), (False, False), (True, True)],
                         ids=["same-executor-repairs", "rejected-review-cannot-commit", "durable-repair-and-session"])
def test_green_partial_candidate_is_reviewed_before_commit_and_resumes_same_executor(tmp_path, monkeypatch, repair, durable):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL, encoding="utf-8")
    test_file = tmp_path / "tests" / "test_eval_regression.py"
    test_file.parent.mkdir()
    test_file.write_text(PUBLIC_TEST, encoding="utf-8")
    executor_calls, reviewer_calls, commands, adapters, review_observations = [], [], [], [], []
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    budget_ids = []
    original_task_run = task_module._run

    def observe_worker(input_data, context):
        # Every role and resumed Assignment must use the same parent budget.
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        budget_ids.append(ledger)
        return original_task_run(input_data, context)

    class Executor(ModelAdapter):
        def next(self, messages, tools_override=None, **_kwargs):
            executor_calls.append(deepcopy(messages))
            number = len(executor_calls)
            if number == 1:
                return _tool("edit_file", {"path": "candidate.py", "old": INITIAL, "new": PARTIAL}, "edit-partial")
            if number == 3 and repair:
                return _tool("executor_test", {"path": "tests/test_eval_regression.py", "code": COMPLETE_TEST}, "test-missing-requirement")
            if number == (4 if repair else 3):
                return _tool("edit_file", {"path": "candidate.py", "old": PARTIAL,
                                          "new": COMPLETE if repair else PARTIAL + "# reviewed, but still incomplete\n"}, "repair-candidate")
            # A live owner may offer another repair on the same budget. The
            # negative adapter explicitly stops instead of failing by accident
            # because an old one-repair fixture ran out of scripted responses.
            blocked = not repair and number == 5
            assert number in ({2, 5} if repair else {2, 4, 5}), "Unexpected additional Executor request"
            submit = next(tool for tool in tools_override.list() if tool.name == "executor_submit")
            count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
            return _tool("executor_submit", {
                "status": "blocked" if blocked else "completed",
                "steps": [{"step": i, "status": "failed" if blocked else "completed"} for i in range(1, count + 1)],
                "deviations": [], "blockers": [FINDING] if blocked else [],
            }, f"submit-{number}")

    class Reviewer(ModelAdapter):
        def __init__(self):
            self.calls = 0

        def next(self, messages, **_kwargs):
            self.calls += 1
            reviewer_calls.append(deepcopy(messages))
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[
                    {"id": "review-source", "toolName": "read_file", "input": {"path": "candidate.py"}},
                    {"id": "review-tests", "toolName": "read_file", "input": {"path": "tests/test_eval_regression.py"}},
                ], usage=ModelUsage(input_tokens=100, output_tokens=10, source="provider"))
            if self.calls == 2:
                prompt = next(str(item.get("content", "")) for item in messages
                              if item.get("role") == "user"
                              and "\n\nASSIGNMENT:\n" in str(item.get("content", "")))
                assignment = _prompt_json(prompt, "ASSIGNMENT")
                # Inspect real tool observations, not a canned pass/fail result.
                source = next(item["content"] for item in reversed(messages)
                              if item.get("role") == "tool_result" and item.get("toolUseId") == "review-source")
                tests = next(item["content"] for item in reversed(messages)
                             if item.get("role") == "tool_result" and item.get("toolUseId") == "review-tests")
                covered = "if value is None:" in source and "assert normalize_name(None) == ''" in tests
                review_observations.append({"source": source, "tests": tests, "covered": covered,
                                            "parent": target.read_text(encoding="utf-8")})
                refs = list(assignment["inputRefs"])
                result = {
                    "resultVersion": 1, "assignmentId": assignment["assignmentId"],
                    "patchDigest": _review_patch_digest(prompt),
                    "verdict": "approved" if covered else "changes_required",
                    "criteria": [
                        {"criterionId": "trim", "status": "satisfied", "evidenceRefs": refs},
                        {"criterionId": "none", "status": "satisfied" if covered else "failed", "evidenceRefs": refs},
                    ],
                    "blockingFindings": [] if covered else [{"id": "missing_none_case", "severity": "high", "statement": FINDING, "evidenceRefs": refs}],
                    "warnings": [], "requestedChecks": [], "confidence": "high",
                }
                all_content = "\n".join(str(item.get("content", "")) for item in messages)
                key = re.search(r"Result channel \(legacy\): `([^`]+)`", all_content)
                assert key is not None
                return _tool("subagent_note_write", {"key": key.group(1), "content": json.dumps(result)}, "review-verdict")
            assert self.calls == 3
            return AgentStep(type="assistant", content="<final> Reviewed both public requirements.", kind="final",
                             usage=ModelUsage(input_tokens=100, output_tokens=10, source="provider"))

    model = Executor()

    def create_model(_runtime, agent_type, **_kwargs):
        adapter = model if agent_type == "general" else Reviewer()
        adapters.append((agent_type, adapter))
        return adapter

    def command_runner(_argv, **kwargs):
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)
        commands.append({"exit": completed.returncode, "parent": target.read_text(encoding="utf-8")})
        return completed

    monkeypatch.setattr(task_module, "_run", observe_worker)
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", create_model)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k:
                        ToolRegistry([edit_file_tool, read_file_tool, subagent_note_write_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    request = _request(risk="low", complex=False)
    request["scope"]["writePaths"] = ["candidate.py", "tests/test_eval_regression.py"]
    request["criteria"] = [
        {"id": "trim", "kind": "behavior", "text": "Strip surrounding whitespace from a string."},
        {"id": "none", "kind": "behavior", "text": "Return the empty string when the input is None."},
    ]
    request["verificationChecks"][0]["criterionIds"] = ["trim", "none"]
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    journal = RunJournal(tmp_path, data_dir=data_dir) if durable else None
    run_scope = (observe_run(workspace=tmp_path, source="headless", title="completion coverage",
                             journal_factory=lambda _resolved: journal)
                 if durable else nullcontext(None))
    with run_scope as observation:
        result = task_module.task_tool.run({
            "description": "public requirement coverage regression", "prompt": TASK,
            "agent_type": "workflow", "workflow_request": request,
        }, ToolContext(cwd=str(tmp_path), _runtime={"model": "fixture"},
                       _agent_budget=budget, _workflow_commit_audit_waived=not durable,
                       _central_workflow_v2_enforced=durable, _event_sink=observation))
        run_id = observation.run_id if durable else None

    assert commands and [entry["exit"] for entry in commands[:2]] == [0, 0], commands
    assert len(review_observations) == 2, (
        "A green partial candidate bypassed semantic review or its rejection was not repaired/reviewed. " + result.output
    )
    assert review_observations[0]["covered"] is False
    assert len(executor_calls) == 5, result.output
    assert review_observations[1]["covered"] is repair
    assert [kind for kind, _ in adapters].count("general") == 1, "Repair must retain the same Executor adapter."
    resumed = executor_calls[2]
    assert FINDING in str(resumed), "The existing Executor must receive the actual review finding."
    assert any(message.get("toolUseId") == "edit-partial" for message in resumed), "Repair lost prior tool history."
    assert result.ok is repair, result.output
    assert target.read_text(encoding="utf-8") == (COMPLETE if repair else INITIAL)
    assert test_file.read_text(encoding="utf-8") == (COMPLETE_TEST if repair else PUBLIC_TEST)
    assert all(entry["parent"] == INITIAL for entry in commands + review_observations), "Rejected bytes reached the parent."
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"] is repair
    assert all(entry is budget for entry in budget_ids), "A role obtained a fresh budget."
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000
    assert budget.snapshot().used_model_calls == len(executor_calls) + len(reviewer_calls)
    if repair:
        assert [entry["exit"] for entry in commands] == [0, 0, 1, 0, 0], "The missing test must fail before the repair and pass afterwards."
    if durable:
        workflow_id = trace["workflowId"]
        manifest = next(data_dir.rglob(f"scheduler-workflows/{workflow_id}/manifest.json"))
        assert (manifest.parent / "commit-ready.json").is_file()
        audit_store = SchedulerAuditJournal(manifest.parents[2])
        audit = audit_store.get(workflow_id)
        ready = audit_store.get_commit_ready(workflow_id)
        assert audit is not None and ready is not None
        assert audit.trace == trace and audit.trace["committed"] is True
        assert any(event["outcome"] == "repairing" for event in audit.trace["transitions"])
        reloaded = RunJournal(tmp_path, data_dir=data_dir)
        assert reloaded.get_scheduler_audit(run_id, workflow_id) == audit
        assert reloaded.get_scheduler_commit_ready(run_id, workflow_id) == ready
        sessions = sorted([
            event.payload
            for child in reloaded.list_subagent_runs(run_id)
            for event in reloaded.list_subagent_events(run_id, child.subagent_id)
            if event.type == "execution.executor_session"
        ], key=lambda item: item["resumed"])
        assert len(sessions) == 2
        assert sessions[0]["sessionId"] == sessions[1]["sessionId"]
        assert sessions[0]["resumed"] is False and sessions[0]["modelReused"] is False
        assert sessions[0]["priorMessageCount"] == 0
        assert sessions[1]["resumed"] is True and sessions[1]["modelReused"] is True
        assert sessions[1]["priorMessageCount"] > 0
