from __future__ import annotations

from collections.abc import Callable

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import (
    ActionType,
    IntentOperation,
    IntentPolarity,
    IntentType,
    ParsedIntent,
)
from minicode.run_events import project_skill_routing_event
from minicode.skill_candidate_reranker import (
    SkillRerankDecision,
    SkillRerankRequest,
)
from minicode.skill_router import SkillRouter


def _skill(name: str, description: str, *keywords: str) -> dict:
    return {
        "name": name,
        "qualified_name": name,
        "description": description,
        "path": f"/tmp/{name}/SKILL.md",
        "source": "project",
        "keywords": list(keywords),
    }


def _operation(
    intent_type: IntentType,
    action_type: ActionType,
    clause: str,
    *keywords: str,
    polarity: IntentPolarity = IntentPolarity.AFFIRMED,
) -> IntentOperation:
    return IntentOperation(
        intent_type=intent_type,
        action_type=action_type,
        polarity=polarity,
        confidence=0.95,
        clause=clause,
        keywords=tuple(keywords),
    )


def _intent(raw_input: str, *operations: IntentOperation) -> ParsedIntent:
    affirmed = [
        (index, operation)
        for index, operation in enumerate(operations)
        if operation.polarity is IntentPolarity.AFFIRMED
    ]
    if affirmed:
        primary_index, primary = affirmed[0]
        intent_type = primary.intent_type
        action_type = primary.action_type
        confidence = primary.confidence
    else:
        primary_index = None
        intent_type = IntentType.UNKNOWN
        action_type = ActionType.UNKNOWN
        confidence = 0.0
    return ParsedIntent(
        raw_input=raw_input,
        intent_type=intent_type,
        action_type=action_type,
        confidence=confidence,
        operations=tuple(operations),
        primary_operation_index=primary_index,
        semantic_query=raw_input,
    )


class _FakeReranker:
    def __init__(
        self,
        decide: Callable[[SkillRerankRequest], SkillRerankDecision],
    ) -> None:
        self._decide = decide
        self.requests: list[SkillRerankRequest] = []
        self.call_contexts: list[object] = []

    def rerank(self, request, *, call_context=None) -> SkillRerankDecision:
        self.requests.append(request)
        self.call_contexts.append(call_context)
        return self._decide(request)


class _UniformEmbeddingMatcher:
    def similarities(self, _query, pairs, *, call_context=None):
        del call_context
        return [0.99 for _pair in pairs]


def _candidate_ids_by_name(request: SkillRerankRequest) -> dict[str, str]:
    return {
        candidate.description.split(" ", 1)[0]: candidate.candidate_id
        for candidate in request.candidates
    }


def test_accepted_reranker_can_reorder_and_filter_admitted_candidates() -> None:
    def decide(request: SkillRerankRequest) -> SkillRerankDecision:
        ids = _candidate_ids_by_name(request)
        return SkillRerankDecision(
            status="accepted",
            ordered_candidate_ids=(ids["beta-review"], ids["alpha-review"]),
            confidence=0.91,
            reason_codes=("direct_match",),
        )

    reranker = _FakeReranker(decide)
    result = SkillRouter(semantic_reranker=reranker).route(
        [
            _skill(
                "alpha-review", "Review router security behavior", "router", "security"
            ),
            _skill(
                "beta-review", "Review router security architecture", "router", "security"
            ),
            _skill(
                "gamma-review", "Review router security docs", "router", "security"
            ),
        ],
        _intent(
            "review the router",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review router security",
                "router",
                "security",
            ),
        ),
        CapabilityRegistry(),
        top_k=2,
    )

    assert [skill.name for skill in result.selected] == [
        "beta-review",
        "alpha-review",
    ]
    assert result.candidate_count == 3
    assert result.decision_source == "semantic_reranker"
    assert result.reranker_status == "accepted"
    assert result.decision_reason == "accepted:direct_match"
    assert result.selected[0].operation_indexes == (0,)
    assert reranker.requests[0].top_k == 2


