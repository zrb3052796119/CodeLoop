"""Adversarial public-contract regressions for deterministic intent parsing.

The examples in this module are deliberately phrased like real user turns.
They exercise only ``parse_intent`` and its public result objects so the tests
remain useful if the parser implementation is reorganized.
"""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
    ParsedIntent,
    parse_intent,
)


def _affirmed_signatures(
    intent: ParsedIntent,
) -> list[tuple[IntentType, ActionType]]:
    return [
        (operation.intent_type, operation.action_type)
        for operation in intent.affirmed_operations
    ]


def test_explicit_current_action_and_target_override_old_context() -> None:
    previous = parse_intent("审查 auth.py 的实现")
    context = IntentContext.from_messages(
        [
            {"role": "user", "content": "审查 auth.py 的实现"},
            {"role": "assistant", "content": "发现一些问题"},
        ],
        previous_intent=previous,
        active_task="review auth",
    )

    intent = parse_intent("按刚才的方案，修复 payment.py", context=context)

    assert _affirmed_signatures(intent) == [
        (IntentType.DEBUG, ActionType.UPDATE)
    ]
    assert (intent.intent_type, intent.action_type) == (
        IntentType.DEBUG,
        ActionType.UPDATE,
    )
    operation = intent.affirmed_operations[0]
    assert "payment.py" in operation.entities.get("files", ())
    assert "auth.py" not in operation.entities.get("files", ())


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        (
            "Show the current intent implementation in agent_loop.py",
            IntentType.EXPLAIN,
            ActionType.READ,
        ),
        (
            "展示当前 intent 路由代码并评价",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
        (
            "列出当前 intent_parser.py 中的错误",
            IntentType.DEBUG,
            ActionType.ANALYZE,
        ),
    ],
    ids=["file-implementation", "routing-review", "file-errors"],
)
def test_catalog_vocabulary_does_not_swallow_file_or_review_work(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert (expected_intent, expected_action) in _affirmed_signatures(intent)
    assert (intent.intent_type, intent.action_type) == (
        expected_intent,
        expected_action,
    )
    assert not any(
        operation.intent_type is IntentType.QUESTION
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "这个系统是否支持搜索？支持的话帮我查找 auth.py",
        "Does this system support search? If so, find references in auth.py",
    ],
    ids=["chinese", "english"],
)
def test_capability_question_preserves_the_following_search_request(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [
        (IntentType.QUESTION, ActionType.READ),
        (IntentType.SEARCH, ActionType.READ),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "pytest 测试失败了，团队已经定位原因并修复了",
        "重构 agent_loop.py 已顺利完成，测试结果正常",
    ],
    ids=["completed-pytest-repair", "completed-refactor"],
)
def test_completed_work_narration_grants_no_new_authority(text: str) -> None:
    intent = parse_intent(text)

    assert intent.affirmed_operations == ()
    assert (intent.intent_type, intent.action_type) == (
        IntentType.UNKNOWN,
        ActionType.UNKNOWN,
    )


@pytest.mark.parametrize(
    "text",
    [
        "删除 auth.py 已经完成",
        "昨天已经修复 auth.py",
    ],
    ids=["completed-delete", "completed-fix"],
)
def test_completed_mutation_narration_grants_no_write_authority(text: str) -> None:
    intent = parse_intent(text)

    assert intent.affirmed_operations == ()
    assert (intent.intent_type, intent.action_type) == (
        IntentType.UNKNOWN,
        ActionType.UNKNOWN,
    )


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        (
            "把已经给出的摘要整理成一页 Markdown 交接文档。",
            IntentType.DOCUMENT,
            ActionType.CREATE,
        ),
        (
            "把根目录删了。",
            IntentType.CODE,
            ActionType.DELETE,
        ),
    ],
    ids=["completed-source-modifier", "ba-delete-command"],
)
def test_object_fronted_commands_are_not_completed_action_reports(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [(expected_intent, expected_action)]


def test_completed_modifier_does_not_hide_a_later_document_update() -> None:
    intent = parse_intent(
        "先搞清订单页为何扫全表，再把已确认结论补进性能手册。"
    )

    assert _affirmed_signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE),
        (IntentType.DOCUMENT, ActionType.UPDATE),
    ]


