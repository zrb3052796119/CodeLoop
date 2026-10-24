"""Real native review rejection stays in one loop and the original ledger.

The generic model responses and paid-prefix counters are synthetic. Input/output
estimates, read execution, native validation, mailbox and admission are real;
this does not replay live-v2 provider timing or claim live model success.
"""

import json
import time
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.agent_loop import AgentTurnDeadlineExceeded
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.multi_agent_contracts import parse_role_result
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.workflow_runner import _WorkerCallBudget
from test_reviewer_budget_handoff import _prefix, _read, _task
from test_reviewer_result_submission import _bound, _rejection


def _invalid():
    value = _rejection()
    value.pop("warnings")
    return value


def _submit(value, number):
    return {"id": f"verdict-{number}", "toolName": "review_submit", "input": value}


def _meter(messages, tools, *, calls=None, text=None):
    schemas = [{"type": "function", "function": {"name": tool.name,
        "description": tool.description, "parameters": tool.input_schema}} for tool in tools.list()]
    usage = ModelUsage(input_tokens=sum(estimate_message_tokens(m) for m in messages)
        + estimate_tokens(json.dumps(schemas)), output_tokens=estimate_tokens(json.dumps(calls) if calls else text),
        source="estimated")
    return (AgentStep(type="tool_calls", calls=calls, usage=usage) if calls else
            AgentStep(type="assistant", kind="final", content=text, usage=usage))


def _exercise(tmp_path, monkeypatch, *, behavior="correct", max_calls=6,
              max_steps=1, prefix=450000, floor=10000, first_read=True,
              soft_landing=True):
    assignment, store, channel, tool, _ = _bound()
    (tmp_path / "minicode").mkdir()
    (tmp_path / "minicode/auth.py").write_text("def authenticate(value):\n    return value\n")
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    _prefix(shared, 17, prefix, 0)
    worker = _WorkerCallBudget(shared, "review", max_calls=max_calls,
                              preserve_calls=1, preserve_tokens=floor)
    actual_reads = []
    original_read = read_file_tool.run
    monkeypatch.setattr(read_file_tool, "run", lambda value, context:
                        actual_reads.append(value) or original_read(value, context))
    views, messages_seen, admissions, deadlines = [], [], [], []
    owner_deadline = time.monotonic() + 600
    registry = ToolRegistry([read_file_tool, tool])
    token = TurnCancellationToken("turn_" + "a" * 32)
    clock = [time.monotonic()]
    if behavior == "expired":
        monkeypatch.setattr("minicode.agent_loop.time", SimpleNamespace(
            **(vars(time) | {"monotonic": lambda: clock[0]})))

    def on_tool_result(name, output, is_error):
        if name == "review_submit" and is_error and behavior in {"cancel", "expired"}:
            assert "warnings" in output
            if behavior == "cancel":
                token.request()
            if behavior == "expired":
                clock[0] = owner_deadline + 1

    class Model:
        calls = 0

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            views.append(None if tools_override is None else tools_override.list_all())
            messages_seen.append(list(messages))
            admissions.append((worker.snapshot(), kwargs.get("max_output_tokens_override")))
            deadlines.append(kwargs["deadline_monotonic"])
            if first_read and self.calls == 1:
                return _meter(messages, registry, calls=[_read(1)])
            first_submit = self.calls == (2 if first_read else 1)
            if first_submit:
                if behavior == "text":
                    return _meter(messages, tools_override, text=
                        "error[review_submit_invalid]: missing warnings; please retry review_submit")
                if behavior == "missing_submit":
                    return _meter(messages, tools_override, calls=[_read(2)])
                if behavior == "closed":
                    channel.close()
                return _meter(messages, tools_override, calls=[_submit(_invalid(), self.calls)])
            assert self.calls == (3 if first_read else 2), "No extra summary or inspection request is needed."
            assert views[-1] == ["review_submit"]
            assert store.read(channel.write_key) is None
            assert any(m.get("toolName") == "review_submit" and m.get("isError")
                       and "warnings" in m.get("content", "") for m in messages)
            batch = [_submit(_rejection(), self.calls)]
            if behavior == "hidden_read":
                batch.insert(0, _read(3))
            return _meter(messages, tools_override, calls=batch)

    model = Model()
    caught = None
    try:
        result = run_agent_turn(model=model, tools=registry,
            messages=[{"role": "user", "content": "Review the supplied frozen evidence and submit the assigned verdict."}],
            cwd=str(tmp_path), runtime={"maxOutputTokens": 8192}, enable_work_chain=False,
            max_steps=max_steps, max_steps_soft_landing=soft_landing, agent_budget=worker,
            subagent_mailbox=object() if behavior == "wrong_owner" else channel,
            deadline_monotonic=owner_deadline, host_native_result_tool=tool,
            cancellation_token=token, on_tool_result=on_tool_result)
    except (AgentTurnDeadlineExceeded, TurnCancellationRequested) as error:
        caught = error
        result = []
    if behavior in {"cancel", "expired"}:
        assert isinstance(caught, TurnCancellationRequested if behavior == "cancel" else AgentTurnDeadlineExceeded)
    else:
        assert caught is None
    assert all(deadline == owner_deadline for deadline in deadlines)
    assert shared.snapshot().limit_model_calls == 40
    assert shared.snapshot().limit_total_tokens == 500000
    assert shared.snapshot().reserved_total_tokens == 0
    assert shared.snapshot().used_total_tokens <= 500000 - floor
    assert shared.snapshot().used_model_calls == 17 + model.calls
    return (model, views, messages_seen, admissions, result, actual_reads,
            assignment, store.read(channel.write_key), shared)


