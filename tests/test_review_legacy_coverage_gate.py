"""A legacy ReviewVerdict may name a coverage document; it cannot mint one.

Real task/file/pytest/native Executor/legacy Reviewer/promotion paths, with only
model decisions scripted. This test never reads a real task or private judge.
"""

import json
import re
import sys
from pathlib import Path

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
from minicode.tools.subagent_notes import subagent_note_write_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_workspace import FrozenWorkflowChange

INITIAL = "def value():\n    return 0\n"
REPLACEMENT = "def value():\n    return 3\n"
TEST = "from candidate import value\ndef test_value():\n    assert value() == 3\n"


def test_legacy_approved_with_named_but_absent_coverage_is_rejected_before_promotion(tmp_path, monkeypatch):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_regression.py").write_text(TEST)
    checks, calls, billed, promotions, submissions, feedback = [], [], [], [], [], []
    budget = AgentTurnBudget()
    original_promote = FrozenWorkflowChange.promote

    def observe_promote(self, *args, **kwargs):
        promotions.append(target.read_text())
        return original_promote(self, *args, **kwargs)

    def response(messages, tools, *, actions=None, content=None):
        schemas = [{"type": "function", "function": {"name": tool.name,
                    "description": tool.description, "parameters": tool.input_schema}}
                   for tool in tools.list()]
        inputs = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
        outputs = estimate_tokens(json.dumps(actions) if actions is not None else content)
        billed.append(inputs + outputs)
        usage = ModelUsage(input_tokens=inputs, output_tokens=outputs, source="estimated")
        return (AgentStep(type="tool_calls", calls=actions, usage=usage) if actions is not None else
                AgentStep(type="assistant", content=content, kind="final", usage=usage))

    class Model:
        def __init__(self, role, tools):
            self.role, self.tools, self.count = role, tools, 0

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            tools = tools_override or self.tools
            calls.append(self.role)
            if self.role == "general":
                assert self.count <= 2
                if self.count == 1:
                    actions = [{"id": "edit-source", "toolName": "edit_file",
                                "input": {"path": "candidate.py", "old": INITIAL, "new": REPLACEMENT}}]
                else:
                    maximum = tools.find("executor_submit").input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                    actions = [{"id": "executor-submit", "toolName": "executor_submit", "input": {
                        "status": "completed", "steps": [{"step": i, "status": "completed"} for i in range(1, maximum + 1)],
                        "deviations": [], "blockers": []}}]
                return response(messages, tools, actions=actions)
            assert target.read_text() == INITIAL
            if self.count == 1:
                assert [item["exit"] for item in checks if item["formal"]] == [0]
                prompt = next(message["content"] for message in messages
                              if message.get("role") == "user" and "\n\nASSIGNMENT:\n" in message.get("content", ""))
                assignment = json.loads(re.search(r"ASSIGNMENT:\n([^\n]+)", prompt).group(1))
                digests = set(re.findall(r'"patchDigest": "(sha256:[0-9a-f]{64})"', prompt))
                assert len(digests) == 1
                key = re.search(r"Result channel \(legacy\): `([^`]+)`", "\n".join(message.get("content", "") for message in messages))
                assert key is not None
                assert "review_test" in {tool.name for tool in tools.list()}
                # Exact owner-bound name passes shape parsing. No host guard
                # has created this document because no probe was run.
                declaration = {"resultVersion": 1, "assignmentId": assignment["assignmentId"],
                    "patchDigest": digests.pop(), "verdict": "approved",
                    "criteria": [{"criterionId": "required_value", "status": "satisfied", "evidenceRefs": assignment["inputRefs"]}],
                    "blockingFindings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
                    "coverageArtifactRef": "artifact_review_coverage_" + assignment["assignmentId"]}
                submissions.append(declaration)
                return response(messages, tools, actions=[{"id": "legacy-approved", "toolName": "subagent_note_write",
                    "input": {"key": key.group(1), "content": json.dumps(declaration)}}])
            assert self.count == 2
            receipt = next(message for message in messages
                           if message.get("role") == "tool_result" and message.get("toolUseId") == "legacy-approved")
            feedback.append(receipt)
            assert not receipt.get("isError"), receipt
            return response(messages, tools, content="The review declaration was saved.")

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == INITIAL
            result = super().run(**kwargs)
            formal = getattr(kwargs["evidence_sink"], "__name__", "") == "per_check_sink"
            checks.extend({"exit": item["exitCode"], "formal": formal, "cwd": kwargs["cwd"]}
                          for item in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(FrozenWorkflowChange, "promote", observe_promote)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter",
                        lambda _runtime, role, **kwargs: Model(role, kwargs["tools"]))
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, subagent_note_write_tool]))
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.subagent_types._read_max_turns_override", lambda _name: None)
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_legacy_coverage_gate", "kind": "change", "risk": "low", "complex": False,
                    "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_regression.py"],
                  "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "required_value", "kind": "behavior", "text": "value() must return integer 3."}],
        "verificationChecks": [{"checkId": "regression", "kind": "tests",
                                "argv": ["python", "-m", "pytest", "-q", "test_regression.py"],
                                "criterionIds": ["required_value"]}]}
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="legacy approval coverage gate",
                     journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "return required value", "agent_type": "workflow",
            "prompt": "Return integer 3 and retain regression coverage.", "workflow_request": request},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"}, _agent_budget=budget,
                        _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert submissions and len(submissions) == len(feedback)
    assert [item["exit"] for item in checks if item["formal"]] == [0]
    assert target.read_text() == INITIAL, (
        "An approved legacy declaration naming a nonexistent coverage document reached the parent workspace. "
        f"promotions={len(promotions)}, terminalReason={trace['terminalReason']}"
    )
    assert promotions == []
    assert len(submissions) == 2, "The existing single Reviewer retry must also reject the absent document."
    assert not result.ok and not trace["committed"]
    assert trace["terminalReason"] == "review_result_invalid"
    assert (tmp_path / "test_regression.py").read_text() == TEST
    snapshot = budget.snapshot()
    assert snapshot.limit_total_tokens is None and snapshot.limit_model_calls is None and snapshot.limit_cost_usd is None
    assert snapshot.used_model_calls == len(calls) and snapshot.used_total_tokens == sum(billed)
    assert all(not Path(item["cwd"]).exists() for item in checks)
    journal = RunJournal(tmp_path, data_dir=data_dir)
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is None
