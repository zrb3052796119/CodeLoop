"""Behavioral regressions for general wild-language routing repairs.

These cases are derived from failure classes, not copied as a score gate from
the frozen holdout.  They exercise public parser/Turn seams and include nearby
negative controls so recall fixes do not silently manufacture authority.
"""

from __future__ import annotations

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
    parse_intent,
)
from minicode.skill_router import SkillRouter
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)

Signature = tuple[IntentType, ActionType, IntentPolarity]


def _signatures(
    text: str,
    *,
    context: IntentContext | None = None,
) -> list[Signature]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in parse_intent(text, context=context).operations
    ]


def _context(
    *,
    active_task: str,
    previous_user: str,
) -> IntentContext:
    return IntentContext.from_messages(
        [
            {"role": "user", "content": previous_user},
            {"role": "assistant", "content": "好的。"},
        ],
        active_task=active_task,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "代码别改测试别跑只查pytest为什么失败",
            [
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "文档不用写给我解释prompt怎么拼的",
            [
                (
                    IntentType.DOCUMENT,
                    ActionType.CREATE,
                    IntentPolarity.NEGATED,
                ),
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "不提交只看diff有没有secret",
            [
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "别动逻辑先测启动慢在哪里",
            [
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "依赖不升级就审一遍版本是不是过期",
            [
                (
                    IntentType.CONFIGURE,
                    ActionType.UPDATE,
                    IntentPolarity.NEGATED,
                ),
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "查所有调用方然后讲清数据怎么走",
            [
                (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED),
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "先别修把删除错位这个bug复现成测试",
            [
                (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "别讲大道理直接把README用法补上",
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.NEGATED),
                (
                    IntentType.DOCUMENT,
                    ActionType.UPDATE,
                    IntentPolarity.AFFIRMED,
                ),
            ],
        ),
        (
            "先测试再实现最后复测",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "测先写，实现随后，README 最后补；不要 commit",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (
                    IntentType.DOCUMENT,
                    ActionType.UPDATE,
                    IntentPolarity.AFFIRMED,
                ),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "先写失败测试再写实现",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.CREATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "先复现成测试确认红了再修",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "测试先行，失败后再实现登录校验",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.CREATE, IntentPolarity.AFFIRMED),
            ],
        ),
    ],
)
def test_unpunctuated_action_anchors_keep_clause_local_polarity(
    text: str,
    expected: list[Signature],
) -> None:
    intent = parse_intent(text)

    assert _signatures(text) == expected
    for operation in intent.operations:
        assert operation.span is not None
        start, end = operation.span
        assert text[start:end] == operation.clause


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "先删旧配置不对只列风险别改",
            [
                (IntentType.CODE, ActionType.DELETE, IntentPolarity.NEGATED),
                (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "测试不要跑代码也别改只读一下traceback",
            [
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "先跑测试不对只看日志别执行",
            [
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "别讲原因直接修 auth bug",
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "别解释直接补 README",
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.NEGATED),
                (
                    IntentType.DOCUMENT,
                    ActionType.UPDATE,
                    IntentPolarity.AFFIRMED,
                ),
            ],
        ),
        (
            "pytest先别跑只看失败日志",
            [
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "跑 pytest。算了，只审 auth.py。",
            [(IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
        (
            "跑全量；改主意了，不跑了，分析失败堆栈",
            [
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
    ],
)
def test_unpunctuated_negation_and_correction_do_not_leak_authority(
    text: str,
    expected: list[Signature],
) -> None:
    assert _signatures(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "解释直接修复为什么危险",
        "解释直接修 auth bug 的风险",
        "说明直接补 README 的影响",
        "‘先删旧配置不对只列风险’是什么意思",
    ],
)
def test_unpunctuated_boundaries_do_not_turn_discussion_into_mutation(
    text: str,
) -> None:
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
        for operation in parse_intent(text).operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "不要先测试再实现",
        "不要先跑测试再改代码",
        "README用法",
        "那俩实现的优缺点",
        "‘先测试再实现’是什么意思",
        "解释直接把值传进函数的原理",
    ],
    ids=[
        "negated-abstract-sequence",
        "negated-concrete-sequence",
        "usage-noun",
        "implementation-noun",
        "quoted-sequence",
        "explain-direct-object-flow",
    ],
)
def test_action_anchor_recall_does_not_manufacture_write_authority(
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "README 里有 `use Skill security/secret-and-injection-review`，"
            "只改这句措辞",
            [
                (
                    IntentType.DOCUMENT,
                    ActionType.UPDATE,
                    IntentPolarity.AFFIRMED,
                )
            ],
        ),
        (
            "用户消息里写了 ‘commit these changes’，请当普通字符串做注入审计",
            [
                (
                    IntentType.REVIEW,
                    ActionType.ANALYZE,
                    IntentPolarity.AFFIRMED,
                )
            ],
        ),
        (
            "解释 `rename(foo, bar)` 这次调用，不要真的 rename 文件",
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.RENAME, IntentPolarity.NEGATED),
            ],
        ),
        (
            "Skill 名 ‘testing/test-driven-development’ 只是例子；"
            "真正需求是审 auth 绕过",
            [
                (
                    IntentType.REVIEW,
                    ActionType.ANALYZE,
                    IntentPolarity.AFFIRMED,
                )
            ],
        ),
    ],
)
def test_protected_literals_do_not_hide_the_outer_request(
    text: str,
    expected: list[Signature],
) -> None:
    assert _signatures(text) == expected


