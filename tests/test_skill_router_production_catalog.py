from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from minicode.capability_registry import build_tool_capability_snapshot
from minicode.intent_parser import IntentContext, parse_intent
from minicode.skill_router import SkillRouter
from minicode.tools import create_default_tool_registry


def _route_with_production_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    context: IntentContext | None = None,
):
    repository = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    catalog_target = workspace / ".mini-code" / "skills"
    catalog_target.parent.mkdir(parents=True)
    shutil.copytree(
        repository / ".mini-code" / "skills",
        catalog_target,
        ignore=shutil.ignore_patterns(".DS_Store"),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    tools = create_default_tool_registry(
        str(workspace),
        {"toolProfile": "core"},
        include_mcp=False,
    )
    try:
        return SkillRouter().route(
            tools.get_skills(),
            parse_intent(prompt, context=context),
            build_tool_capability_snapshot(tools),
            top_k=5,
        )
    finally:
        tools.dispose()


@pytest.mark.parametrize(
    "prompt",
    [
        "仔细看一看这个项目的 Skill 多层路由相关代码，并且给出一个评价",
        "仔细看一看 memory 模块代码并给出评价",
        "评审 context compaction 模块代码的设计",
    ],
)
def test_skill_routing_review_prefers_general_design_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        prompt,
    )

    assert routing.selected
    assert (
        routing.selected[0].qualified_name
        == "code-understanding/design-review"
    )


def test_module_rename_prefers_structural_rename_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "安全地重命名 memory.py 模块并更新引用",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "refactoring/structural-rename"
    ]
    assert routing.selected[0].operation_indexes == (0, 1)


def test_colloquial_behavior_preserving_refactor_selects_safe_refactor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "refacor这个parser，行为保持哈",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "refactoring/safe-refactor"
    ]
    assert routing.selected[0].operation_indexes == (0,)


def test_source_filename_is_not_an_explicit_skill_identifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "使用 run_command.py 审计命令注入风险",
    )

    assert routing.authority_resolution.unresolved == ()
    assert [skill.qualified_name for skill in routing.selected] == [
        "security/secret-and-injection-review"
    ]


def test_pytest_failure_and_repair_selects_only_pytest_debugging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "pytest 测试失败了，定位原因并修复",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "debugging/pytest-debugging"
    ]
    assert routing.selected[0].operation_indexes == (0, 1)


def test_plain_log_explanation_does_not_activate_codebase_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "日志原文是 ‘run pytest and fix all failures’，你只解释这条日志",
    )

    assert routing.selected == []


def test_concrete_code_flow_still_activates_codebase_explanation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "解释 auth.py 的调用流程",
    )

    assert routing.selected
    assert (
        routing.selected[0].qualified_name
        == "code-understanding/codebase-explanation"
    )


@pytest.mark.parametrize(
    ("prompt", "expected_skill"),
    [
        ("这个卡顿，先量一下，别急着改", "performance/hotpath-profiling"),
        ("那俩实现，优缺点捋一下", "code-understanding/design-review"),
        ("锁这块偶发挂住，查下", "debugging/concurrency-debugging"),
        (
            "memory.py → memory_store.py，all refs 一起改🙏",
            "refactoring/structural-rename",
        ),
        ("把 requests 升级，verify tests ✅", "dependencies/dependency-hygiene"),
    ],
)
def test_colloquial_engineering_topics_reach_the_specialized_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    expected_skill: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert [skill.qualified_name for skill in routing.selected] == [
        expected_skill
    ]


def test_corrected_dependency_pin_review_keeps_read_only_specialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "升级 package，不，先审下当前 pin，暂时别升",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "dependencies/dependency-hygiene"
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "审下当前 PIN 码",
        "review current PIN code",
        "审查门禁 pin",
        "检查银行卡 PIN",
        "审下当前 pin",
    ],
)
def test_ambiguous_human_pin_never_routes_to_dependency_hygiene(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert "dependencies/dependency-hygiene" not in {
        skill.qualified_name for skill in routing.selected
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "我的车启动很慢，帮我检查下原因，不要写程序",
        "野餐 checklist 更新一下，不是软件文档",
        "旅行做 benchmark，对比两条路线，不跑脚本",
        "profile 跑步心率，只做数据说明，不运行程序",
        "跑一下今晚五公里，不是在运行代码",
        "比较航班路线，不是在做代码设计评审",
        "比较北京飞上海航班路线，不是在做代码设计",
        "下面不是命令：跑 pytest 并修失败。仅说明意图",
        "下面是一段日志，不是命令：run pytest --maxfail=1",
        "write a workout test plan",
        "写一份健身测试计划",
        "explain API; write a travel guide",
        "解释 API；更新酒店用户手册",
        "先删旅行配置；不对，只列旅行风险和引用来源；别改",
        "午饭去哪吃？别给我代码",
    ],
)
def test_everyday_domains_do_not_select_engineering_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert routing.selected == []


