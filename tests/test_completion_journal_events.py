"""Completion evidence must survive the real journal, not just a fake sink."""
from __future__ import annotations

import pytest

import minicode.agent_loop as loop
from minicode.agent_budget import AgentTurnBudget
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from test_primary_agent_completion import Model, call, registry, run


@pytest.mark.parametrize("mode", ["step_limit", "model_budget", "extension"])
def test_completion_events_persist_through_production_observation(tmp_path, monkeypatch, mode):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "journal"
    journal = RunJournal(workspace, data_dir=data_dir)
    options = {"max_steps": 3}
    model = Model()
    if mode == "model_budget":
        options = {"max_steps": 50, "agent_budget": AgentTurnBudget(max_model_calls=3)}
    elif mode == "extension":
        policy = loop.TurnCompletionPolicy
        monkeypatch.setattr(loop, "TurnCompletionPolicy", lambda **kwargs: policy(
            **{**kwargs, "soft_limit": 3},
        ))
        options = {"max_steps": 15}
        model = Model([call(), call(), call("write_file", path="fixed.py", content="fixed\n")] + [call()] * 10)

    with observe_run(
        workspace=workspace, source="headless", title="Completion journal regression",
        journal_factory=lambda _workspace: journal, enabled=True,
    ) as observation:
        messages = run(workspace, model, registry([]), event_sink=observation, **options)
        run_id = observation.run_id

    # Reopen from disk: an in-memory event callback alone missed the live bug.
    events = RunJournal(workspace, data_dir=data_dir).list_events(run_id, limit=100).items
    limited = [event for event in events if event.type == "execution.completion_limited"]
    assert len(limited) == 1
    assert limited[0].payload["partial"] is True
    assert limited[0].payload["modelSummary"] is True
    assert messages[-1]["_partial"] is True
    outcome = next(event for event in events if event.type == "task.outcome")
    assert outcome.payload["goalAchieved"] is False
    if mode == "extension":
        extensions = [event for event in events if event.type == "execution.steps_extended"]
        assert len(extensions) == 1
        assert extensions[0].payload == {"previousLimit": 3, "newLimit": 8}
        assert extensions[0].sequence < limited[0].sequence
