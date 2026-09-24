from __future__ import annotations

import pytest

from minicode.intent_constraints import (
    ConstraintDiscourse,
    TestConstraintAction,
    TestConstraintScope,
    TestConstraintState,
    extract_test_constraints,
)


def test_passive_test_execution_ban_is_a_global_directive() -> None:
    text = "pytest execution is forbidden for this fix"

    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.action is TestConstraintAction.EXECUTE
    assert constraint.state is TestConstraintState.DENIED
    assert constraint.scope is TestConstraintScope.GLOBAL
    assert constraint.subtypes == ()
    assert constraint.discourse is ConstraintDiscourse.DIRECTIVE
    assert constraint.text == text[constraint.span[0] : constraint.span[1]]


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("zero test runs are allowed", TestConstraintAction.EXECUTE),
        ("omit every test execution", TestConstraintAction.EXECUTE),
        ("leave every test unrun", TestConstraintAction.EXECUTE),
        ("testing must not be executed", TestConstraintAction.EXECUTE),
        ("test creation is forbidden", TestConstraintAction.CREATE),
        ("refrain from adding any tests", TestConstraintAction.CREATE),
        ("leave all test files untouched", TestConstraintAction.CREATE),
        ("omit test authoring entirely", TestConstraintAction.CREATE),
        ("no changes to any tests", TestConstraintAction.CREATE),
    ],
)
def test_english_denial_families_map_to_the_controlled_action(
    text: str,
    action: TestConstraintAction,
) -> None:
    constraints = extract_test_constraints(text)

    assert [(item.action, item.state) for item in constraints] == [
        (action, TestConstraintState.DENIED)
    ]


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("本次修复严禁执行测试", TestConstraintAction.EXECUTE),
        ("任何测试都不要运行", TestConstraintAction.EXECUTE),
        ("测试一个也不跑", TestConstraintAction.EXECUTE),
        ("测试执行全部取消", TestConstraintAction.EXECUTE),
        ("全程禁止再次复测", TestConstraintAction.EXECUTE),
        ("pytest 不准启动", TestConstraintAction.EXECUTE),
        ("禁止编写测试用例", TestConstraintAction.CREATE),
        ("测试代码一个字也别改", TestConstraintAction.CREATE),
        ("不允许补充任何用例", TestConstraintAction.CREATE),
        ("测试文件保持原样", TestConstraintAction.CREATE),
        ("本轮不要写测试代码", TestConstraintAction.CREATE),
        ("测试用例不许再增加", TestConstraintAction.CREATE),
    ],
)
def test_chinese_denial_families_map_to_the_controlled_action(
    text: str,
    action: TestConstraintAction,
) -> None:
    constraints = extract_test_constraints(text)

    assert [(item.action, item.state) for item in constraints] == [
        (action, TestConstraintState.DENIED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "tests may neither be added nor run",
        "avoid both creating and executing tests",
        "refrain from writing tests and from running them",
        "leave tests unwritten and unexecuted",
        "neither write any tests nor run them",
        "create no tests and run none",
        "add zero tests and execute zero tests",
        "测试新增和测试运行都禁止",
        "一条测试也不加，一次测试也不跑",
        "测试代码别动，pytest 也别执行",
        "测试创建与执行一律取消",
    ],
)
def test_coordinated_denial_is_projected_to_both_test_actions(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert {(item.action, item.state) for item in constraints} == {
        (TestConstraintAction.CREATE, TestConstraintState.DENIED),
        (TestConstraintAction.EXECUTE, TestConstraintState.DENIED),
    }


@pytest.mark.parametrize(
    ("text", "action", "subtype"),
    [
        ("regression test creation is forbidden", TestConstraintAction.CREATE, "regression"),
        ("leave the regression suite unrun", TestConstraintAction.EXECUTE, "regression"),
        ("browser test creation is forbidden", TestConstraintAction.CREATE, "browser"),
        ("never execute performance tests", TestConstraintAction.EXECUTE, "performance"),
        ("avoid writing load tests", TestConstraintAction.CREATE, "load"),
        ("security tests must not run", TestConstraintAction.EXECUTE, "security"),
        ("浏览器用例不得新增", TestConstraintAction.CREATE, "browser"),
        ("安全测试保持不执行", TestConstraintAction.EXECUTE, "security"),
    ],
)
def test_test_subtypes_are_canonical_and_remain_scoped(
    text: str,
    action: TestConstraintAction,
    subtype: str,
) -> None:
    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].action is action
    assert constraints[0].scope is TestConstraintScope.SUBTYPE
    assert constraints[0].subtypes == (subtype,)


