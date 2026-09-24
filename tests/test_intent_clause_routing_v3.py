"""Regression coverage for operation-scoped intent clause routing."""

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    parse_intent,
)


def _operation_signatures(text: str) -> list[tuple[IntentType, ActionType, IntentPolarity]]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in parse_intent(text).operations
    ]


def test_chinese_contrast_limits_negation_to_the_rejected_operation() -> None:
    assert _operation_signatures("不要沿用旧方案而是解释代码流程") == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


def test_contrast_scope_applies_to_vague_action_triggers_too() -> None:
    assert _operation_signatures("不要沿用旧方案而是修复 auth.py") == [
        (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED)
    ]


def test_each_operation_keeps_its_own_polarity_and_source_span() -> None:
    text = "不要修改代码而是解释代码流程"
    intent = parse_intent(text)

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
    ]
    assert all(
        operation.span is not None
        and text[operation.span[0] : operation.span[1]] == operation.clause
        for operation in intent.operations
    )


def test_one_coarse_clause_preserves_ordered_non_overlapping_operations() -> None:
    assert _operation_signatures("搜索代码函数并解释调用流程并编写文档") == [
        (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
        (IntentType.DOCUMENT, ActionType.CREATE, IntentPolarity.AFFIRMED),
    ]


def test_chinese_capability_question_outranks_embedded_search_action() -> None:
    assert _operation_signatures("这个系统是否支持查找文件？") == [
        (IntentType.QUESTION, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "该系统能否搜索代码？",
        "本系统有没有能力查找文件？",
        "Does this system support searching files?",
        "Are you able to search files?",
    ],
)
def test_narrow_capability_question_grammar_is_bilingual(text: str) -> None:
    assert _operation_signatures(text) == [
        (IntentType.QUESTION, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "请查找 auth.py 的调用点",
        "Could you find the references in auth.py for me?",
    ],
)
def test_search_commands_and_polite_requests_remain_search_operations(
    text: str,
) -> None:
    assert _operation_signatures(text) == [
        (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


def test_personal_capability_question_is_not_a_search_command() -> None:
    assert _operation_signatures("你能使用联网搜索功能吗？") == [
        (IntentType.QUESTION, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


def test_chinese_fenbie_is_not_a_negation_marker() -> None:
    assert _operation_signatures("分别说明代码模块") == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]
    assert _operation_signatures("别修改代码") == [
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED)
    ]


def test_project_module_question_and_requested_explanation_are_preserved() -> None:
    assert _operation_signatures(
        "这个项目的 Skill 路由分别有哪些模块？给我一份文件结构和模块说明。"
    ) == [
        (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
    ]


def test_discourse_decline_does_not_negate_following_document_request() -> None:
    assert _operation_signatures(
        "不用了 你把这个模块的实现写成 Markdown 文件给我看看"
    ) == [
        (IntentType.DOCUMENT, ActionType.CREATE, IntentPolarity.AFFIRMED)
    ]


def test_infinitive_refactor_purpose_is_not_a_duplicate_operation() -> None:
    assert _operation_signatures("refactor this code to simplify the logic") == [
        (IntentType.REFACTOR, ActionType.UPDATE, IntentPolarity.AFFIRMED)
    ]


def test_nested_memory_topic_is_not_a_second_create_operation() -> None:
    assert _operation_signatures(
        "audit persistent memory retrieval provenance and lesson feedback"
    ) == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]
