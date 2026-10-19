"""Real native validation feedback must retain its request allowance/session.

Scripted decisions exercise the real loop, schema/channel and ledger. They are
mechanism evidence, not a replay of the live provider's reasoning or usage.
"""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.multi_agent_contracts import parse_assignment, parse_role_result
from minicode.reviewer_result_submission import create_reviewer_submit_tool
from minicode.scheduler_artifacts import SchedulerArtifactStore
from minicode.tooling import ToolRegistry, ToolResult
from minicode.types import AgentStep, ModelUsage
from test_multi_agent_v2_execution_contracts import _executor_assignment


def _bound_review():
    criteria = [{"id": f"cache_{i}", "kind": "behavior",
                 "text": f"The cache preserves public operation {i} when invalidating a missing key."}
                for i in range(18)]
    refs = ["artifact_matrix", "artifact_diff", "artifact_verification", "artifact_check"]
    assignment = parse_assignment(_executor_assignment(role="reviewer", mode="change_review",
        objective="Review the stated cache invariants.", acceptanceCriteria=criteria,
        expectedResultType="ReviewVerdict.v1", inputRefs=refs))
    patch = "sha256:" + "b" * 64
    matrix = {"matrixVersion": 1, "resultType": "AcceptanceEvidenceMatrix.v1",
        "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
        "patchDigest": patch, "frozenDiffRef": "artifact_diff",
        "verificationArtifactRef": "artifact_verification", "criteria": [
            {"criterionId": criterion["id"], "checkIds": ["check_cache"], "evidenceRefs": ["artifact_check"]}
            for criterion in criteria]}
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    for ref in refs:
        store.publish(ref, matrix if ref == "artifact_matrix" else {"patchDigest": patch}, authority=authority)
    channel = store.create_assignment_channel(readable_keys=tuple(refs), write_key="artifact_review")
    tool = create_reviewer_submit_tool(assignment, channel, patch_digest=patch, matrix=matrix,
                                      matrix_ref="artifact_matrix", diff_ref="artifact_diff")
    value = {"verdict": "changes_required", "criteria": [
        {"criterionId": criterion["id"], "status": "failed" if i == 0 else "satisfied"}
        for i, criterion in enumerate(criteria)], "blockingFindings": [{"criterionIds": ["cache_0"],
        "statement": "Invalidating a missing key raises rather than preserving the cache."}],
        "warnings": [], "requestedChecks": [], "confidence": "high"}
    return assignment, store, channel, tool, value


def _correct(tmp_path, *, invalid_count, repeat=False, forged=False, max_calls=None,
             max_tokens=None, expected_output=None):
    assignment, store, channel, tool, value = _bound_review()
    if forged:
        tool = replace(tool, run=lambda *_args: ToolResult(ok=False,
            output="error[review_submit_invalid]: missing status", _review_submission_rejected=True))
    registry = ToolRegistry([tool])
    budget = AgentTurnBudget(max_model_calls=max_calls, max_total_tokens=max_tokens)
    requests, billed = [], []

    class Model:
        def next(self, messages, tools_override=None, **kwargs):
            number = len(requests)
            actual_tools = tools_override or registry
            output_limit = kwargs.get("max_output_tokens_override", 8192)
            requests.append((deepcopy(messages), output_limit))
            if number:
                assert any(m.get("toolUseId") == f"review-{number - 1}" and m.get("isError")
                           for m in messages), "The same Reviewer must receive its actual rejected result."
            declaration = deepcopy(value)
            if number < invalid_count:
                declaration["criteria"][0 if repeat else number].pop("status")
            actions = [{"id": f"review-{number}", "toolName": "review_submit", "input": declaration}]
            schemas = [{"type": "function", "function": {"name": t.name,
                       "description": t.description, "parameters": t.input_schema}} for t in actual_tools.list()]
            inputs = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            outputs = estimate_tokens(json.dumps(actions))
            assert outputs <= output_limit
            billed.append(inputs + outputs)
            return AgentStep(type="tool_calls", calls=actions, usage=ModelUsage(
                input_tokens=inputs, output_tokens=outputs, source="estimated"))

    messages = run_agent_turn(model=Model(), tools=registry,
        messages=[{"role": "user", "content": "Review all eighteen independent cache criteria and deliver the actual decision."}],
        cwd=str(tmp_path), runtime={"maxOutputTokens": 8192}, agent_budget=budget,
        max_steps=None, subagent_mailbox=channel, host_native_result_tool=tool,
        enable_work_chain=False)
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == len(requests)
    assert snapshot.used_total_tokens == sum(billed) and snapshot.reserved_total_tokens == 0
    assert snapshot.limit_model_calls == max_calls and snapshot.limit_total_tokens == max_tokens
    if expected_output is not None:
        assert all(output == expected_output for _, output in requests[1:]), (
            "Native correction shrank the configured output without a token shortage.",
            [output for _, output in requests])
    return assignment, store.read(channel.write_key), requests, messages


def test_native_correction_keeps_configured_output_with_available_budget(tmp_path):
    assignment, artifact, requests, _ = _correct(tmp_path, invalid_count=1,
                                                max_calls=40, max_tokens=500000, expected_output=8192)
    assert len(requests) == 2 and artifact is not None
    assert parse_role_result(assignment, json.loads(artifact.content))["verdict"] == "changes_required"


