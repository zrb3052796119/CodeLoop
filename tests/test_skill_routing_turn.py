"""Unit contracts for the canonical parse-and-route Turn seam."""

from __future__ import annotations

import json

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentOperation,
    IntentPolarity,
    IntentType,
    ParsedIntent,
)
from minicode.skill_router import (
    RoutedSkill,
    SkillAuthorityResolution,
    SkillRoutingResult,
    skill_load_policy_for_routing,
)
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.tooling import skill_load_binding_from_value


def _intent(prompt: str = "review the router") -> ParsedIntent:
    return ParsedIntent(
        raw_input=prompt,
        intent_type=IntentType.REVIEW,
        action_type=ActionType.ANALYZE,
        confidence=0.95,
    )


def _selected_skill(name: str = "router-review") -> RoutedSkill:
    return RoutedSkill(
        name=name,
        qualified_name=name,
        description="Review router behavior",
        path=f"/skills/{name}/SKILL.md",
        source="project",
        score=4.0,
    )


def _routing_result(
    *,
    selected: list[RoutedSkill] | None = None,
    used_fallback: bool = False,
    candidate_count: int = 0,
    reranker_status: str = "disabled",
    inferred_candidates: tuple[RoutedSkill, ...] = (),
    requested: tuple[str, ...] = (),
    constraint_vetoed: tuple[str, ...] = (),
) -> SkillRoutingResult:
    routed = list(selected or [])
    return SkillRoutingResult(
        intent_type=IntentType.REVIEW.value,
        action_type=ActionType.ANALYZE.value,
        capability_domains=[],
        capability_scopes=[],
        selected=routed,
        total_skills=3,
        used_fallback=used_fallback,
        candidate_count=candidate_count,
        reranker_status=reranker_status,
        inferred_candidates=inferred_candidates,
        authority_resolution=SkillAuthorityResolution(requested=requested),
        constraint_vetoed_names=constraint_vetoed,
    )


class _CapturingRouter:
    def __init__(self, result: SkillRoutingResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def route(self, skills, intent, registry, **kwargs):
        self.calls.append(
            {
                "skills": skills,
                "intent": intent,
                "registry": registry,
                "kwargs": kwargs,
            }
        )
        return self.result


def test_reuses_parsed_intent_by_identity_and_forwards_nondefault_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_intent = _intent()
    registry = CapabilityRegistry()
    router = _CapturingRouter(_routing_result(selected=[_selected_skill()]))

    def unexpected_parse(*_args, **_kwargs):
        raise AssertionError("a supplied parsed_intent must not be reparsed")

    monkeypatch.setattr("minicode.intent_parser.parse_intent", unexpected_parse)

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=parsed_intent.raw_input,
            skills=({"name": "router-review"},),
            registry=registry,
            parsed_intent=parsed_intent,
            top_k=2,
        ),
        SkillRoutingTurnAdapters(router=router),
    )

    assert decision.intent is parsed_intent
    assert router.calls[0]["intent"] is parsed_intent
    assert router.calls[0]["registry"] is registry
    assert router.calls[0]["kwargs"] == {"top_k": 2}


def test_parser_and_reranker_receive_isolated_control_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_intent = _intent("make the flow less brittle")
    intent_context = IntentContext.from_messages(
        [{"role": "user", "content": "review the router"}]
    )
    semantic_adapter = object()
    candidate_reranker = object()
    budget = object()
    controls = {"agent_budget": budget, "trace_id": "turn-7"}
    parser_call: dict[str, object] = {}
    router = _CapturingRouter(_routing_result(used_fallback=True))

    def parse(prompt: str, **kwargs):
        parser_call["prompt"] = prompt
        parser_call["kwargs"] = kwargs
        return parsed_intent

    monkeypatch.setattr("minicode.intent_parser.parse_intent", parse)

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=parsed_intent.raw_input,
            skills=(),
            registry=CapabilityRegistry(),
            context=intent_context,
        ),
        SkillRoutingTurnAdapters(
            router=router,
            semantic_intent=semantic_adapter,
            candidate_reranker=candidate_reranker,
        ),
        controls=controls,
    )

    assert decision.intent is parsed_intent
    assert parser_call["prompt"] == parsed_intent.raw_input
    parser_kwargs = parser_call["kwargs"]
    assert isinstance(parser_kwargs, dict)
    assert parser_kwargs["context"] is intent_context
    assert parser_kwargs["semantic_adapter"] is semantic_adapter

    intent_controls = parser_kwargs["call_context"]
    routing_kwargs = router.calls[0]["kwargs"]
    assert isinstance(intent_controls, dict)
    assert isinstance(routing_kwargs, dict)
    routing_controls = routing_kwargs["call_context"]
    assert isinstance(routing_controls, dict)
    assert intent_controls == controls
    assert intent_controls is not controls
    assert routing_controls is not intent_controls
    assert routing_controls["agent_budget"] is budget
    assert routing_controls["trace_id"] == "turn-7"
    assert routing_controls["purpose"] == "skill-routing"
    assert routing_controls["preserve_model_calls"] == 1
    assert routing_controls["skill_candidate_reranker"] is candidate_reranker
    assert "purpose" not in intent_controls
    assert "preserve_model_calls" not in intent_controls
    assert "skill_candidate_reranker" not in intent_controls
    assert controls == {"agent_budget": budget, "trace_id": "turn-7"}


