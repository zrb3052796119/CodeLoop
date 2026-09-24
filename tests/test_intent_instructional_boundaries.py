"""Regression coverage for instructional and test-artifact boundaries."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    parse_intent,
)


@pytest.mark.parametrize(
    "text",
    [
        "教我怎么用 uv 运行 pytest",
        "说说该怎么在项目里跑 pytest",
        "告诉我怎么带参数执行 pytest",
        "how do I run pytest?",
        "explain how I can run pytest",
    ],
)
def test_instructional_test_questions_only_affirm_explanation(text: str) -> None:
    intent = parse_intent(text)

    affirmed_pairs = [
        (operation.intent_type, operation.action_type)
        for operation in intent.operations
        if operation.polarity is IntentPolarity.AFFIRMED
    ]
    assert affirmed_pairs == [(IntentType.EXPLAIN, ActionType.READ)]
    assert intent.intent_type is IntentType.EXPLAIN
    assert intent.action_type is ActionType.READ


@pytest.mark.parametrize(
    "text",
    ["run pytest now", "不管怎么说，立即运行 pytest"],
)
def test_direct_test_command_still_affirms_execution(text: str) -> None:
    intent = parse_intent(text)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "write a workout test plan",
        "写一份健身测试计划",
        "写测试报告",
        "写测试计划",
        "写测试规范",
        "写测试说明",
    ],
)
def test_test_named_documents_do_not_affirm_test_creation(text: str) -> None:
    intent = parse_intent(text)

    assert not any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.CREATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize("text", ["write unit tests", "写回归测试"])
def test_real_test_creation_requests_remain_test_intents(text: str) -> None:
    intent = parse_intent(text)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.CREATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