def test_reranker_can_be_injected_through_trusted_call_context() -> None:
    reranker = _FakeReranker(
        lambda _request: SkillRerankDecision(status="abstained")
    )
    call_context = {"skill_candidate_reranker": reranker, "trace": object()}

    result = SkillRouter().route(
        [_skill("router-review", "Review router behavior", "router")],
        _intent(
            "review the router",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review the router",
                "router",
            ),
        ),
        CapabilityRegistry(),
        call_context=call_context,
    )

    assert result.selected == []
    assert result.used_fallback is True
    assert result.reranker_status == "abstained"
    assert reranker.call_contexts == [call_context]


def test_invented_candidate_id_falls_back_to_deterministic_order() -> None:
    reranker = _FakeReranker(
        lambda _request: SkillRerankDecision(
            status="accepted",
            ordered_candidate_ids=("invented_candidate",),
            confidence=0.99,
        )
    )
    result = SkillRouter(semantic_reranker=reranker).route(
        [
            _skill(
                "alpha-review", "Review router security behavior", "router", "security"
            ),
            _skill(
                "beta-review", "Review router security behavior", "router", "security"
            ),
        ],
        _intent(
            "review the router",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review router security",
                "router",
                "security",
            ),
        ),
        CapabilityRegistry(),
        top_k=2,
    )

    assert [skill.name for skill in result.selected] == [
        "alpha-review",
        "beta-review",
    ]
    assert result.decision_source == "deterministic"
    assert result.reranker_status == "invalid_response"


def test_reranker_never_receives_or_deletes_explicit_skill_authority() -> None:
    reranker = _FakeReranker(
        lambda _request: SkillRerankDecision(status="abstained")
    )
    result = SkillRouter(semantic_reranker=reranker).route(
        [
            _skill("explicit-review", "Review router behavior", "router"),
            _skill("inferred-review", "Review router behavior", "router"),
        ],
        _intent(
            "Use the explicit-review Skill to review the router",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review the router",
                "router",
            ),
        ),
        CapabilityRegistry(),
        top_k=1,
    )

    assert [skill.name for skill in result.selected] == ["explicit-review"]
    assert result.selected[0].explicitly_requested is True
    assert all(
        not candidate.description.startswith("explicit-review ")
        for candidate in reranker.requests[0].candidates
    )


def test_negated_operation_skill_never_enters_reranker_candidate_pool() -> None:
    def decide(request: SkillRerankRequest) -> SkillRerankDecision:
        [candidate] = request.candidates
        return SkillRerankDecision(
            status="accepted",
            ordered_candidate_ids=(candidate.candidate_id,),
            confidence=0.9,
        )

    reranker = _FakeReranker(decide)
    result = SkillRouter(semantic_reranker=reranker).route(
        [
            _skill("router-review", "Review router architecture", "router"),
            _skill("pytest-runner", "Run and execute pytest tests", "pytest"),
        ],
        _intent(
            "review the router but do not run pytest",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review the router",
                "router",
            ),
            _operation(
                IntentType.TEST,
                ActionType.EXECUTE,
                "do not run pytest",
                "pytest",
                polarity=IntentPolarity.NEGATED,
            ),
        ),
        CapabilityRegistry(),
    )

    assert [skill.name for skill in result.selected] == ["router-review"]
    assert [
        candidate.description.split(" ", 1)[0]
        for candidate in reranker.requests[0].candidates
    ] == ["router-review"]


