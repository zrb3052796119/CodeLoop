"""A read request owns its nominal action words, not a second write task."""

from __future__ import annotations

import pytest

from minicode.intent_parser import ActionType, IntentType, parse_intent


@pytest.mark.parametrize(
    "text",
    [
        "找出订单编号的生成函数，再解释它为什么能避免同一秒内重复。",
        "查找订单编号的生成函数。",
        "查找后台的生成函数。",
        "查找后端的生成函数。",
        "查看删除文件的代码。",
        "搜索创建目录的代码。",
        "阅读更新缓存的代码。",
        "检查生成报告的函数。",
        "检查能够立刻生成函数的代码。",
        "审查删除用户的逻辑。",
        "解释生成订单编号的代码。",
        "Find the delete function in cleanup.py.",
        "Review the update method in cache.py.",
    ],
)
def test_read_request_does_not_authorize_its_nominal_action(text: str) -> None:
    intent = parse_intent(text)

    assert intent.affirmed_operations
    assert intent.is_read_only() is True
    assert all(
        operation.action_type in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("生成一个订单编号函数。", ActionType.CREATE),
        ("生成函数。", ActionType.CREATE),
        ("创建删除文件的函数。", ActionType.CREATE),
        ("查看 app.py，然后生成函数。", ActionType.CREATE),
        ("查看 app.py，生成函数。", ActionType.CREATE),
        ("查找订单编号的生成函数，然后修改 app.py。", ActionType.UPDATE),
        ("检查 cleanup.py，然后删除 old.py。", ActionType.DELETE),
        ("Review cache.py, then update cache.py.", ActionType.UPDATE),
        ("阅读 app.py 后直接更新函数。", ActionType.UPDATE),
        ("解释 app.py 的逻辑后立刻修改函数。", ActionType.UPDATE),
        ("审查完代码立刻生成函数。", ActionType.CREATE),
    ],
)
def test_independent_current_write_request_keeps_its_action(
    text: str, action: ActionType
) -> None:
    intent = parse_intent(text)

    assert any(operation.action_type is action for operation in intent.affirmed_operations)
    assert intent.is_read_only() is False


def test_search_object_and_following_edit_keep_their_separate_roles() -> None:
    intent = parse_intent("找出订单编号的生成函数，然后修改 app.py。")

    assert any(
        operation.intent_type is IntentType.SEARCH
        and operation.action_type is ActionType.READ
        for operation in intent.affirmed_operations
    )
    assert not any(
        operation.action_type is ActionType.CREATE
        for operation in intent.affirmed_operations
    )
    assert any(
        operation.action_type is ActionType.UPDATE
        and "app.py" in operation.entities.get("files", ())
        for operation in intent.affirmed_operations
    )
