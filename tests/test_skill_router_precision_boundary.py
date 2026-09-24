"""Precision boundaries for intent-to-Skill routing.

These cases exercise the router with canonical structured intents directly so
parser recall changes cannot hide cross-layer admission bugs.
"""

from __future__ import annotations

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import (
    ActionType,
    IntentOperation,
    IntentPolarity,
    IntentType,
    ParsedIntent,
)
from minicode.skill_router import SkillRouter
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)


def _skill(
    qualified_name: str,
    description: str,
    *,
    keywords: tuple[str, ...] = (),
    supported_operations: tuple[str, ...] = (),
    activation_terms: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "name": qualified_name.rsplit("/", 1)[-1],
        "qualified_name": qualified_name,
        "description": description,
        "path": f"/tmp/{qualified_name}/SKILL.md",
        "source": "project",
        "keywords": list(keywords),
        "supported_operations": list(supported_operations),
        "activation_terms": list(activation_terms),
    }


def _operation(
    intent_type: IntentType,
    action_type: ActionType,
    clause: str,
    *keywords: str,
    polarity: IntentPolarity = IntentPolarity.AFFIRMED,
    evidence_codes: tuple[str, ...] = (),
) -> IntentOperation:
    return IntentOperation(
        intent_type=intent_type,
        action_type=action_type,
        polarity=polarity,
        confidence=0.92,
        clause=clause,
        keywords=tuple(keywords),
        evidence_codes=evidence_codes,
    )


def _intent(
    prompt: str,
    *operations: IntentOperation,
) -> ParsedIntent:
    affirmed = [
        (index, operation)
        for index, operation in enumerate(operations)
        if operation.polarity is IntentPolarity.AFFIRMED
    ]
    primary_index = affirmed[0][0] if affirmed else None
    primary = affirmed[0][1] if affirmed else None
    return ParsedIntent(
        raw_input=prompt,
        intent_type=(primary.intent_type if primary else IntentType.UNKNOWN),
        action_type=(primary.action_type if primary else ActionType.UNKNOWN),
        confidence=(primary.confidence if primary else 0.0),
        operations=tuple(operations),
        primary_operation_index=primary_index,
        semantic_query=prompt,
    )


def test_unknown_intent_without_operations_cannot_infer_a_skill() -> None:
    prompt = "audit authentication permission security"
    skill = _skill(
        "review/auth-inspection",
        "Audit authentication permission security",
        keywords=("audit", "authentication", "permission", "security"),
    )

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            skills=(skill,),
            registry=CapabilityRegistry(),
            parsed_intent=_intent(prompt),
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
    )

    assert decision.intent.operations == ()
    assert decision.routing.selected == []
    assert decision.routing.candidate_count == 0
    assert decision.outcome == "no_skill"
    assert decision.prompt_mode == "inventory_names"


def test_explicit_skill_without_operations_does_not_admit_inferred_peers() -> None:
    prompt = (
        "USE security/exact-review SKILL. "
        "audit authentication permission security"
    )
    exact = _skill("security/exact-review", "Exact requested workflow")
    inferred = _skill(
        "review/auth-inspection",
        "Audit authentication permission security",
        keywords=("audit", "authentication", "permission", "security"),
    )

    routing = SkillRouter().route(
        [exact, inferred],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.requested == (
        "security/exact-review",
    )
    assert [skill.qualified_name for skill in routing.selected] == [
        "security/exact-review"
    ]
    assert routing.selected[0].explicitly_requested is True
    assert routing.inferred_candidates == ()


def test_unknown_chinese_skill_label_requires_clarification() -> None:
    prompt = "用 securty-audit skill 查 auth 越权"
    known = _skill(
        "security/secret-and-injection-review",
        "Audit authentication permissions and security",
        keywords=("auth", "permission", "security"),
        supported_operations=("review.analyze",),
    )
    intent = _intent(
        prompt,
        _operation(
            IntentType.REVIEW,
            ActionType.ANALYZE,
            prompt,
            "auth",
            "permission",
            "review",
        ),
    )

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            skills=(known,),
            registry=CapabilityRegistry(),
            parsed_intent=intent,
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
    )

    assert decision.routing.authority_resolution.unresolved == (
        "securty-audit",
    )
    assert decision.routing.selected == []
    assert decision.outcome == "clarify"
    assert decision.outcome_reason == "explicit-skill-unresolved"
    assert decision.prompt_mode == "inventory_names"