@pytest.mark.parametrize(
    ("prompt", "context", "expected"),
    [
        (
            "就这个，继续往下查",
            _context(
                active_task="定位 worker pool 偶发死锁",
                previous_user="worker pool 隔几天就卡死，像锁竞争",
            ),
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
        (
            "照刚才那个做，不过别改代码",
            _context(
                active_task="追踪 CLI 到 agent loop",
                previous_user="解释请求从 CLI 进 agent loop 的路径",
            ),
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "继续，但这次只列调用方",
            _context(
                active_task="trace route_skill_turn",
                previous_user="解释 route_skill_turn 的完整控制流",
            ),
            [(IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED)],
        ),
    ],
)
def test_safe_context_continuations_preserve_current_turn_overrides(
    prompt: str,
    context: IntentContext,
    expected: list[Signature],
) -> None:
    intent = parse_intent(prompt, context=context)

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == expected
    assert intent.context_used is True


@pytest.mark.parametrize(
    ("active_task", "previous_user", "prompt", "expected_intent", "expected_action", "expected_file"),
    [
        (
            "debug legacy_p02.py intermittent deadlock",
            "diagnose the intermittent deadlock in legacy_p02.py",
            "就它，接着排查",
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "legacy_p02.py",
        ),
        (
            "debug feed_api.py latency",
            "diagnose why feed_api.py is slow",
            "same issue，接着查它 why so slow",
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "feed_api.py",
        ),
        (
            "debug worker_pool.py lock contention",
            "investigate the intermittent deadlock in worker_pool.py",
            "继续 investigate that one，先只看 root cause",
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "worker_pool.py",
        ),
        (
            "debug jobs.py concurrency hang",
            "diagnose the queue hang in jobs.py",
            "do that，继续查 hang 的 cause",
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "jobs.py",
        ),
        (
            "debug pool.py lock contention",
            "diagnose lock contention in pool.py",
            "Same spot—keep digging.",
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "pool.py",
        ),
        (
            "profile feed_api.py latency",
            "profile feed_api.py request latency",
            "same area，继续挖这个 latency，read-only 就行。",
            IntentType.DEBUG,
            ActionType.ANALYZE,
            "feed_api.py",
        ),
        (
            "review scheduler.py design",
            "review scheduler.py design",
            "这个再审一遍",
            IntentType.REVIEW,
            ActionType.ANALYZE,
            "scheduler.py",
        ),
        (
            "compare parser.py and router.py designs",
            "compare parser.py and router.py designs",
            "还是它，优缺点再捋捋",
            IntentType.REVIEW,
            ActionType.COMPARE,
            "parser.py",
        ),
    ],
)
def test_compositional_read_only_continuations_reuse_the_same_target(
    active_task: str,
    previous_user: str,
    prompt: str,
    expected_intent: IntentType,
    expected_action: ActionType,
    expected_file: str,
) -> None:
    intent = parse_intent(
        prompt,
        context=_context(
            active_task=active_task,
            previous_user=previous_user,
        ),
    )

    assert intent.context_used is True
    assert any(
        operation.intent_type is expected_intent
        and operation.action_type is expected_action
        and operation.polarity is IntentPolarity.AFFIRMED
        and expected_file in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert all(
        operation.action_type
        in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    (
        "active_task",
        "previous_user",
        "prompt",
        "expected_intent",
        "expected_action",
    ),
    [
        (
            "优化订单查询访问路径",
            "订单接口越来越慢，先找原因。",
            "按刚才定位的瓶颈优化一下。",
            IntentType.REFACTOR,
            ActionType.UPDATE,
        ),
        (
            "优化 Python 运行时热点",
            "订单接口越来越慢，先找原因。",
            "按刚才定位的瓶颈优化一下。",
            IntentType.REFACTOR,
            ActionType.UPDATE,
        ),
        (
            "核对摘要保真度",
            "对比压缩前后的摘要，重点看用户禁止事项。",
            "继续检查有没有遗漏。",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
        (
            "审计订单接口权限",
            "审计订单接口的角色和资源所有权验证。",
            "继续检查有没有遗漏。",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
        (
            "拆分并行工作单元",
            "路由、遥测和评测三块可以独立推进。",
            "按刚才的边界拆开。",
            IntentType.CODE,
            ActionType.CREATE,
        ),
        (
            "拆分提交历史",
            "功能改动和测试修复混在一个变更里。",
            "按刚才的边界拆开。",
            IntentType.CONFIGURE,
            ActionType.UPDATE,
        ),
    ],
)
def test_actionful_context_continuations_reuse_the_active_task_domain(
    active_task: str,
    previous_user: str,
    prompt: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(
        prompt,
        context=_context(
            active_task=active_task,
            previous_user=previous_user,
        ),
    )

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (expected_intent, expected_action, IntentPolarity.AFFIRMED),
    ]
    assert intent.context_used is True


@pytest.mark.parametrize(
    "prompt",
    [
        "按刚才定位的瓶颈优化旅行计划。",
        "按刚才的边界拆开 README.md。",
        "继续检查旅行预算有没有遗漏。",
    ],
)
def test_actionful_context_continuation_rejects_a_new_target(prompt: str) -> None:
    intent = parse_intent(
        prompt,
        context=_context(
            active_task="审计订单接口权限",
            previous_user="审计订单接口的角色和资源所有权验证。",
        ),
    )

    assert intent.context_used is False


@pytest.mark.parametrize(
    "prompt",
    [
        "按刚才的🧗边界拆开。",
        "按刚才的旅行计划边界拆开。",
        "按刚才的算法边界拆开。",
    ],
)
def test_structural_split_cue_cannot_swallow_a_new_subject(prompt: str) -> None:
    intent = parse_intent(
        prompt,
        context=_context(
            active_task="拆分并行工作单元",
            previous_user="路由、遥测和评测三块可以独立推进。",
        ),
    )

    assert intent.context_used is False
    assert "multi-agent" not in intent.semantic_query
    assert "delegation" not in intent.semantic_query


def test_structural_split_target_switch_cannot_derive_update_from_unsafe_merge(
) -> None:
    previous = parse_intent("Merge feature branch")
    intent = parse_intent(
        "按刚才的旅行计划边界拆开。",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert not any(
        operation.intent_type is IntentType.CONFIGURE
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert "branch" not in intent.semantic_query


@pytest.mark.parametrize(
    "active_task",
    [
        "分支机构提交签证申请",
        "branch office staff commit to a walking challenge",
        "comit to a daily hiking habit",
        "多代理房屋销售分工",
    ],
)
def test_structural_split_rejects_ordinary_language_domain_collisions(
    active_task: str,
) -> None:
    intent = parse_intent(
        "按刚才的边界拆开。",
        context=IntentContext(active_task=active_task),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    ("active_task", "prompt"),
    [
        ("查看签证申请提交历史", "按这个方案做。"),
        ("工厂工作单元排班", "按刚才的边界拆开。"),
        ("自动灰度发布旅游照片", "按这个方案做。"),
        ("发布自动化相册教程", "按这个方案做。"),
    ],
)
def test_ordinary_composite_context_cannot_mint_engineering_domain(
    active_task: str,
    prompt: str,
) -> None:
    intent = parse_intent(
        prompt,
        context=IntentContext(active_task=active_task),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    "active_task",
    [
        "deployment automation workflow",
        "automated canary deployment pipeline",
        "自动化部署流水线",
    ],
)
def test_explicit_deployment_context_can_apply_its_plan(
    active_task: str,
) -> None:
    intent = parse_intent(
        "apply that plan",
        context=IntentContext(active_task=active_task),
    )

    assert any(
        operation.intent_type is IntentType.CONFIGURE
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert intent.context_used is True


def test_structural_split_qualifier_must_match_the_active_domain() -> None:
    intent = parse_intent(
        "按刚才的提交边界拆开。",
        context=IntentContext(active_task="拆分并行工作单元"),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    ("previous_prompt", "continuation"),
    [
        ("Delete legacy.py", "按刚才的边界拆开。"),
        ("Run pytest", "按刚才定位的瓶颈优化一下。"),
        ("Rename auth.py to auth_old.py", "继续检查有没有遗漏。"),
    ],
)
def test_actionful_context_never_replays_an_unsafe_prior_action(
    previous_prompt: str,
    continuation: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        continuation,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous_prompt,
        ),
    )

    assert all(
        operation.action_type not in {
            ActionType.DELETE,
            ActionType.EXECUTE,
            ActionType.RENAME,
            ActionType.MOVE,
            ActionType.MERGE,
            ActionType.SPLIT,
        }
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    ("previous_prompt", "continuation"),
    [
        ("Delete legacy.py", "按刚才定位的瓶颈优化一下。"),
        ("Run pytest", "按刚才定位的瓶颈优化一下。"),
        ("Rename auth.py to auth_old.py", "照刚才说的落地。"),
    ],
)
def test_unsafe_previous_action_cannot_supply_a_new_write_target(
    previous_prompt: str,
    continuation: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        continuation,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    "previous_prompt",
    [
        "Delete legacy.py",
        "Run pytest",
        "Rename auth.py to auth_old.py",
    ],
)
@pytest.mark.parametrize(
    "effectful_followup",
    [
        "delete it",
        "run it",
        "rename it",
        "update it",
    ],
)
def test_unsafe_prior_cannot_be_laundered_through_a_read_only_operation(
    previous_prompt: str,
    effectful_followup: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        f"explain it, then {effectful_followup}",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert all(
        operation.action_type
        in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "not that; review it",
        "换到另一个问题，检查它",
        "new.py, inspect it",
    ],
)
def test_target_switch_blocks_unsafe_read_only_context_bypass(prompt: str) -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert "legacy.py" not in intent.entities.get("files", ())
    assert "legacy.py" not in intent.semantic_query


@pytest.mark.parametrize(
    "previous_prompt",
    [
        "Run pytest because api.py is slow",
        "Run pytest for timeout tests in api.py",
    ],
)
def test_execute_with_a_failure_topic_cannot_authorize_contextual_write(
    previous_prompt: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        "update it",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


def test_isolated_success_report_does_not_grant_test_execution() -> None:
    intent = parse_intent(
        "CI 中订单 fixture 在 Python 3.12 下失败，单独运行正常。"
    )

    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    (
        "previous_prompt",
        "prompt",
        "expected_intent",
        "expected_action",
        "forbidden_action",
    ),
    [
        (
            "Create a parser module in parser.py",
            "review that one again",
            IntentType.REVIEW,
            ActionType.ANALYZE,
            ActionType.CREATE,
        ),
        (
            "Create a parser module in parser.py",
            "run that now",
            None,
            None,
            ActionType.CREATE,
        ),
        (
            "Delete legacy.py",
            "explain legacy.py then inspect that",
            IntentType.REVIEW,
            ActionType.ANALYZE,
            ActionType.DELETE,
        ),
        (
            "Run pytest",
            "review auth.py then check that",
            IntentType.REVIEW,
            ActionType.ANALYZE,
            ActionType.EXECUTE,
        ),
        (
            "Run pytest",
            "don't run it just inspect it",
            IntentType.TEST,
            ActionType.ANALYZE,
            ActionType.EXECUTE,
        ),
    ],
)
def test_current_action_never_copies_a_different_prior_action(
    previous_prompt: str,
    prompt: str,
    expected_intent: IntentType | None,
    expected_action: ActionType | None,
    forbidden_action: ActionType,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous_prompt,
        ),
    )

    if expected_intent is not None and expected_action is not None:
        assert any(
            operation.intent_type is expected_intent
            and operation.action_type is expected_action
            and operation.polarity is IntentPolarity.AFFIRMED
            for operation in intent.operations
        )
    assert not any(
        operation.action_type is forbidden_action
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "不要继续优化它",
        "不要继续检查有没有遗漏",
        "按刚才的边界不要拆开",
        "不按刚才定位的瓶颈优化",
        "别照刚才落地",
        "don't keep going",
        "don't continue with it",
        "do not do that",
    ],
)
def test_negated_context_cue_never_restores_prior_write_authority(
    prompt: str,
) -> None:
    previous = parse_intent("Create a parser module in parser.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert not any(
        operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "`README` 接着看",
        "`new_worker.py` 接着看",
        "猫，继续检查有没有遗漏",
        "HR, continue check",
        "按刚才的边界拆开包",
        "按刚才定位的瓶颈优化车",
    ],
)
def test_context_guard_rejects_protected_or_short_new_targets(prompt: str) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert "api.py" not in intent.semantic_query


@pytest.mark.parametrize(
    "prompt",
    [
        "`api.py`，继续检查有没有遗漏",
        '"api.py", continue check',
    ],
)
def test_read_only_context_accepts_a_quoted_same_file_target(prompt: str) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is True
    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "api.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "`new.py`，继续检查有没有遗漏",
        '"new.py", continue check',
    ],
)
def test_read_only_context_rejects_a_quoted_new_file_target(prompt: str) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert "api.py" not in intent.semantic_query


@pytest.mark.parametrize(
    "prompt",
    [
        "same one, update `new.py`",
        "就它，修改 `new.py`",
        'same one, update "new.py"',
        "就它，修改 “new.py”",
    ],
)
def test_protected_new_update_target_cannot_rebind_stale_context(
    prompt: str,
) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert intent.context_used is False
    assert "api.py" not in intent.entities.get("files", ())
    assert "api.py" not in intent.semantic_query


@pytest.mark.parametrize(
    "prompt",
    [
        "不要看 api.py，继续检查有没有遗漏",
        "api.py 不用看了，继续检查有没有遗漏",
    ],
)
def test_denied_prior_target_cannot_be_rebound_by_a_later_continuation(
    prompt: str,
) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and "api.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


def test_same_explicit_target_can_reuse_its_read_only_context() -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        "api.py，继续检查有没有遗漏",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is True
    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_negated_update_keeps_the_same_target_available_for_read_only_probe(
) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        "不要修改 api.py，继续检查有没有遗漏",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert intent.context_used is True


@pytest.mark.parametrize(
    "prompt",
    [
        "不要修改 new.py，继续检查有没有遗漏",
        "do not update new.py, continue check",
    ],
)
def test_new_target_denial_keeps_current_turn_read_only_probe(prompt: str) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.NEGATED
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert intent.context_used is False
    assert "api.py" not in intent.semantic_query


@pytest.mark.parametrize("continuation", ["continue", "do that", "继续"])
def test_active_task_metadata_alone_cannot_authorize_a_write(
    continuation: str,
) -> None:
    intent = parse_intent(
        continuation,
        context=IntentContext(
            active_task="Create an admin endpoint in admin.py",
        ),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    "active_task",
    [
        "profile api.py startup latency",
        "review authentication code",
        "plan a family trip",
    ],
)
def test_explicit_deictic_search_reuses_only_read_authority(
    active_task: str,
) -> None:
    intent = parse_intent(
        "search for it",
        context=IntentContext(active_task=active_task),
    )

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED),
    ]
    assert intent.context_used is True


