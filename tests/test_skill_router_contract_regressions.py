"""Behavioral regressions for SkillRouter authority and operation contracts."""

from __future__ import annotations

import pytest

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import (
    ActionType,
    IntentOperation,
    IntentPolarity,
    IntentType,
    ParsedIntent,
    parse_intent,
)
from minicode.skill_router import SkillRouter


def _skill(
    name: str,
    description: str,
    *,
    keywords: list[str],
    supported_operations: list[str],
    required_operations: list[str] | None = None,
    purposes: list[str] | None = None,
    tools: list[str] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "qualified_name": name,
        "description": description,
        "path": f"/tmp/{name}/SKILL.md",
        "source": "project",
        "keywords": keywords,
        "supported_operations": supported_operations,
        "required_operations": list(required_operations or []),
        "purposes": list(purposes or []),
        "tools": list(tools or []),
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "不要写测试，只修 auth bug",
        "skip tests and fix the auth bug",
        "fix the auth bug without adding tests",
        "No new tests fix the checkout bug now",
        "这个错删 bug 要修 但测试一行也不许写",
        "repair reported logout issue without executing tests",
        "别给这个登录缺陷补测试 直接修好",
    ],
)
def test_required_operation_denial_hard_vetoes_workflow(prompt: str) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "regression-workflow",
                "Reproduce a bug with a regression test, then fix it",
                keywords=["bug", "regression", "auth"],
                supported_operations=["debug.update", "test.create", "test.execute"],
                required_operations=["test.create", "test.execute"],
            )
        ],
        parse_intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.selected == []
    assert routing.constraint_vetoed_names == ("regression-workflow",)


def test_malformed_required_operation_contract_fails_closed() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "malformed-workflow",
                "Fix an auth bug",
                keywords=["bug", "auth"],
                supported_operations=["debug.update"],
                required_operations=["test_everything"],
            )
        ],
        parse_intent("fix the auth bug"),
        CapabilityRegistry(),
    )

    assert routing.selected == []
    assert routing.constraint_vetoed_names == ("malformed-workflow",)


def test_scoped_test_denial_does_not_veto_generic_regression_workflow() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "regression-workflow",
                "Reproduce a bug with a regression test, then fix it",
                keywords=["bug", "regression", "auth"],
                supported_operations=["debug.update", "test.create", "test.execute"],
                required_operations=["test.create", "test.execute"],
            )
        ],
        parse_intent("do not run integration tests; fix the auth bug"),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in routing.selected] == [
        "regression-workflow"
    ]
    assert routing.constraint_vetoed_names == ()


def test_scoped_test_skip_preserves_unrelated_test_execution() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "regression-workflow",
                "Reproduce a bug with a regression test, then fix it",
                keywords=["bug", "regression", "login"],
                supported_operations=["debug.update", "test.create", "test.execute"],
                required_operations=["test.create", "test.execute"],
            )
        ],
        parse_intent(
            "run unit tests but skip integration tests and fix the login bug"
        ),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in routing.selected] == [
        "regression-workflow"
    ]
    assert routing.constraint_vetoed_names == ()


def test_only_a_required_operation_denial_still_reaches_load_policy_veto() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "regression-workflow",
                "Reproduce a bug with a regression test, then fix it",
                keywords=["bug", "regression"],
                supported_operations=["debug.update", "test.create", "test.execute"],
                required_operations=["test.create", "test.execute"],
            )
        ],
        parse_intent("不要创建测试"),
        CapabilityRegistry(),
    )

    assert routing.selected == []
    assert routing.decision_reason == "no-affirmed-operation"
    assert routing.constraint_vetoed_names == ("regression-workflow",)


@pytest.mark.parametrize(
    "prompt",
    [
        "A ticket mentions invoke regression-workflow Skill; explain the ticket",
        "工单提到调用 regression-workflow 技能 这里只解释工单",
    ],
)
def test_reported_skill_invocation_is_not_current_authority(prompt: str) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "regression-workflow",
                "Reproduce a bug with a regression test, then fix it",
                keywords=["bug", "regression"],
                supported_operations=["debug.update", "test.create", "test.execute"],
                required_operations=["test.create", "test.execute"],
            )
        ],
        parse_intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.requested == ()
    assert routing.selected == []


def test_report_context_does_not_hide_a_current_skill_invocation() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "regression-workflow",
                "Reproduce a bug with a regression test, then fix it",
                keywords=["bug", "regression"],
                supported_operations=["debug.update", "test.create", "test.execute"],
                required_operations=["test.create", "test.execute"],
            )
        ],
        parse_intent("根据报告，使用 regression-workflow Skill 修复 auth bug"),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.requested == ("regression-workflow",)
    assert [skill.name for skill in routing.selected] == [
        "regression-workflow"
    ]