def test_polysemous_database_word_does_not_make_restaurant_search_engineering() -> None:
    prompt = "find me a database-themed restaurant nearby"
    intent = _intent(
        prompt,
        _operation(
            IntentType.SEARCH,
            ActionType.READ,
            prompt,
            "find",
            "database-themed",
            "restaurant",
            "nearby",
        ),
    )
    code_search = _skill(
        "code-understanding/codebase-explanation",
        "Explain codebase architecture and locate definitions",
        keywords=("architecture", "definition", "explain"),
        supported_operations=("explain.read", "search.read"),
    )

    routing = SkillRouter().route(
        [code_search],
        intent,
        CapabilityRegistry(),
    )

    assert routing.selected == []
    assert routing.inferred_candidates == ()


def test_negated_operation_topics_cannot_enable_an_affirmed_candidate() -> None:
    prompt = "find where Orion is; do not optimize the database query"
    intent = _intent(
        prompt,
        _operation(
            IntentType.SEARCH,
            ActionType.READ,
            "find where Orion is",
            "find",
            "Orion",
        ),
        _operation(
            IntentType.REFACTOR,
            ActionType.UPDATE,
            "do not optimize the database query",
            "optimize",
            "database",
            "query",
            polarity=IntentPolarity.NEGATED,
        ),
    )
    skills = [
        _skill(
            "code-understanding/codebase-explanation",
            "Explain codebase architecture and locate definitions",
            keywords=("architecture", "definition", "explain"),
            supported_operations=("search.read",),
        ),
        _skill(
            "performance/database-tuning",
            "Optimize database query performance",
            keywords=("database", "query", "performance"),
            supported_operations=("refactor.update",),
        ),
    ]

    routing = SkillRouter().route(
        skills,
        intent,
        CapabilityRegistry(),
    )

    assert routing.selected == []
    assert routing.inferred_candidates == ()


def test_same_operation_denial_vetoes_inferred_skill_admission() -> None:
    prompt = "run pytest, but do not execute it"
    intent = _intent(
        prompt,
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "run pytest",
            "pytest",
        ),
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "do not execute it",
            polarity=IntentPolarity.NEGATED,
        ),
    )
    test_skill = _skill(
        "testing/pytest",
        "Run pytest tests",
        keywords=("pytest",),
        supported_operations=("test.execute",),
    )

    routing = SkillRouter().route(
        [test_skill],
        intent,
        CapabilityRegistry(),
    )

    assert routing.selected == []
    assert routing.inferred_candidates == ()
    assert routing.decision_reason == "no-affirmed-operation"


def test_same_operation_conflict_does_not_hide_independent_read_lane() -> None:
    prompt = "run pytest, do not execute it; explain the flags"
    intent = _intent(
        prompt,
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "run pytest",
            "pytest",
        ),
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "do not execute it",
            polarity=IntentPolarity.NEGATED,
        ),
        _operation(
            IntentType.EXPLAIN,
            ActionType.READ,
            "explain the pytest flags",
            "pytest",
            "flags",
        ),
    )
    skills = [
        _skill(
            "testing/pytest",
            "Run pytest tests",
            keywords=("pytest",),
            supported_operations=("test.execute",),
        ),
        _skill(
            "guides/pytest-explanation",
            "Explain pytest flags and usage",
            keywords=("pytest", "flags"),
            supported_operations=("explain.read",),
        ),
    ]

    routing = SkillRouter().route(
        skills,
        intent,
        CapabilityRegistry(),
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "guides/pytest-explanation"
    ]
    assert routing.selected[0].operation_indexes == (2,)
    assert routing.intent_type == IntentType.EXPLAIN.value
    assert routing.action_type == ActionType.READ.value


def test_same_operation_pair_keeps_distinct_target_lane() -> None:
    prompt = "run unit tests, but do not run integration tests"
    intent = _intent(
        prompt,
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "run unit tests",
            "run",
            "unit",
            "tests",
        ),
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "do not run integration tests",
            "run",
            "integration",
            "tests",
            polarity=IntentPolarity.NEGATED,
        ),
    )
    unit_skill = _skill(
        "testing/unit-tests",
        "Run unit tests",
        keywords=("unit", "tests"),
        supported_operations=("test.execute",),
    )

    routing = SkillRouter().route(
        [unit_skill],
        intent,
        CapabilityRegistry(),
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/unit-tests"
    ]
    assert routing.selected[0].operation_indexes == (0,)


