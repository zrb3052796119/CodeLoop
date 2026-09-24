"""Regression coverage for SkillRouter authority and routing identities."""

from __future__ import annotations

from typing import Any

import pytest

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentType,
    ParsedIntent,
    parse_intent,
)
from minicode.skill_router import SkillRouter, required_skill_names_for_routing

_CONTENT_DIGEST = "a" * 64
_ROUTING_DIGEST = "b" * 64


def _skill(
    name: str,
    *,
    qualified_name: str | None = None,
    description: str = "A focused workflow",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "qualified_name": qualified_name or name,
        "description": description,
        "path": f"/tmp/{qualified_name or name}/SKILL.md",
        "source": "project",
        **extra,
    }


def _intent(
    raw_input: str,
    *,
    keywords: list[str] | None = None,
) -> ParsedIntent:
    return ParsedIntent(
        raw_input=raw_input,
        intent_type=IntentType.REVIEW,
        action_type=ActionType.ANALYZE,
        confidence=0.9,
        entities={},
        keywords=list(keywords or []),
    )


def _registry(
    *capabilities: tuple[str, CapabilityDomain, CapabilityScope],
) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, domain, scope in capabilities:
        registry.register(
            CapabilityMetadata(
                name=name,
                domain=domain,
                scope=scope,
                description=f"{name} capability",
            ),
            lambda **_: None,
        )
    return registry


@pytest.mark.parametrize(
    "prompt",
    [
        "Do not ever use the test Skill",
        "Do not, however, use the test Skill",
        "Do not yet use the test Skill",
        "Never again load Skill named test",
        "Please do not under any circumstances invoke the test Skill",
        "不要再使用 test 技能",
        "不要随后使用 test 技能",
        "请不要在本次任务中调用 test Skill",
    ],
)
def test_negation_modifiers_never_grant_explicit_authority(prompt: str) -> None:
    routing = SkillRouter().route(
        [_skill("test")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Never forget to use alpha skill.",
        "Never hesitate to use alpha skill.",
        "Do not fail to use alpha skill.",
        "Don't fail to use alpha skill.",
        "Don't neglect to use alpha skill.",
        "You must not forget to use alpha skill.",
        "You mustn't forget to invoke alpha skill.",
        "Be sure not to forget to use alpha skill.",
        "Don't forget: use alpha skill.",
        "Without fail, use alpha skill.",
        "切勿忘记使用 alpha 技能。",
        "不能忘记使用 alpha 技能。",
    ],
)
def test_positive_negation_idioms_retain_explicit_skill_authority(
    prompt: str,
) -> None:
    routing = SkillRouter().route(
        [_skill("alpha")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == ["alpha"]


def test_ambiguous_bare_name_grants_no_authority_but_qualified_name_does() -> None:
    skills = [
        _skill("test", qualified_name="alpha/test"),
        _skill("test", qualified_name="beta/test"),
    ]

    ambiguous = SkillRouter().route(
        skills,
        _intent("$test inspect the failure"),
        CapabilityRegistry(),
    )
    qualified = SkillRouter().route(
        skills,
        _intent("$alpha/test inspect the failure"),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(ambiguous) == []
    assert required_skill_names_for_routing(qualified) == ["alpha/test"]


def test_ambiguous_bare_name_denial_vetoes_every_owner() -> None:
    skills = [
        _skill(
            "test",
            qualified_name="alpha/test",
            description="Review code and inspect tests",
            keywords=["review", "code"],
        ),
        _skill(
            "test",
            qualified_name="beta/test",
            description="Review code and inspect tests",
            keywords=["review", "code"],
        ),
    ]

    routing = SkillRouter().route(
        skills,
        _intent(
            "Do not use the test Skill; review code",
            keywords=["review", "code"],
        ),
        CapabilityRegistry(),
    )

    assert routing.used_fallback
    assert routing.selected == []


@pytest.mark.parametrize(
    "prompt",
    [
        "I asked you not to use alpha skill; review code",
        "You are not allowed to use alpha skill; review code",
        "Please remember not to use alpha skill; review code",
        "I want you not to invoke alpha skill; review code",
        "Do everything except use alpha skill; review code",
        "Don't select the alpha skill; review code",
        "Avoid the alpha skill; review code",
        "Do anything but use alpha skill; review code",
        "不使用 alpha 技能；审查代码",
        "勿使用 alpha 技能；审查代码",
        "不能使用 alpha 技能；审查代码",
        "不可使用 alpha 技能；审查代码",
        "不应使用 alpha 技能；审查代码",
        "避免使用 alpha 技能；审查代码",
        "不准使用 alpha 技能；审查代码",
        "严禁使用 alpha 技能；审查代码",
        "拒绝使用 alpha 技能；审查代码",
        "不允许使用 alpha 技能；审查代码",
        "Stop using alpha skill; review code",
        "Cease using alpha skill; review code",
        "Discontinue using alpha skill; review code",
        "No longer use alpha skill; review code",
    ],
)
def test_governed_skill_denial_never_becomes_authority_or_inference(
    prompt: str,
) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "alpha",
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            )
        ],
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert routing.used_fallback
    assert routing.selected == []
    assert required_skill_names_for_routing(routing) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Do not use the alpha or beta skills; review code.",
        "不要使用 alpha 或 beta 技能；审查代码。",
    ],
)
def test_skill_list_denial_vetoes_every_named_candidate(prompt: str) -> None:
    skills = [
        _skill(
            name,
            description="Review code and inspect implementation",
            keywords=["review", "code"],
        )
        for name in ("alpha", "beta")
    ]

    routing = SkillRouter().route(
        skills,
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert routing.used_fallback
    assert routing.selected == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Alpha skill mustn't be invoked; review code.",
        "Alpha skill must not be used; review code.",
        "Alpha skill should not be loaded; review code.",
        "Alpha skill cannot be invoked; review code.",
        "Alpha skill must never be selected; review code.",
        "Alpha skill is not allowed to be used; review code.",
        "Alpha skill is forbidden; review code.",
        "Alpha skill is prohibited; review code.",
        "Alpha skill is disallowed; review code.",
        "Alpha skill is not permitted to be used; review code.",
    ],
)
def test_passive_skill_denial_is_an_absolute_veto(prompt: str) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "alpha",
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            )
        ],
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert routing.used_fallback
    assert routing.selected == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Forbid alpha skill; review code.",
        "Prohibit alpha skill; review code.",
        "Ban alpha skill; review code.",
    ],
)
def test_active_forbiddance_is_an_absolute_skill_veto(prompt: str) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "alpha",
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            )
        ],
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert routing.used_fallback
    assert routing.selected == []