def _review_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, domain in (
        ("read_file", CapabilityDomain.FILE),
        ("grep_files", CapabilityDomain.SEARCH),
        ("find_symbols", CapabilityDomain.SEARCH),
        ("find_references", CapabilityDomain.SEARCH),
        ("code_review", CapabilityDomain.ANALYSIS),
    ):
        registry.register(
            CapabilityMetadata(
                name=name,
                domain=domain,
                scope=CapabilityScope.READONLY,
                description=f"{name} capability",
            ),
            lambda **_: None,
        )
    return registry


def _review_skills() -> list[dict[str, object]]:
    return [
        _skill(
            "design-review",
            "Review existing code for design issues and inconsistencies",
            keywords=["design review", "code smell", "inconsistency"],
            purposes=["design review", "code audit"],
            supported_operations=["review.analyze", "review.compare"],
            tools=["read_file", "grep_files", "find_symbols"],
        ),
        _skill(
            "secret-and-injection-review",
            "Audit code for hardcoded credentials and injection risks",
            keywords=[
                "security",
                "secret",
                "credential",
                "injection",
            ],
            purposes=["security audit", "secret and injection review"],
            supported_operations=["review.analyze"],
            tools=[
                "read_file",
                "grep_files",
                "find_references",
                "code_review",
            ],
        ),
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "使用 run_command.py 审计命令注入风险",
        "Use auth.py to audit authentication security risks",
        "Use auth.py:42 to audit authentication security risks",
    ],
)
def test_source_filename_after_use_verb_is_not_skill_authority(
    prompt: str,
) -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "security-review",
                "Audit code for command injection and security risks",
                keywords=["命令注入", "安全", "security", "injection"],
                supported_operations=["review.analyze"],
            )
        ],
        parse_intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.unresolved == ()
    assert [skill.name for skill in routing.selected] == ["security-review"]


def test_file_like_skill_name_still_requires_explicit_skill_label() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "auth.py",
                "Review authentication code",
                keywords=["authentication"],
                supported_operations=["review.analyze"],
            )
        ],
        parse_intent("Use the auth.py Skill to audit authentication"),
        CapabilityRegistry(),
    )

    assert routing.authority_resolution.requested == ("auth.py",)
    assert routing.selected[0].explicitly_requested is True


def test_declared_operation_contract_rejects_only_incompatible_candidate() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "misleading-review",
                "Review and audit router security behavior",
                keywords=["router", "security"],
                supported_operations=["test.execute"],
            )
        ],
        parse_intent("review router security"),
        CapabilityRegistry(),
    )

    assert routing.selected == []


def test_malformed_operation_contract_rejects_only_candidate() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "malformed-review",
                "Review and audit router security behavior",
                keywords=["router", "security"],
                supported_operations=["review_everything"],
            )
        ],
        parse_intent("review router security"),
        CapabilityRegistry(),
    )

    assert routing.selected == []


def test_partially_malformed_operation_contract_fails_closed() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "partially-malformed-review",
                "Review and audit router security behavior",
                keywords=["router", "security"],
                supported_operations=[
                    "review.analyze",
                    "review_everything",
                ],
            )
        ],
        parse_intent("review router security"),
        CapabilityRegistry(),
    )

    assert routing.selected == []


def test_operation_contract_cannot_be_borrowed_across_compound_request() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "parser-updater",
                "Update the parser module implementation",
                keywords=["parser", "module"],
                supported_operations=["code.update"],
            )
        ],
        parse_intent("review the parser module and update it"),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in routing.selected] == ["parser-updater"]
    assert routing.selected[0].operation_indexes == (1,)


def test_explicit_skill_keeps_selection_without_claiming_unsupported_lane() -> None:
    routing = SkillRouter().route(
        [
            {
                **_skill(
                    "pytest-debugging",
                    "Write README documentation for pytest failures",
                    keywords=["pytest", "readme", "documentation"],
                    supported_operations=["test.execute"],
                ),
                "qualified_name": "debugging/pytest-debugging",
            }
        ],
        parse_intent(
            "Use the debugging/pytest-debugging Skill to write README "
            "documentation"
        ),
        CapabilityRegistry(),
    )

    assert [skill.qualified_name for skill in routing.selected] == [
        "debugging/pytest-debugging"
    ]
    assert routing.selected[0].explicitly_requested is True
    assert routing.selected[0].operation_indexes == ()
    assert "explicit-skill-contract-mismatch" in routing.selected[0].reasons