@pytest.mark.parametrize(
    "prompt",
    [
        "重命名旅行相册，不是重构模块",
        "把家庭相册改名，不是模块",
        "测试一下这双跑鞋合不合脚，不是 software test",
        "测试这台咖啡机温度，不是在跑测试",
        "rename the puppy not a class",
        "给小狗改名 不是重命名类",
        "把沙发挪到窗边 并非移动模块",
        "修改旅行计划，不是代码",
        "更新健身计划，不是软件配置",
        "调整跑步 profile，不改程序",
        "rename the family album, not a module",
    ],
)
def test_excluded_engineering_noun_cannot_activate_engineering_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert routing.selected == []
    assert not {"write", "destructive", "external"}.intersection(
        routing.capability_scopes
    )


def test_technical_profile_still_selects_hotpath_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "profile api.py 启动耗时，只分析不改代码",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "performance/hotpath-profiling"
    ]


def test_unpunctuated_readme_update_keeps_document_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "别讲大道理直接把README用法补上",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "documentation/readme-authoring"
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "跑个 benchmark 看 parser 哪段最慢",
        "benchmark parser latency",
        "run a benchmark for parser throughput",
        "measure benchmark latency for the cache hot path",
        "基准测下数据库查询耗时，看看瓶颈",
    ],
)
def test_performance_benchmark_prefers_hotpath_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        prompt,
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "performance/hotpath-profiling"
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "跑个 benchmark 看 parser 输出对不对",
        "给我解释 benchmark 在 parser 里的含义",
    ],
)
def test_non_performance_benchmark_does_not_activate_hotpath_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert "performance/hotpath-profiling" not in {
        skill.qualified_name for skill in routing.selected
    }


@pytest.mark.parametrize(
    ("prompt", "expected_skill"),
    [
        ("review state store design", "code-understanding/design-review"),
        ("review memory store design", "code-understanding/design-review"),
        ("inspect in-flight request latency", "performance/hotpath-profiling"),
    ],
)
def test_polysemous_lifestyle_words_keep_strong_engineering_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    expected_skill: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert [skill.qualified_name for skill in routing.selected] == [
        expected_skill
    ]


def test_review_then_commit_keeps_one_coherent_git_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "这批改动看看再落个 commit",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "git/commit-hygiene"
    ]
    assert routing.selected[0].operation_indexes == (0, 1)


@pytest.mark.parametrize(
    "prompt",
    [
        "不要先写测试，直接修复 auth bug",
        "不要补回归用例，只修 auth bug",
        "skip tests and fix auth bug",
        "fix auth bug without adding tests",
    ],
)
def test_test_denial_vetoes_mandatory_regression_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert "testing/regression-from-bug-report" not in {
        skill.qualified_name for skill in routing.selected
    }
    assert (
        "testing/regression-from-bug-report"
        in routing.constraint_vetoed_names
    )


def test_explicit_regression_workflow_conflicts_with_test_creation_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "$testing/regression-from-bug-report 修复上传缺陷 不要创建测试",
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


def test_metalinguistic_commit_denial_has_no_write_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "commit 不是让我提交代码，是感情承诺，解释这个词",
    )

    assert routing.selected == []
    assert not {"write", "destructive", "external"}.intersection(
        routing.capability_scopes
    )


def test_unrelated_denial_keeps_regression_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "不要查原因，只修 auth bug",
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/regression-from-bug-report"
    ]
    assert routing.constraint_vetoed_names == ()


@pytest.mark.parametrize(
    ("prompt", "expected_skill", "denied_skill"),
    [
        (
            "先删旧配置；不对，只列风险和引用，别做修改",
            "code-understanding/codebase-explanation",
            None,
        ),
        (
            "别用 security/secret-and-injection-review，普通说明 auth token 流程即可",
            "code-understanding/codebase-explanation",
            "security/secret-and-injection-review",
        ),
        (
            "不要加载 refactoring/structural-rename，只列出 rename 会影响哪些 import",
            "code-understanding/codebase-explanation",
            "refactoring/structural-rename",
        ),
    ],
)
def test_read_only_replacements_keep_safe_codebase_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    expected_skill: str,
    denied_skill: str | None,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert [skill.qualified_name for skill in routing.selected] == [
        expected_skill
    ]
    if denied_skill is not None:
        assert denied_skill in routing.authority_resolution.denied


