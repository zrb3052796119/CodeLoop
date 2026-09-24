"""Production route -> prompt -> browse continuation budget contracts."""

from __future__ import annotations

import hashlib

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import ActionType, IntentType, ParsedIntent
from minicode.prompt import _intent_hint_lines, build_system_prompt
from minicode.skill_router import SkillRouter, skill_load_policy_for_routing
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.tooling import ToolContext
from minicode.tools.browse_skills import create_browse_skill_directory_tool


def _skill(name: str, directory: str, description: str) -> dict:
    qualified = f"{directory}/{name}"
    digest = hashlib.sha256(qualified.encode()).hexdigest()
    return {
        "name": name,
        "qualified_name": qualified,
        "directory": directory,
        "directory_description": "code review and audit helpers",
        "description": description,
        "path": f"/synthetic-skills/{qualified}/SKILL.md",
        "source": "project",
        "content_digest": digest,
        "routing_identity_digest": digest,
    }


@pytest.mark.parametrize("budget", [500, 4000])
@pytest.mark.parametrize("maximal_name", [False, True])
@pytest.mark.parametrize("has_next", [False, True])
def test_intent_prefetch_total_prompt_block_is_bounded_and_continuable(
    tmp_path, budget, maximal_name, has_next
) -> None:
    directory = "d" * 128 if maximal_name else "reviewing"
    name = "a" * 128 if maximal_name else "alpha"
    long_description = "review " + "x" * 12000
    catalog = [_skill(name, directory, long_description)]
    if has_next:
        catalog.append(_skill("zeta", directory, "review helper"))
    raw_input = "review the parser code"
    intent = ParsedIntent(
        raw_input=raw_input,
        intent_type=IntentType.REVIEW,
        action_type=ActionType.ANALYZE,
        confidence=0.8,
    )
    decision = route_skill_turn(
        SkillRoutingTurnRequest(
            prompt=raw_input,
            skills=catalog,
            registry=CapabilityRegistry(),
            parsed_intent=intent,
        ),
        SkillRoutingTurnAdapters(router=SkillRouter()),
        controls={
            "skill_selection_mode": "model",
            "skill_flat_catalog_chars": 10,
            "skill_intent_prefetch": True,
            "skill_page_chars": budget,
        },
    )
    assert decision.prompt_mode == "hierarchical"
    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {"skills": decision.prompt_skills, "skill_routing": decision.routing},
    )
    hint_lines = _intent_hint_lines(decision.routing)
    first = next(
        index
        for index, line in enumerate(hint_lines)
        if line.startswith("Prefetched descriptions")
    )
    # This extraction also works against the original producer/renderer, so
    # its counterexample fails on real emitted text, not a newly added key.
    prefetched_block = "\n".join(hint_lines[first:])
    assert prefetched_block in prompt
    assert len(prefetched_block) <= budget
    assert "description truncated; call load_skill" in prefetched_block
    assert f"- {directory}/{name}: " in prefetched_block
    assert long_description not in prompt
    hints = decision.routing.intent_hints
    assert hints["prefetched_page"] == prefetched_block
    assert hints["prefetch_char_budget"] == budget
    assert hints["prefetch_truncated"] is has_next
    assert decision.prompt_skills == tuple(catalog)
    if not has_next:
        assert "TRUNCATED: no" in prefetched_block
        assert "prefetch_next_offset" not in hints
        return
    assert hints["prefetch_next_offset"] == 1
    assert "call browse_skill_directory again with offset 1" in prefetched_block
    browse = create_browse_skill_directory_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=skill_load_policy_for_routing(decision.routing),
        _runtime={"skillSelection": {"skillPageCharBudget": budget}},
    )
    continuation = browse.run(
        browse.validator({"directory_names": [directory], "offset": 1}), context
    )
    assert continuation.ok is True
    assert len(continuation.output) <= budget
    assert f"- {directory}/zeta:" in continuation.output
    assert f"- {directory}/{name}:" not in continuation.output
    assert "TRUNCATED: no" in continuation.output
