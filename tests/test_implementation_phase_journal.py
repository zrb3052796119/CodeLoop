"""Phase enforcement must remain observable through the real child sidecar."""

from minicode.agent_loop import run_agent_turn
from minicode.run_journal import RunJournal
from minicode.subagent_journal import new_subagent_id
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.task import _SubagentJournalSink
from minicode.types import AgentStep, ModelAdapter


def test_phase_transitions_survive_child_journal_reload(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("PRIVATE_SOURCE_BYTES = 1\n")
    data = tmp_path / "journal"
    journal = RunJournal(workspace, data_dir=data)
    parent = journal.create_run(title="phase fixture", source="headless")
    journal.transition(parent.id, "running")
    child = new_subagent_id()
    sidecar = journal.open_subagent_journal(parent.id)
    sidecar.start(subagent_id=child, agent_type="general", max_turns=20, budget=None)
    sink = _SubagentJournalSink(sidecar, child)
    class Reader(ModelAdapter):
        def __init__(self):
            self.index = 0
        def next(self, messages, **kwargs):
            self.index += 1
            return AgentStep(type="tool_calls", calls=[{
                "id": str(self.index), "toolName": "read_file", "input": {"path": "app.py"},
            }])
    run_agent_turn(bounded_implementation_phases=True,
        model=Reader(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix the authorized implementation."}],
        cwd=str(workspace), implementation_task=True, enable_work_chain=False,
        agent_depth=1, max_steps=20, event_sink=sink)
    events = RunJournal(workspace, data_dir=data).list_subagent_events(parent.id, child)
    phases = [event for event in events if event.type == "execution.phase_changed"]
    assert [event.payload["phase"] for event in phases] == ["investigate", "implement", "finalize"]
    assert all(event.payload["handoffCallLimit"] == 2 for event in phases)
    assert [event.sequence for event in events] == list(range(1, len(events)+1))
    assert "PRIVATE_SOURCE_BYTES" not in repr(events)