def test_unlabelled_qualified_skill_denial_is_fail_closed() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "secure-review",
                qualified_name="project:secure-review",
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            )
        ],
        _intent(
            "Do not use project:secure-review; review code.",
            keywords=["review", "code"],
        ),
        CapabilityRegistry(),
    )

    assert routing.used_fallback
    assert routing.selected == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Use alpha and beta skills.",
        "Use alpha skill and beta skill.",
        "Use the alpha, beta skills.",
        "使用 alpha 和 beta 技能。",
        "使用 alpha、beta 技能。",
    ],
)
def test_one_positive_action_expands_over_a_labelled_skill_list(
    prompt: str,
) -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["alpha", "beta"]
    assert required_skill_names_for_routing(routing) == ["alpha", "beta"]


def test_oxford_comma_skill_list_grants_only_its_named_members() -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta"), _skill("gamma")],
        _intent("Use alpha, beta, and gamma skills."),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == [
        "alpha",
        "beta",
        "gamma",
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "Review code without alpha skill.",
        "Use beta skill to review code without alpha skill.",
    ],
)
def test_without_named_skill_is_an_absolute_candidate_veto(prompt: str) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "alpha",
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            ),
            _skill("beta"),
        ],
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert all(item.name != "alpha" for item in routing.selected)