def test_each_coordinated_action_binds_to_its_own_test_subtype() -> None:
    constraints = extract_test_constraints(
        "do not write regression tests or run integration tests"
    )

    assert [(item.action, item.subtypes) for item in constraints] == [
        (TestConstraintAction.CREATE, ("regression",)),
        (TestConstraintAction.EXECUTE, ("integration",)),
    ]


def test_platform_qualified_test_denial_is_not_global() -> None:
    constraints = extract_test_constraints(
        "do not run tests on Windows; run the Linux suite"
    )

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.EXECUTE
    assert constraints[0].scope is TestConstraintScope.SUBTYPE
    assert constraints[0].subtypes == ("windows",)


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("do not omit tests; execute the suite", TestConstraintAction.EXECUTE),
        ("never skip the tests; run pytest", TestConstraintAction.EXECUTE),
        ("testing is not prohibited; run it now", TestConstraintAction.EXECUTE),
        (
            "it is false that tests are off limits; execute them",
            TestConstraintAction.EXECUTE,
        ),
        ("tests are allowed to run", TestConstraintAction.EXECUTE),
        ("tests are not forbidden", TestConstraintAction.EXECUTE),
        ("不要不跑测试，全部执行", TestConstraintAction.EXECUTE),
        ("不能跳过测试，跑一遍 pytest", TestConstraintAction.EXECUTE),
        ("测试并非禁止执行，现在运行", TestConstraintAction.EXECUTE),
        ("别不写测试，补一个回归用例", TestConstraintAction.CREATE),
        ("并非禁止测试，只是暂缓", TestConstraintAction.EXECUTE),
    ],
)
def test_denial_of_a_test_ban_releases_but_does_not_authorize(
    text: str,
    action: TestConstraintAction,
) -> None:
    constraints = extract_test_constraints(text)

    assert [(item.action, item.state) for item in constraints] == [
        (action, TestConstraintState.RELEASED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "without hesitation run pytest",
        "without delay execute the tests",
        "without fail run the unit tests",
    ],
)
def test_without_manner_adjunct_is_not_a_test_denial(text: str) -> None:
    assert extract_test_constraints(text) == ()


@pytest.mark.parametrize(
    ("text", "actions"),
    [
        ('explain why the string "no test runs" appears', (TestConstraintAction.EXECUTE,)),
        ('review the wording "test creation is forbidden"', (TestConstraintAction.CREATE,)),
        ('the label `skip every test` needs clearer wording', (TestConstraintAction.EXECUTE,)),
        (
            'what does "neither create nor execute tests" mean?',
            (TestConstraintAction.CREATE, TestConstraintAction.EXECUTE),
        ),
        ('CI report says "tests are forbidden"', (TestConstraintAction.EXECUTE,)),
    ],
)
def test_quoted_constraint_language_is_auditable_meta_not_a_directive(
    text: str,
    actions: tuple[TestConstraintAction, ...],
) -> None:
    constraints = extract_test_constraints(text)

    assert tuple(item.action for item in constraints) == actions
    assert all(item.discourse is ConstraintDiscourse.META for item in constraints)


