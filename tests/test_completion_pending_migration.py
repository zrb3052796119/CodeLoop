"""A repeated final cannot rerun migration for an unchanged failed candidate."""

import json

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.types import AgentStep, ToolCall
from test_completion_probe_purposes import _resolution, _seed_legacy
from test_implementation_completion_review import _fixture, SOURCE


def test_same_candidate_classified_obligations_do_not_restart_semantic_classifier(tmp_path, monkeypatch):
    import minicode.agent_loop as loop

    real_loop = loop.run_agent_turn
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy))
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))
    review.queue_legacy_resolution(_resolution(review, tmp_path))
    failed = assess(lambda *_args: pytest.fail("failed replacement must not ask a final reviewer"))
    assert failed["reason"] == "legacy_acceptance_product_gap"
    assert not review.has_legacy_probes and review.state["pendingLegacyResolution"] is None
    owner = "implementation_" + "4" * 32
    feedback = {"role": "user", "_implementation_completion_feedback": True,
                "content": "Original request:\n" + review.spec.objective + "\nCompletion evidence:\n" + json.dumps(failed)}
    messages = [{"role": "user", "content": review.spec.objective}, feedback]
    continuation = HostImplementationContinuation.capture(spec=review.spec,
        context=ToolContext(cwd=str(tmp_path)), messages=messages,
        executor_id=owner, attempt=1, review_state=review.state)
    cap.close()
    calls, decisions, migrations = [], [], []

    def forbid_migration(*args, **kwargs):
        migrations.append((args, kwargs))
        pytest.fail("same candidate pending resolution must reuse existing migration evidence")

    class Model:
        def next(self, current, **_kwargs):
            if any(row.get("role") == "system" and "Independently attribute" in row.get("content", "")
                   for row in current):
                return AgentStep(type="tool_calls", calls=[ToolCall(id="confirmed-product-gap", toolName="completion_probe_adjudication_submit", input={
                    "verdict": "product_gap", "reason": "The original release addition requirement still fails.",
                    "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
                    "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}]})])
            calls.append(list(current))
            if len(calls) <= 2:
                return AgentStep(type="assistant", kind="final", content="Ready for delivery.")
            raise TurnCancellationRequested("turn_" + "4" * 32)

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            if event_type == "implementation.completion_decided":
                decisions.append(payload)

    monkeypatch.setattr("minicode.completion_probe_migration.CompletionProbeMigration.assess", forbid_migration)
    monkeypatch.setattr(loop, "run_agent_turn", real_loop)
    with pytest.raises(TurnCancellationRequested):
        real_loop(model=Model(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
            cwd=str(tmp_path), messages=messages, runtime={"model": "test"},
            implementation_spec=review.spec, implementation_continuation=continuation,
            max_steps=None, agent_budget=AgentTurnBudget(), enable_work_chain=False,
            enable_implementation_phases=False, event_sink=Sink(), defer_curator_maintenance=True)
    assert migrations == []
    assert len(calls) == 3
    assert [row["status"] for row in decisions] == ["changes_required", "changes_required"]
    assert {row["executorId"] for row in decisions} == {owner}
    assert [row["attempt"] for row in decisions] == [2, 3]
    assert all(row["reason"] == "legacy_acceptance_product_gap" for row in decisions)
    assert decisions[0]["patchDigest"] == decisions[1]["patchDigest"]
    assert decisions[0]["repairProbes"] == decisions[1]["repairProbes"]