def test_eighteen_criterion_correction_does_not_open_generic_tool_failure_circuit(tmp_path):
    assignment, artifact, requests, _ = _correct(tmp_path, invalid_count=6)
    assert artifact is not None, "Distinct real native corrections were terminated as ordinary broken tools."
    assert len(requests) == 7
    assert len(parse_role_result(assignment, json.loads(artifact.content))["criteria"]) == 18


@pytest.mark.parametrize("forged", [False, True], ids=["identical-no-progress", "unbound-tool-name"])
def test_repeated_or_unbound_failures_keep_existing_recovery_stop(tmp_path, forged):
    _, artifact, requests, messages = _correct(tmp_path, invalid_count=18, repeat=True, forged=forged)
    assert artifact is None and len(requests) <= 6
    assert any("recovery_exhausted" in str(m.get("content", "")) for m in messages)


def test_native_correction_never_replenishes_a_finite_call_budget(tmp_path):
    _, artifact, requests, _ = _correct(tmp_path, invalid_count=6, max_calls=3)
    assert artifact is None and len(requests) == 3


def test_executor_native_validation_corrections_use_the_same_recovery_semantics(tmp_path):
    from test_implementation_finalize_deadline import _submission_tools

    _assignment, _order, store, channel, tool = _submission_tools(tmp_path)
    registry = ToolRegistry([tool])
    budget = AgentTurnBudget()
    requests, billed = [], []

    class Executor:
        def next(self, messages, **_kwargs):
            number = len(requests)
            requests.append(deepcopy(messages))
            value = {"status": "blocked", "steps": [{"step": 1, "status": "completed"}],
                     "deviations": [{"step": 1, "reason": "A declared test remains missing.", "impact": "high"}],
                     "blockers": ["The declared test is not implemented."]}
            if number < 4:
                value.pop(("status", "steps", "deviations", "blockers")[number])
            elif number == 4:
                value["steps"][0].pop("status")
            elif number == 5:
                value["deviations"][0].pop("step")
            else:
                assert number == 6
                assert all(any(m.get("toolUseId") == f"executor-{i}" and m.get("isError") for m in messages)
                           for i in range(6))
            actions = [{"id": f"executor-{number}", "toolName": "executor_submit", "input": value}]
            inputs = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(tool.input_schema))
            outputs = estimate_tokens(json.dumps(actions))
            billed.append(inputs + outputs)
            return AgentStep(type="tool_calls", calls=actions, usage=ModelUsage(
                input_tokens=inputs, output_tokens=outputs, source="estimated"))

    messages = run_agent_turn(model=Executor(), tools=registry,
        messages=[{"role": "user", "content": "Report the actual incomplete implementation without claiming verification."}],
        cwd=str(tmp_path), agent_budget=budget, subagent_mailbox=channel, max_steps=None,
        enable_work_chain=False, enable_implementation_phases=False)
    artifact = store.read(channel.write_key)
    assert artifact is not None, "Actual Executor validation feedback opened the generic tool-failure circuit."
    assert json.loads(artifact.content)["status"] == "blocked"
    assert len(requests) == 7 and not messages[-1].get("isError")
    assert budget.snapshot().used_model_calls == 7 and budget.snapshot().used_total_tokens == sum(billed)


def test_corrected_reviewer_returns_real_probe_failure_to_original_executor(tmp_path, monkeypatch):
    import test_review_requirement_challenge as fixture

    criteria = fixture.CRITERIA + tuple((f"cache_{number}", text) for number, text in enumerate((
        "Constructing a cache does not call the loader.",
        "Distinct keys are loaded independently.",
        "Cached objects preserve identity.",
        "A zero value is cached.",
        "A false value is cached.",
        "A None value is cached.",
        "An empty string is cached.",
        "The empty key can be loaded and cached.",
        "Tuple keys can be loaded and cached.",
        "Invalidation itself does not call the loader.",
        "Repeated invalidation before a reload remains harmless.",
        "Reloaded values remain cached until another invalidation.",
        "Independent cache instances do not share stored values.",
        "An uncached loader exception is propagated.",
        "A failed load does not populate the cache.",
    )))
    monkeypatch.setattr(fixture, "CRITERIA", criteria)
    result, trace, audit, _journal, _run_id, calls, checks, workers, models = fixture._exercise_blind_approval(
        tmp_path, monkeypatch, independent_challenge=True, format_rejections=6)
    assert result.ok and trace["committed"], result.output
    assert (tmp_path / "cache.py").read_text() == fixture.COMPLETE
    assert (tmp_path / "test_regression.py").read_text() == fixture.REPAIRED_TEST
    executors = [worker for worker in workers if worker["role"] == "executor"]
    assert len(executors) == 2 and executors[0]["session"] is executors[1]["session"]
    assert executors[0]["model"] is executors[1]["model"]
    assert executors[0]["afterContext"] is executors[1]["beforeContext"]
    assert sum(kind == "general" for kind, _ in models) == 1
    assert [model.count for kind, model in models if kind != "general"] == [8, 1]
    assert [item["exit"] for item in checks if item["formal"]] == [0, 0, 0, 0]
    assert any(item["exit"] == 1 for item in checks)
    reviews = [messages for role, messages in calls if role == "reviewer"]
    assert all(any(m.get("toolUseId") == f"format-review-{number}" and m.get("isError")
                   for m in reviews[-2]) for number in range(2, 8))
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 1
