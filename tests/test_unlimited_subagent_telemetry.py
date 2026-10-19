"""Unlimited task limits remain explicit in durable, closed telemetry."""

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.run_journal import RunJournal
from minicode.subagent_journal import SubagentJournalError, new_subagent_id
from minicode.subagent_observation import normalize_subagent_payload, project_subagent_event


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "timed_out"])
def test_null_limit_keeps_real_sidecar_and_terminal_event(tmp_path, outcome):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    parent = journal.create_run(title="parent", source="headless")
    journal.transition(parent.id, "running")
    sidecar = journal.open_subagent_journal(parent.id)
    identity = new_subagent_id()
    budget = AgentTurnBudget()
    sidecar.start(subagent_id=identity, agent_type="general", max_turns=None, budget=budget.snapshot())
    sidecar.append_event(identity, sequence=1, event_type="model.started", step=1,
                         payload={"operationId": "modelop_" + "a" * 32})
    sidecar.finish(identity, outcome=outcome, model_turns=1, tool_calls=0, duration_ms=3,
                   max_turns=None, result_truncated=False, budget=budget.snapshot())
    summary, = sidecar.list_runs()
    assert summary.max_turns is None and summary.outcome == outcome
    assert summary.model_turns == 1 and summary.event_count == 1
    assert summary.to_dict()["maxTurns"] is None
    event = project_subagent_event(agent_type="general", outcome=outcome, model_turns=1,
        tool_calls=0, duration_ms=3, max_turns=None, result_truncated=False,
        subagent_id=identity, result_contract_status="fallback" if outcome == "completed" else "unavailable")
    assert event is not None and event["modelTurnLimit"] is None
    assert normalize_subagent_payload(event) == event
    missing = dict(event)
    del missing["modelTurnLimit"]
    assert normalize_subagent_payload(missing) is None
    assert normalize_subagent_payload({**event, "toolCalls": True}) is None
    assert normalize_subagent_payload({**event, "modelTurnLimit": -1}) is None


def test_explicit_null_legacy_observation_is_distinct_from_omitted_limit():
    fields = dict(agent_type="explore", outcome="completed", model_turns=9,
                  tool_calls=8, duration_ms=30, result_truncated=False)
    assert project_subagent_event(**fields) is None
    event = project_subagent_event(**fields, max_turns=None)
    assert event is not None and event["maxTurns"] is None
    assert normalize_subagent_payload(event) == event
    without_limit = dict(event)
    del without_limit["maxTurns"]
    assert normalize_subagent_payload(without_limit) is None


@pytest.mark.parametrize("invalid", [True, -1, "unlimited"])
def test_null_support_does_not_weaken_other_journal_limits(tmp_path, invalid):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    parent = journal.create_run(title="parent", source="headless")
    sidecar = journal.open_subagent_journal(parent.id)
    with pytest.raises(SubagentJournalError, match="max turns"):
        sidecar.start(subagent_id=new_subagent_id(), agent_type="general", max_turns=invalid,
                      budget=AgentTurnBudget().snapshot())