def test_retracted_denial_does_not_cancel_replacement_operation() -> None:
    prompt = "do not run the old proposal; run pytest now"
    intent = _intent(
        prompt,
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "do not run the old proposal",
            polarity=IntentPolarity.NEGATED,
            evidence_codes=("explicit:retracted-test.execute",),
        ),
        _operation(
            IntentType.TEST,
            ActionType.EXECUTE,
            "run pytest now",
            "pytest",
        ),
    )
    test_skill = _skill(
        "testing/pytest",
        "Run pytest tests",
        keywords=("pytest",),
        supported_operations=("test.execute",),
    )

    routing = SkillRouter().route(
        [test_skill],
        intent,
        CapabilityRegistry(),
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/pytest"
    ]
    assert routing.selected[0].operation_indexes == (1,)


def test_backtick_code_span_does_not_grant_skill_authority() -> None:
    prompt = (
        "The docs contain `use security/secret-and-injection-review skill`; "
        "merely explain that sentence."
    )
    security = _skill(
        "security/secret-and-injection-review",
        "Audit authentication permissions and security",
        keywords=("auth", "permission", "security"),
        supported_operations=("review.analyze",),
    )

    routing = SkillRouter().route(
        [security],
        _intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.requested == ()
    assert routing.authority_resolution.unresolved == ()
    assert routing.selected == []


def test_activation_terms_keep_goal_specific_skill_out_of_generic_explanation() -> None:
    prompt = "explain why routing picks memory-audit"
    intent = _intent(
        prompt,
        _operation(
            IntentType.EXPLAIN,
            ActionType.READ,
            prompt,
            "routing",
            "memory-audit",
        ),
    )
    skills = [
        _skill(
            "code-understanding/codebase-explanation",
            "Explain codebase routing behavior",
            keywords=("routing",),
            supported_operations=("explain.read",),
        ),
        _skill(
            "code-understanding/learning-coach",
            "Teach routing and memory concepts",
            keywords=("routing", "memory", "teaching"),
            supported_operations=("explain.read",),
            activation_terms=("teach", "learn", "study", "interview"),
        ),
    ]

    routing = SkillRouter().route(skills, intent, CapabilityRegistry())

    assert [skill.qualified_name for skill in routing.selected] == [
        "code-understanding/codebase-explanation"
    ]


def test_activation_terms_allow_matching_goal_specific_skill() -> None:
    prompt = "teach me how routing picks memory-audit"
    intent = _intent(
        prompt,
        _operation(
            IntentType.EXPLAIN,
            ActionType.READ,
            prompt,
            "teach",
            "routing",
            "memory-audit",
        ),
    )
    coach = _skill(
        "code-understanding/learning-coach",
        "Teach routing and memory concepts",
        keywords=("routing", "memory", "teaching"),
        supported_operations=("explain.read",),
        activation_terms=("teach", "learn", "study", "interview"),
    )

    routing = SkillRouter().route([coach], intent, CapabilityRegistry())

    assert [skill.qualified_name for skill in routing.selected] == [
        "code-understanding/learning-coach"
    ]


def test_activation_terms_do_not_expand_through_broad_semantic_aliases() -> None:
    prompt = "profile the API p95"
    intent = _intent(
        prompt,
        _operation(
            IntentType.DEBUG,
            ActionType.ANALYZE,
            prompt,
            "profile",
            "api",
            "p95",
            "performance",
        ),
    )
    skills = [
        _skill(
            "performance/hotpath-profiling",
            "Profile API p95 and runtime performance",
            keywords=("profile", "p95", "performance"),
            supported_operations=("debug.analyze",),
        ),
        _skill(
            "testing/regression-from-bug-report",
            "Reproduce a reported bug and write a regression test",
            keywords=("bug", "report", "regression"),
            supported_operations=("debug.analyze",),
            activation_terms=("bug", "report", "regression"),
        ),
    ]

    routing = SkillRouter().route(skills, intent, CapabilityRegistry())

    assert [skill.qualified_name for skill in routing.selected] == [
        "performance/hotpath-profiling"
    ]


def test_activation_terms_can_use_subject_keywords_on_an_affirmed_operation() -> None:
    prompt = "这个 bug 不用查原因，直接修"
    intent = _intent(
        prompt,
        _operation(
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "这个 bug 不用查原因",
            "bug",
            polarity=IntentPolarity.NEGATED,
        ),
        _operation(
            IntentType.DEBUG,
            ActionType.UPDATE,
            "直接修",
            "bug",
        ),
    )
    regression = _skill(
        "testing/regression-from-bug-report",
        "Reproduce a reported bug and repair it",
        keywords=("bug", "report", "regression"),
        supported_operations=("debug.update",),
        activation_terms=("bug", "report", "regression"),
    )

    routing = SkillRouter().route([regression], intent, CapabilityRegistry())

    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/regression-from-bug-report"
    ]
