"""Unavailable Skills must not suppress another Skill's routing fallback."""

from __future__ import annotations

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter


def _review_skill(
    name: str,
    *,
    source: str,
    description: str = "Review code",
    activation_terms: tuple[str, ...] = (),
    required_operations: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "name": name,
        "qualified_name": name,
        "description": description,
        "path": f"/tmp/{name}/SKILL.md",
        "source": source,
        "purposes": ["general review"],
        "supported_operations": ["review.analyze"],
        "activation_terms": list(activation_terms),
        "required_operations": list(required_operations),
    }


@pytest.mark.parametrize(
    "description",
    ["Review code", "Review parser code"],
    ids=["purpose-only-competitor", "topic-compatible-competitor"],
)
@pytest.mark.parametrize(
    ("prompt", "activation_terms", "required_operations"),
    [
        (
            "Inspect the parser. Do not use alpha-review skill.",
            (),
            (),
        ),
        (
            "Inspect the parser.",
            ("teaching",),
            (),
        ),
        (
            "Inspect the parser. Do not run tests.",
            (),
            ("test.execute",),
        ),
    ],
    ids=["explicit-denial", "unmet-activation", "required-operation-denial"],
)
def test_unavailable_candidate_does_not_suppress_general_review_fallback(
    description: str,
    prompt: str,
    activation_terms: tuple[str, ...],
    required_operations: tuple[str, ...],
) -> None:
    unavailable = _review_skill(
        "alpha-review",
        source="project",
        description=description,
        activation_terms=activation_terms,
        required_operations=required_operations,
    )
    fallback = _review_skill("beta-review", source="user")
    intent = parse_intent(prompt)
    router = SkillRouter()

    fallback_only = router.route([fallback], intent, CapabilityRegistry())
    with_unavailable = router.route(
        [unavailable, fallback], intent, CapabilityRegistry()
    )

    assert [skill.name for skill in fallback_only.selected] == ["beta-review"]
    assert [skill.name for skill in with_unavailable.selected] == ["beta-review"]


def test_explicit_skill_request_still_bypasses_inferred_activation() -> None:
    requested = _review_skill(
        "alpha-review",
        source="project",
        activation_terms=("teaching",),
    )

    routing = SkillRouter().route(
        [requested],
        parse_intent("Use alpha-review skill to inspect the parser."),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in routing.selected] == ["alpha-review"]
    assert routing.required_skill_names() == ["alpha-review"]


@pytest.mark.parametrize(
    ("prompt", "activation_terms", "required_operations"),
    [
        (
            "Optimize the database query. Do not use query-profiling skill.",
            (),
            (),
        ),
        ("Optimize the database query.", ("profiling",), ()),
        (
            "Optimize the database query. Do not run tests.",
            (),
            ("test.execute",),
        ),
    ],
    ids=["explicit-denial", "unmet-activation", "required-operation-denial"],
)
def test_unavailable_topic_match_does_not_authorize_unrelated_specialty(
    prompt: str,
    activation_terms: tuple[str, ...],
    required_operations: tuple[str, ...],
) -> None:
    unavailable = {
        "name": "query-profiling",
        "description": "Optimize database query performance",
        "source": "project",
        "purposes": ["measured query optimization"],
        "supported_operations": ["refactor.update"],
        "activation_terms": list(activation_terms),
        "required_operations": list(required_operations),
    }
    unrelated_specialty = {
        "name": "structural-refactor",
        "description": "Refactor code structure in behavior-preserving steps",
        "source": "project",
        "purposes": ["behavior-preserving refactor"],
        "supported_operations": ["refactor.update"],
    }

    routing = SkillRouter().route(
        [unavailable, unrelated_specialty],
        parse_intent(prompt),
        CapabilityRegistry(),
    )

    assert routing.selected == []
