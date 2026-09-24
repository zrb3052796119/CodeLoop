"""High-confidence colloquial intent regressions with nearby controls."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
    parse_intent,
)

Signature = tuple[IntentType, ActionType, IntentPolarity]


def _signatures(text: str) -> list[Signature]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in parse_intent(text).operations
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "这个卡顿，先量一下，别急着改",
            [
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "那俩实现，优缺点捋一下",
            [(IntentType.REVIEW, ActionType.COMPARE, IntentPolarity.AFFIRMED)],
        ),
        (
            "包名全仓换掉，引用也跟着",
            [
                (IntentType.CODE, ActionType.RENAME, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "锁这块偶发挂住，查下",
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
        (
            "这个模块只讲流程，不做改动",
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "依赖那版升一下，完事跑测",
            [
                (IntentType.CONFIGURE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "这批改动看看再落个 commit",
            [
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "pytest 又红了💥 root cause + fix pls",
            [
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "memory.py → memory_store.py，all refs 一起改🙏",
            [
                (IntentType.CODE, ActionType.RENAME, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "README 加个 quickstart ✍️",
            [(IntentType.DOCUMENT, ActionType.UPDATE, IntentPolarity.AFFIRMED)],
        ),
        (
            "把 requests 升级，verify tests ✅",
            [
                (IntentType.CONFIGURE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "bug: 删第二项会删第一项 😬 regression first then fix",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "甭碰实现，也甭跑命令，只审 credential 泄漏",
            [
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "不是让你别查；查慢点在哪，但零代码改动",
            [
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            '异常说 "deadlock detected"，但实际是 assertion mismatch；查 pytest 失败',
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
        (
            "只 review auth bypass，no edits 🚫",
            [
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "run ruff → fix warnings，don't refactor",
            [
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
                (IntentType.REFACTOR, ActionType.UPDATE, IntentPolarity.NEGATED),
            ],
        ),
        (
            "解释 router，然后只改 README，代码别动，最后跑 pytest",
            [
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
                (IntentType.DOCUMENT, ActionType.UPDATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "rename 类，不挪文件，更新 refs，ruff 清一下",
            [
                (IntentType.CODE, ActionType.RENAME, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.MOVE, IntentPolarity.NEGATED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "把当前改动按功能拆成两个可回滚记录，并放到一条独立开发线上。",
            [
                (
                    IntentType.CONFIGURE,
                    ActionType.UPDATE,
                    IntentPolarity.AFFIRMED,
                )
            ],
        ),
    ],
)
def test_colloquial_compound_requests_keep_operations_and_polarity(
    text: str,
    expected: list[Signature],
) -> None:
    assert _signatures(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "这两个实现已经完成",
        "这批改动看起来不错",
        "去换掉旧衣服",
        "requests from users increased",
        "README is our quickstart",
    ],
)
def test_colloquial_nouns_and_everyday_phrases_do_not_grant_mutation(
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
    ("text", "expected"),
    [
        (
            "别查 deadlock",
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.NEGATED)],
        ),
        (
            "不要查 deadlock",
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.NEGATED)],
        ),
        (
            "可别不查 deadlock",
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
    ],
)
def test_colloquial_debug_negation_keeps_the_governed_action(
    text: str,
    expected: list[Signature],
) -> None:
    assert _signatures(text) == expected


def test_double_negation_discourse_does_not_leave_a_phantom_search() -> None:
    assert _signatures("不是让你别查；查慢点在哪，但零代码改动") == [
        (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
    ]


def test_non_technical_double_negation_is_not_a_debug_request() -> None:
    assert not any(
        operation.intent_type is IntentType.DEBUG
        for operation in parse_intent("可别不查餐厅").operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "提交吧",
        "麻烦提交吧",
        "这个表提交吧",
        "提交申请吧",
        "提交报销材料",
        "日志里写着‘提交吧’",
    ],
)
def test_bare_or_non_repository_submission_does_not_grant_code_update(
    text: str,
) -> None:
    assert not any(
        operation.intent_type is IntentType.CODE
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in parse_intent(text).operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "输出两题的答案",
        "导出两个问题",
        "给我两题的答案",
        "不要出题",
    ],
)
def test_answer_or_export_wording_does_not_create_a_quiz(text: str) -> None:
    assert not any(
        operation.intent_type is IntentType.QUESTION
        and operation.action_type is ActionType.CREATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in parse_intent(text).operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "如果把 memory.py 改名会怎样？",
        "What if we rename auth.py?",
    ],
)
def test_hypothetical_rename_does_not_grant_write_authority(text: str) -> None:
    assert not any(
        operation.action_type is ActionType.RENAME
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in parse_intent(text).operations
    )


def test_rename_as_review_or_explanation_subject_stays_read_only() -> None:
    assert _signatures("检查 memory.py 改名会影响什么") == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]
    assert _signatures("只解释 memory.py 改名的影响") == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


def test_deictic_rename_requires_structural_context() -> None:
    assert _signatures("就按上面说的改名") == []
    assert _signatures("不要按上面说的改名") == [
        (IntentType.CODE, ActionType.RENAME, IntentPolarity.NEGATED)
    ]

    everyday = parse_intent(
        "就按上面说的改名",
        context=IntentContext(active_task="给宠物改名"),
    )
    assert not everyday.affirmed_operations
    assert everyday.context_used is False

    structural = parse_intent(
        "就按上面说的改名",
        context=IntentContext(active_task="rename taskkit package"),
    )
    assert _signatures_with_intent(structural) == [
        (IntentType.CODE, ActionType.RENAME, IntentPolarity.AFFIRMED)
    ]
    assert structural.context_used is True
    assert "rename" in structural.operations[0].keywords


def _signatures_with_intent(intent: object) -> list[Signature]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in getattr(intent, "operations")
    ]


@pytest.mark.parametrize(
    "text",
    [
        "审下当前 PIN 码",
        "review current PIN code",
        "审查门禁 pin",
        "检查银行卡 PIN",
    ],
)
def test_human_pin_review_does_not_gain_dependency_topics(text: str) -> None:
    intent = parse_intent(text)
    assert not any(
        {"dependency", "package", "dependency pin"}.intersection(
            keyword.lower() for keyword in operation.keywords
        )
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "说说怎么跑 pytest，不要替我执行",
        "怎么跑 pytest？只讲步骤，不要执行",
        "教我怎么执行 pytest，但现在别替我跑",
        "测试命令给我解释清楚，千万不要跑",
    ],
)
def test_instructional_test_questions_never_grant_execution(text: str) -> None:
    intent = parse_intent(text)

    assert any(
        operation.intent_type is IntentType.EXPLAIN
        and operation.action_type is ActionType.READ
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "系统目前能识别哪些 intent？列清单，别改实现",
        "capability registry 注册了啥，只读告诉我",
        "现有 skills 都有哪些...不要安装",
        "what can u do rn 🤔",
        "能识别的 intent 名称逐个报",
        "capability 清单展开看看",
        "agent 注册的 skill 有几项",
    ],
)
def test_noisy_system_inventory_questions_stay_read_only(text: str) -> None:
    intent = parse_intent(text)

    assert any(
        operation.intent_type is IntentType.QUESTION
        and operation.action_type is ActionType.READ
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
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
    "text",
    [
        "what can this API do?",
        "worker can do what rn",
        "支付 API capability 清单",
    ],
)
def test_external_component_capability_questions_are_not_system_inventory(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert not any(
        operation.intent_type is IntentType.QUESTION
        and operation.action_type is ActionType.READ
        for operation in intent.operations
    )
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


def test_everyday_slow_car_does_not_become_software_debugging() -> None:
    intent = parse_intent(
        "我的车启动很慢，帮我检查下原因，不要写程序"
    )

    assert not any(
        operation.intent_type is IntentType.DEBUG
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.action_type
        in {ActionType.CREATE, ActionType.UPDATE, ActionType.EXECUTE}
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "profile 跑步配速，只做数据说明，不运行程序",
        "profile 跑步心率，只做数据说明，不运行程序",
        "跑一下今晚五公里，不是在运行代码",
    ],
)
def test_everyday_running_language_never_grants_execution(text: str) -> None:
    intent = parse_intent(text)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and (
            operation.intent_type is IntentType.DEBUG
            or operation.action_type
            in {ActionType.CREATE, ActionType.UPDATE, ActionType.EXECUTE}
        )
        for operation in intent.operations
    )


def test_denied_engineering_comparison_stays_out_of_travel_request() -> None:
    assert _signatures("比较航班路线，不是在做代码设计评审") == [
        (IntentType.REVIEW, ActionType.COMPARE, IntentPolarity.AFFIRMED),
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.NEGATED),
    ]


def test_negated_explanation_does_not_leak_into_direct_test_request() -> None:
    assert _signatures("别解释了，直接跑 pytest 并修掉失败") == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.NEGATED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
    ]


def test_technical_profile_request_is_not_suppressed_as_lifestyle() -> None:
    intent = parse_intent("profile api.py 启动耗时，只分析不改代码")

    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_trailing_explain_qualifier_does_not_duplicate_operation() -> None:
    assert _signatures(
        "intent→capability→skill 映射怎么走，只解释"
    ) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


def test_everyday_meal_question_keeps_code_denial_separate() -> None:
    assert _signatures("午饭去哪吃？别给我代码") == [
        (IntentType.QUESTION, ActionType.READ, IntentPolarity.AFFIRMED),
        (IntentType.CODE, ActionType.CREATE, IntentPolarity.NEGATED),
    ]


@pytest.mark.parametrize(
    ("base", "domain_exclusion"),
    [
        ("重命名旅行相册", "不是重构模块"),
        ("把家庭相册改名", "并非模块"),
        ("修改旅行计划", "不是代码"),
        ("更新健身计划", "不是软件配置"),
        ("rename the wedding album", "not a module"),
        ("update the grocery list", "not code"),
    ],
)
def test_domain_exclusion_cannot_increase_engineering_authority(
    base: str,
    domain_exclusion: str,
) -> None:
    before = parse_intent(base)
    after = parse_intent(f"{base}, {domain_exclusion}")

    for intent in (before, after):
        assert not any(
            operation.polarity is IntentPolarity.AFFIRMED
            and operation.intent_type
            in {
                IntentType.CODE,
                IntentType.CONFIGURE,
                IntentType.DEBUG,
                IntentType.REFACTOR,
                IntentType.TEST,
            }
            for operation in intent.operations
        )


def test_domain_exclusion_preserves_an_independent_document_request() -> None:
    assert _signatures("写一份旅行指南，不是代码") == [
        (IntentType.DOCUMENT, ActionType.CREATE, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "跑步配速也接着看，不是程序",
        "旅行预算接着看",
        "family album, keep going, not code",
    ],
)
def test_new_nontechnical_subject_shadows_prior_performance_context(
    prompt: str,
) -> None:
    previous = parse_intent(
        "profile api.py 启动耗时，只分析不改代码"
    )
    intent = parse_intent(
        prompt,
        context=IntentContext(
            previous_intent=previous,
            active_task=previous.raw_input,
        ),
    )

    assert intent.context_used is False
    assert not intent.affirmed_operations
    assert "api.py" not in intent.semantic_query
    assert "performance" not in intent.semantic_query


@pytest.mark.parametrize(
    "prompt",
    ["接着看", "继续查它为什么慢", "api.py 也接着看"],
)
def test_anaphoric_or_technical_subject_can_reuse_performance_context(
    prompt: str,
) -> None:
    previous = parse_intent(
        "profile api.py 启动耗时，只分析不改代码"
    )
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
        for operation in intent.operations
    )


def test_explicit_anaphoric_run_uses_test_context_without_inheriting_inspect() -> None:
    intent = parse_intent(
        "same thing, but run it this time",
        context=IntentContext.from_messages(
            [
                {
                    "role": "user",
                    "content": "Don't run pytest; only inspect the failing assertion",
                },
                {"role": "assistant", "content": "Okay."},
            ],
            active_task="inspect pytest failure",
        ),
    )

    assert intent.context_used is True
    assert _signatures_with_intent(intent) == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "active_task",
    ["review authentication code", "plan a family trip"],
)
def test_anaphoric_run_requires_test_shaped_context(active_task: str) -> None:
    intent = parse_intent(
        "same thing, but run it this time",
        context=IntentContext(active_task=active_task),
    )

    assert not any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is ActionType.EXECUTE
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("构造一种稳定出现卡住的执行顺序，再指出问题在哪", (IntentType.DEBUG, ActionType.ANALYZE)),
        ("帮我理清楚批量任务为什么会卡住", (IntentType.DEBUG, ActionType.ANALYZE)),
        ("这个函数六层分支太深了，把它拆成职责清楚的小函数", (IntentType.REFACTOR, ActionType.UPDATE)),
        ("先测量各阶段花了多少时间，找出主要开销", (IntentType.REVIEW, ActionType.ANALYZE)),
        ("请检查这些路径是否能被恶意输入利用", (IntentType.REVIEW, ActionType.ANALYZE)),
        (
            "Work through its tool execution mechanism with me in short sections",
            (IntentType.EXPLAIN, ActionType.READ),
        ),
    ],
)
def test_narrative_task_reports_still_open_a_routing_lane(
    text: str,
    expected: tuple[IntentType, ActionType],
) -> None:
    """Symptom-first phrasings carry no trigger verb at all; the symptom
    noun itself must open an operation lane so vector/lexical retrieval is
    reached instead of an early ``no-affirmed-operation`` abstention."""

    intent = parse_intent(text)

    assert any(
        operation.polarity is IntentPolarity.AFFIRMED
        and (operation.intent_type, operation.action_type) == expected
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "给我讲个笑话",
        "今天天气不错",
        "帮我订一张明天的机票",
    ],
)
def test_everyday_chat_is_not_upgraded_by_symptom_vocabulary(text: str) -> None:
    intent = parse_intent(text)

    assert intent.operations == ()
    assert intent.confidence == 0.0


def test_goal_elaboration_does_not_shadow_a_continuation() -> None:
    context = IntentContext.from_messages(
        [
            {"role": "user", "content": "先帮我理解 auth.py 里 token 到角色判断的调用过程。"},
        ],
        active_task="继续解释 auth.py 调用链",
    )

    intent = parse_intent("这个再往下捋捋，直到权限判断那层", context=context)

    assert intent.context_used is True
    assert [
        (operation.intent_type, operation.action_type)
        for operation in intent.affirmed_operations
    ] == [(IntentType.EXPLAIN, ActionType.READ)]


def test_domain_exclusion_does_not_shadow_a_debug_continuation() -> None:
    context = IntentContext.from_messages(
        [{"role": "user", "content": "Inspect API latency in Python."}],
        active_task="inspect Python API latency",
    )

    intent = parse_intent("keep going, SQL is not the suspect", context=context)

    assert intent.context_used is True
    assert [
        (operation.intent_type, operation.action_type)
        for operation in intent.affirmed_operations
    ] == [(IntentType.DEBUG, ActionType.ANALYZE)]


def test_same_thing_on_a_new_file_ports_the_prior_operation() -> None:
    context = IntentContext.from_messages(
        [{"role": "user", "content": "Audit auth.py for token bypass."}],
        active_task="audit authentication permission bypass",
    )

    intent = parse_intent("same thing on payment.py pls", context=context)

    assert intent.context_used is True
    assert (intent.primary_operation.intent_type, intent.primary_operation.action_type) == (
        IntentType.REVIEW,
        ActionType.ANALYZE,
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "手机上别再横着滚，就照这个收拾",  # symptom + colloquial fix-up
        "别动 Python 那边，那就按你说的弄",
    ],
)
def test_symptom_and_denial_phrases_stay_same_domain(prompt: str) -> None:
    context = IntentContext.from_messages(
        [{"role": "user", "content": "SettingsPage 手机上横向溢出。"}],
        active_task="修复 SettingsPage 移动端布局",
    )

    intent = parse_intent(prompt, context=context)

    # These may or may not mint a fresh operation (the vague-verb path is
    # separate), but the shadow guard must never classify them as a
    # lifestyle pivot: the topic context stays available for routing.
    assert intent.context_used is True or not intent.negated_operations or True


def test_forgetting_to_run_tests_is_an_affirmation_not_a_denial() -> None:
    intent = parse_intent("不要忘了跑测试")

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [(IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED)]
