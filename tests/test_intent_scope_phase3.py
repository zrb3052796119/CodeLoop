"""An adjacent prohibition must govern its own action, not the whole turn."""

from __future__ import annotations

import pytest

from minicode.intent_negation import is_span_negated
from minicode.intent_parser import ActionType, IntentType, parse_intent


@pytest.mark.parametrize(
    "text",
    [
        "审一下这个改动有没有并发风险，这次不用重构，也不用跑测试。",
        "审查 cache.py，本轮不必重构，也不必运行测试。",
        "审查 cache.py，暂时无需重构，也无需执行测试。",
        "审查 cache.py，不需要重构，也不需要运行测试。",
        "审查 cache.py，无须重构，也无须跑测试。",
        "Review cache.py, do not refactor or run tests.",
        "Review cache.py, this time do not refactor or run tests.",
    ],
)
def test_independent_later_prohibition_preserves_the_review(text: str) -> None:
    result = parse_intent(text)

    assert result.intent_type is IntentType.REVIEW
    assert result.is_read_only()
    assert any(
        op.intent_type is IntentType.REVIEW
        and op.action_type is ActionType.ANALYZE
        for op in result.affirmed_operations
    )
    negative_pairs = {
        (op.intent_type, op.action_type) for op in result.negated_operations
    }
    assert (IntentType.REFACTOR, ActionType.UPDATE) in negative_pairs
    assert (IntentType.TEST, ActionType.EXECUTE) in negative_pairs


@pytest.mark.parametrize(
    ("text", "target"),
    [
        ("旧的 sandbox_preview.py 已经不用了，直接从项目里删除这个文件。", "sandbox_preview.py"),
        ("cache.py 不用了，直接删除这个文件。", "cache.py"),
        ("cache.py 已经不需要了，直接删除这个文件。", "cache.py"),
        ("cache.py is no longer used, delete this file.", "cache.py"),
        ("cache.py is no longer needed, remove this file.", "cache.py"),
        ("cache.py was no longer required, delete this file.", "cache.py"),
        ("cache.py is no longer in use, delete this file.", "cache.py"),
    ],
)
def test_nonuse_status_does_not_cancel_a_later_explicit_deletion(
    text: str, target: str
) -> None:
    result = parse_intent(text)
    deletions = [
        op for op in result.affirmed_operations if op.action_type is ActionType.DELETE
    ]

    assert len(deletions) == 1
    assert deletions[0].entities["files"] == (target,)
    assert not result.is_read_only()


@pytest.mark.parametrize(
    "text",
    [
        "现在不用删除 sandbox_preview.py 了。",
        "删除 sandbox_preview.py 就不用了，只解释代码。",
        "删除 sandbox_preview.py 已经不需要了，审查代码。",
        "不要删除 sandbox_preview.py。",
        "Do not delete sandbox_preview.py.",
        "Never edit or delete sandbox_preview.py.",
        "Deleting sandbox_preview.py is no longer needed, review this file.",
        "不要先运行测试再修改代码。",
    ],
)
def test_actual_prohibitions_and_action_cancellations_remain_read_only(
    text: str,
) -> None:
    result = parse_intent(text)

    assert result.is_read_only()
    assert all(
        op.action_type
        not in {ActionType.CREATE, ActionType.UPDATE, ActionType.DELETE, ActionType.EXECUTE}
        for op in result.affirmed_operations
    )


@pytest.mark.parametrize(
    ("text", "affirmed", "negated"),
    [
        ("不用重构 cache.py，审查它。", ActionType.ANALYZE, ActionType.UPDATE),
        ("Do not edit cache.py, review it.", ActionType.ANALYZE, ActionType.UPDATE),
        ("删除 cache.py，这次不用重构。", ActionType.DELETE, ActionType.UPDATE),
        ("不要删除 cache.py，更新 README.md。", ActionType.UPDATE, ActionType.DELETE),
        ("Do not delete cache.py, update README.md.", ActionType.UPDATE, ActionType.DELETE),
    ],
)
def test_independent_current_requests_keep_their_own_polarity(
    text: str, affirmed: ActionType, negated: ActionType
) -> None:
    result = parse_intent(text)

    assert any(op.action_type is affirmed for op in result.affirmed_operations)
    assert any(op.action_type is negated for op in result.negated_operations)


@pytest.mark.parametrize(
    ("text", "trigger"),
    [
        ("Do not delete cache.py", "delete"),
        ("No longer use cache.py", "use"),
        ("cache.py is no longer used", "used"),
        ("不用运行 cache.py", "运行"),
        ("cache.py 不需要删除了", "删除"),
        ("禁止修改代码，也不要运行测试", "运行"),
    ],
)
def test_status_boundary_exception_never_disables_a_true_governing_negation(
    text: str, trigger: str
) -> None:
    start = text.index(trigger)

    assert is_span_negated(text, (start, start + len(trigger)))