def test_fallback_projects_the_full_inventory_and_reports_no_skill() -> None:
    catalog = [
        {"name": "router-review", "description": "Review routing"},
        {"name": "pytest-debugging", "description": "Debug tests"},
        {"name": "documentation-writing", "description": "Write docs"},
    ]
    router = _CapturingRouter(_routing_result(used_fallback=True))

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="hello",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent("hello"),
        ),
        SkillRoutingTurnAdapters(router=router),
    )

    assert decision.prompt_mode == "inventory_names"
    assert decision.prompt_skills == tuple(catalog)
    assert all(
        projected is original
        for projected, original in zip(decision.prompt_skills, catalog)
    )
    assert decision.outcome == "no_skill"
    # The default top-k is deliberately omitted for legacy-compatible routers.
    assert router.calls[0]["kwargs"] == {}


def test_successful_route_projects_only_selected_skill_dicts() -> None:
    selected = _selected_skill()
    router = _CapturingRouter(_routing_result(selected=[selected]))

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="review the router",
            skills=(
                {"name": "router-review"},
                {"name": "unrelated-skill"},
            ),
            registry=CapabilityRegistry(),
            parsed_intent=_intent(),
        ),
        SkillRoutingTurnAdapters(router=router),
    )

    assert decision.prompt_mode == "routed"
    assert decision.prompt_skills == (selected.to_dict(),)
    assert decision.outcome == "route"


def test_inventory_projection_excludes_constraint_vetoed_skill() -> None:
    catalog = [
        {"name": "safe-skill", "qualified_name": "safe-skill"},
        {"name": "blocked", "qualified_name": "testing/blocked"},
    ]
    router = _CapturingRouter(
        _routing_result(
            used_fallback=True,
            constraint_vetoed=("testing/blocked",),
        )
    )

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="fix the bug without tests",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent("fix the bug without tests"),
        ),
        SkillRoutingTurnAdapters(router=router),
    )

    assert decision.prompt_mode == "inventory_names"
    assert decision.prompt_skills == (catalog[0],)


def test_explicit_skill_required_operation_conflict_requires_clarification() -> None:
    router = _CapturingRouter(
        _routing_result(
            used_fallback=True,
            requested=("testing/regression",),
            constraint_vetoed=("testing/regression",),
        )
    )

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="Use testing/regression Skill, but do not add tests",
            skills=(
                {
                    "name": "regression",
                    "qualified_name": "testing/regression",
                },
            ),
            registry=CapabilityRegistry(),
            parsed_intent=_intent(),
        ),
        SkillRoutingTurnAdapters(router=router),
    )

    assert decision.outcome == "clarify"
    assert decision.outcome_reason == "explicit-skill-operation-conflict"
    assert decision.prompt_mode == "constraint_conflict"
    assert decision.prompt_skills == ()


@pytest.mark.parametrize(
    ("candidate_count", "reranker_status", "expected"),
    [
        (0, "disabled", "no_skill"),
        (2, "abstained", "abstain"),
    ],
)
def test_empty_selection_distinguishes_no_skill_from_reranker_abstention(
    candidate_count: int,
    reranker_status: str,
    expected: str,
) -> None:
    router = _CapturingRouter(
        _routing_result(
            used_fallback=True,
            candidate_count=candidate_count,
            reranker_status=reranker_status,
        )
    )

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="review the router",
            skills=(),
            registry=CapabilityRegistry(),
            parsed_intent=_intent(),
        ),
        SkillRoutingTurnAdapters(router=router),
    )

    assert decision.outcome == expected


def test_candidate_trace_is_in_memory_only_and_never_serialized() -> None:
    hidden_candidate = _selected_skill("private-candidate-name")
    result = _routing_result(
        used_fallback=True,
        candidate_count=1,
        inferred_candidates=(hidden_candidate,),
    )

    payload = result.to_dict()

    assert result.inferred_candidates == (hidden_candidate,)
    assert payload["candidate_count"] == 1
    assert "inferred_candidates" not in payload
    assert "private-candidate-name" not in json.dumps(payload)


