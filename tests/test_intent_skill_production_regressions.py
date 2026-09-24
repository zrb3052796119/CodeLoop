"""Offline regressions joining intent parsing, capabilities, and Skill routing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import minicode.skills as skills_module
import minicode.tools as tools_module
from minicode.capability_registry import (
    CapabilityRegistry,
    build_tool_capability_snapshot,
)
from minicode.skill_router import SkillRouter
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.skills import discover_skills
from minicode.tools import create_default_tool_registry

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "agent_quality"
    / "intent-skill-production-regressions.json"
)
_SUITE = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_CASES = _SUITE["cases"]


def _operation_projection(intent) -> list[dict[str, str]]:
    return [
        {
            "intent": operation.intent_type.value,
            "action": operation.action_type.value,
            "polarity": operation.polarity.value,
        }
        for operation in intent.operations
    ]


def _catalog_projection(skill) -> dict[str, object]:
    return {
        field: getattr(skill, field)
        for field in _SUITE["catalogSnapshot"]["routingFields"]
    }


@pytest.fixture(scope="module")
def core_capability_snapshot(tmp_path_factory: pytest.TempPathFactory):
    """Build the real core registry while making Skill discovery irrelevant."""
    workspace = tmp_path_factory.mktemp("intent-skill-regressions")
    with (
        patch.object(tools_module, "discover_skills", return_value=[]),
        patch.object(
            tools_module,
            "observe_skill_catalog_safely",
            return_value=None,
        ),
    ):
        tools = create_default_tool_registry(
            str(workspace),
            {"toolProfile": "core"},
            include_mcp=False,
        )
    try:
        assert tools.get_skills() == []
        yield build_tool_capability_snapshot(tools)
    finally:
        tools.dispose()


def test_fixture_freezes_complete_project_skill_routing_catalog() -> None:
    snapshot = _SUITE["catalogSnapshot"]
    skills = snapshot["skills"]
    expected_fields = set(snapshot["routingFields"])

    assert _SUITE["schemaVersion"] == 1
    assert snapshot["source"] == "project"
    assert snapshot["skillCount"] == 16
    assert len(skills) == snapshot["skillCount"]
    assert len({skill["qualified_name"] for skill in skills}) == len(skills)
    assert all(set(skill) == expected_fields for skill in skills)
    assert all(skill["source"] == "project" for skill in skills)
    assert all("path" not in skill and "content" not in skill for skill in skills)


def test_workspace_project_skill_catalog_matches_frozen_routing_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the frozen regression fixture bound to workspace project Skills."""
    monkeypatch.setattr(skills_module, "_home_dir", lambda: tmp_path)
    repository = Path(__file__).resolve().parents[1]
    discovered = [
        skill
        for skill in discover_skills(repository)
        if skill.source == "project"
    ]

    assert sorted(
        (_catalog_projection(skill) for skill in discovered),
        key=lambda skill: str(skill["qualified_name"]),
    ) == sorted(
        _SUITE["catalogSnapshot"]["skills"],
        key=lambda skill: skill["qualified_name"],
    )


def test_fixture_expectations_reference_only_snapshotted_skills() -> None:
    catalog_names = {
        skill["qualified_name"]
        for skill in _SUITE["catalogSnapshot"]["skills"]
    }
    assert len(_CASES) == 10
    assert len({case["category"] for case in _CASES}) == len(_CASES)

    for case in _CASES:
        top = case["expectedTopSkill"]
        allowed = set(case["allowedSkills"])
        forbidden = set(case["forbiddenSkills"])
        assert allowed <= catalog_names
        assert forbidden <= catalog_names
        assert allowed.isdisjoint(forbidden)
        if top is None:
            assert not allowed
        else:
            assert top in allowed