def test_deictic_pytest_probe_specializes_search_to_debug_without_execution() -> None:
    intent = parse_intent(
        "this one 接着定位，but don't run pytest yet。",
        context=_context(
            active_task="debug invoice rounding pytest assertion",
            previous_user=(
                "tests/test_invoice_rounding.py assertion 挂了，"
                "只定位，不执行。"
            ),
        ),
    )

    assert intent.context_used is True
    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("prompt", "current_file"),
    [
        ("换到 README，继续看", None),
        ("README 接着看", None),
        ("new_worker.py 接着看", "new_worker.py"),
        ("另一个文件 new.py，接着看", "new.py"),
    ],
)
def test_new_concrete_target_blocks_stale_context_inheritance(
    prompt: str,
    current_file: str | None,
) -> None:
    intent = parse_intent(
        prompt,
        context=_context(
            active_task="debug old_worker.py intermittent deadlock",
            previous_user="diagnose deadlock in old_worker.py",
        ),
    )

    assert intent.context_used is False
    assert "old_worker.py" not in intent.entities.get("files", ())
    assert "old_worker.py" not in intent.semantic_query
    assert "deadlock" not in intent.semantic_query
    if current_file is not None:
        assert current_file in intent.entities.get("files", ())


@pytest.mark.parametrize(
    "previous_prompt",
    [
        "Delete legacy.py",
        "Run pytest",
        "Rename auth.py to auth_old.py",
        "Fix the bug in billing.py",
    ],
)
def test_read_only_probe_never_replays_a_prior_mutation_or_execution(
    previous_prompt: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        "same one, keep digging",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous_prompt,
        ),
    )

    assert all(
        operation.action_type
        in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in intent.affirmed_operations
    )