def test_trailing_revocation_only_negates_the_refactor_before_requested_tests() -> None:
    intent = parse_intent("重构 agent_loop.py 就不用了，只运行测试")

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.REFACTOR, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
    ]
    assert (intent.intent_type, intent.action_type) == (
        IntentType.TEST,
        ActionType.EXECUTE,
    )


@pytest.mark.parametrize(
    ("text", "negated_action", "affirmed_intent", "affirmed_action"),
    [
        (
            "删除 auth.py 就不用了，只解释代码",
            ActionType.DELETE,
            IntentType.EXPLAIN,
            ActionType.READ,
        ),
        (
            "重命名 auth.py 就不用了，只运行测试",
            ActionType.RENAME,
            IntentType.TEST,
            ActionType.EXECUTE,
        ),
    ],
    ids=["delete", "rename"],
)
def test_trailing_revocation_negates_a_targeted_mutation_only(
    text: str,
    negated_action: ActionType,
    affirmed_intent: IntentType,
    affirmed_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert [
        (operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (negated_action, IntentPolarity.NEGATED),
        (affirmed_action, IntentPolarity.AFFIRMED),
    ]
    assert (intent.intent_type, intent.action_type) == (
        affirmed_intent,
        affirmed_action,
    )


@pytest.mark.parametrize(
    "new_topic",
    ["Redis 连接", "缓存失效"],
    ids=["redis-connection", "cache-invalidation"],
)
def test_new_natural_language_topic_does_not_inherit_previous_operation(
    new_topic: str,
) -> None:
    intent = parse_intent(f"审查 auth.py，然后修复 {new_topic}")

    assert _affirmed_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE),
        (IntentType.DEBUG, ActionType.UPDATE),
    ]
    repair = intent.affirmed_operations[1]
    assert "auth.py" not in repair.entities.get("files", ())
    assert {"审查", "audit", "review"}.isdisjoint(repair.keywords)


@pytest.mark.parametrize(
    "text",
    [
        "worker pool 偶发死锁，帮我分析线程和锁的竞争",
        "pytest 测试失败了，定位原因并修复",
        "根据用户报告的删除 bug，先补回归测试再修复",
    ],
    ids=["deadlock-subject", "pytest-subject", "bug-report-subject"],
)
def test_every_operation_clause_is_the_exact_source_span(text: str) -> None:
    intent = parse_intent(text)

    assert intent.operations
    for operation in intent.operations:
        assert operation.span is not None
        start, end = operation.span
        assert 0 <= start < end <= len(text)
        assert text[start:end] == operation.clause


def test_recovered_failure_context_does_not_pollute_later_file_repair() -> None:
    text = "pytest 测试失败了，但现在已经恢复正常，修复 auth.py"

    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [
        (IntentType.DEBUG, ActionType.UPDATE)
    ]
    repair = intent.affirmed_operations[0]
    assert repair.clause == "修复 auth.py"
    assert "auth.py" in repair.entities.get("files", ())
    assert "pytest" not in repair.keywords
    assert "test" not in repair.keywords
    assert not any("失败" in keyword for keyword in repair.keywords)