def test_without_mutation_scope_ends_before_explicit_while_using_skill() -> None:
    prompt = "Review code without modifying it while using alpha skill."
    routing = SkillRouter().route(
        [_skill("alpha")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == ["alpha"]


def test_tail_skill_denial_vetoes_an_inferred_candidate() -> None:
    prompt = "使用 alpha 和 beta 技能，但不要 gamma 技能；审查代码"
    routing = SkillRouter().route(
        [
            _skill(
                name,
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            )
            for name in ("alpha", "beta", "gamma")
        ],
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["alpha", "beta"]
    assert required_skill_names_for_routing(routing) == ["alpha", "beta"]


def test_quoted_skill_invocation_is_discussion_not_authority() -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent("Explain the phrase 'Use alpha and beta skills'."),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Use alpha skill. Actually, use beta skill instead.",
        "Use alpha skill —no, use beta skill instead.",
        "使用 alpha 技能。不对，改用 beta 技能。",
    ],
)
def test_whole_list_correction_revokes_the_superseded_skill(prompt: str) -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["beta"]
    assert required_skill_names_for_routing(routing) == ["beta"]


def test_chinese_whole_list_gaiwei_replaces_prior_skills() -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta"), _skill("gamma")],
        _intent("使用 alpha 和 beta 技能，改为 gamma 技能。"),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["gamma"]
    assert required_skill_names_for_routing(routing) == ["gamma"]


def test_partial_replacement_preserves_unreplaced_skill() -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta"), _skill("gamma")],
        _intent(
            "Use alpha and beta skills. Actually, replace beta with gamma skill."
        ),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["alpha", "gamma"]
    assert required_skill_names_for_routing(routing) == ["alpha", "gamma"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Use alpha skill, do not use beta skill.",
        "Use alpha skill but do not use beta skill.",
        "Use alpha skill; do not use beta.",
        "使用 alpha 技能，不要使用 beta 技能。",
        "使用 alpha 技能但不要使用 beta 技能。",
    ],
)
def test_later_skill_denial_does_not_revoke_an_earlier_skill(
    prompt: str,
) -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["alpha"]
    assert required_skill_names_for_routing(routing) == ["alpha"]


@pytest.mark.parametrize(
    ("prompt", "expected_required"),
    [
        ("Use alpha skill, not beta skill.", ["alpha"]),
        ("Use alpha skill but not beta skill.", ["alpha"]),
        ("Use alpha and not beta skills.", ["alpha"]),
        ("Use alpha skill instead of beta skill.", ["alpha"]),
        ("Use alpha skill rather than beta skill.", ["alpha"]),
        ("Use all skills except beta; review code.", []),
        ("使用 alpha 技能，不是 beta 技能。", ["alpha"]),
        ("使用 alpha 而非 beta 技能。", ["alpha"]),
    ],
)
def test_skill_list_correction_vetoes_only_the_excluded_candidate(
    prompt: str,
    expected_required: list[str],
) -> None:
    routing = SkillRouter().route(
        [
            _skill("alpha"),
            _skill(
                "beta",
                description="Review code and inspect implementation",
                keywords=["review", "code"],
            ),
        ],
        _intent(prompt, keywords=["review", "code"]),
        CapabilityRegistry(),
    )

    assert all(item.name != "beta" for item in routing.selected)
    assert required_skill_names_for_routing(routing) == expected_required


@pytest.mark.parametrize(
    "prompt",
    [
        "Use alpha skill to explain beta skill.",
        "Use alpha skill to compare beta skill.",
        "Use alpha skill to document beta skill.",
        "Use alpha skill to discuss beta skill.",
        "Use alpha skill to evaluate beta skill.",
        "使用 alpha 技能解释 beta 技能。",
        "使用 alpha 技能对比 beta 技能。",
    ],
)
def test_discussed_skill_is_not_promoted_by_list_expansion(prompt: str) -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == ["alpha"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Do not use alpha skill and use beta skill",
        "Do not use alpha skill, use beta skill",
        "Avoid alpha skill and use beta skill",
        "不要使用 alpha 技能并使用 beta 技能",
        "不要使用 alpha 技能，使用 beta 技能",
        "Rather than use alpha skill, use beta skill",
    ],
)
def test_new_explicit_action_ends_prior_skill_denial_scope(prompt: str) -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["beta"]
    assert required_skill_names_for_routing(routing) == ["beta"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Do not modify code, use alpha skill to review it.",
        "Do not run tests and use alpha skill to review code.",
        "Do not write docs but use alpha skill to review.",
        "Do not use beta or use alpha skill to review.",
        "不要修改代码，使用 alpha 技能审查。",
        "不要运行测试并使用 alpha 技能审查。",
        "请勿调用 beta、改用 alpha 技能。",
    ],
)
def test_unrelated_negated_action_does_not_revoke_new_skill_request(
    prompt: str,
) -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert [item.name for item in routing.selected] == ["alpha"]
    assert required_skill_names_for_routing(routing) == ["alpha"]