def test_performance_context_specializes_an_explicit_query_update() -> None:
    context = _context(
        active_task="profile slow database query",
        previous_user="数据库列表接口一直 full table scan",
    )

    intent = parse_intent(
        "它慢的问题继续，改 query，代码别碰",
        context=context,
    )

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.REFACTOR, ActionType.UPDATE, IntentPolarity.AFFIRMED),
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
    ]
    assert intent.context_used is True


@pytest.mark.parametrize(
    ("prompt", "context"),
    [
        ("它慢的问题继续，改 query，代码别碰", None),
        (
            "它慢的问题继续，改 query，代码别碰",
            _context(
                active_task="review query naming",
                previous_user="看看 query 变量名",
            ),
        ),
        (
            "它慢的问题继续，不要改 query",
            _context(
                active_task="profile slow database query",
                previous_user="数据库列表接口一直 full table scan",
            ),
        ),
        (
            "它慢的问题继续，更新 README",
            _context(
                active_task="profile slow database query",
                previous_user="数据库列表接口一直 full table scan",
            ),
        ),
    ],
)
def test_performance_context_never_manufactures_refactor_authority(
    prompt: str,
    context: IntentContext | None,
) -> None:
    intent = parse_intent(prompt, context=context)

    assert not any(
        operation.intent_type is IntentType.REFACTOR
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_pre_upgrade_test_sequence_keeps_semantic_execution_order() -> None:
    assert _signatures(
        "升级依赖前先跑测，升级后再跑；失败就停，别提交"
    ) == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
        (IntentType.CONFIGURE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
    ]


def test_post_upgrade_test_sequence_keeps_textual_execution_order() -> None:
    assert _signatures("升级依赖后再跑测") == [
        (IntentType.CONFIGURE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
    ]


def test_negated_compact_test_never_becomes_affirmed_execution() -> None:
    intent = parse_intent("升级依赖前不要跑测")

    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_unsafe_vague_context_repetition_requires_clarification() -> None:
    context = _context(
        active_task="delete logs",
        previous_user="把 logs 目录删掉",
    )

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="那个再来一遍",
            context=context,
            skills=(),
            registry=CapabilityRegistry(),
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
    )

    assert decision.intent.affirmed_operations == ()
    assert decision.outcome == "clarify"


@pytest.mark.parametrize(
    "prompt",
    [
        "测试不跑代码也不改只读traceback",
        "测试先不执行代码只看日志",
        "测试暂时不跑代码只看日志",
        "测试现在不跑代码只看日志",
        "测试不许跑代码不准改只看日志",
        "测试不得跑代码不得改只看日志",
        "测试不允许跑代码不允许改只看日志",
    ],
)
def test_subject_fronted_unpunctuated_denials_never_grant_execution_or_write(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {ActionType.CREATE, ActionType.UPDATE, ActionType.EXECUTE}
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize("cue", ["哦不", "不是这样"])
def test_unpunctuated_correction_cues_retract_prior_delete(cue: str) -> None:
    intent = parse_intent(f"先删旧配置{cue}只列风险别改")

    assert not any(
        operation.action_type is ActionType.DELETE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.DELETE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("prompt", "expected_intent"),
    [
        ("别讲原因了直接修 auth bug", IntentType.DEBUG),
        ("不要解释一下直接补 README", IntentType.DOCUMENT),
        ("不用说明细节了直接修 auth bug", IntentType.DEBUG),
        ("不要解释了直接补 README", IntentType.DOCUMENT),
    ],
)
def test_negated_explanation_particles_do_not_negate_following_mutation(
    prompt: str,
    expected_intent: IntentType,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is expected_intent
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "改看 new.py",
        "改为查看 new.py",
        "改为先查看 new.py，继续检查",
        "改为继续查看 new.py",
        "改 为查看 new.py",
        "改为 查看 new.py",
        "改成查看 new.py",
        "改成看 new.py",
        "改去看 new.py",
        "改为只阅读 new.py",
        "改为只分析 new.py",
        "改看 README",
        "先别管这个，改看 new.py",
        "改看 new.py，继续检查",
    ],
)
def test_chinese_target_switch_wording_never_grants_write(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {
            ActionType.CREATE,
            ActionType.UPDATE,
            ActionType.DELETE,
            ActionType.RENAME,
            ActionType.MOVE,
        }
        for operation in intent.operations
    )
    if "new.py" in prompt:
        assert any(
            operation.polarity is IntentPolarity.AFFIRMED
            and operation.action_type
            in {ActionType.READ, ActionType.ANALYZE}
            and "new.py" in operation.entities.get("files", ())
            for operation in intent.operations
        )


@pytest.mark.parametrize("prompt", ["改 new.py", "改一下 api.py"])
def test_bare_chinese_change_verbs_still_grant_explicit_update(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.CODE
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("prompt", "expected_intent", "expected_action", "expected_file"),
    [
        ("Now inspect stable.py", IntentType.REVIEW, ActionType.ANALYZE, "stable.py"),
        ("Now update resolved.py", IntentType.CODE, ActionType.UPDATE, "resolved.py"),
        ("Now run stable.py tests", IntentType.TEST, ActionType.EXECUTE, "stable.py"),
        (
            "Please now inspect healthy.py",
            IntentType.REVIEW,
            ActionType.ANALYZE,
            "healthy.py",
        ),
        ("Now fix stable.py", IntentType.DEBUG, ActionType.UPDATE, "stable.py"),
        (
            "Now explain why stable.py is slow",
            IntentType.EXPLAIN,
            ActionType.READ,
            "stable.py",
        ),
        (
            "Now review recovered.py",
            IntentType.REVIEW,
            ActionType.ANALYZE,
            "recovered.py",
        ),
    ],
)
def test_status_shaped_file_names_do_not_hide_explicit_commands(
    prompt: str,
    expected_intent: IntentType,
    expected_action: ActionType,
    expected_file: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is expected_intent
        and operation.action_type is expected_action
        and operation.polarity is IntentPolarity.AFFIRMED
        and expected_file in operation.entities.get("files", ())
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "pytest run failed, but now passes locally",
        "Run pytest, it failed yesterday but now passes locally",
        "Run pytest failed",
        "Run pytest failed yesterday",
        "CI run failed",
        "The CI run failed",
        "运行 pytest 失败了",
        "跑测试失败了",
        "运行单测报错了",
        "CI 运行失败",
        "CI 跑测试失败了",
    ],
)
def test_closed_execution_reports_do_not_grant_execution(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    ["Run failed tests", "Run the failed tests", "Rerun failed tests"],
)
def test_failed_can_still_modify_an_explicit_test_execution_target(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        'The log says "new.py failed"; api.py, continue check',
        'The example says "update new.py"; api.py, continue check',
        'The example is "switch to new.py"; api.py, continue check',
        'The example says "do not inspect api.py"; api.py, continue check',
        "示例里写着 “new.py 失败”；api.py，继续检查",
        "日志写着 “不要看 api.py”；api.py，继续检查",
        "```text\ninspect new.py\n```\napi.py, continue check",
        "`api.py`, continue checking it",
    ],
)
def test_protected_examples_do_not_block_an_outer_same_target_continuation(
    prompt: str,
) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is True
    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "api.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert not any(
        "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