def test_explicit_skill_claims_only_compatible_compound_lane() -> None:
    routing = SkillRouter().route(
        [
            {
                **_skill(
                    "pytest-debugging",
                    "Review parser behavior and run pytest",
                    keywords=["review", "parser", "pytest"],
                    supported_operations=["test.execute"],
                ),
                "qualified_name": "debugging/pytest-debugging",
            }
        ],
        parse_intent(
            "Use the debugging/pytest-debugging Skill to review the parser "
            "and run pytest"
        ),
        CapabilityRegistry(),
    )

    assert routing.selected[0].operation_indexes == (1,)
    assert "explicit-skill-contract-mismatch" in routing.selected[0].reasons


def test_generic_code_review_prefers_general_review_over_security_tool_count() -> None:
    routing = SkillRouter().route(
        _review_skills(),
        parse_intent("仔细看一看 memory 模块代码并给出评价"),
        _review_registry(),
        top_k=2,
    )

    assert [skill.name for skill in routing.selected] == ["design-review"]


def test_specialized_security_review_still_outranks_general_review() -> None:
    routing = SkillRouter().route(
        _review_skills(),
        parse_intent("审计 auth.py 是否有硬编码密钥或命令注入风险"),
        _review_registry(),
        top_k=2,
    )

    assert [skill.name for skill in routing.selected] == [
        "secret-and-injection-review"
    ]


def test_chinese_injection_review_uses_specialized_security_topic() -> None:
    routing = SkillRouter().route(
        _review_skills(),
        parse_intent("使用 run_command.py 审计命令注入风险"),
        _review_registry(),
        top_k=2,
    )

    assert routing.authority_resolution.unresolved == ()
    assert [skill.name for skill in routing.selected] == [
        "secret-and-injection-review"
    ]


def test_general_news_search_does_not_use_codebase_purpose_fallback() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "codebase-explanation",
                "Explain and search code definitions and call paths",
                keywords=["codebase", "definition", "call path"],
                purposes=["codebase explanation", "runtime trace"],
                supported_operations=["explain.read", "search.read"],
            )
        ],
        parse_intent("帮我搜一下今天的热点新闻。"),
        CapabilityRegistry(),
    )

    assert routing.selected == []


def test_creative_docker_poem_does_not_use_documentation_purpose_fallback() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "documentation-writing",
                "Write README files and technical documentation",
                keywords=["readme", "documentation", "guide"],
                purposes=["technical documentation"],
                supported_operations=["document.create", "document.update"],
            )
        ],
        parse_intent("写一首关于 Docker 的短诗。"),
        CapabilityRegistry(),
    )

    assert routing.selected == []


def test_topic_compatible_news_skill_can_route_non_engineering_search() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "news-search",
                "Search current news and headlines",
                keywords=["news", "headlines"],
                purposes=["news search"],
                supported_operations=["search.read"],
            )
        ],
        parse_intent("帮我搜一下今天的热点新闻。"),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in routing.selected] == ["news-search"]


def test_topic_compatible_poetry_skill_can_route_creative_writing() -> None:
    routing = SkillRouter().route(
        [
            _skill(
                "poetry-writing",
                "Write poems and short verse",
                keywords=["poetry", "poem", "verse"],
                purposes=["creative poetry"],
                supported_operations=["document.create"],
            )
        ],
        parse_intent("写一首关于 Docker 的短诗。"),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in routing.selected] == ["poetry-writing"]


@pytest.mark.parametrize("action_type", [ActionType.RENAME, ActionType.MOVE])
def test_structural_file_actions_include_destructive_tool_affinity(
    action_type: ActionType,
) -> None:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityMetadata(
            name="batch_move",
            domain=CapabilityDomain.FILE,
            scope=CapabilityScope.DESTRUCTIVE,
            description="Move files and replace destinations",
        ),
        lambda **_: None,
    )
    operation = IntentOperation(
        intent_type=IntentType.CODE,
        action_type=action_type,
        polarity=IntentPolarity.AFFIRMED,
        confidence=0.95,
        clause=f"{action_type.value} auth.py",
        keywords=(action_type.value, "auth.py"),
        entities={"files": ("auth.py",)},
    )
    intent = ParsedIntent(
        raw_input=operation.clause,
        intent_type=IntentType.CODE,
        action_type=action_type,
        confidence=0.95,
        operations=(operation,),
        primary_operation_index=0,
        semantic_query=operation.clause,
    )

    routing = SkillRouter().route(
        [
            _skill(
                "structural-move",
                "Rename or move source files",
                keywords=["auth.py"],
                supported_operations=[f"code.{action_type.value}"],
                tools=["batch_move"],
            )
        ],
        intent,
        registry,
    )

    assert routing.capability_scopes == ["destructive"]
    assert "tool-scope:destructive" in routing.selected[0].reasons
