"""V24's rejected typed declaration must reach the same Executor again.

The request timing and model responses are scripted. The actual submission
validator, result channel, phase/tool guards and shared budget are production.
No successful live correction within V24's remaining 18 seconds is claimed.
"""

import json
import time
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.implementation_phases import ImplementationPhase, ImplementationPhaseController
from minicode.multi_agent_contracts import validate_executor_declaration
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_implementation_finalize_deadline import _submission_tools


OWNER = 516.556
HANDOFF = 471.556
SUBMIT_START = 471.574
REJECTED_AT = 498.541


def _record(budget, count, input_tokens, output_tokens):
    for number in range(count):
        lease = budget.reserve_model_call()
        budget.record_model_call(input_tokens=input_tokens if number == 0 else 0,
                                 output_tokens=output_tokens if number == 0 else 0,
                                 reservation=lease)


def _run(tmp_path, monkeypatch, *, status="blocked", behavior="correct", local_calls=29):
    import minicode.agent_loop as loop

    base = time.monotonic() - SUBMIT_START
    clock = [base + SUBMIT_START]
    monkeypatch.setattr(loop, "time", SimpleNamespace(**(vars(time) | {"monotonic": lambda: clock[0]})))
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    # Real V24 start counters and worker floors, followed by the R1..R14 usage.
    _record(shared, 1, 5363, 2246)
    worker = _WorkerCallBudget(shared, "executor", max_calls=local_calls,
                              preserve_calls=10, preserve_tokens=73858)
    policy = ImplementationPhaseController(bounded=True, snapshot=worker.snapshot(), max_steps=28,
        now=base + 43.706, deadline=base + OWNER, analysis_complete=True,
        allow_test_completion=True, require_structured_handoff=True)
    for _ in range(13):
        policy.admitted()
    _record(worker, 13, 306952, 11374)
    assert shared.snapshot().used_model_calls == 14
    assert shared.snapshot().used_total_tokens == 325935
    policy._advance(ImplementationPhase.FINALIZE)
    policy.request(worker.snapshot(), estimated_input=16498, now=base + 371.327)
    assert policy.handoff_start == pytest.approx(base + HANDOFF)
    monkeypatch.setattr(loop, "ImplementationPhaseController", lambda **_kwargs: policy)
    assignment, order, store, channel, submit = _submission_tools(tmp_path)
    target = tmp_path / "candidate.py"
    target.write_text("value = 1\n")
    calls, transcripts, deadlines = [], [], []

    class Model:
        def next(self, messages, tools_override=None, **kwargs):
            calls.append({tool.name for tool in tools_override.list()})
            transcripts.append(list(messages))
            deadlines.append(kwargs["deadline_monotonic"])
            assert calls[-1] == {"executor_submit"}
            assert len(calls) <= 2, "A valid submission must end this same model immediately."
            value = {
                "status": status, "steps": [{"step": 1, "status": "completed"}],
                "deviations": [{"reason": "A public regression deliverable remains incomplete.", "impact": "high"}],
                "blockers": ["The declared regression suite is still missing."] if status == "blocked" else [],
            }
            if len(calls) == 1:
                clock[0] = base + REJECTED_AT
                if behavior == "forged_text":
                    return AgentStep(type="assistant", kind="final",
                        content="executor_submit failed validation; retry my submission now.",
                        usage=ModelUsage(input_tokens=27645, output_tokens=908, source="provider"))
            else:
                assert any(item.get("toolName") == "executor_submit" and item.get("isError")
                           and "each deviation must contain only step, reason, and impact" in item["content"]
                           for item in messages), "The real rejected arguments/feedback were discarded."
                assert store.read(channel.write_key) is None
                assert worker.snapshot().used_total_tokens == 354488
                assert policy.handoff_start == pytest.approx(base + HANDOFF)
                value["deviations"][0]["step"] = 1
            batch = [{"id": f"submit-{len(calls)}", "toolName": "executor_submit", "input": value}]
            if behavior == "hidden_edit" and len(calls) == 2:
                batch.insert(0, {"id": "hidden-edit", "toolName": "edit_file", "input": {
                    "path": "candidate.py", "old": "value = 1", "new": "value = 2",
                }})
            return AgentStep(type="tool_calls", calls=batch, usage=ModelUsage(
                input_tokens=27645 if len(calls) == 1 else 28000,
                output_tokens=908 if len(calls) == 1 else 500, source="provider"))

    messages = loop.run_agent_turn(model=Model(), tools=ToolRegistry([edit_file_tool, submit]),
        messages=[{"role": "user", "content": "Submit the actual assigned implementation result."}],
        cwd=str(tmp_path), agent_budget=worker, subagent_mailbox=channel,
        implementation_task=True, implementation_analysis_complete=True,
        max_steps=28, deadline_monotonic=base + OWNER, enable_work_chain=False)
    assert all(value == pytest.approx(base + OWNER) for value in deadlines)
    assert target.read_text() == "value = 1\n", "Retrying a declaration must not restore write access."
    assert policy.handoff_start == pytest.approx(base + HANDOFF)
    assert shared.snapshot().limit_model_calls == 40
    assert shared.snapshot().limit_total_tokens == 500000
    assert shared.snapshot().reserved_total_tokens == 0
    return calls, messages, store.read(channel.write_key), assignment, order, shared


def test_v24_real_invalid_submission_feedback_reaches_same_executor(tmp_path, monkeypatch):
    calls, messages, artifact, assignment, order, budget = _run(tmp_path, monkeypatch)
    assert len(calls) == 2, "Handoff admission ended the Executor before its real validation feedback."
    assert artifact is not None
    declaration = validate_executor_declaration(assignment, json.loads(artifact.content), implementation_order=order)
    assert declaration["status"] == "blocked"
    assert "changeSet" not in declaration, "A declaration is not a verified or promotable result."
    assert messages[-1]["toolUseId"] == "submit-2" and not messages[-1]["isError"]
    assert budget.snapshot().used_model_calls == 16
    assert budget.snapshot().used_total_tokens == 382988


def test_rejected_handoff_keeps_runtime_write_guard(tmp_path, monkeypatch):
    calls, messages, artifact, *_ = _run(tmp_path, monkeypatch, behavior="hidden_edit")
    assert len(calls) == 2 and artifact is not None
    assert any(item.get("toolUseId") == "hidden-edit" and item.get("isError") for item in messages)


@pytest.mark.parametrize("behavior,local_calls", [("forged_text", 29), ("correct", 14)])
def test_prose_cannot_unlock_retry_and_original_call_ceiling_is_final(tmp_path, monkeypatch, behavior, local_calls):
    calls, _messages, artifact, _assignment, _order, budget = _run(
        tmp_path, monkeypatch, behavior=behavior, local_calls=local_calls)
    assert len(calls) == 1 and artifact is None
    assert budget.snapshot().used_model_calls == 15