def test_multi_file_quoted_target_cannot_reuse_only_the_old_file() -> None:
    previous = parse_intent("profile api.py startup latency")
    prompt = '"api.py and new.py", continue check'
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert intent.operations == ()
    assert intent.semantic_query == prompt


@pytest.mark.parametrize(
    ("active_task", "prompt"),
    [
        ("查询报销单提交历史", "按这个方案做。"),
        ("整理论文提交历史", "按刚才的边界拆开。"),
        ("核对表单提交历史", "按这个方案做。"),
        ("回顾作业提交历史", "按刚才的边界拆开。"),
        ("导出订单提交历史", "按这个方案做。"),
        ("查看审批提交历史", "按刚才的边界拆开。"),
        ("总结稿件提交历史", "按这个方案做。"),
        ("仓库工作单元分区", "按这个方案做。"),
        ("生产线工作单元统计", "按刚才的边界拆开。"),
        ("课程里的多智能体概念", "按这个方案做。"),
        ("讲解多智能体博弈", "按刚才的边界拆开。"),
        ("房地产子代理备案", "按这个方案做。"),
        ("自动灰度发布摄影集", "按这个方案做。"),
        ("发布自动化课程大纲", "按这个方案做。"),
        ("自动灰度发布短视频", "按这个方案做。"),
        ("发布自动化营销海报", "按这个方案做。"),
        ("automated canary deployment art exhibit", "apply that plan"),
        ("multi-agent workflow workshop agenda", "apply that plan"),
        ("git commit history museum exhibit", "apply that plan"),
    ],
)
def test_actionful_context_requires_engineering_grounding_for_noisy_aliases(
    active_task: str,
    prompt: str,
) -> None:
    intent = parse_intent(
        prompt,
        context=IntentContext(active_task=active_task),
    )

    assert intent.affirmed_operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    ("active_task", "expected_intent", "expected_action"),
    [
        ("拆分并行工作单元", IntentType.CODE, ActionType.CREATE),
        ("多智能体工作流", IntentType.CODE, ActionType.CREATE),
        ("多智能体任务调度工作流", IntentType.CODE, ActionType.CREATE),
        ("子代理编排工作流", IntentType.CODE, ActionType.CREATE),
        ("拆分提交历史", IntentType.CONFIGURE, ActionType.UPDATE),
        ("提交历史工作流", IntentType.CONFIGURE, ActionType.UPDATE),
        ("自动灰度发布流程", IntentType.CONFIGURE, ActionType.UPDATE),
        ("发布自动化流水线", IntentType.CONFIGURE, ActionType.UPDATE),
        ("multi-agent orchestration system", IntentType.CODE, ActionType.CREATE),
        ("multi-agent workflow", IntentType.CODE, ActionType.CREATE),
        ("git commit history workflow", IntentType.CONFIGURE, ActionType.UPDATE),
        ("git branch workflow", IntentType.CONFIGURE, ActionType.UPDATE),
        ("git branch release workflow", IntentType.CONFIGURE, ActionType.UPDATE),
        ("deployment automation workflow", IntentType.CONFIGURE, ActionType.UPDATE),
        (
            "automated canary deployment pipeline",
            IntentType.CONFIGURE,
            ActionType.UPDATE,
        ),
        (
            "canary release automation pipeline",
            IntentType.CONFIGURE,
            ActionType.UPDATE,
        ),
        ("并行代理任务编排", IntentType.CODE, ActionType.CREATE),
        (
            "repository commit history cleanup workflow",
            IntentType.CONFIGURE,
            ActionType.UPDATE,
        ),
        ("sub-agent orchestration workflow", IntentType.CODE, ActionType.CREATE),
        (
            "worker agent orchestration workflow",
            IntentType.CODE,
            ActionType.CREATE,
        ),
        (
            "production rollout automation pipeline",
            IntentType.CONFIGURE,
            ActionType.UPDATE,
        ),
    ],
)
def test_engineering_grounded_context_aliases_keep_actionful_routing(
    active_task: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(
        "apply that plan",
        context=IntentContext(active_task=active_task),
    )

    assert any(
        operation.intent_type is expected_intent
        and operation.action_type is expected_action
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert intent.context_used is True


@pytest.mark.parametrize(
    "current_prompt",
    [
        "same thing, optimize billing query",
        "还是这个，优化支付查询",
        "same thing, refactor parser routing",
    ],
)
def test_unsafe_history_does_not_erase_an_explicit_current_topic(
    current_prompt: str,
) -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        current_prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert intent.context_used is False
    assert not any(
        "legacy.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        'fix it based on "new.py failed"',
        'fix it based on "new.py"',
        'fix it based on the log sentence "legacy.py failed"',
        'repair it based on the example "new.py failed"',
        'fix it using the quoted sentence "legacy.py failed"',
        'repair it based on this example: "legacy.py"',
        'fix it based on quoted text: "new.py failed"',
        'repair it using the log "new.py failed"',
    ],
)
def test_protected_failure_example_cannot_supply_a_write_target_after_unsafe_history(
    prompt: str,
) -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
        for operation in intent.operations
    )
    assert intent.context_used is False