def _catalog_skill(name: str) -> dict[str, object]:
    return {
        "name": name,
        "qualified_name": name,
        "description": f"{name} description",
        "path": f"/skills/{name}/SKILL.md",
        "source": "project",
        "content_digest": "a" * 64,
        "routing_identity_digest": "b" * 64,
    }


_MODEL_MODE_CONTROLS = {"skill_selection_mode": "model"}


def test_model_mode_projects_full_catalog_without_router_scoring() -> None:
    catalog = [
        _catalog_skill("alpha"),
        _catalog_skill("beta"),
        _catalog_skill("gamma"),
    ]
    router = _CapturingRouter(_routing_result())

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="review the router",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent(),
        ),
        SkillRoutingTurnAdapters(router=router),
        controls=_MODEL_MODE_CONTROLS,
    )

    assert router.calls == []
    assert decision.prompt_mode == "model"
    assert decision.prompt_skills == tuple(catalog)
    assert decision.outcome == "route"
    assert decision.outcome_reason == "model-catalog-selection"
    assert decision.routing.decision_source == "model-first"
    assert decision.routing.selected == []
    assert decision.routing.used_fallback is False
    assert {
        binding.qualified_name for binding in decision.routing.catalog_bindings
    } == {"alpha", "beta", "gamma"}


def test_model_mode_global_denial_suppresses_the_whole_catalog() -> None:
    catalog = [_catalog_skill("alpha"), _catalog_skill("beta")]
    prompt = "never invoke or apply any skill"
    router = _CapturingRouter(_routing_result())

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent(prompt),
        ),
        SkillRoutingTurnAdapters(router=router),
        controls=_MODEL_MODE_CONTROLS,
    )

    assert router.calls == []
    assert decision.prompt_mode == "suppressed"
    assert decision.prompt_skills == ()
    assert decision.outcome == "no_skill"
    assert decision.outcome_reason == "user-denied-all-skills"
    assert decision.routing.authority_resolution.deny_all is True


def test_model_mode_single_denial_is_enforced_by_the_load_policy() -> None:
    catalog = [
        _catalog_skill("alpha"),
        _catalog_skill("beta"),
        _catalog_skill("gamma"),
    ]
    prompt = "Use alpha skill, do not use beta skill."

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent(prompt),
        ),
        SkillRoutingTurnAdapters(router=_CapturingRouter(_routing_result())),
        controls=_MODEL_MODE_CONTROLS,
    )

    assert decision.prompt_mode == "model"
    assert decision.outcome_reason == "model-catalog-selection"
    assert decision.routing.authority_resolution.denied == ("beta",)
    # Denied Skills stay visible in the catalog projection; enforcement lives
    # in the turn-scoped load policy, which denies them by name.
    assert [skill["name"] for skill in decision.prompt_skills] == [
        "alpha",
        "beta",
        "gamma",
    ]
    policy = skill_load_policy_for_routing(decision.routing)
    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.allow_catalog_scope is True
    assert policy.denied_qualified_names == frozenset({"beta"})
    assert policy.permits(skill_load_binding_from_value(catalog[0]))
    assert not policy.permits(skill_load_binding_from_value(catalog[1]))
    assert policy.permits(skill_load_binding_from_value(catalog[2]))


def test_model_mode_unresolved_reference_does_not_close_the_catalog() -> None:
    catalog = [_catalog_skill("alpha"), _catalog_skill("beta")]
    prompt = "Use the ghost-name skill for this review"

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=prompt,
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent(prompt),
        ),
        SkillRoutingTurnAdapters(router=_CapturingRouter(_routing_result())),
        controls=_MODEL_MODE_CONTROLS,
    )

    assert decision.prompt_mode == "model"
    assert decision.outcome == "route"
    assert decision.outcome_reason == "model-catalog-selection-unresolved-not-enforced"
    assert decision.routing.authority_resolution.unresolved == ("ghost-name",)
    assert decision.prompt_skills == tuple(catalog)
    policy = skill_load_policy_for_routing(decision.routing)
    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.allow_catalog_scope is True
    assert all(policy.permits(binding) for binding in decision.routing.catalog_bindings)