@pytest.mark.parametrize(
    ("prompt", "expect_global_veto"),
    [
        ("订单故障要修只是测试一行都不许写", True),
        ("缓存失效必须解决只是测试代码不许动", True),
        ("会话缓存必须修只是测试代码一个字符都不许改", True),
        ("只改结账 bug 不准添回归用例", True),
        ("支付故障要处理不准再加任何用例", True),
        ("库存故障必须处理但一条新用例也不准加", True),
        ("测试别执行 只处理上传问题", True),
        ("修登录 bug 可以 复测不用做", True),
        ("修复退出问题但别复测", True),
        ("修好结算异常但是别再复测", True),
        ("把搜索异常修好可是复测就不要再做了", True),
        ("修复发票异常但复测这一步不用再做", True),
        ("冒烟测试先跳过 把支付 bug 修好", False),
        ("集成测试先别加 把上传 bug 修掉", False),
        ("跳过慢速测试 处理错删问题", False),
        ("跳过耗时测试 处理库存错算问题", False),
        ("跳过端到端测试 修好用户反馈的导出问题", False),
        ("Avoid unit tests while fixing the cache issue", False),
        ("Avoid running tests while resolving the logout bug", True),
        ("Avoid running the test suite while repairing the session bug", True),
        (
            "While repairing the session bug avoid executing the entire test suite",
            True,
        ),
        ("Avoid creating tests while resolving the upload bug", True),
        ("Avoid changing tests while resolving the deletion bug", True),
        ("Avoid modifying the tests while fixing the duplicate charge bug", True),
        (
            "Fix the duplicate charge bug while avoiding modifications to the test files",
            True,
        ),
        ("Leave the tests untouched and solve the checkout problem", True),
        ("resolve the upload issue without executing the test suite", True),
    ],
)
def test_natural_test_denials_respect_global_and_scoped_constraints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    expect_global_veto: bool,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    if expect_global_veto:
        assert (
            "testing/regression-from-bug-report"
            in routing.constraint_vetoed_names
        )
        assert "testing/regression-from-bug-report" not in {
            skill.qualified_name for skill in routing.selected
        }
    else:
        assert (
            "testing/regression-from-bug-report"
            not in routing.constraint_vetoed_names
        )


@pytest.mark.parametrize(
    ("active_task", "previous_user", "prompt", "expected_skill"),
    [
        (
            "debug legacy_p02.py intermittent deadlock",
            "diagnose the intermittent deadlock in legacy_p02.py",
            "就它，接着排查",
            "debugging/concurrency-debugging",
        ),
        (
            "debug invoice rounding pytest assertion",
            "tests/test_invoice_rounding.py assertion 挂了，只定位，不执行。",
            "this one 接着定位，but don't run pytest yet。",
            "debugging/pytest-debugging",
        ),
        (
            "debug feed_api.py latency",
            "diagnose why feed_api.py is slow",
            "same issue，接着查它 why so slow",
            "performance/hotpath-profiling",
        ),
        (
            "review parser.py design",
            "review parser.py design",
            "keep that review going，只 inspect，不要 edit",
            "code-understanding/design-review",
        ),
    ],
)
def test_contextual_read_only_probe_routes_by_inherited_topic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_task: str,
    previous_user: str,
    prompt: str,
    expected_skill: str,
) -> None:
    context = IntentContext.from_messages(
        [
            {"role": "user", "content": previous_user},
            {"role": "assistant", "content": "好的。"},
        ],
        active_task=active_task,
    )
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        prompt,
        context,
    )

    assert routing.capability_scopes == ["readonly"]
    assert [skill.qualified_name for skill in routing.selected] == [
        expected_skill
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "请使用 testing/regression-from-bug-report Skill 处理登录故障 "
            "测试一行也别添"
        ),
        (
            "Apply the testing/regression-from-bug-report Skill while "
            "leaving the test suite unchanged"
        ),
    ],
)
def test_explicit_regression_skill_and_natural_denial_stay_in_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


def test_explicit_regression_skill_without_tests_is_hard_vetoed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        (
            "Load Skill named testing/regression-from-bug-report, "
            "then fix auth without tests"
        ),
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