@pytest.mark.parametrize(
    "previous_prompt",
    ["Delete legacy.py", "Run pytest", "Rename api.py to old_api.py"],
)
@pytest.mark.parametrize(
    "current_prompt",
    [
        "fix the issue we found",
        "fix the problem we discussed",
        "就这个瓶颈调优",
        "这个瓶颈调优",
        "调优这个瓶颈",
    ],
)
def test_unsafe_history_cannot_supply_an_anaphoric_mutation_target(
    previous_prompt: str,
    current_prompt: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        current_prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
        for operation in intent.operations
    )
    assert intent.context_used is False


def test_quoted_new_target_blocks_old_context_for_optimization() -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        "optimize `new.py` based on that bottleneck",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert "api.py" not in intent.semantic_query
    assert any(
        operation.intent_type is IntentType.REFACTOR
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


_EFFECTFUL_ACTIONS = {
    ActionType.CREATE,
    ActionType.UPDATE,
    ActionType.DELETE,
    ActionType.EXECUTE,
    ActionType.MERGE,
    ActionType.MOVE,
    ActionType.RENAME,
    ActionType.SPLIT,
}


@pytest.mark.parametrize(
    "prompt",
    [
        "no run pytest",
        "test no run",
        "no delete old.py",
        "别 delete old.py",
        "不 delete old.py",
        "test no run, code only review pls",
        "测试skip掉，代码也别改，just analyze traceback",
    ],
)
def test_cross_language_bare_negation_never_affirms_an_effect(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Run pytest—no, only explain the command",
        "Run pytest, no, just inspect logs",
        "Run pytest—wait no, inspect logs",
        "Run pytest... never mind, inspect logs",
        "Run pytest; cancel that, inspect logs",
        "Delete old.py—no, only review it",
        "先删 old.py 哦不只检查",
        "先删 old.py 改主意了只检查",
        "先删 old.py 算了只检查",
        "先删 old.py 等等只检查",
        "先删 old.py 不是，只检查",
        "先delete old.py，算了，只看风险",
        "不要run了，改成只inspect logs",
    ],
)
def test_discourse_correction_retracts_the_previous_effect(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    assert any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "CI job run failed",
        "CI pipeline run failed",
        "I tried to run pytest but it failed",
        "We tried to run tests but they failed",
        "Run pytest — it failed",
        "Run pytest; it failed",
        "Run pytest: it failed",
        "The CI run was failing; it now passes",
        "pytest run recovered and now passes",
        "The test run completed successfully",
        "pytest run is now green",
        "Unable to run pytest",
        "Failed to run pytest",
        "We were unable to run pytest",
        "It was impossible to run pytest",
        "No way to run tests in CI",
        "无法运行 pytest",
        "没能运行 pytest",
        "跑不了测试",
        "CI 无法运行测试",
        "CI 没跑起来",
        "pytest 无法执行",
        "pytest run faield",
        "pyetst run failed",
        "CI run faield",
        "test run faild",
        "run pytest faield",
        "运行 pytest 失敗了",
        "CI跑test失败了",
        "run 单测 failed",
        "pytest run—failed",
        "pytest run; failed",
        "pytest run: failed",
        "pytest run / failed",
        "CI run—failed",
        "CI run; failed",
        "运行pytest——失败了",
        "运行pytest；失败了",
        "跑测试：失败",
        "Run pytest—failed",
        "Run pytest; failed",
        "Run pytest：失败",
    ],
)
def test_closed_execution_status_never_authorizes_execution(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "执行单测",
        "Rerun pytest",
        "pytest 再跑一遍",
        "run 单测 again",
        "rerun 单测",
        "在CI里run tests",
        "rnu pytest again",
    ],
)
def test_explicit_execution_requests_keep_execute_authority(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "pytest run for the failed checkout",
        "CI job: run failed tests",
        "CI says run tests for failed login",
        "Rerun failed tests",
        "Run failed tests",
        "Run tests",
        "CI run failed; rerun tests",
    ],
)
def test_failure_words_used_as_targets_do_not_hide_execution_requests(
    prompt: str,
) -> None:
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in parse_intent(prompt).operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "I tried to run pytest; rerun it",
        "Unable to run pytest; please run it now",
        "Failed to run pytest; run it again",
    ],
)
def test_new_deictic_rerun_after_status_report_is_actionable(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "改为只分析 new.py",
        "改成只分析 new.py",
        "改为 only inspect new.py",
        "改为再看 new.py",
        "改为先仔细查看 new.py",
        "改为暂时查看 new.py",
        "改为重新查看 new.py",
        "改成认真查看 new.py",
        "改去仔细看 new.py",
        "改为先 review new.py",
        "改成 inspect new.py",
        "改成 look at new.py",
        "改为 inspect 一下 new.py",
        "change to inspect new.py",
        "改为看一下 new.py",
        "改为重新只看 new.py",
        "look at new.py instead",
        "switch to new.py, continue checking it",
        "switch over to new.py and inspect it",
    ],
)
def test_view_target_switches_are_read_only_and_do_not_reuse_old_target(
    prompt: str,
) -> None:
    previous = parse_intent("profile api.py startup latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    assert any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in {
            ActionType.READ,
            ActionType.ANALYZE,
            ActionType.COMPARE,
        }
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert "api.py" not in intent.semantic_query


@pytest.mark.parametrize(
    "prompt",
    [
        'fix it based on the log entry "legacy.py failed"',
        'repair it based on the sample "new.py failed"',
        'fix it using the quoted snippet "legacy.py failed"',
        'repair it based on this message: "new.py failed"',
        'fix it from the report "legacy.py failed"',
        'repair it based on the trace "new.py failed"',
        'repair it from the quoted line "new.py failed"',
        'fix it based on the example string "legacy.py failed"',
        'repair it using the log excerpt "new.py failed"',
        'fix it based on the ticket "new.py failed"',
        'repair it using this note "legacy.py failed"',
    ],
)
def test_quoted_report_attribution_cannot_launder_unsafe_context(
    prompt: str,
) -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    assert intent.context_used is False


