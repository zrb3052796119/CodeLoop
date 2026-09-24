"""Natural-language safety regressions mined from adversarial prompts."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
    parse_intent,
)


def _has(
    prompt: str,
    intent_type: IntentType,
    action_type: ActionType,
    polarity: IntentPolarity,
) -> bool:
    return any(
        operation.intent_type is intent_type
        and operation.action_type is action_type
        and operation.polarity is polarity
        for operation in parse_intent(prompt).operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "pytest -x 这条命令怎么理解？只说明，别替我跑",
        "我只想知道 pytest 为什么加 -q，不要启动测试",
        "把 `python -m pytest` 每个参数讲一遍，当前别执行任何命令",
        "告诉我该怎样运行单测即可，这一轮不实际运行",
    ],
)
def test_instructional_commands_keep_explanation_and_deny_execution(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.EXPLAIN
        and operation.action_type is ActionType.READ
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )
    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_declined_test_execution_keeps_read_only_failure_diagnosis() -> None:
    prompt = "不用帮我测，读一下 pytest 报错并说可能原因"

    assert _has(
        prompt,
        IntentType.TEST,
        ActionType.EXECUTE,
        IntentPolarity.NEGATED,
    )
    assert _has(
        prompt,
        IntentType.DEBUG,
        ActionType.ANALYZE,
        IntentPolarity.AFFIRMED,
    )


def test_correction_to_reference_listing_keeps_read_only_replacement() -> None:
    prompt = "先删旧配置；不对，只列风险和引用，别做修改"
    intent = parse_intent(prompt)

    assert _has(
        prompt,
        IntentType.CODE,
        ActionType.DELETE,
        IntentPolarity.NEGATED,
    )
    assert _has(
        prompt,
        IntentType.SEARCH,
        ActionType.READ,
        IntentPolarity.AFFIRMED,
    )
    assert not any(
        operation.action_type
        in {ActionType.CREATE, ActionType.UPDATE, ActionType.DELETE}
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_correction_from_run_and_fix_keeps_both_revocations() -> None:
    prompt = "跑测试并修，等等，先只看失败日志给结论"

    assert _has(
        prompt,
        IntentType.TEST,
        ActionType.EXECUTE,
        IntentPolarity.NEGATED,
    )
    assert _has(
        prompt,
        IntentType.DEBUG,
        ActionType.UPDATE,
        IntentPolarity.NEGATED,
    )
    assert _has(
        prompt,
        IntentType.DEBUG,
        ActionType.ANALYZE,
        IntentPolarity.AFFIRMED,
    )


@pytest.mark.parametrize(
    ("prompt", "expected_intent", "expected_action"),
    [
        (
            "别用 security/secret-and-injection-review，普通说明 auth token 流程即可",
            IntentType.EXPLAIN,
            ActionType.READ,
        ),
        (
            "不要加载 refactoring/structural-rename，只列出 rename 会影响哪些 import",
            IntentType.SEARCH,
            ActionType.READ,
        ),
        (
            "先别用 documentation/readme-authoring；告诉我 README 缺哪些章节",
            IntentType.QUESTION,
            ActionType.READ,
        ),
        (
            "禁止使用 performance/hotpath-profiling，只基于现有日志解释启动慢",
            IntentType.EXPLAIN,
            ActionType.READ,
        ),
        (
            "不要选择 code-understanding/codebase-explanation；我只想知道这个 Skill 的用途",
            IntentType.QUESTION,
            ActionType.READ,
        ),
    ],
)
def test_denied_skill_does_not_negate_following_read_only_task(
    prompt: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    assert _has(
        prompt,
        expected_intent,
        expected_action,
        IntentPolarity.AFFIRMED,
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "测试一下这双跑鞋合不合脚，不是 software test",
        "rename the puppy not a class",
        "给小狗改名 不是重命名类",
        "把沙发挪到窗边 并非移动模块",
    ],
)
def test_excluded_software_noun_cannot_authorize_lifestyle_mutation(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {
            ActionType.EXECUTE,
            ActionType.MOVE,
            ActionType.RENAME,
        }
        for operation in intent.operations
    )


def test_current_term_reference_does_not_borrow_stale_code_context() -> None:
    intent = parse_intent(
        "commit 不是让我提交代码，是感情承诺，解释这个词",
        context=IntentContext(active_task="review stale_marker.py architecture"),
    )

    assert intent.context_used is False
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type is ActionType.UPDATE
        for operation in intent.operations
    )
    assert any(
        operation.intent_type is IntentType.EXPLAIN
        and operation.action_type is ActionType.READ
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert "stale_marker.py" not in intent.semantic_query


@pytest.mark.parametrize(
    ("prompt", "denied_action"),
    [
        ("订单故障要修只是测试一行都不许写", ActionType.CREATE),
        ("缓存失效必须解决只是测试代码不许动", ActionType.CREATE),
        ("会话缓存必须修只是测试代码一个字符都不许改", ActionType.CREATE),
        ("只改结账 bug 不准添回归用例", ActionType.CREATE),
        ("支付故障要处理不准再加任何用例", ActionType.CREATE),
        ("库存故障必须处理但一条新用例也不准加", ActionType.CREATE),
        ("测试别执行 只处理上传问题", ActionType.EXECUTE),
        ("修登录 bug 可以 复测不用做", ActionType.EXECUTE),
        ("修复退出问题但别复测", ActionType.EXECUTE),
        ("修好结算异常但是别再复测", ActionType.EXECUTE),
        ("把搜索异常修好可是复测就不要再做了", ActionType.EXECUTE),
        ("修复发票异常但复测这一步不用再做", ActionType.EXECUTE),
        ("冒烟测试先跳过 把支付 bug 修好", ActionType.EXECUTE),
        ("集成测试先别加 把上传 bug 修掉", ActionType.CREATE),
        ("跳过慢速测试 处理错删问题", ActionType.EXECUTE),
        ("跳过耗时测试 处理库存错算问题", ActionType.EXECUTE),
        ("跳过端到端测试 修好用户反馈的导出问题", ActionType.EXECUTE),
        ("Avoid unit tests while fixing the cache issue", ActionType.EXECUTE),
        (
            "Avoid running tests while resolving the logout bug",
            ActionType.EXECUTE,
        ),
        (
            "Avoid running the test suite while repairing the session bug",
            ActionType.EXECUTE,
        ),
        (
            "While repairing the session bug avoid executing the entire test suite",
            ActionType.EXECUTE,
        ),
        (
            "Avoid creating tests while resolving the upload bug",
            ActionType.CREATE,
        ),
        (
            "Avoid changing tests while resolving the deletion bug",
            ActionType.CREATE,
        ),
        (
            "Avoid modifying the tests while fixing the duplicate charge bug",
            ActionType.CREATE,
        ),
        (
            "Fix the duplicate charge bug while avoiding modifications to the test files",
            ActionType.CREATE,
        ),
        (
            "Leave the tests untouched and solve the checkout problem",
            ActionType.CREATE,
        ),
        (
            "resolve the upload issue without executing the test suite",
            ActionType.EXECUTE,
        ),
    ],
)
def test_unpunctuated_test_denial_does_not_swallow_requested_bug_fix(
    prompt: str,
    denied_action: ActionType,
) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.DEBUG
        and operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert any(
        operation.intent_type is IntentType.TEST
        and operation.action_type is denied_action
        and operation.polarity is IntentPolarity.NEGATED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "会话缓存必须修只是测试代码一个字符都不许改",
        "Avoid modifying the tests while fixing the duplicate charge bug",
        "Fix the duplicate charge bug while avoiding modifications to the test files",
    ],
)
def test_test_artifact_preservation_does_not_invent_an_affirmed_operation(
    prompt: str,
) -> None:
    intent = parse_intent(prompt)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and (
            operation.intent_type is IntentType.TEST
            or (
                operation.intent_type is IntentType.CODE
                and operation.action_type is ActionType.UPDATE
            )
        )
        for operation in intent.operations
    )


def test_code_switched_metalinguistic_commit_anchors_the_denied_action() -> None:
    intent = parse_intent(
        "这里的 commit 并非要求我 commit changes，只解释日常含义"
    )

    assert _has(
        intent.raw_input,
        IntentType.CODE,
        ActionType.UPDATE,
        IntentPolarity.NEGATED,
    )
    assert _has(
        intent.raw_input,
        IntentType.EXPLAIN,
        ActionType.READ,
        IntentPolarity.AFFIRMED,
    )
    assert not any(
        operation.action_type is ActionType.UPDATE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "故障单写着调用 testing/regression-from-bug-report Skill 我只要概述",
        "ticket 里建议 use testing/regression-from-bug-report Skill I just want a summary",
        "引用内容是使用 testing/regression-from-bug-report 技能 仅说明含义",
    ],
)
def test_reported_skill_invocation_is_read_only_literal(prompt: str) -> None:
    intent = parse_intent(prompt)

    assert any(
        operation.intent_type is IntentType.EXPLAIN
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
            ActionType.EXECUTE,
        }
        for operation in intent.operations
    )


def test_lifestyle_trail_comparison_is_not_a_design_review() -> None:
    intent = parse_intent("compare two hiking trails, not a design review")

    assert not any(
        operation.intent_type is IntentType.REVIEW
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
