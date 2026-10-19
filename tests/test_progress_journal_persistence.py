"""Progress telemetry must survive the real observation sink and disk reload."""

from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter


class ReadingFixtureModel(ModelAdapter):
    """Local deterministic input; no provider or model API is involved."""

    def __init__(self):
        self.reads = 0

    def next(self, messages, on_stream_chunk=None):
        if self.reads < 12:
            self.reads += 1
            return AgentStep(type="tool_calls", calls=[{
                "id": f"read-{self.reads}", "toolName": "read_file",
                "input": {"path": f"part-{self.reads}.py", "limit": 8000},
            }])
        return AgentStep(type="assistant", content="Inspection fixture complete; no changes made.")


@pytest.mark.parametrize("bounded_budget", [False, True])
def test_real_loop_progress_and_read_history_events_persist(tmp_path, bounded_budget):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(1, 13):
        (workspace / f"part-{index}.py").write_text(
            f"# SOURCE_BODY_MARKER_{index}\n" + "# implementation context\n" * 250,
            encoding="utf-8",
        )
    data_dir = tmp_path / "journal"
    journal = RunJournal(workspace, data_dir=data_dir)
    with observe_run(
        workspace=workspace, source="headless", title="Progress observation fixture",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        assert run_id is not None
        run_agent_turn(
            model=ReadingFixtureModel(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
            messages=[{"role": "user", "content": "Fix the requested implementation defect."}],
            cwd=str(workspace), implementation_task=True, enable_work_chain=False,
            max_steps=22, event_sink=observation,
            agent_budget=AgentTurnBudget(max_total_tokens=500_000) if bounded_budget else None,
        )

    reloaded = RunJournal(workspace, data_dir=data_dir)
    events = reloaded.list_events(run_id, limit=100).items
    checkpoints = [event for event in events if event.type == "execution.progress_checkpoint"]
    compactions = [event for event in events if event.type == "context.read_history_bounded"]
    assert checkpoints and compactions
    assert all(event.payload["inspectionBatches"] >= 4 for event in checkpoints)
    assert all(set(event.payload) == {
        "reasonCode", "inspectionBatches", "remainingTokens", "estimatedRequestsLeft",
        "remainingSeconds",
    } for event in checkpoints)
    assert all((event.payload["remainingTokens"] is not None) is bounded_budget for event in checkpoints)
    assert all(event.payload["charactersSaved"] > 0 for event in compactions)
    assert all(event.payload["protectedResultCount"] == 1 for event in compactions)
    assert reloaded.get_run(run_id).status == "completed"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert "SOURCE_BODY_MARKER" not in repr([event.to_dict() for event in events])
    assert len(list(Path(workspace).iterdir())) == 12
