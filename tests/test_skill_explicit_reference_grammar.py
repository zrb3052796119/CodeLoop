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


def _route_catalog(prompt: str, *qualified_names: str):
    return SkillRouter().route(
        [
            {
                "name": qualified_name.rsplit("/", 1)[-1],
                "qualified_name": qualified_name,
                "description": f"Exercise {qualified_name}",
                "path": f"/tmp/{qualified_name.replace('/', '-')}/SKILL.md",
                "source": "project",
            }
            for qualified_name in qualified_names
        ],
        parse_intent(prompt),
        CapabilityRegistry(),
        top_k=max(1, len(qualified_names)),
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "$test please inspect the failures",
        "Use the test Skill to inspect the failures",
        "Load Skill named `test` before continuing",
        "使用 test 技能检查失败",
        "请使用 test Skill 检查失败",
        "test 用这个 Skill",
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
        "test 用这个 Skill 只是例子",
        "例如 test 用这个 Skill",
    ],
)
def test_ordinary_prose_never_grants_explicit_skill_authority(prompt: str) -> None:
    assert _required(prompt) == []


def test_unknown_reverse_form_skill_identifier_requires_clarification() -> None:
    prompt = "security/secret-and-injection-reveiw 用这个 Skill"
    routing = SkillRouter().route(
        [
            {
                "name": "secret-and-injection-review",
                "qualified_name": "security/secret-and-injection-review",
                "description": "Audit injection risks",
                "path": "/tmp/security/SKILL.md",
                "source": "project",
            }
        ],
        parse_intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.unresolved == (
        "security/secret-and-injection-reveiw",
    )
    assert routing.selected == []
    assert routing.decision_reason == "explicit-skill-unresolved"


@pytest.mark.parametrize(
    "prompt",
    [
        "security/secret-and-injection-review 用这个 Skill，只是例子",
        "security/secret-and-injection-review 用这个 Skill——只是例子",
        "文档里写着 security/secret-and-injection-review 用这个 Skill",
        "security/secret-and-injection-review 用这个 Skill 是什么意思？",
    ],
)
def test_reverse_form_meta_mentions_do_not_grant_authority(prompt: str) -> None:
    routing = _route_catalog(
        prompt,
        "security/secret-and-injection-review",
    )

    assert routing.authority_resolution.requested == ()
    assert required_skill_names_for_routing(routing) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "例子里写着 security/secret-and-injection-review 用这个 Skill",
        "文章说 security/secret-and-injection-review 用这个 Skill",
        "博客中提到 security/secret-and-injection-review 用这个 Skill",
        "文档里写着 security/secret-and-injection-review 用这个 Skill",
        "The article says use security/secret-and-injection-review Skill",
    ],
)
def test_reported_skill_invocations_are_inert_data(prompt: str) -> None:
    routing = _route_catalog(
        prompt,
        "security/secret-and-injection-review",
    )

    assert parse_intent(prompt).affirmed_operations == ()
    assert routing.authority_resolution.requested == ()
    assert routing.selected == []


def test_outer_edit_of_a_reported_invocation_remains_authoritative() -> None:
    prompt = (
        "文档里写着 security/secret-and-injection-review 用这个 Skill，"
        "只改这句措辞"
    )
    intent = parse_intent(prompt)
    routing = _route_catalog(
        prompt,
        "security/secret-and-injection-review",
    )

    assert [
        (operation.intent_type.value, operation.action_type.value)
        for operation in intent.affirmed_operations
    ] == [("document", "update")]
    assert routing.authority_resolution.requested == ()


def test_reverse_form_correction_only_authorizes_replacement() -> None:
    routing = _route_catalog(
        "alpha 用这个 Skill。不对，beta 用这个 Skill",
        "alpha",
        "beta",
    )

    assert routing.authority_resolution.requested == ("beta",)
    assert required_skill_names_for_routing(routing) == ["beta"]


def test_backticked_identifier_in_reverse_form_remains_authoritative() -> None:
    routing = _route_catalog(
        "`security/secret-and-injection-review` 用这个 Skill",
        "security/secret-and-injection-review",
    )

    assert routing.authority_resolution.requested == (
        "security/secret-and-injection-review",
    )
    assert required_skill_names_for_routing(routing) == [
        "security/secret-and-injection-review"
    ]


def test_unicode_ellipsis_english_correction_only_authorizes_replacement() -> None:
    routing = _route_catalog(
        "Use alpha Skill… actually, use beta Skill",
        "alpha",
        "beta",
    )

    assert routing.authority_resolution.requested == ("beta",)
    assert required_skill_names_for_routing(routing) == ["beta"]