def test_core_capability_snapshot_matches_frozen_fixture(
    core_capability_snapshot: CapabilityRegistry,
) -> None:
    actual = []
    for name in sorted(core_capability_snapshot.list_all()):
        capability = core_capability_snapshot.get(name)
        assert capability is not None
        actual.append(
            {
                "name": name,
                "domain": capability.metadata.domain.value,
                "scope": capability.metadata.scope.value,
            }
        )

    assert actual == sorted(
        _SUITE["coreCapabilities"],
        key=lambda item: item["name"],
    )


def test_project_skill_scopes_cover_every_declared_tool_capability(
    core_capability_snapshot: CapabilityRegistry,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Skill must declare every effect class reachable through its tools."""
    monkeypatch.setattr(skills_module, "_home_dir", lambda: tmp_path)
    repository = Path(__file__).resolve().parents[1]
    project_skills = [
        skill
        for skill in discover_skills(repository)
        if skill.source == "project"
    ]
    violations: list[str] = []

    for skill in project_skills:
        declared_scopes = set(skill.scopes)
        for tool_name in skill.tools:
            capability = core_capability_snapshot.get(tool_name)
            if capability is None:
                violations.append(
                    f"{skill.qualified_name}: unknown tool {tool_name!r}"
                )
                continue
            required_scope = capability.metadata.scope.value
            if required_scope not in declared_scopes:
                violations.append(
                    f"{skill.qualified_name}: tool {tool_name!r} requires "
                    f"scope {required_scope!r}, declared={sorted(declared_scopes)!r}"
                )

    assert len(project_skills) == _SUITE["catalogSnapshot"]["skillCount"]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
def test_production_intent_and_skill_routing_regression(
    case: dict,
    core_capability_snapshot: CapabilityRegistry,
) -> None:
    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=case["prompt"],
            skills=_SUITE["catalogSnapshot"]["skills"],
            registry=core_capability_snapshot,
            top_k=_SUITE["topK"],
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
    )
    intent = decision.intent
    routing = decision.routing
    selected = [skill.qualified_name for skill in routing.selected]
    allowed = set(case["allowedSkills"])
    forbidden = set(case["forbiddenSkills"])
    failures: list[str] = []

    operations = _operation_projection(intent)
    if operations != case["expectedOperations"]:
        failures.append(
            f"operations expected={case['expectedOperations']!r} actual={operations!r}"
        )

    actual_top = selected[0] if selected else None
    if actual_top != case["expectedTopSkill"]:
        failures.append(
            f"top expected={case['expectedTopSkill']!r} actual={actual_top!r}"
        )

    unexpected = set(selected) - allowed
    if unexpected:
        failures.append(f"selected skills outside allow-list={sorted(unexpected)!r}")

    forbidden_selected = set(selected) & forbidden
    if forbidden_selected:
        failures.append(f"forbidden skills selected={sorted(forbidden_selected)!r}")

    catalog_by_name = {
        skill["qualified_name"]: skill
        for skill in _SUITE["catalogSnapshot"]["skills"]
    }
    for routed_skill in routing.selected:
        contract = set(
            catalog_by_name[routed_skill.qualified_name]["supported_operations"]
        )
        for operation_index in routed_skill.operation_indexes:
            operation = intent.operations[operation_index]
            actual_operation = (
                f"{operation.intent_type.value}.{operation.action_type.value}"
            )
            if contract and actual_operation not in contract:
                failures.append(
                    f"{routed_skill.qualified_name} claimed operation "
                    f"{operation_index}={actual_operation!r} outside contract="
                    f"{sorted(contract)!r}"
                )

    expected_outcome = "route" if case["expectedTopSkill"] else "no_skill"
    if decision.outcome != expected_outcome:
        failures.append(
            f"outcome expected={expected_outcome!r} actual={decision.outcome!r}"
        )
    expected_prompt_mode = (
        "routed" if case["expectedTopSkill"] else "inventory_names"
    )
    if decision.prompt_mode != expected_prompt_mode:
        failures.append(
            "prompt mode expected="
            f"{expected_prompt_mode!r} actual={decision.prompt_mode!r}"
        )

    assert not failures, (
        f"{case['id']}: " + "; ".join(failures) + f"; selected={selected!r}"
    )