def test_recovered_deadlock_status_grants_no_test_execution() -> None:
    intent = parse_intent("worker pool 昨天死锁，现在运行正常")

    assert intent.affirmed_operations == ()
    assert (intent.intent_type, intent.action_type) == (
        IntentType.UNKNOWN,
        ActionType.UNKNOWN,
    )
    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        ("你好。", IntentType.CHAT, ActionType.READ),
        ("今天是几月几日？", IntentType.QUESTION, ActionType.READ),
        ("帮我搜一下今天的热点新闻。", IntentType.SEARCH, ActionType.READ),
        ("release 这个词怎么翻译？", IntentType.QUESTION, ActionType.READ),
        ("写一首关于 Docker 的短诗。", IntentType.DOCUMENT, ActionType.CREATE),
        (
            "比较两个旅游 agent 给出的海岛行程。",
            IntentType.REVIEW,
            ActionType.COMPARE,
        ),
        ("博物馆的 frontend 朝哪个方向？", IntentType.QUESTION, ActionType.READ),
        ("附近哪个 bank branch 周日营业？", IntentType.QUESTION, ActionType.READ),
    ],
    ids=[
        "greeting",
        "date",
        "news-search",
        "translation",
        "poem",
        "travel-comparison",
        "direction",
        "business-hours",
    ],
)
def test_non_engineering_turns_use_taxonomy_without_skill_authority(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [(expected_intent, expected_action)]


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        ("别做安全审计", IntentType.REVIEW, ActionType.ANALYZE),
        ("别进行安全审计", IntentType.REVIEW, ActionType.ANALYZE),
        ("别给我写 README", IntentType.DOCUMENT, ActionType.CREATE),
        ("别再帮我运行 pytest", IntentType.TEST, ActionType.EXECUTE),
        ("不要再帮我运行 pytest", IntentType.TEST, ActionType.EXECUTE),
    ],
    ids=["do-audit", "perform-audit", "readme", "run-again", "do-not-run-again"],
)
def test_polite_words_do_not_break_chinese_negation_scope(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(text)

    assert intent.affirmed_operations == ()
    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (expected_intent, expected_action, IntentPolarity.NEGATED)
    ]
    assert intent.operations[0].clause == text


def test_positive_run_again_request_remains_affirmed() -> None:
    intent = parse_intent("再帮我运行 pytest")

    assert _affirmed_signatures(intent) == [
        (IntentType.TEST, ActionType.EXECUTE)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "不要加载任何 Skill，只回答 2 加 2 等于多少。",
        "`documentation-writing` 这个名字是否容易理解？只评价命名。",
    ],
    ids=["arithmetic-after-global-skill-denial", "quoted-skill-name"],
)
def test_non_skill_questions_still_have_a_read_operation(text: str) -> None:
    intent = parse_intent(text)

    assert _affirmed_signatures(intent) == [
        (IntentType.QUESTION, ActionType.READ)
    ]


def test_same_specific_topic_continuation_inherits_context_file() -> None:
    context = IntentContext.from_messages(
        [
            {"role": "user", "content": "使用 security-audit 审计 auth.py。"},
            {"role": "assistant", "content": "安全审计已完成。"},
        ],
        active_task="解释 auth.py 的 token 调用链",
    )

    intent = parse_intent(
        "现在只解释 token 调用链，不做安全审计。",
        context=context,
    )

    assert intent.context_used is True
    assert _affirmed_signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ)
    ]
    assert "auth.py" in intent.affirmed_operations[0].entities.get("files", ())


def test_explicit_continuation_uses_matching_active_task_topic() -> None:
    context = IntentContext.from_messages(
        [
            {
                "role": "user",
                "content": "不要使用 context-compaction；先暂停摘要审计。",
            },
            {"role": "assistant", "content": "已暂停。"},
        ],
        active_task="恢复摘要保真度审计",
    )

    intent = parse_intent(
        "更正，使用 context-compaction 继续核对摘要。",
        context=context,
    )

    assert intent.context_used is True
    assert _affirmed_signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE)
    ]


def test_new_explicit_file_blocks_same_topic_context_entity_inheritance() -> None:
    context = IntentContext(active_task="解释 auth.py 的 token 调用链")

    intent = parse_intent(
        "现在只解释 payment.py 的 token 调用链",
        context=context,
    )

    operation = intent.affirmed_operations[0]
    assert operation.entities.get("files", ()) == ("payment.py",)
    assert "auth.py" not in operation.entities.get("files", ())