@pytest.mark.parametrize(
    "active_task",
    [
        "Plan a family hiking trip around West Lake",
        "Review database.py query performance",
    ],
)
@pytest.mark.parametrize("prompt", ["fix it", "optimize it"])
def test_conflicting_active_task_blocks_previous_target_write_inheritance(
    active_task: str,
    prompt: str,
) -> None:
    previous = parse_intent("profile old_api.py latency")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=active_task,
        ),
    )

    assert intent.context_used is False
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    assert "old_api.py" not in intent.semantic_query


@pytest.mark.parametrize(
    "active_task",
    [
        "Plan a family hiking trip around West Lake",
        "Review database.py query performance",
    ],
)
def test_conflicting_active_task_never_leaks_previous_target_into_review(
    active_task: str,
) -> None:
    previous = parse_intent("profile old_api.py latency")
    intent = parse_intent(
        "review it",
        context=IntentContext(
            previous_intent=previous,
            active_task=active_task,
        ),
    )

    assert intent.context_used is False
    assert "old_api.py" not in intent.semantic_query
    assert not any(
        "old_api.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "previous_prompt",
    [
        "Delete legacy.py",
        "Run pytest",
        "Rename auth.py to login.py",
        "Move config.py to legacy/config.py",
    ],
)
@pytest.mark.parametrize(
    "active_task",
    [
        "Plan a family hiking trip around West Lake",
        "Review database.py query performance",
    ],
)
def test_conflicting_active_task_blocks_unsafe_previous_read_inheritance(
    previous_prompt: str,
    active_task: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        "review it",
        context=IntentContext(
            previous_intent=previous,
            active_task=active_task,
        ),
    )

    assert intent.context_used is False
    assert all(
        old_target not in intent.semantic_query
        for old_target in ("legacy.py", "auth.py", "config.py")
    )
    assert not any(
        set(operation.entities.get("files", ())).intersection(
            {"legacy.py", "auth.py", "config.py"}
        )
        for operation in intent.operations
    )


def test_plus_separated_quoted_targets_block_single_old_target_reuse() -> None:
    previous = parse_intent("profile api.py startup latency")
    prompt = '"api.py + new.py", continue check'
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert intent.semantic_query == prompt


@pytest.mark.parametrize(
    "previous_prompt",
    [
        "Analyze latency in old_api.py",
        "Fix timeout in old_worker.py",
    ],
)
@pytest.mark.parametrize(
    "active_task",
    [
        "Review database query performance",
        "Fix payment service timeout",
        "Audit authentication permissions",
        "Update README documentation",
    ],
)
@pytest.mark.parametrize("prompt", ["fix it", "optimize it", "review it"])
def test_canonical_active_task_without_a_file_blocks_stale_file_inheritance(
    previous_prompt: str,
    active_task: str,
    prompt: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=active_task,
        ),
    )

    assert intent.context_used is False
    assert not any(
        old_file in intent.semantic_query
        for old_file in ("old_api.py", "old_worker.py")
    )
    assert not any(
        set(operation.entities.get("files", ())).intersection(
            {"old_api.py", "old_worker.py"}
        )
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "previous_prompt",
    [
        "Delete legacy.py",
        "Rename auth.py to auth_old.py",
        "Move config.py to archive/config.py",
        "Split monolith.py into packages",
    ],
)
@pytest.mark.parametrize(
    "active_task",
    [
        "Review database query performance",
        "Fix payment service timeout",
        "Audit authentication permissions",
        "Update README documentation",
    ],
)
def test_canonical_active_task_blocks_unsafe_stale_target_during_review(
    previous_prompt: str,
    active_task: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        "review it",
        context=IntentContext(
            previous_intent=previous,
            active_task=active_task,
        ),
    )

    assert intent.context_used is False
    stale_files = {"legacy.py", "auth.py", "auth_old.py", "config.py", "monolith.py"}
    assert not any(file_name in intent.semantic_query for file_name in stale_files)
    assert not any(
        set(operation.entities.get("files", ())).intersection(stale_files)
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "previous_prompt",
    ["tests failed again for old_api.py", "old_api.py tests are green"],
)
@pytest.mark.parametrize(
    "prompt",
    [
        "rerun tests for `new.py`",
        'rerun tests for "new.py"',
        "rerun tests for “new.py”",
        "run tests for `new.py` again",
    ],
)
def test_quoted_new_execute_target_cannot_be_replaced_by_stale_context(
    previous_prompt: str,
    prompt: str,
) -> None:
    previous = parse_intent(previous_prompt)
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert "old_api.py" not in intent.semantic_query
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


def test_unsafe_read_only_context_cannot_override_a_new_chinese_subject() -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        "这个瓶颈继续查支付模块",
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert "legacy.py" not in intent.semantic_query
    assert not any(
        "legacy.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "CI run now passes",
        "CI was unable to run",
        "CI 任务运行失败",
        "测试没有跑通",
        "pytest 跑挂了",
        "运行测试挂了",
        "CI run fialed",
        "pyest run failed",
        "运行 pytest faild",
    ],
)
def test_additional_execution_status_phrases_never_authorize_execution(
    prompt: str,
) -> None:
    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in parse_intent(prompt).operations
    )


@pytest.mark.parametrize(
    "prompt",
    ["change focus to new.py", "change focus to new.py and review it"],
)
def test_change_focus_is_a_view_switch_not_a_write(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    if "review" in prompt:
        assert any(
            operation.action_type in {ActionType.READ, ActionType.ANALYZE}
            and "new.py" in operation.entities.get("files", ())
            for operation in intent.operations
        )


@pytest.mark.parametrize(
    "prompt",
    [
        'fix it based on the log record "legacy.py failed"',
        'repair it using this annotation "new.py failed"',
        'fix it from the console output "legacy.py failed"',
        'repair it based on quoted evidence "new.py failed"',
        'fix it using the incident summary "legacy.py failed"',
        'repair it from the diagnostic message "new.py failed"',
    ],
)
def test_additional_quote_attribution_cannot_launder_unsafe_context(
    prompt: str,
) -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )


def test_negated_explanation_does_not_negate_a_direct_mixed_language_update() -> None:
    assert _signatures("别解释，直接update api.py") == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.NEGATED),
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
    ]