@pytest.mark.parametrize(
    "text",
    [
        "tests were not run yesterday",
        "the tests didn't execute during the last run",
        "测试此前未曾执行",
    ],
)
def test_historical_non_execution_is_status_evidence(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.EXECUTE
    assert constraints[0].state is TestConstraintState.DENIED
    assert constraints[0].discourse is ConstraintDiscourse.STATUS


def test_report_question_is_meta_even_without_quotes() -> None:
    constraints = extract_test_constraints(
        "report how many tests did not execute, without changing policy"
    )

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.EXECUTE
    assert constraints[0].discourse is ConstraintDiscourse.META


def test_report_provenance_does_not_hide_a_following_directive() -> None:
    constraints = extract_test_constraints(
        "Per the bug report, do not run tests"
    )

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.EXECUTE
    assert constraints[0].state is TestConstraintState.DENIED
    assert constraints[0].discourse is ConstraintDiscourse.DIRECTIVE


@pytest.mark.parametrize(
    "text",
    [
        "test execution is slow; profile the runner",
        "omit the test-results section from README",
        "omit every test failure from the report",
        "leave test results unchanged",
        "do not write test documentation; execute tests",
        "generate a test execution report",
        "generate a report about test executions",
    ],
)
def test_test_artifacts_and_neutral_status_do_not_become_constraints(
    text: str,
) -> None:
    assert extract_test_constraints(text) == ()


def test_every_result_span_round_trips_to_the_source_text() -> None:
    text = (
        'prefix; do not run browser tests; explain "test creation is forbidden"'
    )

    constraints = extract_test_constraints(text)

    assert constraints
    assert all(
        item.text == text[item.span[0] : item.span[1]]
        for item in constraints
    )


@pytest.mark.parametrize(
    "text",
    [
        "tests are no longer forbidden",
        "test creation is not disallowed",
        "tests aren't prohibited from running",
    ],
)
def test_explicitly_lifted_bans_are_released(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].state is TestConstraintState.RELEASED


@pytest.mark.parametrize(
    "text",
    [
        "tests are not allowed to run",
        "tests aren't permitted to execute",
        "the test suite mustn't run",
    ],
)
def test_negative_permission_and_modal_contractions_remain_denials(
    text: str,
) -> None:
    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.EXECUTE
    assert constraints[0].state is TestConstraintState.DENIED


@pytest.mark.parametrize(
    "text",
    [
        "the tests are currently unexecuted",
        "pytest didn't run",
        "这些测试尚未执行",
    ],
)
def test_observed_non_execution_is_status_not_a_directive(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].discourse is ConstraintDiscourse.STATUS


@pytest.mark.parametrize(
    "text",
    [
        "run all tests",
        "tests may run",
        "please create regression tests",
        "test execution is slow",
    ],
)
def test_positive_or_neutral_test_language_creates_no_constraint(text: str) -> None:
    assert extract_test_constraints(text) == ()


def test_multiple_named_subtypes_share_one_scoped_constraint() -> None:
    constraints = extract_test_constraints(
        "do not run unit and integration tests"
    )

    assert len(constraints) == 1
    assert constraints[0].subtypes == ("unit", "integration")


def test_positive_action_before_later_denial_is_not_back_propagated() -> None:
    constraints = extract_test_constraints(
        "run unit tests and do not create regression tests"
    )

    assert [(item.action, item.subtypes) for item in constraints] == [
        (TestConstraintAction.CREATE, ("regression",))
    ]


def test_attributed_statement_is_meta_not_a_user_directive() -> None:
    constraints = extract_test_constraints(
        "the author says test execution is forbidden"
    )

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.EXECUTE
    assert constraints[0].discourse is ConstraintDiscourse.META


@pytest.mark.parametrize(
    "text",
    [
        "create no tests and run all tests",
        "add zero tests and execute all tests",
    ],
)
def test_create_quantity_does_not_deny_a_later_positive_execution(
    text: str,
) -> None:
    constraints = extract_test_constraints(text)

    assert [(item.action, item.state) for item in constraints] == [
        (TestConstraintAction.CREATE, TestConstraintState.DENIED)
    ]


def test_denial_of_an_unrelated_change_does_not_leak_to_tests() -> None:
    assert extract_test_constraints(
        "do not change policy while running tests"
    ) == ()


@pytest.mark.parametrize(
    "text",
    [
        "leave tests alone",
        "keep tests unchanged",
        "test files must remain unmodified",
    ],
)
def test_unchanged_test_directives_deny_test_creation(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].action is TestConstraintAction.CREATE
    assert constraints[0].state is TestConstraintState.DENIED
    assert constraints[0].discourse is ConstraintDiscourse.DIRECTIVE


def test_in_platform_qualifier_is_preserved_as_scope() -> None:
    constraints = extract_test_constraints("do not run tests in Windows")

    assert len(constraints) == 1
    assert constraints[0].subtypes == ("windows",)


@pytest.mark.parametrize(
    ("text", "actions"),
    [
        ("Skip executing the suite and patch the bug", (TestConstraintAction.EXECUTE,)),
        ("Keep pytest idle and patch the bug", (TestConstraintAction.EXECUTE,)),
        (
            "Leave regression cases uncreated and the suite unrun",
            (TestConstraintAction.CREATE, TestConstraintAction.EXECUTE),
        ),
        (
            "测试不用补、也不要跑。",
            (TestConstraintAction.CREATE, TestConstraintAction.EXECUTE),
        ),
        (
            "别生成 case，别跑 suite。",
            (TestConstraintAction.CREATE, TestConstraintAction.EXECUTE),
        ),
    ],
)
def test_elliptical_test_vocabulary_still_yields_typed_denials(
    text: str,
    actions: tuple[TestConstraintAction, ...],
) -> None:
    constraints = extract_test_constraints(text)

    assert tuple(item.action for item in constraints) == actions
    assert all(item.state is TestConstraintState.DENIED for item in constraints)
    assert all(item.discourse is ConstraintDiscourse.DIRECTIVE for item in constraints)


@pytest.mark.parametrize(
    "text",
    [
        "the CI job could not run pytest after checkout",
        "This morning's test job never got running",
        "The release report says pytest was never started.",
    ],
)
def test_non_execution_reports_are_not_current_directives(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert constraints
    assert all(
        item.discourse in {ConstraintDiscourse.META, ConstraintDiscourse.STATUS}
        for item in constraints
    )


def test_relative_historical_failure_does_not_negate_outer_command() -> None:
    assert extract_test_constraints(
        "Start the tests that the pipeline did not launch"
    ) == ()


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("修登录 bug 可以 复测不用做", TestConstraintAction.EXECUTE),
        ("修复发票异常但复测这一步不用再做", TestConstraintAction.EXECUTE),
        ("把搜索异常修好可是复测就不要再做了", TestConstraintAction.EXECUTE),
        (
            "Fix the duplicate charge bug while avoiding modifications "
            "to the test files",
            TestConstraintAction.CREATE,
        ),
        ("fix auth without tests", TestConstraintAction.EXECUTE),
    ],
)
def test_postposed_denial_yields_a_typed_global_directive(
    text: str,
    action: TestConstraintAction,
) -> None:
    constraints = extract_test_constraints(text)

    assert any(
        item.action is action
        and item.state is TestConstraintState.DENIED
        and item.scope is TestConstraintScope.GLOBAL
        and item.discourse is ConstraintDiscourse.DIRECTIVE
        for item in constraints
    )


