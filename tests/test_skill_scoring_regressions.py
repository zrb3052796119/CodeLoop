from __future__ import annotations

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import IntentType, parse_intent
from minicode.skill_router import SkillRouter


def _review_skills(count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "name": f"review-{index}",
            "qualified_name": f"review/review-{index}",
            "description": "Review a code subsystem",
            "source": "project",
        }
        for index in range(count)
    ]


def test_one_alias_concept_counts_as_one_specific_signal() -> None:
    intent = parse_intent("审查代码")

    result = SkillRouter().route(
        _review_skills(),
        intent,
        CapabilityRegistry(),
    )

    assert len(result.selected) == 1
    reasons = result.selected[0].reasons
    assert len([reason for reason in reasons if reason.startswith("keyword:")]) == 1


@pytest.mark.parametrize(
    "prompt",
    [
        "Tell me a joke about database query syntax",
        "Write a poem about database query optimization",
        "写一首关于数据库查询的诗",
        "讲一个数据库查询的笑话",
    ],
)
def test_non_task_creative_requests_with_multiple_domain_terms_abstain(
    prompt: str,
) -> None:
    skill = {
        "name": "database-tuning",
        "qualified_name": "database/database-tuning",
        "description": "Optimize database query performance",
        "keywords": ["database", "query", "optimization"],
        "source": "project",
    }

    result = SkillRouter().route(
        [skill],
        parse_intent(prompt),
        CapabilityRegistry(),
    )

    assert result.used_fallback is True
    assert result.selected == []


def test_unknown_descriptive_sentence_with_domain_terms_abstains() -> None:
    skill = {
        "name": "frontend-design",
        "qualified_name": "frontend/frontend-design",
        "description": "Design production frontend pages and layouts",
        "keywords": ["frontend", "layout"],
        "source": "project",
    }

    result = SkillRouter().route(
        [skill],
        parse_intent(
            "The frontend of the museum faces east and has a symmetrical layout"
        ),
        CapabilityRegistry(),
    )

    assert result.used_fallback is True
    assert result.selected == []


def test_topic_match_beats_unrelated_purpose_only_compound_candidates() -> None:
    skills = [
        {
            "name": "database-tuning",
            "qualified_name": "database/database-tuning",
            "description": (
                "Optimize database queries, SQL indexes, transactions, and "
                "slow queries"
            ),
            "keywords": ["database", "sql", "index", "slow query"],
            "source": "project",
        },
        {
            "name": "memory-audit",
            "qualified_name": "memory/memory-audit",
            "description": "Audit persistent agent memory and retrieval",
            "source": "project",
        },
        {
            "name": "security-audit",
            "qualified_name": "security/security-audit",
            "description": "Audit application security and permissions",
            "source": "project",
        },
    ]

    result = SkillRouter().route(
        skills,
        parse_intent("检查数据库事务慢查询并优化 SQL 索引"),
        CapabilityRegistry(),
    )

    assert result.selected
    assert result.selected[0].qualified_name == "database/database-tuning"
    assert all(
        skill.qualified_name
        not in {"memory/memory-audit", "security/security-audit"}
        for skill in result.selected
    )


def test_chinese_code_evaluation_request_is_classified_as_review() -> None:
    intent = parse_intent(
        "仔细看一看这个项目的 Skill 多层路由相关代码，并且给出一个评价"
    )

    assert intent.intent_type is IntentType.REVIEW