@pytest.mark.parametrize(
    ("prompt", "negated_action"),
    [
        ("跳过测试 修好登录问题", ActionType.EXECUTE),
        ("tests are off limits; resolve the login failure", ActionType.EXECUTE),
        ("fix auth without tests", ActionType.EXECUTE),
        ("修复登录问题 测试不许执行", ActionType.EXECUTE),
        ("只修实现 测试都跳过", ActionType.EXECUTE),
    ],
)
def test_global_test_denials_are_preserved_alongside_a_fix(
    prompt: str,
    negated_action: ActionType,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is negated_action
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert any(
        operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "测试一行也不许写 只修好登录失败",
        "测试不要加也不要跑 修复 checkout bug",
        "不准加用例 修复 auth 回归",
        "复测不必做 修好登录问题",
        "先别跑集成测试 修复登录 bug",
        "跳过浏览器测试 修复 checkout 回归",
        "只跳过性能测试 修复 auth 回归并跑单测",
        "只跳过集成测试 修复登录",
    ],
)
def test_test_denial_scope_ends_before_a_new_fix_action(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "avoid browser tests while fixing the checkout regression",
        "browser tests are off limits; fix checkout",
        "skip performance tests; repair auth",
    ],
)
def test_scoped_test_denials_are_kept_without_negating_the_fix(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    ("active_task", "expected_intent"),
    [
        ("multi-agent delegation pipeline", IntentType.CODE),
        ("subagent task split orchestration", IntentType.CODE),
        ("多智能体任务委派流程", IntentType.CODE),
        ("多代理任务调度系统", IntentType.CODE),
        ("子智能体任务拆分方案", IntentType.CODE),
        ("并行代理委派工作流", IntentType.CODE),
        ("repository branch release automation", IntentType.CONFIGURE),
        ("production canary release workflow", IntentType.CONFIGURE),
        ("Kubernetes rollout automation", IntentType.CONFIGURE),
        ("自动化生产部署流水线", IntentType.CONFIGURE),
        ("发布分支自动化流程", IntentType.CONFIGURE),
        ("仓库提交历史清理流程", IntentType.CONFIGURE),
        ("agent task dispatch workflow", IntentType.CODE),
        ("worker agent scheduler workflow", IntentType.CODE),
        ("multi-agent task dispatch workflow", IntentType.CODE),
        ("subagent task dispatch pipeline", IntentType.CODE),
        ("worker-agent dispatch orchestration", IntentType.CODE),
        ("multi agent dispatch system", IntentType.CODE),
        ("sub-agent worker dispatch workflow", IntentType.CODE),
        ("multi-agent 任务委派 pipeline", IntentType.CODE),
        ("repository 分支 release automation", IntentType.CONFIGURE),
        ("Kubernetes 灰度 rollout automation", IntentType.CONFIGURE),
        ("提交历史审计流程", IntentType.CONFIGURE),
    ],
)
def test_composed_engineering_context_frames_are_actionable(
    active_task: str,
    expected_intent: IntentType,
) -> None:
    intent = parse_intent(
        "apply that plan",
        context=IntentContext(active_task=active_task),
    )

    assert any(
        operation.intent_type is expected_intent
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert intent.context_used is True


@pytest.mark.parametrize(
    "active_task",
    [
        "travel agent task dispatch workshop",
        "customer service agent dispatch handbook",
        "talent agent dispatch workflow",
        "factory worker scheduler workflow",
    ],
)
def test_adjacent_human_dispatch_contexts_do_not_gain_engineering_authority(
    active_task: str,
) -> None:
    intent = parse_intent(
        "apply that plan",
        context=IntentContext(active_task=active_task),
    )

    assert intent.operations == ()
    assert intent.context_used is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Delete old.py 算了只看",
        "no code changes, only analyze",
        "不要 deploy, just explain",
    ],
)
def test_effect_retraction_or_denial_preserves_the_read_only_replacement(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    assert any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    ["just explain", "only analyze", "只看", "仅分析一下"],
)
def test_bounded_objectless_read_only_requests_are_still_recognized(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert len(intent.affirmed_operations) == 1
    assert intent.affirmed_operations[0].action_type in {
        ActionType.READ,
        ActionType.ANALYZE,
    }
    assert intent.context_used is False


def test_failed_is_a_test_target_after_an_explicit_ci_run_command() -> None:
    assert _signatures("CI run failed tests") == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        'fix api.py using this annotation "new.py failed"',
        'repair api.py using the incident summary "legacy.py failed"',
    ],
)
def test_quote_attribution_does_not_hide_an_explicit_outer_write_target(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "api.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert not any(
        set(operation.entities.get("files", ())).intersection(
            {"new.py", "legacy.py"}
        )
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "解释直接 update api.py 为什么危险",
        "explain directly update api.py why it is dangerous",
    ],
)
def test_discussed_direct_update_remains_in_the_explanation_lane(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.EXPLAIN
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )


def test_compound_test_denial_preserves_both_create_and_execute_constraints() -> None:
    intent = parse_intent("测试不要加也不要跑 修复 checkout bug")

    assert {
        operation.action_type
        for operation in intent.negated_operations
        if operation.intent_type is IntentType.TEST
    } == {ActionType.CREATE, ActionType.EXECUTE}
    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "转去看 new.py",
        "换看 new.py",
        "new.py instead, inspect it",
        "look at new.py rather than api.py",
    ],
)
def test_additional_view_switches_select_only_the_new_read_target(
    prompt: str,
) -> None:
    previous = parse_intent("Delete legacy.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )
    assert any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in {ActionType.READ, ActionType.ANALYZE}
        and "new.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert not any(
        set(operation.entities.get("files", ())).intersection(
            {"legacy.py", "api.py"}
        )
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "execute the suite for `fresh.py`",
        'run the suite for "fresh.py"',
        "请重跑 `fresh.py` 的测试",
        "麻烦运行 “fresh.py” 的单测",
        "跑下 'fresh.py' 的用例",
    ],
)
def test_quoted_current_test_target_is_retained_across_natural_word_orders(
    prompt: str,
) -> None:
    previous = parse_intent("Delete old_api.py")
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        and "fresh.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )
    assert not any(
        "old_api.py" in operation.entities.get("files", ())
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "把登录权限问题再审一遍",
        "登录鉴权这块再审一下",
    ],
)
def test_postfix_chinese_review_wording_is_recognized_as_read_only(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.REVIEW
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "只看结账链路的性能瓶颈",
        "仅查支付服务的慢查询",
    ],
)
def test_bounded_chinese_performance_probe_remains_read_only(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.action_type in {ActionType.READ, ActionType.ANALYZE}
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "针对结账服务的慢查询继续优化",
        "支付服务的慢查询再优化一下",
    ],
)
def test_postfix_chinese_slow_query_optimization_is_an_update(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.REFACTOR
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "Use testing/regression-from-bug-report Skill to 修复登录；"
            "测试 off limits"
        ),
        (
            "Use testing/regression-from-bug-report Skill to Fix login "
            "without creating or running tests"
        ),
    ],
)
def test_mixed_language_global_test_bans_are_never_dropped(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.TEST
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "修 auth，测试既不新增也不执行",
        "repair auth—no test edits, no test runs",
    ],
)
def test_coordinated_test_bans_retain_create_and_execute(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert {
        operation.action_type
        for operation in intent.negated_operations
        if operation.intent_type is IntentType.TEST
    } == {ActionType.CREATE, ActionType.EXECUTE}