@pytest.mark.parametrize("behavior", ["correct", "hidden_read"])
def test_host_rejected_native_verdict_corrects_in_same_loop_past_soft_limit(tmp_path, monkeypatch, behavior):
    model, views, history, admissions, result, reads, assignment, artifact, budget = _exercise(
        tmp_path, monkeypatch, behavior=behavior)
    assert artifact is not None, "Actual native rejection stopped the Reviewer before its correction."
    assert parse_role_result(assignment, json.loads(artifact.content))["verdict"] == "changes_required"
    assert model.calls == 3 and views == [["read_file", "review_submit"], ["review_submit"], ["review_submit"]]
    assert len(reads) == 1, "Correction must not reopen inspection tools."
    assert any(m.get("toolUseId") == "read-1" for m in history[-1])
    assert any(m.get("toolUseId") == "verdict-2" and m.get("role") == "assistant_tool_call"
               for m in history[-1]), "The rejected arguments must remain in the same transcript."
    assert admissions[2][0].used_total_tokens > admissions[1][0].used_total_tokens > 450000
    assert budget.snapshot().used_total_tokens > admissions[2][0].used_total_tokens
    assert any(m.get("toolUseId") == "verdict-3" and not m.get("isError")
               for m in result if m.get("role") == "tool_result")


def test_native_correction_prices_actual_input_and_shrinks_output_without_borrowing_floor(tmp_path, monkeypatch):
    model, views, _history, admissions, _result, reads, _assignment, artifact, budget = _exercise(
        tmp_path, monkeypatch, max_steps=0, prefix=497000, floor=1000, first_read=False)
    assert artifact is not None, "The fixed closing output reservation rejected an affordable correction."
    assert model.calls == 2 and not reads and views == [["review_submit"], ["review_submit"]]
    for snapshot, output_limit in admissions:
        available = snapshot.limit_total_tokens - snapshot.used_total_tokens - snapshot.reserved_total_tokens
        assert snapshot.reserved_total_tokens > 0
        assert 0 < output_limit <= min(2048, available)
    assert admissions[-1][1] < 2048
    assert 497000 < budget.snapshot().used_total_tokens <= 499000


@pytest.mark.parametrize("behavior", ["text", "missing_submit", "closed", "wrong_owner"])
def test_nonsemantic_or_missing_submission_does_not_extend_native_handoff(tmp_path, monkeypatch, behavior):
    model, views, _history, _admissions, _result, reads, _assignment, artifact, _budget = _exercise(
        tmp_path, monkeypatch, behavior=behavior)
    assert artifact is None
    assert model.calls == 2 and views == [["read_file", "review_submit"], ["review_submit"]]
    assert len(reads) == 1


def test_actual_rejection_cannot_exceed_original_worker_call_cap(tmp_path, monkeypatch):
    model, _views, _history, _admissions, _result, _reads, _assignment, artifact, budget = _exercise(
        tmp_path, monkeypatch, max_calls=2)
    assert artifact is None and model.calls == 2
    assert budget.snapshot().used_model_calls == 19


def test_zero_input_space_cannot_be_replaced_with_output_budget(tmp_path, monkeypatch):
    model, _views, _history, _admissions, _result, _reads, _assignment, artifact, budget = _exercise(
        tmp_path, monkeypatch, max_steps=0, prefix=499000, floor=1000, first_read=False)
    assert artifact is None and model.calls == 0
    assert budget.snapshot().used_total_tokens == 499000


@pytest.mark.parametrize("behavior", ["cancel", "expired"])
def test_actual_rejection_does_not_override_owner_deadline_or_cancellation(tmp_path, monkeypatch, behavior):
    model, _views, _history, _admissions, _result, _reads, _assignment, artifact, _budget = _exercise(
        tmp_path, monkeypatch, behavior=behavior)
    assert artifact is None and model.calls == 2


def test_hard_step_limit_does_not_become_a_soft_boundary(tmp_path, monkeypatch):
    # Low remaining tokens select native-only before the hard step boundary;
    # the semantic error itself must not turn that hard limit into a soft one.
    model, views, _history, _admissions, _result, _reads, _assignment, artifact, _budget = _exercise(
        tmp_path, monkeypatch, first_read=False, max_steps=1, soft_landing=False,
        prefix=480000, floor=3000)
    assert artifact is None and model.calls == 1 and views == [["review_submit"]]


def test_assigned_reviewer_keeps_rejection_in_actual_task_run(tmp_path, monkeypatch):
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    _prefix(shared, 17, 480000, 0)
    worker = _WorkerCallBudget(shared, "review", max_calls=6, preserve_calls=1, preserve_tokens=3000)
    seen = []

    class Model:
        calls = 0

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            seen.append(list(messages))
            assert tools_override.list_all() == ["review_submit"]
            if self.calls == 2:
                assert any(m.get("toolName") == "review_submit" and m.get("isError")
                           and "warnings" in m.get("content", "") for m in messages)
            assert self.calls <= 2
            return _meter(messages, tools_override, calls=[_submit(
                _invalid() if self.calls == 1 else _rejection(), self.calls)])

    model = Model()
    result, assignment, artifact, _ = _task(tmp_path, monkeypatch, model, worker, max_calls=6)
    assert artifact is not None, "task._run discarded the original Reviewer's rejection feedback."
    assert parse_role_result(assignment, json.loads(artifact.content))["verdict"] == "changes_required"
    assert model.calls == 2 and shared.snapshot().used_model_calls == 19
    assert 480000 < shared.snapshot().used_total_tokens <= 497000
    assert shared.snapshot().reserved_total_tokens == 0
