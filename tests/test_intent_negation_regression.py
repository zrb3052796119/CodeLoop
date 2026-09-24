"""Minimal behavioral proof for scoped intent negation."""

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    parse_intent,
)


def test_negated_test_request_does_not_authorize_execution() -> None:
    intent = parse_intent("please do not run the tests")

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN


@pytest.mark.parametrize(
    "text",
    [
        "不需要不写测试，但我没让你写",
        "并非不跑测试，只是说明现状",
        "不是不修 auth.py，只是讨论这种说法",
    ],
)
def test_discursive_double_negation_does_not_grant_action_authority(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {
            ActionType.CREATE,
            ActionType.UPDATE,
            ActionType.DELETE,
            ActionType.MOVE,
            ActionType.RENAME,
            ActionType.EXECUTE,
        }
        for operation in intent.operations
    )


def test_double_negation_does_not_swallow_independent_test_denial() -> None:
    intent = parse_intent(
        "不是不让你跑单测，只是不要跑集成测试"
    )

    assert not intent.affirmed_operations
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.NEGATED
        and "集成" in operation.clause
        for operation in intent.operations
    )


def test_denial_of_a_prohibition_is_not_itself_a_denied_operation() -> None:
    intent = parse_intent(
        "不是让你别审安全，只是不要修改代码"
    )

    assert not any(
        operation.intent_type is IntentType.REVIEW
        for operation in intent.operations
    )
    assert any(
        operation.intent_type is IntentType.CODE
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("text", "denied_action", "affirmed_action"),
    [
        ("No new tests fix the checkout bug now", ActionType.CREATE, ActionType.UPDATE),
        ("这个错删 bug 要修 但测试一行也不许写", ActionType.CREATE, ActionType.UPDATE),
        (
            "repair reported logout issue without executing tests",
            ActionType.EXECUTE,
            ActionType.UPDATE,
        ),
        ("别执行冒烟测试 仍然解决缓存 bug", ActionType.EXECUTE, ActionType.UPDATE),
        ("别给这个登录缺陷补测试 直接修好", ActionType.CREATE, ActionType.UPDATE),
    ],
)
def test_compact_test_denial_does_not_swallow_following_bug_fix(
    text: str,
    denied_action: ActionType,
    affirmed_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is denied_action
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is affirmed_action
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_scoped_test_skip_preserves_other_test_and_fix_authority() -> None:
    intent = parse_intent(
        "run unit tests but skip integration tests and fix the login bug"
    )

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
        (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED),
    ]
    assert "integration" in intent.operations[1].clause


def test_test_run_report_artifact_is_not_test_creation() -> None:
    intent = parse_intent(
        "do not generate the test-run report; execute pytest"
    )

    assert not any(
        operation.intent_type is IntentType.TEST
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


if __name__ == "__main__":
    test_negated_test_request_does_not_authorize_execution()