def test_model_mode_oversized_catalog_stays_catalog_scoped() -> None:
    catalog = [_catalog_skill(f"skill-{index}") for index in range(129)]
    router = _CapturingRouter(_routing_result(used_fallback=True))

    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="hello",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent("hello"),
        ),
        SkillRoutingTurnAdapters(router=router),
        controls=_MODEL_MODE_CONTROLS,
    )

    assert router.calls == []
    assert decision.prompt_mode == "model"
    assert decision.routing.decision_source == "model-first"
    assert len(decision.routing.catalog_bindings) == 129
    policy = skill_load_policy_for_routing(decision.routing)
    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.allow_catalog_scope is True
    assert policy.allowed_bindings == frozenset()
    assert all(policy.permits(binding) for binding in decision.routing.catalog_bindings)


def _directoried_skill(name: str, directory: str) -> dict[str, object]:
    skill = _catalog_skill(name)
    skill["qualified_name"] = f"{directory}/{name}"
    skill["directory"] = directory
    skill["directory_description"] = f"{directory} area"
    return skill


def _model_decision(catalog, controls):
    return route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="review the router",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=_intent(),
        ),
        SkillRoutingTurnAdapters(router=_CapturingRouter(_routing_result())),
        controls=controls,
    )


def test_model_mode_under_flat_budget_stays_flat() -> None:
    catalog = [
        _directoried_skill("alpha", "docs"),
        _directoried_skill("beta", "docs"),
    ]

    decision = _model_decision(
        catalog,
        {"skill_selection_mode": "model", "skill_flat_catalog_chars": 100000},
    )

    assert decision.prompt_mode == "model"
    assert decision.outcome_reason == "model-catalog-selection"


def test_model_mode_over_budget_with_directories_escalates() -> None:
    catalog = [
        _directoried_skill("alpha", "docs"),
        _directoried_skill("beta", "docs"),
        _catalog_skill("rooty"),
    ]

    decision = _model_decision(
        catalog,
        {"skill_selection_mode": "model", "skill_flat_catalog_chars": 10},
    )

    # The escalation changes only the prompt projection: the visible catalog
    # and the catalog-scoped load policy are identical to flat model mode.
    assert decision.prompt_mode == "hierarchical"
    assert decision.prompt_skills == tuple(catalog)
    assert decision.outcome == "route"
    assert decision.outcome_reason == "model-catalog-selection"
    policy = skill_load_policy_for_routing(decision.routing)
    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.allow_catalog_scope is True


def test_model_mode_over_budget_without_directories_stays_flat() -> None:
    catalog = [_catalog_skill(f"skill-{index}") for index in range(40)]

    decision = _model_decision(
        catalog,
        {"skill_selection_mode": "model", "skill_flat_catalog_chars": 10},
    )

    # Directory cards are meaningless for an all-root catalog.
    assert decision.prompt_mode == "model"


def test_hierarchical_and_flat_modes_share_the_same_policy() -> None:
    catalog = [
        _directoried_skill("alpha", "docs"),
        _directoried_skill("beta", "docs"),
    ]

    flat = _model_decision(
        catalog,
        {"skill_selection_mode": "model", "skill_flat_catalog_chars": 100000},
    )
    hierarchical = _model_decision(
        catalog,
        {"skill_selection_mode": "model", "skill_flat_catalog_chars": 10},
    )

    assert skill_load_policy_for_routing(flat.routing) == skill_load_policy_for_routing(
        hierarchical.routing
    )


def test_model_mode_invalid_budget_control_fails_closed_to_default() -> None:
    catalog = [
        _directoried_skill("alpha", "docs"),
        _directoried_skill("beta", "docs"),
    ]

    decision = _model_decision(
        catalog,
        {
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": "not-a-number",
        },
    )

    assert decision.prompt_mode == "model"


# --- Experiment arm B: advisory intent hints (hierarchical mode only) ---


def _hint_intent() -> ParsedIntent:
    prompt = "review the parser code"
    return ParsedIntent(
        raw_input=prompt,
        intent_type=IntentType.REVIEW,
        action_type=ActionType.ANALYZE,
        confidence=0.8,
        entities={"files": ["parser.py"]},
        operations=(
            IntentOperation(
                intent_type=IntentType.REVIEW,
                action_type=ActionType.ANALYZE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.9,
                clause="review the parser code",
                span=(0, 22),
            ),
            IntentOperation(
                intent_type=IntentType.SYSTEM,
                action_type=ActionType.EXECUTE,
                polarity=IntentPolarity.NEGATED,
                confidence=0.9,
                clause="do not deploy",
            ),
        ),
    )


