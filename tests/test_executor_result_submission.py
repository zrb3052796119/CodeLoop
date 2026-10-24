"""Semantic handoff cannot spend its capability on a repairable declaration."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.executor_result_submission import (
    ExecutorResultChannel,
    create_executor_submit_tool,
)
from minicode.multi_agent_contracts import (
    ContractValidationError,
    parse_assignment,
    parse_executor_declaration,
    parse_implementation_order,
)
from minicode.scheduler_artifacts import SchedulerArtifactError, SchedulerArtifactStore
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
from minicode.tools import task as task_module
from minicode.workflow_runner import _WorkerCallBudget
from test_multi_agent_v2_execution_contracts import (
    _executor_assignment,
    _implementation_order,
)
from test_workflow_v2_structured_routing import (
    _prompt_json,
    _request,
    _run,
    _verification_result,
    _write_analyst_result,
    _write_reviewer_result,
)


def _bound():
    assignment = parse_assignment(_executor_assignment())
    order = parse_implementation_order(assignment, _implementation_order())
    store = SchedulerArtifactStore(scheduler_authority=object())
    channel = ExecutorResultChannel(
        store.create_assignment_channel(
            readable_keys=(), write_key="artifact_executor_result"
        ),
        assignment,
        order,
    )
    tool = create_executor_submit_tool(assignment, order, channel)
    return assignment, order, store, channel, tool


def _submission():
    return {
        "status": "completed",
        "steps": [
            {"step": 1, "status": "completed"},
            {"step": 2, "status": "completed"},
        ],
        "deviations": [],
        "blockers": [],
    }


def _execute(tool, channel, value):
    return ToolRegistry([tool]).execute(
        tool.name, value, ToolContext(_subagent_mailbox=channel)
    )


def test_received_semantics_are_host_bound_and_are_not_a_promotable_changeset():
    assignment, order, store, channel, tool = _bound()
    result = _execute(tool, channel, _submission())
    assert result.ok, result.output
    assert result._implementation_submitted is True
    assert json.loads(result.output) == {
        "declarationReceived": True,
        "verification": "pending",
        "writeback": "pending",
    }
    document = json.loads(store.read(channel.write_key).content)
    assert document["assignmentId"] == assignment.assignment_id
    assert document["orderId"] == order.order_id
    assert document["resultVersion"] == 1
    assert document["selfChecks"] == []
    assert [step["artifactRefs"] for step in document["stepResults"]] == [[], []]
    assert [step["stepId"] for step in document["stepResults"]] == [
        step.step_id for step in order.steps
    ]
    assert "changeSet" not in document
    with pytest.raises(ContractValidationError, match="scheduler ChangeSet"):
        parse_executor_declaration(
            assignment, document, implementation_order=order, scheduler_change_set=None
        )
    assert not _execute(tool, channel, _submission()).ok


def test_legacy_note_submission_stops_only_a_strict_executor_channel():
    from minicode.subagent_mailbox import SubagentMailbox
    from minicode.tools.subagent_notes import subagent_note_write_tool
    assignment, order, _store, channel, _tool = _bound()
    declaration = {
        "resultVersion": 1, "assignmentId": assignment.assignment_id, "orderId": order.order_id,
        "status": "completed", "stepResults": [
            {"stepId": step.step_id, "status": "completed", "artifactRefs": []} for step in order.steps
        ], "deviations": [], "selfChecks": [], "blockers": [],
    }
    result = ToolRegistry([subagent_note_write_tool]).execute("subagent_note_write", {
        "key": channel.write_key, "content": json.dumps(declaration),
    }, ToolContext(_subagent_mailbox=channel))
    assert result.ok and result._implementation_submitted
    ordinary = ToolRegistry([subagent_note_write_tool]).execute("subagent_note_write", {
        "key": "ordinary", "content": "coordination note",
    }, ToolContext(_subagent_mailbox=SubagentMailbox()))
    assert ordinary.ok and not ordinary._implementation_submitted


def test_real_task_loop_submits_once_then_workflow_freezes_verifies_and_writes_back(tmp_path, monkeypatch):
    from minicode.types import AgentStep, ModelAdapter
    from minicode import subagent_model_routing
    (tmp_path / "allowed.txt").write_text("before")
    class Implementing(ModelAdapter):
        def __init__(self):
            self.calls = 0
        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            assert self.calls <= 2, "no model closing request after strict submission"
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "implementation", "toolName": "edit_file",
                    "input": {"path": "allowed.txt", "old": "before", "new": "after"},
                }])
            submit = next(tool for tool in tools_override.list() if tool.name == "executor_submit")
            count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
            return AgentStep(type="tool_calls", calls=[{
                "id": "handoff", "toolName": "executor_submit", "input": {
                    "status": "completed", "steps": [{"step": i, "status": "completed"} for i in range(1, count+1)],
                    "deviations": [], "blockers": [],
                },
            }])
    model = Implementing()
    original_worker = task_module._run
    reviews = []

    def worker(input_data, context):
        # This test exercises submission and the Verifier, not Reviewer model
        # behavior (covered by test_workflow_completion_coverage).
        if context._logical_agent_role == "reviewer":
            assert (tmp_path / "allowed.txt").read_text() == "before"
            _write_reviewer_result(input_data, context)
            reviews.append(context._logical_agent_role)
            return ToolResult(ok=True, output="Reviewed the submitted candidate.")
        return original_worker(input_data, context)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k:model)
    monkeypatch.setattr(DeterministicVerifier, "run", lambda _self, **request:_verification_result(request))
    result = task_module.task_tool.run({
        "description": "real bounded child loop",
        "prompt": "Update allowed.txt. Please review the changes.",
        "agent_type": "workflow", "workflow_request": _request(risk="low", complex=False),
    }, ToolContext(cwd=str(tmp_path), _runtime={"model":"fixture"},
        _agent_budget=AgentTurnBudget(max_model_calls=40, max_total_tokens=500000),
        _workflow_commit_audit_waived=True))
    assert result.ok, result.output
    assert model.calls == 2
    assert reviews == ["reviewer"]
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert result._must_deliver


@pytest.mark.parametrize(
    "change",
    [
        {"assignmentId": "asg_forged"},
        {"orderId": "order_forged"},
        {"resultVersion": 2},
        {"changeSet": {}},
        {"selfChecks": []},
        {"status": "approved"},
        {"status": []},
        {"steps": []},
        {"steps": [{"step": 1, "status": "completed"}]},
        {
            "steps": [
                {"step": 1, "status": "completed"},
                {"step": 1, "status": "completed"},
            ]
        },
        {
            "steps": [
                {"step": True, "status": "completed"},
                {"step": 2, "status": "completed"},
            ]
        },
        {
            "steps": [
                {"step": "s_implement", "status": "completed"},
                {"step": 2, "status": "completed"},
            ]
        },
        {
            "steps": [
                {"step": 0, "status": "completed"},
                {"step": 2, "status": "completed"},
            ]
        },
        {
            "steps": [
                {"step": 1, "status": "completed"},
                {"step": 3, "status": "completed"},
            ]
        },
        {
            "steps": [
                {"step": 1, "status": "skipped"},
                {"step": 2, "status": "completed"},
            ]
        },
        {"blockers": ["still unfinished"]},
        {"blockers": "none"},
        {"deviations": [{"step": 1, "reason": "unresolved", "impact": "high"}]},
        {"deviations": [{"step": 1, "reason": "scope", "impact": "unknown"}]},
        {
            "deviations": [
                {"step": 1, "reason": "a", "impact": "low"},
                {"step": 1, "reason": "b", "impact": "low"},
            ]
        },
        {"deviations": [{"step": 3, "reason": "unknown", "impact": "low"}]},
        {"status": "blocked"},
        {"status": "failed"},
    ],
)
def test_bad_submission_can_be_corrected_without_spending_write_once(change):
    _assignment, _order, store, channel, tool = _bound()
    value = _submission()
    value.update(copy.deepcopy(change))
    rejected = _execute(tool, channel, value)
    assert not rejected.ok
    assert "tool_crashed" not in rejected.output
    assert rejected._implementation_submitted is False
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _submission()).ok


@pytest.mark.parametrize(
    ("missing", "extra", "expected_errors"),
    [
        (("blockers",), {}, ("missing required fields: blockers",)),
        ((), {"assignmentId": "forged"}, ("unknown fields: assignmentId",)),
        (
            ("blockers", "deviations"),
            {"blocker": []},
            (
                "missing required fields: deviations, blockers",
                "unknown fields: blocker",
            ),
        ),
    ],
)
def test_submission_reports_missing_and_unknown_fields_and_allows_correction(
    missing, extra, expected_errors
):
    _assignment, _order, store, channel, tool = _bound()
    value = _submission()
    for field in missing:
        value.pop(field)
    value.update(extra)

    rejected = _execute(tool, channel, value)

    assert not rejected.ok
    for expected in expected_errors:
        assert expected in rejected.output
        assert expected in channel.validation_errors[-1]
    if not missing:
        assert "missing required fields" not in rejected.output
    if not extra:
        assert "unknown fields" not in rejected.output
    assert not rejected._implementation_submitted
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _submission()).ok


def test_submission_requires_an_object_before_checking_fields():
    _assignment, _order, store, channel, tool = _bound()
    rejected = _execute(tool, channel, [])
    assert not rejected.ok
    assert "Executor submission must be an object" in rejected.output
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _submission()).ok


def test_non_completed_result_reports_all_step_statuses_and_concrete_blocker():
    assignment, order, store, channel, tool = _bound()
    value = _submission()
    value.update(
        status="blocked",
        steps=[{"step": 2, "status": "skipped"}, {"step": 1, "status": "completed"}],
        blockers=["The second source is unavailable."],
    )
    assert _execute(tool, channel, value).ok
    result = parse_executor_declaration(
        assignment,
        json.loads(store.read(channel.write_key).content),
        implementation_order=order,
        scheduler_change_set=None,
    )
    assert result["status"] == "blocked" and result["changeSet"] is None


def test_closed_and_cross_assignment_channels_remain_denied():
    assignment, order, store, channel, tool = _bound()
    _other_assignment, _other_order, other_store, other_channel, _other_tool = _bound()
    assert not _execute(tool, other_channel, _submission()).ok
    assert store.read(channel.write_key) is None
    assert other_store.read(other_channel.write_key) is None
    with pytest.raises(ValueError, match="another Assignment"):
        create_executor_submit_tool(
            replace(assignment, assignment_id="asg_other"), order, channel
        )
    channel.close()
    assert not _execute(tool, channel, _submission()).ok
    assert store.read(channel.write_key) is None


def test_legacy_note_path_also_preflights_semantics_before_channel_write():
    assignment, order, store, channel, tool = _bound()
    invalid = {
        "resultVersion": 1,
        "assignmentId": "asg_wrong",
        "orderId": order.order_id,
        "status": "completed",
        "stepResults": [],
        "deviations": [],
        "selfChecks": [],
        "blockers": [],
    }
    with pytest.raises(SchedulerArtifactError, match="assignmentId"):
        channel.write(channel.write_key, json.dumps(invalid), author="forged")
    assert store.read(channel.write_key) is None
    with pytest.raises(SchedulerArtifactError, match="exact write key"):
        channel.write("artifact_other", "{}", author="scheduler")
    with pytest.raises(SchedulerArtifactError):
        channel.read(channel.write_key)
    assert _execute(tool, channel, _submission()).ok


def test_production_executor_registry_receives_only_its_host_bound_submit_tool(
    tmp_path, monkeypatch
):
    _assignment, _order, store, channel, tool = _bound()
    seen = []

    def capture_turn(**kwargs):
        registry = kwargs["tools"]
        assert kwargs["implementation_analysis_complete"] is False
        assert registry.find("executor_submit") is tool
        result = registry.execute(
            "executor_submit",
            _submission(),
            ToolContext(
                cwd=str(tmp_path), _subagent_mailbox=kwargs["subagent_mailbox"]
            ),
        )
        assert result.ok, result.output
        seen.append(result._implementation_submitted)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "Declared the implementation."}]

    monkeypatch.setattr(task_module, "run_agent_turn", capture_turn)
    result = task_module.task_tool.run(
        {
            "description": "submit",
            "prompt": "Complete the locked Assignment.",
            "agent_type": "general",
            "_executor_submit_tool": tool,
            "_result_instruction": "Use executor_submit.",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_mailbox=channel,
            _logical_agent_role="executor",
            _logical_agent_workspace_root=str(tmp_path),
            _logical_agent_scope={
                "read_roots": (".",),
                "write_paths": (),
                "forbidden_paths": (".git",),
            },
        ),
    )
    assert result.ok, result.output
    assert seen == [True] and store.read(channel.write_key) is not None


@pytest.mark.parametrize("failure", ["success", "verification", "scope", "no_change"])
def test_submission_does_not_bypass_frozen_workspace_or_verifier(
    failure, tmp_path, monkeypatch
):
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    receipts = []
    reviews = []

    def worker(input_data, context):
        # Keep the fixture focused on submission/Verifier safety. The separate
        # completion-coverage regression uses a real Reviewer tool/model loop.
        if context._logical_agent_role == "reviewer":
            assert target.read_text(encoding="utf-8") == "before"
            _write_reviewer_result(input_data, context)
            reviews.append(context._logical_agent_role)
            return ToolResult(ok=True, output="Reviewed the submitted candidate.")
        order = _prompt_json(input_data["prompt"], "LOCKED IMPLEMENTATION ORDER")
        if failure != "no_change":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        if failure == "scope":
            Path(context.cwd, "outside.txt").write_text(
                "unauthorized", encoding="utf-8"
            )
        submit = input_data["_executor_submit_tool"]
        receipt = _execute(
            submit,
            context._subagent_mailbox,
            {
                "status": "completed",
                "steps": [
                    {"step": index, "status": "completed"}
                    for index, _step in enumerate(order["steps"], start=1)
                ],
                "deviations": [],
                "blockers": [],
            },
        )
        assert receipt.ok
        receipts.append(receipt)
        return ToolResult(ok=True, output="Declaration received.")

    def verifier(self, **request):
        if failure == "success":
            return _verification_result(request)
        if failure != "verification":
            raise AssertionError("invalid frozen bytes must not reach Verifier")
        evidence = []
        original_sink = request["evidence_sink"]
        result = _verification_result(
            {
                **request,
                "evidence_sink": lambda ref, document: evidence.append((ref, document)),
            }
        )
        result["status"] = "failed"
        result["checks"][0].update(status="failed", exitCode=1)
        ref, document = evidence[0]
        document.update(status="failed", exitCode=1)
        original_sink(ref, document)
        return result

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)
    result = _run(
        tmp_path, _request(risk="low", complex=False), monkeypatch,
        commit_audit_waived=True,
        prompt="Update allowed.txt. Please review the changes.",
    )
    assert receipts and all(receipt._implementation_submitted for receipt in receipts)
    assert result.ok is (failure == "success"), result.output
    assert reviews == (["reviewer"] if failure == "success" else [])
    assert target.read_text(encoding="utf-8") == (
        "after" if failure == "success" else "before"
    )
    assert not (tmp_path / "outside.txt").exists()


def test_frozen_analyst_handoff_skips_duplicate_executor_analysis(
    tmp_path, monkeypatch
):
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    roles = []

    def worker(input_data, context):
        role = context._logical_agent_role
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            assert context._implementation_analysis_complete is True
            # The flag is issued only alongside frozen Analyst report input.
            assignment = _prompt_json(input_data["prompt"], "EXECUTOR ASSIGNMENT")
            assert any("analyst_handoff" in ref for ref in assignment["inputRefs"])
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            order = _prompt_json(input_data["prompt"], "LOCKED IMPLEMENTATION ORDER")
            submission = {
                "status": "completed",
                "steps": [
                    {"step": index, "status": "completed"}
                    for index, _step in enumerate(order["steps"], start=1)
                ],
                "deviations": [],
                "blockers": [],
            }
            assert _execute(
                input_data["_executor_submit_tool"],
                context._subagent_mailbox,
                submission,
            ).ok
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="Worker result received.")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda _self, **request: _verification_result(request),
    )
    result = _run(
        tmp_path, _request(complex=True), monkeypatch, commit_audit_waived=True,
        prompt="Analyze first, then update allowed.txt. Please review the changes.",
    )
    assert result.ok, result.output
    assert roles == ["analyst", "executor", "reviewer"]
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"


def test_worker_atomic_admission_preserves_token_floor_without_new_allowance():
    budget = AgentTurnBudget(max_model_calls=5, max_total_tokens=500)
    worker = _WorkerCallBudget(budget, "executor", max_calls=4, preserve_calls=1)
    with pytest.raises(AgentBudgetExceeded, match="token budget"):
        worker.reserve_model_call(401, preserve_total_tokens=100)
    assert budget.snapshot().used_model_calls == 0
    reservation = worker.reserve_model_call(400, preserve_total_tokens=100)
    assert budget.snapshot().reserved_total_tokens == 400
    worker.record_model_call(input_tokens=400, output_tokens=0, reservation=reservation)
    assert budget.snapshot().limit_total_tokens == 500
    with pytest.raises(AgentBudgetExceeded, match="token budget"):
        worker.reserve_model_call(1, preserve_total_tokens=100)
