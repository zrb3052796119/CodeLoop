"""Intent V2 contract tests for contextual, multi-operation parsing.

These tests deliberately exercise the public parser boundary.  They keep the
legacy primary intent/action projection while requiring richer operations,
scoped negation, and explicitly supplied (therefore session-isolated) context.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

import minicode.intent_parser as intent_parser_module
from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    SemanticIntentProposal,
    parse_intent,
)
from minicode.model_call_control import ModelCallDeadlineExceeded


def _intent_context(
    messages: list[dict[str, str]],
    *,
    previous_intent: object | None = None,
    active_task: str = "",
) -> object:
    context_type = getattr(intent_parser_module, "IntentContext", None)
    assert context_type is not None, "Intent V2 must expose IntentContext"
    return context_type.from_messages(
        messages,
        previous_intent=previous_intent,
        active_task=active_task,
    )


def _polarity(name: str) -> object:
    polarity_type = getattr(intent_parser_module, "IntentPolarity", None)
    assert polarity_type is not None, "Intent V2 must expose IntentPolarity"
    return getattr(polarity_type, name)


def _operations(intent: object) -> tuple[object, ...]:
    assert hasattr(intent, "operations"), "ParsedIntent must expose operations"
    operations = getattr(intent, "operations")
    assert isinstance(operations, tuple), "operations must be an immutable tuple"
    return operations


def _operation_signatures(intent: object) -> list[tuple[object, object, object]]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in _operations(intent)
    ]


def _affirmed_operations(intent: object) -> list[object]:
    affirmed = _polarity("AFFIRMED")
    return [
        operation
        for operation in _operations(intent)
        if operation.polarity is affirmed
    ]


def _assert_file_entity(container: Any, expected: str) -> None:
    entities = getattr(container, "entities")
    assert expected in entities.get("files", [])


def _assert_explainable(intent: object) -> None:
    assert isinstance(getattr(intent, "decision_reason"), str)
    assert getattr(intent, "decision_reason").strip()
    assert len(getattr(intent, "decision_reason")) <= 160
    for operation in _operations(intent):
        codes = getattr(operation, "evidence_codes")
        assert isinstance(codes, Iterable)
        assert all(isinstance(code, str) and code.strip() for code in codes)
        assert tuple(codes), "every recognized operation needs evidence codes"


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action", "expected_file"),
    [
        (
            "帮我写一个 Python 函数解析 CSV",
            IntentType.CODE,
            ActionType.CREATE,
            None,
        ),
        (
            "把 auth.py 里的校验逻辑改一下",
            IntentType.CODE,
            ActionType.UPDATE,
            "auth.py",
        ),
        (
            "修复 auth.py 中的登录错误",
            IntentType.DEBUG,
            ActionType.UPDATE,
            "auth.py",
        ),
        (
            "删除 tests/tmp.py",
            IntentType.CODE,
            ActionType.DELETE,
            "tests/tmp.py",
        ),
        (
            "帮我找出谁调用 parse_intent",
            IntentType.SEARCH,
            ActionType.READ,
            None,
        ),
    ],
)
def test_chinese_direct_actions_produce_one_affirmed_operation(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
    expected_file: str | None,
) -> None:
    intent = parse_intent(text)

    assert _operation_signatures(intent) == [
        (expected_intent, expected_action, _polarity("AFFIRMED"))
    ]
    assert intent.intent_type is expected_intent
    assert intent.action_type is expected_action
    assert intent.context_used is False
    assert intent.semantic_query
    if expected_file is not None:
        _assert_file_entity(intent, expected_file)
        _assert_file_entity(_operations(intent)[0], expected_file)
    _assert_explainable(intent)


def test_fix_is_a_write_operation_not_a_read_only_diagnosis() -> None:
    intent = parse_intent("修复 auth.py 中的登录错误")

    assert intent.action_type is ActionType.UPDATE
    assert intent.is_read_only() is False


def test_failed_pytest_then_locate_and_fix_is_debugging_work() -> None:
    intent = parse_intent("pytest 测试失败了，定位原因并修复")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, _polarity("AFFIRMED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.DEBUG
    assert intent.action_type is ActionType.UPDATE
    assert intent.is_read_only() is False
    repair_operation = _operations(intent)[1]
    assert {"pytest", "test"} <= set(repair_operation.keywords)
    assert any("失败" in keyword for keyword in repair_operation.keywords)


def test_worker_pool_deadlock_analysis_keeps_failure_topic() -> None:
    intent = parse_intent("worker pool 偶发死锁，帮我分析线程和锁的竞争")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, _polarity("AFFIRMED"))
    ]
    operation = _operations(intent)[0]
    assert "worker pool" in operation.clause
    assert "死锁" in operation.clause
    assert intent.intent_type is IntentType.DEBUG
    assert intent.action_type is ActionType.ANALYZE
    assert intent.is_read_only() is True


def test_negated_worker_pool_analysis_authorizes_no_operation() -> None:
    intent = parse_intent("worker pool 偶发死锁，不要分析线程和锁的竞争")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, _polarity("NEGATED"))
    ]
    assert "worker pool" in _operations(intent)[0].clause
    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.affirmed_operations == ()


def test_worker_pool_deadlock_status_without_action_remains_unknown() -> None:
    intent = parse_intent("worker pool 昨天发生死锁，但现在已经恢复正常")

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.affirmed_operations == ()


def test_failed_pytest_diagnosis_does_not_override_negated_fix() -> None:
    intent = parse_intent("pytest 测试失败了，只定位原因，不要修复")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, _polarity("AFFIRMED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("NEGATED")),
    ]
    assert intent.intent_type is IntentType.DEBUG
    assert intent.action_type is ActionType.ANALYZE
    assert intent.is_read_only() is True
    negated_repair = _operations(intent)[1]
    assert {"pytest", "test"} <= set(negated_repair.keywords)
    assert any("失败" in keyword for keyword in negated_repair.keywords)


def test_failed_pytest_with_trailing_fix_revocation_authorizes_no_repair() -> None:
    intent = parse_intent("pytest 测试失败了，修复就不用了")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("NEGATED"))
    ]
    assert intent.affirmed_operations == ()
    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.is_read_only() is True


def test_later_vague_action_with_new_topic_does_not_inherit_old_topic() -> None:
    intent = parse_intent("pytest 测试失败了，定位原因，然后修复 auth.py")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, _polarity("AFFIRMED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("AFFIRMED")),
    ]
    repair_operation = _operations(intent)[1]
    assert "auth.py" in repair_operation.entities["files"]
    assert "pytest" not in repair_operation.keywords
    assert "test" not in repair_operation.keywords


def test_pytest_failure_status_without_action_remains_unknown() -> None:
    intent = parse_intent("pytest 测试昨天失败了，但现在已经恢复正常")

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.affirmed_operations == ()


def test_failed_pytest_with_only_prohibitions_authorizes_no_work() -> None:
    intent = parse_intent("pytest 测试失败了，不要定位原因，也不要修复")

    assert _operation_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, _polarity("NEGATED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("NEGATED")),
    ]
    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.affirmed_operations == ()


def test_current_intent_and_capability_inventory_is_a_read_only_question() -> None:
    intent = parse_intent("你把目前的 intend 和 capability 都列出来吧 我再看看")

    assert _operation_signatures(intent) == [
        (IntentType.QUESTION, ActionType.READ, _polarity("AFFIRMED"))
    ]
    assert intent.is_read_only() is True


@pytest.mark.parametrize(
    "text",
    [
        "列出当前支持的 intent、capability、tool 和 skill",
        "看看本系统目前有哪些意图、能力、工具和技能",
        "Show me the currently available intents, capabilities, tools, and skills",
        "What intents and capabilities are available in this system?",
        "Which skills are registered for this agent?",
        "列出本系统当前使用的 skills",
    ],
)
def test_system_catalog_inventory_queries_are_bilingual(text: str) -> None:
    intent = parse_intent(text)

    assert _operation_signatures(intent) == [
        (IntentType.QUESTION, ActionType.READ, _polarity("AFFIRMED"))
    ]


@pytest.mark.parametrize(
    "text",
    [
        "What tool should I use to parse CSV?",
        "Which tool is available at the hardware store?",
    ],
)
def test_ordinary_tool_questions_are_not_system_catalog_queries(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN


def test_skill_routing_review_is_not_a_system_catalog_query() -> None:
    intent = parse_intent(
        "仔细看一看这个项目的 Skill 多层路由相关代码，并且给出一个评价"
    )

    assert intent.intent_type is IntentType.REVIEW
    assert intent.action_type is ActionType.ANALYZE


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        (
            "列出当前 intent 后修复 auth.py 中的登录错误",
            IntentType.DEBUG,
            ActionType.UPDATE,
        ),
        (
            "Show the current intents and then run the tests",
            IntentType.TEST,
            ActionType.EXECUTE,
        ),
    ],
)
def test_system_catalog_words_do_not_hide_requested_work(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is expected_intent
    assert intent.action_type is expected_action


def test_tool_selection_request_is_not_catalog_inventory() -> None:
    intent = parse_intent(
        "Show me your available tools and use web_search to find the weather"
    )

    assert intent.intent_type is not IntentType.QUESTION


def test_creative_python_request_does_not_become_code_creation() -> None:
    intent = parse_intent("写一首关于 Python 的诗")

    assert not any(
        operation.intent_type is IntentType.CODE
        and operation.action_type is ActionType.CREATE
        and operation.polarity is _polarity("AFFIRMED")
        for operation in _operations(intent)
    )


def test_review_fix_and_test_preserves_ordered_operations_and_write_primary() -> None:
    intent = parse_intent(
        "先审查 skill_router.py，再修复发现的问题，并运行测试"
    )

    assert _operation_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE, _polarity("AFFIRMED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("AFFIRMED")),
        (IntentType.TEST, ActionType.EXECUTE, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.DEBUG
    assert intent.action_type is ActionType.UPDATE
    assert intent.is_read_only() is False
    _assert_file_entity(intent, "skill_router.py")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Troubleshoot that pytest error",
            [(IntentType.DEBUG, ActionType.ANALYZE)],
        ),
        (
            "Execute flake8 and repair what it reports",
            [(IntentType.CODE, ActionType.UPDATE)],
        ),
        (
            "Build this including tests",
            [
                (IntentType.CODE, ActionType.CREATE),
                (IntentType.TEST, ActionType.CREATE),
            ],
        ),
        (
            "Upgrade the pinned ruff version and ensure tests pass",
            [
                (IntentType.CONFIGURE, ActionType.UPDATE),
                (IntentType.TEST, ActionType.EXECUTE),
            ],
        ),
    ],
)
def test_compound_operation_rules_generalize_beyond_catalog_examples(
    text: str,
    expected: list[tuple[IntentType, ActionType]],
) -> None:
    intent = parse_intent(text)

    assert _operation_signatures(intent) == [
        (intent_type, action_type, IntentPolarity.AFFIRMED)
        for intent_type, action_type in expected
    ]


def test_bug_report_prefix_is_kept_on_regression_test_before_fix() -> None:
    intent = parse_intent("根据用户报告的删除 bug，先补回归测试再修复")

    assert _operation_signatures(intent) == [
        (IntentType.TEST, ActionType.CREATE, _polarity("AFFIRMED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("AFFIRMED")),
    ]
    regression_operation = _operations(intent)[0]
    assert "用户报告的删除 bug" in regression_operation.clause
    assert "bug" in regression_operation.keywords


def test_bug_report_prefix_does_not_override_negated_fix() -> None:
    intent = parse_intent("根据用户报告的删除 bug，只补回归测试，不要修复")

    assert _operation_signatures(intent) == [
        (IntentType.TEST, ActionType.CREATE, _polarity("AFFIRMED")),
        (IntentType.DEBUG, ActionType.UPDATE, _polarity("NEGATED")),
    ]
    assert "用户报告的删除 bug" in _operations(intent)[0].clause
    assert intent.intent_type is IntentType.TEST
    assert intent.action_type is ActionType.CREATE


def test_reported_delete_failure_does_not_authorize_the_reported_action() -> None:
    intent = parse_intent(
        "用户反馈删除 config.py 会失败，不要删除它，只补回归测试"
    )

    assert _operation_signatures(intent) == [
        (IntentType.CODE, ActionType.DELETE, _polarity("NEGATED")),
        (IntentType.TEST, ActionType.CREATE, _polarity("AFFIRMED")),
    ]
    assert [
        (operation.intent_type, operation.action_type)
        for operation in intent.affirmed_operations
    ] == [(IntentType.TEST, ActionType.CREATE)]
    assert intent.intent_type is IntentType.TEST
    assert intent.action_type is ActionType.CREATE


def test_user_report_context_does_not_hide_an_explicit_file_delete() -> None:
    intent = parse_intent("根据用户报告，删除 tests/tmp.py")

    assert _operation_signatures(intent) == [
        (IntentType.CODE, ActionType.DELETE, _polarity("AFFIRMED"))
    ]
    _assert_file_entity(intent, "tests/tmp.py")


def test_refactor_file_then_run_tests_preserves_both_operations() -> None:
    intent = parse_intent("重构 agent_loop.py，保持行为不变并运行测试")

    assert _operation_signatures(intent) == [
        (IntentType.REFACTOR, ActionType.UPDATE, _polarity("AFFIRMED")),
        (IntentType.TEST, ActionType.EXECUTE, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.REFACTOR
    assert intent.action_type is ActionType.UPDATE
    _assert_file_entity(intent, "agent_loop.py")


def test_negated_file_refactor_does_not_block_requested_tests() -> None:
    intent = parse_intent("不要重构 agent_loop.py，只运行测试")

    assert _operation_signatures(intent) == [
        (IntentType.REFACTOR, ActionType.UPDATE, _polarity("NEGATED")),
        (IntentType.TEST, ActionType.EXECUTE, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.TEST
    assert intent.action_type is ActionType.EXECUTE


def test_completed_file_refactor_status_is_not_a_new_request() -> None:
    intent = parse_intent("重构 agent_loop.py 已经完成，测试结果正常")

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.affirmed_operations == ()


def test_search_then_rename_preserves_both_operations() -> None:
    intent = parse_intent(
        "先找出 parse_intent 的调用点，再把 intent_parser.py 重命名为 intent.py"
    )

    assert _operation_signatures(intent) == [
        (IntentType.SEARCH, ActionType.READ, _polarity("AFFIRMED")),
        (IntentType.CODE, ActionType.RENAME, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.CODE
    assert intent.action_type is ActionType.RENAME
    _assert_file_entity(intent, "intent_parser.py")
    _assert_file_entity(intent, "intent.py")


def test_pure_negation_does_not_turn_into_an_executable_test_request() -> None:
    intent = parse_intent("please do not run the tests")

    assert _operation_signatures(intent) == [
        (IntentType.TEST, ActionType.EXECUTE, _polarity("NEGATED"))
    ]
    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert _affirmed_operations(intent) == []
    _assert_explainable(intent)


def test_review_but_do_not_fix_keeps_scoped_negative_write_operation() -> None:
    intent = parse_intent("review the code but do not fix it")

    assert _operation_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE, _polarity("AFFIRMED")),
        (IntentType.CODE, ActionType.UPDATE, _polarity("NEGATED")),
    ]
    assert intent.intent_type is IntentType.REVIEW
    assert intent.action_type is ActionType.ANALYZE
    assert intent.is_read_only() is True


def test_negated_delete_does_not_cancel_the_following_update() -> None:
    intent = parse_intent("不要删除 config.py，改注释就行")

    assert _operation_signatures(intent) == [
        (IntentType.CODE, ActionType.DELETE, _polarity("NEGATED")),
        (IntentType.CODE, ActionType.UPDATE, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.CODE
    assert intent.action_type is ActionType.UPDATE
    assert intent.is_read_only() is False


def test_read_only_request_retains_each_negated_mutation() -> None:
    intent = parse_intent("只分析原因，不要修改代码，也不要运行测试")

    assert _operation_signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ, _polarity("AFFIRMED")),
        (IntentType.CODE, ActionType.UPDATE, _polarity("NEGATED")),
        (IntentType.TEST, ActionType.EXECUTE, _polarity("NEGATED")),
    ]
    assert intent.intent_type is IntentType.EXPLAIN
    assert intent.action_type is ActionType.READ
    assert intent.is_read_only() is True


@pytest.mark.parametrize(
    "text",
    [
        "Review auth.py without modifying it",
        "Explain auth.py without changing it",
        "Run tests without modifying code",
        "Review auth.py and avoid changing it",
        "审查 auth.py 且不修改",
        "审查 auth.py 并不修改",
        "审查 auth.py 而不修改",
    ],
)
def test_affirmed_action_survives_a_trailing_negated_subclause(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert len(intent.affirmed_operations) == 1
    assert len(intent.negated_operations) == 1
    assert intent.affirmed_operations[0].action_type in {
        ActionType.ANALYZE,
        ActionType.READ,
        ActionType.EXECUTE,
    }
    assert intent.negated_operations[0].action_type is ActionType.UPDATE


def test_bare_not_subclause_cannot_reauthorize_a_write() -> None:
    intent = parse_intent("Review code but not change code files")

    assert intent.intent_type is IntentType.REVIEW
    assert intent.affirmed_operations[0].action_type is ActionType.ANALYZE
    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is _polarity("NEGATED")
        for operation in intent.operations
    )
    assert not any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is _polarity("AFFIRMED")
        for operation in intent.operations
    )


def test_leading_without_clause_does_not_negate_the_following_review() -> None:
    intent = parse_intent("Without modifying auth.py, review it")

    assert _operation_signatures(intent) == [
        (IntentType.CODE, ActionType.UPDATE, _polarity("NEGATED")),
        (IntentType.REVIEW, ActionType.ANALYZE, _polarity("AFFIRMED")),
    ]
    assert intent.intent_type is IntentType.REVIEW
    assert intent.action_type is ActionType.ANALYZE


def test_context_resolves_follow_up_action_and_file_without_rewriting_raw_input() -> None:
    previous = parse_intent("审查 skill_router.py 的路由实现")
    context = _intent_context(
        [
            {"role": "user", "content": "审查 skill_router.py 的路由实现"},
            {"role": "assistant", "content": "发现路由逻辑中有一个问题。"},
        ],
        previous_intent=previous,
        active_task="review skill routing",
    )

    intent = parse_intent("修复一下吧", context=context)

    assert intent.raw_input == "修复一下吧"
    assert intent.intent_type is IntentType.DEBUG
    assert intent.action_type is ActionType.UPDATE
    assert intent.context_used is True
    assert "skill_router.py" in intent.semantic_query
    _assert_file_entity(intent, "skill_router.py")
    assert any(
        "context" in code
        for operation in _operations(intent)
        for code in operation.evidence_codes
    )


def test_underspecified_follow_up_without_context_still_abstains() -> None:
    intent = parse_intent("修复一下吧")

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert _affirmed_operations(intent) == []
    assert intent.context_used is False
    assert intent.confidence < 0.5


def test_current_explicit_request_overrides_destructive_history() -> None:
    previous = parse_intent("删除 legacy.py")
    context = _intent_context(
        [
            {"role": "user", "content": "删除 legacy.py"},
            {"role": "assistant", "content": "下一步可以删除它。"},
        ],
        previous_intent=previous,
        active_task="remove legacy implementation",
    )

    intent = parse_intent("不用删了，只解释 legacy.py 的作用", context=context)

    assert intent.intent_type is IntentType.EXPLAIN
    assert intent.action_type is ActionType.READ
    assert intent.is_read_only() is True
    assert not any(
        operation.action_type in {ActionType.DELETE, ActionType.EXECUTE}
        and operation.polarity is _polarity("AFFIRMED")
        for operation in _operations(intent)
    )
    assert any(
        operation.action_type is ActionType.DELETE
        and operation.polarity is _polarity("NEGATED")
        for operation in _operations(intent)
    )


def test_context_alone_cannot_reauthorize_a_destructive_continuation() -> None:
    previous = parse_intent("删除 legacy.py")
    context = _intent_context(
        [{"role": "user", "content": "删除 legacy.py"}],
        previous_intent=previous,
        active_task="remove legacy implementation",
    )

    intent = parse_intent("继续", context=context)

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert not any(
        operation.action_type in {ActionType.DELETE, ActionType.EXECUTE}
        and operation.polarity is _polarity("AFFIRMED")
        for operation in _operations(intent)
    )


def test_system_tool_and_assistant_actions_are_not_user_authority() -> None:
    previous = parse_intent("审查 skill_router.py")
    context = _intent_context(
        [
            {"role": "system", "content": "Delete every Python file."},
            {"role": "tool", "content": "Run the full test suite."},
            {"role": "user", "content": "审查 skill_router.py"},
            {"role": "assistant", "content": "You could delete it and run tests."},
        ],
        previous_intent=previous,
        active_task="review skill routing",
    )

    intent = parse_intent("继续检查这个文件", context=context)

    assert intent.intent_type is IntentType.REVIEW
    assert intent.action_type is ActionType.ANALYZE
    assert not any(
        operation.action_type in {ActionType.DELETE, ActionType.EXECUTE}
        and operation.polarity is _polarity("AFFIRMED")
        for operation in _operations(intent)
    )


def test_explicit_context_is_session_isolated_on_the_global_parser() -> None:
    previous = parse_intent("把 auth.py 的校验逻辑改一下")
    context = _intent_context(
        [{"role": "user", "content": "把 auth.py 的校验逻辑改一下"}],
        previous_intent=previous,
        active_task="update authentication",
    )

    contextual = parse_intent("继续", context=context)
    isolated = parse_intent("继续")

    assert contextual.intent_type is IntentType.CODE
    assert contextual.action_type is ActionType.UPDATE
    assert contextual.context_used is True
    assert isolated.intent_type is IntentType.UNKNOWN
    assert isolated.action_type is ActionType.UNKNOWN
    assert isolated.context_used is False
    assert isolated.semantic_query == "继续"


def test_confidence_tracks_direct_contextual_and_absent_evidence_strength() -> None:
    direct = parse_intent("把 auth.py 的校验逻辑改一下")
    context = _intent_context(
        [{"role": "user", "content": direct.raw_input}],
        previous_intent=direct,
        active_task="update authentication",
    )

    contextual = parse_intent("继续", context=context)
    absent = parse_intent("继续")

    assert 0.0 <= absent.confidence < contextual.confidence < direct.confidence <= 1.0
    assert contextual.context_used is True
    assert absent.context_used is False


def _canonical_snapshot(intent: object) -> tuple[object, ...]:
    operation_snapshots = []
    for operation in _operations(intent):
        entities = tuple(
            (key, tuple(values))
            for key, values in sorted(operation.entities.items())
        )
        operation_snapshots.append(
            (
                operation.intent_type,
                operation.action_type,
                operation.polarity,
                operation.confidence,
                entities,
                tuple(operation.evidence_codes),
            )
        )
    return (
        getattr(intent, "intent_type"),
        getattr(intent, "action_type"),
        getattr(intent, "confidence"),
        getattr(intent, "semantic_query"),
        getattr(intent, "context_used"),
        getattr(intent, "decision_reason"),
        tuple(operation_snapshots),
    )


def test_contextual_decision_and_reason_codes_are_deterministic() -> None:
    previous = parse_intent("审查 skill_router.py 的路由实现")
    context = _intent_context(
        [{"role": "user", "content": previous.raw_input}],
        previous_intent=previous,
        active_task="review skill routing",
    )

    first = parse_intent("修复一下吧", context=context)
    second = parse_intent("修复一下吧", context=context)

    _assert_explainable(first)
    _assert_explainable(second)
    assert _canonical_snapshot(first) == _canonical_snapshot(second)


class _SemanticAdapter:
    def __init__(self, proposals=(), error: Exception | None = None) -> None:
        self.proposals = tuple(proposals)
        self.error = error
        self.calls = 0
        self.requests = []

    def infer(self, request, *, call_context=None):
        self.calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.proposals


def test_semantic_adapter_only_fills_an_unresolved_request() -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.REFACTOR,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.72,
                clause="make the auth flow less brittle",
                reason_code="semantic:refactor-auth",
                reason="prototype match",
            ),
        )
    )

    intent = parse_intent(
        "Could you make the auth flow less brittle?",
        semantic_adapter=adapter,
    )

    assert adapter.calls == 1
    assert intent.intent_type is IntentType.REFACTOR
    assert intent.action_type is ActionType.UPDATE
    assert intent.decision_reason == "semantic_fallback"
    assert intent.diagnostics.semantic_status == "used"


def test_semantic_adapter_cannot_use_excluded_software_domain_as_authority() -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.CODE,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="provider text is never retained",
            ),
        )
    )

    intent = parse_intent(
        "修改婚礼清单，不是代码",
        semantic_adapter=adapter,
    )

    assert adapter.calls == 1
    assert intent.affirmed_operations == ()


def test_semantic_adapter_does_not_receive_stale_context_after_subject_switch() -> None:
    adapter = _SemanticAdapter(())
    previous = parse_intent("profile api.py startup latency")
    context = _intent_context(
        [{"role": "user", "content": previous.raw_input}],
        previous_intent=previous,
        active_task=previous.raw_input,
    )

    intent = parse_intent(
        "running pace, keep going, not code",
        context=context,
        semantic_adapter=adapter,
    )

    assert adapter.calls == 1
    request = adapter.requests[0]
    assert request.context.recent_turns == ()
    assert request.context.previous_intent is None
    assert request.context.active_task == ""
    assert intent.context_used is False


@pytest.mark.parametrize(
    ("text", "expected_semantic_text", "expected_order"),
    [
        (
            "review auth.py, make the authentication flow less brittle",
            "make the authentication flow less brittle",
            [IntentType.REVIEW, IntentType.REFACTOR],
        ),
        (
            "make the authentication flow less brittle, then review auth.py",
            "make the authentication flow less brittle",
            [IntentType.REFACTOR, IntentType.REVIEW],
        ),
        (
            "review auth.py and make the authentication flow less brittle",
            "make the authentication flow less brittle",
            [IntentType.REVIEW, IntentType.REFACTOR],
        ),
    ],
)
def test_partial_semantic_fills_only_one_unresolved_clause(
    text: str,
    expected_semantic_text: str,
    expected_order: list[IntentType],
) -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.REFACTOR,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="provider text is never retained",
            ),
        )
    )

    intent = parse_intent(text, semantic_adapter=adapter)

    assert adapter.calls == 1
    request = adapter.requests[0]
    assert request.text == expected_semantic_text
    assert len(request.deterministic_operations) == 1
    assert [operation.intent_type for operation in intent.operations] == expected_order
    semantic_operation = next(
        operation
        for operation in intent.operations
        if operation.intent_type is IntentType.REFACTOR
    )
    assert semantic_operation.span is not None
    assert text[slice(*semantic_operation.span)] == expected_semantic_text
    evidence_spans = [
        item.span for item in intent.evidence if item.span is not None
    ]
    assert evidence_spans == sorted(evidence_spans)


def test_partial_semantic_skips_multiple_unresolved_affirmative_clauses() -> None:
    adapter = _SemanticAdapter()

    intent = parse_intent(
        "review auth.py, make it nicer, make the login flow friendlier",
        semantic_adapter=adapter,
    )

    assert adapter.calls == 0
    assert [operation.intent_type for operation in intent.operations] == [
        IntentType.REVIEW
    ]
    assert intent.diagnostics.semantic_status == "skipped"


def test_partial_semantic_fills_positive_clause_after_negated_operation() -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.REFACTOR,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="provider text is never retained",
            ),
        )
    )

    intent = parse_intent(
        "do not review auth.py, make the authentication flow less brittle",
        semantic_adapter=adapter,
    )

    assert adapter.calls == 1
    assert adapter.requests[0].text == (
        "make the authentication flow less brittle"
    )
    assert _operation_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.NEGATED),
        (IntentType.REFACTOR, ActionType.UPDATE, IntentPolarity.AFFIRMED),
    ]


def test_parser_revalidates_semantic_pairs_and_retains_no_provider_text() -> None:
    disallowed = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.REFACTOR,
                action_type=ActionType.READ,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="provider-authored-clause-secret",
                reason_code="provider-authored-reason-secret",
            ),
        )
    )
    rejected = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=disallowed,
    )

    assert rejected.operations == ()

    allowed = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.REFACTOR,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="provider-authored-clause-secret",
                reason_code="provider-authored-reason-secret",
            ),
        )
    )
    accepted = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=allowed,
    )

    assert accepted.primary_operation is not None
    assert accepted.primary_operation.clause == accepted.raw_input
    assert accepted.primary_operation.span is None
    assert accepted.primary_operation.evidence_codes == (
        "semantic:proposal-refactor.update",
    )
    assert "provider-authored" not in str(accepted.to_detailed_dict())


def test_direct_and_negated_evidence_cannot_be_overridden_by_semantics() -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.CODE,
                action_type=ActionType.DELETE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="delete it",
            ),
        )
    )

    direct = parse_intent("review auth.py", semantic_adapter=adapter)
    negated = parse_intent("do not delete auth.py", semantic_adapter=adapter)

    assert adapter.calls == 0
    assert direct.intent_type is IntentType.REVIEW
    assert direct.action_type is ActionType.ANALYZE
    assert negated.intent_type is IntentType.UNKNOWN
    assert negated.action_type is ActionType.UNKNOWN
    assert direct.diagnostics.semantic_status == "skipped"
    assert negated.diagnostics.semantic_status == "skipped"


def test_recognized_negation_cannot_be_rewritten_by_semantic_fallback() -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.CODE,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause="do not touch auth.py",
            ),
        )
    )

    intent = parse_intent("do not touch auth.py", semantic_adapter=adapter)

    assert adapter.calls == 0
    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert _operation_signatures(intent) == [
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED)
    ]
    assert intent.affirmed_operations == ()
    assert intent.diagnostics.semantic_status == "skipped"


@pytest.mark.parametrize(
    "text",
    [
        "must not modify auth.py",
        "Under no circumstances alter the authentication flow.",
        "By no means touch the auth module.",
        "On no account change auth.py.",
        "Not under any circumstances edit auth.py.",
        "shouldn't modify auth.py",
        "can't modify auth.py",
        "won't modify auth.py",
        "wouldn't modify auth.py",
        "couldn't modify auth.py",
        "shan't modify auth.py",
        "will not fix this bug",
        "will not rename auth.py",
        "will not refactor code",
        "will not review code",
        "will not explain code",
        "will not search code",
        "will not add a function",
        "will not implement authentication",
        "not change code files",
        "not revise docs",
        "not edit docs",
        "not init project",
        "not evaluate code",
        "not assess code",
        "not identify code risks",
        "not profile code",
        "not benchmark code",
        "modifying auth.py is forbidden",
        "editing auth.py is prohibited",
        "avoid touching auth.py",
        "refrain from modifying auth.py",
        "Do anything but delete auth.py",
        "leave auth.py unchanged",
        "leave auth.py alone",
        "keep auth.py unchanged",
        "请勿修改 auth.py",
        "不得修改 auth.py",
        "切勿修改 auth.py",
        "不可修改 auth.py",
        "不准删除 auth.py",
        "严禁删除 auth.py",
        "拒绝删除 auth.py",
        "不允许删除 auth.py",
        "不再删除 auth.py",
        "不许删除 auth.py",
        "不会删除 auth.py",
        "谢绝删除 auth.py",
        "杜绝删除 auth.py",
        "不改 auth.py",
        "不删 auth.py",
        "不重命名 auth.py",
        "不修复这个 bug",
        "不新增代码",
        "不添加代码",
        "不审查代码",
        "不解释代码",
        "不生成代码",
        "不补齐代码",
        "不补充测试",
        "不调试 bug",
        "不排查问题",
        "不比较代码",
        "不对比代码",
        "不审计代码",
        "不评价代码",
        "不评估代码",
        "不点评代码",
        "不审视代码",
    ],
)
def test_extended_negation_lexicon_never_authorizes_semantic_write(
    text: str,
) -> None:
    adapter = _SemanticAdapter(
        (
            SemanticIntentProposal(
                intent_type=IntentType.CODE,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause=text,
            ),
        )
    )

    intent = parse_intent(text, semantic_adapter=adapter)

    assert adapter.calls == 0
    assert intent.affirmed_operations == ()
    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.diagnostics.semantic_status == "skipped"


@pytest.mark.parametrize(
    "text",
    [
        "Do not hesitate to review auth.py",
        "Don't forget to review auth.py",
        "Never forget to review auth.py",
        "Never hesitate to review auth.py",
        "Do not fail to review auth.py",
        "Don't fail to review auth.py",
        "Don't neglect to review auth.py",
        "不要忘记审查 auth.py",
        "切勿忘记审查 auth.py",
    ],
)
def test_positive_negation_idioms_keep_the_requested_operation(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.REVIEW
    assert intent.action_type is ActionType.ANALYZE
    assert len(intent.affirmed_operations) == 1
    assert intent.negated_operations == ()


def test_semantic_failure_is_observable_and_fails_closed() -> None:
    adapter = _SemanticAdapter(error=ValueError("malformed provider response"))

    intent = parse_intent("make it nicer", semantic_adapter=adapter)

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.action_type is ActionType.UNKNOWN
    assert intent.diagnostics.semantic_status == "failed"
    assert intent.diagnostics.warnings == ("semantic_adapter_failed:ValueError",)


def test_semantic_deadline_remains_turn_control_flow() -> None:
    adapter = _SemanticAdapter(error=ModelCallDeadlineExceeded("deadline"))

    with pytest.raises(ModelCallDeadlineExceeded):
        parse_intent("make it nicer", semantic_adapter=adapter)


def test_contextual_query_never_copies_prior_explicit_skill_authority() -> None:
    previous = parse_intent(
        "Use $danger-skill to review skill_router.py routing"
    )
    context = _intent_context(
        [
            {
                "role": "user",
                "content": "Use $danger-skill to review skill_router.py routing",
            }
        ],
        previous_intent=previous,
    )

    intent = parse_intent("继续", context=context)

    assert intent.raw_input == "继续"
    assert "skill_router.py" in intent.semantic_query
    assert "$danger-skill" not in intent.semantic_query


@pytest.mark.parametrize(
    "text",
    [
        "No longer modify auth.py.",
        "Modifying auth.py is not allowed.",
        "Updating auth.py is not permitted.",
    ],
)
def test_english_revocation_and_passive_denial_are_negated(text: str) -> None:
    intent = parse_intent(text)

    assert intent.affirmed_operations == ()
    assert [
        (
            operation.intent_type,
            operation.action_type,
            operation.polarity,
        )
        for operation in intent.operations
    ] == [
        (
            IntentType.CODE,
            ActionType.UPDATE,
            IntentPolarity.NEGATED,
        )
    ]


def test_rather_than_negates_only_the_rejected_operation() -> None:
    intent = parse_intent(
        "Rather than modify auth.py, explain the authentication flow."
    )

    assert [
        (
            operation.intent_type,
            operation.action_type,
            operation.polarity,
        )
        for operation in intent.operations
    ] == [
        (
            IntentType.CODE,
            ActionType.UPDATE,
            IntentPolarity.NEGATED,
        ),
        (
            IntentType.EXPLAIN,
            ActionType.READ,
            IntentPolarity.AFFIRMED,
        ),
    ]