def test_prior_user_skill_denial_persists_for_contextual_continuation() -> None:
    prior = "Do not use router-review skill; review routing architecture."
    context = IntentContext.from_messages(
        [{"role": "user", "content": prior}],
        previous_intent=parse_intent(prior),
    )
    intent = parse_intent("continue", context=context)

    routing = SkillRouter().route(
        [
            _skill(
                "router-review",
                description="Review routing architecture and Skill routing",
                keywords=["review", "routing", "architecture"],
            )
        ],
        intent,
        CapabilityRegistry(),
    )

    assert intent.routing_directive_context == (prior,)
    assert routing.used_fallback
    assert routing.selected == []


def test_assistant_skill_denial_is_never_inherited_as_user_authority() -> None:
    prior = "Review routing architecture."
    context = IntentContext.from_messages(
        [
            {"role": "user", "content": prior},
            {
                "role": "assistant",
                "content": "Do not use router-review skill.",
            },
        ],
        previous_intent=parse_intent(prior),
    )
    intent = parse_intent("continue", context=context)

    routing = SkillRouter().route(
        [
            _skill(
                "router-review",
                description="Review routing architecture and Skill routing",
                keywords=["review", "routing", "architecture"],
            )
        ],
        intent,
        CapabilityRegistry(),
    )

    assert intent.routing_directive_context == (prior,)
    assert [item.name for item in routing.selected] == ["router-review"]
    assert routing.selected[0].explicitly_requested is False


