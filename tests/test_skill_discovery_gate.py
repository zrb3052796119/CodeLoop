"""Contracts for the advisory pre-final skill discovery gate."""

from __future__ import annotations

from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.run_events import SkillUsageTracker
from minicode.skill_router import (
    SkillRoutingResult,
    suggested_skill_directories_for_routing,
)
from minicode.tooling import ToolRegistry
from minicode.tools.load_skill import create_load_skill_tool
from minicode.types import AgentStep


class ScriptedModel:
    """Minimal scripted adapter mirroring the agent-loop test idiom."""

    def __init__(self, steps):
        self._steps = iter(steps)
        self.calls = 0

    def next(self, messages, **kwargs):
        self.calls += 1
        return next(self._steps)


def _empty_registry() -> ToolRegistry:
    return ToolRegistry([])


def _routing_result(suggested=None):
    result = SkillRoutingResult(
        intent_type="code",
        action_type="analyze",
        capability_domains=[],
        capability_scopes=[],
        selected=[],
        total_skills=1,
        used_fallback=False,
    )
    if suggested:
        result.intent_hints = {"suggested_directories": list(suggested)}
    return result


def _write_skill(tmp_path: Path) -> None:
    skill_file = tmp_path / ".mini-code" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("# Demo\n\nDemo body.\n", encoding="utf-8")


def _final(step_content: str) -> list[AgentStep]:
    return [AgentStep(type="assistant", content=step_content)]


class TestSuggestedDirectoriesHelper:
    def test_extracts_from_hints(self) -> None:
        routing = _routing_result(["http", "cache"])
        assert suggested_skill_directories_for_routing(routing) == {"http", "cache"}

    def test_empty_without_hints(self) -> None:
        assert suggested_skill_directories_for_routing(_routing_result()) == frozenset()
        assert suggested_skill_directories_for_routing(None) == frozenset()
        assert suggested_skill_directories_for_routing({}) == frozenset()


class TestTrackerDiscovery:
    def test_record_then_seen(self) -> None:
        tracker = SkillUsageTracker()
        assert tracker.discovery_seen() is False
        tracker.record_discovery("browse_skill_directory")
        assert tracker.discovery_seen() is True

    def test_failed_attempt_still_counts(self) -> None:
        # The gate must not re-nudge a turn that already tried and failed.
        tracker = SkillUsageTracker()
        tracker.record_discovery("search_skills")
        assert tracker.discovery_seen() is True


class TestDiscoveryGate:
    def test_gate_off_leaves_final_untouched(self, tmp_path) -> None:
        _write_skill(tmp_path)
        model = ScriptedModel(_final("direct answer"))
        messages = run_agent_turn(
            model=model,
            tools=_empty_registry(),
            messages=[{"role": "system", "content": "sys"}],
            cwd=str(tmp_path),
            suggested_skill_directories=frozenset({"demo"}),
            skill_discovery_gate=False,
        )
        assert messages[-1] == {"role": "assistant", "content": "direct answer"}
        assert model.calls == 1

    def test_no_suggestions_no_nudge(self, tmp_path) -> None:
        model = ScriptedModel(_final("direct answer"))
        messages = run_agent_turn(
            model=model,
            tools=_empty_registry(),
            messages=[{"role": "system", "content": "sys"}],
            cwd=str(tmp_path),
            suggested_skill_directories=frozenset(),
            skill_discovery_gate=True,
        )
        assert messages[-1] == {"role": "assistant", "content": "direct answer"}
        assert model.calls == 1

    def test_zero_discovery_nudges_once_then_accepts_final(self, tmp_path) -> None:
        _write_skill(tmp_path)
        model = ScriptedModel(
            [
                AgentStep(type="assistant", content="premature final"),
                AgentStep(type="assistant", content="final after nudge"),
            ]
        )
        messages = run_agent_turn(
            model=model,
            tools=_empty_registry(),
            messages=[{"role": "system", "content": "sys"}],
            cwd=str(tmp_path),
            suggested_skill_directories=frozenset({"demo"}),
            skill_discovery_gate=True,
        )
        assert model.calls == 2
        assert messages[-1] == {"role": "assistant", "content": "final after nudge"}
        nudges = [
            message
            for message in messages
            if message.get("role") == "user"
            and "possibly" in str(message.get("content"))
        ]
        assert len(nudges) == 1
        assert "demo" in nudges[0]["content"]

    def test_prior_discovery_skips_nudge(self, tmp_path) -> None:
        _write_skill(tmp_path)
        model = ScriptedModel(
            [
                AgentStep(
                    type="tool_calls",
                    calls=[
                        {
                            "id": "browse-1",
                            "toolName": "browse_skill_directory",
                            "input": {"directory_names": ["demo"]},
                        }
                    ],
                ),
                AgentStep(type="assistant", content="final after browsing"),
            ]
        )
        messages = run_agent_turn(
            model=model,
            tools=ToolRegistry([create_load_skill_tool(str(tmp_path))]),
            messages=[{"role": "system", "content": "sys"}],
            cwd=str(tmp_path),
            suggested_skill_directories=frozenset({"demo"}),
            skill_discovery_gate=True,
        )
        assert messages[-1] == {
            "role": "assistant",
            "content": "final after browsing",
        }
        assert not any(
            message.get("role") == "user"
            and "possibly" in str(message.get("content"))
            for message in messages
        )

    def test_second_final_accepted_without_second_nudge(self, tmp_path) -> None:
        _write_skill(tmp_path)
        model = ScriptedModel(
            [
                AgentStep(type="assistant", content="premature final"),
                AgentStep(type="assistant", content="still no discovery"),
            ]
        )
        messages = run_agent_turn(
            model=model,
            tools=_empty_registry(),
            messages=[{"role": "system", "content": "sys"}],
            cwd=str(tmp_path),
            suggested_skill_directories=frozenset({"demo"}),
            skill_discovery_gate=True,
        )
        # Advisory-once: the second final is accepted even without discovery.
        assert model.calls == 2
        assert messages[-1] == {"role": "assistant", "content": "still no discovery"}