@pytest.mark.parametrize(
    "text",
    [
        "测试不用补、也不要跑。",
        "别生成 case，别跑 suite。",
    ],
)
def test_postposed_denial_grammar_does_not_absorb_prefix_verb_forms(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert constraints
    assert all(
        item.evidence_family == "negated-action" for item in constraints
    )
    assert all(item.state is TestConstraintState.DENIED for item in constraints)


@pytest.mark.parametrize(
    "text",
    [
        "别再拒绝跑 regression tests 了。",
        "We cannot leave pytest unrun this time.",
        "不是让你别跑 tests；现在执行。",
        "It isn't true that you should not launch the suite.",
    ],
)
def test_outer_negation_releases_an_inner_test_denial(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert constraints
    assert all(item.state is TestConstraintState.RELEASED for item in constraints)


@pytest.mark.parametrize(
    ("text", "actions"),
    [
        ("no new tests", (TestConstraintAction.CREATE,)),
        ("no test edits", (TestConstraintAction.CREATE,)),
        ("不要新建任何测试", (TestConstraintAction.CREATE,)),
        ("一条测试也别添", (TestConstraintAction.CREATE,)),
        ("不需要测试用例", (TestConstraintAction.CREATE,)),
        ("复测不需要做", (TestConstraintAction.EXECUTE,)),
        (
            "单测不补并且全程不跑",
            (TestConstraintAction.CREATE, TestConstraintAction.EXECUTE),
        ),
    ],
)
def test_compact_nominal_denials_are_shared_router_constraints(
    text: str,
    actions: tuple[TestConstraintAction, ...],
) -> None:
    constraints = extract_test_constraints(text)

    assert tuple(item.action for item in constraints) == actions
    assert all(item.state is TestConstraintState.DENIED for item in constraints)
    assert all(item.scope is TestConstraintScope.GLOBAL for item in constraints)


def test_slow_test_denial_is_scoped_not_global() -> None:
    constraints = extract_test_constraints("只跳过慢速测试")

    assert len(constraints) == 1
    assert constraints[0].subtypes == ("slow",)
    assert constraints[0].scope is TestConstraintScope.SUBTYPE


@pytest.mark.parametrize(
    "text",
    [
        "重构 agent_loop.py，保持行为不变并运行测试",
        "代码别改测试别跑只查pytest为什么失败",
        "测试不要跑代码也别改只读一下traceback",
    ],
)
def test_non_test_mutation_does_not_become_test_creation_denial(text: str) -> None:
    constraints = extract_test_constraints(text)

    assert all(item.action is not TestConstraintAction.CREATE for item in constraints)


def test_negative_permission_remains_a_denial_in_chinese() -> None:
    constraints = extract_test_constraints("测试不允许跑代码不允许改只看日志")

    assert any(
        item.action is TestConstraintAction.EXECUTE
        and item.state is TestConstraintState.DENIED
        for item in constraints
    )


def test_constraint_span_is_anchored_to_the_denial_not_the_whole_clause() -> None:
    text = "先跑测试不对只看日志别执行"

    constraints = extract_test_constraints(text)

    assert len(constraints) == 1
    assert constraints[0].text == "别执行"
    assert constraints[0].span == (10, 13)
