"""Shared calls are allocated, never increased, by structured workflow dispatch."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.multi_agent_contracts import parse_workflow_request
from minicode.subagent_types import configured_max_turns, resolve_max_turns
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools import task as task_module
from minicode.workflow_runner import (
    _WorkerCallBudget,
    _build_worker_assignment,
    _executor_followup_calls,
)
from test_subagent_soft_landing import ScriptedModel, _counting_echo_tool, _echo_call
from test_workflow_v2_structured_routing import (
    _prompt_json,
    _request,
    _verification_result,
    _worker_role,
    _write_analyst_result,
    _write_reviewer_result,
)
from minicode.types import AgentStep


def _allocate(*, calls=40, used=1, reviewed=False, role="executor"):
    request = parse_workflow_request(
        _request(risk="medium" if reviewed else "low", complex=reviewed), legacy_task_id="task_budget"
    )
    budget = AgentTurnBudget(
        max_model_calls=calls, max_total_tokens=500_000, max_cost_usd="5"
    )
    for _ in range(used):
        budget.reserve_model_call()
    tail = _executor_followup_calls(request.task_profile) if role == "executor" else 1
    assignment = _build_worker_assignment(
        request,
        SimpleNamespace(workflow_id="wf_budget", assignment_id="asg_budget"),
        context=ToolContext(cwd=".", _task_timeout_seconds=600),
        agent_budget=budget,
        role=role,
        mode="implement" if role == "executor" else "change_review",
        objective="Update allowed.txt; preserve other files.",
        preserve_model_calls=tail,
    )
    return assignment, budget, tail


@pytest.mark.parametrize("reviewed,expected,tail", [(False, 29, 10), (True, 29, 10)])
def test_dynamic_executor_uses_remaining_calls_and_keeps_mandatory_tail(
    reviewed, expected, tail
):
    assignment, budget, reserved = _allocate(reviewed=reviewed)
    assert assignment.budget.max_model_calls == expected
    assert reserved == tail
    assert reserved == 8 + 1 + 1  # Reviewer work, Reviewer closing, parent delivery.
    assert assignment.budget.max_model_calls - 1 > 15
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500_000
    assert budget.snapshot().limit_cost_usd == "5"


@pytest.mark.parametrize("value,expected", [("1", 2), ("15", 16), ("199", 29)])
def test_valid_explicit_ceiling_keeps_original_work_plus_closing_semantics(
    monkeypatch, value, expected
):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", value)
    assignment, _, _ = _allocate()
    assert assignment.budget.max_model_calls == expected
    assert assignment.budget.max_model_calls - 1 <= int(value)


@pytest.mark.parametrize("raw", ["bad", "1.5", "-1"])
def test_invalid_override_is_rejected_instead_of_inventing_a_ceiling(monkeypatch, raw):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raw)
    with pytest.raises(ValueError):
        configured_max_turns("general")
    with pytest.raises(ValueError):
        resolve_max_turns("general")
    with pytest.raises(ValueError):
        _allocate()


@pytest.mark.parametrize("raw", ["0", ""])
def test_disabled_role_cap_still_obeys_explicit_shared_budget(monkeypatch, raw):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raw)
    assert configured_max_turns("general") is None
    assert resolve_max_turns("general") is None
    assert _allocate()[0].budget.max_model_calls == 29


def test_explicit_large_role_cap_has_no_arbitrary_two_hundred_ceiling(monkeypatch):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "201")
    assert configured_max_turns("general") == 201
    assert _allocate()[0].budget.max_model_calls == 29


def test_private_override_and_process_precedence_are_respected(monkeypatch):
    monkeypatch.setattr(
        "minicode.env_file.read_private_env_file",
        lambda *a, **k: {
            "MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS": "4",
        },
    )
    monkeypatch.delenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raising=False)
    assert configured_max_turns("general") == 4
    assert _allocate()[0].budget.max_model_calls == 5
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "6")
    assert _allocate()[0].budget.max_model_calls == 7
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "bad")
    with pytest.raises(ValueError):
        configured_max_turns("general")
    with pytest.raises(ValueError):
        _allocate()


@pytest.mark.parametrize("reviewed", [False, True])
def test_review_override_is_included_in_executor_tail(monkeypatch, reviewed):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_PLAN_MAX_TURNS", "3")
    assignment, _, tail = _allocate(reviewed=reviewed)
    assert tail == 5
    assert assignment.budget.max_model_calls == 34


def test_unbounded_shared_calls_do_not_acquire_a_default_executor_limit():
    assignment, budget, _ = _allocate(calls=None)
    assert budget.max_model_calls is None
    assert assignment.budget.max_model_calls is None


@pytest.mark.parametrize(
    "calls,expected", [(1, 0), (2, 0), (3, 0), (4, 0), (10, 0), (11, 0), (12, 1), (13, 2)]
)
def test_small_shared_allowance_is_not_rounded_up(calls, expected):
    assignment, budget, reserved = _allocate(calls=calls)
    assert assignment.budget.max_model_calls == expected
    assert reserved == 10
    assert budget.snapshot().limit_model_calls == calls
    assert budget.snapshot().used_model_calls == 1


def test_reviewer_assignment_declares_work_and_closing():
    assert _allocate(role="reviewer")[0].budget.max_model_calls == 9


def test_worker_guard_counts_retry_admissions_and_never_borrows_from_followup():
    shared = AgentTurnBudget(
        max_model_calls=10, max_total_tokens=500_000, max_cost_usd="5"
    )
    view = _WorkerCallBudget(shared, "worker", max_calls=3, preserve_calls=2)
    leaf = view.attribution_view("leaf")
    failed = leaf.reserve_model_call(11)
    leaf.fail_model_call(failed)
    for _ in range(2):
        lease = leaf.reserve_model_call(11)
        leaf.record_model_call(input_tokens=11, output_tokens=3, reservation=lease)
    with pytest.raises(AgentBudgetExceeded, match="worker model call budget"):
        leaf.reserve_model_call()
    assert shared.snapshot().used_model_calls == 3
    assert leaf.attributed_tokens() == (22, 6)
    assert shared.snapshot().used_total_tokens == 28
    assert shared.snapshot().reserved_total_tokens == 0
    # Another admitted consumer changes availability after Assignment issue.
    for _ in range(5):
        shared.reserve_model_call()
    fresh = _WorkerCallBudget(shared, "fresh", max_calls=9, preserve_calls=2)
    with pytest.raises(AgentBudgetExceeded, match="model call budget exhausted"):
        fresh.reserve_model_call()
    assert shared.snapshot().used_model_calls == 8
    shared.reserve_model_call()  # required downstream work remains possible
    shared.reserve_model_call()
    with pytest.raises(AgentBudgetExceeded):
        shared.reserve_model_call()


def test_parallel_admission_enforces_local_cap_on_one_shared_ledger():
    shared = AgentTurnBudget(max_model_calls=40)
    view = _WorkerCallBudget(shared, "worker", max_calls=7, preserve_calls=3)

    def admit(_):
        try:
            return view.reserve_model_call() is not None
        except AgentBudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(admit, range(20)))
    assert sum(accepted) == 7
    assert shared.snapshot().used_model_calls == 7


def test_real_loop_work_and_tool_disabled_closing_stay_within_declared_cap():
    shared = AgentTurnBudget(max_model_calls=5)
    view = _WorkerCallBudget(shared, "worker", max_calls=3, preserve_calls=2)
    counter = {"calls": 0}
    model = ScriptedModel(
        [
            _echo_call(1),
            _echo_call(2),
            AgentStep(type="assistant", content="Partial findings"),
        ]
    )
    result = run_agent_turn(
        model=model,
        tools=_counting_echo_tool(counter),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        max_steps=2,
        max_steps_soft_landing=True,
        agent_budget=view,
    )
    assert model.calls == 3
    assert counter["calls"] == 2
    assert result[-1]["_partial"] is True
    assert shared.snapshot().used_model_calls == 3


@pytest.mark.parametrize("reviewed", [False, True])
def test_public_workflow_assignment_and_actual_worker_loop_share_one_cap(
    tmp_path, monkeypatch, reviewed
):
    (tmp_path / "allowed.txt").write_text("before")
    budget = AgentTurnBudget(
        max_model_calls=40, max_total_tokens=500_000, max_cost_usd="5"
    )
    budget.reserve_model_call()
    observed = []
    current_worker = {}
    real_run = task_module._run

    def loop(**kwargs):
        inp, context, assignment = current_worker["active"]
        role = context._logical_agent_role
        assert kwargs["max_steps"] + 1 == assignment["budget"]["maxModelCalls"]
        assert kwargs["max_steps_soft_landing"] is True
        observed.append((role, kwargs["max_steps"]))
        lease = kwargs["agent_budget"].reserve_model_call()
        kwargs["agent_budget"].record_model_call(
            input_tokens=1, output_tokens=1, reservation=lease
        )
        if role == "analyst":
            _write_analyst_result(inp, context, prepare_change=True)
        elif role == "executor":
            assert "start with grep_files/read_file at those paths" in inp["prompt"]
            assert "report blocked instead of guessing" in inp["prompt"]
            (tmp_path / "allowed.txt").read_text()  # source is not modified yet
            from pathlib import Path

            Path(context.cwd, "allowed.txt").write_text("after")
            # Exercise the real bound check/submit capabilities. Writing a
            # completed note directly is no longer an authorized completion
            # path; this budget fixture still mocks command verification.
            from dataclasses import replace
            tool_context = replace(context, _subagent_mailbox=kwargs["subagent_mailbox"])
            tools = kwargs["tools"]
            checked = tools.execute("executor_check", {"check": 1}, tool_context)
            assert checked.ok, checked.output
            order = _prompt_json(inp["prompt"], "IMPLEMENTATION ORDER")
            submitted = tools.execute("executor_submit", {
                "status": "completed",
                "steps": [{"step": i, "status": "completed"}
                          for i in range(1, len(order["steps"]) + 1)],
                "deviations": [], "blockers": [],
            }, tool_context)
            assert submitted.ok, submitted.output
        else:
            _write_reviewer_result(inp, context)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "worker done"}]

    def worker(inp, context):
        role = _worker_role(inp)
        label = {
            "analyst": "ASSIGNMENT",
            "executor": "EXECUTOR ASSIGNMENT",
            "reviewer": "ASSIGNMENT",
        }[role]
        assignment = _prompt_json(inp["prompt"], label)
        assert (
            context._logical_agent_max_model_calls
            == assignment["budget"]["maxModelCalls"]
        )
        current_worker["active"] = inp, context, assignment
        return real_run(inp, context)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(task_module, "run_agent_turn", loop)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda _self, **request: _verification_result(request),
    )
    result = task_module.task_tool.run(
        {
            "description": "bounded change",
            "prompt": "Update allowed.txt.",
            "agent_type": "workflow",
            "workflow_request": _request(risk="medium" if reviewed else "low", complex=reviewed),
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_budget=budget,
            _workflow_commit_audit_waived=True,
        ),
    )
    assert result.ok, result.output
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert observed == (
        [("analyst", 5), ("executor", 27), ("reviewer", 8)]
        if reviewed
        else [("executor", 28), ("reviewer", 8)]
    )
    assert budget.max_model_calls == 40
    assert budget.max_total_tokens == 500_000
    assert budget.snapshot().used_model_calls == 1 + len(observed)
    assert budget.snapshot().used_total_tokens == 2 * len(observed)


def test_task_honors_sealed_assignment_when_default_resolution_changes(
    tmp_path, monkeypatch
):
    observed = []

    def loop(**kwargs):
        observed.append(kwargs["max_steps"])
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "resolve_max_turns", lambda _: 1)
    monkeypatch.setattr(task_module, "run_agent_turn", loop)
    result = task_module.task_tool.run(
        {
            "description": "bounded implementation",
            "prompt": "Update allowed.txt.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _logical_agent_role="executor",
            _logical_agent_max_model_calls=7,
            _logical_agent_workspace_root=str(tmp_path),
            _logical_agent_scope={
                "read_roots": (".",),
                "write_paths": ("allowed.txt",),
                "forbidden_paths": (".git",),
            },
        ),
    )
    assert result.ok
    assert observed == [6]


@pytest.mark.parametrize("calls", [1, 2, 3, 4, 10, 11, 12])
def test_insufficient_scoped_budget_blocks_before_worker_or_verifier(
    tmp_path, monkeypatch, calls
):
    (tmp_path / "allowed.txt").write_text("before")
    budget = AgentTurnBudget(max_model_calls=calls)
    budget.reserve_model_call()  # parent dispatch
    invoked = []
    monkeypatch.setattr(task_module, "_run", lambda *a: invoked.append("worker"))
    monkeypatch.setattr(
        DeterministicVerifier, "run", lambda *a, **k: invoked.append("verifier")
    )
    result = task_module.task_tool.run(
        {
            "description": "bounded change",
            "prompt": "Update allowed.txt.",
            "agent_type": "workflow",
            "workflow_request": _request(risk="low", complex=False),
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_budget=budget,
            _workflow_commit_audit_waived=True,
        ),
    )
    assert not result.ok
    assert "executor_budget_insufficient" in result.output
    assert invoked == []
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().limit_model_calls == calls
    assert (tmp_path / "allowed.txt").read_text() == "before"


def test_local_allocation_cannot_increase_shared_tokens_or_cost():
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=7, max_cost_usd="5")
    worker = _WorkerCallBudget(shared, "worker", max_calls=30, preserve_calls=1)
    with pytest.raises(AgentBudgetExceeded, match="token budget exhausted"):
        worker.reserve_model_call(8)
    assert shared.snapshot().used_model_calls == 0
    lease = worker.reserve_model_call(7)
    worker.record_model_call(input_tokens=7, cost_usd="5", reservation=lease)
    with pytest.raises(AgentBudgetExceeded, match="cost budget exhausted"):
        worker.reserve_model_call()
    assert shared.snapshot().used_model_calls == 1
    assert shared.snapshot().used_total_tokens == 7
    assert float(shared.snapshot().used_cost_usd) == 5.0


def test_unreadable_private_configuration_is_reported(monkeypatch):
    def unreadable(*args, **kwargs):
        raise OSError("private settings unavailable")

    monkeypatch.delenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raising=False)
    monkeypatch.setattr("minicode.env_file.read_private_env_file", unreadable)
    with pytest.raises(ValueError, match="unreadable"):
        configured_max_turns("general")
    with pytest.raises(ValueError, match="unreadable"):
        resolve_max_turns("general")
    with pytest.raises(ValueError, match="unreadable"):
        _allocate()
