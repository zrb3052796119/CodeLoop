"""Route-to-prompt contracts; these do not assert model adoption behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.prompt import build_system_prompt
from minicode.skill_router import SkillRouter, skill_load_policy_for_routing
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)


def _skill(name: str, directory: str = "diagnostics") -> dict[str, str]:
    qualified = f"{directory}/{name}" if directory else name
    digest = hashlib.sha256(qualified.encode()).hexdigest()
    return {
        "name": name,
        "qualified_name": qualified,
        "directory": directory,
        "directory_description": "Service evidence workflows",
        "description": f"{name} full capability description",
        "path": f"/synthetic-skills/{qualified}/SKILL.md",
        "source": "project",
        "content_digest": digest,
        "routing_identity_digest": digest,
    }


def _render(tmp_path: Path, catalog: list[dict[str, str]], request: str,
            *, mode: str = "model", budget: int = 1):
    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=request, skills=catalog, registry=CapabilityRegistry(),
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
        controls={"skill_selection_mode": mode, "skill_flat_catalog_chars": budget},
    )
    prompt = build_system_prompt(str(tmp_path), [], {
        "skills": list(decision.prompt_skills), "skill_routing": decision.routing,
        "user_interaction_available": False,
    })
    return decision, prompt


@pytest.mark.parametrize("include_root", [False, True])
def test_hierarchical_projection_explains_when_to_expand_directory_cards(
    tmp_path: Path, include_root: bool,
) -> None:
    catalog = [_skill("trace-audit")]
    if include_root:
        catalog.append(_skill("root-helper", ""))
    decision, prompt = _render(tmp_path, catalog, "Review the captured service evidence.")

    assert decision.prompt_mode == "hierarchical"
    assert "Directory cards are navigation summaries, not complete Skill descriptions." in prompt
    assert "current stage needs a specialized workflow and a directory looks relevant" in prompt
    assert "inspect actual Skill descriptions with browse_skill_directory or search_skills before deciding whether any applies" in prompt
    assert "diagnostics: Service evidence workflows" in prompt
    assert "diagnostics/trace-audit" not in prompt
    assert "trace-audit full capability description" not in prompt
    assert ("root-helper: root-helper full capability description" in prompt) is include_root
    assert ("Undirectoried skills:" in prompt) is include_root
    assert "call load_skill with the exact qualified name" in prompt


def test_hierarchical_guidance_preserves_ordinary_and_nonexecuting_zero_skill_choices(
    tmp_path: Path,
) -> None:
    for request in (
        "What is 9 + 6?",
        "List the names of available skills; do not execute their workflows.",
        'Explain the quoted phrase "use trace-audit skill" without executing it.',
        "Use the nonexistent-example skill.",
    ):
        decision, prompt = _render(tmp_path, [_skill("trace-audit")], request)
        assert decision.prompt_mode == "hierarchical"
        assert decision.routing.selected == []
        assert "If no described capability applies" in prompt
        assert "simple ordinary operation that needs no specialized workflow" in prompt
        assert "choose zero skills and proceed with normal tools and reasoning" in prompt
        assert "Choosing zero skills is also valid" not in prompt
        assert "Always browse" not in prompt


def test_hierarchical_guidance_allows_condition_inspection_before_discovery(
    tmp_path: Path,
) -> None:
    _, prompt = _render(tmp_path, [_skill("trace-audit")],
                        "Read the evidence first; only if it shows a specialist problem, use an applicable workflow.")
    assert "For conditional requests, establish the condition before choosing a later-stage workflow" in prompt
    assert "You may inspect project files first to clarify facts or conditions" in prompt
    assert "this does not reveal unexpanded Skill descriptions" in prompt
    assert "Read an applicable skill before carrying out its specialized workflow" in prompt


def test_flat_projection_keeps_descriptions_and_its_existing_zero_skill_contract(
    tmp_path: Path,
) -> None:
    decision, prompt = _render(tmp_path, [_skill("trace-audit")], "Review the evidence.", budget=100000)
    assert decision.prompt_mode == "model"
    assert "diagnostics/trace-audit: trace-audit full capability description" in prompt
    assert "If no skill applies, proceed with normal tools and reasoning." in prompt
    assert "Directory cards are navigation summaries" not in prompt


def test_all_root_catalog_keeps_flat_projection_even_over_budget(tmp_path: Path) -> None:
    decision, prompt = _render(tmp_path, [_skill("root-helper", "")], "Review the evidence.")
    assert decision.prompt_mode == "model"
    assert "root-helper: root-helper full capability description" in prompt
    assert "Available skill directories:" not in prompt
    assert "Directory cards are navigation summaries" not in prompt


def test_routed_projection_does_not_inherit_directory_discovery_guidance(tmp_path: Path) -> None:
    decision, prompt = _render(tmp_path, [_skill("trace-audit")],
                              "Use diagnostics/trace-audit skill to review the evidence.", mode="routed")
    assert decision.routing.selected
    assert decision.prompt_mode != "hierarchical"
    assert "Routed skills:" in prompt
    assert "trace-audit full capability description" in prompt
    assert "Directory cards are navigation summaries" not in prompt


def test_explicit_denial_suppresses_discovery_and_loading(tmp_path: Path) -> None:
    for request in (
        "Do not use any skills; just read the evidence.",
        "Cancel skill use for this request; never invoke or apply any skill.",
    ):
        decision, prompt = _render(tmp_path, [_skill("trace-audit")], request)
        assert decision.prompt_mode == "suppressed"
        assert decision.prompt_skills == ()
        assert "Do not load, infer, or suggest a Skill." in prompt
        assert "Available skill directories:" not in prompt
        assert "Directory cards are navigation summaries" not in prompt
        policy = skill_load_policy_for_routing(decision.routing)
        assert policy is not None
        assert not any(policy.permits(binding) for binding in decision.routing.catalog_bindings)