def _hint_catalog() -> list[dict[str, object]]:
    # "reviewing" covers the query's review/code concept groups; "docs"
    # covers none of them and must be excluded from suggestions.
    catalog = [
        _directoried_skill("alpha", "reviewing"),
        _directoried_skill("beta", "reviewing"),
        _directoried_skill("gamma", "docs"),
    ]
    for skill in catalog:
        if skill["directory"] == "reviewing":
            skill["directory_description"] = "code review and audit helpers"
        else:
            skill["directory_description"] = "documentation work"
    return catalog


def _hint_decision(catalog, controls, intent=None):
    return route_skill_turn(
        SkillRoutingTurnRequest(
            prompt="review the parser code",
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=intent or _hint_intent(),
        ),
        SkillRoutingTurnAdapters(router=_CapturingRouter(_routing_result())),
        controls=controls,
    )


_HIERARCHICAL_HINT_CONTROLS = {
    "skill_selection_mode": "model",
    "skill_flat_catalog_chars": 10,
    "skill_intent_hints": True,
}


def test_intent_hints_off_leaves_routing_without_payload() -> None:
    decision = _hint_decision(
        _hint_catalog(),
        {"skill_selection_mode": "model", "skill_flat_catalog_chars": 10},
    )

    assert decision.prompt_mode == "hierarchical"
    assert decision.routing.intent_hints is None
    assert "intent_hints" not in decision.routing.to_dict()


def test_intent_hints_hierarchical_mode_builds_advisory_payload() -> None:
    decision = _hint_decision(_hint_catalog(), _HIERARCHICAL_HINT_CONTROLS)

    hints = decision.routing.intent_hints
    assert hints is not None
    assert hints["hinted"] is True
    assert hints["stages"] == [
        {
            "operation": "review the parser code",
            "evidence": "review the parser code",
        }
    ]
    assert hints["entities"] == ["parser.py"]
    assert hints["constraints"] == ["do not deploy"]
    assert hints["suggested_directories"] == ["reviewing"]
    assert hints["confidence"] == 0.8
    assert "prefetched_directory" not in hints
    # The payload crosses the routing projection for evaluator/log analysis.
    assert decision.routing.to_dict()["intent_hints"]["suggested_directories"] == [
        "reviewing"
    ]
    # Hints are presentation-only: catalog and policy are untouched.
    assert decision.prompt_skills == tuple(_hint_catalog())
    policy = skill_load_policy_for_routing(decision.routing)
    assert policy is not None
    assert policy.allow_catalog_scope is True


def test_intent_hints_flat_model_mode_carries_no_payload() -> None:
    decision = _hint_decision(
        _hint_catalog(),
        {
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": 100000,
            "skill_intent_hints": True,
        },
    )

    assert decision.prompt_mode == "model"
    assert decision.routing.intent_hints is None
    assert "intent_hints" not in decision.routing.to_dict()


def test_intent_hints_exclude_zero_coverage_directories() -> None:
    catalog = [_directoried_skill("gamma", "docs")]
    catalog[0]["directory_description"] = "documentation work"

    decision = _hint_decision(
        catalog,
        {
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": 10,
            # Prefetch alone implies hints, matching the config contract.
            "skill_intent_prefetch": True,
        },
    )

    assert decision.prompt_mode == "hierarchical"
    hints = decision.routing.intent_hints
    assert hints is not None
    assert hints["suggested_directories"] == []
    # No suggestion means no prefetch, honestly.
    assert "prefetched_directory" not in hints


def test_intent_prefetch_caps_to_page_budget() -> None:
    long_description = "deep static review of parser edge cases " * 30
    catalog = []
    for name in ("alpha", "beta", "gamma"):
        skill = _directoried_skill(name, "reviewing")
        skill["description"] = long_description
        skill["directory_description"] = "code review and audit helpers"
        catalog.append(skill)

    decision = _hint_decision(
        catalog,
        {
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": 10,
            "skill_intent_hints": True,
            "skill_intent_prefetch": True,
            "skill_page_chars": 500,
        },
    )

    hints = decision.routing.intent_hints
    assert hints is not None
    assert hints["prefetched_directory"] == "reviewing"
    prefetched = hints["prefetched_skills"]
    # The complete prompt block is bounded, not only its description lines.
    assert sum(len(line) for line in prefetched) <= 500
    assert len(hints["prefetched_page"]) <= 500
    assert len(prefetched) == 1
    assert prefetched[0].startswith("- reviewing/alpha: ")
    assert "description truncated; call load_skill" in prefetched[0]
    assert hints["prefetch_truncated"] is True
    assert hints["prefetch_next_offset"] == 1
    # Prefetch is presentation-only: prompt skills and policy stay identical.
    assert decision.prompt_skills == tuple(catalog)
