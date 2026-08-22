"""Authority grammar for explicit Skill invocations."""
from __future__ import annotations

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter, required_skill_names_for_routing


def _required(prompt: str) -> list[str]:
    routing = SkillRouter().route(
        [
            {
                "name": "test",
                "qualified_name": "test",
                "description": "Run project tests",
                "path": "/tmp/test/SKILL.md",
                "source": "project",
                "keywords": ["test"],
            }
        ],
        parse_intent(prompt),
        CapabilityRegistry(),
        top_k=1,
    )
    return required_skill_names_for_routing(routing)


@pytest.mark.parametrize(
    "prompt",
    [
        "$test please inspect the failures",
        "Use the test Skill to inspect the failures",
        "Load Skill named `test` before continuing",
        "使用 test 技能检查失败",
        "请使用 test Skill 检查失败",
        "test",
    ],
)
def test_deliberate_skill_invocation_is_required(prompt: str) -> None:
    assert _required(prompt) == ["test"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Please test this code",
        "Run tests and report failures",
        "The `test` function failed",
        "Use test data for this fixture",
        "审查测试和技能路由",
        "Do not use the test Skill; explain the result without it",
        "Never load Skill named test for this request",
        "不要使用 test 技能检查失败",
        "请勿调用 test Skill",
    ],
)
def test_ordinary_prose_never_grants_explicit_skill_authority(prompt: str) -> None:
    assert _required(prompt) == []
