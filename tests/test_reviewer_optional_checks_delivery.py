"""Native review correction reaches real commit through the same Reviewer.

Model choices are scripted and billed from actual messages and schemas. File
tools, subprocess pytest, native validation, audit and promotion are real. Both
versions get the same 500k/40/1800 shared envelope and two-call Reviewer slice.
"""

import json
import sys
import time
from dataclasses import replace

from test_workflow_v2_structured_routing import _request

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.agent_runtime import final_response_text
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolRegistry, ToolResult
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget

OMITTED = "omitted"


def _exercise(tmp_path, monkeypatch, *, second_checks=OMITTED):
    target = tmp_path / "allowed.txt"
    target.write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_updated():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    request = _request(risk="low", complex=False)
    request["verificationChecks"][0]["argv"] = ["python", "-m", "pytest", "-q"]
    budget = AgentTurnBudget(max_total_tokens=500000, max_model_calls=40)
    deadline = time.monotonic() + 1800
    roles, checks, calls, billed, submissions, receipts, acknowledgements, reviewers = [], [], [], [], [], [], [], []

    def response(role, messages, registry, actions=None, text=""):
        schemas = [{"type": "function", "function": {"name": tool.name, "description": tool.description,
            "parameters": tool.input_schema}} for tool in registry.list()]
        usage = ModelUsage(input_tokens=sum(estimate_message_tokens(message) for message in messages)
            + estimate_tokens(json.dumps(schemas)), output_tokens=estimate_tokens(json.dumps(actions) if actions else text),
            source="estimated")
        calls.append(role)
        billed.append(usage.input_tokens + usage.output_tokens)
        return AgentStep(type="tool_calls", calls=actions, usage=usage) if actions else AgentStep(type="assistant", content=text, usage=usage)

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == "before", "Verification must run before parent promotion"
            result = super().run(**kwargs)
            checks.extend(check["exitCode"] for check in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    def worker(data, context):
        role = context._logical_agent_role
        roles.append(role)
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        if role == "executor":
            submit = data["_executor_submit_tool"]
            registry = data["_executor_check_capability"].bind_registry(ToolRegistry([edit_file_tool, submit]))
            result = registry.execute("edit_file", {"path": "allowed.txt", "old": "before", "new": "after"}, context)
            assert result.ok, result.output
            count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
            result = registry.execute("executor_submit", {"status": "completed", "steps": [
                {"step": i, "status": "completed"} for i in range(1, count + 1)
            ], "deviations": [], "blockers": []}, context)
            assert result.ok, result.output
            return result

        assert role == "reviewer" and checks == [0, 0]
        if reviewers:
            # A rejected second declaration may trigger the scheduler's normal
            # stage retry. It has no additional scripted model budget here.
            return ToolResult(ok=False, output="Reviewer two-call slice exhausted.")
        submit = data["_review_submit_tool"]
        channel = context._subagent_mailbox
        registry = ToolRegistry([submit])
        before = channel._store.read(channel.write_key)
        assert before is None

        class Reviewer:
            count = 0

            def next(self, messages, tools_override=None, **_kwargs):
                self.count += 1
                assert self.count <= 2, "The same Reviewer has only its original two-call slice"
                tools = registry if tools_override is None else tools_override
                assert tools.list_all() == ["review_submit"]
                value = {"verdict": "approved", "criteria": [{"criterionId": "ac1", "status": "satisfied"}],
                         "blockingFindings": [], "warnings": [], "confidence": "high"}
                if self.count == 1:
                    value["requestedChecks"] = ["Consider another optional coverage example."]
                else:
                    assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "review-1"
                               and m.get("isError") and "requested checks" in m.get("content", "") for m in messages)
                    assert any(m.get("role") == "assistant_tool_call" and m.get("toolUseId") == "review-1" for m in messages)
                    assert channel._store.read(channel.write_key) is None
                    value["observations"] = [{"criterionIds": ["ac1"],
                        "statement": "A future optional coverage example could be useful."}]
                    if second_checks != OMITTED:
                        value["requestedChecks"] = second_checks
                return response("reviewer", messages, tools, actions=[
                    {"id": f"review-{self.count}", "toolName": "review_submit", "input": value}])

        model = Reviewer()
        reviewers.append((model, channel))
        seen = run_agent_turn(model=model, tools=registry, messages=[{"role": "user", "content": data["prompt"]}],
            cwd=context.cwd, runtime={"maxOutputTokens": 4096}, enable_work_chain=False,
            max_steps=0, max_steps_soft_landing=True, agent_depth=1,
            agent_budget=_WorkerCallBudget(context._agent_budget, "review-correction", max_calls=2, preserve_calls=0),
            subagent_mailbox=channel, host_native_result_tool=submit,
            deadline_monotonic=context._task_deadline_monotonic, event_sink=context._event_sink)
        submissions.extend(m for m in seen if m.get("role") == "tool_result" and m.get("toolName") == "review_submit")
        artifact = channel._store.read(channel.write_key)
        if artifact is not None:
            assert json.loads(artifact.content)["requestedChecks"] == []
        return ToolResult(ok=artifact is not None, output="Reviewer native result received." if artifact else "Reviewer result absent after two requests.")

    monkeypatch.setattr(task_module, "_run", worker)
    original_task = task_module.task_tool.run

    def workflow(data, context):
        result = original_task(data, context)
        receipts.append(result)
        previous = result._delivery_ack

        def acknowledge():
            if previous:
                previous()
            acknowledgements.append(True)

        return replace(result, _delivery_ack=acknowledge)

    tools = ToolRegistry([replace(task_module.task_tool, run=workflow)])

    class Parent:
        count = 0

        def next(self, messages, **_kwargs):
            self.count += 1
            assert self.count <= 2
            if self.count == 1:
                return response("parent", messages, tools, actions=[{"id": "workflow", "toolName": "task", "input": {
                    "description": "Update target", "agent_type": "workflow", "prompt": "Change allowed.txt from before to after.",
                    "workflow_request": request}}])
            assert receipts and any(m.get("role") == "tool_result" and m.get("toolUseId") == "workflow" for m in messages)
            return response("parent", messages, tools, text=("The workflow change is verified, reviewed and committed."
                            if receipts[0].ok else "The review did not finish; no workflow changes were committed."))

    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="optional requested checks",
                     journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir)) as observation:
        messages = run_agent_turn(model=Parent(), tools=tools,
            messages=[{"role": "user", "content": "Update the target and deliver its verified result."}],
            cwd=str(tmp_path), runtime={"model": "local-fixture"}, agent_budget=budget,
            deadline_monotonic=deadline, event_sink=observation, enable_work_chain=False,
            enable_bounded_completion=True, enable_implementation_phases=False, max_steps=40)
        run_id = observation.run_id
    assert roles[:2] == ["executor", "reviewer"] and all(role == "reviewer" for role in roles[2:])
    assert len(reviewers) == 1 and reviewers[0][0].count == 2
    assert calls == ["parent", "reviewer", "reviewer", "parent"]
    snapshot = budget.snapshot()
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls) == (500000, 40)
    assert snapshot.used_model_calls == 4 and snapshot.used_total_tokens == sum(billed)
    assert snapshot.reserved_total_tokens == 0 and snapshot.used_total_tokens < 500000
    assert len(submissions) == 2 and submissions[0]["isError"] is True
    trace = extract_scheduler_trace(receipts[0].output)
    assert trace is not None
    audit = RunJournal(tmp_path, data_dir=data_dir).get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None
    return messages, receipts[0], submissions, target, audit, acknowledgements


def test_same_reviewer_can_omit_empty_requested_checks_and_deliver_verified_workflow(tmp_path, monkeypatch):
    messages, result, submissions, target, audit, ack = _exercise(tmp_path, monkeypatch)
    assert submissions[1]["isError"] is False, submissions[1]["content"]
    assert result.ok and result._must_deliver and result._continue_parent
    assert audit.trace["committed"] and target.read_text() == "after" and ack == [True]
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    review = next(document for document in documents if document.get("verdict") == "approved")
    assert review["verdict"] == "approved" and review["requestedChecks"] == []
    assert review["warnings"][0]["severity"] == "low" and "optional coverage" in review["warnings"][0]["statement"]
    assert "verified, reviewed and committed" in final_response_text(messages)