def test_current_explicit_request_restores_a_previously_denied_skill() -> None:
    prior = "Do not use router-review skill; review routing architecture."
    context = IntentContext.from_messages(
        [{"role": "user", "content": prior}],
        previous_intent=parse_intent(prior),
    )
    intent = parse_intent(
        "Use router-review skill and continue the review.",
        context=context,
    )

    routing = SkillRouter().route(
        [_skill("router-review")],
        intent,
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == ["router-review"]
    assert routing.selected[0].explicitly_requested is True


def test_hyphen_and_underscore_identifiers_do_not_share_authority() -> None:
    skills = [
        _skill("review-flow"),
        _skill("review_flow"),
    ]

    hyphenated = SkillRouter().route(
        skills,
        _intent("$review-flow inspect the module"),
        CapabilityRegistry(),
    )
    underscored = SkillRouter().route(
        skills,
        _intent("$review_flow inspect the module"),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(hyphenated) == ["review-flow"]
    assert required_skill_names_for_routing(underscored) == ["review_flow"]


def test_positive_clause_after_a_negated_invocation_retains_authority() -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent("Do not use the alpha Skill, but use the beta Skill"),
        CapabilityRegistry(),
    )

    assert required_skill_names_for_routing(routing) == ["beta"]


def test_top_k_limits_only_inferred_skills() -> None:
    skills = [
        *[
            _skill(f"forced-{index}", description="A forced workflow")
            for index in range(3)
        ],
        *[
            _skill(
                f"inferred-{index}",
                description="Review authentication behavior",
                keywords=["authentication"],
            )
            for index in range(3)
        ],
    ]
    routing = SkillRouter().route(
        skills,
        _intent(
            "$forced-0 $forced-1 $forced-2 review authentication",
            keywords=["authentication"],
        ),
        CapabilityRegistry(),
        top_k=1,
    )

    assert required_skill_names_for_routing(routing) == [
        "forced-0",
        "forced-1",
        "forced-2",
    ]
    assert len([skill for skill in routing.selected if not skill.explicitly_requested]) == 1


def test_tool_metadata_is_deduplicated_bounded_and_cannot_veto_authority() -> None:
    declared_tools = ["run_command"] * 200 + [
        f"unknown_tool_{index}" for index in range(100)
    ]
    routing = SkillRouter().route(
        [_skill("danger", tools=declared_tools)],
        _intent("$danger inspect this module"),
        _registry(
            (
                "run_command",
                CapabilityDomain.EXECUTION,
                CapabilityScope.DESTRUCTIVE,
            )
        ),
    )

    assert required_skill_names_for_routing(routing) == ["danger"]
    assert routing.selected[0].tools[0] == "run_command"
    assert len(routing.selected[0].tools) == 64
    assert routing.tool_affinity == {"danger": -1.0}


class _CapturingMatcher:
    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []
        self.call_context: dict[str, Any] | None = None

    def similarities(
        self,
        _query: str,
        pairs: list[tuple[str, str]],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> list[float]:
        self.pairs = pairs
        self.call_context = call_context
        return [0.0 for _ in pairs]


class _CapturingFeedback:
    def __init__(self) -> None:
        self.query: dict[str, Any] | None = None

    def decision(self, **query: Any) -> None:
        self.query = query


def test_routing_identity_drives_embedding_feedback_and_projection() -> None:
    matcher = _CapturingMatcher()
    feedback = _CapturingFeedback()
    call_context = {"run_id": "run-123"}
    routing = SkillRouter(
        embedding_matcher=matcher,
        routing_feedback=feedback,
    ).route(
        [
            _skill(
                "auth-review",
                description="Review authentication behavior",
                keywords=["authentication"],
                content_digest=_CONTENT_DIGEST,
                routing_identity_digest=_ROUTING_DIGEST,
            )
        ],
        _intent("review authentication", keywords=["authentication"]),
        CapabilityRegistry(),
        call_context=call_context,
    )

    selected = routing.selected[0]
    assert matcher.pairs[0][0] == _ROUTING_DIGEST
    assert matcher.call_context is call_context
    assert feedback.query is not None
    assert feedback.query["content_digest"] == _CONTENT_DIGEST
    assert feedback.query["routing_identity_digest"] == _ROUTING_DIGEST
    assert selected.content_digest == _CONTENT_DIGEST
    assert selected.routing_identity_digest == _ROUTING_DIGEST
    assert selected.to_dict()["routing_identity_digest"] == _ROUTING_DIGEST


def test_routing_identity_remains_compatible_with_legacy_feedback_provider() -> None:
    class LegacyFeedback:
        def __init__(self) -> None:
            self.content_digest = ""

        def decision(
            self,
            *,
            qualified_name: str,
            source: str,
            directory: str,
            content_digest: str,
            intent_type: str,
            action_type: str,
        ) -> None:
            del qualified_name, source, directory, intent_type, action_type
            self.content_digest = content_digest

    feedback = LegacyFeedback()
    SkillRouter(routing_feedback=feedback).route(
        [
            _skill(
                "auth-review",
                description="Review authentication behavior",
                keywords=["authentication"],
                content_digest=_CONTENT_DIGEST,
                routing_identity_digest=_ROUTING_DIGEST,
            )
        ],
        _intent("review authentication", keywords=["authentication"]),
        CapabilityRegistry(),
    )

    assert feedback.content_digest == _ROUTING_DIGEST


def test_call_context_keeps_legacy_matchers_compatible() -> None:
    class LegacyMatcher:
        def similarities(self, _query: str, pairs: list[tuple[str, str]]) -> list[float]:
            return [0.0 for _ in pairs]

    routing = SkillRouter(embedding_matcher=LegacyMatcher()).route(
        [_skill("test")],
        _intent("$test"),
        CapabilityRegistry(),
        call_context={"run_id": "run-123"},
    )

    assert required_skill_names_for_routing(routing) == ["test"]


def test_matcher_internal_type_error_is_not_mistaken_for_legacy_signature() -> None:
    class BrokenMatcher:
        def similarities(
            self,
            _query: str,
            _pairs: list[tuple[str, str]],
            *,
            call_context: dict[str, Any] | None = None,
        ) -> list[float]:
            del call_context
            raise TypeError("matcher implementation failed")

    with pytest.raises(TypeError, match="matcher implementation failed"):
        SkillRouter(embedding_matcher=BrokenMatcher()).route(
            [_skill("test")],
            _intent("$test"),
            CapabilityRegistry(),
            call_context={"run_id": "run-123"},
        )


def test_unresolved_pure_negation_cannot_activate_unknown_alias_routing() -> None:
    intent = parse_intent("Do not touch the database query settings.")

    routing = SkillRouter().route(
        [
            _skill(
                "database-tuning",
                description="Optimize database queries and SQL settings",
            )
        ],
        intent,
        CapabilityRegistry(),
    )

    assert intent.affirmed_operations == ()
    assert routing.selected == []
    assert routing.used_fallback


def test_quoted_identifier_in_control_grammar_remains_a_denial() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "alpha",
                description="Review code architecture and security",
                keywords=["code", "review"],
            )
        ],
        _intent(
            "Do not use the 'alpha' skill; review code.",
            keywords=["review", "code"],
        ),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.denied == ("alpha",)
    assert routing.selected == []


