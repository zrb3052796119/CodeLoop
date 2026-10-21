"""Skill execution state must not inherit the attribution event's item limit."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.capability_registry import CapabilityRegistry
from minicode.skill_router import (
    SkillRouter,
    required_skill_names_for_routing,
    skill_load_policy_for_routing,
)
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.skills import SkillSummary, discover_skills
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import SkillLoadPolicy, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.load_skill import create_load_skill_tool
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class _ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = iter(steps)
        self.calls = 0

    def next(self, messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        self.calls += 1
        return next(self.steps)


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type, *, step=None, payload=None) -> None:
        self.events.append((event_type, dict(payload or {})))


def _catalog(root: Path, count: int = 21) -> list[SkillSummary]:
    for index in range(count):
        name = f"skill{index:02d}"
        path = root / ".mini-code" / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            f"---\nname: {name}\ndescription: A focused workflow\n---\nFollow {name}.\n",
            encoding="utf-8",
        )
    return sorted(discover_skills(root), key=lambda skill: skill.name)


def _route(catalog: list[SkillSummary], mode: str):
    label = " skill." if len(catalog) == 1 else " skills."
    prompt = "Use " + ", ".join(skill.name for skill in catalog) + label
    return route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            skills=[asdict(skill) for skill in catalog],
            registry=CapabilityRegistry(),
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
        controls={"skill_selection_mode": mode},
    ).routing


def _loads(catalog: list[SkillSummary]) -> AgentStep:
    return AgentStep(type="tool_calls", calls=[
        {"id": skill.name, "toolName": "load_skill", "input": {"name": skill.name}}
        for skill in catalog
    ])


def _recording_tool(actions: list[str]) -> ToolDefinition:
    def run(input_data, _context) -> ToolResult:
        actions.append(input_data["note"])
        return ToolResult(ok=True, output="recorded")

    return ToolDefinition(
        name="record_action",
        description="Record an observable action.",
        input_schema={"type": "object", "properties": {"note": {"type": "string"}}},
        validator=lambda data: data,
        run=run,
    )


def _reload_and_act() -> AgentStep:
    return AgentStep(type="tool_calls", calls=[
        {"id": "reload", "toolName": "load_skill", "input": {"name": "skill20"}},
        {"id": "act", "toolName": "record_action", "input": {"note": "same-batch"}},
    ])


def _results(messages: list[ChatMessage]) -> dict[str, ChatMessage]:
    return {
        message["toolUseId"]: message
        for message in messages
        if message.get("role") == "tool_result"
    }


@pytest.mark.parametrize("observe", [False, True])
def test_all_explicit_skills_can_complete_beyond_attribution_limit(tmp_path, observe):
    catalog = _catalog(tmp_path)
    routing = _route(catalog, "routed")
    required = required_skill_names_for_routing(routing)
    assert len(required) == 21
    sink = _RecordingSink() if observe else None
    outcome = AgentOutcomeCapture()
    messages = run_agent_turn(
        model=_ScriptedModel([
            _loads(catalog),
            AgentStep(type="assistant", content="done"),
            AgentStep(type="assistant", content="unexpected retry"),
        ]),
        tools=ToolRegistry([create_load_skill_tool(str(tmp_path), catalog)]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        required_skill_names=required,
        skill_load_policy=skill_load_policy_for_routing(routing),
        outcome_capture=outcome,
        event_sink=sink,
    )

    assert len(_results(messages)) == 21
    assert all(not result["isError"] for result in _results(messages).values())
    assert outcome.outcome.status == "success"
    assert messages[-1]["content"] == "done"
    if sink is not None:
        attributed = [payload for kind, payload in sink.events if kind == "skill.attributed"]
        assert len(attributed) == 1
        assert attributed[0]["loadedSkillCount"] == 20
        assert len(attributed[0]["loadedSkills"]) == 20
        assert attributed[0]["loadedSkillsTruncated"] is True


def test_model_reload_beyond_attribution_limit_does_not_defer_sibling(tmp_path):
    catalog = _catalog(tmp_path)
    routing = _route(catalog, "model")
    assert required_skill_names_for_routing(routing) == []
    actions: list[str] = []
    messages = run_agent_turn(
        model=_ScriptedModel([
            _loads(catalog), _reload_and_act(), AgentStep(type="assistant", content="done")
        ]),
        tools=ToolRegistry([
            create_load_skill_tool(str(tmp_path), catalog), _recording_tool(actions)
        ]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        skill_load_policy=skill_load_policy_for_routing(routing),
    )

    results = _results(messages)
    assert results["skill20"]["isError"] is False
    assert results["reload"]["isError"] is False
    assert results["act"]["isError"] is False
    assert actions == ["same-batch"]


@pytest.mark.parametrize("failure", ["denied", "stale"])
def test_failed_load_beyond_attribution_limit_still_defers_sibling(tmp_path, failure):
    catalog = _catalog(tmp_path)
    loader = create_load_skill_tool(str(tmp_path), catalog)
    policy = SkillLoadPolicy(allow_catalog_scope=True)
    if failure == "denied":
        policy = SkillLoadPolicy(
            allow_catalog_scope=True, denied_qualified_names=frozenset({"skill20"})
        )
    else:
        Path(catalog[-1].path).write_text("Changed after catalog discovery.\n", encoding="utf-8")
    actions: list[str] = []
    messages = run_agent_turn(
        model=_ScriptedModel([
            _loads(catalog), _reload_and_act(), AgentStep(type="assistant", content="done")
        ]),
        tools=ToolRegistry([loader, _recording_tool(actions)]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        skill_load_policy=policy,
    )

    results = _results(messages)
    assert sum(not results[skill.name]["isError"] for skill in catalog) == 20
    assert results["skill20"]["isError"] is True
    assert results["reload"]["isError"] is True
    assert "deferred_until_skill_loaded" in results["act"]["content"]
    assert actions == []


def test_historical_success_does_not_replace_current_turn_required_load(tmp_path):
    catalog = _catalog(tmp_path, count=1)
    routing = _route(catalog, "routed")
    tools = ToolRegistry([create_load_skill_tool(str(tmp_path), catalog)])
    previous = run_agent_turn(
        model=_ScriptedModel([_loads(catalog), AgentStep(type="assistant", content="first done")]),
        tools=tools,
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        skill_load_policy=skill_load_policy_for_routing(routing),
    )
    assert _results(previous)["skill00"]["isError"] is False
    model = _ScriptedModel([
        AgentStep(type="assistant", content="premature final"),
        AgentStep(type="tool_calls", calls=[
            {"id": "current-load", "toolName": "load_skill", "input": {"name": "skill00"}}
        ]),
        AgentStep(type="assistant", content="current done"),
    ])
    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[*previous, {"role": "user", "content": "Use skill00 again."}],
        cwd=str(tmp_path),
        required_skill_names=required_skill_names_for_routing(routing),
        skill_load_policy=skill_load_policy_for_routing(routing),
    )

    assert model.calls == 3
    assert _results(messages)["current-load"]["isError"] is False
    assert messages[-1]["content"] == "current done"