def test_compound_candidate_merge_round_robins_affirmed_operations() -> None:
    result = SkillRouter().route(
        [
            _skill("review-a", "Review router design", "router"),
            _skill("review-b", "Review router structure", "router"),
            _skill("test-a", "Test pytest behavior", "pytest"),
            _skill("test-b", "Test pytest regressions", "pytest"),
        ],
        _intent(
            "review the router and run pytest",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review the router",
                "router",
            ),
            _operation(
                IntentType.TEST,
                ActionType.EXECUTE,
                "run pytest",
                "pytest",
            ),
        ),
        CapabilityRegistry(),
        top_k=4,
    )

    assert [skill.name for skill in result.selected] == [
        "review-a",
        "test-a",
        "review-b",
        "test-b",
    ]
    assert [skill.operation_indexes for skill in result.selected] == [
        (0,),
        (1,),
        (0,),
        (1,),
    ]


def test_explicit_supported_operations_override_misleading_prose() -> None:
    skills = [
        {
            **_skill(
                "misleading-review",
                "Review and analyze router security behavior",
                "router",
                "security",
            ),
            "supported_operations": ["test.execute"],
        },
        {
            **_skill(
                "declared-review",
                "Router security behavior checks",
                "router",
                "security",
            ),
            "supported_operations": ["review.analyze"],
        },
    ]

    result = SkillRouter().route(
        skills,
        _intent(
            "review router security",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review router security",
                "router",
                "security",
            ),
        ),
        CapabilityRegistry(),
        top_k=2,
    )

    assert [skill.name for skill in result.selected] == ["declared-review"]
    assert "operation-purpose-compatible" in result.selected[0].reasons


def test_malformed_supported_operations_fail_closed() -> None:
    skills = [
        {
            **_skill(
                "malformed-review",
                "Review and analyze router security behavior",
                "router",
                "security",
            ),
            "supported_operations": ["review_everything"],
        },
        {
            **_skill(
                "declared-review",
                "Router security behavior checks",
                "router",
                "security",
            ),
            "supported_operations": ["review.analyze"],
        },
    ]

    result = SkillRouter().route(
        skills,
        _intent(
            "review router security",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review router security",
                "router",
                "security",
            ),
        ),
        CapabilityRegistry(),
        top_k=2,
    )

    assert [skill.name for skill in result.selected] == ["declared-review"]


def test_whole_request_embedding_does_not_fabricate_compound_operation_lanes() -> None:
    result = SkillRouter(
        embedding_matcher=_UniformEmbeddingMatcher()
    ).route(
        [
            _skill("apple-a", "Review apple behavior", "apple"),
            _skill("apple-b", "Review apple structure", "apple"),
            _skill("banana-z", "Review banana behavior", "banana"),
        ],
        _intent(
            "review apple and review banana",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review apple",
                "apple",
            ),
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review banana",
                "banana",
            ),
        ),
        CapabilityRegistry(),
        top_k=2,
    )

    assert [skill.name for skill in result.selected] == ["apple-a", "banana-z"]
    assert [skill.operation_indexes for skill in result.selected] == [(0,), (1,)]


def test_deterministic_candidate_pool_is_bounded_to_sixteen() -> None:
    result = SkillRouter().route(
        [
            _skill(
                f"review-{index:02d}",
                "Review router security behavior",
                "router",
                "security",
            )
            for index in range(24)
        ],
        _intent(
            "review router security",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review router security",
                "router",
                "security",
            ),
        ),
        CapabilityRegistry(),
        top_k=24,
    )

    assert result.candidate_count == 16
    assert len(result.selected) == 16


def test_v3_event_projects_only_bounded_decision_provenance() -> None:
    result = SkillRouter().route(
        [_skill("router-review", "Review router behavior", "router")],
        _intent(
            "review the router",
            _operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                "review the router",
                "router",
            ),
        ),
        CapabilityRegistry(),
    )

    payload = project_skill_routing_event(result)

    assert payload["routingVersion"] == 1
    assert payload["routeDecisionVersion"] == 3
    assert payload["candidateCount"] == 1
    assert payload["decisionSource"] == "deterministic"
    assert payload["rerankerStatus"] == "disabled"
    assert "decisionReason" not in payload
    assert "review the router" not in str(payload)