def test_instead_of_rejects_first_skill_and_requests_second() -> None:
    routing = SkillRouter().route(
        [_skill("alpha"), _skill("beta")],
        _intent("Instead of using alpha skill, use beta skill."),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.denied == ("alpha",)
    assert routing.authority_resolution.requested == ("beta",)
    assert required_skill_names_for_routing(routing) == ["beta"]


@pytest.mark.parametrize("verb", ["explain", "analyze", "search"])
def test_new_skill_request_resets_prior_negated_action(verb: str) -> None:
    routing = SkillRouter().route(
        [_skill("alpha")],
        _intent(f"Do not {verb} the code, use alpha skill to review it."),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.denied == ()
    assert required_skill_names_for_routing(routing) == ["alpha"]


def test_denied_skill_never_crosses_embedding_boundary() -> None:
    matcher = _CapturingMatcher()

    SkillRouter(embedding_matcher=matcher).route(
        [
            _skill(
                "alpha",
                description="Review alpha authentication behavior",
            ),
            _skill(
                "beta",
                description="Review beta authentication behavior",
            ),
        ],
        parse_intent(
            "Do not use alpha skill; review authentication."
        ),
        CapabilityRegistry(),
    )

    pair_texts = [text for _digest, text in matcher.pairs]
    assert not any("alpha" in text for text in pair_texts)
    assert any("beta" in text for text in pair_texts)


def test_colon_is_part_of_the_explicit_identifier_boundary() -> None:
    unresolved = SkillRouter().route(
        [_skill("alpha")],
        _intent("$alpha:bogus"),
        CapabilityRegistry(),
    )
    qualified = SkillRouter().route(
        [
            _skill("project"),
            _skill(
                "secure-review",
                qualified_name="project:secure-review",
            ),
        ],
        _intent("$project:secure-review"),
        CapabilityRegistry(),
    )

    assert unresolved.authority_resolution.requested == ()
    assert unresolved.authority_resolution.unresolved == ("alpha:bogus",)
    assert unresolved.selected == []
    assert qualified.authority_resolution.requested == (
        "project:secure-review",
    )
    assert required_skill_names_for_routing(qualified) == [
        "project:secure-review"
    ]


def test_identifier_containing_skills_directory_is_not_truncated() -> None:
    """``使用 code-skills/minicode-study`` used to backtrack the identifier
    to ``code-`` so the trailing ``skill`` literal could eat ``skills/``'s
    prefix; the phantom unresolved token then vetoed the explicit request."""

    catalog = [
        _skill("minicode-study", qualified_name="code-skills/minicode-study"),
        _skill("other"),
    ]

    routing = SkillRouter().route(
        catalog,
        parse_intent("请使用 code-skills/minicode-study，带我看记忆系统的层次"),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.requested == (
        "code-skills/minicode-study",
    )
    assert routing.authority_resolution.unresolved == ()
    assert routing.selected[0].qualified_name == "code-skills/minicode-study"
    assert required_skill_names_for_routing(routing) == [
        "code-skills/minicode-study"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "这一次不要加载或使用任何 Skill。直接给我一个 Python 表达式。",
        "不要加载或使用任何技能",
        "don't use or load any skills for this turn",
        "never invoke or apply any skill",
        "不要任何技能",
    ],
)
def test_global_skill_denial_covers_coordinated_verbs(text: str) -> None:
    routing = SkillRouter().route(
        [_skill("alpha")],
        parse_intent(text),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.deny_all is True
    assert routing.selected == []
