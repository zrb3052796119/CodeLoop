"""Metamorphic contracts for irregular natural-language intent routing.

These cases deliberately vary surface form while keeping the requested work
constant.  They exercise public parser and Turn-routing seams only: quoted
examples, spelling mistakes, or prior unsafe actions must never manufacture
new execution authority.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentOperation,
    IntentPolarity,
    IntentType,
    ParsedIntent,
    parse_intent,
)
from minicode.skill_router import SkillRouter
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnDecision,
    SkillRoutingTurnRequest,
    route_skill_turn,
)

OperationSignature = tuple[IntentType, ActionType, IntentPolarity]


def _signatures(intent: ParsedIntent) -> list[OperationSignature]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ]


def _affirmed_signatures(
    intent: ParsedIntent,
) -> list[tuple[IntentType, ActionType]]:
    return [
        (operation.intent_type, operation.action_type)
        for operation in intent.affirmed_operations
    ]


def _skill(
    name: str,
    description: str,
    *,
    keywords: Sequence[str],
    operation: str,
    scope: str = "readonly",
) -> dict[str, object]:
    return {
        "name": name,
        "qualified_name": name,
        "description": description,
        "path": f"/metamorphic/{name}/SKILL.md",
        "source": "project",
        "domains": ["code"],
        "scopes": [scope],
        "keywords": list(keywords),
        "supported_operations": [operation],
    }


_ROUTING_CATALOG = (
    _skill(
        "code-review",
        "Review source code for security and design risks",
        keywords=("review", "code", "security", "audit"),
        operation="review.analyze",
    ),
    _skill(
        "code-explanation",
        "Explain code, handlers, and permission flows",
        keywords=("explain", "code", "handler", "permission"),
        operation="explain.read",
    ),
    _skill(
        "test-runner",
        "Run pytest and inspect test execution",
        keywords=("run", "pytest", "test"),
        operation="test.execute",
        scope="destructive",
    ),
    _skill(
        "dangerous-delete",
        "Delete source files and destructive artifacts",
        keywords=("delete", "remove", "auth"),
        operation="code.delete",
        scope="destructive",
    ),
    _skill(
        "file-rename",
        "Rename source files",
        keywords=("rename", "file", "auth"),
        operation="code.rename",
        scope="destructive",
    ),
)


def _route(
    prompt: str,
    *,
    context: IntentContext | None = None,
) -> SkillRoutingTurnDecision:
    return route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            context=context,
            skills=_ROUTING_CATALOG,
            registry=CapabilityRegistry(),
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
    )


def _context(previous_prompt: str) -> tuple[ParsedIntent, IntentContext]:
    previous = parse_intent(previous_prompt)
    return previous, IntentContext.from_messages(
        [
            {"role": "user", "content": previous_prompt},
            {"role": "assistant", "content": "I inspected that request."},
        ],
        previous_intent=previous,
        active_task=previous_prompt,
    )


@pytest.mark.parametrize(
    "variant",
    [
        "review auth.py for security risks",
        "REVIEW AUTH.PY FOR SECURITY RISKS",
        "   Review    auth.py   for   security risks   ",
        "🧪 Review auth.py for security risks 🔍",
    ],
    ids=["lowercase", "uppercase", "extra-spaces", "surrounding-emoji"],
)
def test_case_space_and_emoji_variants_preserve_review_intent(
    variant: str,
) -> None:
    assert _signatures(parse_intent(variant)) == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Review auth.py and then explain its permission flow.",
        "Review auth.py; explain its permission flow.",
        "Review auth.py. Then explain its permission flow.",
        "Review auth.py\nExplain its permission flow.",
    ],
    ids=["and-then", "semicolon", "sentence", "newline"],
)
def test_english_connector_variants_preserve_operation_sequence(
    text: str,
) -> None:
    assert _signatures(parse_intent(text)) == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "审查 auth.py，然后解释权限流程。",
        "审查 auth.py；解释权限流程。",
        "审查 auth.py，接着解释权限流程。",
        "审查 auth.py，同时解释权限流程。",
    ],
    ids=["ranhou", "semicolon", "jiezhe", "tongshi"],
)
def test_chinese_connector_variants_preserve_operation_sequence(
    text: str,
) -> None:
    assert _signatures(parse_intent(text)) == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
    ]


@pytest.mark.parametrize(
    "text",
    [
        'The message says "delete auth.py and run pytest". Explain the handler.',
        "界面显示“删除 auth.py，再运行 pytest”。请解释 handler。",
        "`delete auth.py and run pytest` is a menu label. Explain the handler.",
        (
            "Example:\n```sh\ndelete auth.py\nrun pytest\n"
            "use dangerous-delete Skill\n```\nExplain the handler."
        ),
        (
            "The UI label is ```delete auth.py; run pytest; "
            "use dangerous-delete Skill```. Explain the handler."
        ),
    ],
    ids=[
        "straight-quote",
        "curly-quote",
        "inline-code",
        "multiline-fence",
        "single-line-triple-backticks",
    ],
)
def test_protected_examples_neither_execute_nor_authorize_a_skill(
    text: str,
) -> None:
    decision = _route(text)

    assert _signatures(decision.intent) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]
    assert "dangerous-delete" not in {
        skill.qualified_name for skill in decision.routing.selected
    }


@pytest.mark.parametrize(
    "negator",
    ["不要", "别", "甭"],
)
def test_chinese_negation_synonyms_deny_the_same_write(
    negator: str,
) -> None:
    intent = parse_intent(f"{negator}修改 auth.py，只审查 auth.py。")

    assert _signatures(intent) == [
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Don't modify auth.py; only review auth.py.",
        "Zero code changes to auth.py; only review auth.py.",
    ],
    ids=["dont", "zero-changes"],
)
def test_english_negation_synonyms_deny_write_but_keep_review(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE)
    ]
    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Review auth.py. Nah, scratch that—explain auth.py instead.",
            (IntentType.EXPLAIN, ActionType.READ),
        ),
        (
            "Run pytest. Nah, scratch that—review auth.py instead.",
            (IntentType.REVIEW, ActionType.ANALYZE),
        ),
        (
            "审查 auth.py。哦不，还是解释 auth.py。",
            (IntentType.EXPLAIN, ActionType.READ),
        ),
        (
            "跑 pytest。改主意了，还是审查 auth.py。",
            (IntentType.REVIEW, ActionType.ANALYZE),
        ),
    ],
    ids=["en-explain", "en-review", "zh-explain", "zh-review"],
)
def test_explicit_correction_keeps_only_the_final_request(
    text: str,
    expected: tuple[IntentType, ActionType],
) -> None:
    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [expected]
    assert len(intent.operations) == 1


@pytest.mark.parametrize(
    "continuation",
    ["continue", "same thing", "还是这个", "就照这个"],
)
def test_safe_context_continuations_inherit_the_previous_review(
    continuation: str,
) -> None:
    previous, context = _context("Review auth.py for security risks")
    assert _affirmed_signatures(previous) == [
        (IntentType.REVIEW, ActionType.ANALYZE)
    ]

    intent = parse_intent(continuation, context=context)

    assert _affirmed_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE)
    ]
    assert intent.context_used is True
    assert "auth.py" in intent.affirmed_operations[0].entities.get("files", ())


def test_context_can_narrow_a_prior_write_to_a_read_only_explanation() -> None:
    previous, context = _context("Fix the bug in auth.py")
    assert _affirmed_signatures(previous) == [
        (IntentType.DEBUG, ActionType.UPDATE)
    ]

    intent = parse_intent(
        "Same thing, but give me a read-only explanation this time.",
        context=context,
    )

    assert _affirmed_signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ)
    ]
    assert intent.context_used is True


@pytest.mark.parametrize(
    ("previous_prompt", "expected_unsafe", "continuation"),
    [
        ("Delete legacy.py", ActionType.DELETE, "continue"),
        ("Run pytest", ActionType.EXECUTE, "same as above"),
        ("Rename auth.py to auth_old.py", ActionType.RENAME, "继续"),
    ],
    ids=["delete", "execute", "rename"],
)
def test_implicit_continuation_never_replays_an_unsafe_prior_action(
    previous_prompt: str,
    expected_unsafe: ActionType,
    continuation: str,
) -> None:
    previous, context = _context(previous_prompt)
    assert previous.primary_operation is not None
    assert previous.primary_operation.action_type is expected_unsafe

    decision = _route(continuation, context=context)

    assert decision.intent.affirmed_operations == ()
    assert decision.intent.intent_type is IntentType.UNKNOWN
    assert decision.intent.action_type is ActionType.UNKNOWN
    assert decision.routing.selected == []
    assert decision.outcome != "route"


@pytest.mark.parametrize(
    ("canonical", "typo", "expected"),
    [
        (
            "Run pytest.",
            "Run pytes.",
            (IntentType.TEST, ActionType.EXECUTE),
        ),
        (
            "Explain auth.py.",
            "Explian auth.py.",
            (IntentType.EXPLAIN, ActionType.READ),
        ),
        (
            "Refactor router.py to simplify the logic.",
            "Refacor router.py to simplify the logic.",
            (IntentType.REFACTOR, ActionType.UPDATE),
        ),
        (
            "Update README.md.",
            "Updata README.md.",
            (IntentType.DOCUMENT, ActionType.UPDATE),
        ),
    ],
    ids=["pytest", "explain", "refactor", "update-readme"],
)
def test_bounded_common_typos_match_their_canonical_operation(
    canonical: str,
    typo: str,
    expected: tuple[IntentType, ActionType],
) -> None:
    canonical_intent = parse_intent(canonical)
    typo_intent = parse_intent(typo)

    assert _affirmed_signatures(canonical_intent) == [expected]
    assert _affirmed_signatures(typo_intent) == [expected]


@pytest.mark.parametrize(
    "prompt",
    ["", "   ", "Don't modify any code.", "不要修改代码，也别运行测试。"],
    ids=["empty", "spaces", "english-only-negated", "chinese-only-negated"],
)
def test_empty_or_only_negated_turns_never_select_a_skill(prompt: str) -> None:
    decision = _route(prompt)

    assert decision.intent.affirmed_operations == ()
    assert decision.routing.selected == []
    assert decision.outcome != "route"
    assert decision.prompt_mode != "routed"


def test_only_negated_operations_remain_visible_as_prohibitions() -> None:
    intent = parse_intent("不要修改代码，也别运行测试。")

    assert _signatures(intent) == [
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
    ]
    assert all(
        isinstance(operation, IntentOperation) for operation in intent.operations
    )