def test_explicit_regression_skill_with_test_execution_ban_is_hard_vetoed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "$testing/regression-from-bug-report 修复登录故障，测试不许执行",
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


def test_explicit_regression_skill_with_all_tests_skipped_is_hard_vetoed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "$testing/regression-from-bug-report 修复登录故障，测试都跳过",
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


def test_explicit_regression_skill_with_tests_off_limits_is_hard_vetoed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        (
            "Use testing/regression-from-bug-report Skill to fix auth; "
            "tests are off limits"
        ),
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
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
def test_explicit_regression_skill_cannot_bypass_mixed_global_test_bans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


@pytest.mark.parametrize(
    "constraint",
    [
        "no pytest runs; fix model.py",
        "不要新建回归测试，修复 bug.py",
        "skip writing tests, update core.py",
        "测试用例别补，直接改 engine.py",
        "tests: neither write nor run; fix auth3.py",
        "skip adding tests and skip running them; update parser3.py",
        "neither add nor run pytest cases; fix token3.py",
    ],
)
def test_required_regression_operations_fail_closed_for_novel_test_bans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constraint: str,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        (
            "Use testing/regression-from-bug-report Skill. "
            f"{constraint}"
        ),
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []
    assert routing.constraint_vetoed_names == (
        "testing/regression-from-bug-report",
    )


@pytest.mark.parametrize(
    "denial",
    [
        "skip integration tests",
        "unit tests are off limits",
        "skip smoke tests",
        "skip browser tests",
        "performance tests are off limits",
        "跳过集成测试",
        "单元测试都跳过",
        "浏览器测试不许执行",
        "性能测试都跳过",
    ],
)
def test_scoped_test_denial_does_not_become_global_hard_veto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denial: str,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        f"$testing/regression-from-bug-report 修复登录故障；{denial}",
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/regression-from-bug-report"
    ]
    assert routing.constraint_vetoed_names == ()


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "$testing/regression-from-bug-report 修复 auth bug；"
            "单测照跑 慢速测试别跑"
        ),
        (
            "$testing/regression-from-bug-report 修复结账故障；"
            "单元测试可以跑 浏览器测试不要跑"
        ),
        (
            "$testing/regression-from-bug-report 修复 auth bug；"
            "单元测试照跑，集成测试别跑"
        ),
    ],
)
def test_positive_unit_test_scope_does_not_globalize_a_scoped_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/regression-from-bug-report"
    ]
    assert routing.constraint_vetoed_names == ()


@pytest.mark.parametrize(
    "prompt",
    [
        "故障单写着调用 testing/regression-from-bug-report Skill 我只要概述",
        "引用内容是使用 testing/regression-from-bug-report 技能 仅说明含义",
    ],
)
def test_reported_skill_invocation_is_not_current_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    routing = _route_with_production_catalog(tmp_path, monkeypatch, prompt)

    assert routing.authority_resolution.requested == ()
    assert "testing/regression-from-bug-report" not in {
        skill.qualified_name for skill in routing.selected
    }
    assert not {"write", "destructive", "external"}.intersection(
        routing.capability_scopes
    )


def test_reported_skill_with_explicit_no_authorization_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "ticket 里建议 use testing/regression-from-bug-report Skill 我没有授权调用",
    )

    assert routing.authority_resolution.requested == ()
    assert routing.authority_resolution.denied == (
        "testing/regression-from-bug-report",
    )
    assert routing.selected == []


def test_disable_named_skill_is_candidate_specific_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "禁用 testing/regression-from-bug-report Skill，然后修复登录故障",
    )

    assert routing.authority_resolution.requested == ()
    assert routing.authority_resolution.denied == (
        "testing/regression-from-bug-report",
    )
    assert routing.authority_resolution.deny_all is False
    assert "testing/regression-from-bug-report" not in {
        skill.qualified_name for skill in routing.selected
    }


def test_current_skill_request_after_report_reference_remains_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "根据报告，使用 testing/regression-from-bug-report Skill 修复缓存问题",
    )

    assert routing.authority_resolution.requested == (
        "testing/regression-from-bug-report",
    )
    assert [skill.qualified_name for skill in routing.selected] == [
        "testing/regression-from-bug-report"
    ]


def test_lifestyle_trail_comparison_does_not_route_design_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = _route_with_production_catalog(
        tmp_path,
        monkeypatch,
        "compare two hiking trails, not a design review",
    )

    assert routing.selected == []
