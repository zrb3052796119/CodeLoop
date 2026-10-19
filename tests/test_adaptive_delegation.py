"""Parent delegates collaborative edits and keeps trivial edits."""

from __future__ import annotations

from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.delegation_policy import (
    decide_delegation,
    delegation_prompt_note,
    parent_delegation_gate,
)
from minicode.prompt import build_system_prompt
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.task import task_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep

MULTI = "Implement hold and release in stock.py and test_public.py."
SINGLE = "Fix the off-by-one in hold() in stock.py."


class Script:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0
        self.seen: list[list] = []

    def next(self, messages, on_stream_chunk=None):
        self.seen.append(messages)
        step = self.steps[self.calls]
        self.calls += 1
        return step


def _write(identity: str, content: str) -> AgentStep:
    return AgentStep(type="tool_calls", calls=[{
        "id": identity,
        "toolName": "write_file",
        "input": {"path": "stock.py", "content": content},
    }])


def _final() -> AgentStep:
    return AgentStep(type="assistant", content="<final>done")


def _turn(tmp_path: Path, model, *, runtime=None, agent_depth=0, tools=None, messages=None):
    return run_agent_turn(
        model=model,
        tools=tools or ToolRegistry([write_file_tool]),
        messages=messages or [{"role": "user", "content": MULTI}],
        cwd=str(tmp_path),
        runtime=runtime or {"subagentRoutingEnabled": True},
        enable_work_chain=False,
        defer_curator_maintenance=True,
        max_steps=4,
        agent_depth=agent_depth,
    )


def test_multi_file_change_prefers_workflow_and_a_typo_stays_solo() -> None:
    collaborative = decide_delegation(MULTI, routing_enabled=True, implementation_owned=False)
    assert collaborative.mode == "workflow"
    assert collaborative.write_paths == ("stock.py", "test_public.py")

    local = decide_delegation(SINGLE, routing_enabled=True, implementation_owned=False)
    assert local.mode == "solo"
    assert local.reason == "local change"

    typo = decide_delegation(
        "Fix the typo in stock.py and test_public.py.",
        routing_enabled=True,
        implementation_owned=False,
    )
    assert typo.mode == "solo"

    assert decide_delegation(MULTI, routing_enabled=False, implementation_owned=False).mode == "solo"
    assert decide_delegation(MULTI, routing_enabled=True, implementation_owned=True).mode == "solo"
    survey = decide_delegation(
        "How does authentication work across the repository?",
        routing_enabled=True,
        implementation_owned=False,
    )
    assert survey.mode == "explore"
    assert decide_delegation(
        "Use a subagent to implement hold in stock.py.",
        routing_enabled=True,
        implementation_owned=False,
    ).mode == "workflow"
    assert parent_delegation_gate(
        MULTI, routing_enabled=True, implementation_owned=False, agent_depth=1,
    ).pending is False


def test_turn_note_names_the_decision_and_the_static_guide_stays_specific() -> None:
    note = delegation_prompt_note(MULTI, routing_enabled=True, implementation_owned=False)
    guided = build_system_prompt("/tmp", [], {"delegation_note": note})
    assert "stock.py, test_public.py" in guided
    assert "Do not also call plan or explore" in guided
    plain = build_system_prompt("/tmp", [], {"skills": []})
    assert "This turn's delegation" not in plain
    assert "single-file or single-function" in plain
    assert "call agent_type='workflow' directly" in plain
    assert "Do not routinely run Plan first" in plain
    assert "before the parent edits those files" in task_tool.description
    assert "creates or modifies files" in task_tool.description
    assert delegation_prompt_note(
        SINGLE, routing_enabled=True, implementation_owned=False,
    ).startswith("This request is a small local change")
    owned = delegation_prompt_note(
        MULTI, routing_enabled=True, implementation_owned=True,
    )
    assert owned.startswith("This turn is a host-owned implementation")
    assert "Do not call task." in owned


def test_routed_parent_is_coached_off_the_first_multi_file_edit(tmp_path: Path) -> None:
    messages = _turn(tmp_path, Script([_write("1", "x = 1\n"), _final()]))
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" in blob
    assert "stock.py" in blob and "test_public.py" in blob
    assert "Do not also start a plan or explore" in blob
    assert not (tmp_path / "stock.py").exists()


def test_coach_lets_a_later_edit_through_when_workflow_cannot_run(tmp_path: Path) -> None:
    _turn(tmp_path, Script([
        _write("1", "x = 1\n"),
        _write("2", "x = 2\n"),
        _final(),
    ]))
    assert (tmp_path / "stock.py").read_text() == "x = 2\n"


def test_single_file_edit_is_not_coached(tmp_path: Path) -> None:
    messages = _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        messages=[{"role": "user", "content": SINGLE}],
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" not in blob
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_routing_off_keeps_the_multi_file_edit_with_the_parent(tmp_path: Path) -> None:
    _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        runtime={"subagentRoutingEnabled": False},
    )
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_a_workflow_handoff_clears_the_coach(tmp_path: Path) -> None:
    def accept(_input, _context):
        return ToolResult(ok=True, output="workflow accepted")

    task = ToolDefinition(
        name="task",
        description="workflow",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=accept,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1",
                "toolName": "task",
                "input": {"agent_type": "workflow", "prompt": "implement hold"},
            }]),
            _write("2", "x = 1\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "workflow accepted" in blob
    assert "delegate it before editing" not in blob
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_plan_or_explore_does_not_replace_the_workflow_handoff(tmp_path: Path) -> None:
    def reject(_input, _context):
        raise AssertionError("explore must not start for a change that already has its files")

    task = ToolDefinition(
        name="task",
        description="explore",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=reject,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1",
                "toolName": "task",
                "input": {"agent_type": "explore", "prompt": "read both files"},
            }]),
            _write("2", "x = 1\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert blob.count("delegate it before editing") == 2
    assert not (tmp_path / "stock.py").exists()


def test_nested_worker_is_not_coached(tmp_path: Path) -> None:
    _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        agent_depth=1,
    )
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"
